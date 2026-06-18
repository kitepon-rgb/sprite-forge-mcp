"""LoRA training orchestrator — the planned `train_style_lora` (docs/04, Phase 5).

The sprite-forge backend (a trusted service the user runs) drives the GPU box over
SSH the same way it drives ComfyUI: it builds the dataset, ships it to the box, runs
the (already-installed) kohya sd-scripts trainer, and copies the resulting LoRA into
ComfyUI/models/loras. One WebUI button / MCP call — NO manual PowerShell per train.

Training is long (~20-40 min), so a call starts a background asyncio job and returns a
job_id; poll status(). No silent fallback: failures are recorded with the box log tail.
"""
from __future__ import annotations
import asyncio
import contextlib
import io
import re
from pathlib import Path

import numpy as np
from PIL import Image

from . import config, audit

# Default house-style training set for a STYLE LoRA: (sprite_stem, subject_desc) pairs. The
# subject lives in the CAPTION so the LoRA binds STYLE, not the subject. Used only when the
# caller passes no sprite list. These are PLACEHOLDER example stems — replace them with your
# own adopted sprite stems (files under SPRITEFORGE_RPGDEV_SPRITES) or pass an explicit list
# of (stem, desc) to train_style_lora(); a stem with no matching .png is skipped.
HOUSE_STYLE_SPRITES = [
    ("hero", "a hero swordsman, full body"),
    ("mage", "a mage character, full body"),
    ("knight", "an armored knight, full body"),
    ("archer", "an archer character, full body"),
    ("slime", "a slime monster"),
    ("goblin", "a goblin enemy"),
    ("dragon", "a dragon enemy"),
]

_jobs: dict[str, dict] = {}


def _resolve(stem: str) -> Path | None:
    p = config.RPGDEV_SPRITES / f"{stem}.png"
    return p if p.exists() else None


def _build_dataset(sprites, trigger: str, img_dir: Path) -> int:
    """Composite each sprite on white (RGB) keeping its alpha (for alpha_mask), 1024,
    + a trigger-first caption. Returns the count actually written."""
    img_dir.mkdir(parents=True, exist_ok=True)
    n = 0
    for stem, desc in sprites:
        p = _resolve(stem)
        if not p:
            continue
        sp = Image.open(p).convert("RGBA")
        sp.thumbnail((960, 960), Image.LANCZOS)
        white = Image.new("RGBA", sp.size, (255, 255, 255, 255))
        white.alpha_composite(sp)
        rgb = Image.new("RGB", (1024, 1024), (255, 255, 255))
        al = Image.new("L", (1024, 1024), 0)
        ox, oy = (1024 - sp.width) // 2, (1024 - sp.height) // 2
        rgb.paste(white.convert("RGB"), (ox, oy))
        al.paste(sp.split()[-1], (ox, oy))
        out = rgb.convert("RGBA"); out.putalpha(al)
        out.save(img_dir / f"{stem}.png")
        (img_dir / f"{stem}.txt").write_text(
            f"{trigger}, {desc}, full body, on white background, retro jrpg game sprite, "
            f"limited palette, bold dark outline, flat shading")
        n += 1
    return n


def _toml(remote_img_dir: str, repeats: int, alpha_mask: bool = True) -> str:
    am = "  alpha_mask = true\n" if alpha_mask else ""
    return ('[general]\ncaption_extension = ".txt"\nshuffle_caption = true\nkeep_tokens = 1\n\n'
            '[[datasets]]\nresolution = 1024\nbatch_size = 2\nenable_bucket = true\n'
            'bucket_no_upscale = true\n\n  [[datasets.subsets]]\n'
            f'  image_dir = "{remote_img_dir}"\n  num_repeats = {repeats}\n' + am)


def _train_ps1(name: str, steps: int) -> str:
    # No Start-Transcript: it does not capture sd-scripts' native tqdm output. Instead we
    # let accelerate's stdout/stderr flow back over SSH so the backend can stream + parse
    # live step progress (see _stream_train). PYTHONUNBUFFERED keeps tqdm flushing.
    venv = config.BOX_TRAIN_VENV
    return (
        "$ErrorActionPreference = 'Stop'\n"
        "$env:PYTHONUNBUFFERED = '1'\n"
        f"$venv = '{venv}'\n"
        "try {\n"
        f"  Set-Location {config.BOX_SDSCRIPTS}\n"
        "  try { Invoke-RestMethod -Uri http://127.0.0.1:8188/free -Method Post -Body '{\"unload_models\":true,\"free_memory\":true}' -ContentType 'application/json' -TimeoutSec 15 | Out-Null } catch {}\n"
        "  Start-Sleep -Seconds 2\n"
        "  & \"$venv\\Scripts\\accelerate.exe\" launch --num_cpu_threads_per_process 1 --num_processes 1 --num_machines 1 --mixed_precision bf16 --dynamo_backend no sdxl_train_network.py "
        f"--pretrained_model_name_or_path \"{config.SDXL_CKPT_PATH}\" "
        f"--dataset_config \"{config.BOX_SFLORA}\\{name}.toml\" "
        f"--output_dir \"{config.BOX_SFLORA}\\output\" "
        f"--output_name {name} --network_module networks.lora --network_dim 16 --network_alpha 8 "
        "--learning_rate 1e-4 --unet_lr 1e-4 --text_encoder_lr 1e-5 --optimizer_type AdamW "
        "--lr_scheduler cosine --lr_warmup_steps 100 --lr_scheduler_num_cycles 3 "
        f"--max_train_steps {steps} --train_batch_size 2 --resolution 1024,1024 --enable_bucket --bucket_no_upscale "
        "--mixed_precision bf16 --save_precision bf16 --gradient_checkpointing --cache_latents --cache_latents_to_disk "
        "--sdpa --min_snr_gamma 5 --noise_offset 0.0357 --shuffle_caption --keep_tokens 1 "
        "--save_model_as safetensors --save_every_n_epochs 2 --seed 42\n"
        "  if ($LASTEXITCODE -eq 0) { Write-Output 'SF_DONE' } else { Write-Output ('SF_ERROR: exit ' + $LASTEXITCODE) }\n"
        "} catch { Write-Output ('SF_ERROR: ' + $_.Exception.Message) }\n")


async def _sh(*argv, timeout: float | None = None) -> tuple[int, str, str]:
    proc = await asyncio.create_subprocess_exec(
        *argv, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
    out, err = await asyncio.wait_for(proc.communicate(), timeout)
    return proc.returncode or 0, out.decode("utf-8", "replace"), err.decode("utf-8", "replace")


_PROG = re.compile(r"(\d+)\s*/\s*(\d+)\s*\[")   # tqdm "k/N [elapsed<remaining]"


async def _stream_train(box: str, name: str, steps: int, job: dict) -> tuple[int, str]:
    """Run the box training over SSH, STREAMING sd-scripts' output (its tqdm goes to
    stderr → merged into stdout) so the job exposes live step/percent + recent log lines
    for the WebUI. Per-read timeout catches a hang (no output) without capping total time."""
    proc = await asyncio.create_subprocess_exec(
        "ssh", "-o", "ConnectTimeout=20", box,
        f"powershell -ExecutionPolicy Bypass -File {config.BOX_SFLORA}\\{name}.ps1",
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT)
    buf = b""; full: list[str] = []
    try:
        while True:
            chunk = await asyncio.wait_for(proc.stdout.read(4096), timeout=config.TRAIN_TIMEOUT)
            if not chunk:
                break
            buf += chunk
            parts = re.split(rb"[\r\n]", buf)
            buf = parts.pop()                       # keep trailing partial token
            for raw in parts:
                line = raw.decode("utf-8", "replace").strip()
                if not line:
                    continue
                full.append(line)
                m = _PROG.search(line)
                if m and int(m.group(2)) == steps:   # the TRAINING bar (not latent-cache k/26)
                    job["step"] = int(m.group(1)); job["total"] = steps
                    job["percent"] = round(100 * job["step"] / steps)
                    job["last"] = line[-180:]
                elif "%" not in line:                # keep meaningful non-tqdm lines as a log
                    job["log"] = (job.get("log", []) + [line[-180:]])[-12:]
    finally:
        with contextlib.suppress(Exception):
            await proc.wait()
    return proc.returncode or 0, "\n".join(full[-80:])


async def _run_job(job_id: str, name: str, sprites, trigger: str, repeats: int, steps: int):
    job = _jobs[job_id]
    box = config.BOX_SSH
    try:
        job["status"] = "building dataset"
        local = config.CACHE / f"train_{name}"
        img = local / "img" / f"{repeats}_set"
        count = _build_dataset(sprites, trigger, img)
        if count == 0:
            raise RuntimeError("no source sprites found for the dataset")
        (local / f"{name}.toml").write_text(_toml(f"{config.BOX_SFLORA_FS}/train_{name}/img/{repeats}_set", repeats))
        (local / f"{name}.ps1").write_text(_train_ps1(name, steps))
        job["images"] = count

        job["status"] = "uploading to box"
        rc, o, e = await _sh("scp", "-r", "-o", "ConnectTimeout=20", str(local),
                             f"{box}:sf-lora/train_{name}", timeout=300)
        if rc != 0:
            raise RuntimeError(f"scp dataset failed: {e[-300:]}")
        # toml + ps1 to the expected box locations
        rc, o, e = await _sh("scp", "-o", "ConnectTimeout=20",
                             str(local / f"{name}.toml"), str(local / f"{name}.ps1"),
                             f"{box}:sf-lora/", timeout=120)
        if rc != 0:
            raise RuntimeError(f"scp config failed: {e[-300:]}")

        job.update(status="training on box", step=0, total=steps, percent=0, log=[])
        rc, full = await _stream_train(box, name, steps, job)
        if "SF_DONE" not in full:
            raise RuntimeError(f"training failed: {full[-600:]}")

        job["status"] = "installing LoRA into ComfyUI"
        rc, o, e = await _sh("ssh", "-o", "ConnectTimeout=20", box,
                             f"powershell -NoProfile -Command \"Copy-Item {config.BOX_SFLORA}\\output\\{name}.safetensors {config.COMFY_LORAS}\\ -Force\"",
                             timeout=120)
        if rc != 0:
            raise RuntimeError(f"copy LoRA failed: {e[-300:]}")
        # make the freshly-trained LoRA the active style LoRA immediately (runtime, no
        # code edit) so generate_sprite(style_lora=True) uses it right away.
        config.STYLE_LORA = f"{name}.safetensors"
        config.STYLE_LORA_TRIGGER = trigger
        job.update(status="done", lora=f"{name}.safetensors", trigger=trigger)
    except Exception as ex:
        job.update(status="error", error=str(ex))


def start(name: str | None = None, sprites=None, trigger: str = "sfrpg",
          repeats: int = 10, steps: int = 1500) -> dict:
    """Kick off a style-LoRA training job on the box (background). Returns a job_id to poll.
    sprites = list of (sprite_stem, subject_desc); default = the canonical house-style set."""
    name = name or "sprite-style"
    sprites = sprites or HOUSE_STYLE_SPRITES
    jid = f"train-{abs(hash((name, steps, len(sprites)))) % 10**8:08d}"
    _jobs[jid] = {"status": "starting", "name": name, "lora": None}
    asyncio.create_task(_run_job(jid, name, sprites, trigger, repeats, steps))
    return {"job_id": jid, "status": "starting", "name": name, "expect_lora": f"{name}.safetensors"}


def status(job_id: str) -> dict:
    return {"job_id": job_id, **_jobs.get(job_id, {"status": "unknown"})}


# ============ Character LoRA from a generated bible (C') ============
# Train a CHARACTER LoRA from the high-res, on-model panels a bible job produced
# (generated/bible_<name>_panels/). Identity-consistent data -> a robust char LoRA.
CHAR_PANELS = {
    "turn_front": "standing front view", "turn_34": "three-quarter view",
    "turn_side": "side view", "turn_back": "rear view from behind",
    "body_front": "front body reference", "body_back": "rear body reference",
    "ex_neutral": "neutral face", "ex_smile": "smiling face", "ex_angry": "angry face",
    "ex_sad": "sad face", "ex_surp": "surprised face", "ex_shy": "shy face",
    "act_cast": "casting action pose", "act_run": "running action pose", "act_jump": "jumping action pose",
    "cos_casual": "casual outfit", "cos_armor": "armor outfit", "cos_dress": "formal dress",
}


def _build_char_dataset(bible_name: str, trigger: str, img_dir: Path) -> int:
    """Build a kohya dataset from a bible's high-res panels (white-bg RGB, centered 1024,
    trigger+pose caption). No matte (panels are clean on white; train without alpha_mask)."""
    pdir = config.GENERATED / f"bible_{bible_name}_panels"
    img_dir.mkdir(parents=True, exist_ok=True)
    n = 0
    for key, pose in CHAR_PANELS.items():
        p = pdir / f"{key}.png"
        if not p.exists():
            continue
        im = Image.open(p).convert("RGB"); im.thumbnail((960, 960), Image.LANCZOS)
        canvas = Image.new("RGB", (1024, 1024), (255, 255, 255))
        canvas.paste(im, ((1024 - im.width) // 2, (1024 - im.height) // 2))
        canvas.save(img_dir / f"{key}.png")
        (img_dir / f"{key}.txt").write_text(
            f"{trigger}, {pose}, full body, retro jrpg game sprite, plain white background")
        n += 1
    return n


def _char_train_ps1(name: str, steps: int) -> str:
    venv = config.BOX_TRAIN_VENV
    return (
        "$ErrorActionPreference = 'Stop'\n$env:PYTHONUNBUFFERED = '1'\n"
        f"try {{\n  Set-Location {config.BOX_SDSCRIPTS}\n"
        "  & \"" + venv + "\\Scripts\\accelerate.exe\" launch --num_cpu_threads_per_process 1 --num_processes 1 --num_machines 1 --mixed_precision bf16 --dynamo_backend no sdxl_train_network.py "
        f"--pretrained_model_name_or_path \"{config.SDXL_CKPT_PATH}\" "
        f"--dataset_config \"{config.BOX_SFLORA}\\{name}.toml\" "
        f"--output_dir \"{config.BOX_SFLORA}\\output\" "
        f"--output_name {name} --network_module networks.lora --network_dim 32 --network_alpha 16 "
        "--learning_rate 1e-4 --unet_lr 1e-4 --text_encoder_lr 1e-5 --optimizer_type AdamW "
        "--lr_scheduler cosine --lr_warmup_steps 80 --lr_scheduler_num_cycles 3 "
        f"--max_train_steps {steps} --train_batch_size 2 --resolution 1024,1024 --enable_bucket --bucket_no_upscale "
        "--mixed_precision bf16 --save_precision bf16 --gradient_checkpointing --cache_latents --cache_latents_to_disk "
        "--sdpa --min_snr_gamma 5 --noise_offset 0.0357 --shuffle_caption --keep_tokens 1 "
        "--save_model_as safetensors --save_every_n_epochs 4 --seed 42\n"
        "  if ($LASTEXITCODE -eq 0) { Write-Output 'SF_DONE' } else { Write-Output ('SF_ERROR exit ' + $LASTEXITCODE) }\n"
        "} catch { Write-Output ('SF_ERROR: ' + $_.Exception.Message) }\n")


async def _svc(box: str, action: str):
    await _sh("ssh", "-o", "ConnectTimeout=20", box,
              f"powershell -NoProfile -Command \"{action}-Service ComfyUI -Force; Start-Sleep 4\"", timeout=60)


async def _run_char_job(job_id: str, bible_name: str, trigger: str, name: str, steps: int):
    job = _jobs[job_id]; box = config.BOX_SSH
    comfy_stopped = False
    try:
        job["status"] = "building dataset from bible panels"
        local = config.CACHE / f"char_{name}"
        cnt = await asyncio.to_thread(_build_char_dataset, bible_name, trigger, local / "img" / "10_set")
        if cnt == 0:
            raise RuntimeError(f"no bible panels for '{bible_name}' — run generate_character_bible first")
        (local / f"{name}.toml").write_text(_toml(f"{config.BOX_SFLORA_FS}/char_{name}/img/10_set", 10, alpha_mask=False))
        (local / f"{name}.ps1").write_text(_char_train_ps1(name, steps))
        job["images"] = cnt

        job["status"] = "uploading to box"
        rc, o, e = await _sh("scp", "-r", "-o", "ConnectTimeout=20", str(local / "img"),
                             f"{box}:sf-lora/char_{name}/img", timeout=300)
        if rc != 0:
            raise RuntimeError(f"scp dataset failed: {e[-300:]}")
        rc, o, e = await _sh("scp", "-o", "ConnectTimeout=20",
                             str(local / f"{name}.toml"), str(local / f"{name}.ps1"),
                             f"{box}:sf-lora/", timeout=120)
        if rc != 0:
            raise RuntimeError(f"scp config failed: {e[-300:]}")

        job["status"] = "freeing GPU (stopping ComfyUI)"
        await _svc(box, "Stop"); comfy_stopped = True
        job.update(status="training on box", step=0, total=steps, percent=0, log=[])
        rc, full = await _stream_train(box, name, steps, job)
        await _svc(box, "Start"); comfy_stopped = False           # restart ComfyUI
        if "SF_DONE" not in full:
            raise RuntimeError(f"training failed: {full[-500:]}")

        job["status"] = "installing LoRA into ComfyUI"
        rc, o, e = await _sh("ssh", "-o", "ConnectTimeout=20", box,
                             f"powershell -NoProfile -Command \"Copy-Item {config.BOX_SFLORA}\\output\\{name}.safetensors {config.COMFY_LORAS}\\ -Force\"",
                             timeout=120)
        if rc != 0:
            raise RuntimeError(f"copy LoRA failed: {e[-300:]}")
        job.update(status="done", lora=f"{name}.safetensors", trigger=trigger)
    except Exception as ex:
        if comfy_stopped:
            with contextlib.suppress(Exception):
                await _svc(box, "Start")                          # never leave ComfyUI down
        job.update(status="error", error=str(ex))


def start_character(bible_name: str, trigger: str | None = None,
                    name: str | None = None, steps: int = 1500) -> dict:
    """Train a CHARACTER LoRA from a generated bible's panels (generated/bible_<bible_name>_panels/).
    Stops ComfyUI during training (clean GPU), restarts + installs after. Returns a job_id."""
    name = name or f"char-{bible_name}"
    trigger = trigger or ("sf" + "".join(ch for ch in bible_name.lower() if ch.isalnum()))
    jid = f"char-{abs(hash((name, steps))) % 10**8:08d}"
    _jobs[jid] = {"status": "starting", "name": name, "trigger": trigger, "lora": None}
    asyncio.create_task(_run_char_job(jid, bible_name, trigger, name, steps))
    return {"job_id": jid, "status": "starting", "name": name, "trigger": trigger,
            "expect_lora": f"{name}.safetensors"}

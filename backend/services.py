"""Shared service layer — the single implementation behind BOTH the human WebUI
(FastAPI endpoints) and the agent MCP (FastMCP tools). Scar-tissue gates live here
so neither face can bypass them.
"""
from __future__ import annotations
import io
import json
import re
import subprocess
import time
import uuid
from pathlib import Path

from PIL import Image

from . import config, audit, workflows
from .comfy_client import ComfyClient

# set by app lifespan
comfy: ComfyClient | None = None


# ---------- candidate store (local .cache) ----------
def _new_id(kind: str) -> str:
    return f"{kind}-{uuid.uuid4().hex[:12]}"


def _path(cid: str) -> Path:
    return config.GENERATED / f"{cid}.png"


def save_candidate(img: Image.Image, kind: str = "cand", meta: dict | None = None) -> str:
    cid = _new_id(kind)
    img.convert("RGBA").save(_path(cid))
    if meta is not None:
        (_path(cid).with_suffix(".json")).write_text(json.dumps(meta))
    return cid


def load_candidate(cid: str) -> Image.Image:
    p = _path(cid)
    if not p.exists():
        raise FileNotFoundError(f"unknown candidate id: {cid}")
    return Image.open(p).convert("RGBA")


def save_mask(data: bytes) -> str:
    """Store an outfit mask PNG (painted region = opaque) as a candidate, return id."""
    return save_candidate(audit.load(data), "mask", {"kind": "mask"})


# SAM2 runs in an ISOLATED venv (torch+ultralytics) so it can't disturb the main
# backend venv or the GPU box's ComfyUI torch env.
SAM2_PY = config.ROOT / ".venv-sam2" / "bin" / "python"
SAM2_BRIDGE = Path(__file__).resolve().parent / "sam2_bridge.py"


def sam2_mask(base: str, points: list) -> dict:
    """Segment an outfit region from a base sprite via SAM2 foreground click points
    (isolated venv subprocess). Saves the region as an outfit-mask candidate."""
    base_path = resolve_base(base)
    if not SAM2_PY.exists():
        raise RuntimeError("SAM2 isolated venv (.venv-sam2) is not installed.")
    cid = _new_id("mask")
    out = _path(cid)
    r = subprocess.run([str(SAM2_PY), str(SAM2_BRIDGE), str(base_path),
                        json.dumps(points), str(out)],
                       capture_output=True, text=True, timeout=180)
    if r.returncode != 0 or not out.exists():
        raise RuntimeError(f"SAM2 failed (rc={r.returncode}): {r.stderr[-400:]}")
    return {"id": cid, "points": points}


def candidate_bytes(cid: str) -> bytes:
    return _path(cid).read_bytes()


# ---------- base sprite resolution (rpgdev) ----------
def resolve_base(name_or_path: str) -> Path:
    p = Path(name_or_path)
    if p.exists():
        return p
    cand = config.RPGDEV_SPRITES / name_or_path
    if not cand.suffix:
        cand = cand.with_suffix(".png")
    if cand.exists():
        return cand
    raise FileNotFoundError(f"base sprite not found: {name_or_path}")


# ---------- services ----------
async def gpu_status() -> dict:
    s = await comfy.system_stats()
    dev = (s.get("devices") or [{}])[0]
    total = dev.get("vram_total", 0)
    free = dev.get("vram_free", 0)
    return {
        "comfy_up": True,
        "device": dev.get("name"),
        "vram_total_mb": round(total / 1048576),
        "vram_used_mb": round((total - free) / 1048576),
        "vram_free_mb": round(free / 1048576),
        "comfyui_version": (s.get("system") or {}).get("comfyui_version"),
    }


def bg_instruction(name: str) -> str:
    """Scar tissue: force the edit model to KEEP the distinctive flat background
    (instructing the color by NAME — auto-picked, never black)."""
    return (f" Keep the background exactly as in the input image: a single, uniform, "
            f"perfectly flat, solid {name} color. Do NOT add any texture, pattern, "
            f"checkerboard, grid, gradient, noise, or shading to the background. The "
            f"background must stay one flat solid {name}, unchanged from the input.")


async def _run_save(workflow: dict, *, base: Image.Image | None, kind: str, meta: dict,
                    chroma: tuple | None = None, matte: bool = False,
                    mask: Image.Image | None = None) -> dict:
    hist = await comfy.run(workflow)
    imgs = comfy.outputs_images(hist)
    if not imgs:
        status = (hist.get("status") or {})
        raise RuntimeError(f"ComfyUI produced no image. status={json.dumps(status)[:500]}")
    raw = await comfy.get_image(imgs[0]["filename"], imgs[0].get("subfolder", ""),
                                imgs[0].get("type", "output"))
    img = audit.load(raw)
    extra: dict = {}
    if chroma is not None:
        name, nominal = chroma
        bg = audit.sample_bg_color(img)
        comply = round(audit.color_dist(bg, nominal), 1)
        extra = {"chroma_name": name, "chroma_nominal": list(nominal),
                 "chroma_produced_rgb": list(bg), "chroma_compliance_dist": comply}
        if comply <= config.CHROMA_COMPLIANCE_MAX and not audit.is_near_black(bg):
            img = audit.chroma_key(img, bg)
            # masked variant: paste edited pixels only inside the outfit mask, keep the
            # base everywhere else -> bbox guaranteed identical (the #2 structural fix).
            if mask is not None and base is not None:
                img = audit.composite_inside_mask(base, img, mask)
                extra["masked"] = True
                extra["outside_mask_identical"] = audit.outside_mask_identical(img, base, mask)
            a = audit.fit_to_base(img, base, chroma=bg)
        else:
            # the edit model ignored the background instruction -> surface loudly and
            # do NOT key (no silent fallback). Candidate fails the adoption gate.
            a = audit.fit_to_base(img, base)
            a["pass"] = False
            a["reason"] = (f"background is not the requested {name}: produced "
                           f"rgb{tuple(bg)} (dist {comply} > {config.CHROMA_COMPLIANCE_MAX})")
    elif matte:
        img = audit.matte(img)
        a = audit.fit_to_base(img, base)
    else:
        a = audit.fit_to_base(img, base)
    cid = save_candidate(img, kind, {**meta, **extra})
    return {"id": cid, **meta, **extra, "audit": a}


async def generate_variant(base: str, prompt: str, *, negative: str = "",
                           count: int = 4, seed: int | None = None,
                           steps: int = 4, cfg: float = 1.0,
                           denoise: float | None = None,
                           mask_id: str | None = None) -> dict:
    """Edit a base sprite (e.g. damaged variant) via Qwen-Image-Edit.

    Per design: composite the subject onto a DISTINCTIVE non-black color (auto-picked
    farthest from its own palette, default magenta), instruct the model to keep that
    flat background, then key that color out and audit. Near-black pixels are the
    character's own dark outline (kept), never treated as leak.

    denoise defaults to config.VARIANT_DENOISE (0.7): full re-gen (1.0) re-poses the
    character and breaks the bbox; 0.7 keeps the base pose AND shows clear outfit
    damage. Higher = more damage but more pose drift (visible in the bbox audit).
    """
    d = config.VARIANT_DENOISE if denoise is None else float(denoise)
    base_path = resolve_base(base)
    base_img = audit.load(base_path)
    mask_img = load_candidate(mask_id) if mask_id else None
    chroma_name, chroma_rgb = audit.pick_chroma(base_img)
    inp = audit.composite_on(base_img, chroma_rgb)
    buf = io.BytesIO(); inp.save(buf, "PNG")
    up_name = await comfy.upload_image(buf.getvalue(), f"{base_path.stem}_{chroma_name}.png")
    full_prompt = prompt + bg_instruction(chroma_name)
    base_seed = seed if seed is not None else int(time.time()) % 100000
    cands = []
    for i in range(count):
        s = base_seed + i * 1000
        wf = workflows.qwen_edit_variant(up_name, full_prompt, negative=negative,
                                         seed=s, steps=steps, cfg=cfg, denoise=d)
        meta = {"kind": "variant", "base": base_path.name, "prompt": prompt,
                "seed": s, "denoise": d, "masked": mask_id is not None}
        cands.append(await _run_save(wf, base=base_img, kind="variant", meta=meta,
                                     chroma=(chroma_name, chroma_rgb), mask=mask_img))
    return {"base": base_path.name, "chroma": chroma_name, "denoise": d,
            "masked": mask_id is not None, "candidates": cands}


# Pale / low-contrast-against-grey subjects: their silhouette is near light-grey so the matte
# clips them — these get a GREEN background automatically (bg="auto"). Conservative: only strong
# pale signals (skin/hair/element/translucency) trigger it; outfit colour alone does not.
_PALE_RE = re.compile(
    r"\b(pale|fair[- ]skin|white[- ]skin|white[- ]hair|silver[- ]hair|platinum|"
    r"light[- ]blue|pale[- ]blue|sky[- ]blue|translucent|transparent|see[- ]through|"
    r"water[- ](?:spirit|elemental|girl|mage|nymph)|aqua|ice|frost|snow|mermaid|"
    r"ghost|spectral|albino|ivory|porcelain)\b", re.I)


def _auto_bg(prompt: str) -> str:
    """Pick the generation background: GREEN for pale/low-contrast subjects (gives the matte
    contrast), else GREY. Used when bg='auto' (the default). Conservative by design."""
    return "green" if _PALE_RE.search(prompt or "") else "grey"


async def generate_sprite(prompt: str, *, negative: str = "lowres, blurry, jpeg artifacts, "
                          "thin delicate line art, hairline outline, watermark, text",
                          width: int = 1024, height: int = 1024,
                          count: int = 4, seed: int | None = None,
                          steps: int = 28, cfg: float = 6.0,
                          style_lora: bool = False, lora_strength: float | None = None,
                          lora_name: str | None = None, lora_trigger: str | None = None,
                          control_base: str | None = None, control_strength: float = 0.55,
                          bg: str = "grey") -> dict:
    """SDXL (Illustrious) txt2img -> RGBA sprite batch. The mandatory style phrase is
    force-injected (scar tissue: never omit). Transparency = rembg matting; LayerDiffuse
    is NOT used (incoherent on Illustrious-XL).

    Pick ONE LoRA: lora_name (explicit, e.g. a CHARACTER LoRA like 'char-water.safetensors'
    with lora_trigger 'sfwater') OVERRIDES style_lora; else style_lora=True applies the
    default style LoRA (config.STYLE_LORA) + its trigger.
    control_base=<sprite name> applies a ControlNet Canny structure hint (Phase 1d)."""
    # Use the MATTABLE generation style (NOT STYLE_PHRASE — its literal "chunky pixel
    # clusters" pixelates the whole frame and defeats matting). Transparency = rembg
    # matting; dot granularity comes later via pixelize() (docs/03). A CLEAN, contrasting
    # background is essential: thin elements (hair tips, fingers) over a busy/same-colored
    # background get clipped by any matte — keep the bg flat/plain (verified 2026-06-17).
    # bg: generation background colour. "auto" (default) = GREY for normal subjects, GREEN for
    # PALE characters (white/pale-blue water spirits) whose own colour is near light-grey and
    # confuses the matte. "grey"/"green"/"magenta"/"blue" force a specific colour. Non-pale
    # prompts resolve to grey = original behaviour (no regression); only pale prompts change.
    _BG = {
        "grey": "plain flat solid light-grey",
        "green": "plain flat solid chroma-key green (saturated #00b140)",
        "magenta": "plain flat solid saturated magenta (#ff00cc)",
        "blue": "plain flat solid saturated cobalt-blue",
    }
    bg_resolved = _auto_bg(prompt) if bg == "auto" else (bg if bg in _BG else "grey")
    bg_phrase = _BG[bg_resolved]
    full = (f"{prompt}, {config.GEN_STYLE_PHRASE}, single character, full body, centered, "
            f"isolated on a {bg_phrase} background, no scenery, no foliage")
    negative = (negative + ", busy background, foliage, vines, plants, leaves, scenery, "
                "cluttered background, gradient background").strip(", ")
    use_lora = None; trig = None
    if lora_name:                                       # explicit LoRA (e.g. character LoRA)
        use_lora = lora_name; trig = lora_trigger
    elif style_lora:                                    # default style LoRA
        use_lora = config.STYLE_LORA; trig = config.STYLE_LORA_TRIGGER
    if trig:
        full = f"{trig}, {full}"                        # trigger first (keep_tokens=1)
    lstr = config.STYLE_LORA_STRENGTH if lora_strength is None else float(lora_strength)
    control_image = None
    if control_base:                                    # ControlNet structure reference
        ref = audit.load(resolve_base(control_base))
        ref_rgb = audit.composite_on(ref, (255, 255, 255))   # white bg so Canny sees the silhouette
        buf = io.BytesIO(); ref_rgb.save(buf, "PNG")
        control_image = await comfy.upload_image(buf.getvalue(),
                                                  f"sfctrl_{resolve_base(control_base).stem}.png")
    base_seed = seed if seed is not None else int(time.time()) % 100000
    cands = []
    for i in range(count):
        s = base_seed + i * 1000
        wf = workflows.sdxl_generate(full, negative=negative, width=width,
                                     height=height, seed=s, steps=steps, cfg=cfg,
                                     lora_name=use_lora, lora_strength=lstr,
                                     control_image=control_image, control_strength=control_strength)
        meta = {"kind": "sprite", "prompt": full, "seed": s, "canvas": [width, height],
                "lora": use_lora, "control_base": control_base, "bg": bg_resolved}
        cands.append(await _run_save(wf, base=None, kind="sprite", meta=meta, matte=True))
    return {"candidates": cands, "bg": bg_resolved, "bg_requested": bg}


def make_transparent(image_id: str) -> dict:
    img = load_candidate(image_id)
    out = audit.matte(img)
    cid = save_candidate(out, "matte", {"kind": "matte", "src": image_id})
    return {"id": cid, "audit": audit.fit_to_base(out, None)}


def pixelize(image_id: str, *, block: float = 2.4, posterize_step: int = 28) -> dict:
    """Deterministic NEAREST downscale + palette posterize (rpgdev dot granularity)."""
    img = load_candidate(image_id).convert("RGBA")
    w, h = img.size
    sw, sh = max(1, round(w / block)), max(1, round(h / block))
    small = img.resize((sw, sh), Image.NEAREST).resize((w, h), Image.NEAREST)
    import numpy as np
    a = np.asarray(small).astype(int)
    a[..., :3] = (a[..., :3] // posterize_step) * posterize_step
    out = Image.fromarray(a.astype("uint8"), "RGBA")
    cid = save_candidate(out, "pixel", {"kind": "pixelize", "src": image_id,
                                        "block": block, "posterize": posterize_step})
    return {"id": cid, "kind": "pixelize", "src": image_id, "audit": audit.fit_to_base(out, None)}


def fit_to_base(candidate_id: str, base: str | None = None) -> dict:
    cand = load_candidate(candidate_id)
    base_img = audit.load(resolve_base(base)) if base else None
    return audit.fit_to_base(cand, base_img)


def adopt(candidate_id: str, target_name: str, *, pair_with: str | None = None,
          force: bool = False) -> dict:
    """Gate then write into rpgdev/public/assets/sprites. No silent fallback."""
    cand = load_candidate(candidate_id)
    base_img = audit.load(resolve_base(pair_with)) if pair_with else None
    a = audit.fit_to_base(cand, base_img)
    if not a["pass"] and not force:
        return {"ok": False, "stage": "gate", "reason": "adoption gate failed", "audit": a}
    if not target_name.endswith(".png"):
        target_name += ".png"
    target = config.RPGDEV_SPRITES / target_name
    cand.convert("RGBA").save(target)
    config.ADOPTION_LOG.parent.mkdir(parents=True, exist_ok=True)
    rec = {"at": time.strftime("%Y-%m-%dT%H:%M:%S"), "candidate": candidate_id,
           "target": str(target), "pair_with": pair_with, "forced": force, "audit": a}
    with config.ADOPTION_LOG.open("a") as f:
        f.write(json.dumps(rec) + "\n")
    return {"ok": True, "target": str(target), "audit": a}

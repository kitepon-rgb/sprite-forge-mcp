"""sprite-forge backend: ONE process, TWO faces.

- Human WebUI  : FastAPI REST + (static SPA in web/).
- Agent MCP    : FastMCP streamable-HTTP app mounted at /mcp.
Both call the SAME services.* layer (no logic duplication, no gate bypass).

The MCP app is built BEFORE FastAPI() so its lifespan (the StreamableHTTP session
manager) can be nested inside the parent lifespan — a mounted sub-app's lifespan is
NOT run by Starlette, so without this /mcp returns "Task group is not initialized".

Run:  uvicorn backend.app:app --host 127.0.0.1 --port 8765
"""
from __future__ import annotations
import base64
import contextlib
import json
from pathlib import Path

from fastapi import FastAPI, Body
from fastapi.responses import Response, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from fastmcp import FastMCP   # loud import: MCP is a first-class face, not optional

from . import config, services, training, bible, promptcraft
from .comfy_client import ComfyClient

WEB = Path(__file__).resolve().parent.parent / "web"


# ---------------- MCP (agent face) — built first so its lifespan can be nested ----------------
MCP_INSTRUCTIONS = """\
sprite-forge — a local studio for generating game CHARACTER SPRITES for the sibling rpgdev
project. Image generation runs on a remote ComfyUI GPU box; this MCP is the agent face (the
human WebUI shares the SAME logic). All outputs are RGBA sprites (transparent corners).

RECOMMENDED PIPELINE — create a new character:
 1. craft_prompt(idea)            -> a detailed danbooru-tag prompt (optional; you can pass your own).
 2. generate_sprite(prompt, count=4, style_lora=True)
                                  -> candidates [{id, audit}]. Pick the best candidate `id` (the "base figure").
 3. generate_character_bible(source=<candidate id OR sprite name>, name="...")
                                  -> long job; poll bible_status(job_id) until status=="done".
                                     Produces a model sheet (turnaround/expressions/poses) + per-panel training data.
 4. train_character_lora(bible_name="...")
                                  -> long job (~20-30 min); poll train_status(job_id) until done;
                                     returns the installed lora filename + trigger word.
 5. generate_sprite(prompt, lora_name="<that .safetensors>", lora_trigger="<trigger>")
                                  -> that character in any new pose/costume.
 6. adopt(candidate_id, target_name)
                                  -> write the chosen sprite into rpgdev. IRREVERSIBLE — only with explicit user intent.

DAMAGE / EDIT path: generate_variant(base=<sprite name>, prompt) edits an existing sprite (e.g. a
battle-damaged version). denoise=0.7 keeps the pose; pass mask_id for pixel-exact bbox.
STYLE LoRA: train_style_lora() trains the shared house art-style LoRA from the canonical sprites.
DERIVE: make_transparent(id) re-mattes; pixelize(id) adds dot granularity; fit_to_base(id, base) re-audits geometry.

KEY CONCEPTS:
 - candidate id: returned by generate_*; pass to make_transparent/pixelize/fit_to_base/adopt and as a bible `source`.
 - audit: each candidate has an audit dict; audit.pass must be True (corners transparent, canvas/bbox match) before adopt.
 - long jobs: generate_character_bible / train_* return {job_id}; poll bible_status / train_status
   (status: starting -> ... -> done | error; training reports step/total/percent/log).
 - discovery: list_sprites() = valid base/source/control_base names; list_loras() = installed lora_name values.
 - gpu_status() confirms the GPU box is reachable before heavy work.

RULES (do not violate): outputs are RGBA with four transparent corners, never a black background; the
retro-JRPG style phrase is auto-injected; adopt writes irreversibly into the sibling rpgdev project —
only when the user explicitly asks. uvicorn must be running for any of this to work."""


def _build_mcp_app():
    mcp = FastMCP("sprite-forge", instructions=MCP_INSTRUCTIONS)

    @mcp.tool
    async def gpu_status() -> dict:
        """ComfyUI reachability + VRAM (residency check). Call before heavy generation/training."""
        return await services.gpu_status()

    @mcp.tool
    def list_sprites() -> dict:
        """Discovery: adoptable rpgdev sprite names — the valid values for generate_variant `base`,
        generate_sprite `control_base`, and generate_character_bible `source`."""
        return {"sprites": sorted(p.name for p in config.RPGDEV_SPRITES.glob("*.png"))}

    @mcp.tool
    async def list_loras() -> dict:
        """Discovery: installed LoRA filenames (the valid values for generate_sprite `lora_name`)."""
        rc, o, e = await training._sh(
            "ssh", "-o", "ConnectTimeout=15", config.BOX_SSH,
            f"cmd /c dir /b {config.COMFY_LORAS}\\*.safetensors", timeout=20)
        return {"loras": [l.strip() for l in o.splitlines() if l.strip().lower().endswith(".safetensors")]}

    @mcp.tool
    async def generate_variant(base: str, prompt: str, count: int = 4,
                               seed: int | None = None,
                               denoise: float | None = None,
                               mask_id: str | None = None) -> dict:
        """Edit a base rpgdev sprite (e.g. 'hero') into a damaged/variant version
        via Qwen-Image-Edit: subject is composited onto an auto-picked distinctive
        color, that color is keyed out, and each candidate is audited (corners clear,
        bbox<=1px vs base). denoise defaults to 0.7 (keeps base pose, shows clear
        outfit damage; raise for more damage at the cost of pose drift). Pass mask_id
        (from an uploaded outfit mask) to GUARANTEE bbox match: only the masked region
        is edited and the base is pasted back everywhere else (pixel-exact)."""
        return await services.generate_variant(base, prompt, count=count, seed=seed,
                                               denoise=denoise, mask_id=mask_id)

    @mcp.tool
    async def craft_prompt(idea: str, kind: str = "character", project: str | None = None) -> dict:
        """Expand a rough idea into a detailed generation prompt by shelling the user's own
        `claude -p` / `codex` CLI (cwd = a project root so it uses that project's CLAUDE.md/
        context). Returns {prompt, cli, model, project}. No Anthropic API key needed."""
        return await promptcraft.craft(idea, kind=kind, project=project)

    @mcp.tool
    async def generate_sprite(prompt: str, width: int = 1024, height: int = 1024,
                              count: int = 4, seed: int | None = None,
                              style_lora: bool = False, control_base: str | None = None,
                              lora_name: str | None = None, lora_trigger: str | None = None) -> dict:
        """Generate RGBA sprites: SDXL (Illustrious) txt2img + rembg matte. Mandatory
        retro style auto-injected. style_lora=True applies the default style LoRA; OR pass
        lora_name (+lora_trigger) for a CHARACTER LoRA (e.g. 'char-water.safetensors'/'sfwater')
        — that overrides style_lora. control_base=<sprite> = ControlNet Canny pose hint."""
        return await services.generate_sprite(prompt, width=width, height=height,
                                               count=count, seed=seed, style_lora=style_lora,
                                               control_base=control_base,
                                               lora_name=lora_name, lora_trigger=lora_trigger)

    @mcp.tool
    def make_transparent(image_id: str) -> dict:
        """Matte a candidate to clean RGBA (rembg anime)."""
        return services.make_transparent(image_id)

    @mcp.tool
    def pixelize(image_id: str, block: float = 2.4, posterize_step: int = 28) -> dict:
        """Deterministic NEAREST downscale + palette posterize."""
        return services.pixelize(image_id, block=block, posterize_step=posterize_step)

    @mcp.tool
    def fit_to_base(candidate_id: str, base: str | None = None) -> dict:
        """Audit a candidate vs a base: corners-transparent, canvas/bbox match (gate metrics)."""
        return services.fit_to_base(candidate_id, base)

    @mcp.tool
    def adopt(candidate_id: str, target_name: str, pair_with: str | None = None,
              force: bool = False) -> dict:
        """Gate then write the candidate into rpgdev/public/assets/sprites (irreversible)."""
        return services.adopt(candidate_id, target_name, pair_with=pair_with, force=force)

    @mcp.tool
    def train_style_lora(name: str = "sprite-style", trigger: str = "sfrpg",
                         steps: int = 1500, repeats: int = 10) -> dict:
        """Train a style LoRA on the GPU box from the rpgdev house-style sprites (backend
        builds the dataset, runs kohya sd-scripts over SSH, installs the LoRA into ComfyUI
        — no manual box steps). Long-running: returns a job_id; poll train_status."""
        return training.start(name=name, trigger=trigger, steps=steps, repeats=repeats)

    @mcp.tool
    def train_status(job_id: str) -> dict:
        """Poll a train_style_lora job (status / installed lora name / error)."""
        return training.status(job_id)

    @mcp.tool
    def generate_character_bible(source: str, char_desc: str | None = None,
                                 name: str | None = None, attr: str = "") -> dict:
        """Generate a full CHARACTER BIBLE / model sheet from a source sprite (turnaround
        + body ref + expressions + action + costumes + chibi + wardrobe + palette), every
        panel identity-consistent via Qwen-edit, composed into a clean labeled sheet.
        Long-running: returns a job_id; poll bible_status. The panels also save as clean
        character-LoRA training data under generated/bible_<name>_panels/."""
        return bible.start(source, char_desc=char_desc, name=name, attr=attr)

    @mcp.tool
    def bible_status(job_id: str) -> dict:
        """Poll a generate_character_bible job (status / done-count / sheet path / error)."""
        return bible.status(job_id)

    @mcp.tool
    def train_character_lora(bible_name: str, trigger: str | None = None,
                             name: str | None = None, steps: int = 1500) -> dict:
        """Train a CHARACTER LoRA from a generated bible's panels (run generate_character_bible
        first). Backend builds the dataset, stops ComfyUI for a clean GPU, trains kohya over
        SSH, restarts ComfyUI + installs the LoRA. Long-running: returns a job_id; poll
        train_status. Use it in generate_sprite via lora_name=<name>.safetensors + lora_trigger."""
        return training.start_character(bible_name, trigger=trigger, name=name, steps=steps)

    return mcp.http_app(path="/")


mcp_app = _build_mcp_app()


@contextlib.asynccontextmanager
async def lifespan(app: FastAPI):
    services.comfy = ComfyClient()
    await services.comfy.start()
    # Nest FastMCP's session-manager lifespan (Starlette won't run a mounted sub-app's).
    async with mcp_app.lifespan(app):
        yield
    await services.comfy.aclose()


app = FastAPI(title="sprite-forge", lifespan=lifespan)


# ---------------- REST (human WebUI face) ----------------
@app.get("/api/gpu")
async def api_gpu():
    return await services.gpu_status()


@app.post("/api/variant")
async def api_variant(body: dict = Body(...)):
    return await services.generate_variant(
        body["base"], body["prompt"], negative=body.get("negative", ""),
        count=int(body.get("count", 4)), seed=body.get("seed"),
        steps=int(body.get("steps", 4)), cfg=float(body.get("cfg", 1.0)),
        denoise=(float(body["denoise"]) if body.get("denoise") is not None else None),
        mask_id=body.get("mask_id"))


@app.post("/api/sam2_mask")
async def api_sam2_mask(body: dict = Body(...)):
    """Segment an outfit region from a base sprite via SAM2 click points (isolated
    venv). body = {base, points:[[x,y],...] in base-image pixels}. Returns a mask id."""
    return services.sam2_mask(body["base"], body["points"])


@app.post("/api/upload_mask")
async def api_upload_mask(body: dict = Body(...)):
    """Accept a painted outfit mask as a data URL / base64 PNG, store it, return id.
    The mask's OPAQUE pixels mark the region to edit; everything else keeps the base."""
    raw = body["png"]
    if "," in raw:
        raw = raw.split(",", 1)[1]   # strip data:image/png;base64,
    return {"id": services.save_mask(base64.b64decode(raw))}


@app.post("/api/craft_prompt")
async def api_craft_prompt(body: dict = Body(...)):
    """Expand a rough idea into a generation prompt via the user's own claude -p / codex CLI
    (run with cwd = a project root for that project's context). body: {idea, kind?, project?}."""
    try:
        return await promptcraft.craft(body["idea"], kind=body.get("kind", "character"),
                                       project=body.get("project"))
    except Exception as ex:
        return JSONResponse({"error": str(ex)}, status_code=502)


@app.post("/api/generate")
async def api_generate(body: dict = Body(...)):
    return await services.generate_sprite(
        body["prompt"], width=int(body.get("width", 1024)),
        height=int(body.get("height", 1024)), count=int(body.get("count", 4)),
        seed=body.get("seed"), steps=int(body.get("steps", 28)),
        cfg=float(body.get("cfg", 6.0)),
        style_lora=bool(body.get("style_lora", False)),
        lora_strength=(float(body["lora_strength"]) if body.get("lora_strength") is not None else None),
        lora_name=(body.get("lora_name") or None),
        lora_trigger=(body.get("lora_trigger") or None),
        control_base=(body.get("control_base") or None),
        control_strength=float(body.get("control_strength", 0.55)))


@app.post("/api/transparent")
async def api_transparent(body: dict = Body(...)):
    return services.make_transparent(body["image_id"])


@app.post("/api/pixelize")
async def api_pixelize(body: dict = Body(...)):
    return services.pixelize(body["image_id"], block=float(body.get("block", 2.4)),
                             posterize_step=int(body.get("posterize_step", 28)))


@app.post("/api/fit")
async def api_fit(body: dict = Body(...)):
    return services.fit_to_base(body["candidate_id"], body.get("base"))


@app.post("/api/adopt")
async def api_adopt(body: dict = Body(...)):
    return services.adopt(body["candidate_id"], body["target_name"],
                          pair_with=body.get("pair_with"), force=bool(body.get("force", False)))


@app.get("/api/progress")
async def api_progress():
    """SSE stream of live ComfyUI progress (sampling step / current node) so the
    WebUI can show a live bar during a (blocking) generation. Best-effort."""
    q = services.comfy.subscribe()

    async def gen():
        try:
            while True:
                ev = await q.get()
                if ev.get("type") in ("progress", "executing", "execution_start",
                                       "execution_success", "execution_error"):
                    yield f"data: {json.dumps(ev)}\n\n"
        finally:
            services.comfy.unsubscribe(q)

    return StreamingResponse(gen(), media_type="text/event-stream")


@app.get("/api/image/{cid}")
async def api_image(cid: str):
    try:
        return Response(content=services.candidate_bytes(cid), media_type="image/png")
    except Exception:
        return JSONResponse({"error": "not found"}, status_code=404)


@app.post("/api/bible")
async def api_bible(body: dict = Body(...)):
    """Generate a full character bible (model sheet) from a source sprite. Background job;
    poll /api/bible_status. body: {source, char_desc?, name?, attr?}."""
    source = body.get("candidate_id") or body["source"]   # a picked base candidate, or an rpgdev sprite
    return bible.start(source, char_desc=body.get("char_desc"),
                       name=body.get("name"), attr=body.get("attr", ""))


@app.get("/api/bible_status/{job_id}")
async def api_bible_status(job_id: str):
    return bible.status(job_id)


@app.get("/api/bible_image/{name}")
async def api_bible_image(name: str):
    p = config.GENERATED / f"bible_{name}.png"
    if not p.exists():
        return JSONResponse({"error": "not found"}, status_code=404)
    return Response(content=p.read_bytes(), media_type="image/png")


@app.get("/api/bible_html/{name}")
async def api_bible_html(name: str):
    """Serve the self-contained HTML character bible (builds it if missing)."""
    p = config.GENERATED / f"bible_{name}.html"
    try:
        if not p.exists():
            bible.build_html(name)
        return Response(content=p.read_bytes(), media_type="text/html; charset=utf-8")
    except Exception as ex:
        return JSONResponse({"error": str(ex)}, status_code=404)


@app.get("/api/loras")
async def api_loras():
    """List installed LoRAs (ComfyUI/models/loras on the box) for the WebUI picker."""
    rc, o, e = await training._sh(
        "ssh", "-o", "ConnectTimeout=15", config.BOX_SSH,
        f"cmd /c dir /b {config.COMFY_LORAS}\\*.safetensors", timeout=20)
    names = [l.strip() for l in o.splitlines() if l.strip().lower().endswith(".safetensors")]
    return {"loras": names}


@app.get("/api/lora_candidates")
async def api_lora_candidates():
    """The default training set (house-style sprites + their captions) so the WebUI can
    SHOW what will be trained and let the user pick/edit. Thumbnails via /api/base/{stem}."""
    return {"candidates": [{"stem": s, "desc": d} for s, d in training.HOUSE_STYLE_SPRITES
                           if (config.RPGDEV_SPRITES / f"{s}.png").exists()]}


@app.post("/api/train_lora")
async def api_train_lora(body: dict = Body(...)):
    """Start a style-LoRA training job on the box (backend orchestrates dataset->train->
    install; no manual box commands). body.sprites = [{stem, desc}] selected in the UI
    (defaults to the house-style set). Returns a job_id to poll via /api/train_status."""
    sprites = body.get("sprites")
    if sprites:
        sprites = [(s["stem"], s.get("desc") or s.get("caption") or s["stem"]) for s in sprites]
    return training.start(name=body.get("name") or "sprite-style",
                          sprites=sprites,
                          trigger=body.get("trigger", "sfrpg"),
                          repeats=int(body.get("repeats", 10)),
                          steps=int(body.get("steps", 1500)))


@app.post("/api/train_character")
async def api_train_character(body: dict = Body(...)):
    """Train a CHARACTER LoRA from a generated bible's panels. body: {bible_name, trigger?,
    name?, steps?}. Stops ComfyUI during training, restarts+installs after. Poll /api/train_status."""
    return training.start_character(body["bible_name"], trigger=body.get("trigger"),
                                    name=body.get("name"), steps=int(body.get("steps", 1500)))


@app.get("/api/train_status/{job_id}")
async def api_train_status(job_id: str):
    return training.status(job_id)


@app.get("/api/sprites")
async def api_sprites():
    """List adoptable rpgdev sprite names (for the 'pair_with'/target pickers)."""
    names = sorted(p.name for p in config.RPGDEV_SPRITES.glob("*.png"))
    return {"sprites": names}


@app.get("/api/base/{name}")
async def api_base(name: str):
    """Serve a base rpgdev sprite (for the mask painter canvas)."""
    try:
        return Response(content=services.resolve_base(name).read_bytes(), media_type="image/png")
    except Exception:
        return JSONResponse({"error": "not found"}, status_code=404)


# ---------------- mounts (MCP before the "/" catch-all so static never shadows it) ----------------
app.mount("/mcp", mcp_app)

if WEB.exists():
    app.mount("/", StaticFiles(directory=str(WEB), html=True), name="web")

"""sprite-forge backend configuration.

The backend runs on the Mac (where rpgdev + Claude/Codex live). It drives ComfyUI
on the GPU box over the LAN for generation/editing, does deterministic Pillow
audit locally, and "adopts" results straight into the local rpgdev sprite folder.
"""
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def _load_dotenv(path: Path) -> None:
    """Load KEY=VALUE lines from a local .env (gitignored) into the environment so personal /
    box-specific values stay OUT of the public repo. A real shell env always wins (setdefault)."""
    if not path.exists():
        return
    for raw in path.read_text().splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, val = line.split("=", 1)
        os.environ.setdefault(key.strip(), val.strip().strip('"').strip("'"))


_load_dotenv(ROOT / ".env")

# --- ComfyUI (GPU box). Set your real host in .env (SPRITEFORGE_COMFY_URL). ---
COMFY_URL = os.environ.get("SPRITEFORGE_COMFY_URL", "http://gpu-box:8188")

# --- GPU box SSH + paths (backend drives training over SSH; see training.py).
# Put your real user@host and Windows account name in .env (SPRITEFORGE_BOX_SSH / _BOX_USER). ---
BOX_SSH = os.environ.get("SPRITEFORGE_BOX_SSH", "user@gpu-box")
BOX_USER = os.environ.get("SPRITEFORGE_BOX_USER", "youruser")   # Windows account on the GPU box
SDXL_CKPT_PATH = os.environ.get(
    "SPRITEFORGE_BOX_SDXL_CKPT",
    f"C:\\Users\\{BOX_USER}\\ComfyUI\\models\\checkpoints\\Illustrious-XL-v2.0.safetensors")
COMFY_LORAS = os.environ.get(
    "SPRITEFORGE_BOX_LORA_DIR", f"C:\\Users\\{BOX_USER}\\ComfyUI\\models\\loras")
# box training dirs (kohya sd-scripts + trainer venv + dataset staging), under the box account home
BOX_SFLORA = os.environ.get("SPRITEFORGE_BOX_SFLORA", f"C:\\Users\\{BOX_USER}\\sf-lora")        # backslash (PowerShell/cmd)
BOX_SFLORA_FS = os.environ.get("SPRITEFORGE_BOX_SFLORA_FS", f"C:/Users/{BOX_USER}/sf-lora")      # forward-slash (TOML)
BOX_SDSCRIPTS = os.environ.get("SPRITEFORGE_BOX_SDSCRIPTS", f"C:\\Users\\{BOX_USER}\\sd-scripts")
BOX_TRAIN_VENV = os.environ.get("SPRITEFORGE_BOX_TRAIN_VENV", f"C:\\Users\\{BOX_USER}\\sf-lora-venv")
TRAIN_TIMEOUT = int(os.environ.get("SPRITEFORGE_TRAIN_TIMEOUT", "3600"))  # box training max seconds

# --- this backend (served on the Mac) ---
HOST = os.environ.get("SPRITEFORGE_HOST", "127.0.0.1")
PORT = int(os.environ.get("SPRITEFORGE_PORT", "8765"))

# --- adopt target: rpgdev sprites on this Mac ---
RPGDEV_SPRITES = Path(os.environ.get(
    "SPRITEFORGE_RPGDEV_SPRITES",
    "/path/to/your-game/public/assets/sprites",   # set your real adopt target in .env
))
# rpgdev/ root = sprites -> assets -> public -> rpgdev
RPGDEV_ROOT = RPGDEV_SPRITES.parents[2]
ADOPTION_LOG = RPGDEV_ROOT / ".rpgdev" / "adoption.ndjson"

# --- AI prompt-crafting (backend shells the user's own claude -p / codex CLI; NO Anthropic
# API key, no extra cost). Run with cwd = a project root so the CLI loads THAT project's
# CLAUDE.md / .mcp.json / memory (e.g. rpgdev's world & character context). ---
PROMPT_CLI = os.environ.get("SPRITEFORGE_PROMPT_CLI", "claude")          # "claude" | "codex"
PROMPT_MODEL = os.environ.get("SPRITEFORGE_PROMPT_MODEL", "opus")
PROMPT_PROJECT_DIR = os.environ.get("SPRITEFORGE_PROMPT_PROJECT_DIR", str(ROOT))
PROMPT_TIMEOUT = int(os.environ.get("SPRITEFORGE_PROMPT_TIMEOUT", "150"))

# --- local working dirs (repo .cache, gitignored) ---
CACHE = ROOT / ".cache"
GENERATED = CACHE / "generated"
GENERATED.mkdir(parents=True, exist_ok=True)

# --- mandatory style phrase (scar tissue: never omit; the retro marker) ---
STYLE_PHRASE = ("retro RPG / pixel art / old JRPG sprite / "
                "limited palette / chunky pixel clusters")
# Generation-path style: the literal "pixel art / chunky pixel clusters" makes SDXL
# txt2img render a pixel-NOISE field that defeats matting AND chroma-key (verified
# 2026-06-17 — rembg removed the whole frame). For NEW sprite generation we use a
# MATTABLE retro style (anti-drift intent preserved: retro/JRPG/limited-palette/bold
# outline) and apply the actual dot granularity with the deterministic pixelize()
# post-step (docs/03), NOT the prompt.
GEN_STYLE_PHRASE = ("retro JRPG game sprite, old-school RPG character, limited palette, "
                    "bold dark outline, flat cel shading")

# --- style LoRA (in ComfyUI/models/loras on the box). v2 = a house-style training set,
# trigger "sfrpg"; v1 was an earlier, overfit set. ---
STYLE_LORA = os.environ.get("SPRITEFORGE_STYLE_LORA", "sprite-style-v2.safetensors")
STYLE_LORA_TRIGGER = "sfrpg"          # v2 trigger token
STYLE_LORA_STRENGTH = 0.65            # 0.65 = content follows + style applies; raise toward 0.8 for
                                      # stronger style. Use concrete prompts (vague ones drift ornate).

# --- audit thresholds (scar tissue) ---
BLACK_ALPHA_MIN = 24   # a pixel counts as "black leak" if alpha > this ...
BLACK_RGB_MAX = 28     # ... and r,g,b all < this
BBOX_TOL_PX = 1.0      # damaged variant must match base bbox within this (kept STRICT:
                       # VARIANT_DENOISE makes <=1px reliably achievable, so no loosening)

# --- variant edit strength (scar tissue, measured 2026-06-17) ---
# denoise=1.0 (full re-gen) made Qwen-Edit re-pose the character (e.g. flip a rear-view
# ally to front) -> bbox drift 3-8px. Measured sweep at seed 777 on a base sprite:
#   0.5 -> 0.5px (subtle damage) | 0.7 -> 0.5px (clear damage, skin intact) | 0.9 -> 2.0px.
# 0.7 keeps the base pose faithfully AND shows clear outfit damage AND meets bbox<=1px.
VARIANT_DENOISE = 0.7

# --- safe chroma default for residual non-LayerDiffuse paths (NEVER black) ---
SAFE_CHROMA = "#ff00cc"
# Distinctive, model-nameable key colors (NEVER black). Magenta (#ff00cc) is the
# default; audit.pick_chroma() auto-selects the entry FARTHEST from the subject's
# own colors so a magenta/pink character keys against green/cyan/etc instead.
# (name is what we instruct the edit model with; rgb is the canonical target.)
CHROMA_PALETTE = [
    ("magenta", (255, 0, 204)),   # = SAFE_CHROMA, the default
    ("green",   (0, 255, 0)),
    ("cyan",    (0, 255, 255)),
    ("blue",    (0, 0, 255)),
    ("yellow",  (255, 255, 0)),
]
# how much closer-to-subject a non-default color must be before we abandon magenta
CHROMA_DEFAULT_BIAS = 30.0
# residual chroma audit: a candidate's background must key out so corners are clear;
# halo (fringe) within this band of the key color is reported (cosmetic, not a gate).
CHROMA_KEY_THRESHOLD = 110.0
CHROMA_HALO_BAND = 60.0
# the produced background must be within this of the requested color, else the edit
# model ignored the instruction -> surfaced loudly, candidate fails (no silent key).
CHROMA_COMPLIANCE_MAX = 140.0

# --- model filenames present on the box (Phase 1) ---
MODELS = {
    "sdxl_checkpoint": "Illustrious-XL-v2.0.safetensors",
    "qwen_dit": "qwen_image_edit_2511_fp8mixed.safetensors",
    "qwen_te": "qwen_2.5_vl_7b_fp8_scaled.safetensors",
    "qwen_vae": "qwen_image_vae.safetensors",
    "qwen_lightning_lora": "Qwen-Image-Edit-2511-Lightning-4steps-V1.0-bf16.safetensors",
}

# Matting node class name in ComfyUI (verified against /object_info at runtime).
MATTING_NODE = os.environ.get("SPRITEFORGE_MATTING_NODE", "InspyrenetRembg")

# rembg model for backend matting. "birefnet-general" preserves thin extremities
# (hands/fingers) that "isnet-anime" cut off over busy/similar backgrounds (verified
# 2026-06-17: a generated hand survived BiRefNet but was clipped by isnet-anime).
MATTE_MODEL = os.environ.get("SPRITEFORGE_MATTE_MODEL", "birefnet-general")

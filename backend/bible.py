"""Character bible (model sheet) generator — feature A, master-sheet-anchored.

Architecture (validated 2026-06-18):
  1. Generate ONE comprehensive packed MASTER SHEET via a Qwen one-shot from the source
     reference (all views drawn together = the consistency anchor; the back view comes out
     correct because a coordinated turnaround orients it).
  2. Feed that whole master sheet back as the Qwen-edit reference and generate each panel
     as a SINGLE high-res figure ("using the character in this reference sheet, redraw ONLY
     that character as ..."). Individual panels are high-res + clean + on-model (they
     reference the multi-angle master), so we get consistency AND resolution/extractability.
  3. Compose the labeled bible from the high-res panels; keep the master sheet too. The
     panels double as clean character-LoRA training data.

Long-running (~24 Qwen edits): start() runs a background asyncio job; poll status().
"""
from __future__ import annotations
import asyncio
import io

import numpy as np
from PIL import Image, ImageDraw, ImageFont

from . import config, audit, workflows
from .comfy_client import ComfyClient

# Packed one-shot master sheet (the consistency anchor).
MASTER_PROMPT = (
    "Create a comprehensive character reference model sheet of this exact character on one "
    "white sheet: full-body front view, three-quarter view, side view and back view in a row; "
    "a row of facial expressions (neutral, smile, angry, sad, surprised); two action poses; "
    "same character throughout, consistent design, plain white background, model sheet layout")

# Panel spec: key -> (section, label, kind, instruction-suffix). kind drives the instruction.
PANELS = [
    ("turn_front", "TURNAROUND", "FRONT", "full", "standing straight facing forward, front view"),
    ("turn_34", "TURNAROUND", "3/4", "full", "three-quarter front view, standing"),
    ("turn_side", "TURNAROUND", "SIDE", "full", "exact side profile view, standing"),
    ("turn_back", "TURNAROUND", "BACK", "back", "turned with her back fully toward the viewer, rear view, only the back of her body and head visible, face hidden"),
    ("body_front", "BODY REFERENCE", "FRONT (leotard)", "body", "wearing only a plain sport leotard bodysuit, front view, neutral A-pose, exact body proportions"),
    ("body_back", "BODY REFERENCE", "BACK (leotard)", "body", "wearing only a plain sport leotard bodysuit, rear view from behind, face hidden, exact body proportions"),
    ("ex_neutral", "EXPRESSIONS", "NEUTRAL", "face", "neutral calm expression"),
    ("ex_smile", "EXPRESSIONS", "SMILE", "face", "bright happy smile"),
    ("ex_angry", "EXPRESSIONS", "ANGRY", "face", "angry fierce expression"),
    ("ex_sad", "EXPRESSIONS", "SAD", "face", "sad expression"),
    ("ex_surp", "EXPRESSIONS", "SURPRISE", "face", "surprised wide-eyed expression"),
    ("ex_shy", "EXPRESSIONS", "SHY", "face", "shy blushing expression"),
    ("act_cast", "ACTION POSES", "CAST", "full", "dynamic action pose casting a powerful spell, dramatic angle"),
    ("act_run", "ACTION POSES", "RUN", "full", "running fast, dynamic"),
    ("act_jump", "ACTION POSES", "JUMP", "full", "jumping in the air, dynamic pose"),
    ("cos_casual", "ALTERNATE COSTUMES", "CASUAL", "full", "wearing a casual hoodie and shorts, standing"),
    ("cos_armor", "ALTERNATE COSTUMES", "ARMOR", "full", "wearing ornate knight armor, standing"),
    ("cos_dress", "ALTERNATE COSTUMES", "FORMAL", "full", "wearing an elegant formal dress, standing"),
    ("chibi_big", "CHIBI / SD", "CHIBI", "free", "a cute chibi super-deformed version, big head small body, full body"),
    ("chibi_multi", "CHIBI / SD", "POSES", "free", "three small chibi SD versions in different cute poses in a row"),
    ("item_tiara", "WARDROBE / ITEMS", "HEADWEAR", "item", "the head accessory / headwear"),
    ("item_outfit", "WARDROBE / ITEMS", "OUTFIT", "item", "the main outfit garments"),
    ("item_shoes", "WARDROBE / ITEMS", "FOOTWEAR", "item", "the footwear / boots"),
]
# panels reference the master SHEET, so always forbid reproducing the sheet layout:
NEG = "reference sheet, multiple views, grid, collage, multiple characters, extra people, text, watermark, lowres"
NEG_BACK = NEG + ", face, facing viewer, front view, eyes, looking at viewer, frontal"

_jobs: dict[str, dict] = {}


def _crop_nonwhite(rgb: Image.Image, pad: int = 10, thr: int = 243) -> Image.Image:
    a = np.asarray(rgb.convert("RGB")); m = (a < thr).any(2); ys, xs = np.where(m)
    if len(xs) == 0:
        return rgb
    return rgb.crop((max(int(xs.min()) - pad, 0), max(int(ys.min()) - pad, 0),
                     min(int(xs.max()) + pad, a.shape[1]), min(int(ys.max()) + pad, a.shape[0])))


def _palette(rgb: Image.Image, k: int = 7, thr: int = 243):
    a = np.asarray(rgb.convert("RGB")).reshape(-1, 3); a = a[(a < thr).any(1)]
    if len(a) == 0:
        return [(200, 200, 200)] * k
    q = Image.fromarray(a.reshape(-1, 1, 3).astype("uint8")).quantize(colors=k)
    pal = q.getpalette()[:k * 3]
    return [tuple(pal[i * 3:i * 3 + 3]) for i in range(k)]


def _instr(kind: str, suffix: str) -> str:
    """Instruction that references the MASTER SHEET (fed as the edit image) and asks for a
    single isolated high-res panel of that same character."""
    head = "Using the exact character shown in this character reference sheet,"
    if kind == "face":
        return f"{head} draw a close-up headshot of ONLY that character's face with a {suffix}, single face, head and shoulders, plain white background, high detail"
    if kind == "item":
        return f"{head} draw ONLY {suffix} of that character as a single isolated object, no person, no body, centered on plain white background, high detail"
    if kind == "free":
        return f"{head} draw {suffix} of that character, keep her exact colors, plain white background, high detail"
    # full / body / back
    return (f"{head} redraw ONLY that character as a SINGLE full-body figure {suffix}, "
            f"one character only, isolated, centered, keep her exact face hair outfit and colors, "
            f"plain white background, high detail")


async def _edit(c: ComfyClient, ref_name: str, instr: str, neg: str, seed: int) -> Image.Image:
    wf = workflows.qwen_edit_variant(ref_name, instr, negative=neg, seed=seed, denoise=1.0)
    hist = await c.run(wf)
    im = c.outputs_images(hist)[0]
    raw = await c.get_image(im["filename"], im.get("subfolder", ""), im.get("type", "output"))
    return audit.load(raw).convert("RGB")


def _font(sz):
    for p in ("/System/Library/Fonts/Helvetica.ttc", "/System/Library/Fonts/Supplemental/Arial.ttf"):
        try:
            return ImageFont.truetype(p, sz)
        except Exception:
            pass
    return ImageFont.load_default()


def _on_white(im: Image.Image) -> Image.Image:
    im = audit.ensure_rgba(im); b = Image.new("RGBA", im.size, (255, 255, 255, 255))
    b.alpha_composite(im); return b.convert("RGB")


def _compose(name: str, attr: str, imgs: dict, master: Image.Image | None) -> Image.Image:
    W = 2040; BG = (250, 250, 248); INK = (38, 40, 46); MUT = (96, 100, 110); LINE = (210, 210, 212)
    sheet = Image.new("RGB", (W, 4200), BG); d = ImageDraw.Draw(sheet)
    fT, fSub, fSec, fLab = _font(46), _font(20), _font(26), _font(17)

    def row(keys, y, h, labels, baseline=True, area=(40, W - 40), gap=16):
        x0, x1 = area; n = len(keys); cw = (x1 - x0) // max(n, 1)
        for i, k in enumerate(keys):
            if k not in imgs:
                continue
            im = imgs[k].copy(); sc = min((cw - gap) / im.width, h / im.height)
            im = im.resize((max(1, int(im.width * sc)), max(1, int(im.height * sc))), Image.LANCZOS)
            cx = x0 + cw * i + cw // 2; py = (y + h - im.height) if baseline else (y + (h - im.height) // 2)
            sheet.paste(im, (cx - im.width // 2, py))
            lab = labels[i]; tw = d.textlength(lab, font=fLab); d.text((cx - tw / 2, y + h + 6), lab, font=fLab, fill=MUT)
        return y + h + 30

    def sec(title, y):
        d.line([(40, y + 14), (W - 40, y + 14)], fill=LINE, width=2)
        d.rectangle([40, y + 2, 46, y + 26], fill=(70, 130, 200)); d.text((58, y), title, font=fSec, fill=INK)
        return y + 44

    d.rectangle([0, 0, W, 96], fill=(28, 31, 38)); d.text((40, 22), "CHARACTER BIBLE", font=fT, fill=(242, 242, 245))
    d.text((44, 72), f"{name}  ·  {attr}  ·  sprite-forge model sheet", font=fSub, fill=(165, 176, 192))
    y = 120
    if master is not None:
        y = sec("MASTER REFERENCE (one-shot anchor)", y)
        mw = W - 80; m = master.copy(); sc = min(mw / m.width, 520 / m.height)
        m = m.resize((int(m.width * sc), int(m.height * sc)), Image.LANCZOS)
        sheet.paste(m, (40 + (mw - m.width) // 2, y)); y += m.height + 24
    y = sec("TURNAROUND", y); y = row(["turn_front", "turn_34", "turn_side", "turn_back"], y, 440, ["FRONT", "3/4", "SIDE", "BACK"])
    y = sec("BODY REFERENCE  (proportions / silhouette)", y); y = row(["body_front", "body_back"], y, 440, ["FRONT (leotard)", "BACK (leotard)"], area=(W // 4, W * 3 // 4))
    y = sec("EXPRESSIONS", y); y = row(["ex_neutral", "ex_smile", "ex_angry", "ex_sad", "ex_surp", "ex_shy"], y, 210, ["NEUTRAL", "SMILE", "ANGRY", "SAD", "SURPRISE", "SHY"], baseline=False)
    y = sec("ACTION POSES", y); y = row(["act_cast", "act_run", "act_jump"], y, 400, ["CAST", "RUN", "JUMP"])
    y = sec("ALTERNATE COSTUMES", y); y = row(["cos_casual", "cos_armor", "cos_dress"], y, 440, ["CASUAL", "ARMOR", "FORMAL"])
    y = sec("CHIBI / SD", y); y = row(["chibi_big", "chibi_multi"], y, 300, ["CHIBI", "POSES"], baseline=False)
    y = sec("WARDROBE / ITEMS", y); y = row(["item_tiara", "item_outfit", "item_shoes"], y, 280, ["HEADWEAR", "OUTFIT", "FOOTWEAR"], baseline=False)
    y = sec("COLOR PALETTE", y)
    base = imgs.get("turn_front") or (next(iter(imgs.values())) if imgs else master)
    for i, col in enumerate(_palette(base, 7)):
        x = 40 + i * 150; d.rectangle([x, y + 6, x + 130, y + 70], fill=col, outline=(120, 120, 120))
        d.text((x, y + 74), "#%02X%02X%02X" % col, font=_font(14), fill=MUT)
    y += 110
    return sheet.crop((0, 0, W, min(y, 4200)))


async def _job(job_id: str, source: str, char_desc: str, name: str, attr: str):
    job = _jobs[job_id]
    try:
        job["status"] = "loading reference"
        c = ComfyClient(); await c.start()
        try:
            # source can be a generated candidate id (picked base figure) OR an rpgdev sprite name
            from . import services
            cand = None
            try:
                cand = services.load_candidate(source)
            except Exception:
                cand = None
            if cand is not None:
                ref = audit.composite_on(cand, (255, 255, 255))
            else:
                src = config.RPGDEV_SPRITES / (source if source.lower().endswith(".png") else f"{source}.png")
                ref = audit.composite_on(audit.load(src), (255, 255, 255))
            buf = io.BytesIO(); ref.save(buf, "PNG")
            rn = await c.upload_image(buf.getvalue(), f"sf_bible_src_{name}.png")

            # 1) master sheet (consistency anchor)
            job["status"] = "generating master sheet"
            master = await _edit(c, rn, MASTER_PROMPT, "extra different characters, text", 5)
            mbuf = io.BytesIO(); _on_white(master).save(mbuf, "PNG")
            mn = await c.upload_image(mbuf.getvalue(), f"sf_bible_master_{name}.png")
            (config.GENERATED / f"bible_{name}_master.png").write_bytes(mbuf.getvalue())

            # 2) per-panel high-res, referencing the master sheet
            imgs = {}
            job.update(status="generating panels", done=0, total=len(PANELS))
            for i, (key, _sec, _lab, kind, suffix) in enumerate(PANELS):
                neg = NEG_BACK if (kind == "back" or "back" in suffix.lower() or "rear" in suffix.lower()) else NEG
                imgs[key] = _crop_nonwhite(await _edit(c, mn, _instr(kind, suffix), neg, 600 + i))
                job["done"] = i + 1; job["last"] = key

            # 3) compose + dump panels
            job["status"] = "composing sheet"
            out = config.GENERATED / f"bible_{name}.png"
            _compose(name, attr, imgs, master).save(out)
            pdir = config.GENERATED / f"bible_{name}_panels"; pdir.mkdir(exist_ok=True)
            for k, im in imgs.items():
                im.save(pdir / f"{k}.png")
            html = build_html(name, attr)   # B: self-contained shareable HTML
            job.update(status="done", sheet=str(out),
                       master=str(config.GENERATED / f"bible_{name}_master.png"),
                       panels_dir=str(pdir), panel_count=len(imgs), html=html)
        finally:
            await c.aclose()
    except Exception as ex:
        job.update(status="error", error=str(ex))


def start(source: str, char_desc: str | None = None, name: str | None = None, attr: str = "") -> dict:
    """Start a master-sheet-anchored character-bible job. source = rpgdev sprite name.
    Returns a job_id; poll status()."""
    name = name or source
    char_desc = char_desc or f"the character ({name})"
    jid = f"bible-{abs(hash((name, char_desc))) % 10**8:08d}"
    _jobs[jid] = {"status": "starting", "name": name, "sheet": None}
    asyncio.create_task(_job(jid, source, char_desc, name, attr))
    return {"job_id": jid, "status": "starting", "name": name, "panels": len(PANELS)}


def status(job_id: str) -> dict:
    return {"job_id": job_id, **_jobs.get(job_id, {"status": "unknown"})}


# ============ B: self-contained HTML bible (shareable; base for claude.ai/design) ============
import base64


def _b64(im: Image.Image, maxpx: int = 560) -> str:
    im = im.copy(); im.thumbnail((maxpx, maxpx), Image.LANCZOS)
    buf = io.BytesIO(); _on_white(im).save(buf, "JPEG", quality=86)
    return "data:image/jpeg;base64," + base64.b64encode(buf.getvalue()).decode()


def build_html(name: str, attr: str = "") -> str:
    """Build a self-contained (base64) HTML character bible from existing
    generated/bible_<name>_panels/ (+ master). Returns the saved .html path."""
    pdir = config.GENERATED / f"bible_{name}_panels"
    if not pdir.exists():
        raise RuntimeError(f"no panels for '{name}' — run generate_character_bible first")
    imgs = {p.stem: audit.load(p).convert("RGB") for p in pdir.glob("*.png")}
    master_p = config.GENERATED / f"bible_{name}_master.png"
    master = audit.load(master_p).convert("RGB") if master_p.exists() else None
    by_sec: dict[str, list] = {}
    for key, _sec, lab, _kind, _suf in PANELS:
        if key in imgs:
            by_sec.setdefault(_sec, []).append((lab, imgs[key]))
    pal = _palette(imgs.get("turn_front") or (next(iter(imgs.values())) if imgs else master), 7)

    css = ("body{margin:0;background:#15171c;color:#e7e9ee;font:15px/1.5 -apple-system,system-ui,sans-serif}"
           "header{background:#1f242b;padding:20px 28px;border-bottom:1px solid #333}"
           "h1{margin:0;font-size:26px}h2{font-size:15px;letter-spacing:.08em;color:#9aa3b2;margin:26px 28px 8px;"
           "border-left:4px solid #4682dc;padding-left:10px}.wrap{padding:0 20px 40px}"
           ".row{display:flex;flex-wrap:wrap;gap:14px;padding:0 8px}.cell{background:#1d2026;border:1px solid #333;"
           "border-radius:10px;padding:8px;text-align:center}.cell img{max-height:300px;max-width:240px;display:block;border-radius:6px;background:#fff}"
           ".cell span{font-size:12px;color:#9aa3b2;display:block;margin-top:6px}.master img{max-width:96%;border-radius:10px;background:#fff}"
           ".pal{display:flex;gap:10px;padding:0 16px;flex-wrap:wrap}.sw{width:88px}.sw div{height:48px;border-radius:6px;border:1px solid #555}"
           ".sw code{font-size:11px;color:#9aa3b2}")
    parts = [f"<!-- @dsCard group=\"Characters\" -->\n",
             f"<!doctype html><meta charset=utf-8><title>{name} — character bible</title><style>{css}</style>",
             f"<header><h1>{name}</h1><div style='color:#9aa3b2'>{attr or 'character bible'} · sprite-forge</div></header><div class=wrap>"]
    if master is not None:
        parts.append(f"<h2>MASTER REFERENCE</h2><div class='row master'><div class=cell><img src='{_b64(master, 1400)}'></div></div>")
    order = ["TURNAROUND", "BODY REFERENCE", "EXPRESSIONS", "ACTION POSES", "ALTERNATE COSTUMES", "CHIBI / SD", "WARDROBE / ITEMS"]
    for s in order:
        if s not in by_sec:
            continue
        parts.append(f"<h2>{s}</h2><div class=row>")
        for lab, im in by_sec[s]:
            parts.append(f"<div class=cell><img src='{_b64(im)}'><span>{lab}</span></div>")
        parts.append("</div>")
    parts.append("<h2>COLOR PALETTE</h2><div class=pal>")
    for col in pal:
        hexc = "#%02X%02X%02X" % col
        parts.append(f"<div class=sw><div style='background:{hexc}'></div><code>{hexc}</code></div>")
    parts.append("</div></div>")
    html = "".join(parts)
    out = config.GENERATED / f"bible_{name}.html"
    out.write_text(html, encoding="utf-8")
    return str(out)

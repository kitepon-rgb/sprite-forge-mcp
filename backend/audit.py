"""Deterministic post-processing + scar-tissue audit gates (Pillow/numpy).

- matte(): RGB-on-bg edit output -> clean RGBA (rembg, anime-tuned). Kills pain #1
  for the edit path (Qwen-Edit returns RGB; we re-derive alpha here).
- black_leak_count(): the black-leak audit (a sprite once had 200k+ leaked px). Must be ~0 to adopt.
- bbox / fit_to_base(): canvas + bbox match vs a base sprite (damaged-variant pairing).
No creative hand-painting — only mechanical, reproducible operations.
"""
from __future__ import annotations
import io
from pathlib import Path

import numpy as np
from PIL import Image

from . import config

_REMBG_SESSION = None


def load(data: bytes | str | Path) -> Image.Image:
    if isinstance(data, (str, Path)):
        return Image.open(data).convert("RGBA")
    return Image.open(io.BytesIO(data)).convert("RGBA")


def to_png_bytes(img: Image.Image) -> bytes:
    buf = io.BytesIO()
    img.convert("RGBA").save(buf, format="PNG")
    return buf.getvalue()


def ensure_rgba(img: Image.Image) -> Image.Image:
    return img if img.mode == "RGBA" else img.convert("RGBA")


def _rembg_session():
    global _REMBG_SESSION
    if _REMBG_SESSION is None:
        try:
            from rembg import new_session
        except Exception as e:  # no silent fallback
            raise RuntimeError(
                "matting requires the 'rembg' package (pip install rembg onnxruntime). "
                f"import failed: {e}")
        _REMBG_SESSION = new_session(config.MATTE_MODEL)
    return _REMBG_SESSION


def matte(img: Image.Image) -> Image.Image:
    """Derive a clean alpha for an edit output (subject on opaque bg) -> RGBA."""
    from rembg import remove
    out = remove(ensure_rgba(img), session=_rembg_session(),
                 post_process_mask=True)
    return ensure_rgba(out)


def alpha_array(img: Image.Image) -> np.ndarray:
    return np.asarray(ensure_rgba(img))


def black_leak_count(img: Image.Image) -> int:
    """Pixels that are visible (alpha>min) yet near-black (r,g,b<max) = leak."""
    a = alpha_array(img)
    r, g, b, al = a[..., 0], a[..., 1], a[..., 2], a[..., 3]
    dark = (r < config.BLACK_RGB_MAX) & (g < config.BLACK_RGB_MAX) & (b < config.BLACK_RGB_MAX)
    visible = al > config.BLACK_ALPHA_MIN
    return int(np.count_nonzero(dark & visible))


def corners_opaque(img: Image.Image) -> bool:
    a = alpha_array(img)
    h, w = a.shape[:2]
    return any(a[y, x, 3] > config.BLACK_ALPHA_MIN
               for (y, x) in [(0, 0), (0, w - 1), (h - 1, 0), (h - 1, w - 1)])


def bbox(img: Image.Image) -> tuple[int, int, int, int] | None:
    """Bounding box of the visible (alpha>min) region. (l,t,r,b) or None."""
    a = alpha_array(img)
    mask = a[..., 3] > config.BLACK_ALPHA_MIN
    ys, xs = np.where(mask)
    if len(xs) == 0:
        return None
    return int(xs.min()), int(ys.min()), int(xs.max()) + 1, int(ys.max()) + 1


def _center(bb):
    return ((bb[0] + bb[2]) / 2.0, (bb[1] + bb[3]) / 2.0)


# ---------- distinctive-chroma transparency (the instructed path) ----------
# RGB models (Qwen-Edit) draw the subject on an opaque background. Per design we
# put the subject on a DISTINCTIVE NON-BLACK color (auto-picked farthest from the
# subject's own palette, default magenta), instruct the model to keep that flat
# color, then key THAT color out. Near-black pixels are then unambiguously the
# character's own thick dark outline (which the art style requires), never "leak".

def color_dist(a, b) -> float:
    return float(np.sqrt(((np.asarray(a, float) - np.asarray(b, float)) ** 2).sum()))


def is_near_black(rgb) -> bool:
    return all(int(c) < config.BLACK_RGB_MAX for c in rgb[:3])


def pick_chroma(base_img: Image.Image) -> tuple[str, tuple[int, int, int]]:
    """Pick the palette color farthest from the subject's own colors (default
    magenta unless a magenta/pink subject makes another color clearly safer)."""
    pal = config.CHROMA_PALETTE
    a = alpha_array(base_img)
    vis = a[..., 3] > config.BLACK_ALPHA_MIN
    rgb = a[..., :3][vis].astype(int)
    if rgb.size == 0:
        return pal[0][0], tuple(pal[0][1])
    q = (rgb // 32) * 32 + 16                      # coarse dominant-color bins
    qv, counts = np.unique(q, axis=0, return_counts=True)
    keep = counts >= max(1, int(0.005 * len(rgb)))
    dom = qv[keep] if keep.any() else qv
    scored = [(float(np.sqrt(((dom - np.asarray(c)) ** 2).sum(axis=1)).min()), name, tuple(c))
              for name, c in pal]                  # dmin = closest dominant subject color
    best = max(scored, key=lambda t: t[0])
    magenta = scored[0]                            # pal[0] is the default
    if magenta[0] >= best[0] - config.CHROMA_DEFAULT_BIAS:
        return magenta[1], magenta[2]
    return best[1], best[2]


def composite_on(base_img: Image.Image, rgb) -> Image.Image:
    """Lay the subject over a solid color -> opaque RGB (the edit model's input)."""
    bg = Image.new("RGBA", base_img.size, tuple(int(c) for c in rgb) + (255,))
    bg.alpha_composite(ensure_rgba(base_img))
    return bg.convert("RGB")


def sample_bg_color(img: Image.Image) -> tuple[int, int, int]:
    """Median color of the four corner patches (reliably background)."""
    a = np.asarray(img.convert("RGB")).astype(int)
    h, w = a.shape[:2]
    m = 10
    patches = np.concatenate([a[:m, :m].reshape(-1, 3), a[:m, -m:].reshape(-1, 3),
                              a[-m:, :m].reshape(-1, 3), a[-m:, -m:].reshape(-1, 3)])
    return tuple(int(x) for x in np.median(patches, axis=0))


def chroma_key(img: Image.Image, key_rgb, thr: float = None) -> Image.Image:
    """Knock out pixels near key_rgb -> alpha 0 (clearing their RGB too)."""
    thr = config.CHROMA_KEY_THRESHOLD if thr is None else thr
    a = np.asarray(ensure_rgba(img)).astype(int)
    dist = np.sqrt(((a[..., :3] - np.asarray(key_rgb)) ** 2).sum(axis=2))
    bg = dist < thr
    a[..., 3] = np.where(bg, 0, 255)
    a[bg, :3] = 0
    return Image.fromarray(a.astype("uint8"), "RGBA")


def chroma_halo_count(img: Image.Image, key_rgb) -> int:
    """Visible fringe pixels still near the key color (cosmetic spill, reported)."""
    lo = config.CHROMA_KEY_THRESHOLD
    hi = lo + config.CHROMA_HALO_BAND
    a = np.asarray(ensure_rgba(img))
    dist = np.sqrt(((a[..., :3].astype(int) - np.asarray(key_rgb)) ** 2).sum(axis=2))
    return int(np.count_nonzero((dist >= lo) & (dist < hi) & (a[..., 3] > config.BLACK_ALPHA_MIN)))


def layerdiffuse_rgba(rgb_bytes: bytes, mask_bytes: bytes, invert: bool = False) -> Image.Image:
    """Re-join LayerDiffuse's separate RGB + alpha outputs into one RGBA image
    (replacing the broken ComfyUI-side RGBA node). The decode's `1.0 - mask` was
    written assuming JoinImageWithAlpha re-inverts; composing directly, alpha = mask
    (invert=False) is correct — verified on the box (center opaque, corners clear)."""
    rgb = Image.open(io.BytesIO(rgb_bytes)).convert("RGB")
    mk = Image.open(io.BytesIO(mask_bytes)).convert("L")
    a = np.asarray(mk).astype("uint8")
    alpha = (255 - a) if invert else a
    out = np.dstack([np.asarray(rgb), alpha])
    return Image.fromarray(out.astype("uint8"), "RGBA")


def composite_inside_mask(base: Image.Image, edited: Image.Image,
                          mask: Image.Image) -> Image.Image:
    """Keep base pixels everywhere EXCEPT inside the (binary) mask, where the edited
    pixels are used. This is a compositing identity: outside-mask pixels are byte-
    identical to the base, so the visible-region bbox cannot move there — the only
    way to GUARANTEE bbox<=1px for a damaged variant (mask = the outfit region,
    interior to the silhouette; flames/hair/boots stay outside it = base-exact)."""
    base = ensure_rgba(base)
    edited = ensure_rgba(edited).resize(base.size, Image.NEAREST) if edited.size != base.size else ensure_rgba(edited)
    m = ensure_rgba(mask).resize(base.size, Image.NEAREST) if mask.size != base.size else ensure_rgba(mask)
    sel = np.asarray(m)[..., 3] > 127            # painted (opaque) = the edit region
    out = np.asarray(base).copy()
    out[sel] = np.asarray(edited)[sel]
    return Image.fromarray(out, "RGBA")


def outside_mask_identical(a: Image.Image, base: Image.Image, mask: Image.Image) -> bool:
    """Verify the guarantee: every pixel outside the mask equals the base exactly."""
    base = ensure_rgba(base)
    m = ensure_rgba(mask).resize(base.size, Image.NEAREST) if mask.size != base.size else ensure_rgba(mask)
    sel = np.asarray(m)[..., 3] > 127
    aa, bb = np.asarray(ensure_rgba(a)), np.asarray(base)
    return bool(np.array_equal(aa[~sel], bb[~sel]))


def fit_to_base(cand: Image.Image, base: Image.Image | None, chroma=None) -> dict:
    """Audit a candidate for adoption.

    Transparency gate = four corners cleared (background removed) + a valid bbox.
    `black_px` is reported but NOT a gate: near-black visible pixels are the
    character's own dark outline, not background leak (the old black-leak==0 rule
    rejected even adopted base sprites). For the chroma path, the produced bg is
    keyed by sampled color so corners-clear already proves the removal; chroma
    halo (fringe) is reported as cosmetic. If base given, also check pair alignment.
    """
    cbb = bbox(cand)
    res = {
        "canvas": list(cand.size),
        "corners_transparent": not corners_opaque(cand),
        "bbox": list(cbb) if cbb else None,
        "black_px": black_leak_count(cand),   # informational: usually char outline
    }
    ok = res["corners_transparent"] and (cbb is not None)
    if chroma is not None:
        res["chroma_key_rgb"] = list(int(c) for c in chroma)
        res["chroma_halo_px"] = chroma_halo_count(cand, chroma)
    if base is not None:
        bbb = bbox(base)
        canvas_match = (cand.size == base.size)
        delta = None
        if cbb and bbb:
            cc, bc = _center(cbb), _center(bbb)
            delta = round(max(abs(cc[0] - bc[0]), abs(cc[1] - bc[1])), 2)
        res.update({"canvas_match": canvas_match,
                    "base_canvas": list(base.size),
                    "bbox_center_delta_px": delta})
        ok = ok and canvas_match and (delta is not None and delta <= config.BBOX_TOL_PX)
    res["pass"] = bool(ok)
    return res


def compare_grid(images: list[Image.Image], cols: int = 4, cell: int = 320,
                 checker: bool = True) -> Image.Image:
    """Side-by-side grid on a checkerboard so alpha/black-leak is visible."""
    n = len(images)
    cols = min(cols, max(1, n))
    rows = (n + cols - 1) // cols
    grid = Image.new("RGBA", (cols * cell, rows * cell), (255, 255, 255, 255))
    if checker:
        c = 16
        for y in range(0, grid.height, c):
            for x in range(0, grid.width, c):
                if (x // c + y // c) % 2:
                    for yy in range(y, min(y + c, grid.height)):
                        for xx in range(x, min(x + c, grid.width)):
                            grid.putpixel((xx, yy), (204, 204, 204, 255))
    for i, im in enumerate(images):
        th = ensure_rgba(im).copy()
        th.thumbnail((cell, cell))
        gx = (i % cols) * cell + (cell - th.width) // 2
        gy = (i // cols) * cell + (cell - th.height) // 2
        grid.alpha_composite(th, (gx, gy))
    return grid

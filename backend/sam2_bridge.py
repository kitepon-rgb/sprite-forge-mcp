"""SAM2 point->mask bridge. Runs in the ISOLATED .venv-sam2 (ultralytics+torch),
NOT the main backend venv and NOT the GPU box — so it cannot disturb either.

Invoked as a subprocess by services.sam2_mask:
    <.venv-sam2/bin/python> sam2_bridge.py <image_path> <points_json> <out_png>
points_json = [[x,y], ...] in IMAGE pixels (foreground clicks). Writes an RGBA PNG
where the segmented region is opaque white (= the edit region for generate_variant).
"""
import sys
import json
import numpy as np
from PIL import Image


def main() -> int:
    image_path, points_json, out_png = sys.argv[1], sys.argv[2], sys.argv[3]
    points = json.loads(points_json)
    if not points:
        print("no points", file=sys.stderr)
        return 2
    labels = [1] * len(points)  # all foreground clicks

    from ultralytics import SAM
    model = SAM("sam2_t.pt")  # tiny SAM2; auto-downloads to the venv on first use
    res = model(image_path, points=points, labels=labels, verbose=False)
    masks = res[0].masks
    if masks is None or masks.data is None or len(masks.data) == 0:
        print("SAM2 returned no mask", file=sys.stderr)
        return 3
    m = masks.data.cpu().numpy()          # (N, H, W) bool/float
    union = (m.sum(axis=0) > 0.5)         # union of returned masks
    h, w = union.shape
    out = np.zeros((h, w, 4), np.uint8)
    out[union] = (255, 255, 255, 255)     # opaque = region to edit
    Image.fromarray(out, "RGBA").save(out_png)
    print(out_png)
    return 0


if __name__ == "__main__":
    sys.exit(main())

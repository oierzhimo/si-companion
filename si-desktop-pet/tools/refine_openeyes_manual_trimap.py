#!/usr/bin/env python3
"""Refine the hand-traced open-eyes mask with GrabCut; alpha only.

Reads 072 and manual-v1 RGBA (read-only), writes six fixed drafts. RGB is copied
byte-for-byte from 072; no generation/recolor/inpaint. GrabCut can still remove
thin ornaments or keep background, so full-size visual review is required.
"""
from __future__ import annotations

import argparse, hashlib, json, sys
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFilter, ImageFont

ROOT = Path(__file__).resolve().parents[1]
DEPS = ROOT / "qa" / "segmentation-deps"
sys.path.insert(0, str(DEPS))
import cv2  # noqa: E402  (local qa/segmentation-deps copy)

STEM = "si-game-openeyes-trimap-draft-v1"
A, Q = ROOT / "art-source" / "v1.3", ROOT / "qa"
REF, MANUAL = Q / "si-body-reference-072.png", A / "si-game-openeyes-manual-v1.png"
OUT_RGBA, OUT_PINK, OUT_DESKTOP = A / f"{STEM}.png", Q / f"{STEM}-pink.jpg", Q / f"{STEM}-desktop.png"
OUT_COMPARE, OUT_SEEDS, OUT_REPORT = Q / f"{STEM}-upper-compare.jpg", Q / f"{STEM}-seeds.png", Q / f"{STEM}-report.json"
TARGETS = (OUT_RGBA, OUT_PINK, OUT_DESKTOP, OUT_COMPARE, OUT_SEEDS, OUT_REPORT)
SIZE, PINK, DESKTOP = (820, 1440), (255, 105, 180), (384, 416)
DESKTOP_BG, FIT_MAX, BASELINE = (232, 232, 232), (368, 404), 410
COMPARE_BG, LABEL_BG, CROP, LABEL_H = (232, 232, 232), (245, 245, 245), (270, 70, 610, 580), 24
NECK_POLYGON = [(359, 350), (384, 350), (412, 364), (464, 348), (480, 365), (495, 405), (478, 427), (367, 424), (349, 405)]
SURE_FG = [(366, 154), (377, 181), (468, 144), (480, 171), (291, 420), (306, 445), (567, 420), (548, 445)]
RING = (525, 995, 605, 1190)  # inclusive x1/y1; tail ring ornament
SEED_RADIUS, GC_ITERS = 2, 5

def digest(path):
    h = hashlib.sha256()
    with path.open("rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""): h.update(block)
    return h.hexdigest()
def rel(path): return path.resolve().relative_to(ROOT.resolve()).as_posix()
def require(ok, message):
    if not ok: raise SystemExit(message)
def ellipse(n): return cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (n, n))
def bbox(mask):
    ys, xs = np.nonzero(mask)
    return [int(xs.min()), int(ys.min()), int(xs.max()) + 1, int(ys.max()) + 1] if xs.size else None
def load(path):
    with Image.open(path) as im:
        require((im.mode, im.size) == ("RGBA", SIZE), f"Expected RGBA 820x1440: {path}")
        return np.array(im, dtype=np.uint8).copy()
def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--overwrite", action="store_true", help="replace only the six fixed outputs")
    args = parser.parse_args(argv)
    for path in (REF, MANUAL):
        require(path.is_file() and not path.is_symlink(), f"Missing or symlinked fixed input: {path}")
    existing = [rel(path) for path in TARGETS if path.exists()]
    require(args.overwrite or not existing, f"Refusing to overwrite (use --overwrite): {existing}")
    before = {path: digest(path) for path in (REF, MANUAL)}
    ref, manual = load(REF), load(MANUAL)
    require(np.array_equal(ref[..., :3], manual[..., :3]), "Manual RGB differs from the 072 source")
    rgb, coarse = ref[..., :3].copy(), (manual[..., 3] > 127).astype(np.uint8)
    require(0 < int(coarse.sum()) < coarse.size, "Manual alpha>127 produced an empty or full mask")
    coarse = cv2.morphologyEx(coarse, cv2.MORPH_CLOSE, ellipse(9))
    cv2.fillPoly(coarse, [np.array(NECK_POLYGON, np.int32)], 1)
    allowed, interior = cv2.dilate(coarse, ellipse(31)), cv2.erode(coarse, ellipse(17))
    gc = np.full(coarse.shape, cv2.GC_BGD, np.uint8)
    gc[allowed == 1], gc[coarse == 1], gc[interior == 1] = cv2.GC_PR_BGD, cv2.GC_PR_FGD, cv2.GC_FGD
    x0, y0, x1, y1 = RING
    ring = gc[y0:y1 + 1, x0:x1 + 1]
    ring_demoted = int((ring == cv2.GC_FGD).sum())
    ring[ring == cv2.GC_FGD] = cv2.GC_PR_FGD
    for x, y in SURE_FG: cv2.circle(gc, (x, y), SEED_RADIUS, cv2.GC_FGD, -1)
    seed_gc = gc.copy()

    bgd_model, fgd_model = np.zeros((1, 65), np.float64), np.zeros((1, 65), np.float64)
    cv2.grabCut(np.ascontiguousarray(rgb[..., ::-1]), gc, None, bgd_model, fgd_model, GC_ITERS, cv2.GC_INIT_WITH_MASK)
    mask = (gc == cv2.GC_FGD) | (gc == cv2.GC_PR_FGD)
    require(mask.any() and not mask.all(), "GrabCut produced an empty or full mask")
    alpha = np.array(Image.fromarray(mask.astype(np.uint8) * 255, "L").filter(ImageFilter.GaussianBlur(0.55)), dtype=np.uint8).copy()
    corners = [int(alpha[y, x]) for y, x in ((0, 0), (0, -1), (-1, 0), (-1, -1))]
    require(not any(corners), f"Four corners are not transparent: {corners}")

    cutout = Image.fromarray(np.dstack([rgb, alpha]).astype(np.uint8), "RGBA")
    a = alpha.astype(np.float32)[..., None] / 255.0
    pink = np.clip(np.rint(rgb * a + np.asarray(PINK, np.float32) * (1 - a)), 0, 255).astype(np.uint8)
    alpha_bbox = cutout.getchannel("A").getbbox()
    require(alpha_bbox is not None, "Refined alpha is empty")
    content = cutout.crop(alpha_bbox)
    ratio = min(FIT_MAX[0] / content.width, FIT_MAX[1] / content.height)
    fitted_size = (min(FIT_MAX[0], max(1, round(content.width * ratio))), min(FIT_MAX[1], max(1, round(content.height * ratio))))
    fitted = content.resize(fitted_size, Image.Resampling.LANCZOS)
    offset = ((DESKTOP[0] - fitted.width) // 2, BASELINE - fitted.height)
    require(offset[1] >= 0, "Desktop crop exceeds the 410 px baseline")
    desktop = Image.new("RGB", DESKTOP, DESKTOP_BG); desktop.paste(fitted, offset, fitted)
    original, region = Image.fromarray(rgb, "RGB").crop(CROP), cutout.crop(CROP)
    on_grey = Image.new("RGB", region.size, COMPARE_BG); on_grey.paste(region, (0, 0), region)
    compare = Image.new("RGB", (original.width * 2, original.height + LABEL_H), LABEL_BG)
    compare.paste(original, (0, LABEL_H)); compare.paste(on_grey, (original.width, LABEL_H))
    draw, font = ImageDraw.Draw(compare), ImageFont.load_default()
    draw.text((8, 6), "Original crop (270,70,610,580)", fill=(30, 30, 30), font=font)
    draw.text((original.width + 8, 6), "GrabCut-refined trimap, grey bg", fill=(30, 30, 30), font=font)

    seed_fill = np.zeros((SIZE[1], SIZE[0], 4), np.uint8)
    seed_fill[seed_gc == cv2.GC_FGD], seed_fill[seed_gc == cv2.GC_PR_BGD] = (255, 64, 64, 78), (64, 112, 255, 70)
    overlay = Image.fromarray(seed_fill, "RGBA"); seed_draw = ImageDraw.Draw(overlay)
    for x, y in SURE_FG: seed_draw.ellipse((x - 5, y - 5, x + 5, y + 5), fill=(255, 20, 20, 220))
    seed_text = "red = FG seeds (sure/GC_FGD), blue = probable BG seeds (GC_PR_BGD)"
    seed_draw.rectangle((8, 8, 18 + int(seed_draw.textlength(seed_text, font=font)), 30), fill=(255, 255, 255, 210))
    seed_draw.text((13, 12), seed_text, fill=(20, 20, 20, 255), font=font)
    seeds = Image.alpha_composite(Image.fromarray(rgb, "RGB").convert("RGBA"), overlay).convert("RGB")

    cutout.save(OUT_RGBA, format="PNG", optimize=True); Image.fromarray(pink, "RGB").save(OUT_PINK, format="JPEG", quality=95, subsampling=0, optimize=True)
    desktop.save(OUT_DESKTOP, format="PNG", optimize=True); compare.save(OUT_COMPARE, format="JPEG", quality=95, subsampling=0, optimize=True); seeds.save(OUT_SEEDS, format="PNG", optimize=True)
    with Image.open(OUT_RGBA) as im:
        require((im.mode, im.size) == ("RGBA", SIZE), "Saved RGBA mode/size mismatch"); saved = np.array(im, dtype=np.uint8).copy()
    require(np.array_equal(saved[..., :3], rgb), "Saved PNG RGB differs from source")
    require(np.array_equal(saved[..., 3], alpha), "Saved PNG alpha differs from computed alpha")
    saved_corners = [int(saved[y, x, 3]) for y, x in ((0, 0), (0, -1), (-1, 0), (-1, -1))]
    require(not any(saved_corners), f"Saved PNG corners are not transparent: {saved_corners}")
    with Image.open(OUT_DESKTOP) as im: desktop_ok = (im.mode, im.size) == ("RGB", DESKTOP) and np.array_equal(np.array(im), np.array(desktop))
    for path, mode, size in ((OUT_PINK, "RGB", SIZE), (OUT_COMPARE, "RGB", compare.size), (OUT_SEEDS, "RGB", SIZE)):
        with Image.open(path) as im: require((im.mode, im.size) == (mode, size), f"Preview mismatch: {path}"); im.load()
    require(desktop_ok, "Desktop preview readback mismatch")

    after = {path: digest(path) for path in (REF, MANUAL)}
    require(after == before, "Source or manual PNG changed during the run")
    outputs = (OUT_RGBA, OUT_PINK, OUT_DESKTOP, OUT_COMPARE, OUT_SEEDS)
    shas, mask_bbox = {path: digest(path) for path in outputs}, bbox(mask)
    method = {"coarse": "manual alpha>127; 9x9 ellipse close; add neck-protection polygon as foreground",
              "neck_protect_polygon": NECK_POLYGON, "sure_fg_points_radius_2": [list(p) for p in SURE_FG],
              "allowed": "dilate(coarse, 31x31 ellipse)", "interior": "erode(coarse, 17x17 ellipse)",
              "grabcut": {"init": "GC_INIT_WITH_MASK", "iterations": GC_ITERS, "kept_classes": ["GC_FGD", "GC_PR_FGD"], "outside_allowed": "GC_BGD",
                          "allowed_minus_coarse": "GC_PR_BGD", "coarse": "GC_PR_FGD", "interior": "GC_FGD", "tail_ring_demoted": {"bbox_inclusive_xyxy": list(RING), "pixels": ring_demoted}},
              "alpha": "binary mask *255, Pillow GaussianBlur(0.55) edge softening only", "color_key_used": False,
              "connected_component_removal_used": False, "rgb_policy": "RGB copied byte-for-byte from 072; no redraw/recolor/inpaint/face swap"}
    report = {
        "schema": "si-game-openeyes-trimap-draft-report/v1", "method": method,
        "source": {"path": rel(REF), "sha256_before": before[REF], "sha256_after": after[REF], "unchanged": before[REF] == after[REF]},
        "raw_manual": {"path": rel(MANUAL), "sha256_before": before[MANUAL], "sha256_after": after[MANUAL], "unchanged": before[MANUAL] == after[MANUAL]},
        "foreground_pixels": int(mask.sum()), "foreground_bbox_xyxy_half_open": mask_bbox,
        "alpha": {"visible_pixels": int((alpha > 0).sum()), "opaque_pixels": int((alpha == 255).sum()), "bbox_xyxy_half_open": alpha_bbox, "four_corners_transparent": not any(corners),
                  "four_corners": dict(zip(("top_left", "top_right", "bottom_left", "bottom_right"), corners)), "desktop_content_size": list(fitted_size), "desktop_xy": list(offset), "desktop_bottom_y": BASELINE},
        "outputs": {rel(path): {"sha256": shas[path], "bytes": path.stat().st_size} for path in outputs},
        "checks": {"source_hash_unchanged": before[REF] == after[REF], "raw_manual_hash_unchanged": before[MANUAL] == after[MANUAL], "output_png_rgb_equals_source": True,
                    "output_png_alpha_equals_computed": True, "four_corners_transparent": True, "desktop_readback_exact": bool(desktop_ok), "pink_compare_seeds_readback_ok": True},
        "seeds_preview": {"path": rel(OUT_SEEDS), "purpose": "seed placement review only; not a character asset", "red": "FG seeds (GC_FGD)", "blue": "probable BG seeds (GC_PR_BGD ring)"},
        "limitations": ["GrabCut 仍可能误删细角、肩饰、发丝或尾部环饰，也可能在 allowed 带内保留背景；本轮没有颜色键、连通域删饰或人工补画兜底。",
                         "必须按 820x1440 全尺寸逐部位目视核对，并检查粉色底、灰底对比与种子图；RGB 与 source 逐字节相同只证明没有改色，不能证明视觉合格。",
                         "尾端环饰仅把 GC_FGD 降为 GC_PR_FGD，环内部是否保留仍由 GrabCut 决定；本图不是运行素材、动画帧或验收结论。"],
        "runtime_asset": False, "visual_quality_verified": False, "is_animation": False,
        "non_destructive": {"fixed_inputs_only": True, "fixed_write_targets_only": True, "write_targets": [rel(path) for path in TARGETS], "overwrite_requested": bool(args.overwrite),
                             "existing_outputs_before_run": existing, "new_rgb_generated": False, "network_used": False, "configs_or_runtime_assets_written": False},
    }
    OUT_REPORT.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"rgba": rel(OUT_RGBA), "foreground_pixels": int(mask.sum()), "bbox": mask_bbox, "four_corners_transparent": not any(corners), "runtime_asset": False, "visual_quality_verified": False, "is_animation": False}, ensure_ascii=False, indent=2))
    return 0
if __name__ == "__main__": raise SystemExit(main())

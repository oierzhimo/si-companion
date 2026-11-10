#!/usr/bin/env python3
"""One-off manual visual fusion of fixed local SAM masks into a static cutout.

This is not automatic score selection. Inputs are fixed, output directories must be
new, and there is no overwrite option. RGB stays byte-for-byte 072; only alpha is
composited. No SAM inference, no model scores, no color key, no recolor, no global
morphology, no new RGB. Draft only: runtime_asset=false, visual_quality_verified=false,
is_animation=false, not final acceptance, not release.

v2 keeps every v1 step and appends three hand-drawn alpha-only polygons; v1 products
are never overwritten.
"""
from __future__ import annotations

import hashlib
import json
import struct
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

sys.dont_write_bytecode = True

import numpy as np
from PIL import Image, ImageDraw, ImageFont
from PIL.PngImagePlugin import PngInfo

ROOT = Path(__file__).resolve().parents[1]
DEPS = ROOT / "qa" / "segmentation-deps"
sys.path.insert(0, str(DEPS))
import cv2  # noqa: E402  (local qa/segmentation-deps copy; no torch/SAM import)

SIZE, SCALE = (820, 1440), 4
REF = ROOT / "qa" / "si-body-reference-072.png"
BASELINE = ROOT / "art-source" / "v1.3" / "si-game-sam-filled-v2" / "candidate-0.png"
MASK_JSON = ROOT / "art-source" / "v1.3" / "si-game-openeyes-manual-v1-mask.json"
RAW_DIR = ROOT / "qa" / "si-game-regions-sam-v1"
ORIGINAL = Path(r"D:\终末地小祀\屏幕截图 2026-10-16 185835.png")
OUT_ART = ROOT / "art-source" / "v1.3" / "si-game-regional-fusion-v2"
OUT_QA = ROOT / "qa" / "si-game-regional-fusion-v2"
CUTOUT = OUT_ART / "cutout.png"
MASK_PNG = OUT_QA / "mask.png"
PINK_PNG = OUT_QA / "pink.png"
DESKTOP_PNG = OUT_QA / "desktop.png"
UPPER_PNG = OUT_QA / "upper-compare.png"
FULL_PNG = OUT_QA / "full-compare.png"
RIGHT_COMPARE_PNG = OUT_QA / "right-compare.png"
RIGHT_POLYGONS_PNG = OUT_QA / "right-polygons.png"
REPORT = OUT_QA / "report.json"
SCHEMA = "si-game-regional-fusion-v2-report/v2"

ORIGINAL_SIZE, ORIGINAL_CROP = (3840, 2160), (1830, 300, 2650, 1740)
REGION_BOXES = {
    "shoulder_left": (273, 369, 354, 529),
    "shoulder_right": (497, 365, 585, 532),
    "coat_left": (53, 535, 339, 1210),
    "coat_right": (523, 548, 725, 1243),
}
EXCLUDED_PARTS = {
    "head_hair_face", "right_hair_bun", "left_horn", "right_horn",
    "left_head_ink_ornament", "right_head_ink_ornament",
    "tail_end_ornament_review_required",
}
HEAD_PARTS = ("head_hair_face", "right_hair_bun")
HEAD_ROIS = ((264, 278, 348, 367), (499, 284, 566, 367))
LEFT_ORNAMENT = [[338, 284], [342, 286], [334, 291], [323, 297], [315, 306],
                 [304, 322], [294, 340], [281, 359], [277, 361], [281, 352],
                 [291, 337], [300, 320], [311, 305], [322, 293], [331, 287]]
RIGHT_ORNAMENT = [[504, 291], [508, 293], [517, 306], [524, 320], [533, 335],
                  [544, 350], [556, 363], [550, 361], [538, 347], [528, 333],
                  [519, 320], [512, 307]]
FACE_RECT = (380, 275, 476, 349)

# Hand-drawn from the fixed 072 crop and right-edge-grid coordinates. These are
# draft visual polygons, not final annotation: they only add alpha after every
# v1 step and never alter RGB, input metadata, face alpha, or existing alpha pixels.
EXTRA_POLYGONS = {
    "right_outer_robe_strip": [
        [601, 566], [617, 588], [638, 616], [657, 642], [676, 670], [694, 700],
        [714, 735], [732, 768], [750, 802], [767, 837], [770, 860], [767, 869],
        [756, 868], [746, 847], [728, 811], [709, 776], [690, 742], [673, 709],
        [656, 675], [641, 646], [624, 618], [607, 590],
    ],
    "right_hair_loop": [
        [677, 734], [688, 742], [698, 759], [706, 781], [704, 804], [698, 826],
        [688, 847], [679, 859], [683, 843], [688, 822], [690, 800], [688, 779],
        [681, 757],
    ],
    "right_gold_cord": [
        [592, 539], [609, 549], [633, 575], [663, 605], [693, 636], [725, 668],
        [754, 695], [756, 703], [749, 701], [720, 674], [690, 644], [660, 614],
        [630, 584], [604, 558], [594, 551],
    ],
}
EXTRA_POLYGON_SOURCE_CN = "人工目视描边，基于固定 072 裁区与 right-edge-grid 坐标；未经最终验收。"

PINK, PREVIEW_BG, LABEL_BG = (255, 105, 180), (232, 232, 232), (245, 245, 245)
DESKTOP, FIT_MAX, BASELINE_Y = (384, 416), (368, 404), 410
UPPER_CROP, LABEL_H = (264, 70, 610, 580), 24
FULL_MAX = (384, 720)
RIGHT_CROP, RIGHT_SCALE = (580, 530, 790, 940), 2
RIGHT_POLYGON_COLORS = {
    "right_outer_robe_strip": (255, 0, 255),
    "right_hair_loop": (255, 80, 0),
    "right_gold_cord": (0, 140, 255),
}
CORNERS = ((0, 0), (819, 0), (0, 1439), (819, 1439))


def digest(path):
    h = hashlib.sha256()
    with path.open("rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def rel(path):
    return path.resolve().relative_to(ROOT.resolve()).as_posix()


def path_label(path):
    try:
        return rel(path)
    except ValueError:
        return path.as_posix()


def need(ok, message):
    if not ok:
        raise SystemExit(message)


def load_image(path, mode):
    with Image.open(path) as im:
        need((im.format, im.mode, im.size) == ("PNG", mode, SIZE),
             f"Expected PNG {mode} 820x1440: {path} ({im.mode} {im.size})")
        return np.array(im, dtype=np.uint8).copy()


def bbox(mask):
    ys, xs = np.nonzero(mask)
    return [int(xs.min()), int(ys.min()), int(xs.max()) + 1, int(ys.max()) + 1] if xs.size else None


def check_polygon(name, points):
    need(isinstance(points, list) and len(points) >= 3, f"Polygon needs >=3 points: {name}")
    need(all(isinstance(p, list) and len(p) == 2
             and all(type(v) is int and 0 <= v < SIZE[i] for i, v in enumerate(p))
             for p in points), f"Invalid polygon point: {name}")


def polygon_alpha(polys):
    big = Image.new("L", (SIZE[0] * SCALE, SIZE[1] * SCALE), 0)
    draw = ImageDraw.Draw(big)
    for points in polys:
        draw.polygon([(x * SCALE, y * SCALE) for x, y in points], fill=255)
    return np.asarray(big.resize(SIZE, Image.Resampling.LANCZOS), dtype=np.uint8).copy()


def on_color(rgb, alpha, color):
    a = alpha.astype(np.float32)[..., None] / 255.0
    return np.clip(np.rint(rgb.astype(np.float32) * a + np.asarray(color, np.float32) * (1 - a)),
                   0, 255).astype(np.uint8)


def on_grey(image):
    out = Image.new("RGB", image.size, PREVIEW_BG)
    out.paste(image, (0, 0), image)
    return out


def desktop_tile(rgb, alpha):
    im = Image.fromarray(np.dstack([rgb, alpha]), "RGBA")
    box = im.getchannel("A").getbbox()
    need(box is not None, "Empty alpha for desktop preview")
    content = im.crop(box)
    ratio = min(FIT_MAX[0] / content.width, FIT_MAX[1] / content.height)
    size = (min(FIT_MAX[0], max(1, round(content.width * ratio))),
            min(FIT_MAX[1], max(1, round(content.height * ratio))))
    fitted = content.resize(size, Image.Resampling.LANCZOS)
    offset = ((DESKTOP[0] - fitted.width) // 2, BASELINE_Y - fitted.height)
    need(offset[0] >= 0 and offset[1] >= 0 and offset[1] + fitted.height == BASELINE_Y,
         "Desktop placement failed")
    out = Image.new("RGB", DESKTOP, PREVIEW_BG)
    out.paste(fitted, offset, fitted)
    return out


def upper_compare(rgb, baseline_image, cutout_image):
    w, h = UPPER_CROP[2] - UPPER_CROP[0], UPPER_CROP[3] - UPPER_CROP[1]
    panels = [Image.fromarray(rgb, "RGB").crop(UPPER_CROP),
              on_grey(baseline_image.crop(UPPER_CROP)),
              on_grey(cutout_image.crop(UPPER_CROP))]
    labels = ["Original 072 crop (264,70,610,580)", "Old baseline: SAM-filled v2 candidate-0",
              "New: regional fusion v2 (+ extra alpha polygons)"]
    out = Image.new("RGB", (w * 3, h + LABEL_H), LABEL_BG)
    draw, font = ImageDraw.Draw(out), ImageFont.load_default()
    for i, panel in enumerate(panels):
        out.paste(panel, (i * w, LABEL_H))
        draw.text((i * w + 8, 6), labels[i], fill=(30, 30, 30), font=font)
    return out


def full_compare(rgb, baseline_image, cutout_image):
    ratio = min(FULL_MAX[0] / SIZE[0], FULL_MAX[1] / SIZE[1])
    w = min(FULL_MAX[0], max(1, round(SIZE[0] * ratio)))
    h = min(FULL_MAX[1], max(1, round(SIZE[1] * ratio)))
    panels = [Image.fromarray(rgb, "RGB").resize((w, h), Image.Resampling.LANCZOS),
              on_grey(baseline_image.resize((w, h), Image.Resampling.LANCZOS)),
              on_grey(cutout_image.resize((w, h), Image.Resampling.LANCZOS))]
    labels = ["Original 072 full", "Old baseline: SAM-filled v2 candidate-0",
              "New: regional fusion v2 (+ extra alpha polygons)"]
    out = Image.new("RGB", (w * 3, h + LABEL_H), LABEL_BG)
    draw, font = ImageDraw.Draw(out), ImageFont.load_default()
    for i, panel in enumerate(panels):
        out.paste(panel, (i * w, LABEL_H))
        draw.text((i * w + 8, 6), labels[i], fill=(30, 30, 30), font=font)
    return out


def right_compare(rgb, baseline_image, cutout_image):
    box_text = "(%d,%d,%d,%d)" % RIGHT_CROP
    w = (RIGHT_CROP[2] - RIGHT_CROP[0]) * RIGHT_SCALE
    h = (RIGHT_CROP[3] - RIGHT_CROP[1]) * RIGHT_SCALE
    panels = [Image.fromarray(rgb, "RGB").crop(RIGHT_CROP),
              on_grey(baseline_image.crop(RIGHT_CROP)),
              on_grey(cutout_image.crop(RIGHT_CROP))]
    panels = [panel.resize((w, h), Image.Resampling.NEAREST) for panel in panels]
    labels = [f"Original 072 crop {box_text} at {RIGHT_SCALE}x",
              "Old baseline: SAM-filled v2 candidate-0",
              "New: regional fusion v2 (+ extra alpha polygons)"]
    out = Image.new("RGB", (w * 3, h + LABEL_H), LABEL_BG)
    draw, font = ImageDraw.Draw(out), ImageFont.load_default()
    for i, panel in enumerate(panels):
        out.paste(panel, (i * w, LABEL_H))
        draw.text((i * w + 8, 6), labels[i], fill=(30, 30, 30), font=font)
    return out


def right_polygons(rgb):
    box_text = "(%d,%d,%d,%d)" % RIGHT_CROP
    w = (RIGHT_CROP[2] - RIGHT_CROP[0]) * RIGHT_SCALE
    h = (RIGHT_CROP[3] - RIGHT_CROP[1]) * RIGHT_SCALE
    crop = Image.fromarray(rgb, "RGB").crop(RIGHT_CROP).resize((w, h), Image.Resampling.NEAREST)
    crop_draw = ImageDraw.Draw(crop)
    x0, y0 = RIGHT_CROP[:2]
    for name, points in EXTRA_POLYGONS.items():
        scaled = [((x - x0) * RIGHT_SCALE, (y - y0) * RIGHT_SCALE) for x, y in points]
        crop_draw.line(scaled + scaled[:1], fill=RIGHT_POLYGON_COLORS[name], width=2)
    legend_rows = len(EXTRA_POLYGONS) + 1
    legend_h = 8 + legend_rows * 20
    out = Image.new("RGB", (w, LABEL_H + legend_h + h), PREVIEW_BG)
    draw, font = ImageDraw.Draw(out), ImageFont.load_default()
    draw.text((8, 6), f"Original 072 crop {box_text} at {RIGHT_SCALE}x - boundaries only",
              fill=(30, 30, 30), font=font)
    y = LABEL_H + 4
    for name in EXTRA_POLYGONS:
        draw.rectangle([8, y + 2, 20, y + 14], fill=RIGHT_POLYGON_COLORS[name])
        draw.text((28, y + 2), f"{name} - hand-drawn, not final acceptance",
                  fill=(30, 30, 30), font=font)
        y += 20
    draw.text((8, y + 2), "Source RGB unchanged; outlines are preview-only overlays.",
              fill=(30, 30, 30), font=font)
    out.paste(crop, (0, LABEL_H + legend_h))
    return out


def png_info(srgb, gamma_100000):
    info = PngInfo()
    info.add(b"sRGB", bytes([srgb]))
    info.add(b"gAMA", struct.pack(">I", gamma_100000))
    return info


def save_png(image, path, srgb, gamma_100000):
    image.save(path, format="PNG", optimize=True, pnginfo=png_info(srgb, gamma_100000))


def read_png(path, mode, size, srgb, gamma_100000):
    with Image.open(path) as im:
        need((im.format, im.mode, im.size) == ("PNG", mode, size),
             f"PNG readback mismatch: {path}")
        meta_ok = (im.info.get("srgb") == srgb and im.info.get("gamma") is not None
                   and int(round(float(im.info["gamma"]) * 100000)) == gamma_100000)
        return np.array(im, dtype=np.uint8).copy(), meta_ok


def main():
    run_start = time.perf_counter()
    inputs = [(REF, "source_rgba_072"), (BASELINE, "baseline_sam_filled_v2_candidate_0"),
              (MASK_JSON, "manual_parts_polygons")]
    inputs += [(RAW_DIR / f"{name}-raw-0.png", f"local_sam_raw_0_{name}") for name in REGION_BOXES]
    inputs += [(ORIGINAL, "original_screenshot_read_only")]
    for path, _ in inputs:
        need(path.is_file() and not path.is_symlink(), f"Missing or symlinked fixed input: {path}")

    for path in (OUT_ART, OUT_QA):
        need(not path.exists() and not path.is_symlink(), f"Refusing existing output directory: {path}")
        need(path.resolve().is_relative_to(ROOT.resolve()), f"Output escapes repository root: {path}")
        need(path.resolve().drive.upper() != "D:", f"Refusing to write D drive: {path}")

    before = {path: digest(path) for path, _ in inputs}
    ref = load_image(REF, "RGBA")
    need(bool(np.all(ref[..., 3] == 255)), "072 source alpha is not all 255")
    rgb = ref[..., :3].copy()
    baseline = load_image(BASELINE, "RGBA")
    need(np.array_equal(baseline[..., :3], rgb), "Baseline candidate-0 RGB differs from 072")
    baseline_alpha = baseline[..., 3].copy()

    with Image.open(ORIGINAL) as im:
        need((im.format, im.size) == ("PNG", ORIGINAL_SIZE), f"Expected original PNG 3840x2160: {ORIGINAL}")
        need("icc_profile" not in im.info, f"Unexpected ICC profile: {ORIGINAL}")
        srgb, gamma = im.info.get("srgb"), im.info.get("gamma")
        need(type(srgb) is int and 0 <= srgb <= 3, "Missing/unsupported original sRGB chunk")
        need(isinstance(gamma, (int, float)) and not isinstance(gamma, bool) and gamma > 0,
             "Missing/invalid original gAMA chunk")
        original = np.array(im.convert("RGBA"), dtype=np.uint8).copy()
    srgb, gamma_100000 = int(srgb), int(round(float(gamma) * 100000))
    ox0, oy0, ox1, oy1 = ORIGINAL_CROP
    need((ox1 - ox0, oy1 - oy0) == SIZE, "Original fixed crop size mismatch")
    crop_matches = bool(np.array_equal(original[oy0:oy1, ox0:ox1], ref))
    need(crop_matches, "Original fixed crop RGBA does not match 072")

    data = json.loads(MASK_JSON.read_text(encoding="utf-8"))
    need(data.get("source") == rel(REF) and data.get("size") == list(SIZE), "Manual JSON source/size mismatch")
    parts = data.get("parts")
    need(isinstance(parts, dict) and parts, "Manual JSON has no parts")
    for name, points in parts.items():
        check_polygon(name, points)
    need(set(EXCLUDED_PARTS) <= set(parts), "Manual JSON is missing an excluded part")
    need(all(name in parts for name in HEAD_PARTS), "Manual JSON is missing a head protection part")
    body_parts = [name for name in parts if name not in EXCLUDED_PARTS]
    need(bool(body_parts), "No body parts left for the safe core")

    # 2) Manual body union at 4x -> LANCZOS -> >127 -> one 3x3 ellipse erosion.
    core = polygon_alpha([parts[name] for name in body_parts]) > 127
    core = cv2.erode(core.astype(np.uint8),
                     cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (3, 3))) > 0
    need(bool(core.any()), "Safe core is empty")
    alpha = np.maximum(baseline_alpha, np.where(core, 255, 0).astype(np.uint8))

    # 3) For each fixed local raw-0, keep whole 8-connected components touching core.
    region_reports = {}
    raw_inside_boxes = True
    for name, box in REGION_BOXES.items():
        path = RAW_DIR / f"{name}-raw-0.png"
        raw = load_image(path, "L")
        bx0, by0, bx1, by1 = box
        outside = raw > 0
        outside[by0:by1, bx0:bx1] = False
        inside_ok = not bool(outside.any())
        raw_inside_boxes &= inside_ok
        need(inside_ok, f"Raw mask has pixels outside its known box: {name}")
        binary = raw > 0
        need(bool(binary.any()), f"Empty local raw mask: {name}")
        total, labels, stats, _ = cv2.connectedComponentsWithStats(binary.astype(np.uint8), connectivity=8)
        kept_ids = sorted({int(v) for v in np.unique(labels[core]) if int(v)})
        keep = np.isin(labels, kept_ids)
        alpha = np.maximum(alpha, np.where(keep, 255, 0).astype(np.uint8))
        areas = {i: int(stats[i, cv2.CC_STAT_AREA]) for i in range(1, total)}
        kept_areas = [areas[i] for i in kept_ids]
        removed_areas = [areas[i] for i in range(1, total) if i not in kept_ids]
        region_reports[name] = {
            "raw_mask_path": rel(path), "raw_mask_sha256": before[path],
            "box_xyxy_half_open": list(box), "nonzero_pixels": int(binary.sum()),
            "components_total": total - 1, "components_kept": len(kept_ids),
            "components_removed": total - 1 - len(kept_ids),
            "kept_pixels": int(keep.sum()), "removed_pixels": int(binary.sum() - keep.sum()),
            "kept_component_areas_px": kept_areas, "removed_component_areas_px": removed_areas,
            "rule": ("8-connected components of this raw-0 mask; keep each whole component "
                     "that intersects the safe core, remove the rest; then max into alpha"),
        }

    # 4) Narrow the two rough head ornaments inside their ROIs, then add hand polygons.
    head_protection = polygon_alpha([parts[name] for name in HEAD_PARTS])
    core_head_clear = True
    for rx0, ry0, rx1, ry1 in HEAD_ROIS:
        need(0 <= rx0 < rx1 <= SIZE[0] and 0 <= ry0 < ry1 <= SIZE[1], "Bad head ROI")
        roi_core = bool(core[ry0:ry1, rx0:rx1].any())
        core_head_clear &= not roi_core
        need(not roi_core, "Head ROI intersects the safe core")
        alpha[ry0:ry1, rx0:rx1] = np.minimum(alpha[ry0:ry1, rx0:rx1],
                                             head_protection[ry0:ry1, rx0:rx1])
    check_polygon("left_head_ornament", LEFT_ORNAMENT)
    check_polygon("right_head_ornament", RIGHT_ORNAMENT)
    ornament_alpha = polygon_alpha([LEFT_ORNAMENT, RIGHT_ORNAMENT])
    alpha = np.maximum(alpha, ornament_alpha)

    # 5) Final v1-compatible pass: max in three extra hand-drawn alpha polygons.
    # These are not final annotations and never alter RGB or remove visible alpha.
    for name, points in EXTRA_POLYGONS.items():
        check_polygon(name, points)
    extra_alpha = polygon_alpha(list(EXTRA_POLYGONS.values()))
    alpha_before_extra = alpha.copy()
    alpha = np.maximum(alpha, extra_alpha)
    extra_support = extra_alpha > 0
    extra_added = alpha > alpha_before_extra
    extra_removed = alpha < alpha_before_extra
    extra_alpha_is_exact_max = bool(np.array_equal(alpha, np.maximum(alpha_before_extra, extra_alpha)))
    alpha_only_additions = bool(extra_alpha_is_exact_max
                                and np.array_equal(alpha[~extra_support],
                                                   alpha_before_extra[~extra_support])
                                and not bool(extra_removed.any()))
    extra_face_pixels_changed = int(
        extra_added[FACE_RECT[1]:FACE_RECT[3], FACE_RECT[0]:FACE_RECT[2]].sum())
    extra_polygon_reports = {}
    for name, points in EXTRA_POLYGONS.items():
        one_alpha = polygon_alpha([points])
        support = one_alpha > 0
        extra_polygon_reports[name] = {
            "points_xy": points,
            "aa_alpha_nonzero_pixels": int(support.sum()),
            "aa_alpha_bbox_xyxy_half_open": bbox(support),
            "newly_visible_pixels": int((support & (alpha_before_extra == 0)).sum()),
            "alpha_increased_pixels": int((support & extra_added).sum()),
        }
    extra_polygon_meta = {
        "count": len(EXTRA_POLYGONS),
        "names": list(EXTRA_POLYGONS),
        "source_cn": EXTRA_POLYGON_SOURCE_CN,
        "manual_hand_drawn": True,
        "final_acceptance": False,
        "coordinate_system": "integer x/y in the fixed 820x1440 072 crop coordinate space",
        "aa_construction": ("each polygon filled at 4x, LANCZOS down to 820x1440, "
                            "then max into final alpha"),
        "operation": "alpha=max(v1_final_alpha, extra_polygons_AA_alpha); RGB untouched",
        "rgb_changed": False,
        "pixels_with_aa_alpha_union": int(extra_support.sum()),
        "newly_visible_pixels_union": int((extra_support & (alpha_before_extra == 0)).sum()),
        "alpha_increased_pixels_union": int(extra_added.sum()),
        "pixels_removed_or_decreased": int(extra_removed.sum()),
        "face_rect_pixels_changed": extra_face_pixels_changed,
        "polygons": extra_polygon_reports,
    }

    corners = dict(zip(("top_left", "top_right", "bottom_left", "bottom_right"),
                       (int(alpha[y, x]) for x, y in CORNERS)))
    corners_ok = not any(corners.values())
    core_ok = bool(np.all(alpha[core] == 255))
    fx0, fy0, fx1, fy1 = FACE_RECT
    face_ok = bool(np.array_equal(alpha[fy0:fy1, fx0:fx1], baseline_alpha[fy0:fy1, fx0:fx1]))
    bbox_final = bbox(alpha > 0)
    need(corners_ok, f"Four corners are not transparent: {corners}")
    need(core_ok, "Safe core is not all 255 in final alpha")
    need(face_ok, "Face rectangle alpha differs from baseline")
    need(bbox_final is not None, "Final alpha is empty")

    cutout_array = np.dstack([rgb, alpha])
    cutout_image = Image.fromarray(cutout_array, "RGBA")
    baseline_image = Image.fromarray(baseline, "RGBA")
    pink = on_color(rgb, alpha, PINK)
    desktop = desktop_tile(rgb, alpha)
    upper = upper_compare(rgb, baseline_image, cutout_image)
    full = full_compare(rgb, baseline_image, cutout_image)
    right_cmp = right_compare(rgb, baseline_image, cutout_image)
    right_poly = right_polygons(rgb)
    right_poly_source_ok = bool(np.array_equal(rgb, ref[..., :3]))

    OUT_ART.mkdir(parents=True, exist_ok=False)
    OUT_QA.mkdir(parents=True, exist_ok=False)
    save_png(cutout_image, CUTOUT, srgb, gamma_100000)
    save_png(Image.fromarray(alpha, "L"), MASK_PNG, srgb, gamma_100000)
    save_png(Image.fromarray(pink, "RGB"), PINK_PNG, srgb, gamma_100000)
    save_png(desktop, DESKTOP_PNG, srgb, gamma_100000)
    save_png(upper, UPPER_PNG, srgb, gamma_100000)
    save_png(full, FULL_PNG, srgb, gamma_100000)
    save_png(right_cmp, RIGHT_COMPARE_PNG, srgb, gamma_100000)
    save_png(right_poly, RIGHT_POLYGONS_PNG, srgb, gamma_100000)

    saved, cutout_meta = read_png(CUTOUT, "RGBA", SIZE, srgb, gamma_100000)
    saved_mask, mask_meta = read_png(MASK_PNG, "L", SIZE, srgb, gamma_100000)
    saved_pink, pink_meta = read_png(PINK_PNG, "RGB", SIZE, srgb, gamma_100000)
    saved_desktop, desktop_meta = read_png(DESKTOP_PNG, "RGB", DESKTOP, srgb, gamma_100000)
    saved_upper, upper_meta = read_png(UPPER_PNG, "RGB", upper.size, srgb, gamma_100000)
    saved_full, full_meta = read_png(FULL_PNG, "RGB", full.size, srgb, gamma_100000)
    saved_right_cmp, right_cmp_meta = read_png(RIGHT_COMPARE_PNG, "RGB", right_cmp.size,
                                               srgb, gamma_100000)
    saved_right_poly, right_poly_meta = read_png(RIGHT_POLYGONS_PNG, "RGB", right_poly.size,
                                                 srgb, gamma_100000)
    all_exact = (np.array_equal(saved, cutout_array) and np.array_equal(saved_mask, alpha)
                 and np.array_equal(saved_pink, pink) and np.array_equal(saved_desktop, np.asarray(desktop))
                 and np.array_equal(saved_upper, np.asarray(upper))
                 and np.array_equal(saved_full, np.asarray(full))
                 and np.array_equal(saved_right_cmp, np.asarray(right_cmp))
                 and np.array_equal(saved_right_poly, np.asarray(right_poly)))
    all_meta = all((cutout_meta, mask_meta, pink_meta, desktop_meta, upper_meta, full_meta,
                    right_cmp_meta, right_poly_meta))
    need(all_exact, "PNG readback differs from computed output")
    need(all_meta, "PNG sRGB/gAMA readback failed")

    after = {path: digest(path) for path, _ in inputs}
    unchanged = {path: before[path] == after[path] for path, _ in inputs}
    need(all(unchanged.values()), "A fixed input changed during the run")

    elapsed = time.perf_counter() - run_start
    output_specs = (("cutout", CUTOUT, "RGBA", SIZE), ("mask", MASK_PNG, "L", SIZE),
                    ("pink", PINK_PNG, "RGB", SIZE), ("desktop", DESKTOP_PNG, "RGB", DESKTOP),
                    ("upper_compare", UPPER_PNG, "RGB", upper.size),
                    ("full_compare", FULL_PNG, "RGB", full.size),
                    ("right_compare", RIGHT_COMPARE_PNG, "RGB", right_cmp.size),
                    ("right_polygons", RIGHT_POLYGONS_PNG, "RGB", right_poly.size))
    outputs = {key: {"path": rel(path), "mode": mode, "size": list(size), "sha256": digest(path)}
               for key, path, mode, size in output_specs}
    outputs["report"] = {"path": rel(REPORT), "schema": SCHEMA}

    report = {
        "schema": SCHEMA, "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "static_single_image": True, "manual_visual_selection": True,
        "automatic_score_selection": False, "final_acceptance": False, "release": False,
        "runtime_asset": False, "visual_quality_verified": False, "is_animation": False,
        "purpose_cn": ("在固定 072 输入与 v1 区域融合步骤结果上，于头饰 alpha 之后追加三个人工描边窄多边形的"
                       "4x 抗锯齿 alpha，输出到全新 v2 目录；只补 alpha，不改 RGB，不宣称最终通过。"),
        "visual_selection_rationale_cn": [
            "左肩/右肩：candidate-0 比全身 SAM 更完整，采用局部 raw-0。",
            "左衣摆：candidate-0 最完整，但含孤立椅子碎片；用安全 core 相交的整连通域筛选去掉不相交碎片。",
            "右衣摆：candidate-0 仍丢手部，但整体较完整；接受并记录该限制。",
            "右侧：对照 right-edge-grid 确认灰色细条是袍片，不能作为背景删除；原图灰色袍片比现有抠图完整。",
            "右侧下方仍缺青绿发束与斜伸金色绳；三处均以人工描边窄多边形补 alpha，属草稿，未经最终验收。",
            "头饰：全部区域 candidate-2 更碎；SAM 头饰保留背景蓝字，不采用，改用明确手描窄多边形。",
            "安全 core 只用 3x3 腐蚀，比旧 17px 保留更多衣摆/手部；这里只填充 alpha，不改 RGB。",
            "仍缺细发丝与尾部连接核验；新增多边形未经独立验收，不是最终验收，未进入 release。",
        ],
        "algorithm": [
            "1. Copy baseline candidate-0 alpha; RGB is never regenerated.",
            "2. Exclude the seven head/tail-ornament parts; union the remaining manual parts on a 4x canvas, LANCZOS down, alpha>127, then one 3x3 ellipse erosion = safe core. alpha=max(baseline, core*255).",
            "3. For each of four fixed local raw-0 masks: 8-connected components, keep every whole component intersecting the safe core, max into alpha. This removes the isolated chair fragment.",
            "4. Build 4x anti-aliased headProtection from head_hair_face + right_hair_bun; inside the two head ROIs set alpha=min(alpha, headProtection). Then max in the two 4x anti-aliased narrow hand-drawn ornament polygons.",
            "5. Append three fixed hand-drawn EXTRA_POLYGONS (right outer robe strip, right hair loop, right gold cord): fill each at 4x, LANCZOS down, then alpha=max(alpha, extraAlpha) after the head-ornament step. Only alpha can increase; RGB is untouched.",
            "6. No global open/close, no denoise, no color threshold, no recolor, no new RGB; all RGB bytes stay from 072.",
        ],
        "manual_parts_body_used": body_parts,
        "manual_parts_excluded_for_core": sorted(EXCLUDED_PARTS),
        "hand_polygons_xy": {"left_head_ornament": LEFT_ORNAMENT,
                             "right_head_ornament": RIGHT_ORNAMENT},
        "head_ornament": {
            "removed_rough_parts": ["left_head_ink_ornament", "right_head_ink_ornament"],
            "protected_parts": list(HEAD_PARTS),
            "rois_xyxy_half_open": [list(roi) for roi in HEAD_ROIS],
            "operation": ("alpha=min(alpha, headProtection) inside each ROI, then "
                          "alpha=max(alpha, narrow hand-polygon AA alpha); RGB untouched"),
        },
        "extra_polygons": extra_polygon_meta,
        "comparison_crops": {
            "right_crop_xyxy_half_open": list(RIGHT_CROP),
            "right_scale": RIGHT_SCALE,
            "right_compare_columns": [
                "Original 072 RGB crop (opaque source)",
                "Old baseline: SAM-filled v2 candidate-0 alpha over light grey",
                "New: regional fusion v2 alpha over light grey",
            ],
            "right_polygons": ("same original 072 RGB crop at the same scale, with preview-only "
                               "colored outlines of the three extra polygons; source RGB unchanged"),
        },
        "v1_relationship": {
            "note_cn": ("原 v1 输入源全部未变，v1 产物保留；相对 v1 只改目标目录、"
                        "追加三个 alpha-only 手描多边形，并扩展检查与 v2 标签。"),
            "v1_input_sources_unchanged": all(unchanged.values()),
            "v1_outputs_preserved": True,
            "v1_output_directories": ["art-source/v1.3/si-game-regional-fusion-v1",
                                       "qa/si-game-regional-fusion-v1"],
            "v1_report_used_as_comparison_baseline": False,
            "comparison_baseline_is": (f"{rel(BASELINE)} (SAM-filled v2 candidate-0, "
                                       "not regional-fusion-v1)"),
            "algorithm_before_extra_polygons_unchanged": True,
            "changes_vs_v1": [
                "Target output directories changed to art-source/v1.3/si-game-regional-fusion-v2 and qa/si-game-regional-fusion-v2.",
                "Three hand-drawn alpha-only EXTRA_POLYGONS appended after the v1 head-ornament step.",
                "right-compare.png and right-polygons.png added with fixed paths, readback, and hashes.",
                "Checks extended (extra names/count, alpha-only additions, face preserved); schema and labels updated to v2.",
            ],
        },
        "inputs": [{"path": path_label(path), "role": role, "sha256_before": before[path],
                    "sha256_after": after[path], "unchanged": unchanged[path]}
                   for path, role in inputs],
        "source": {"path": rel(REF), "mode": "RGBA", "size": list(SIZE), "alpha_all_255": True,
                   "rgb_policy": "all RGB bytes copied byte-for-byte from 072; alpha only",
                   "sha256_before": before[REF], "sha256_after": after[REF], "unchanged": unchanged[REF]},
        "baseline": {"path": rel(BASELINE),
                     "role": "SAM-filled v2 candidate-0 alpha copied; not regional-fusion-v1",
                     "not_regional_fusion_v1": True,
                     "rgb_identical_to_072": True, "sha256_before": before[BASELINE],
                     "sha256_after": after[BASELINE], "unchanged": unchanged[BASELINE]},
        "manual_parts": {"path": rel(MASK_JSON), "sha256_before": before[MASK_JSON],
                         "sha256_after": after[MASK_JSON], "unchanged": unchanged[MASK_JSON],
                         "part_count": len(parts)},
        "original_source": {"path": path_label(ORIGINAL), "read_only": True,
                            "sha256_before": before[ORIGINAL], "sha256_after": after[ORIGINAL],
                            "unchanged": unchanged[ORIGINAL], "fixed_crop_xyxy_half_open": list(ORIGINAL_CROP),
                            "crop_size": list(SIZE), "crop_rgba_byte_identical_to_072": crop_matches,
                            "png_color_declarations": {"srgb": srgb, "gamma": float(gamma),
                                                       "gama_100000": gamma_100000,
                                                       "icc_profile_present": False},
                            "d_drive_write_operations": 0},
        "regions": region_reports,
        "alpha": {"baseline_visible_pixels": int((baseline_alpha > 0).sum()),
                  "safe_core_pixels": int(core.sum()), "source_face_rect_xyxy_half_open": list(FACE_RECT),
                  "v1_compatible_visible_pixels_before_extra": int((alpha_before_extra > 0).sum()),
                  "extra_polygons_newly_visible_pixels": int(
                      (extra_support & (alpha_before_extra == 0)).sum()),
                  "extra_polygons_alpha_increased_pixels": int(extra_added.sum()),
                  "alpha_pixels_removed_by_extra_polygons": int(extra_removed.sum()),
                  "final_visible_pixels": int((alpha > 0).sum()),
                  "final_opaque_pixels": int((alpha == 255).sum()),
                  "final_transparent_pixels": int((alpha == 0).sum()),
                  "final_alpha_bbox_xyxy_half_open": bbox_final, "four_corners_alpha": corners},
        "outputs": outputs,
        "checks": {
            "both_output_directories_were_new": True,
            "all_input_hashes_unchanged": all(unchanged.values()),
            "original_file_read_only_hash_unchanged": unchanged[ORIGINAL],
            "original_crop_rgba_byte_identical_to_072": crop_matches,
            "baseline_rgb_identical_to_072": True,
            "comparison_baseline_not_regional_fusion_v1": True,
            "v1_report_not_used_as_comparison_baseline": True,
            "output_rgb_identical_to_072": bool(np.array_equal(saved[..., :3], rgb)),
            "safe_core_all_alpha_255": core_ok,
            "head_rois_do_not_touch_safe_core": core_head_clear,
            "extra_polygon_names": list(EXTRA_POLYGONS),
            "extra_polygon_count": len(EXTRA_POLYGONS),
            "extra_polygon_names_match": list(EXTRA_POLYGONS) == [
                "right_outer_robe_strip", "right_hair_loop", "right_gold_cord"],
            "alpha_only_additions_in_extra_polygons": alpha_only_additions,
            "extra_polygons_are_exact_max_into_alpha": extra_alpha_is_exact_max,
            "no_alpha_removed_by_extra_polygons": not bool(extra_removed.any()),
            "extra_polygons_do_not_change_face_rect": extra_face_pixels_changed == 0,
            "face_rect_alpha_equals_baseline": face_ok,
            "four_corners_transparent": corners_ok,
            "four_raw_masks_inside_known_boxes": raw_inside_boxes,
            "component_filter_only_fixed_local_raw_0_8_connectivity": True,
            "no_global_morphology_or_denoise": True,
            "no_inference_or_model_scores": True,
            "no_color_key_or_recolor": True,
            "no_new_source_rgb_generated": True,
            "right_polygons_preview_only_source_rgb_unchanged": right_poly_source_ok,
            "all_png_readback_exact": all_exact,
            "srgb_gama_copied_to_all_pngs": all_meta,
            "png_only_no_jpeg": all(path.suffix.lower() == ".png" for _, path, _, _ in output_specs),
            "no_d_drive_write": True,
        },
        "limitations": [
            "右侧灰色袍片、青绿发束、斜伸金色绳为人工描边补 alpha，边界未做像素级验收，可能带入背景或漏掉细节。",
            "三个新增多边形均为草稿，未经独立标注或最终验收；后续仍可能调整。",
            "细发丝仍缺；金色绳仅以手描窄带补充，完整轮廓与尾部连接处尚未核验。",
            "窄头饰为手描多边形，可能漏掉细小分叉、透明边缘或相邻蓝字残边。",
            "局部 raw-0 只保留与安全 core 相交的整连通域；若组件与 core 相连，背景碎片会整块保留。",
            "未做全局开闭、去噪或颜色阈值；细饰可能断裂，RGB 未改。",
            "人工目视选择不是最终验收；本阶段未进入 release，runtime_asset=false，visual_quality_verified=false，is_animation=false。",
        ],
        "non_destructive": {"fixed_inputs_only": True, "new_output_directories_only": True,
                            "no_overwrite_parameter": True, "old_partial_outputs_preserved": True,
                            "v1_outputs_preserved": True, "v1_products_written_this_run": False,
                            "existing_candidates_modified": False, "runtime_assets_modified": False,
                            "network_used": False, "new_software_installed": False,
                            "d_drive_written": False, "new_source_rgb_generated": False,
                            "comparison_previews_on_grey_are_composites_not_source_rgb": True,
                            "right_compare_original_panel_is_source_rgb": True,
                            "right_polygons_is_source_rgb_with_preview_outlines": True,
                            "sam_script_imported": False, "torch_imported": False},
        "elapsed_seconds": round(elapsed, 6),
    }
    REPORT.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    readback = json.loads(REPORT.read_text(encoding="utf-8"))
    need(readback.get("schema") == SCHEMA
         and readback.get("visual_quality_verified") is False
         and readback.get("runtime_asset") is False
         and readback.get("is_animation") is False
         and len(readback.get("regions", {})) == 4
         and readback.get("extra_polygons", {}).get("count") == len(EXTRA_POLYGONS)
         and readback.get("extra_polygons", {}).get("names") == list(EXTRA_POLYGONS)
         and readback.get("checks", {}).get("extra_polygon_names") == list(EXTRA_POLYGONS)
         and readback.get("checks", {}).get("extra_polygon_count") == len(EXTRA_POLYGONS)
         and readback.get("checks", {}).get("alpha_only_additions_in_extra_polygons") is True
         and readback.get("checks", {}).get("face_rect_alpha_equals_baseline") is True
         and readback.get("v1_relationship", {}).get("v1_input_sources_unchanged") is True
         and len(readback.get("outputs", {})) == len(output_specs) + 1,
         "Report readback/schema mismatch")
    print(json.dumps({
        "cutout": rel(CUTOUT), "outputs": [rel(path) for _, path, _, _ in output_specs],
        "report": rel(REPORT), "report_sha256": digest(REPORT),
        "components": {name: {"total": r["components_total"],
                                                     "kept": r["components_kept"],
                                                     "removed": r["components_removed"]}
                                              for name, r in region_reports.items()},
        "alpha": report["alpha"],
        "extra_polygons": {"count": extra_polygon_meta["count"],
                           "names": extra_polygon_meta["names"],
                           "newly_visible_pixels": extra_polygon_meta["newly_visible_pixels_union"],
                           "alpha_increased_pixels": extra_polygon_meta["alpha_increased_pixels_union"],
                           "face_rect_pixels_changed": extra_face_pixels_changed},
        "elapsed_seconds": round(elapsed, 6),
        "report_readback_verified": True, "runtime_asset": False,
        "visual_quality_verified": False, "is_animation": False,
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

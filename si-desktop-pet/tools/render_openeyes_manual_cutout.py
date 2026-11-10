#!/usr/bin/env python3
"""Manual-polygon cutout: alpha only, RGB untouched; fixed I/O, no overwrite.
4x L polygon union -> LANCZOS 820x1440; no model/key/recolor/warp/inpaint.
Draft only; not runtime/animation/visual approval.
"""
from __future__ import annotations

import argparse, hashlib, json
from pathlib import Path
from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parents[1]
REF = ROOT / "qa" / "si-body-reference-072.png"
MASK_JSON = ROOT / "art-source" / "v1.3" / "si-game-openeyes-manual-v1-mask.json"
OUT_RGBA = ROOT / "art-source" / "v1.3" / "si-game-openeyes-manual-v1.png"
OUT_PINK = ROOT / "qa" / "si-game-openeyes-manual-v1-pink.jpg"
OUT_DESKTOP = ROOT / "qa" / "si-game-openeyes-manual-v1-desktop.png"
OUT_COMPARE = ROOT / "qa" / "si-game-openeyes-manual-v1-upper-compare.jpg"
OUT_REPORT = ROOT / "qa" / "si-game-openeyes-manual-v1-report.json"
TARGETS = (OUT_RGBA, OUT_PINK, OUT_DESKTOP, OUT_COMPARE, OUT_REPORT)
SIZE, SCALE, PINK = (820, 1440), 4, (255, 105, 180)
DESKTOP, DESKTOP_BG, FIT_MAX, BASELINE = (384, 416), (232, 232, 232), (368, 404), 410
COMPARE_BG, LABEL_BG, CROP, LABEL_H = (232, 232, 232), (245, 245, 245), (270, 70, 610, 580), 24


def digest(path):
    h = hashlib.sha256()
    with path.open("rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""): h.update(block)
    return h.hexdigest()


def rel(path):
    return path.resolve().relative_to(ROOT.resolve()).as_posix()


def require(ok, message):
    if not ok: raise SystemExit(message)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--overwrite", action="store_true", help="replace only the five fixed outputs")
    args = parser.parse_args(argv)
    for path in (REF, MASK_JSON): require(path.is_file(), f"Missing fixed input: {path}")
    existing = [rel(path) for path in TARGETS if path.exists()]
    require(args.overwrite or not existing, f"Refusing to overwrite (use --overwrite): {existing}")
    before = {path: digest(path) for path in (REF, MASK_JSON)}
    with Image.open(REF) as im:
        require((im.mode, im.size) == ("RGBA", SIZE), f"Expected RGBA 820x1440, got {im.mode} {im.size}")
        source = im.copy()
    data = json.loads(MASK_JSON.read_text(encoding="utf-8"))
    require(data.get("source") == rel(REF) and data.get("size") == list(SIZE), "Manual mask source/size mismatch")
    parts = data.get("parts")
    require(isinstance(parts, dict) and parts, "Manual mask has no parts")
    big = Image.new("L", (SIZE[0] * SCALE, SIZE[1] * SCALE), 0)
    draw = ImageDraw.Draw(big)
    for name, points in parts.items():
        require(isinstance(name, str) and name and isinstance(points, list) and len(points) >= 3,
                f"Part needs a name and at least 3 points: {name!r}")
        for point in points:
            require(isinstance(point, list) and len(point) == 2 and all(type(v) is int for v in point)
                    and 0 <= point[0] < SIZE[0] and 0 <= point[1] < SIZE[1],
                    f"Polygon point invalid or outside source in {name}: {point!r}")
        draw.polygon([(x * SCALE, y * SCALE) for x, y in points], fill=255)
    alpha = big.resize(SIZE, Image.Resampling.LANCZOS)
    hist = alpha.histogram()
    visible, opaque, bbox = SIZE[0] * SIZE[1] - hist[0], hist[255], alpha.getbbox()
    corners = dict(zip(("top_left", "top_right", "bottom_left", "bottom_right"),
                       (alpha.getpixel(p) for p in ((0, 0), (SIZE[0] - 1, 0), (0, SIZE[1] - 1), (SIZE[0] - 1, SIZE[1] - 1)))))
    require(visible and bbox is not None, "Manual polygons produced an empty alpha")
    require(not any(corners.values()), f"Four corners are not transparent: {corners}")
    cutout = source.copy()
    cutout.putalpha(alpha)
    pink = Image.new("RGB", SIZE, PINK)
    pink.paste(cutout, (0, 0), cutout)
    content = cutout.crop(bbox)
    ratio = min(FIT_MAX[0] / content.width, FIT_MAX[1] / content.height)
    fitted_size = (min(FIT_MAX[0], max(1, round(content.width * ratio))),
                   min(FIT_MAX[1], max(1, round(content.height * ratio))))
    fitted = content.resize(fitted_size, Image.Resampling.LANCZOS)
    offset = ((DESKTOP[0] - fitted.width) // 2, BASELINE - fitted.height)
    require(offset[1] >= 0, "Desktop crop exceeds the 410 px baseline")
    desktop = Image.new("RGB", DESKTOP, DESKTOP_BG)
    desktop.paste(fitted, offset, fitted)
    original, region = source.crop(CROP).convert("RGB"), cutout.crop(CROP)
    on_grey = Image.new("RGB", region.size, COMPARE_BG)
    on_grey.paste(region, (0, 0), region)
    compare = Image.new("RGB", (original.width * 2, original.height + LABEL_H), LABEL_BG)
    compare.paste(original, (0, LABEL_H))
    compare.paste(on_grey, (original.width, LABEL_H))
    draw, font = ImageDraw.Draw(compare), ImageFont.load_default()
    draw.text((8, 6), "Original crop (270,70,610,580)", fill=(30, 30, 30), font=font)
    draw.text((original.width + 8, 6), "Manual polygon cutout, grey bg", fill=(30, 30, 30), font=font)
    cutout.save(OUT_RGBA, format="PNG", optimize=True); pink.save(OUT_PINK, format="JPEG", quality=95, subsampling=0, optimize=True)
    desktop.save(OUT_DESKTOP, format="PNG", optimize=True); compare.save(OUT_COMPARE, format="JPEG", quality=95, subsampling=0, optimize=True)
    with Image.open(OUT_RGBA) as im:
        require((im.mode, im.size) == ("RGBA", SIZE), "Saved cutout mode/size mismatch")
        rgb_ok = all(im.getchannel(c).tobytes() == source.getchannel(c).tobytes() for c in "RGB")
        alpha_ok = im.getchannel("A").tobytes() == alpha.tobytes()
    require(rgb_ok and alpha_ok, "Saved cutout RGB or alpha mismatch")
    with Image.open(OUT_PINK) as im: pink_ok = (im.mode, im.size) == ("RGB", SIZE); im.load()
    with Image.open(OUT_DESKTOP) as im: desktop_ok = (im.mode, im.size) == ("RGB", DESKTOP) and im.tobytes() == desktop.tobytes()
    with Image.open(OUT_COMPARE) as im: compare_ok = (im.mode, im.size) == ("RGB", compare.size); im.load()
    require(pink_ok and desktop_ok and compare_ok, "Preview readback mismatch")
    after = {path: digest(path) for path in (REF, MASK_JSON)}
    require(after == before, "Source image or manual mask JSON changed during the run")
    shas = {path: digest(path) for path in (OUT_RGBA, OUT_PINK, OUT_DESKTOP, OUT_COMPARE)}
    report = {
        "schema": "si-game-openeyes-manual-cutout-report/v1",
        "method": {"name": "manual_polygon_alpha_only", "mask": "L 4x, ImageDraw per-part polygon union fill=255, LANCZOS to 820x1440", "alpha_source": "manual polygons only", "color_key_used": False, "model_generation_used": False, "color_adjustment_used": False, "deformation_used": False, "inpainting_used": False, "rgb_policy": "all RGB bytes copied from source; only alpha replaced"},
        "source": {"path": rel(REF), "size": list(SIZE), "mode": "RGBA", "sha256_before": before[REF], "sha256_after": after[REF], "unchanged": before[REF] == after[REF]},
        "manual_mask": {"path": rel(MASK_JSON), "sha256_before": before[MASK_JSON], "sha256_after": after[MASK_JSON], "unchanged": before[MASK_JSON] == after[MASK_JSON]},
        "part_count": len(parts),
        "alpha": {"opaque_pixels": opaque, "visible_pixels": visible, "transparent_pixels": hist[0], "alpha_bbox_xyxy_half_open": list(bbox), "four_corners_alpha": corners, "all_four_corners_transparent": not any(corners.values()), "desktop_content_size": list(fitted_size), "desktop_offset_xy": list(offset), "desktop_bottom_y": BASELINE},
        "output_sha256": {rel(p): shas[p] for p in (OUT_RGBA, OUT_PINK, OUT_DESKTOP, OUT_COMPARE)},
        "checks": {"source_hash_unchanged": before[REF] == after[REF], "manual_mask_hash_unchanged": before[MASK_JSON] == after[MASK_JSON], "all_polygons_within_source": True, "all_parts_at_least_3_points": True, "output_png_rgb_equals_source": rgb_ok, "output_png_alpha_equals_computed": alpha_ok, "four_corners_transparent": not any(corners.values()), "desktop_readback_exact": desktop_ok, "pink_and_compare_readback_ok": pink_ok and compare_ok},
        "review": {"manual_trace_requires_per_part_review": True, "focus": ["horn contours", "thin ink ornaments", "tail end", "long hair and gold cords not fully traced"], "json_review_required": data.get("review_required", []), "visual_quality_verified": False, "note": "手描边尚需逐部位复核，尤其角、细墨饰、尾端、长发金绳未完整描边。RGB 逐字节一致仅证明未改色，不代表视觉验收合格。"},
        "runtime_asset": False, "is_animation": False,
        "non_destructive": {"fixed_write_targets_only": True, "write_targets": [rel(p) for p in TARGETS], "overwrite_requested": args.overwrite, "existing_outputs_before_run": existing},
    }
    OUT_REPORT.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"cutout": rel(OUT_RGBA), "part_count": len(parts), "opaque_pixels": opaque, "alpha_bbox_xyxy": list(bbox), "runtime_asset": False, "is_animation": False, "visual_quality_verified": False}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

#!/usr/bin/env python3
"""Fill all three SAM masks from a manual solid core; RGB stays the 072 original.
Post-process only; no SAM rerun, model scores, color key, threshold rescale,
redraw, warp, or grade. v2 fixes the 3-up preview readback and copies the
original screenshot's PNG sRGB/gAMA declarations; all candidates kept; draft only."""
from __future__ import annotations
import hashlib, json, struct, sys
from datetime import datetime, timezone
from pathlib import Path
sys.dont_write_bytecode = True
import numpy as np
from PIL import Image, ImageDraw, ImageFont
from PIL.PngImagePlugin import PngInfo
ROOT = Path(__file__).resolve().parents[1]
DEPS = ROOT / "qa" / "segmentation-deps"
sys.path.insert(0, str(DEPS))
import cv2  # noqa: E402
SIZE, CORNERS = (820, 1440), ((0, 0), (0, -1), (-1, 0), (-1, -1))
A, Q = ROOT / "art-source" / "v1.3", ROOT / "qa"
REF, MANUAL = Q / "si-body-reference-072.png", A / "si-game-openeyes-manual-v1.png"
ORIGINAL = Path(r"D:\终末地小祀\屏幕截图 2026-10-16 185835.png")
MASK_JSON = A / "si-game-openeyes-manual-v1-mask.json"
CANDIDATES = tuple(A / "si-game-sam-v1" / f"candidate-{i}.png" for i in range(3))
OUT_ART, OUT_QA = A / "si-game-sam-filled-v2", Q / "si-game-sam-filled-v2"
CORE_MASK, REPORT = OUT_QA / "core-mask.png", OUT_QA / "report.json"
PINK_PATHS = [OUT_QA / f"pink-{i}.jpg" for i in range(3)]
DESKTOP_PATH, UPPER_PATH = OUT_QA / "desktop-contact.jpg", OUT_QA / "upper-contact.jpg"
PINK, PREVIEW_BG, LABEL_BG = (255, 105, 180), (232, 232, 232), (245, 245, 245)
DESKTOP, FIT_MAX, BASELINE, CROP, LABEL_H = (384, 416), (368, 404), 410, (270, 70, 610, 580), 26
ORIGINAL_SIZE, ORIGINAL_CROP = (3840, 2160), (1830, 300, 2650, 1740)
NECK = [(359, 350), (384, 350), (412, 364), (464, 348), (480, 365), (495, 405),
        (478, 427), (367, 424), (349, 405)]
HEAD = ("left_head_ink_ornament", "right_head_ink_ornament")
RING = (525, 995, 605, 1190)  # half-open x525:605, y995:1190
INPUTS = ((REF, "source_rgba", "RGBA"), (MANUAL, "manual_rgba", "RGBA"),
          (MASK_JSON, "manual_polygon_json", "JSON"),
          (ORIGINAL, "original_screenshot_read_only", "PNG")) + tuple(
          (p, f"sam_candidate_{i}", "RGBA") for i, p in enumerate(CANDIDATES))
SCHEMA = "si-game-sam-filled-v2-report/v2"
def digest(path):
    h = hashlib.sha256()
    with path.open("rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""): h.update(block)
    return h.hexdigest()
def rel(path): return path.resolve().relative_to(ROOT.resolve()).as_posix()
def path_label(path):
    try: return rel(path)
    except ValueError: return str(path)
def need(ok, message):
    if not ok: raise SystemExit(message)
def ellipse(n): return cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (n, n))
def bbox(mask):
    ys, xs = np.nonzero(mask)
    return [int(xs.min()), int(ys.min()), int(xs.max()) + 1, int(ys.max()) + 1] if xs.size else None
def load(path):
    with Image.open(path) as im:
        need((im.mode, im.size) == ("RGBA", SIZE), f"Expected RGBA 820x1440: {path}")
        return np.array(im, dtype=np.uint8).copy()
def load_original(path):
    with Image.open(path) as im:
        need((im.format, im.size) == ("PNG", ORIGINAL_SIZE), f"Expected PNG 3840x2160: {path}")
        need("icc_profile" not in im.info, f"Unexpected ICC profile in original PNG; refusing to convert: {path}")
        srgb, gamma = im.info.get("srgb"), im.info.get("gamma")
        need(type(srgb) is int and 0 <= srgb <= 3, f"Missing/unsupported sRGB chunk: {path}")
        need(isinstance(gamma, (int, float)) and not isinstance(gamma, bool) and gamma > 0,
             f"Missing/invalid gAMA chunk: {path}")
        return np.array(im.convert("RGBA"), dtype=np.uint8).copy(), int(srgb), float(gamma)
def poly(points):
    need(isinstance(points, (list, tuple)) and len(points) >= 3, "Bad polygon")
    need(all(isinstance(p, (list, tuple)) and len(p) == 2 and all(
        type(v) is int and 0 <= v < SIZE[i] for i, v in enumerate(p)) for p in points), "Bad point")
    return np.asarray(points, np.int32)
def alpha_info(alpha):
    c = [int(alpha[y, x]) for y, x in CORNERS]
    return {"alpha_bbox_xyxy_half_open": bbox(alpha > 0), "opaque_pixels": int((alpha == 255).sum()),
            "transparent_pixels": int((alpha == 0).sum()),
            "four_corners_alpha": dict(zip(("top_left", "top_right", "bottom_left", "bottom_right"), c)),
            "all_four_corners_transparent": not any(c)}
def on_color(rgb, alpha, color):
    a = alpha.astype(np.float32)[..., None] / 255.0
    return np.clip(np.rint(rgb.astype(np.float32) * a + np.asarray(color, np.float32) * (1 - a)), 0, 255).astype(np.uint8)
def desktop_tile(rgb, alpha):
    im = Image.fromarray(np.dstack([rgb, alpha]), "RGBA"); box = im.getchannel("A").getbbox()
    need(box is not None, "Empty alpha for desktop preview")
    c = im.crop(box); scale = min(FIT_MAX[0] / c.width, FIT_MAX[1] / c.height)
    size = (min(FIT_MAX[0], max(1, round(c.width * scale))), min(FIT_MAX[1], max(1, round(c.height * scale))))
    fit = c.resize(size, Image.Resampling.LANCZOS); off = ((DESKTOP[0] - fit.width) // 2, BASELINE - fit.height)
    need(off[0] >= 0 and off[1] >= 0 and off[1] + fit.height == BASELINE, "Desktop placement failed")
    out = Image.new("RGB", DESKTOP, PREVIEW_BG); out.paste(fit, off, fit)
    return out
def png_color_chunks(srgb, gamma):
    info = PngInfo()
    info.add(b"sRGB", bytes([srgb]))
    info.add(b"gAMA", struct.pack(">I", int(round(gamma * 100000))))
    return info
def main():
    for path, _, _ in INPUTS: need(path.is_file() and not path.is_symlink(), f"Missing/symlinked input: {path}")
    for path in (OUT_ART, OUT_QA): need(not path.exists() and not path.is_symlink(), f"Refusing existing dir: {path}")
    before = {path: digest(path) for path, _, _ in INPUTS}
    source, manual = load(REF), load(MANUAL)
    need(np.all(source[..., 3] == 255), "Source alpha is not 255")
    need(np.array_equal(source[..., :3], manual[..., :3]), "Manual RGB differs from source")
    original, original_srgb, original_gamma = load_original(ORIGINAL)
    crop_x0, crop_y0, crop_x1, crop_y1 = ORIGINAL_CROP
    need((crop_x1 - crop_x0, crop_y1 - crop_y0) == SIZE, "Original crop size mismatch")
    crop_matches = bool(np.array_equal(original[crop_y0:crop_y1, crop_x0:crop_x1], source))
    need(crop_matches, "Original crop RGBA does not match current 072")
    gamma_100000 = int(round(original_gamma * 100000))
    cands = [load(path) for path in CANDIDATES]
    need(all(np.array_equal(c[..., :3], source[..., :3]) for c in cands), "SAM RGB differs from source")
    data = json.loads(MASK_JSON.read_text(encoding="utf-8")); parts = data.get("parts")
    need(data.get("source") == rel(REF) and data.get("size") == list(SIZE) and isinstance(parts, dict), "Manual JSON mismatch")
    coarse = (manual[..., 3] > 127).astype(np.uint8)
    need(0 < int(coarse.sum()) < coarse.size, "Empty/full manual alpha")
    coarse = cv2.morphologyEx(coarse, cv2.MORPH_CLOSE, ellipse(9)); cv2.fillPoly(coarse, [poly(NECK)], 1)
    coarse_b = coarse.astype(bool); core = cv2.erode(coarse, ellipse(17))
    for name in HEAD: need(name in parts, f"Missing part: {name}"); cv2.fillPoly(core, [poly(parts[name])], 0)
    core[RING[1]:RING[3], RING[0]:RING[2]] = 0; core_b = core.astype(bool)
    need(core_b.any() and not any(core_b[y, x] for y, x in CORNERS), "Bad core")
    results = []
    for i, (path, cand) in enumerate(zip(CANDIDATES, cands)):
        alpha = np.maximum(cand[..., 3], np.where(core_b, 255, 0).astype(np.uint8))
        num, labels, stats, _ = cv2.connectedComponentsWithStats((alpha > 32).astype(np.uint8), connectivity=8)
        ids = {int(v) for v in np.unique(labels[coarse_b]) if int(v)}
        keep = np.isin(labels, list(ids))
        need(bool(np.all(keep[core_b])), f"Core touched by component removal: {i}")
        comps = []
        for label in range(1, num):
            x, y, w, h, area = (int(v) for v in stats[label]); kept = label in ids
            comps.append({"label": label, "area_pixels_alpha_gt_32": area, "bbox_xyxy_half_open": [x, y, x + w, y + h],
                          "intersects_coarse": kept, "kept": kept, "decision": "kept" if kept else "deleted"})
        alpha[~keep] = 0
        need(bool(np.all(alpha[core_b] == 255)), f"Core alpha !=255: {i}")
        info = alpha_info(alpha)
        need(info["alpha_bbox_xyxy_half_open"] is not None and info["all_four_corners_transparent"], f"Alpha/corners: {i}")
        results.append({"index": i, "input": path, "out": OUT_ART / f"candidate-{i}.png", "alpha": alpha,
                        "rgba": np.dstack([source[..., :3], alpha]).astype(np.uint8), "components": comps,
                        "retained": sum(c["kept"] for c in comps), "info": info})
    pinks = [on_color(source[..., :3], r["alpha"], PINK) for r in results]
    tiles = [desktop_tile(source[..., :3], r["alpha"]) for r in results]
    desktop = Image.new("RGB", (DESKTOP[0] * 3, DESKTOP[1]), PREVIEW_BG)
    for i, tile in enumerate(tiles): desktop.paste(tile, (DESKTOP[0] * i, 0))
    cw, ch = CROP[2] - CROP[0], CROP[3] - CROP[1]
    upper = Image.new("RGB", (cw * 4, ch + LABEL_H), LABEL_BG); draw = ImageDraw.Draw(upper); font = ImageFont.load_default()
    upper.paste(Image.fromarray(source[..., :3], "RGB").crop(CROP), (0, LABEL_H))
    draw.text((6, 7), "original | no mask", fill=(30, 30, 30), font=font)
    for i, r in enumerate(results):
        panel = Image.new("RGB", (cw, ch), PREVIEW_BG); region = Image.fromarray(r["rgba"], "RGBA").crop(CROP)
        panel.paste(region, (0, 0), region); upper.paste(panel, (cw * (i + 1), LABEL_H))
        draw.text((cw * (i + 1) + 6, 7), f"candidate-{i} | manual core fill", fill=(30, 30, 30), font=font)
    for path in (OUT_ART, OUT_QA): need(not path.exists() and not path.is_symlink(), f"Output appeared: {path}")
    OUT_ART.mkdir(parents=True, exist_ok=False); OUT_QA.mkdir(parents=True, exist_ok=False)
    for r in results:
        pnginfo = png_color_chunks(original_srgb, original_gamma)
        Image.fromarray(r["rgba"], "RGBA").save(r["out"], format="PNG", optimize=True, pnginfo=pnginfo)
        with Image.open(r["out"]) as im:
            need((im.mode, im.size) == ("RGBA", SIZE), "RGBA readback")
            need(im.info.get("srgb") == original_srgb, f"PNG sRGB readback: {r['out']}")
            read_gamma = im.info.get("gamma")
            need(read_gamma is not None and int(round(float(read_gamma) * 100000)) == gamma_100000,
                 f"PNG gAMA readback: {r['out']}")
            r["png_srgb"], r["png_gamma"] = int(im.info["srgb"]), float(read_gamma)
            saved = np.array(im, dtype=np.uint8).copy()
        need(np.array_equal(saved[..., :3], source[..., :3]) and np.array_equal(saved[..., 3], r["alpha"]), "PNG mismatch")
        need(not any(int(saved[y, x, 3]) for y, x in CORNERS) and bool(np.all(saved[..., 3][core_b] == 255)), "PNG core/corners")
        r["out_sha"] = digest(r["out"])
    core255 = core_b.astype(np.uint8) * 255
    Image.fromarray(core255, "L").save(CORE_MASK, format="PNG", optimize=True)
    with Image.open(CORE_MASK) as im:
        need((im.mode, im.size) == ("L", SIZE) and np.array_equal(np.array(im), core255), "Core mask readback")
    for path, arr in zip(PINK_PATHS, pinks): Image.fromarray(arr, "RGB").save(path, format="JPEG", quality=95, subsampling=0, optimize=True)
    desktop.save(DESKTOP_PATH, format="JPEG", quality=95, subsampling=0, optimize=True)
    upper.save(UPPER_PATH, format="JPEG", quality=95, subsampling=0, optimize=True)
    for path, size in [(p, SIZE) for p in PINK_PATHS] + [(DESKTOP_PATH, desktop.size), (UPPER_PATH, upper.size)]:
        with Image.open(path) as im: need((im.mode, im.size) == ("RGB", size), f"Preview readback: {path}")
    after = {p: digest(p) for p, _, _ in INPUTS}; need(before == after, "Input changed")
    paths = [r["out"] for r in results] + [CORE_MASK] + PINK_PATHS + [DESKTOP_PATH, UPPER_PATH]
    shas = {p: digest(p) for p in paths}
    rows = [{"path": path_label(p), "role": role, "mode": mode, "sha256_before": before[p], "sha256_after": after[p],
             "unchanged": before[p] == after[p]} for p, role, mode in INPUTS]
    cr = [{"index": r["index"], "input_path": rel(r["input"]), "input_sha256_before": before[r["input"]],
           "input_sha256_after": after[r["input"]], "output_path": rel(r["out"]), "output_sha256": shas[r["out"]],
           "component_count": len(r["components"]), "retained_component_count": r["retained"],
           "component_results": r["components"], **r["info"],
           "png_color_declarations": {"srgb": r["png_srgb"], "gamma": r["png_gamma"]}} for r in results]
    report = {
        "schema": SCHEMA, "created_at_utc": datetime.now(timezone.utc).isoformat(), "visual_quality_verified": False,
        "runtime_asset": False, "is_animation": False,
        "source": {"path": rel(REF), "sha256_before": before[REF], "sha256_after": after[REF], "unchanged": before[REF] == after[REF]},
        "original_source": {"path": path_label(ORIGINAL), "sha256_before": before[ORIGINAL], "sha256_after": after[ORIGINAL],
                            "unchanged": before[ORIGINAL] == after[ORIGINAL], "size": list(ORIGINAL_SIZE),
                            "crop_xyxy_half_open": list(ORIGINAL_CROP), "crop_size": list(SIZE),
                            "crop_rgba_byte_identical_to_072": crop_matches,
                            "png_color_declarations": {"srgb": original_srgb, "gamma": original_gamma,
                                                       "gama_100000": gamma_100000, "icc_profile_present": False}},
        "inputs": rows, "candidates": cr,
        "method": {"coarse": "manual alpha>127; 9x9 ellipse close; add checked neck polygon", "neck_polygon": NECK,
                   "core": "17x17 ellipse erode; subtract head ornament polygons; zero tail-ring rectangle", "ring_rect_half_open_xyxy": list(RING),
                   "head_ornaments_subtracted": list(HEAD), "fill": "max(SAM alpha, core*255)",
                   "component_filter": "alpha>32; 8-connected; keep whole components intersecting coarse; outside alpha=0",
                   "inside_retained_components": "original alpha unchanged", "rgb": "source RGB byte-for-byte",
                   "no_color_key_threshold_rescale_redraw_warp_or_grade": True, "scores_used": False,
                   "png_color_metadata": "original sRGB/gAMA copied via PngInfo on candidate PNGs; pixels untouched; ICC absent",
                   "automatic_selection": False, "same_algorithm_all_three": True},
        "core": {"path": rel(CORE_MASK), "sha256": shas[CORE_MASK], "bbox_xyxy_half_open": bbox(core_b), "foreground_pixels": int(core_b.sum()), "four_corners_transparent": True},
        "outputs": {"candidates": [{"path": rel(p), "sha256": shas[p]} for p in paths[:3]], "core_mask": rel(CORE_MASK),
                    "pink": {"paths": [rel(p) for p in PINK_PATHS], "size": list(SIZE), "background_rgb": list(PINK)},
                    "desktop_contact": {"path": rel(DESKTOP_PATH), "size": list(desktop.size), "columns": 3, "fit_max": list(FIT_MAX), "baseline_y": BASELINE, "background_rgb": list(PREVIEW_BG)},
                    "upper_contact": {"path": rel(UPPER_PATH), "size": list(upper.size), "crop": list(CROP), "layout": ["original", "candidate-0", "candidate-1", "candidate-2"]},
                    "report": rel(REPORT)},
        "checks": {"dirs_new": True, "all_input_sha_unchanged": before == after, "png_rgb_equals_source": True,
                   "png_alpha_equals_computed": True, "core_alpha_255": all(bool(np.all(r["alpha"][core_b] == 255)) for r in results),
                   "four_corners_transparent": all(r["info"]["all_four_corners_transparent"] for r in results),
                   "component_removal_never_touched_core": True, "no_automatic_selection": True, "no_model_scores_used": True,
                   "previews_are_composite_scale_only": True,
                   "original_png_read_only_unchanged": before[ORIGINAL] == after[ORIGINAL],
                   "original_crop_matches_072": crop_matches, "original_icc_absent": True,
                   "candidate_png_srgb_preserved": all(r["png_srgb"] == original_srgb for r in results),
                   "candidate_png_gamma_preserved": all(int(round(r["png_gamma"] * 100000)) == gamma_100000 for r in results)},
        "limitations": ["手描 core 仍可能填入背景；coarse 不是逐像素真值。", "细墨饰和绳索仍需逐项检查；孤立细节可能被连通域规则删除，相连碎片可能保留。",
                        "未做颜色键、mask 阈值重缩放、重绘、变形、调色或重新运行 SAM；预览只做合成/缩放。",
                        "预览 JPEG 仅用于查看，不是颜色基准；颜色声明以原始 PNG 的 sRGB/gAMA 和候选 PNG 为准。",
                        "visual_quality_verified=false；runtime_asset=false；is_animation=false。"],
        "non_destructive": {"fixed_inputs_only": True, "fixed_new_dirs_only": True, "new_rgb_generated": False,
                            "original_video_read": False, "source_or_inputs_modified": False,
                            "original_screenshot_modified": False, "old_drafts_android_release_configs_modified": False},
    }
    REPORT.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"candidates": [rel(p) for p in paths[:3]], "core_mask": rel(CORE_MASK), "report": rel(REPORT),
                      "visual_quality_verified": False, "runtime_asset": False, "is_animation": False},
                     ensure_ascii=False, indent=2))
    return 0

if __name__ == "__main__":
    raise SystemExit(main())

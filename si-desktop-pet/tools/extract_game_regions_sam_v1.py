#!/usr/bin/env python3
"""Diagnostic SAM ViT-B masks for six local regions of qa/si-body-reference-072.png.

This tool only segments the fixed source image. It copies source RGB unchanged,
clamps every candidate mask to its original-image region box, writes all three
multimask results per region (no score-based selection), and creates per-region
contact/point previews. It does not redraw, recolor, warp, inpaint, face-swap,
merge a runtime atlas, or modify existing candidates. All inputs are local except
the fixed read-only original screenshot used only for sRGB/gAMA and crop checks.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import struct
import sys
import time
from datetime import datetime, timezone
from importlib.metadata import PackageNotFoundError, version as package_version
from pathlib import Path

sys.dont_write_bytecode = True

import numpy as np
from PIL import Image, ImageDraw, ImageFont, __version__ as pillow_version
from PIL.PngImagePlugin import PngInfo

ROOT = Path(__file__).resolve().parents[1]
SAM_DEPS = ROOT / "qa" / "sam-deps"
sys.path.insert(0, str(SAM_DEPS))
import torch  # noqa: E402

torch.set_num_threads(8)
torch.set_num_interop_threads(2)
import segment_anything  # noqa: E402
from segment_anything import SamPredictor, sam_model_registry  # noqa: E402

REF = ROOT / "qa" / "si-body-reference-072.png"
CKPT = ROOT / "qa" / "segmentation-deps" / "sam_vit_b_01ec64.pth"
ORIGINAL = Path(r"D:\终末地小祀\屏幕截图 2026-10-16 185835.png")
OUT_ART = ROOT / "art-source" / "v1.3" / "si-game-regions-sam-v1"
OUT_QA = ROOT / "qa" / "si-game-regions-sam-v1"
REPORT = OUT_QA / "report.json"

SIZE = (820, 1440)
ORIGINAL_SIZE = (3840, 2160)
ORIGINAL_CROP = (1830, 300, 2650, 1740)
MODEL_BYTES = 375_042_383
MODEL_SHA = "ec2df62732614e57411cdcf32a23ffdf28910380d03139ee0f4fcbe91eb8c912"
MODEL_URL = "https://dl.fbaipublicfiles.com/segment_anything/sam_vit_b_01ec64.pth"
SAM_REPO = "Meta/facebookresearch/segment-anything"
SAM_COMMIT = "dca509fe793f601edb92606367a655c15ac00fdf"
SCHEMA = "si-game-regions-sam-v1-report/v1"
LABEL_H = 24
PREVIEW_BG = (232, 232, 232)
POINTS_BG = (245, 245, 245)
TEXT = (30, 30, 30)
POS_COLOR = (0, 180, 0)
NEG_COLOR = (220, 0, 0)

REGIONS = (
    {"name": "head_ink_left", "box": (264, 278, 348, 367),
     "pos": ((321, 300), (301, 326), (287, 347)),
     "neg": ((300, 300), (314, 335), (329, 321), (279, 330))},
    {"name": "head_ink_right", "box": (499, 284, 566, 367),
     "pos": ((512, 307), (535, 337), (548, 353)),
     "neg": ((521, 295), (520, 337), (547, 329))},
    {"name": "shoulder_left", "box": (273, 369, 354, 529),
     "pos": ((286, 397), (292, 439), (312, 455), (305, 489), (331, 491)),
     "neg": ((275, 425), (299, 411), (311, 433), (321, 433))},
    {"name": "shoulder_right", "box": (497, 365, 585, 532),
     "pos": ((574, 383), (568, 408), (540, 436), (547, 479), (558, 494)),
     "neg": ((559, 392), (583, 420), (548, 416), (550, 451))},
    {"name": "coat_left", "box": (53, 535, 339, 1210),
     "pos": ((185, 800), (152, 950), (120, 1100), (162, 1175), (249, 650), (320, 562)),
     "neg": ((74, 1100), (183, 1207), (246, 1000), (286, 821))},
    {"name": "coat_right", "box": (523, 548, 725, 1243),
     "pos": ((635, 850), (668, 1100), (644, 1180), (695, 1180), (581, 790)),
     "neg": ((723, 1040), (590, 1040), (619, 1228))},
)


def digest(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def rel(path: Path) -> str:
    return path.resolve().relative_to(ROOT.resolve()).as_posix()


def path_label(path: Path) -> str:
    try:
        return rel(path)
    except ValueError:
        return path.as_posix()


def need(ok, message):
    if not ok:
        raise SystemExit(message)


def ensure_new_output_dirs():
    for path in (OUT_ART, OUT_QA):
        need(not path.exists() and not path.is_symlink(),
             f"Refusing existing output directory: {path}")
        need(path.resolve().is_relative_to(ROOT.resolve()),
             f"Output escapes fixed repository root: {path}")
        need(path.resolve().drive.upper() != "D:", f"Refusing to write D drive: {path}")


def validate_regions():
    need(len(REGIONS) == 6, "Expected exactly six regions")
    names = [region["name"] for region in REGIONS]
    need(len(names) == len(set(names)), "Duplicate region names")
    for region in REGIONS:
        need(region["name"].replace("_", "").isalnum(), f"Bad region name: {region['name']}")
        box = region["box"]
        need(len(box) == 4 and all(type(v) is int for v in box), f"Bad box: {box}")
        x0, y0, x1, y1 = box
        need(0 <= x0 < x1 <= SIZE[0] and 0 <= y0 < y1 <= SIZE[1], f"Box out of bounds: {box}")
        need(region["pos"] and region["neg"], f"Missing prompt points: {region['name']}")
        for point in region["pos"] + region["neg"]:
            need(len(point) == 2 and all(type(v) is int for v in point), f"Bad point: {point}")
            x, y = point
            need(x0 <= x < x1 and y0 <= y < y1, f"Point outside its box: {region['name']} {point}")
        need(not (set(region["pos"]) & set(region["neg"])), f"Point is both POS and NEG: {region['name']}")


def load_model():
    model = sam_model_registry["vit_b"](checkpoint=None)
    state = torch.load(CKPT, map_location="cpu", weights_only=True)
    need(isinstance(state, dict), "SAM checkpoint did not load as a state dict")
    model.load_state_dict(state, strict=True)
    model.eval().cpu()
    need(all(p.device.type == "cpu" for p in model.parameters()), "SAM model is not on CPU")
    return model


def load_original(path: Path):
    with Image.open(path) as image:
        need((image.format, image.size) == ("PNG", ORIGINAL_SIZE),
             f"Expected PNG {ORIGINAL_SIZE}: {path}")
        need("icc_profile" not in image.info, f"Unexpected ICC profile; refusing conversion: {path}")
        srgb = image.info.get("srgb")
        gamma = image.info.get("gamma")
        need(type(srgb) is int and 0 <= srgb <= 3, f"Missing/unsupported sRGB chunk: {path}")
        need(isinstance(gamma, (int, float)) and not isinstance(gamma, bool) and gamma > 0,
             f"Missing/invalid gAMA chunk: {path}")
        pixels = np.array(image.convert("RGBA"), dtype=np.uint8).copy()
    return pixels, int(srgb), float(gamma)


def png_color_info(srgb: int, gamma: int):
    info = PngInfo()
    info.add(b"sRGB", bytes([srgb]))
    info.add(b"gAMA", struct.pack(">I", gamma))
    return info


def color_info_matches(image: Image.Image, srgb: int, gamma: int) -> bool:
    got = image.info.get("gamma")
    return (image.info.get("srgb") == srgb and got is not None
            and int(round(float(got) * 100000)) == gamma)


def load_font(size: int):
    for name in ("arial.ttf", "segoeui.ttf", "DejaVuSans.ttf"):
        try:
            return ImageFont.truetype(name, size)
        except OSError:
            pass
    return ImageFont.load_default()


def mask_bbox(mask: np.ndarray):
    ys, xs = np.nonzero(mask)
    if not xs.size:
        return None
    return [int(xs.min()), int(ys.min()), int(xs.max()) + 1, int(ys.max()) + 1]


def alpha_stats(raw_bool: np.ndarray, alpha: np.ndarray, box_mask: np.ndarray):
    return {
        "foreground_pixels": int(raw_bool.sum()),
        "background_pixels_in_box": int(box_mask.sum() - raw_bool.sum()),
        "box_pixels": int(box_mask.sum()),
        "foreground_fraction_of_box": round(float(raw_bool.sum()) / float(box_mask.sum()), 9),
        "alpha_bbox_xyxy_half_open": mask_bbox(raw_bool),
        "alpha_min": int(alpha.min()), "alpha_max": int(alpha.max()),
        "alpha_unique_values": [int(v) for v in np.unique(alpha)],
        "outside_box_nonzero_alpha_pixels": int(np.count_nonzero(alpha[~box_mask])),
    }


def fit_tile(box):
    width, height = box[2] - box[0], box[3] - box[1]
    scale = min(2.0, 360.0 / width, 720.0 / height)
    tile_w = min(360, max(1, int(width * scale)))
    tile_h = min(720, max(1, int(height * scale)))
    return tile_w, tile_h, min(tile_w / width, tile_h / height)


def make_contact(region, rgb, candidates):
    x0, y0, x1, y1 = region["box"]
    tile_w, tile_h, scale = fit_tile(region["box"])
    canvas = Image.new("RGB", (tile_w * 4, LABEL_H + tile_h), PREVIEW_BG)
    original = Image.fromarray(rgb, "RGB").crop((x0, y0, x1, y1)).resize(
        (tile_w, tile_h), Image.Resampling.LANCZOS)
    canvas.paste(original, (0, LABEL_H))
    for index, candidate in enumerate(candidates):
        panel = Image.new("RGB", (tile_w, tile_h), PREVIEW_BG)
        cutout = Image.fromarray(candidate["rgba"], "RGBA").crop((x0, y0, x1, y1)).resize(
            (tile_w, tile_h), Image.Resampling.LANCZOS)
        panel.paste(cutout, (0, 0), cutout)
        canvas.paste(panel, (tile_w * (index + 1), LABEL_H))
    draw = ImageDraw.Draw(canvas)
    font_size = 12 if tile_w >= 160 else 10
    font = load_font(font_size)
    labels = ["original | no mask"] + [
        f"cand-{index} | {candidate['score']:.6f}" for index, candidate in enumerate(candidates)
    ]
    for column, label in enumerate(labels):
        draw.text((tile_w * column + 4, 5), label, fill=TEXT, font=font)
    meta = {"tile_size_wh": [tile_w, tile_h], "scale": round(scale, 9),
            "label_height_px": LABEL_H, "columns": 4,
            "column_order": ["original", "candidate-0", "candidate-1", "candidate-2"],
            "background_rgb": list(PREVIEW_BG), "resampling": "LANCZOS",
            "max_tile_wh": [360, 720], "max_upscale": 2.0}
    return np.array(canvas, dtype=np.uint8).copy(), meta


def make_points(region, rgb):
    x0, y0, x1, y1 = region["box"]
    tile_w, tile_h, scale = fit_tile(region["box"])
    header_h, legend_w, line_h = 34, 272, 15
    entries = ([("POS (green)", POS_COLOR)]
               + [(f"P{i + 1}  ({x}, {y})", POS_COLOR) for i, (x, y) in enumerate(region["pos"])]
               + [("NEG (red)", NEG_COLOR)]
               + [(f"N{i + 1}  ({x}, {y})", NEG_COLOR) for i, (x, y) in enumerate(region["neg"])])
    body_h = max(tile_h, 8 + line_h * len(entries) + 6)
    canvas = Image.new("RGB", (tile_w + 12 + legend_w + 8, header_h + body_h), POINTS_BG)
    crop = Image.fromarray(rgb, "RGB").crop((x0, y0, x1, y1)).resize(
        (tile_w, tile_h), Image.Resampling.NEAREST)
    canvas.paste(crop, (0, header_h))
    draw = ImageDraw.Draw(canvas)
    head_font, legend_font, tag_font = load_font(13), load_font(12), load_font(10)
    draw.text((5, 3), f"{region['name']} | box=[{x0},{y0},{x1},{y1}]", fill=TEXT, font=head_font)
    draw.text((5, 18), "green=POS, red=NEG | legend coordinates are original pixels",
              fill=TEXT, font=load_font(10))
    draw.rectangle((0, header_h, tile_w - 1, header_h + tile_h - 1), outline=(80, 80, 80))
    sx, sy = tile_w / (x1 - x0), tile_h / (y1 - y0)
    for prefix, points, color in (("P", region["pos"], POS_COLOR),
                                  ("N", region["neg"], NEG_COLOR)):
        for index, (x, y) in enumerate(points):
            cx = min(tile_w - 1, max(0, int(round((x - x0) * sx))))
            cy = header_h + min(tile_h - 1, max(0, int(round((y - y0) * sy))))
            arm = 6
            draw.line((cx - arm, cy, cx + arm, cy), fill=(0, 0, 0), width=5)
            draw.line((cx, cy - arm, cx, cy + arm), fill=(0, 0, 0), width=5)
            draw.line((cx - arm, cy, cx + arm, cy), fill=color, width=2)
            draw.line((cx, cy - arm, cx, cy + arm), fill=color, width=2)
            lx = min(max(2, cx + 7), tile_w - 24)
            ly = min(max(header_h + 2, cy - 16), header_h + tile_h - 14)
            draw.text((lx, ly), f"{prefix}{index + 1}", fill=color, font=tag_font,
                      stroke_width=2, stroke_fill=(0, 0, 0))
    legend_x = tile_w + 12
    for row, (text, color) in enumerate(entries):
        draw.text((legend_x, header_h + 8 + row * line_h), text, fill=color, font=legend_font)
    meta = {"tile_size_wh": [tile_w, tile_h], "scale": round(scale, 9),
            "size_wh": list(canvas.size), "header_height_px": header_h,
            "legend_width_px": legend_w, "background_rgb": list(POINTS_BG),
            "resampling": "NEAREST", "pos_color_rgb": list(POS_COLOR),
            "neg_color_rgb": list(NEG_COLOR),
            "original_coordinates_in_legend": [f"P{i + 1}/{p}" for i, p in enumerate(region["pos"])]
            + [f"N{i + 1}/{p}" for i, p in enumerate(region["neg"])]}
    return np.array(canvas, dtype=np.uint8).copy(), meta


def main(argv=None):
    argparse.ArgumentParser(
        description="Fixed, non-destructive six-region SAM diagnostics; no output/overwrite options."
    ).parse_args(argv)
    run_start = time.perf_counter()
    ensure_new_output_dirs()
    validate_regions()
    for path in (REF, CKPT, ORIGINAL):
        need(path.is_file() and not path.is_symlink(), f"Missing or symlinked fixed input: {path}")

    source_sha_before = digest(REF)
    model_sha_before = digest(CKPT)
    original_sha_before = digest(ORIGINAL)
    need(CKPT.stat().st_size == MODEL_BYTES, f"Checkpoint byte size mismatch: {CKPT.stat().st_size}")
    need(model_sha_before == MODEL_SHA, f"Checkpoint SHA256 mismatch: {model_sha_before}")
    sam_path = Path(segment_anything.__file__).resolve()
    need(sam_path.is_relative_to(SAM_DEPS.resolve()),
         f"segment_anything not imported from qa/sam-deps: {sam_path}")

    with Image.open(REF) as image:
        need((image.format, image.mode, image.size) == ("PNG", "RGBA", SIZE),
             f"Expected PNG RGBA {SIZE}: {image.format} {image.mode} {image.size}")
        source = np.array(image, dtype=np.uint8).copy()
    need(np.all(source[..., 3] == 255), "Source alpha is not uniformly 255")
    rgb = np.ascontiguousarray(source[..., :3], dtype=np.uint8)

    original, original_srgb, original_gamma = load_original(ORIGINAL)
    ox0, oy0, ox1, oy1 = ORIGINAL_CROP
    need((ox1 - ox0, oy1 - oy0) == SIZE, "Original crop size mismatch")
    original_crop = original[oy0:oy1, ox0:ox1]
    crop_matches = bool(original_crop.tobytes() == source.tobytes())
    need(crop_matches, "Original screenshot crop RGBA is not byte-identical to 072")
    gamma_100000 = int(round(original_gamma * 100000))

    load_start = time.perf_counter()
    model = load_model()
    model_load_seconds = time.perf_counter() - load_start
    predictor = SamPredictor(model)
    inference_start = time.perf_counter()
    results = []
    with torch.inference_mode():
        set_start = time.perf_counter()
        predictor.set_image(rgb, image_format="RGB")
        set_image_seconds = time.perf_counter() - set_start
        for region in REGIONS:
            x0, y0, x1, y1 = region["box"]
            box_mask = np.zeros((SIZE[1], SIZE[0]), dtype=bool)
            box_mask[y0:y1, x0:x1] = True
            points = region["pos"] + region["neg"]
            labels = np.asarray([1] * len(region["pos"]) + [0] * len(region["neg"]), dtype=np.int32)
            coords = np.asarray(points, dtype=np.float32)
            box = np.asarray(region["box"], dtype=np.float32)[None, :]
            predict_start = time.perf_counter()
            masks, scores, _low_res = predictor.predict(
                point_coords=coords, point_labels=labels, box=box, multimask_output=True)
            predict_seconds = time.perf_counter() - predict_start
            masks, scores = np.asarray(masks), np.asarray(scores)
            need(masks.shape == (3, SIZE[1], SIZE[0]), f"Bad masks for {region['name']}: {masks.shape}")
            need(scores.shape == (3,) and np.all(np.isfinite(scores)),
                 f"Bad scores for {region['name']}: {scores}")
            candidates = []
            for index in range(3):
                raw_bool = (masks[index] > 0) & box_mask
                raw = raw_bool.astype(np.uint8) * np.uint8(255)
                alpha = raw.copy()
                rgba = np.dstack([rgb, alpha]).astype(np.uint8)
                candidates.append({"index": index, "score": float(scores[index]),
                                   "raw_bool": raw_bool, "raw": raw, "alpha": alpha, "rgba": rgba})
            contact, contact_meta = make_contact(region, rgb, candidates)
            points_image, points_meta = make_points(region, rgb)
            results.append({"region": region, "labels": labels, "box_mask": box_mask,
                            "scores": [float(v) for v in scores],
                            "predict_seconds": predict_seconds,
                            "candidates": candidates, "contact": contact,
                            "contact_meta": contact_meta, "points": points_image,
                            "points_meta": points_meta})
    inference_seconds = time.perf_counter() - inference_start
    need(len(results) == 6 and all(len(r["candidates"]) == 3 for r in results),
         "Incomplete six-region/three-candidate result set")

    for path in (OUT_ART, OUT_QA):
        need(not path.exists() and not path.is_symlink(), f"Output appeared during inference: {path}")
    OUT_ART.mkdir(parents=True, exist_ok=False)
    OUT_QA.mkdir(parents=True, exist_ok=False)
    save_start = time.perf_counter()
    for result in results:
        name = result["region"]["name"]
        for candidate in result["candidates"]:
            index = candidate["index"]
            candidate["rgba_path"] = OUT_ART / f"{name}-candidate-{index}.png"
            candidate["raw_path"] = OUT_QA / f"{name}-raw-{index}.png"
            Image.fromarray(candidate["rgba"], "RGBA").save(
                candidate["rgba_path"], format="PNG", optimize=True,
                pnginfo=png_color_info(original_srgb, gamma_100000))
            Image.fromarray(candidate["raw"], "L").save(
                candidate["raw_path"], format="PNG", optimize=True)
        result["contact_path"] = OUT_QA / f"{name}-contact.png"
        result["points_path"] = OUT_QA / f"{name}-points.png"
        Image.fromarray(result["contact"], "RGB").save(
            result["contact_path"], format="PNG", optimize=True,
            pnginfo=png_color_info(original_srgb, gamma_100000))
        Image.fromarray(result["points"], "RGB").save(
            result["points_path"], format="PNG", optimize=True,
            pnginfo=png_color_info(original_srgb, gamma_100000))
    save_seconds = time.perf_counter() - save_start

    readback_start = time.perf_counter()
    region_reports, rgba_outputs, raw_outputs, contact_outputs, points_outputs = [], [], [], [], []
    all_rgba_exact = True
    all_raw_exact = True
    all_rgb_source = True
    all_alpha_outside_zero = True
    all_preview_exact = True
    all_metadata_copied = True
    all_rgba_metadata = True
    for result in results:
        region = result["region"]
        name = region["name"]
        candidate_reports = []
        for candidate in result["candidates"]:
            index = candidate["index"]
            with Image.open(candidate["rgba_path"]) as image:
                need((image.format, image.mode, image.size) == ("PNG", "RGBA", SIZE),
                     f"RGBA readback shape/mode: {candidate['rgba_path']}")
                rgba_meta_ok = color_info_matches(image, original_srgb, gamma_100000)
                rgba_png_srgb = image.info.get("srgb")
                rgba_png_gamma = image.info.get("gamma")
                saved_rgba = np.array(image, dtype=np.uint8).copy()
            with Image.open(candidate["raw_path"]) as image:
                need((image.format, image.mode, image.size) == ("PNG", "L", SIZE),
                     f"Raw mask readback shape/mode: {candidate['raw_path']}")
                saved_raw = np.array(image, dtype=np.uint8).copy()
            rgb_ok = bool(np.array_equal(saved_rgba[..., :3], rgb))
            alpha_ok = bool(np.array_equal(saved_rgba[..., 3], candidate["alpha"]))
            raw_ok = bool(np.array_equal(saved_raw, candidate["raw"]))
            outside_ok = bool(not np.any(saved_rgba[..., 3][~result["box_mask"]])
                              and not np.any(saved_raw[~result["box_mask"]]))
            stats = alpha_stats(candidate["raw_bool"], candidate["alpha"], result["box_mask"])
            candidate_sha = digest(candidate["rgba_path"])
            raw_sha = digest(candidate["raw_path"])
            all_rgba_exact &= alpha_ok
            all_raw_exact &= raw_ok
            all_rgb_source &= rgb_ok
            all_alpha_outside_zero &= outside_ok
            all_metadata_copied &= rgba_meta_ok
            all_rgba_metadata &= rgba_meta_ok
            need(rgb_ok and alpha_ok and raw_ok and outside_ok and rgba_meta_ok,
                 f"RGBA/raw readback or metadata failed: {name} candidate {index}")
            candidate_reports.append({
                "index": index, "predicted_score": candidate["score"],
                "rgba_path": rel(candidate["rgba_path"]), "rgba_sha256": candidate_sha,
                "rgba_mode": "RGBA", "rgba_size": list(SIZE),
                "raw_mask_path": rel(candidate["raw_path"]), "raw_mask_sha256": raw_sha,
                "raw_mask_mode": "L", "raw_mask_size": list(SIZE),
                "raw_mask_png_color_declarations": None,
                "mask_pixel_counts": stats, "rgba_readback": {
                    "rgb_byte_identical_to_source": rgb_ok, "alpha_equals_raw_mask": alpha_ok,
                    "outside_box_alpha_all_zero": outside_ok, "raw_mask_identical": raw_ok,
                    "png_srgb": rgba_png_srgb, "png_gamma": rgba_png_gamma},
            })
            rgba_outputs.append({"region": name, "index": index,
                                 "path": rel(candidate["rgba_path"]), "sha256": candidate_sha})
            raw_outputs.append({"region": name, "index": index,
                                "path": rel(candidate["raw_path"]), "sha256": raw_sha})

        with Image.open(result["contact_path"]) as image:
            need((image.format, image.mode, image.size)
                 == ("PNG", "RGB", (result["contact"].shape[1], result["contact"].shape[0])),
                 f"Contact readback shape/mode: {result['contact_path']}")
            contact_meta_ok = color_info_matches(image, original_srgb, gamma_100000)
            saved_contact = np.array(image, dtype=np.uint8).copy()
        contact_ok = bool(np.array_equal(saved_contact, result["contact"]))
        contact_sha = digest(result["contact_path"])
        with Image.open(result["points_path"]) as image:
            need((image.format, image.mode, image.size)
                 == ("PNG", "RGB", (result["points"].shape[1], result["points"].shape[0])),
                 f"Points readback shape/mode: {result['points_path']}")
            points_meta_ok = color_info_matches(image, original_srgb, gamma_100000)
            saved_points = np.array(image, dtype=np.uint8).copy()
        points_ok = bool(np.array_equal(saved_points, result["points"]))
        points_sha = digest(result["points_path"])
        all_preview_exact &= contact_ok and points_ok
        all_metadata_copied &= contact_meta_ok and points_meta_ok
        need(contact_ok and points_ok and contact_meta_ok and points_meta_ok,
             f"Preview readback failed: {name}")
        result["contact_meta"]["size_wh"] = list(result["contact"].shape[1::-1])
        result["contact_meta"]["png_color_declarations"] = {
            "srgb": original_srgb, "gamma": original_gamma, "gama_100000": gamma_100000}
        result["points_meta"]["png_color_declarations"] = {
            "srgb": original_srgb, "gamma": original_gamma, "gama_100000": gamma_100000}
        result["contact_sha"] = contact_sha
        result["points_sha"] = points_sha
        contact_outputs.append({"region": name, "path": rel(result["contact_path"]),
                                "sha256": contact_sha, "size": result["contact_meta"]["size_wh"]})
        points_outputs.append({"region": name, "path": rel(result["points_path"]),
                               "sha256": points_sha, "size": result["points_meta"]["size_wh"]})
        region_reports.append({
            "name": name, "box_xyxy_half_open": list(region["box"]),
            "box_size_wh": [region["box"][2] - region["box"][0],
                            region["box"][3] - region["box"][1]],
            "positive_points_xy": [list(p) for p in region["pos"]],
            "negative_points_xy": [list(p) for p in region["neg"]],
            "point_labels_1_fg_0_bg": result["labels"].tolist(),
            "all_points_inside_box_and_image": True,
            "predicted_scores_three": result["scores"],
            "predicted_score_note": "Model confidence scores only; not visual acceptance evidence.",
            "mask_pixel_counts": [c["mask_pixel_counts"]["foreground_pixels"]
                                  for c in candidate_reports],
            "predict_seconds": round(result["predict_seconds"], 6),
            "candidates": candidate_reports,
            "previews": {
                "contact_path": rel(result["contact_path"]), "contact_sha256": contact_sha,
                "contact_size_wh": result["contact_meta"]["size_wh"],
                "contact": result["contact_meta"],
                "points_path": rel(result["points_path"]), "points_sha256": points_sha,
                "points_size_wh": result["points_meta"]["size_wh"], "points": result["points_meta"],
            },
            "candidate_selection": "none; all three preserved in SAM multimask order 0,1,2",
        })
    readback_seconds = time.perf_counter() - readback_start

    source_sha_after = digest(REF)
    model_sha_after = digest(CKPT)
    original_sha_after = digest(ORIGINAL)
    need(source_sha_before == source_sha_after, "Source changed during the run")
    need(model_sha_before == model_sha_after, "Checkpoint changed during the run")
    need(original_sha_before == original_sha_after, "Original screenshot changed during the run")
    need(len(rgba_outputs) == 18 and len(raw_outputs) == 18
         and len(contact_outputs) == 6 and len(points_outputs) == 6, "Unexpected output count")

    try:
        sam_version = package_version("segment_anything")
    except PackageNotFoundError:
        sam_version = "unknown"
    elapsed_seconds = time.perf_counter() - run_start
    report = {
        "schema": SCHEMA, "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "static_single_image": True, "visual_quality_verified": False,
        "runtime_asset": False, "is_animation": False, "diagnostic_only": True,
        "elapsed_seconds": round(elapsed_seconds, 6),
        "fixed_paths": {
            "source_rgba": rel(REF), "checkpoint": rel(CKPT),
            "original_screenshot_read_only": path_label(ORIGINAL),
            "output_art_dir": rel(OUT_ART), "output_qa_dir": rel(OUT_QA),
            "rgba_pattern": rel(OUT_ART) + "/<region>-candidate-<0|1|2>.png",
            "raw_mask_pattern": rel(OUT_QA) + "/<region>-raw-<0|1|2>.png",
            "contact_pattern": rel(OUT_QA) + "/<region>-contact.png",
            "points_pattern": rel(OUT_QA) + "/<region>-points.png",
            "report": rel(REPORT),
        },
        "source": {
            "path": rel(REF), "format": "PNG", "mode": "RGBA", "size": list(SIZE),
            "bytes": REF.stat().st_size, "alpha_all_255": True,
            "sha256_before": source_sha_before, "sha256_after": source_sha_after,
            "unchanged": source_sha_before == source_sha_after,
        },
        "original_source": {
            "path": path_label(ORIGINAL), "format": "PNG", "size": list(ORIGINAL_SIZE),
            "bytes": ORIGINAL.stat().st_size, "read_only": True,
            "sha256_before": original_sha_before, "sha256_after": original_sha_after,
            "unchanged": original_sha_before == original_sha_after,
            "fixed_crop_xyxy_half_open": list(ORIGINAL_CROP), "crop_size": list(SIZE),
            "crop_rgba_byte_identical_to_072": crop_matches,
            "png_color_declarations": {"srgb": original_srgb, "gamma": original_gamma,
                                       "gama_100000": gamma_100000,
                                       "icc_profile_present": False},
            "d_drive_write_operations": 0,
        },
        "model": {
            "architecture": "vit_b", "path": rel(CKPT), "bytes": CKPT.stat().st_size,
            "sha256_expected": MODEL_SHA, "sha256_before": model_sha_before,
            "sha256_after": model_sha_after, "sha256_matches_expected": model_sha_before == MODEL_SHA,
            "unchanged": model_sha_before == model_sha_after, "official_url": MODEL_URL,
            "repository": SAM_REPO, "commit": SAM_COMMIT, "version": sam_version,
            "imported_from": rel(sam_path), "network_used": False,
            "downloaded_or_modified": False,
            "load": ("sam_model_registry['vit_b'](checkpoint=None); "
                     "torch.load(map_location='cpu', weights_only=True); "
                     "load_state_dict(strict=True); eval(); cpu()"),
        },
        "inference": {
            "device": "cpu", "threads": torch.get_num_threads(),
            "interop_threads": torch.get_num_interop_threads(), "torch_version": torch.__version__,
            "set_image_calls": 1, "predict_calls": len(REGIONS), "multimask_output": True,
            "set_image_input": "original RGB uint8 820x1440x3; source alpha excluded",
            "set_image_seconds": round(set_image_seconds, 6),
            "model_load_seconds": round(model_load_seconds, 6),
            "per_region_predict_seconds": {r["region"]["name"]: round(r["predict_seconds"], 6)
                                            for r in results},
            "mask_prediction_seconds_total": round(sum(r["predict_seconds"] for r in results), 6),
            "inference_seconds": round(inference_seconds, 6),
            "sam_predict": ("set_image(RGB) once; for each region predict(point_coords, "
                            "point_labels, box, multimask_output=True)"),
            "consumed_outputs": ["masks", "predicted scores"],
            "low_res_masks_used": False,
        },
        "predicted_score_note": ("Each score is a SAM predictor confidence/IoU-like model score, "
                                 "not visual acceptance evidence. Scores were not used to select, "
                                 "rank, threshold, or modify any mask."),
        "method": {
            "model_role": ("segmentation masks only; candidate RGB is copied from the source, "
                           "with no redraw, recolor, warp, inpaint, or face swap"),
            "region_names": "Human labels only; the model does not understand clothing/semantic names.",
            "raw_mask": "full-size 820x1440 L; SAM mask >0 is ANDed with the half-open region box; outside box=0",
            "alpha": "raw boolean x 255; no feathering, blur, threshold rescale, or shape edit; outside box=0",
            "rgb": "full-size candidate RGBA uses source RGB byte-for-byte, including pixels outside the region",
            "box_clamp": "pixels x0<=x<x1, y0<=y<y1; SAM output outside the box is discarded",
            "preview_contact": ("four equal-size columns: original crop then candidate-0/1/2 composited on "
                                "light gray; aspect-preserving fit within 360x720, max 2x upscale, 24px top labels; PNG"),
            "preview_points": ("original crop with POS green and NEG red crosses, marker tags, and a legend "
                               "listing original-image coordinates; NEAREST scale only"),
            "selection": "none; all three multimask outputs retained in model order 0,1,2",
            "no_redraw_recolor_warp_inpaint_face_swap": True,
            "png_color_metadata": ("original sRGB/gAMA copied to RGBA candidates and RGB previews; "
                                   "raw L masks intentionally carry no color declarations"),
            "merged_runtime_atlas": False, "existing_candidates_modified": False,
        },
        "selection": {"automatic_best_selection": False, "selected_candidate": None,
                      "preserved_candidates_per_region": 3,
                      "candidate_order": "SAM multimask output order 0, 1, 2"},
        "regions": region_reports,
        "outputs": {
            "rgba_candidates": rgba_outputs, "raw_masks": raw_outputs,
            "contacts": contact_outputs, "points": points_outputs,
            "image_file_count": 18 + 18 + 6 + 6,
            "report": {"path": rel(REPORT), "schema": SCHEMA},
        },
        "checks": {
            "both_output_directories_were_new": True,
            "source_sha_unchanged": source_sha_before == source_sha_after,
            "original_sha_unchanged": original_sha_before == original_sha_after,
            "checkpoint_sha_matches_expected_and_unchanged": (model_sha_before == MODEL_SHA
                                                               and model_sha_before == model_sha_after),
            "original_crop_rgba_byte_identical_to_072": crop_matches,
            "single_set_image_call": True, "six_predict_calls": len(REGIONS) == 6,
            "three_candidates_preserved_per_region": all(len(r["candidates"]) == 3 for r in results),
            "all_18_rgba_rgb_byte_identical_to_source": all_rgb_source,
            "all_18_rgba_alpha_equals_raw_bool_times_255": all_rgba_exact,
            "all_18_raw_masks_full_size_and_identical": all_raw_exact,
            "all_18_rgba_and_raw_masks_zero_outside_box": all_alpha_outside_zero,
            "all_12_previews_readback_exact": all_preview_exact,
            "contact_columns_four_equal_size": True, "contact_label_height_24px": True,
            "contact_fit_within_360x720": True, "contact_max_upscale_2x": True,
            "points_pos_green_neg_red_with_original_coordinates": True,
            "sRGB_gAMA_copied_to_candidates_contacts_points": all_metadata_copied,
            "all_18_rgba_png_srgb_gama_verified": all_rgba_metadata,
            "all_outputs_read_back": True, "no_automatic_selection": True,
            "no_model_scores_used_for_selection_or_mask_edits": True,
            "no_d_drive_write": True, "original_screenshot_read_only": True,
            "no_network_or_download": True, "no_new_software_installed": True,
            "existing_candidates_or_runtime_assets_modified": False,
            "visual_quality_verified": False,
        },
        "timings": {
            "model_load_seconds": round(model_load_seconds, 6),
            "inference_seconds": round(inference_seconds, 6),
            "save_seconds": round(save_seconds, 6),
            "readback_seconds": round(readback_seconds, 6),
            "total_seconds": round(elapsed_seconds, 6),
        },
        "software": {"python": sys.version.split()[0], "numpy": np.__version__,
                     "pillow": pillow_version, "torch": torch.__version__,
                     "segment_anything": sam_version},
        "review_required": [
            "逐区域查看三张 RGBA、三张 raw L、contact 与 points；分数不是视觉验收。",
            "重点核对 head_ink_* 细墨饰、shoulder_* 肩饰、coat_* 边缘与负点是否落在背景。",
            "points 图用于检查原图坐标是否落错；区域名称只是人工标签，模型不理解“衣服”语义。",
            "三种候选全部保留，不按 score 选图，不合并运行图集，不改变现有候选。",
        ],
        "limitations": [
            "SAM ViT-B 只给局部分割候选，不能保证细角墨饰、肩饰、衣摆和手部结构正确。",
            "score 是模型预测置信度，不是独立视觉质量指标，不能作为验收证据。",
            "alpha 为原始 boolean x 255，无羽化；细小、半透明或深色细节可能缺失或边缘粗糙。",
            "contact/points 仅为缩放与合成预览，不修改全尺寸 RGBA/raw mask，也不代表 runtime 资产。",
            "visual_quality_verified=false; runtime_asset=false; 仍需主协作者目视验收。",
        ],
        "non_destructive": {
            "fixed_inputs_only": True, "fixed_new_output_directories_only": True,
            "no_overwrite_parameter": True, "new_candidate_rgb_generated": False,
            "source_rgb_copied_byte_for_byte": True,
            "redraw_recolor_warp_inpaint_face_swap": False,
            "merged_runtime_atlas_generated": False, "existing_candidates_modified": False,
            "runtime_android_release_configs_modified": False, "network_used": False,
            "new_software_installed": False, "d_drive_written": False,
            "source_model_original_or_existing_outputs_modified": False,
        },
    }
    REPORT.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    report_readback = json.loads(REPORT.read_text(encoding="utf-8"))
    need(report_readback.get("schema") == SCHEMA
         and report_readback.get("visual_quality_verified") is False
         and report_readback.get("runtime_asset") is False
         and len(report_readback.get("regions", [])) == 6,
         "Report readback/schema mismatch")
    print(json.dumps({
        "rgba_candidates": len(rgba_outputs), "raw_masks": len(raw_outputs),
        "contacts": [rel(p) for p in (r["contact_path"] for r in results)],
        "points": [rel(p) for p in (r["points_path"] for r in results)],
        "report": rel(REPORT), "predicted_scores": {r["region"]["name"]: r["scores"] for r in results},
        "elapsed_seconds": round(elapsed_seconds, 6), "report_readback_verified": True,
        "visual_quality_verified": False, "runtime_asset": False,
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

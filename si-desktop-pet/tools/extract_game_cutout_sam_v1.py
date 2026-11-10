#!/usr/bin/env python3
"""SAM ViT-B masks for qa/si-body-reference-072.png; RGB is read-only.

Writes three full-size RGBA candidates and three raw L masks, two contact sheets,
and a report into two new fixed directories. The local SAM package/checkpoint load
from disk with no network. The model only produces segmentation masks: no
candidate RGB is generated, resized, recolored, inpainted, or face-swapped. All
three multimask results are kept in model order; scores are model predicted
scores, not visual quality proof.
"""
from __future__ import annotations

import argparse, hashlib, json, sys, time
from datetime import datetime, timezone
from importlib.metadata import PackageNotFoundError, version as package_version
from pathlib import Path

sys.dont_write_bytecode = True  # keep qa/sam-deps and the environment unchanged

import numpy as np
from PIL import Image, ImageDraw, ImageFilter, ImageFont, __version__ as pillow_version

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
OUT_ART = ROOT / "art-source" / "v1.3" / "si-game-sam-v1"
OUT_QA = ROOT / "qa" / "si-game-sam-v1"
REPORT = OUT_QA / "report.json"
SIZE, BOX = (820, 1440), [55, 80, 770, 1400]
POS = [(425, 300), (420, 600), (334, 329), (509, 330), (368, 154), (377, 181),
       (469, 125), (480, 171), (294, 429), (559, 443), (340, 414), (515, 411),
       (270, 630), (213, 737), (580, 630), (667, 725), (180, 900), (655, 900),
       (350, 1000), (490, 1000), (330, 1355), (512, 1355), (433, 1000),
       (568, 1098), (310, 315)]
NEG = [(25, 25), (795, 25), (25, 1415), (795, 1415), (401, 1200), (584, 1300),
       (260, 1100), (517, 595), (750, 1350), (200, 1375)]
MODEL_BYTES, MODEL_SHA = 375_042_383, "ec2df62732614e57411cdcf32a23ffdf28910380d03139ee0f4fcbe91eb8c912"
MODEL_URL = "https://dl.fbaipublicfiles.com/segment_anything/sam_vit_b_01ec64.pth"
SAM_REPO, SAM_COMMIT = "Meta/facebookresearch/segment-anything", "dca509fe793f601edb92606367a655c15ac00fdf"
CROP, DESKTOP, DESKTOP_BG = (270, 70, 610, 580), (384, 416), (232, 232, 232)
FIT_MAX, BASELINE, LABEL_H, SCHEMA = (368, 404), 410, 26, "si-game-sam-v1-report/v1"


def digest(path):
    h = hashlib.sha256()
    with path.open("rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def rel(path):
    return path.resolve().relative_to(ROOT.resolve()).as_posix()


def need(ok, message):
    if not ok:
        raise SystemExit(message)


def alpha_info(raw, alpha):
    def box(mask):
        ys, xs = np.nonzero(mask)
        return [int(xs.min()), int(ys.min()), int(xs.max()) + 1, int(ys.max()) + 1] if xs.size else None
    corners = [int(alpha[y, x]) for y, x in ((0, 0), (0, -1), (-1, 0), (-1, -1))]
    return {"raw_mask_bbox_xyxy_half_open": box(raw), "alpha_bbox_xyxy_half_open": box(alpha),
            "raw_foreground_pixels": int((raw == 255).sum()),
            "four_corners_alpha": dict(zip(("top_left", "top_right", "bottom_left", "bottom_right"), corners)),
            "all_four_corners_transparent": not any(corners), "alpha_min": int(alpha.min()),
            "alpha_max": int(alpha.max()), "alpha_partial_pixels": int(((alpha > 0) & (alpha < 255)).sum())}


def load_model():
    model = sam_model_registry["vit_b"](checkpoint=None)
    state = torch.load(CKPT, map_location="cpu", weights_only=True)
    need(isinstance(state, dict), "SAM checkpoint did not load as a state dict")
    model.load_state_dict(state, strict=True)
    model.eval().cpu()
    need(all(p.device.type == "cpu" for p in model.parameters()), "SAM model is not on CPU")
    return model


def desktop_tile(cutout):
    image = Image.fromarray(cutout, "RGBA")
    bbox = image.getchannel("A").getbbox()
    canvas = Image.new("RGB", DESKTOP, DESKTOP_BG)
    if bbox is None:
        return canvas, None, None
    content = image.crop(bbox)
    scale = min(FIT_MAX[0] / content.width, FIT_MAX[1] / content.height)
    size = (min(FIT_MAX[0], max(1, round(content.width * scale))),
            min(FIT_MAX[1], max(1, round(content.height * scale))))
    fitted = content.resize(size, Image.Resampling.LANCZOS)
    offset = ((DESKTOP[0] - fitted.width) // 2, BASELINE - fitted.height)
    need(offset[0] >= 0 and offset[1] >= 0 and offset[1] + fitted.height == BASELINE,
         f"Desktop placement failed: size={size}, offset={offset}")
    canvas.paste(fitted, offset, fitted)
    return canvas, list(size), list(offset)


def main(argv=None):
    argparse.ArgumentParser(description="Non-destructive SAM ViT-B masks for 072.").parse_args(argv)
    for path in (OUT_ART, OUT_QA):
        need(not path.exists() and not path.is_symlink(), f"Refusing existing output directory: {path}")
    for path in (REF, CKPT):
        need(path.is_file() and not path.is_symlink(), f"Missing or symlinked fixed input: {path}")
    need(CKPT.stat().st_size == MODEL_BYTES, f"Checkpoint byte size mismatch: {CKPT.stat().st_size}")
    sam_path = Path(segment_anything.__file__).resolve()
    need(sam_path.is_relative_to(SAM_DEPS.resolve()), f"segment_anything not imported from qa/sam-deps: {sam_path}")

    w, h = SIZE
    for x, y in POS + NEG:
        need(0 <= x < w and 0 <= y < h, f"Prompt point out of bounds: ({x}, {y})")
    x0, y0, x1, y1 = BOX
    need(0 <= x0 < x1 <= w and 0 <= y0 < y1 <= h, f"Invalid box: {BOX}")
    source_sha_before = digest(REF)
    with Image.open(REF) as image:
        need((image.mode, image.size) == ("RGBA", SIZE), f"Expected RGBA 820x1440, got {image.mode} {image.size}")
        source = np.array(image, dtype=np.uint8).copy()
    need(np.all(source[..., 3] == 255), "Source alpha is not uniformly 255")
    model_sha_before = digest(CKPT)
    need(model_sha_before == MODEL_SHA, f"Checkpoint SHA256 mismatch: {model_sha_before}")

    model = load_model()
    predictor = SamPredictor(model)
    points = np.asarray(POS + NEG, dtype=np.float32)
    labels = np.asarray([1] * len(POS) + [0] * len(NEG), dtype=np.int32)
    box = np.asarray(BOX, dtype=np.float32)[None, :]
    rgb = np.ascontiguousarray(source[..., :3], dtype=np.uint8)
    t0 = time.perf_counter()
    with torch.inference_mode():
        predictor.set_image(rgb, image_format="RGB")
        t1 = time.perf_counter()
        masks, scores, _low_res_masks = predictor.predict(
            point_coords=points, point_labels=labels, box=box, multimask_output=True)
        t2 = time.perf_counter()
    masks, scores = np.asarray(masks), np.asarray(scores)
    need(masks.shape == (3, h, w), f"Expected 3 full-size masks, got {masks.shape}")
    need(scores.shape == (3,) and np.all(np.isfinite(scores)), f"Unexpected predicted scores: {scores}")

    raws, alphas, cutouts = [], [], []
    for index in range(3):
        raw = (masks[index] > 0).astype(np.uint8) * np.uint8(255)
        alpha = np.array(Image.fromarray(raw, "L").filter(ImageFilter.GaussianBlur(0.4)), dtype=np.uint8).copy()
        cutout = source.copy()
        cutout[..., 3] = alpha
        raws.append(raw); alphas.append(alpha); cutouts.append(cutout)

    need(not OUT_ART.exists() and not OUT_QA.exists(), "An output directory appeared during inference")
    OUT_ART.mkdir(parents=True, exist_ok=False); OUT_QA.mkdir(parents=True, exist_ok=False)
    candidates_paths, raw_paths, readbacks = [], [], []
    for index, (raw, alpha, cutout) in enumerate(zip(raws, alphas, cutouts)):
        candidate, raw_path = OUT_ART / f"candidate-{index}.png", OUT_QA / f"raw-mask-{index}.png"
        Image.fromarray(cutout, "RGBA").save(candidate, format="PNG", optimize=True)
        Image.fromarray(raw, "L").save(raw_path, format="PNG", optimize=True)
        with Image.open(candidate) as image:
            need((image.mode, image.size) == ("RGBA", SIZE), f"Candidate readback mismatch: {candidate}")
            saved = np.array(image, dtype=np.uint8).copy()
        with Image.open(raw_path) as image:
            need((image.mode, image.size) == ("L", SIZE), f"Raw mask readback mismatch: {raw_path}")
            saved_raw = np.array(image, dtype=np.uint8).copy()
        readback = {"rgb_identical": bool(np.array_equal(saved[..., :3], source[..., :3])),
                    "alpha_identical": bool(np.array_equal(saved[..., 3], alpha)),
                    "raw_mask_identical": bool(np.array_equal(saved_raw, raw))}
        need(all(readback.values()), f"Candidate/raw readback differs at index {index}")
        candidates_paths.append(candidate); raw_paths.append(raw_path); readbacks.append(readback)

    desktop, desktop_meta = Image.new("RGB", (DESKTOP[0] * 3, DESKTOP[1]), DESKTOP_BG), []
    for index, cutout in enumerate(cutouts):
        tile, content_size, offset = desktop_tile(cutout)
        desktop.paste(tile, (DESKTOP[0] * index, 0))
        desktop_meta.append({"content_size": content_size, "offset_xy": offset,
                             "bottom_y": None if offset is None else offset[1] + content_size[1]})
    desktop_path = OUT_QA / "desktop-contact.jpg"
    desktop.save(desktop_path, format="JPEG", quality=95, subsampling=0, optimize=True)
    with Image.open(desktop_path) as image:
        need((image.mode, image.size) == ("RGB", (DESKTOP[0] * 3, DESKTOP[1])), "desktop-contact.jpg readback mismatch")

    crop_w, crop_h = CROP[2] - CROP[0], CROP[3] - CROP[1]
    upper = Image.new("RGB", (crop_w * 4, crop_h + LABEL_H), (245, 245, 245))
    draw, font = ImageDraw.Draw(upper), ImageFont.load_default()
    upper.paste(Image.fromarray(rgb, "RGB").crop(CROP), (0, LABEL_H))
    draw.text((6, 7), "original | no mask", fill=(30, 30, 30), font=font)
    for index, cutout in enumerate(cutouts):
        panel, region = Image.new("RGB", (crop_w, crop_h), DESKTOP_BG), Image.fromarray(cutout, "RGBA").crop(CROP)
        panel.paste(region, (0, 0), region)
        x = crop_w * (index + 1)
        upper.paste(panel, (x, LABEL_H))
        draw.text((x + 6, 7), f"candidate-{index} | model predicted score {float(scores[index]):.6f}",
                  fill=(30, 30, 30), font=font)
    upper_path = OUT_QA / "upper-contact.jpg"
    upper.save(upper_path, format="JPEG", quality=95, subsampling=0, optimize=True)
    with Image.open(upper_path) as image:
        need((image.mode, image.size) == ("RGB", upper.size), "upper-contact.jpg readback mismatch")

    source_sha_after, model_sha_after = digest(REF), digest(CKPT)
    need(source_sha_before == source_sha_after, "Source changed during the run")
    need(model_sha_before == model_sha_after, "Checkpoint changed during the run")
    all_paths = candidates_paths + raw_paths + [desktop_path, upper_path]
    shas = {rel(path): digest(path) for path in all_paths}
    try:
        sam_version = package_version("segment_anything")
    except PackageNotFoundError:
        sam_version = "unknown"
    candidates = [{
        "index": index, "candidate_path": rel(candidates_paths[index]), "mode": "RGBA", "size": list(SIZE),
        "candidate_sha256": shas[rel(candidates_paths[index])], "raw_mask_path": rel(raw_paths[index]),
        "raw_mask_mode": "L", "raw_mask_size": list(SIZE),
        "raw_mask_sha256": shas[rel(raw_paths[index])], "predicted_score": float(scores[index]),
        "readback": readbacks[index], "desktop": desktop_meta[index], **alpha_info(raws[index], alphas[index]),
    } for index in range(3)]

    report = {
        "schema": SCHEMA, "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "static_single_image": True, "visual_quality_verified": False,
        "runtime_asset": False, "is_animation": False,
        "source": {"path": rel(REF), "size": list(SIZE), "mode": "RGBA", "alpha_all_255": True,
                   "sha256_before": source_sha_before, "sha256_after": source_sha_after,
                   "unchanged": source_sha_before == source_sha_after, "bytes": REF.stat().st_size},
        "model": {"architecture": "vit_b", "path": rel(CKPT), "bytes": CKPT.stat().st_size,
                  "sha256_expected": MODEL_SHA, "sha256_before": model_sha_before,
                  "sha256_after": model_sha_after, "sha256_matches_expected": model_sha_before == MODEL_SHA,
                  "unchanged": model_sha_before == model_sha_after, "official_url": MODEL_URL,
                  "repository": SAM_REPO, "commit": SAM_COMMIT, "version": sam_version,
                  "imported_from": rel(sam_path), "network_used": False, "downloaded_or_modified": False,
                  "load": "sam_model_registry['vit_b'](checkpoint=None); torch.load(map_location='cpu', weights_only=True); load_state_dict(strict=True); eval(); cpu()"},
        "inference": {"device": "cpu", "threads": torch.get_num_threads(),
                      "interop_threads": torch.get_num_interop_threads(), "torch_version": torch.__version__,
                      "image_embedding_seconds": round(t1 - t0, 6),
                      "mask_prediction_seconds": round(t2 - t1, 6),
                      "cpu_inference_seconds": round(t2 - t0, 6),
                      "sam_predict": "set_image(RGB); predict(point_coords, point_labels, box, multimask_output=True)",
                      "consumed_outputs": ["masks", "predicted scores"], "low_res_masks_used": False},
        "prompts": {"box_xyxy": BOX, "positive_points_xy": [list(p) for p in POS],
                    "negative_points_xy": [list(p) for p in NEG], "labels_1_fg_0_bg": labels.tolist(),
                    "all_prompts": [{"xy": [int(x), int(y)], "label": 1} for x, y in POS] +
                                   [{"xy": [int(x), int(y)], "label": 0} for x, y in NEG]},
        "method": {"model_role": "segmentation masks only; candidate RGB copied, with no recolor, inpainting, or face swap",
                   "raw_mask": "each SAM mask -> 0/255 L PNG at full 820x1440",
                   "alpha": "raw mask -> Pillow GaussianBlur(0.4) edge softening only; no shape edits",
                   "rgb": "candidate RGB copied byte-for-byte from read-only 072, including transparent pixels",
                   "selection": "none; all three multimask outputs preserved in model order"},
        "predicted_score_note": "Scores are SAM predictor mask scores, not verified visual quality; they are not used to select or rank a candidate.",
        "selection": {"automatic_best_selection": False, "preserved_candidate_count": 3,
                      "candidate_order": "SAM multimask output order 0, 1, 2"},
        "candidates": candidates,
        "outputs": {"candidate_pngs": [{"path": rel(p), "sha256": shas[rel(p)]} for p in candidates_paths],
                    "raw_masks": [{"path": rel(p), "sha256": shas[rel(p)]} for p in raw_paths],
                    "desktop_contact": {"path": rel(desktop_path), "sha256": shas[rel(desktop_path)],
                                        "size": [DESKTOP[0] * 3, DESKTOP[1]], "format": "JPEG", "columns": 3,
                                        "column_order": ["candidate-0", "candidate-1", "candidate-2"],
                                        "background_rgb": list(DESKTOP_BG), "fit_max": list(FIT_MAX), "baseline_y": BASELINE},
                    "upper_contact": {"path": rel(upper_path), "sha256": shas[rel(upper_path)],
                                      "size": list(upper.size), "format": "JPEG", "crop": list(CROP),
                                      "layout": ["original", "candidate-0", "candidate-1", "candidate-2"],
                                      "background_rgb": list(DESKTOP_BG), "labels": "English only"},
                    "report": {"path": rel(REPORT)}},
        "checks": {"both_output_directories_were_new": True,
                   "source_sha_unchanged": source_sha_before == source_sha_after,
                   "model_sha_unchanged": model_sha_before == model_sha_after,
                   "model_sha_matches_expected": model_sha_before == MODEL_SHA,
                   "three_candidates_rgba_readback_rgb_and_alpha": all(r["rgb_identical"] and r["alpha_identical"] for r in readbacks),
                   "three_raw_masks_readback": all(r["raw_mask_identical"] for r in readbacks),
                   "four_corners_alpha_recorded": True, "no_automatic_selection": True, "no_candidate_rgb_generated": True},
        "review_required": ["模型只产生分割蒙版，仍需逐张检查细角墨饰、肩饰、尾端和手部；分数不能证明角色合格。",
                            "全尺寸 RGBA、三张 raw mask、desktop-contact.jpg 与 upper-contact.jpg 都必须人工查看；保留全部三种结果，不自动选优。",
                            "本工具不生成、不修复候选 RGB，不换脸，不缩放候选，也不接入 runtime/Android/发布包。"],
        "non_destructive": {"fixed_inputs_only": True, "fixed_new_output_directories_only": True,
                            "new_candidate_rgb_generated": False, "face_swap": False, "recolor_or_inpaint": False,
                            "preview_compositing_only": True, "network_used": False,
                            "runtime_android_release_configs_modified": False, "source_or_model_modified": False},
        "software": {"python": sys.version.split()[0], "numpy": np.__version__, "pillow": pillow_version,
                     "torch": torch.__version__, "segment_anything": sam_version},
        "limitations": ["SAM ViT-B produces candidate segmentation masks only; it cannot guarantee fine horns/ink ornaments, shoulder ornaments, tail ends, or hands.",
                        "Scores are model predicted scores, not independent visual quality measurements; do not use them as acceptance evidence.",
                        "GaussianBlur(0.4) softens the raw edge but is not manual matting; thin, dark, or semi-transparent details may be missing or rough.",
                        "All three candidates remain for comparison; this report does not rank or select one."],
    }
    REPORT.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"candidates": [rel(p) for p in candidates_paths], "raw_masks": [rel(p) for p in raw_paths],
                      "desktop_contact": rel(desktop_path), "upper_contact": rel(upper_path), "report": rel(REPORT),
                      "predicted_scores": [float(s) for s in scores], "visual_quality_verified": False,
                      "runtime_asset": False, "is_animation": False}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

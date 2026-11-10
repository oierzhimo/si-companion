#!/usr/bin/env python3
"""First-pass open-eyes cutout from qa/si-body-reference-072.png (read-only).

Writes five fixed draft outputs; refuses existing files unless --overwrite.
isnet-anime supplies alpha only; all RGB stays from the 072 crop. Static draft,
not an animation/runtime asset, not wired into runtime assets; full-size and
384x416 review still required. No manual polygon, network, model download, or
config change.
"""
from __future__ import annotations

import argparse, hashlib, json, os, sys
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
from PIL import Image, __version__ as pillow_version

ROOT = Path(__file__).resolve().parents[1]
DEPS = ROOT / "qa" / "segmentation-deps"
sys.path.insert(0, str(DEPS))
import cv2  # noqa: E402  (local qa/segmentation-deps copy)
REF = ROOT / "qa" / "si-body-reference-072.png"
PROV = ROOT / "qa" / "si-body-reference-072-provenance.json"
MODEL = DEPS / "isnet-anime.onnx"
OUT_RGBA = ROOT / "art-source" / "v1.3" / "si-game-openeyes-draft-v1.png"
OUT_RAW = ROOT / "qa" / "si-game-openeyes-mask-raw-v1.png"
OUT_PINK = ROOT / "qa" / "si-game-openeyes-draft-v1-pink.jpg"
OUT_DESKTOP = ROOT / "qa" / "si-game-openeyes-draft-v1-desktop.png"
OUT_REPORT = ROOT / "qa" / "si-game-openeyes-draft-v1-report.json"
TARGETS = (OUT_RGBA, OUT_RAW, OUT_PINK, OUT_DESKTOP, OUT_REPORT)
SIZE, MODEL_SIZE, MODEL_MD5 = (820, 1440), (1024, 1024), "6f184e756bb3bd901c8849220a83e38e"
MEAN, PINK, DESKTOP_SIZE = (0.485, 0.456, 0.406), (255, 105, 180), (384, 416)
DESKTOP_BG, QUALITY, CROP = (232, 232, 232), 95, [1830, 300, 2650, 1740]
SCHEMA = "si-game-openeyes-cutout-report/v1"


def digest(path, algorithm="sha256"):
    h = hashlib.new(algorithm)
    with path.open("rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""): h.update(block)
    return h.hexdigest()


def rel(path):
    return path.resolve().relative_to(ROOT.resolve()).as_posix()


def alpha_stats(a):
    ys, xs = np.nonzero(a)
    c = [int(a[0, 0]), int(a[0, -1]), int(a[-1, 0]), int(a[-1, -1])]
    return {"total_pixels": int(a.size), "transparent_pixels": int((a == 0).sum()), "opaque_pixels": int((a == 255).sum()), "foreground_pixels": int(ys.size),
            "alpha_min": int(a.min()), "alpha_max": int(a.max()), "alpha_mean": round(float(a.mean()), 6), "four_corners": dict(zip(("top_left", "top_right", "bottom_left", "bottom_right"), c)),
            "all_four_corners_transparent": not any(c), "foreground_bbox_xyxy": [int(xs.min()), int(ys.min()), int(xs.max()) + 1, int(ys.max()) + 1] if xs.size else None}


def on_color(rgba, color):
    alpha = rgba[..., 3:4].astype(np.float32) / 255.0
    mixed = rgba[..., :3].astype(np.float32) * alpha + np.asarray(color, dtype=np.float32) * (1 - alpha)
    return np.clip(np.rint(mixed), 0, 255).astype(np.uint8)


def desktop(rgba):
    h, w = rgba.shape[:2]; scale = min(DESKTOP_SIZE[0] / w, DESKTOP_SIZE[1] / h)
    tw, th = max(1, round(w * scale)), max(1, round(h * scale))
    content = Image.fromarray(on_color(rgba, DESKTOP_BG)).resize((tw, th), Image.Resampling.LANCZOS)
    canvas = Image.new("RGB", DESKTOP_SIZE, DESKTOP_BG); offset = ((DESKTOP_SIZE[0] - tw) // 2, (DESKTOP_SIZE[1] - th) // 2)
    canvas.paste(content, offset)
    return np.asarray(canvas, dtype=np.uint8).copy(), [tw, th], list(offset)


def save(path, image, **kwargs):
    path.parent.mkdir(parents=True, exist_ok=True); tmp = path.with_name(f".{path.name}.tmp")
    image.save(tmp, **kwargs); os.replace(tmp, path)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0]); parser.add_argument("--overwrite", action="store_true", help="replace only the five fixed outputs")
    args = parser.parse_args(argv)
    for path in (REF, PROV, MODEL):
        if not path.is_file() or path.is_symlink(): raise SystemExit(f"Missing or symlinked fixed input: {path}")
    existing = [rel(path) for path in TARGETS if path.exists()]
    if existing and not args.overwrite: raise SystemExit(f"Refusing to overwrite (use --overwrite): {existing}")

    source_sha, prov_sha = digest(REF), digest(PROV)
    with Image.open(REF) as im:
        if (im.mode, im.size) != ("RGBA", SIZE): raise SystemExit(f"Expected RGBA 820x1440, got {im.mode} {im.size}")
        source = np.array(im, dtype=np.uint8).copy()
    if not np.all(source[..., 3] == 255): raise SystemExit("Source alpha is not uniformly 255")
    provenance = json.loads(PROV.read_text(encoding="utf-8"))
    if provenance.get("size") != list(SIZE) or provenance.get("crop") != CROP: raise SystemExit("072 provenance size/crop mismatch")

    model_md5, model_sha = digest(MODEL, "md5"), digest(MODEL)
    if model_md5 != MODEL_MD5: raise SystemExit(f"Model MD5 mismatch: {model_md5}")
    import onnxruntime as ort
    opts = ort.SessionOptions(); opts.intra_op_num_threads, opts.inter_op_num_threads, opts.log_severity_level = 4, 1, 3
    opts.execution_mode = ort.ExecutionMode.ORT_SEQUENTIAL; opts.graph_optimization_level = ort.GraphOptimizationLevel.ORT_ENABLE_ALL
    session = ort.InferenceSession(str(MODEL), sess_options=opts, providers=["CPUExecutionProvider"])
    inp, out = session.get_inputs()[0], session.get_outputs()[0]

    arr = np.array(Image.fromarray(np.ascontiguousarray(source[..., :3])).resize(MODEL_SIZE, Image.Resampling.LANCZOS))
    arr = arr / max(float(arr.max()), 1e-6); norm = np.zeros((*MODEL_SIZE, 3))
    for channel, mean in enumerate(MEAN): norm[..., channel] = arr[..., channel] - mean
    tensor = np.ascontiguousarray(norm.transpose(2, 0, 1)[None].astype(np.float32))
    raw = session.run([out.name], {inp.name: tensor})[0]
    if raw.ndim != 4 or raw.shape[:2] != (1, 1): raise SystemExit(f"Unexpected model output shape: {raw.shape}")
    pred = raw[:, 0, :, :]; lo, hi = float(pred.min()), float(pred.max())
    if not np.isfinite(lo) or not np.isfinite(hi) or hi - lo < 1e-8: raise SystemExit("Model output is constant or non-finite")
    pred = (pred - lo) / (hi - lo)
    raw_alpha = np.array(Image.fromarray((pred.squeeze() * 255).astype(np.uint8)).resize(SIZE, Image.Resampling.LANCZOS), dtype=np.uint8).copy()

    r, g, b = (source[..., index].astype(np.int16) for index in range(3))
    key = ((r >= 10) & (r <= 48) & (g >= 25) & (g <= 65) & (b >= 40) & (b <= 88) & (b - g >= 8) & (b - g <= 28) & (g - r >= 7) & (g - r <= 28))
    keyed = (raw_alpha < 235) & key; alpha = raw_alpha.copy(); alpha[keyed] = 0
    below = alpha < 80
    alpha = np.where(below, 0, np.clip(np.rint((alpha.astype(np.float32) - 80) * 255 / 175), 0, 255)).astype(np.uint8)
    n, labels, stats, _ = cv2.connectedComponentsWithStats((alpha > 32).astype(np.uint8), connectivity=8)
    if n <= 1: raise SystemExit("No alpha>32 component to keep")
    areas = stats[1:, cv2.CC_STAT_AREA]; keep = 1 + int(np.argmax(areas)); removed = (labels > 0) & (labels != keep)
    comps = sorted([{"area": int(stats[i, cv2.CC_STAT_AREA]), "bbox_xywh": [int(stats[i, c]) for c in (cv2.CC_STAT_LEFT, cv2.CC_STAT_TOP, cv2.CC_STAT_WIDTH, cv2.CC_STAT_HEIGHT)]} for i in range(1, n)], key=lambda item: item["area"], reverse=True)
    kept_area, removed_area = int(stats[keep, cv2.CC_STAT_AREA]), int(removed.sum()); alpha[removed] = 0

    output = source.copy(); output[..., 3] = alpha
    rgb_mismatch = int(np.any(output[..., :3] != source[..., :3], axis=2).sum())
    pink, (desk, content, offset) = on_color(output, PINK), desktop(output)
    save(OUT_RGBA, Image.fromarray(output), format="PNG", optimize=True); save(OUT_RAW, Image.fromarray(raw_alpha), format="PNG", optimize=True)
    save(OUT_PINK, Image.fromarray(pink), format="JPEG", quality=QUALITY, subsampling=0, optimize=True); save(OUT_DESKTOP, Image.fromarray(desk), format="PNG", optimize=True)

    with Image.open(OUT_RGBA) as im:
        if (im.mode, im.size) != ("RGBA", SIZE): raise SystemExit("Saved RGBA mode/size mismatch")
        saved = np.array(im, dtype=np.uint8)
    if not np.array_equal(saved[..., 3], alpha): raise SystemExit("Saved PNG alpha differs from computed alpha")
    saved_mismatch = int(np.any(saved[..., :3] != source[..., :3], axis=2).sum())
    if saved_mismatch: raise SystemExit("Saved PNG RGB differs from the 072 source")
    with Image.open(OUT_RAW) as im:
        if (im.mode, im.size) != ("L", SIZE) or not np.array_equal(np.array(im), raw_alpha): raise SystemExit("Saved raw alpha mismatch")
    with Image.open(OUT_PINK) as im:
        if (im.mode, im.size) != ("RGB", SIZE): raise SystemExit("Pink preview mismatch")
        im.load()
    with Image.open(OUT_DESKTOP) as im:
        if (im.mode, im.size) != ("RGB", DESKTOP_SIZE): raise SystemExit("Desktop preview mismatch")
        desk_saved = np.array(im, dtype=np.uint8)
    if any(not np.array_equal(desk_saved[y, x], DESKTOP_BG) for y, x in ((0, 0), (0, -1), (-1, 0), (-1, -1))): raise SystemExit("Desktop corners not grey")

    source_sha2, prov_sha2, model_md5_2, model_sha2 = digest(REF), digest(PROV), digest(MODEL, "md5"), digest(MODEL)
    if (source_sha2, prov_sha2, model_md5_2, model_sha2) != (source_sha, prov_sha, model_md5, model_sha): raise SystemExit("Fixed input or model changed during the run")
    shas = {path: digest(path) for path in (OUT_RGBA, OUT_RAW, OUT_PINK, OUT_DESKTOP)}

    def entry(path, size, mode, fmt, **extra):
        return {"path": rel(path), "sha256": shas[path], "size": list(size), "mode": mode, "format": fmt, "bytes": path.stat().st_size, **extra}

    report = {
        "schema": SCHEMA, "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "draft_status": {"name": "si-game-openeyes-draft-v1", "not_wired_into_runtime_assets": True, "needs_full_size_visual_review": True, "needs_desktop_size_visual_review": True, "is_animation": False, "note": "第一轮视觉判断用静态草稿；未接入运行素材，不是动画帧/图集，需全尺寸与384x416桌面尺寸复核。"},
        "source": {"path": rel(REF), "size": list(SIZE), "mode": "RGBA", "alpha_all_255": True, "sha256_before": source_sha, "sha256_after": source_sha2, "unchanged": source_sha == source_sha2, "provenance": {"path": rel(PROV), "sha256_before": prov_sha, "sha256_after": prov_sha2, "unchanged": prov_sha == prov_sha2, **provenance}},
        "model": {"path": rel(MODEL), "md5_before": model_md5, "md5_after": model_md5_2, "md5_expected": MODEL_MD5, "md5_matches_expected": model_md5 == MODEL_MD5, "sha256_before": model_sha, "sha256_after": model_sha2, "unchanged": model_sha == model_sha2, "input": {"name": inp.name, "shape": list(inp.shape), "type": inp.type}, "output": {"name": out.name, "shape": list(out.shape), "type": out.type}},
        "method": {"provider": "CPUExecutionProvider", "intra_op_num_threads": 4, "inter_op_num_threads": 1, "model_role": "alpha only; no RGB generated", "preprocess": "RGB LANCZOS 1024x1024; /max pixel; -mean(0.485,0.456,0.406); NCHW float32", "postprocess": "output[:,0,:,:]; min-max; *255 uint8 L; LANCZOS 820x1440", "alpha_steps": ["raw alpha<235 and RGB in v2 key (10<=r<=48,25<=g<=65,40<=b<=88,8<=b-g<=28,7<=g-r<=28) -> 0", "alpha<80 -> 0; else round((a-80)*255/175) clamped 0..255", "keep only largest 8-connected component of alpha>32; others -> 0", "no manual polygon/GrabCut/old mask/recolor/redraw/face fill"]},
        "cleanup": {"background_key_pixels_zeroed": int(keyed.sum()), "below_80_pixels_zeroed_after_key": int(below.sum()), "remap": "(a-80)*255/175, rounded, clamped 0..255", "components_above_32_before": n - 1, "components_removed": n - 2, "kept_component_area": kept_area, "removed_component_area": removed_area, "component_areas_top8": comps[:8], "manual_polygon_fill_used": False},
        "raw_alpha": alpha_stats(raw_alpha), "alpha": alpha_stats(alpha),
        "pixel_fidelity": {"all_pixels_compared": int(alpha.size), "output_rgb_mismatch_pixels": rgb_mismatch, "saved_rgb_mismatch_pixels": saved_mismatch, "all_pixels_rgb_identical_to_source": rgb_mismatch == 0 and saved_mismatch == 0, "note": "RGB copied byte-for-byte from the 072 crop, including RGB under transparent pixels."},
        "outputs": {"rgba": entry(OUT_RGBA, SIZE, "RGBA", "PNG"), "raw_mask": entry(OUT_RAW, SIZE, "L", "PNG"), "pink_preview": entry(OUT_PINK, SIZE, "RGB", "JPEG", quality=QUALITY, background_rgb=list(PINK)), "desktop_preview": entry(OUT_DESKTOP, DESKTOP_SIZE, "RGB", "PNG", background_rgb=list(DESKTOP_BG), content_size=content, content_offset_xy=offset, scaling_filter="PIL LANCZOS", aspect_ratio_preserved=True, not_animation_frame=True, not_runtime_asset=True), "report": {"path": rel(OUT_REPORT)}},
        "checks": {"source_and_provenance_sha_unchanged": source_sha == source_sha2 and prov_sha == prov_sha2, "model_md5_matches_expected": model_md5 == MODEL_MD5, "model_md5_and_sha_unchanged": model_md5 == model_md5_2 and model_sha == model_sha2, "output_rgba_round_trip": True, "output_alpha_matches": True, "raw_alpha_round_trip": True, "all_rgb_identical_to_source": True, "pink_preview_round_trip": True, "desktop_preview_round_trip": True, "desktop_preview_corners_light_grey": True},
        "software": {"python": sys.version.split()[0], "numpy": np.__version__, "pillow": pillow_version, "onnxruntime": ort.__version__, "opencv": cv2.__version__},
        "non_destructive": {"fixed_inputs_only": True, "fixed_write_targets_only": True, "write_targets": [rel(path) for path in TARGETS], "overwrite_requested": bool(args.overwrite), "existing_outputs_before_run": existing, "new_rgb_generated": False, "preview_compositing_only": True, "network_used": False, "model_downloaded": False, "model_config_changed": False, "assets_release_mobile_written": False},
        "limitations": ["isnet-anime 是动漫显著性分割模型；发丝、肩饰、薄纱、半透明边缘可能漏检或被色键误删，本轮仅为第一轮视觉判断，仍需全尺寸复核。", "只保留 alpha>32 的最大 8 连通区域，独立的小发丝、肩饰或鞋饰可能被整块删除；删去面积已记录，但不代表它们一定该删。", "背景色键只在原始 alpha<235 且 RGB 落在 v2 键范围时生效；接近该色范围的角色衣饰/发梢可能被误清，反之背景残边可能保留。", "未手工填任何内部 polygon，未用 GrabCut、旧蒙版、人工种子、调色、重绘或补脸；所有 RGB 逐字节来自 072 裁切原图。", "这是静态草稿，不是动画帧、图集或运行素材；尚未接入运行素材，需全尺寸 RGBA、粉色底和 384x416 浅灰底预览视觉复核。", "粉色底 JPG 有压缩损失，只用于快速查看；验收以 RGBA PNG 和 raw alpha PNG 为准。"],
    }
    OUT_REPORT.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"output": rel(OUT_RGBA), "raw_alpha": rel(OUT_RAW), "pink_preview": rel(OUT_PINK), "desktop_preview": rel(OUT_DESKTOP), "report": rel(OUT_REPORT), "foreground_pixels": int((alpha > 0).sum()), "alpha_four_corners": alpha_stats(alpha)["four_corners"], "removed_component_area": removed_area, "all_rgb_identical": True, "source_and_model_unchanged": True}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

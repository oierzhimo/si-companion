#!/usr/bin/env python3
"""Build a soft cutout of the in-game screenshot with the local U2NetP model.

This is a local, non-destructive QA tool. It reads only:
  - qa/si-body-reference-071.png
  - qa/segmentation-deps/u2netp.onnx

and writes only the three fixed model-draft-v1 paths below. The source screenshot and
the model are hashed before and after the run. Only output alpha is synthesized:
every output RGB byte remains identical to the source screenshot, including RGB
under fully transparent pixels.

The ONNX graph is inspected before inference. The tool requires the standard
U2NetP contract (float32, NCHW, 1x3x320x320 input; float32 1x1x320x320 primary
output) and fails with a descriptive error instead of guessing at a different
graph. Optional auxiliary U2Net heads are recorded but not consumed.

Run with the environment that has numpy/Pillow/onnxruntime, for example:
  "E:\\新建文件夹\\VoiceStudio\\project\\.venv\\Scripts\\python.exe" \
      tools\\extract_game_screenshot_u2netp.py

The earlier GrabCut draft uses different filenames and is never read or written.
A rerun refuses to overwrite these three new model-draft files unless --overwrite
is passed explicitly.

If guided edge refinement is selected, cv2 is loaded from qa/segmentation-deps.
No GrabCut result is read, and no GrabCut mask is combined with the U2NetP mask.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np
from PIL import Image, __version__ as pillow_version

TOOL_PATH = Path(__file__).resolve()
ROOT = TOOL_PATH.parents[1]
CV2_DEPS = ROOT / "qa" / "segmentation-deps"
CANONICAL_INPUT = ROOT / "qa" / "si-body-reference-071.png"
CANONICAL_MODEL = ROOT / "qa" / "segmentation-deps" / "u2netp.onnx"
DEFAULT_OUTPUT = ROOT / "art-source" / "v1.3" / "si-game-cutout-model-draft-v1.png"
DEFAULT_REPORT = ROOT / "qa" / "si-game-cutout-model-draft-v1-report.json"
DEFAULT_PREVIEW = ROOT / "qa" / "si-game-cutout-model-draft-v1-pink-preview.jpg"

EXPECTED_SIZE = (920, 1540)
EXPECTED_MODEL_INPUT_SHAPE = [1, 3, 320, 320]
EXPECTED_MODEL_OUTPUT_SHAPE = [1, 1, 320, 320]
INPUT_MEAN = (0.485, 0.456, 0.406)
INPUT_STD = (0.229, 0.224, 0.225)
PINK_RGB = (255, 105, 180)
PREVIEW_QUALITY = 95
SCHEMA = "si-game-cutout-u2netp-report/v1"

# Fixed writes only. The source, model, runtime assets, releases, mobile copies,
# and other art are never write targets.
WRITE_TARGETS = (DEFAULT_OUTPUT, DEFAULT_REPORT, DEFAULT_PREVIEW)

# Kept here so the report can state the non-destructive policy explicitly. The
# fixed target guard means these paths are never even candidates for writing.
PROTECTED_DIRS = (
    ROOT / ".git",
    ROOT / ".venv",
    ROOT / "assets",
    ROOT / "release",
    ROOT / "mobile",
    ROOT / "art-source" / "original",
    ROOT / "art-source" / "v1.1",
    ROOT / "qa" / "segmentation-deps",
)

LIMITATIONS = [
    "U2NetP 是 320x320 通用显著性模型；细角、墨翼、发丝、薄纱和半透明衣料仍可能漏检或边缘粗糙，需以全尺寸 RGBA 和粉色预览人工复核。",
    "本工具只生成 alpha，不生成或修饰任何 RGB；透明和半透明像素下方的 RGB 保留源截图原值，便于复查，但不代表这些隐藏颜色适合直接用于新背景。",
    "软 alpha 由概率阈值带和可选的色彩引导滤波生成，不是专业 trimap/matting；高光、烟雾、景深以及与角色同色的背景仍可能有误差。",
    "粉色底 JPG 仅用于快速查看，存在 JPEG 压缩；验收和后续处理应以 RGBA PNG 为准。",
    "本工具不使用、也不与旧 GrabCut 结果相与，不读取旧草稿蒙版；没有人工多边形或前景种子，因此不会继承 GrabCut 误删角和翅膀的问题。",
    "模型固定为本地 rembg 官方 release 的 u2netp.onnx，不联网、不重训；若模型输入或主输出不是预期的 float32 1x3x320x320 / 1x1x320x320，工具会明确报错而不猜测。",
]


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _relative(path: Path) -> str:
    return path.resolve().relative_to(ROOT.resolve()).as_posix()


def _guard_targets(overwrite: bool) -> list[str]:
    """Validate only the three fixed model-draft-v1 targets can be written."""
    for path in WRITE_TARGETS:
        # Fixed paths are used, but still reject a symlinked parent or leaf so a
        # write cannot be redirected into original art or a release directory.
        if not path.parent.resolve().is_relative_to(ROOT.resolve()):
            raise SystemExit(f"Write target escapes the project: {path}")
        for protected in PROTECTED_DIRS:
            if path.resolve().is_relative_to(protected.resolve()):
                raise SystemExit(
                    f"Refusing to write into protected path: {_relative(protected)}"
                )
        if path.is_symlink():
            raise SystemExit(f"Refusing to write through a symlink: {path}")
        if path.exists() and path.is_dir():
            raise SystemExit(f"Write target is a directory: {path}")

    existing = [_relative(path) for path in WRITE_TARGETS if path.exists()]
    if existing and not overwrite:
        names = ", ".join(existing)
        raise SystemExit(
            "Refusing to overwrite existing output(s): "
            f"{names}. Pass --overwrite to replace only the three fixed "
            "si-game-cutout-model-draft-v1 outputs."
        )
    return existing


def _guard_sources() -> None:
    if CANONICAL_INPUT.is_symlink():
        raise SystemExit(f"Source path must not be a symlink: {CANONICAL_INPUT}")
    if CANONICAL_MODEL.is_symlink():
        raise SystemExit(f"Model path must not be a symlink: {CANONICAL_MODEL}")
    if not CANONICAL_INPUT.is_file():
        raise SystemExit(f"Expected source screenshot is missing: {CANONICAL_INPUT}")
    if not CANONICAL_MODEL.is_file():
        raise SystemExit(f"Expected U2NetP model is missing: {CANONICAL_MODEL}")
    if CANONICAL_INPUT.resolve() in {path.resolve() for path in WRITE_TARGETS}:
        raise SystemExit("Source screenshot collides with an output path")
    if CANONICAL_MODEL.resolve() in {path.resolve() for path in WRITE_TARGETS}:
        raise SystemExit("Model collides with an output path")


def _read_source() -> tuple[np.ndarray, str]:
    sha_before = _sha256(CANONICAL_INPUT)
    with Image.open(CANONICAL_INPUT) as image:
        if image.mode != "RGBA":
            raise SystemExit(f"Expected an RGBA source, got {image.mode}")
        if image.size != EXPECTED_SIZE:
            raise SystemExit(
                f"Expected {EXPECTED_SIZE[0]}x{EXPECTED_SIZE[1]}, got "
                f"{image.width}x{image.height}"
            )
        image.load()
        source = np.array(image, dtype=np.uint8).copy()

    sha_after_read = _sha256(CANONICAL_INPUT)
    if sha_before != sha_after_read:
        raise SystemExit("Source screenshot changed while it was being read")
    if source.shape != (EXPECTED_SIZE[1], EXPECTED_SIZE[0], 4):
        raise SystemExit(f"Unexpected source array shape: {source.shape}")
    if not np.all(source[..., 3] == 255):
        raise SystemExit("Source alpha is not uniformly 255; refusing this screenshot")
    return source, sha_before


def _meta(info: Any) -> dict[str, Any]:
    return {
        "name": info.name,
        "type": info.type,
        "shape": list(info.shape) if info.shape is not None else None,
    }


def _create_session(threads: int) -> tuple[Any, dict[str, Any], str]:
    try:
        import onnxruntime as ort
    except ImportError as exc:
        raise SystemExit(
            "onnxruntime is unavailable in the active interpreter; use the QA "
            "environment containing numpy/Pillow/onnxruntime."
        ) from exc

    if "CPUExecutionProvider" not in ort.get_available_providers():
        raise SystemExit("onnxruntime CPUExecutionProvider is not available")

    model_sha_before = _sha256(CANONICAL_MODEL)
    options = ort.SessionOptions()
    options.intra_op_num_threads = threads
    options.inter_op_num_threads = 1
    options.execution_mode = ort.ExecutionMode.ORT_SEQUENTIAL
    options.graph_optimization_level = ort.GraphOptimizationLevel.ORT_ENABLE_ALL
    options.log_severity_level = 3

    try:
        session = ort.InferenceSession(
            str(CANONICAL_MODEL),
            sess_options=options,
            providers=["CPUExecutionProvider"],
        )
    except Exception as exc:
        raise SystemExit(f"Could not load the ONNX model: {exc}") from exc

    inputs = session.get_inputs()
    outputs = session.get_outputs()
    if len(inputs) != 1:
        raise SystemExit(
            "Expected one U2NetP input, got "
            f"{len(inputs)}: {[item.name for item in inputs]}. "
            "Refusing to guess which input to use."
        )
    input_info = inputs[0]
    if input_info.type != "tensor(float)":
        raise SystemExit(
            f"Expected float32 input, got {input_info.type} for '{input_info.name}'"
        )
    input_shape = list(input_info.shape) if input_info.shape is not None else None
    if input_shape != EXPECTED_MODEL_INPUT_SHAPE:
        raise SystemExit(
            "Expected U2NetP input shape "
            f"{EXPECTED_MODEL_INPUT_SHAPE}, got {input_shape} for "
            f"'{input_info.name}'. The model is not the expected U2NetP graph."
        )
    if not outputs:
        raise SystemExit("The ONNX model exposes no outputs")

    # U2Net exports d0 first, followed by optional auxiliary heads (d1..d6).
    # rembg uses graph output 0, so require that primary contract explicitly and
    # record -- but do not consume -- any auxiliary outputs. This avoids guessing
    # from output names while still accepting the standard multi-head graph.
    primary_info = outputs[0]
    if primary_info.type != "tensor(float)":
        raise SystemExit(
            "Expected float32 primary output, got "
            f"{primary_info.type} for '{primary_info.name}'"
        )
    primary_shape = (
        list(primary_info.shape) if primary_info.shape is not None else None
    )
    if primary_shape != EXPECTED_MODEL_OUTPUT_SHAPE:
        raise SystemExit(
            "Expected U2NetP primary output shape "
            f"{EXPECTED_MODEL_OUTPUT_SHAPE}, got {primary_shape} for "
            f"'{primary_info.name}'. The model is not the expected U2NetP graph."
        )

    output_meta = []
    for index, info in enumerate(outputs):
        if info.type != "tensor(float)":
            raise SystemExit(
                "Expected every U2NetP output to be float32, got "
                f"{info.type} for output #{index} '{info.name}'"
            )
        output_meta.append(_meta(info))

    model_sha_after_load = _sha256(CANONICAL_MODEL)
    if model_sha_before != model_sha_after_load:
        raise SystemExit("Model file changed while it was being loaded")

    metadata = {
        "input": _meta(input_info),
        "outputs": output_meta,
        "primary_output": outputs[0].name,
        "onnxruntime_version": ort.__version__,
    }
    return session, metadata, model_sha_before


def _preprocess(source_rgb: np.ndarray) -> np.ndarray:
    source_rgb = np.ascontiguousarray(source_rgb, dtype=np.uint8)
    image = Image.fromarray(source_rgb)
    resized = image.resize((320, 320), Image.Resampling.BILINEAR)
    rgb = np.asarray(resized, dtype=np.float32) / np.float32(255.0)
    normalized = (rgb - np.asarray(INPUT_MEAN, dtype=np.float32)) / np.asarray(
        INPUT_STD, dtype=np.float32
    )
    return np.ascontiguousarray(
        normalized.transpose(2, 0, 1)[None, ...], dtype=np.float32
    )


def _infer(
    session: Any, input_name: str, output_name: str, tensor: np.ndarray
) -> tuple[np.ndarray, dict[str, float]]:
    try:
        raw = session.run([output_name], {input_name: tensor})[0]
    except Exception as exc:
        raise SystemExit(f"ONNX CPU inference failed: {exc}") from exc

    output = np.asarray(raw)
    if output.shape != tuple(EXPECTED_MODEL_OUTPUT_SHAPE):
        raise SystemExit(
            "Runtime output shape "
            f"{list(output.shape)} does not match "
            f"{EXPECTED_MODEL_OUTPUT_SHAPE}; refusing to continue."
        )
    if output.dtype != np.float32:
        raise SystemExit(
            f"Runtime output dtype is {output.dtype}, expected float32"
        )
    if not np.all(np.isfinite(output)):
        raise SystemExit("Model output contains NaN or infinity")

    probability = output[0, 0]
    raw_min = float(probability.min())
    raw_max = float(probability.max())
    if raw_min < -1e-5 or raw_max > 1.0 + 1e-5:
        raise SystemExit(
            "Model output is outside [0, 1] "
            f"(min={raw_min:.6f}, max={raw_max:.6f}). Expected a U2NetP "
            "sigmoid probability output; refusing to guess an output transform."
        )
    if raw_max - raw_min < 1e-8:
        raise SystemExit("Model output is constant; cannot build an alpha mask")

    probability = np.clip(probability, 0.0, 1.0).astype(np.float32)
    return probability, {
        "raw_min": raw_min,
        "raw_max": raw_max,
        "clipped_min": float(probability.min()),
        "clipped_max": float(probability.max()),
    }


def _resize_probability(probability: np.ndarray) -> np.ndarray:
    image = Image.fromarray(probability.astype(np.float32))
    resized = image.resize(EXPECTED_SIZE, Image.Resampling.BILINEAR)
    full = np.asarray(resized, dtype=np.float32).copy()
    if full.shape != (EXPECTED_SIZE[1], EXPECTED_SIZE[0]):
        raise SystemExit(f"Unexpected resized probability shape: {full.shape}")
    return np.clip(full, 0.0, 1.0)


def _load_cv2() -> Any:
    if str(CV2_DEPS) not in sys.path:
        sys.path.insert(0, str(CV2_DEPS))
    try:
        import cv2
    except ImportError as exc:
        raise SystemExit(
            "cv2 is unavailable in qa/segmentation-deps; run with "
            "--edge-refine none or restore the local OpenCV dependency."
        ) from exc
    cv2.setNumThreads(1)
    cv2.setRNGSeed(0)
    return cv2


def _guided_refine(
    probability: np.ndarray,
    rgb: np.ndarray,
    threshold: float,
    band: float,
    radius: int,
    eps: float,
    cv2: Any,
) -> tuple[np.ndarray, dict[str, Any]]:
    """Apply a color-guided filter only inside the uncertain probability band.

    Confident foreground/background probabilities are left untouched. This is a
    soft edge refinement, not a second segmentation mask and not a GrabCut union.
    """
    guide = rgb.astype(np.float32) / np.float32(255.0)
    src = probability.astype(np.float32)
    ksize = (radius * 2 + 1, radius * 2 + 1)
    border = cv2.BORDER_REFLECT

    mean_i = cv2.boxFilter(
        guide, -1, ksize, normalize=True, borderType=border
    )
    mean_p = cv2.boxFilter(
        src, -1, ksize, normalize=True, borderType=border
    )
    corr_ip = cv2.boxFilter(
        guide * src[..., None], -1, ksize, normalize=True, borderType=border
    )
    cov_ip = corr_ip - mean_i * mean_p[..., None]
    corr_ii = cv2.boxFilter(
        guide * guide, -1, ksize, normalize=True, borderType=border
    )
    var_i = corr_ii - mean_i * mean_i
    a = cov_ip / (var_i + np.float32(eps))
    b = mean_p - np.sum(a * mean_i, axis=2)
    mean_a = cv2.boxFilter(
        a, -1, ksize, normalize=True, borderType=border
    )
    mean_b = cv2.boxFilter(
        b, -1, ksize, normalize=True, borderType=border
    )
    filtered = np.sum(mean_a * guide, axis=2) + mean_b
    filtered = np.clip(filtered, 0.0, 1.0)

    distance = np.clip(np.abs(src - np.float32(threshold)) / np.float32(band), 0, 1)
    weight = 1.0 - distance
    weight = weight * weight * (3.0 - 2.0 * weight)
    refined = src * (1.0 - weight) + filtered * weight
    refined = np.clip(refined, 0.0, 1.0).astype(np.float32)

    return refined, {
        "filter": "color-guided filter (cv2 boxFilter implementation)",
        "radius_pixels": radius,
        "eps": eps,
        "band_probability": band,
        "weighted_pixels": int(np.count_nonzero(weight > 0)),
        "fully_refined_pixels": int(np.count_nonzero(weight >= 0.999999)),
        "cv2_version": cv2.__version__,
    }


def _probability_to_alpha(
    probability: np.ndarray, mode: str, threshold: float, softness: float
) -> tuple[np.ndarray, dict[str, Any]]:
    if mode == "binary":
        alpha = np.where(probability >= threshold, 255, 0).astype(np.uint8)
        return alpha, {
            "mode": "binary",
            "threshold": threshold,
            "transition": "none",
        }

    low = max(0.0, threshold - softness)
    high = min(1.0, threshold + softness)
    if high <= low:
        raise SystemExit("Soft alpha transition is empty; check threshold/softness")
    t = np.clip((probability - np.float32(low)) / np.float32(high - low), 0.0, 1.0)
    t = t * t * (3.0 - 2.0 * t)
    alpha = np.rint(t * np.float32(255.0)).astype(np.uint8)
    return alpha, {
        "mode": "soft",
        "threshold": threshold,
        "softness": softness,
        "low_probability": low,
        "high_probability": high,
        "transition": "smoothstep over [threshold-softness, threshold+softness]",
    }


def _distribution(values: np.ndarray) -> dict[str, Any]:
    flat = np.asarray(values, dtype=np.float64).reshape(-1)
    if flat.size == 0:
        raise SystemExit("Cannot summarize an empty array")
    percentiles = np.percentile(flat, [0, 1, 5, 25, 50, 75, 95, 99, 100])
    return {
        "count": int(flat.size),
        "min": round(float(flat.min()), 8),
        "p1": round(float(percentiles[1]), 8),
        "p5": round(float(percentiles[2]), 8),
        "p25": round(float(percentiles[3]), 8),
        "median": round(float(percentiles[4]), 8),
        "p75": round(float(percentiles[5]), 8),
        "p95": round(float(percentiles[6]), 8),
        "p99": round(float(percentiles[7]), 8),
        "max": round(float(flat.max()), 8),
        "mean": round(float(flat.mean()), 8),
    }


def _alpha_report(alpha: np.ndarray) -> dict[str, Any]:
    total = int(alpha.size)
    values, counts = np.unique(alpha, return_counts=True)
    histogram = [0] * 256
    for value, count in zip(values.tolist(), counts.tolist()):
        histogram[int(value)] = int(count)
    transparent = histogram[0]
    opaque = histogram[255]
    partial = total - transparent - opaque
    ys, xs = np.nonzero(alpha)
    bbox = (
        [int(xs.min()), int(ys.min()), int(xs.max()) + 1, int(ys.max()) + 1]
        if xs.size
        else None
    )
    return {
        "total_pixels": total,
        "transparent_pixels": transparent,
        "partial_pixels": partial,
        "foreground_pixels": total - transparent,
        "opaque_pixels": opaque,
        "alpha_min": int(alpha.min()),
        "alpha_max": int(alpha.max()),
        "alpha_mean": round(float(alpha.mean()), 6),
        "binary_alpha": bool(np.all((alpha == 0) | (alpha == 255))),
        "histogram_256": histogram,
        "four_corners": {
            "top_left": int(alpha[0, 0]),
            "top_right": int(alpha[0, -1]),
            "bottom_left": int(alpha[-1, 0]),
            "bottom_right": int(alpha[-1, -1]),
        },
        "all_four_corners_transparent": bool(
            alpha[0, 0] == 0
            and alpha[0, -1] == 0
            and alpha[-1, 0] == 0
            and alpha[-1, -1] == 0
        ),
        "foreground_bbox_xyxy": bbox,
        "foreground_coverage_percent": round((total - transparent) / total * 100, 4),
        "foreground_on_image_border": bool(
            np.any(alpha[0, :])
            or np.any(alpha[-1, :])
            or np.any(alpha[:, 0])
            or np.any(alpha[:, -1])
        ),
    }


def _component_report(alpha: np.ndarray, cv2: Any | None) -> dict[str, Any] | None:
    if cv2 is None:
        return None
    binary = (alpha >= 128).astype(np.uint8)
    count, _labels, stats, _centroids = cv2.connectedComponentsWithStats(
        binary, connectivity=8
    )
    areas = stats[1:, cv2.CC_STAT_AREA] if count > 1 else np.empty(0, np.int32)
    return {
        "threshold_alpha": 128,
        "component_count": int(max(0, count - 1)),
        "largest_component_area": int(areas.max()) if areas.size else 0,
        "components_under_16_pixels": int(np.count_nonzero(areas < 16)),
        "components_under_64_pixels": int(np.count_nonzero(areas < 64)),
        "note": "Reported only for review; no components are removed by this tool.",
    }


def _rgb_fidelity(output: np.ndarray, source: np.ndarray) -> dict[str, Any]:
    foreground = output[..., 3] > 0
    foreground_count = int(np.count_nonzero(foreground))
    output_rgb = output[..., :3]
    source_rgb = source[..., :3]
    if foreground_count:
        fg_different = np.any(
            output_rgb[foreground] != source_rgb[foreground], axis=1
        )
        foreground_mismatch = int(np.count_nonzero(fg_different))
    else:
        foreground_mismatch = 0
    all_mismatch = int(np.count_nonzero(np.any(output_rgb != source_rgb, axis=2)))
    return {
        "foreground_pixels_compared": foreground_count,
        "foreground_rgb_mismatch_pixels": foreground_mismatch,
        "foreground_rgb_identical_to_source": foreground_mismatch == 0,
        "all_pixels_rgb_mismatch_pixels": all_mismatch,
        "all_pixels_rgb_identical_to_source": all_mismatch == 0,
        "note": "Only alpha is replaced; every RGB byte is inherited from the source.",
    }


def _pink_preview(output: np.ndarray) -> np.ndarray:
    rgb = output[..., :3].astype(np.float32)
    alpha = output[..., 3].astype(np.float32)[..., None] / np.float32(255.0)
    pink = np.asarray(PINK_RGB, dtype=np.float32)
    composited = rgb * alpha + pink * (1.0 - alpha)
    return np.clip(np.rint(composited), 0, 255).astype(np.uint8)


def _temp_path(path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, name = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=str(path.parent)
    )
    os.close(descriptor)
    return Path(name)


def _save_png(path: Path, rgba: np.ndarray) -> None:
    Image.fromarray(rgba).save(path, format="PNG", optimize=True)


def _save_jpeg(path: Path, rgb: np.ndarray) -> None:
    Image.fromarray(rgb).save(
        path,
        format="JPEG",
        quality=PREVIEW_QUALITY,
        subsampling=0,
        optimize=True,
    )


def _save_json(path: Path, payload: dict[str, Any]) -> None:
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def _verify_saved_png(
    path: Path, alpha: np.ndarray, source: np.ndarray
) -> tuple[np.ndarray, dict[str, Any]]:
    with Image.open(path) as image:
        if image.mode != "RGBA":
            raise SystemExit(f"Saved PNG mode is {image.mode}, expected RGBA")
        if image.size != EXPECTED_SIZE:
            raise SystemExit(
                f"Saved PNG size is {image.width}x{image.height}, expected "
                f"{EXPECTED_SIZE[0]}x{EXPECTED_SIZE[1]}"
            )
        image.load()
        saved = np.array(image, dtype=np.uint8).copy()
    if not np.array_equal(saved[..., 3], alpha):
        raise SystemExit("Saved PNG alpha differs from the computed alpha")
    fidelity = _rgb_fidelity(saved, source)
    if not fidelity["all_pixels_rgb_identical_to_source"]:
        raise SystemExit("Saved PNG RGB fidelity check failed")
    return saved, fidelity


def _validate_args(args: argparse.Namespace) -> None:
    if not 0.0 <= args.threshold <= 1.0:
        raise SystemExit("--threshold must be in [0, 1]")
    if args.alpha_mode == "soft" and not 0.0 < args.softness <= 1.0:
        raise SystemExit("--softness must be in (0, 1] for soft alpha")
    if args.threads < 1:
        raise SystemExit("--threads must be >= 1")
    if args.edge_refine == "guided":
        if args.guide_radius < 1:
            raise SystemExit("--guide-radius must be >= 1")
        if args.guide_eps <= 0:
            raise SystemExit("--guide-eps must be > 0")
        if not 0.0 < args.refine_band <= 1.0:
            raise SystemExit("--refine-band must be in (0, 1]")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Non-destructive U2NetP soft cutout of qa/si-body-reference-071.png. "
            "Only the three fixed si-game-cutout-model-draft-v1 outputs are written."
        ),
        epilog=(
            "Existing draft-v1 outputs are refused unless --overwrite is passed. "
            "The source, model, original art, assets, release, and mobile paths are "
            "never write targets. No network access or model training is performed."
        ),
    )
    parser.add_argument(
        "--threshold",
        type=float,
        default=0.5,
        help="Probability threshold in [0, 1] (default: 0.5)",
    )
    parser.add_argument(
        "--alpha-mode",
        choices=("soft", "binary"),
        default="soft",
        help="Soft threshold band or hard 0/255 alpha (default: soft)",
    )
    parser.add_argument(
        "--softness",
        type=float,
        default=0.06,
        help="Half-width of the soft probability band (default: 0.06)",
    )
    parser.add_argument(
        "--edge-refine",
        choices=("guided", "none"),
        default="guided",
        help="Color-guided soft edge refinement from qa/segmentation-deps cv2",
    )
    parser.add_argument(
        "--guide-radius",
        type=int,
        default=4,
        help="Guided-filter radius in full-resolution pixels (default: 4)",
    )
    parser.add_argument(
        "--guide-eps",
        type=float,
        default=1e-4,
        help="Guided-filter regularization (default: 1e-4)",
    )
    parser.add_argument(
        "--refine-band",
        type=float,
        default=0.12,
        help="Probability band around threshold that guided refinement may touch",
    )
    parser.add_argument(
        "--threads",
        type=int,
        default=1,
        help="ONNX intra-op CPU threads; 1 is deterministic (default: 1)",
    )
    parser.add_argument(
        "--overwrite",
        action="store_true",
        help=(
            "Replace only the three fixed draft-v1 outputs; never originals, "
            "assets, release, mobile, source, or model"
        ),
    )
    args = parser.parse_args(argv)
    _validate_args(args)

    _guard_sources()
    preexisting_targets = _guard_targets(args.overwrite)

    source, source_sha_before = _read_source()
    session, model_meta, model_sha_before = _create_session(args.threads)
    tensor = _preprocess(source[..., :3])
    probability_320, probability_meta = _infer(
        session,
        input_name=model_meta["input"]["name"],
        output_name=model_meta["primary_output"],
        tensor=tensor,
    )
    probability_full = _resize_probability(probability_320)

    cv2 = _load_cv2() if args.edge_refine == "guided" else None
    if cv2 is not None:
        probability_refined, refine_meta = _guided_refine(
            probability_full,
            source[..., :3],
            threshold=args.threshold,
            band=args.refine_band,
            radius=args.guide_radius,
            eps=args.guide_eps,
            cv2=cv2,
        )
    else:
        probability_refined = probability_full
        refine_meta = {"filter": "none", "cv2_version": None}

    alpha, alpha_meta = _probability_to_alpha(
        probability_refined,
        mode=args.alpha_mode,
        threshold=args.threshold,
        softness=args.softness,
    )
    alpha_stats = _alpha_report(alpha)
    if alpha_stats["foreground_pixels"] == 0:
        raise SystemExit("U2NetP produced no foreground pixels")

    output = source.copy()
    output[..., 3] = alpha
    preview_rgb = _pink_preview(output)

    source_sha_after_inference = _sha256(CANONICAL_INPUT)
    model_sha_after_inference = _sha256(CANONICAL_MODEL)
    if source_sha_before != source_sha_after_inference:
        raise SystemExit("Source screenshot changed during inference")
    if model_sha_before != model_sha_after_inference:
        raise SystemExit("Model file changed during inference")

    tmp_output = _temp_path(DEFAULT_OUTPUT)
    tmp_preview = _temp_path(DEFAULT_PREVIEW)
    tmp_report = _temp_path(DEFAULT_REPORT)
    try:
        _save_png(tmp_output, output)
        saved, fidelity = _verify_saved_png(tmp_output, alpha, source)
        _save_jpeg(tmp_preview, preview_rgb)
        with Image.open(tmp_preview) as preview_image:
            if preview_image.mode != "RGB" or preview_image.size != EXPECTED_SIZE:
                raise SystemExit("Preview JPEG did not round-trip as RGB 920x1540")

        # Recheck immediately before committing. Hash fields in the report use
        # these final pre-commit values.
        source_sha_after = _sha256(CANONICAL_INPUT)
        model_sha_after = _sha256(CANONICAL_MODEL)
        if source_sha_before != source_sha_after:
            raise SystemExit("Source screenshot changed before outputs were written")
        if model_sha_before != model_sha_after:
            raise SystemExit("Model file changed before outputs were written")
        if not args.overwrite:
            for path in WRITE_TARGETS:
                if path.exists():
                    raise SystemExit(
                        f"Refusing to overwrite file created during the run: {path}"
                    )

        output_sha = _sha256(tmp_output)
        preview_sha = _sha256(tmp_preview)
        report = {
            "schema": SCHEMA,
            "created_at_utc": datetime.now(timezone.utc).isoformat(),
            "tool": {
                "path": _relative(TOOL_PATH),
                "sha256": _sha256(TOOL_PATH),
            },
            "source": {
                "path": _relative(CANONICAL_INPUT),
                "sha256_before_run": source_sha_before,
                "sha256_after_run": source_sha_after,
                "unchanged": source_sha_before == source_sha_after,
                "size": list(EXPECTED_SIZE),
                "mode": "RGBA",
                "alpha_all_255": True,
                "bytes": CANONICAL_INPUT.stat().st_size,
            },
            "model": {
                "path": _relative(CANONICAL_MODEL),
                "sha256_before_run": model_sha_before,
                "sha256_after_run": model_sha_after,
                "unchanged": model_sha_before == model_sha_after,
                "bytes": CANONICAL_MODEL.stat().st_size,
                "input": model_meta["input"],
                "outputs": model_meta["outputs"],
                "output_count": len(model_meta["outputs"]),
                "primary_output": model_meta["primary_output"],
                "auxiliary_outputs_used": False,
            },
            "inference": {
                "provider": "onnxruntime CPUExecutionProvider",
                "onnxruntime_version": model_meta["onnxruntime_version"],
                "intra_op_num_threads": args.threads,
                "inter_op_num_threads": 1,
                "execution_mode": "ORT_SEQUENTIAL",
                "graph_optimization_level": "ORT_ENABLE_ALL",
                "input_resize": "PIL bilinear, direct 920x1540 -> 320x320",
                "input_layout": "NCHW",
                "input_dtype": "float32",
                "input_scale": "uint8 RGB / 255.0",
                "normalization_mean_rgb": list(INPUT_MEAN),
                "normalization_std_rgb": list(INPUT_STD),
                "output_resize": "PIL bilinear, 320x320 -> 920x1540",
                "probability_source": "model sigmoid output, clipped to [0, 1]",
                **probability_meta,
            },
            "parameters": {
                "threshold": args.threshold,
                "alpha_mode": args.alpha_mode,
                "softness": args.softness,
                "edge_refine": args.edge_refine,
                "guide_radius": args.guide_radius,
                "guide_eps": args.guide_eps,
                "refine_band": args.refine_band,
                "grabcut_used": False,
                "failed_grabcut_mask_used": False,
                "manual_polygons_or_seeds_used": False,
                "components_removed": False,
            },
            "mask": {
                "probability_320": _distribution(probability_320),
                "probability_full_resolution": _distribution(probability_full),
                "probability_after_edge_refine": _distribution(probability_refined),
                "thresholded_pixels_at_or_above": int(
                    np.count_nonzero(probability_refined >= args.threshold)
                ),
                "thresholded_coverage_percent": round(
                    float(
                        np.count_nonzero(probability_refined >= args.threshold)
                    )
                    / probability_refined.size
                    * 100.0,
                    4,
                ),
                "alpha": alpha_stats,
                "binary_components_at_alpha_128": _component_report(alpha, cv2),
            },
            "edge_refinement": refine_meta,
            "alpha_conversion": alpha_meta,
            "output": {
                "path": _relative(DEFAULT_OUTPUT),
                "sha256": output_sha,
                "size": [int(saved.shape[1]), int(saved.shape[0])],
                "mode": "RGBA",
                "bytes": tmp_output.stat().st_size,
            },
            "preview": {
                "path": _relative(DEFAULT_PREVIEW),
                "sha256": preview_sha,
                "size": list(EXPECTED_SIZE),
                "mode": "RGB",
                "format": "JPEG",
                "quality": PREVIEW_QUALITY,
                "subsampling": 0,
                "background_rgb": list(PINK_RGB),
                "note": "Lossy preview only; inspect the RGBA PNG for acceptance.",
            },
            "report": {"path": _relative(DEFAULT_REPORT)},
            "pixel_fidelity": fidelity,
            "checks": {
                "source_sha_unchanged": source_sha_before == source_sha_after,
                "model_sha_unchanged": model_sha_before == model_sha_after,
                "model_shape_and_dtype_match": True,
                "output_rgba_round_trip": True,
                "output_alpha_matches": True,
                "foreground_rgb_identical": fidelity[
                    "foreground_rgb_identical_to_source"
                ],
                "all_rgb_identical": fidelity["all_pixels_rgb_identical_to_source"],
            },
            "software": {
                "python": sys.version.split()[0],
                "numpy": np.__version__,
                "pillow": pillow_version,
                "opencv": cv2.__version__ if cv2 is not None else None,
            },
            "non_destructive": {
                "fixed_inputs_only": True,
                "fixed_write_targets_only": True,
                "write_targets": [_relative(path) for path in WRITE_TARGETS],
                "protected_directories": [_relative(path) for path in PROTECTED_DIRS],
                "overwrite_requested": bool(args.overwrite),
                "existing_outputs_before_run": preexisting_targets,
                "existing_outputs_replaced": preexisting_targets,
                "originals_assets_release_mobile_written": False,
                "network_used": False,
                "model_retrained": False,
            },
            "limitations": LIMITATIONS,
        }
        _save_json(tmp_report, report)

        source_sha_final = _sha256(CANONICAL_INPUT)
        model_sha_final = _sha256(CANONICAL_MODEL)
        if source_sha_before != source_sha_final:
            raise SystemExit("Source screenshot changed before report commit")
        if model_sha_before != model_sha_final:
            raise SystemExit("Model file changed before report commit")

        for target, temporary in (
            (DEFAULT_OUTPUT, tmp_output),
            (DEFAULT_PREVIEW, tmp_preview),
            (DEFAULT_REPORT, tmp_report),
        ):
            os.replace(temporary, target)
    finally:
        for temporary in (tmp_output, tmp_preview, tmp_report):
            temporary.unlink(missing_ok=True)

    print(
        json.dumps(
            {
                "output": _relative(DEFAULT_OUTPUT),
                "preview": _relative(DEFAULT_PREVIEW),
                "report": _relative(DEFAULT_REPORT),
                "foreground_pixels": alpha_stats["foreground_pixels"],
                "partial_alpha_pixels": alpha_stats["partial_pixels"],
                "foreground_rgb_identical": fidelity[
                    "foreground_rgb_identical_to_source"
                ],
                "source_sha_unchanged": source_sha_before == source_sha_after,
                "model_sha_unchanged": model_sha_before == model_sha_after,
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

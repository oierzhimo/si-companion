#!/usr/bin/env python3
"""Build a soft cutout of the in-game screenshot with the local isnet-anime model.

This is a local, non-destructive QA tool. It reads only:
  - qa/si-body-reference-071.png
  - qa/segmentation-deps/isnet-anime.onnx

and writes only the three fixed isnet-draft-v1 paths below. The source screenshot
and the model are hashed before and after the run. Only output alpha is
synthesized: every output RGB byte remains identical to the source screenshot,
including RGB under fully transparent pixels.

The preprocessing and postprocessing intentionally mirror the locally installed
rembg DIS session (qa/segmentation-deps/rembg/sessions/dis_anime.py and base.py):

  PIL RGB -> 1024x1024 LANCZOS
  numpy uint8 / max(pixel), subtract mean (0.485, 0.456, 0.406), std (1, 1, 1)
  float32 NCHW -> ONNX Runtime CPU -> first output
  output[:, 0, :, :], min-max normalize, *255 -> uint8 L
  PIL LANCZOS back to the source size

The tool does not import rembg/pooch, so no model download or network access is
attempted. It also never invokes a cloud provider.

Run with the environment that has numpy/Pillow/onnxruntime, for example:
  "E:\\新建文件夹\\VoiceStudio\\project\\.venv\\Scripts\\python.exe" \
      tools\\extract_game_screenshot_isnet_anime.py

The default alpha is the model mask directly. --threshold N optionally turns it
into a binary 0/255 mask. A rerun refuses to overwrite the three isnet-draft-v1
outputs unless --overwrite is passed explicitly.

The tool never writes the original qa screenshot, the model, D:\\终末地小祀,
runtime assets, release payloads, mobile copies, or older cutout drafts.
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
CANONICAL_INPUT = ROOT / "qa" / "si-body-reference-071.png"
CANONICAL_MODEL = ROOT / "qa" / "segmentation-deps" / "isnet-anime.onnx"
DEFAULT_OUTPUT = (
    ROOT / "art-source" / "v1.3" / "si-game-cutout-isnet-draft-v1.png"
)
DEFAULT_PREVIEW = ROOT / "qa" / "si-game-cutout-isnet-draft-v1-pink-preview.jpg"
DEFAULT_REPORT = ROOT / "qa" / "si-game-cutout-isnet-draft-v1-report.json"

EXPECTED_SOURCE_SIZE = (920, 1540)
MODEL_INPUT_SIZE = (1024, 1024)
EXPECTED_MODEL_INPUT_SHAPE = [1, 3, 1024, 1024]
EXPECTED_MODEL_OUTPUT_SHAPE = [1, 1, 1024, 1024]
EXPECTED_MODEL_MD5 = "6f184e756bb3bd901c8849220a83e38e"
INPUT_MEAN = (0.485, 0.456, 0.406)
INPUT_STD = (1.0, 1.0, 1.0)
PINK_RGB = (255, 105, 180)
PREVIEW_QUALITY = 95
SCHEMA = "si-game-cutout-isnet-anime-report/v1"

# Fixed writes only. The source, model, runtime assets, releases, mobile copies,
# old drafts, and other art are never write targets.
WRITE_TARGETS = (DEFAULT_OUTPUT, DEFAULT_PREVIEW, DEFAULT_REPORT)

# Kept here so the report can state the non-destructive policy explicitly. The
# fixed target guard means these paths are never candidates for writing.
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

# Read-only or older-draft files that must never be replaced, even if a future
# edit accidentally changes one of the fixed output constants.
PROTECTED_FILES = (
    CANONICAL_INPUT,
    CANONICAL_MODEL,
    ROOT / "art-source" / "v1.3" / "si-game-cutout-draft-v1.png",
    ROOT / "art-source" / "v1.3" / "si-game-cutout-model-draft-v1.png",
    ROOT / "qa" / "si-game-cutout-draft-v1-report.json",
    ROOT / "qa" / "si-game-cutout-draft-v1-pink-preview.jpg",
    ROOT / "qa" / "si-game-cutout-model-draft-v1-report.json",
    ROOT / "qa" / "si-game-cutout-model-draft-v1-pink-preview.jpg",
)

# Hard stop for the explicitly forbidden output tree. Write targets are already
# fixed under ROOT, but this makes the policy executable rather than a comment.
FORBIDDEN_ROOTS = (Path(r"D:\终末地小祀"),)

LIMITATIONS = [
    "isnet-anime 是面向动漫角色的显著性分割模型；细角、墨翼、发丝、薄纱、半透明衣料和与角色同色的背景仍可能漏检或边缘粗糙，需以全尺寸 RGBA 和粉色预览人工复核。",
    "本工具只生成 alpha，不生成或修饰任何 RGB；透明和半透明像素下方的 RGB 保留源截图原值，便于复查，但不代表这些隐藏颜色适合直接用于新背景。",
    "默认 alpha 是模型输出的 min-max 归一化结果，不是专业 trimap/matting；高光、烟雾、景深以及角色边缘的半透明过渡仍可能有误差。",
    "使用 --threshold 时 alpha 会被二值化为 0/255，柔边会丢失；默认不传该参数即可保留模型原始软 mask。",
    "粉色底 JPG 仅用于快速查看，存在 JPEG 压缩；验收和后续处理应以 RGBA PNG 为准。",
    "本工具不读取、不相与旧 GrabCut 或其他旧草稿蒙版，也不使用人工多边形或前景种子。",
    "模型固定为本地 rembg 官方 isnet-anime.onnx，不联网、不重训；运行前会校验 MD5 6f184e756bb3bd901c8849220a83e38e，且只接受 1024x1024 输入/首输出契约，形状不符会明确报错而不猜测。",
]


def _hash_file(path: Path, algorithm: str) -> str:
    digest = hashlib.new(algorithm)
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _sha256(path: Path) -> str:
    return _hash_file(path, "sha256")


def _md5(path: Path) -> str:
    return _hash_file(path, "md5")


def _relative(path: Path) -> str:
    return path.resolve().relative_to(ROOT.resolve()).as_posix()


def _is_under(path: Path, root: Path) -> bool:
    try:
        path.resolve().relative_to(root.resolve())
    except (OSError, ValueError):
        return False
    return True


def _guard_targets(overwrite: bool) -> list[str]:
    """Validate only the three fixed isnet-draft-v1 targets can be written."""
    for path in WRITE_TARGETS:
        # Fixed paths are used, but still reject a symlinked parent or leaf so a
        # write cannot be redirected into original art or a release directory.
        if not _is_under(path, ROOT):
            raise SystemExit(f"Write target escapes the project: {path}")
        for forbidden in FORBIDDEN_ROOTS:
            if _is_under(path, forbidden):
                raise SystemExit(
                    f"Refusing to write into forbidden path: {forbidden}"
                )
        for protected in PROTECTED_DIRS:
            if _is_under(path, protected):
                raise SystemExit(
                    f"Refusing to write into protected path: {_relative(protected)}"
                )
        if path.resolve() in {item.resolve() for item in PROTECTED_FILES}:
            raise SystemExit(f"Write target is a protected file: {_relative(path)}")
        if path.is_symlink():
            raise SystemExit(f"Refusing to write through a symlink: {path}")
        for parent in path.parents:
            if parent.resolve() == ROOT.resolve():
                break
            if parent.is_symlink():
                raise SystemExit(
                    f"Refusing to write through a symlinked directory: {parent}"
                )
        if path.exists() and path.is_dir():
            raise SystemExit(f"Write target is a directory: {path}")

    existing = [_relative(path) for path in WRITE_TARGETS if path.exists()]
    if existing and not overwrite:
        names = ", ".join(existing)
        raise SystemExit(
            "Refusing to overwrite existing output(s): "
            f"{names}. Pass --overwrite to replace only the three fixed "
            "si-game-cutout-isnet-draft-v1 outputs."
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
        raise SystemExit(f"Expected isnet-anime model is missing: {CANONICAL_MODEL}")
    if CANONICAL_INPUT.resolve() == CANONICAL_MODEL.resolve():
        raise SystemExit("Source screenshot and model resolve to the same file")
    targets = {path.resolve() for path in WRITE_TARGETS}
    if CANONICAL_INPUT.resolve() in targets:
        raise SystemExit("Source screenshot collides with an output path")
    if CANONICAL_MODEL.resolve() in targets:
        raise SystemExit("Model collides with an output path")


def _read_source() -> tuple[np.ndarray, str]:
    sha_before = _sha256(CANONICAL_INPUT)
    with Image.open(CANONICAL_INPUT) as image:
        if image.mode != "RGBA":
            raise SystemExit(f"Expected an RGBA source, got {image.mode}")
        if image.size != EXPECTED_SOURCE_SIZE:
            raise SystemExit(
                f"Expected {EXPECTED_SOURCE_SIZE[0]}x{EXPECTED_SOURCE_SIZE[1]}, "
                f"got {image.width}x{image.height}"
            )
        image.load()
        source = np.array(image, dtype=np.uint8).copy()

    sha_after_read = _sha256(CANONICAL_INPUT)
    if sha_before != sha_after_read:
        raise SystemExit("Source screenshot changed while it was being read")
    expected_shape = (EXPECTED_SOURCE_SIZE[1], EXPECTED_SOURCE_SIZE[0], 4)
    if source.shape != expected_shape:
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


def _dim_matches(value: Any, expected: int) -> bool:
    """Accept a fixed expected dimension or a dynamic (-1/None/str) dimension."""
    return not isinstance(value, int) or value in (-1, expected)


def _validate_input_info(info: Any) -> None:
    if info.type != "tensor(float)":
        raise SystemExit(
            f"Expected float32 input, got {info.type} for '{info.name}'"
        )
    shape = list(info.shape) if info.shape is not None else None
    if shape is None or len(shape) != 4:
        raise SystemExit(
            "Expected a 4-D NCHW isnet-anime input, got "
            f"{shape} for '{info.name}'"
        )
    if not _dim_matches(shape[0], EXPECTED_MODEL_INPUT_SHAPE[0]):
        raise SystemExit(
            f"Expected input batch 1 or dynamic, got {shape[0]} for '{info.name}'"
        )
    if not _dim_matches(shape[1], EXPECTED_MODEL_INPUT_SHAPE[1]):
        raise SystemExit(
            f"Expected input channels 3 or dynamic, got {shape[1]} for '{info.name}'"
        )
    for dim in shape[2:]:
        if not _dim_matches(dim, MODEL_INPUT_SIZE[0]):
            raise SystemExit(
                "Expected input spatial size 1024x1024 or dynamic, got "
                f"{shape} for '{info.name}'. The model is not the expected "
                "isnet-anime graph."
            )


def _validate_output_info(info: Any) -> None:
    if info.type != "tensor(float)":
        raise SystemExit(
            f"Expected float32 primary output, got {info.type} for '{info.name}'"
        )
    shape = list(info.shape) if info.shape is not None else None
    if shape is None or len(shape) != 4:
        raise SystemExit(
            "Expected a 4-D primary output, got "
            f"{shape} for '{info.name}'"
        )
    if not _dim_matches(shape[0], EXPECTED_MODEL_OUTPUT_SHAPE[0]):
        raise SystemExit(
            f"Expected output batch 1 or dynamic, got {shape[0]} for '{info.name}'"
        )
    if not _dim_matches(shape[1], EXPECTED_MODEL_OUTPUT_SHAPE[1]):
        raise SystemExit(
            f"Expected output channels 1 or dynamic, got {shape[1]} for "
            f"'{info.name}'"
        )
    for dim in shape[2:]:
        if not _dim_matches(dim, MODEL_INPUT_SIZE[0]):
            raise SystemExit(
                "Expected output spatial size 1024x1024 or dynamic, got "
                f"{shape} for '{info.name}'. The model is not the expected "
                "isnet-anime graph."
            )


def _create_session(
    threads: int,
) -> tuple[Any, dict[str, Any], str, str]:
    try:
        import onnxruntime as ort
    except ImportError as exc:
        raise SystemExit(
            "onnxruntime is unavailable in the active interpreter; use the QA "
            "environment containing numpy/Pillow/onnxruntime."
        ) from exc

    if "CPUExecutionProvider" not in ort.get_available_providers():
        raise SystemExit("onnxruntime CPUExecutionProvider is not available")

    model_md5_before = _md5(CANONICAL_MODEL)
    if model_md5_before != EXPECTED_MODEL_MD5:
        raise SystemExit(
            "Model MD5 mismatch: expected "
            f"{EXPECTED_MODEL_MD5}, got {model_md5_before}. Refusing to run on "
            "a different model."
        )
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
            "Expected one isnet-anime input, got "
            f"{len(inputs)}: {[item.name for item in inputs]}. "
            "Refusing to guess which input to use."
        )
    if not outputs:
        raise SystemExit("The ONNX model exposes no outputs")

    input_info = inputs[0]
    _validate_input_info(input_info)
    _validate_output_info(outputs[0])

    model_md5_after_load = _md5(CANONICAL_MODEL)
    model_sha_after_load = _sha256(CANONICAL_MODEL)
    if model_md5_before != model_md5_after_load:
        raise SystemExit("Model MD5 changed while it was being loaded")
    if model_sha_before != model_sha_after_load:
        raise SystemExit("Model file changed while it was being loaded")

    metadata = {
        "input": _meta(input_info),
        "outputs": [_meta(info) for info in outputs],
        "primary_output": outputs[0].name,
        "output_count": len(outputs),
        "onnxruntime_version": ort.__version__,
    }
    return session, metadata, model_md5_before, model_sha_before


def _preprocess(source_rgb: np.ndarray) -> tuple[np.ndarray, dict[str, Any]]:
    """Reproduce rembg BaseSession.normalize exactly for isnet-anime."""
    rgb_array = np.ascontiguousarray(source_rgb, dtype=np.uint8)
    image = Image.fromarray(rgb_array)
    if image.mode != "RGB":
        raise SystemExit(f"Expected an RGB preprocessing image, got {image.mode}")

    resized = image.convert("RGB").resize(
        MODEL_INPUT_SIZE, Image.Resampling.LANCZOS
    )
    im_ary = np.array(resized)
    max_pixel = float(np.max(im_ary))
    denominator = max(max_pixel, 1e-6)
    im_ary = im_ary / denominator

    tmp = np.zeros((im_ary.shape[0], im_ary.shape[1], 3))
    for channel, (mean, std) in enumerate(zip(INPUT_MEAN, INPUT_STD)):
        tmp[:, :, channel] = (im_ary[:, :, channel] - mean) / std

    tmp = tmp.transpose((2, 0, 1))
    tensor = np.ascontiguousarray(
        np.expand_dims(tmp, 0).astype(np.float32)
    )
    expected = tuple(EXPECTED_MODEL_INPUT_SHAPE)
    if tensor.shape != expected:
        raise SystemExit(
            f"Preprocessed tensor shape is {tensor.shape}, expected {expected}"
        )

    return tensor, {
        "input_resize": "PIL LANCZOS, direct 920x1540 -> 1024x1024",
        "input_layout": "NCHW",
        "input_dtype": "float32",
        "input_scale": "uint8 RGB divided by max pixel (rembg base.normalize)",
        "normalization_mean_rgb": list(INPUT_MEAN),
        "normalization_std_rgb": list(INPUT_STD),
        "max_pixel": max_pixel,
        "denominator": denominator,
        "tensor_shape": list(tensor.shape),
    }


def _infer(
    session: Any,
    input_name: str,
    output_name: str,
    tensor: np.ndarray,
) -> tuple[np.ndarray, np.ndarray, dict[str, Any]]:
    """Reproduce rembg DisSession.predict exactly, including the mask resize."""
    try:
        raw = session.run([output_name], {input_name: tensor})[0]
    except Exception as exc:
        raise SystemExit(f"ONNX CPU inference failed: {exc}") from exc

    output = np.asarray(raw)
    if output.ndim != 4:
        raise SystemExit(
            f"Runtime output must be 4-D, got shape {list(output.shape)}"
        )
    if output.shape[0] != 1 or output.shape[1] != 1:
        raise SystemExit(
            "Runtime output must be 1x1xHxW, got "
            f"{list(output.shape)}"
        )
    if output.shape[2] < 2 or output.shape[3] < 2:
        raise SystemExit(
            f"Runtime output spatial size is invalid: {list(output.shape)}"
        )
    if output.dtype != np.float32:
        raise SystemExit(
            f"Runtime output dtype is {output.dtype}, expected float32"
        )
    if not np.all(np.isfinite(output)):
        raise SystemExit("Model output contains NaN or infinity")

    pred = output[:, 0, :, :]
    raw_min = float(np.min(pred))
    raw_max = float(np.max(pred))
    ma = np.max(pred)
    mi = np.min(pred)
    if not np.isfinite(ma) or not np.isfinite(mi):
        raise SystemExit("Model output min/max is not finite")
    if float(ma - mi) < 1e-8:
        raise SystemExit("Model output is constant; cannot build an alpha mask")

    # Keep the rembg expression order and float32 arithmetic.
    pred = (pred - mi) / (ma - mi)
    pred = np.squeeze(pred)
    if pred.ndim != 2:
        raise SystemExit(
            f"Expected a 2-D squeezed mask, got shape {list(pred.shape)}"
        )

    mask_1024_image = Image.fromarray((pred * 255).astype("uint8"))
    if mask_1024_image.mode != "L":
        raise SystemExit(f"Expected an L-mode model mask, got {mask_1024_image.mode}")
    mask_1024 = np.array(mask_1024_image, dtype=np.uint8).copy()

    full_mask_image = mask_1024_image.resize(
        EXPECTED_SOURCE_SIZE, Image.Resampling.LANCZOS
    )
    alpha = np.array(full_mask_image, dtype=np.uint8).copy()
    expected_alpha_shape = (EXPECTED_SOURCE_SIZE[1], EXPECTED_SOURCE_SIZE[0])
    if alpha.shape != expected_alpha_shape:
        raise SystemExit(
            f"Unexpected resized alpha shape: {alpha.shape}"
        )

    meta = {
        "provider": "onnxruntime CPUExecutionProvider",
        "output_postprocess": (
            "first output[:, 0, :, :], min-max normalize, *255 uint8 L, "
            "PIL LANCZOS 1024x1024 -> 920x1540"
        ),
        "output_shape": list(output.shape),
        "output_dtype": str(output.dtype),
        "raw_output_min": raw_min,
        "raw_output_max": raw_max,
        "normalized_output_min": float(np.min(pred)),
        "normalized_output_max": float(np.max(pred)),
        "mask_1024_shape": list(mask_1024.shape),
        "alpha_shape": list(alpha.shape),
    }
    return alpha, mask_1024, meta


def _apply_threshold(
    alpha: np.ndarray, threshold: float | None
) -> tuple[np.ndarray, dict[str, Any]]:
    if threshold is None:
        return alpha, {
            "alpha_mode": "direct",
            "threshold": None,
            "threshold_cutoff_255": None,
            "transition": "model mask used directly as alpha",
        }

    cutoff = float(threshold) * 255.0
    binary = np.where(alpha.astype(np.float32) >= cutoff, 255, 0).astype(np.uint8)
    return binary, {
        "alpha_mode": "binary_threshold",
        "threshold": float(threshold),
        "threshold_cutoff_255": cutoff,
        "transition": "alpha >= threshold * 255 becomes 255, otherwise 0",
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
        "foreground_coverage_percent": round(
            (total - transparent) / total * 100, 4
        ),
        "foreground_on_image_border": bool(
            np.any(alpha[0, :])
            or np.any(alpha[-1, :])
            or np.any(alpha[:, 0])
            or np.any(alpha[:, -1])
        ),
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
        if image.size != EXPECTED_SOURCE_SIZE:
            raise SystemExit(
                f"Saved PNG size is {image.width}x{image.height}, expected "
                f"{EXPECTED_SOURCE_SIZE[0]}x{EXPECTED_SOURCE_SIZE[1]}"
            )
        image.load()
        saved = np.array(image, dtype=np.uint8).copy()
    if not np.array_equal(saved[..., 3], alpha):
        raise SystemExit("Saved PNG alpha differs from the computed alpha")
    fidelity = _rgb_fidelity(saved, source)
    if not fidelity["all_pixels_rgb_identical_to_source"]:
        raise SystemExit("Saved PNG RGB fidelity check failed")
    return saved, fidelity


def _verify_saved_jpeg(path: Path) -> None:
    with Image.open(path) as image:
        if image.mode != "RGB":
            raise SystemExit(f"Saved preview mode is {image.mode}, expected RGB")
        if image.size != EXPECTED_SOURCE_SIZE:
            raise SystemExit(
                f"Saved preview size is {image.width}x{image.height}, expected "
                f"{EXPECTED_SOURCE_SIZE[0]}x{EXPECTED_SOURCE_SIZE[1]}"
            )
        image.load()


def _validate_args(args: argparse.Namespace) -> None:
    if args.threshold is not None and not 0.0 <= args.threshold <= 1.0:
        raise SystemExit("--threshold must be in [0, 1]")
    if args.threads < 1:
        raise SystemExit("--threads must be >= 1")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Non-destructive isnet-anime cutout of qa/si-body-reference-071.png. "
            "Only the three fixed si-game-cutout-isnet-draft-v1 outputs are written."
        ),
        epilog=(
            "Existing isnet-draft-v1 outputs are refused unless --overwrite is "
            "passed. The source, model, original art, assets, release, mobile, "
            "and old draft paths are never write targets. No network access, "
            "cloud inference, or model training is performed."
        ),
    )
    parser.add_argument(
        "--threshold",
        type=float,
        default=None,
        help=(
            "Optional binary threshold in [0, 1] applied to the 0-255 model "
            "mask; omit to use the direct soft mask (default: omitted)"
        ),
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
            "Replace only the three fixed isnet-draft-v1 outputs; never "
            "originals, assets, release, mobile, source, model, or old drafts"
        ),
    )
    args = parser.parse_args(argv)
    _validate_args(args)

    _guard_sources()
    preexisting_targets = _guard_targets(args.overwrite)

    source, source_sha_before = _read_source()
    session, model_meta, model_md5, model_sha_before = _create_session(
        args.threads
    )
    tensor, preprocess_meta = _preprocess(source[..., :3])
    alpha_model, mask_1024, infer_meta = _infer(
        session,
        input_name=model_meta["input"]["name"],
        output_name=model_meta["primary_output"],
        tensor=tensor,
    )
    alpha, alpha_conversion = _apply_threshold(alpha_model, args.threshold)
    alpha_stats = _alpha_report(alpha)
    if alpha_stats["foreground_pixels"] == 0:
        raise SystemExit("isnet-anime produced no foreground pixels")

    output = source.copy()
    output[..., 3] = alpha
    preview_rgb = _pink_preview(output)

    source_sha_after_inference = _sha256(CANONICAL_INPUT)
    model_md5_after_inference = _md5(CANONICAL_MODEL)
    model_sha_after_inference = _sha256(CANONICAL_MODEL)
    if source_sha_before != source_sha_after_inference:
        raise SystemExit("Source screenshot changed during inference")
    if model_md5 != model_md5_after_inference:
        raise SystemExit("Model MD5 changed during inference")
    if model_sha_before != model_sha_after_inference:
        raise SystemExit("Model file changed during inference")

    tmp_output = _temp_path(DEFAULT_OUTPUT)
    tmp_preview = _temp_path(DEFAULT_PREVIEW)
    tmp_report = _temp_path(DEFAULT_REPORT)
    try:
        _save_png(tmp_output, output)
        saved, fidelity = _verify_saved_png(tmp_output, alpha, source)
        _save_jpeg(tmp_preview, preview_rgb)
        _verify_saved_jpeg(tmp_preview)

        # Recheck immediately before committing. Hash fields in the report use
        # these final pre-commit values.
        source_sha_after = _sha256(CANONICAL_INPUT)
        model_md5_after = _md5(CANONICAL_MODEL)
        model_sha_after = _sha256(CANONICAL_MODEL)
        if source_sha_before != source_sha_after:
            raise SystemExit("Source screenshot changed before outputs were written")
        if model_md5 != model_md5_after:
            raise SystemExit("Model MD5 changed before outputs were written")
        if model_sha_before != model_sha_after:
            raise SystemExit("Model file changed before outputs were written")
        if not args.overwrite:
            for path in WRITE_TARGETS:
                if path.exists():
                    raise SystemExit(
                        "Refusing to overwrite file created during the run: "
                        f"{path}"
                    )

        output_sha = _sha256(tmp_output)
        preview_sha = _sha256(tmp_preview)
        report = {
            "schema": SCHEMA,
            "created_at_utc": datetime.now(timezone.utc).isoformat(),
            "summary": {
                "foreground_pixels": alpha_stats["foreground_pixels"],
                "foreground_coverage_percent": alpha_stats[
                    "foreground_coverage_percent"
                ],
                "alpha_min": alpha_stats["alpha_min"],
                "alpha_max": alpha_stats["alpha_max"],
                "model_md5": model_md5,
                "model_md5_matches_expected": model_md5 == EXPECTED_MODEL_MD5,
            },
            "tool": {
                "path": _relative(TOOL_PATH),
                "sha256": _sha256(TOOL_PATH),
            },
            "source": {
                "path": _relative(CANONICAL_INPUT),
                "sha256_before_run": source_sha_before,
                "sha256_after_run": source_sha_after,
                "unchanged": source_sha_before == source_sha_after,
                "size": list(EXPECTED_SOURCE_SIZE),
                "mode": "RGBA",
                "alpha_all_255": True,
                "bytes": CANONICAL_INPUT.stat().st_size,
            },
            "model": {
                "path": _relative(CANONICAL_MODEL),
                "official_release": "rembg isnet-anime",
                "md5": model_md5,
                "md5_expected": EXPECTED_MODEL_MD5,
                "md5_matches_expected": model_md5 == EXPECTED_MODEL_MD5,
                "sha256_before_run": model_sha_before,
                "sha256_after_run": model_sha_after,
                "unchanged": model_sha_before == model_sha_after,
                "bytes": CANONICAL_MODEL.stat().st_size,
                "input": model_meta["input"],
                "outputs": model_meta["outputs"],
                "output_count": model_meta["output_count"],
                "primary_output": model_meta["primary_output"],
                "auxiliary_outputs_used": False,
            },
            "inference": {
                "provider": infer_meta["provider"],
                "onnxruntime_version": model_meta["onnxruntime_version"],
                "intra_op_num_threads": args.threads,
                "inter_op_num_threads": 1,
                "execution_mode": "ORT_SEQUENTIAL",
                "graph_optimization_level": "ORT_ENABLE_ALL",
                "model_input_size": list(MODEL_INPUT_SIZE),
                **preprocess_meta,
                **infer_meta,
            },
            "parameters": {
                "threshold": args.threshold,
                "overwrite_requested": bool(args.overwrite),
                "grabcut_used": False,
                "old_draft_masks_used": False,
                "manual_polygons_or_seeds_used": False,
                "cloud_inference_used": False,
                "rembg_imported": False,
            },
            "mask": {
                "model_mask_1024": _distribution(mask_1024),
                "alpha": alpha_stats,
            },
            "alpha_conversion": alpha_conversion,
            "pixel_fidelity": fidelity,
            "checks": {
                "source_sha_unchanged": source_sha_before == source_sha_after,
                "model_md5_matches_expected": model_md5 == EXPECTED_MODEL_MD5,
                "model_md5_unchanged": model_md5 == model_md5_after,
                "model_sha_unchanged": model_sha_before == model_sha_after,
                "model_shape_and_dtype_match": True,
                "output_rgba_round_trip": True,
                "output_alpha_matches": True,
                "foreground_rgb_identical": fidelity[
                    "foreground_rgb_identical_to_source"
                ],
                "all_rgb_identical": fidelity["all_pixels_rgb_identical_to_source"],
            },
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
                "size": list(EXPECTED_SOURCE_SIZE),
                "mode": "RGB",
                "format": "JPEG",
                "quality": PREVIEW_QUALITY,
                "subsampling": 0,
                "background_rgb": list(PINK_RGB),
                "note": "Lossy preview only; inspect the RGBA PNG for acceptance.",
            },
            "report": {"path": _relative(DEFAULT_REPORT)},
            "software": {
                "python": sys.version.split()[0],
                "numpy": np.__version__,
                "pillow": pillow_version,
                "onnxruntime": model_meta["onnxruntime_version"],
            },
            "non_destructive": {
                "fixed_inputs_only": True,
                "fixed_write_targets_only": True,
                "write_targets": [_relative(path) for path in WRITE_TARGETS],
                "protected_directories": [
                    _relative(path) for path in PROTECTED_DIRS
                ],
                "protected_files": [_relative(path) for path in PROTECTED_FILES],
                "forbidden_roots": [str(path) for path in FORBIDDEN_ROOTS],
                "overwrite_requested": bool(args.overwrite),
                "existing_outputs_before_run": preexisting_targets,
                "existing_outputs_replaced": preexisting_targets,
                "originals_assets_release_mobile_old_drafts_written": False,
                "network_used": False,
                "cloud_inference_used": False,
                "model_downloaded": False,
                "model_retrained": False,
            },
            "limitations": LIMITATIONS,
        }
        _save_json(tmp_report, report)

        source_sha_final = _sha256(CANONICAL_INPUT)
        model_md5_final = _md5(CANONICAL_MODEL)
        model_sha_final = _sha256(CANONICAL_MODEL)
        if source_sha_before != source_sha_final:
            raise SystemExit("Source screenshot changed before report commit")
        if model_md5 != model_md5_final:
            raise SystemExit("Model MD5 changed before report commit")
        if model_sha_before != model_sha_final:
            raise SystemExit("Model file changed before report commit")

        for target, temporary in (
            (DEFAULT_OUTPUT, tmp_output),
            (DEFAULT_PREVIEW, tmp_preview),
            (DEFAULT_REPORT, tmp_report),
        ):
            os.replace(temporary, target)

        # Final post-commit verification. If anything moved or changed, fail
        # loudly rather than reporting success.
        if _sha256(DEFAULT_OUTPUT) != output_sha:
            raise SystemExit("Committed PNG hash differs from the verified temp file")
        if _sha256(DEFAULT_PREVIEW) != preview_sha:
            raise SystemExit(
                "Committed preview hash differs from the verified temp file"
            )
        _verify_saved_png(DEFAULT_OUTPUT, alpha, source)
        if _sha256(CANONICAL_INPUT) != source_sha_before:
            raise SystemExit("Source screenshot changed after outputs were committed")
        if _md5(CANONICAL_MODEL) != model_md5:
            raise SystemExit("Model MD5 changed after outputs were committed")
        if _sha256(CANONICAL_MODEL) != model_sha_before:
            raise SystemExit("Model file changed after outputs were committed")
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
                "alpha_min": alpha_stats["alpha_min"],
                "alpha_max": alpha_stats["alpha_max"],
                "model_md5": model_md5,
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

#!/usr/bin/env python3
"""Formalize the verified isnet color-key trial v3 as a non-destructive tool.

Fixed inputs, read only:
  - qa/si-body-reference-071.png                    RGB source only
  - art-source/v1.3/si-game-cutout-isnet-draft-v1.png
                                                    alpha only

Fixed outputs, never any other path:
  - art-source/v1.3/si-game-cutout-draft-v2.png
  - qa/si-game-cutout-draft-v2-pink-preview.jpg
  - qa/si-game-cutout-draft-v2-desktop-size.png
  - qa/si-game-cutout-draft-v2-report.json

The cutout keeps every RGB byte from the source screenshot. Only the alpha
channel is changed, using the exact steps from qa/si-isnet-color-key-trial-v3.png:

  1. Start with the ISNet draft alpha. Zero it where the source RGB matches the
     background color key and the initial alpha is below 235:
       (10 <= r <= 48) & (25 <= g <= 65) & (40 <= b <= 88) &
       (8 <= b-g <= 28) & (7 <= g-r <= 28) & (isnet_alpha < 235)
  2. Set alpha below 80 to 0. Rescale the rest with
       floor((alpha - 80) * 255 / 175)
     and cast to uint8.
  3. Run cv2.connectedComponentsWithStats on alpha > 32, connectivity 8. Keep
     only components whose area is at least 900 pixels; zero every other alpha.

The pink JPG is a lossy visual preview. The 384x416 PNG is a static fit preview
on light grey only; it is not an animation frame, atlas, or runtime asset.

A rerun refuses to overwrite any of the four outputs unless --overwrite is
passed. The tool never writes the source screenshot, the ISNet draft, older
drafts, runtime assets, release payloads, mobile copies, or D:\\终末地小祀.

Run with the local environment that contains numpy/Pillow/OpenCV, for example:
  .venv\\Scripts\\python.exe tools\\refine_game_cutout.py
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
if str(CV2_DEPS) not in sys.path:
    sys.path.insert(0, str(CV2_DEPS))
try:
    import cv2
except ImportError as exc:
    raise SystemExit(
        "cv2 is unavailable. Expected OpenCV in qa/segmentation-deps and "
        "numpy/Pillow in the active interpreter."
    ) from exc

CANONICAL_SOURCE = ROOT / "qa" / "si-body-reference-071.png"
CANONICAL_ISNET = (
    ROOT / "art-source" / "v1.3" / "si-game-cutout-isnet-draft-v1.png"
)
DEFAULT_OUTPUT = ROOT / "art-source" / "v1.3" / "si-game-cutout-draft-v2.png"
DEFAULT_PINK_PREVIEW = (
    ROOT / "qa" / "si-game-cutout-draft-v2-pink-preview.jpg"
)
DEFAULT_DESKTOP_PREVIEW = (
    ROOT / "qa" / "si-game-cutout-draft-v2-desktop-size.png"
)
DEFAULT_REPORT = ROOT / "qa" / "si-game-cutout-draft-v2-report.json"

EXPECTED_SIZE = (920, 1540)
PINK_RGB = (255, 105, 180)
DESKTOP_SIZE = (384, 416)
DESKTOP_BACKGROUND_RGB = (232, 232, 232)
PREVIEW_QUALITY = 95
KEY_ALPHA_LIMIT = 235
LOW_ALPHA_CUTOFF = 80
RESCALE_NUMERATOR = 255
RESCALE_DENOMINATOR = 175
COMPONENT_ALPHA_THRESHOLD = 32
COMPONENT_CONNECTIVITY = 8
MIN_COMPONENT_AREA = 900
SCHEMA = "si-game-cutout-draft-report/v2"

WRITE_TARGETS = (
    DEFAULT_OUTPUT,
    DEFAULT_PINK_PREVIEW,
    DEFAULT_DESKTOP_PREVIEW,
    DEFAULT_REPORT,
)

# Fixed target guard. These paths are never candidates for writing.
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

PROTECTED_FILES = (
    CANONICAL_SOURCE,
    CANONICAL_ISNET,
    ROOT / "art-source" / "v1.3" / "si-game-cutout-draft-v1.png",
    ROOT / "art-source" / "v1.3" / "si-game-cutout-model-draft-v1.png",
    ROOT / "qa" / "si-game-cutout-draft-v1-report.json",
    ROOT / "qa" / "si-game-cutout-draft-v1-pink-preview.jpg",
    ROOT / "qa" / "si-game-cutout-model-draft-v1-report.json",
    ROOT / "qa" / "si-game-cutout-model-draft-v1-pink-preview.jpg",
    ROOT / "qa" / "si-game-cutout-isnet-draft-v1-report.json",
    ROOT / "qa" / "si-game-cutout-isnet-draft-v1-pink-preview.jpg",
    ROOT / "qa" / "si-isnet-color-key-trial-v2.png",
    ROOT / "qa" / "si-isnet-color-key-trial-v2.jpg",
    ROOT / "qa" / "si-isnet-color-key-trial-v3.png",
    ROOT / "qa" / "si-isnet-color-key-trial-v3.jpg",
    ROOT / "qa" / "si-isnet-grabcut-trial-v4.png",
    ROOT / "qa" / "si-isnet-grabcut-trial-v4.jpg",
)

FORBIDDEN_ROOTS = (Path(r"D:\终末地小祀"),)

LIMITATIONS = [
    "肩部、发丝和鞋边仍可能有漏切或残片；必须用全尺寸 RGBA、粉色底和浅灰底预览逐处人工复核。",
    "颜色键只覆盖偏蓝灰的背景范围，角色身上相近颜色的细节可能被误删，背景中同色残片也可能被保留。",
    "alpha<80 清零会去掉半透明软边；面积 900 的连通域过滤可能删掉细小分离部件，也可能保留不需要的背景残片。",
    "所有 RGB 都逐字节继承原始截图，包括透明像素下方的 RGB；本工具不重绘、不生成也不修饰脸、肤色、墨纹或尾巴。",
    "粉色底 JPG 有压缩损失，只用于快速查看；验收应以 RGBA PNG 为准。",
    "qa/si-game-cutout-draft-v2-desktop-size.png 只是 384x416 的静态尺寸对照图，缩放并居中在浅灰底上，不是动画帧、图集或运行素材，不能直接用于真实动画。",
    "本工具不读取旧草稿蒙版，不调用云端或生成模型，不修改运行图集、assets、release、mobile 或原图；默认拒绝覆盖同名输出。",
]


def _hash_file(path: Path, algorithm: str) -> str:
    digest = hashlib.new(algorithm)
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _sha256(path: Path) -> str:
    return _hash_file(path, "sha256")


def _sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _relative(path: Path) -> str:
    return path.resolve().relative_to(ROOT.resolve()).as_posix()


def _is_under(path: Path, root: Path) -> bool:
    try:
        path.resolve().relative_to(root.resolve())
    except (OSError, ValueError):
        return False
    return True


def _guard_targets(overwrite: bool) -> list[str]:
    """Allow only the four fixed draft-v2 targets, and refuse by default."""
    resolved_targets = [path.resolve() for path in WRITE_TARGETS]
    if len(set(resolved_targets)) != len(resolved_targets):
        raise SystemExit("Output targets are not distinct")

    for path in WRITE_TARGETS:
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
            f"{names}. Pass --overwrite to replace only the four fixed "
            "si-game-cutout-draft-v2 outputs."
        )
    return existing


def _guard_sources() -> None:
    for path in (CANONICAL_SOURCE, CANONICAL_ISNET):
        if path.is_symlink():
            raise SystemExit(f"Input path must not be a symlink: {path}")
        if not path.is_file():
            raise SystemExit(f"Expected fixed input is missing: {path}")
    if CANONICAL_SOURCE.resolve() == CANONICAL_ISNET.resolve():
        raise SystemExit("Source screenshot and ISNet draft resolve to one file")

    targets = {path.resolve() for path in WRITE_TARGETS}
    if CANONICAL_SOURCE.resolve() in targets:
        raise SystemExit("Source screenshot collides with an output path")
    if CANONICAL_ISNET.resolve() in targets:
        raise SystemExit("ISNet draft collides with an output path")


def _read_rgba(
    path: Path, label: str, require_opaque_alpha: bool
) -> tuple[np.ndarray, str]:
    sha_before = _sha256(path)
    try:
        with Image.open(path) as image:
            if image.mode != "RGBA":
                raise SystemExit(f"{label} must be RGBA, got {image.mode}")
            if image.size != EXPECTED_SIZE:
                raise SystemExit(
                    f"{label} must be {EXPECTED_SIZE[0]}x{EXPECTED_SIZE[1]}, "
                    f"got {image.width}x{image.height}"
                )
            image.load()
            array = np.array(image, dtype=np.uint8).copy()
    except OSError as exc:
        raise SystemExit(f"Could not read {label}: {exc}") from exc

    if _sha256(path) != sha_before:
        raise SystemExit(f"{label} changed while it was being read")
    expected_shape = (EXPECTED_SIZE[1], EXPECTED_SIZE[0], 4)
    if array.shape != expected_shape:
        raise SystemExit(
            f"{label} array shape is {array.shape}, expected {expected_shape}"
        )
    if require_opaque_alpha and not np.all(array[..., 3] == 255):
        raise SystemExit(
            f"{label} alpha is not uniformly 255; refusing this screenshot"
        )
    return array, sha_before


def _alpha_stats(alpha: np.ndarray, include_histogram: bool) -> dict[str, Any]:
    total = int(alpha.size)
    transparent = int(np.count_nonzero(alpha == 0))
    opaque = int(np.count_nonzero(alpha == 255))
    partial = total - transparent - opaque
    ys, xs = np.nonzero(alpha)
    bbox = (
        [int(xs.min()), int(ys.min()), int(xs.max()) + 1, int(ys.max()) + 1]
        if xs.size
        else None
    )
    stats: dict[str, Any] = {
        "total_pixels": total,
        "transparent_pixels": transparent,
        "partial_pixels": partial,
        "foreground_pixels": total - transparent,
        "opaque_pixels": opaque,
        "alpha_min": int(alpha.min()),
        "alpha_max": int(alpha.max()),
        "alpha_mean": round(float(alpha.mean()), 6),
        "binary_alpha": bool(np.all((alpha == 0) | (alpha == 255))),
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
    if include_histogram:
        stats["histogram_256"] = [
            int(value)
            for value in np.bincount(alpha.reshape(-1), minlength=256).tolist()
        ]
    return stats


def _build_color_key(source_rgb: np.ndarray, initial_alpha: np.ndarray) -> np.ndarray:
    """Exact v3 background key, evaluated before any alpha rescale."""
    r = source_rgb[..., 0].astype(np.int16)
    g = source_rgb[..., 1].astype(np.int16)
    b = source_rgb[..., 2].astype(np.int16)
    return (
        (r >= 10)
        & (r <= 48)
        & (g >= 25)
        & (g <= 65)
        & (b >= 40)
        & (b <= 88)
        & ((b - g) >= 8)
        & ((b - g) <= 28)
        & ((g - r) >= 7)
        & ((g - r) <= 28)
        & (initial_alpha < KEY_ALPHA_LIMIT)
    )


def _apply_key_and_rescale(
    source_rgb: np.ndarray, initial_alpha: np.ndarray
) -> tuple[np.ndarray, dict[str, Any]]:
    key = _build_color_key(source_rgb, initial_alpha)
    after_key = initial_alpha.copy()
    after_key[key] = 0

    low_alpha = after_key < LOW_ALPHA_CUTOFF
    scaled = np.zeros_like(after_key, dtype=np.uint8)
    high_alpha = ~low_alpha
    # Exact integer form of truncating (alpha - 80) * 255 / 175 to uint8.
    scaled[high_alpha] = (
        ((after_key[high_alpha].astype(np.uint16) - LOW_ALPHA_CUTOFF)
         * RESCALE_NUMERATOR)
        // RESCALE_DENOMINATOR
    ).astype(np.uint8)

    details = {
        "total_pixels": int(initial_alpha.size),
        "initial_isnet_alpha": _alpha_stats(
            initial_alpha, include_histogram=False
        ),
        "color_key_pixels": int(np.count_nonzero(key)),
        "color_key_pixels_with_nonzero_initial_alpha": int(
            np.count_nonzero(key & (initial_alpha > 0))
        ),
        "color_key_pixels_already_transparent": int(
            np.count_nonzero(key & (initial_alpha == 0))
        ),
        "low_alpha_pixels_before_rescale": int(np.count_nonzero(low_alpha)),
        "nonzero_low_alpha_pixels_zeroed": int(
            np.count_nonzero(low_alpha & (after_key > 0))
        ),
        "foreground_pixels_before_component_filter": int(
            np.count_nonzero(scaled)
        ),
        "alpha_after_key_and_rescale": _alpha_stats(
            scaled, include_histogram=False
        ),
    }
    return scaled, details


def _filter_components(alpha: np.ndarray) -> tuple[np.ndarray, dict[str, Any]]:
    binary = (alpha > COMPONENT_ALPHA_THRESHOLD).astype(np.uint8)
    count, labels, stats, _ = cv2.connectedComponentsWithStats(
        binary,
        connectivity=COMPONENT_CONNECTIVITY,
        ltype=cv2.CV_32S,
    )
    areas = stats[1:, cv2.CC_STAT_AREA].astype(np.int64)
    keep = np.zeros(count, dtype=bool)
    keep[1:] = areas >= MIN_COMPONENT_AREA

    filtered = alpha.copy()
    filtered[~keep[labels]] = 0

    retained = areas >= MIN_COMPONENT_AREA
    details = {
        "function": "cv2.connectedComponentsWithStats",
        "opencv_version": cv2.__version__,
        "mask": f"alpha > {COMPONENT_ALPHA_THRESHOLD}",
        "connectivity": COMPONENT_CONNECTIVITY,
        "min_area": MIN_COMPONENT_AREA,
        "components_total": int(areas.size),
        "components_retained": int(np.count_nonzero(retained)),
        "components_rejected": int(np.count_nonzero(~retained)),
        "pixels_in_retained_components": int(areas[retained].sum()),
        "pixels_in_rejected_components": int(areas[~retained].sum()),
        "largest_component_area": int(areas.max()) if areas.size else 0,
        "retained_component_areas_desc": [
            int(value) for value in sorted(areas[retained].tolist(), reverse=True)
        ],
        "foreground_pixels_after_component_filter": int(
            np.count_nonzero(filtered)
        ),
    }
    return filtered, details


def _composite_on_solid(rgba: np.ndarray, background: tuple[int, int, int]) -> np.ndarray:
    rgb = rgba[..., :3].astype(np.float32)
    alpha = rgba[..., 3].astype(np.float32)[..., None] / np.float32(255.0)
    background_array = np.asarray(background, dtype=np.float32)
    composited = rgb * alpha + background_array * (1.0 - alpha)
    return np.clip(np.rint(composited), 0, 255).astype(np.uint8)


def _desktop_preview(rgba: np.ndarray) -> tuple[np.ndarray, dict[str, Any]]:
    """Fit the full art into 384x416, keep aspect, center it on light grey."""
    height, width = rgba.shape[:2]
    canvas_width, canvas_height = DESKTOP_SIZE
    scale = min(canvas_width / width, canvas_height / height)
    target_width = max(1, int(round(width * scale)))
    target_height = max(1, int(round(height * scale)))

    # Keep the aspect ratio if independent rounding nudged a dimension over.
    if target_width > canvas_width:
        target_width = canvas_width
        target_height = max(1, int(round(height * target_width / width)))
    if target_height > canvas_height:
        target_height = canvas_height
        target_width = max(1, int(round(width * target_height / height)))

    # Composite before downscaling so hidden source RGB cannot create a fringe.
    full_rgb = _composite_on_solid(rgba, DESKTOP_BACKGROUND_RGB)
    content = Image.fromarray(full_rgb).resize(
        (target_width, target_height), Image.Resampling.LANCZOS
    )
    canvas = Image.new("RGB", DESKTOP_SIZE, DESKTOP_BACKGROUND_RGB)
    offset_x = (canvas_width - target_width) // 2
    offset_y = (canvas_height - target_height) // 2
    canvas.paste(content, (offset_x, offset_y))
    preview = np.array(canvas, dtype=np.uint8).copy()

    return preview, {
        "canvas_size": list(DESKTOP_SIZE),
        "content_size": [target_width, target_height],
        "content_offset_xy": [offset_x, offset_y],
        "scale": round(float(scale), 10),
        "scaling_filter": "PIL LANCZOS",
        "aspect_ratio_preserved": True,
        "background_rgb": list(DESKTOP_BACKGROUND_RGB),
        "mode": "RGB",
        "format": "PNG",
        "not_animation_frame": True,
        "not_runtime_asset": True,
        "note": (
            "Static 384x416 size check only; not an animation frame, atlas, or "
            "runtime desktop-pet asset."
        ),
    }


def _rgb_fidelity(output: np.ndarray, source: np.ndarray) -> dict[str, Any]:
    foreground = output[..., 3] > 0
    foreground_count = int(np.count_nonzero(foreground))
    output_rgb = output[..., :3]
    source_rgb = source[..., :3]
    if foreground_count:
        foreground_mismatch = int(
            np.count_nonzero(
                np.any(output_rgb[foreground] != source_rgb[foreground], axis=1)
            )
        )
    else:
        foreground_mismatch = 0
    all_mismatch = int(
        np.count_nonzero(np.any(output_rgb != source_rgb, axis=2))
    )
    return {
        "foreground_pixels_compared": foreground_count,
        "foreground_rgb_mismatch_pixels": foreground_mismatch,
        "foreground_rgb_identical_to_source": foreground_mismatch == 0,
        "all_pixels_rgb_mismatch_pixels": all_mismatch,
        "all_pixels_rgb_identical_to_source": all_mismatch == 0,
        "note": (
            "Every output RGB byte is copied from the source screenshot; only "
            "alpha is replaced. No RGB was generated or edited."
        ),
    }


def _temp_path(path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, name = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=str(path.parent)
    )
    os.close(descriptor)
    return Path(name)


def _save_png(path: Path, array: np.ndarray) -> None:
    Image.fromarray(array).save(path, format="PNG", optimize=True)


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


def _load_rgba(path: Path, label: str) -> np.ndarray:
    with Image.open(path) as image:
        if image.mode != "RGBA":
            raise SystemExit(f"{label} mode is {image.mode}, expected RGBA")
        if image.size != EXPECTED_SIZE:
            raise SystemExit(
                f"{label} size is {image.width}x{image.height}, expected "
                f"{EXPECTED_SIZE[0]}x{EXPECTED_SIZE[1]}"
            )
        image.load()
        return np.array(image, dtype=np.uint8).copy()


def _verify_saved_png(
    path: Path, alpha: np.ndarray, source: np.ndarray
) -> tuple[np.ndarray, dict[str, Any]]:
    saved = _load_rgba(path, "Saved cutout")
    if not np.array_equal(saved[..., 3], alpha):
        raise SystemExit("Saved PNG alpha differs from the computed alpha")
    fidelity = _rgb_fidelity(saved, source)
    if not fidelity["all_pixels_rgb_identical_to_source"]:
        raise SystemExit("Saved PNG RGB fidelity check failed")
    return saved, fidelity


def _verify_saved_pink(path: Path) -> None:
    with Image.open(path) as image:
        if image.mode != "RGB":
            raise SystemExit(f"Pink preview mode is {image.mode}, expected RGB")
        if image.size != EXPECTED_SIZE:
            raise SystemExit(
                f"Pink preview size is {image.width}x{image.height}, expected "
                f"{EXPECTED_SIZE[0]}x{EXPECTED_SIZE[1]}"
            )
        image.load()


def _verify_saved_desktop(path: Path) -> None:
    with Image.open(path) as image:
        if image.mode != "RGB":
            raise SystemExit(
                f"Desktop preview mode is {image.mode}, expected RGB"
            )
        if image.size != DESKTOP_SIZE:
            raise SystemExit(
                f"Desktop preview size is {image.width}x{image.height}, "
                f"expected {DESKTOP_SIZE[0]}x{DESKTOP_SIZE[1]}"
            )
        image.load()
        array = np.array(image, dtype=np.uint8).copy()
    background = np.asarray(DESKTOP_BACKGROUND_RGB, dtype=np.uint8)
    corners = (
        array[0, 0],
        array[0, -1],
        array[-1, 0],
        array[-1, -1],
    )
    if any(not np.array_equal(corner, background) for corner in corners):
        raise SystemExit("Desktop preview corners are not the fixed background")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Non-destructively formalize qa/si-isnet-color-key-trial-v3.png: "
            "use the source screenshot RGB plus the ISNet draft alpha, then "
            "write only the four fixed si-game-cutout-draft-v2 outputs."
        ),
        epilog=(
            "Existing draft-v2 outputs are refused unless --overwrite is "
            "passed. The source, ISNet draft, old drafts, assets, release, "
            "mobile, and trial files are never write targets. No image "
            "generation, cloud inference, DSH, or model configuration change "
            "is used."
        ),
    )
    parser.add_argument(
        "--overwrite",
        action="store_true",
        help=(
            "Replace only the four fixed draft-v2 outputs; never inputs, old "
            "drafts, runtime assets, release, or mobile files"
        ),
    )
    args = parser.parse_args(argv)

    _guard_sources()
    preexisting_targets = _guard_targets(args.overwrite)

    source, source_sha_before = _read_rgba(
        CANONICAL_SOURCE, "Source screenshot", require_opaque_alpha=True
    )
    isnet_draft, isnet_sha_before = _read_rgba(
        CANONICAL_ISNET, "ISNet draft", require_opaque_alpha=False
    )
    initial_alpha = isnet_draft[..., 3]
    isnet_alpha_sha = _sha256_bytes(initial_alpha.tobytes(order="C"))

    alpha_rescaled, key_details = _apply_key_and_rescale(
        source[..., :3], initial_alpha
    )
    alpha_filtered, component_details = _filter_components(alpha_rescaled)
    filtered_stats = _alpha_stats(alpha_filtered, include_histogram=False)
    if filtered_stats["foreground_pixels"] == 0:
        raise SystemExit("The color-key and component steps removed all foreground")
    if not filtered_stats["all_four_corners_transparent"]:
        raise SystemExit(
            "All four output corners must be transparent; got "
            f"{filtered_stats['four_corners']}"
        )

    output = source.copy()
    output[..., 3] = alpha_filtered
    pink_rgb = _composite_on_solid(output, PINK_RGB)
    desktop_rgb, desktop_meta = _desktop_preview(output)

    source_sha_after_processing = _sha256(CANONICAL_SOURCE)
    isnet_sha_after_processing = _sha256(CANONICAL_ISNET)
    if source_sha_before != source_sha_after_processing:
        raise SystemExit("Source screenshot changed during processing")
    if isnet_sha_before != isnet_sha_after_processing:
        raise SystemExit("ISNet draft changed during processing")

    tmp_output = _temp_path(DEFAULT_OUTPUT)
    tmp_pink = _temp_path(DEFAULT_PINK_PREVIEW)
    tmp_desktop = _temp_path(DEFAULT_DESKTOP_PREVIEW)
    tmp_report = _temp_path(DEFAULT_REPORT)
    try:
        _save_png(tmp_output, output)
        saved, fidelity = _verify_saved_png(tmp_output, alpha_filtered, source)
        final_stats = _alpha_stats(saved[..., 3], include_histogram=True)
        if not final_stats["all_four_corners_transparent"]:
            raise SystemExit("Saved cutout corners are not transparent")

        _save_jpeg(tmp_pink, pink_rgb)
        _verify_saved_pink(tmp_pink)
        _save_png(tmp_desktop, desktop_rgb)
        _verify_saved_desktop(tmp_desktop)

        # Recheck immediately before committing. The report records these
        # final pre-commit input hashes.
        source_sha_after = _sha256(CANONICAL_SOURCE)
        isnet_sha_after = _sha256(CANONICAL_ISNET)
        if source_sha_before != source_sha_after:
            raise SystemExit("Source screenshot changed before outputs were written")
        if isnet_sha_before != isnet_sha_after:
            raise SystemExit("ISNet draft changed before outputs were written")
        if not args.overwrite:
            for path in WRITE_TARGETS:
                if path.exists():
                    raise SystemExit(
                        "Refusing to overwrite file created during the run: "
                        f"{path}"
                    )

        output_sha = _sha256(tmp_output)
        pink_sha = _sha256(tmp_pink)
        desktop_sha = _sha256(tmp_desktop)
        isnet_stats = _alpha_stats(initial_alpha, include_histogram=False)

        report = {
            "schema": SCHEMA,
            "created_at_utc": datetime.now(timezone.utc).isoformat(),
            "method_origin": {
                "path": "qa/si-isnet-color-key-trial-v3.png",
                "role": "verified visual trial formalized by this tool",
                "read_by_this_tool": False,
            },
            "summary": {
                "foreground_pixels": final_stats["foreground_pixels"],
                "foreground_coverage_percent": final_stats[
                    "foreground_coverage_percent"
                ],
                "alpha_min": final_stats["alpha_min"],
                "alpha_max": final_stats["alpha_max"],
                "all_four_corners_transparent": final_stats[
                    "all_four_corners_transparent"
                ],
                "all_pixels_rgb_identical_to_source": fidelity[
                    "all_pixels_rgb_identical_to_source"
                ],
                "components_retained": component_details["components_retained"],
                "components_rejected": component_details["components_rejected"],
                "output_size": list(EXPECTED_SIZE),
                "desktop_preview_size": list(DESKTOP_SIZE),
                "desktop_preview_content_size": desktop_meta["content_size"],
            },
            "tool": {
                "path": _relative(TOOL_PATH),
                "sha256": _sha256(TOOL_PATH),
            },
            "inputs": {
                "source_screenshot": {
                    "path": _relative(CANONICAL_SOURCE),
                    "role": "RGB source only",
                    "sha256_before_run": source_sha_before,
                    "sha256_after_run": source_sha_after,
                    "unchanged": source_sha_before == source_sha_after,
                    "size": list(EXPECTED_SIZE),
                    "mode": "RGBA",
                    "alpha_all_255": True,
                    "bytes": CANONICAL_SOURCE.stat().st_size,
                },
                "isnet_draft": {
                    "path": _relative(CANONICAL_ISNET),
                    "role": "alpha source only",
                    "sha256_before_run": isnet_sha_before,
                    "sha256_after_run": isnet_sha_after,
                    "unchanged": isnet_sha_before == isnet_sha_after,
                    "alpha_sha256": isnet_alpha_sha,
                    "alpha": isnet_stats,
                    "size": list(EXPECTED_SIZE),
                    "mode": "RGBA",
                    "bytes": CANONICAL_ISNET.stat().st_size,
                },
                "fixed_inputs_only": True,
                "new_cutout_rgb_generated": False,
                "old_draft_masks_used": False,
                "trial_image_read": False,
            },
            "parameters": {
                "argv": list(sys.argv),
                "overwrite_requested": bool(args.overwrite),
                "source_rgb": "qa/si-body-reference-071.png",
                "initial_alpha": (
                    "art-source/v1.3/si-game-cutout-isnet-draft-v1.png alpha"
                ),
                "output_rgb_policy": (
                    "copy all source RGB bytes unchanged, including RGB under "
                    "transparent pixels"
                ),
                "preview_rgb_policy": (
                    "previews only composite the unchanged cutout RGB over a "
                    "solid preview background; they are not candidate art"
                ),
                "color_key": {
                    "r": {"min": 10, "max": 48},
                    "g": {"min": 25, "max": 65},
                    "b": {"min": 40, "max": 88},
                    "b_minus_g": {"min": 8, "max": 28},
                    "g_minus_r": {"min": 7, "max": 28},
                    "initial_alpha": {"max_exclusive": KEY_ALPHA_LIMIT},
                    "expression": (
                        "(10 <= r <= 48) and (25 <= g <= 65) and "
                        "(40 <= b <= 88) and (8 <= b-g <= 28) and "
                        "(7 <= g-r <= 28) and (initial_alpha < 235)"
                    ),
                    "action": "set alpha to 0 at key pixels",
                    "stage": "before alpha rescale",
                    "uses_original_screenshot_rgb": True,
                    "uses_isnet_draft_alpha": True,
                },
                "alpha_rescale": {
                    "cutoff": (
                        f"alpha < {LOW_ALPHA_CUTOFF} becomes 0; otherwise "
                        f"(alpha - {LOW_ALPHA_CUTOFF}) * "
                        f"{RESCALE_NUMERATOR} / {RESCALE_DENOMINATOR}"
                    ),
                    "integer_formula": (
                        f"((alpha - {LOW_ALPHA_CUTOFF}) * "
                        f"{RESCALE_NUMERATOR}) // {RESCALE_DENOMINATOR}"
                    ),
                    "clip": [0, 255],
                    "cast": "uint8, fractional values truncated toward zero",
                    "stage": "after color key",
                },
                "component_filter": {
                    "function": "cv2.connectedComponentsWithStats",
                    "mask": f"alpha > {COMPONENT_ALPHA_THRESHOLD}",
                    "connectivity": COMPONENT_CONNECTIVITY,
                    "min_area_inclusive": MIN_COMPONENT_AREA,
                    "action": (
                        "retain every alpha value in components with area >= "
                        "900 pixels; set all other alpha to 0"
                    ),
                    "stage": "after alpha rescale",
                },
                "pink_preview": {
                    "background_rgb": list(PINK_RGB),
                    "format": "JPEG",
                    "quality": PREVIEW_QUALITY,
                    "subsampling": 0,
                    "note": "lossy preview only; use the RGBA PNG for acceptance",
                },
                "desktop_preview": {
                    "canvas_size": list(DESKTOP_SIZE),
                    "background_rgb": list(DESKTOP_BACKGROUND_RGB),
                    "scaling": "keep aspect ratio, fit, LANCZOS, centered",
                    "note": (
                        "static desktop-size inspection only; not an animation "
                        "frame, atlas, or runtime asset"
                    ),
                },
            },
            "alpha_processing": {
                **key_details,
                "component_filter": component_details,
                "output_alpha": final_stats,
            },
            "pixel_fidelity": fidelity,
            "checks": {
                "source_sha_unchanged": source_sha_before == source_sha_after,
                "isnet_draft_sha_unchanged": isnet_sha_before == isnet_sha_after,
                "output_rgba_round_trip": True,
                "output_alpha_matches": True,
                "foreground_rgb_identical": fidelity[
                    "foreground_rgb_identical_to_source"
                ],
                "all_rgb_identical": fidelity[
                    "all_pixels_rgb_identical_to_source"
                ],
                "all_four_corners_transparent": final_stats[
                    "all_four_corners_transparent"
                ],
                "pink_preview_round_trip": True,
                "desktop_preview_round_trip": True,
                "desktop_preview_size_384x416": True,
            },
            "outputs": {
                "cutout": {
                    "path": _relative(DEFAULT_OUTPUT),
                    "sha256": output_sha,
                    "size": [int(saved.shape[1]), int(saved.shape[0])],
                    "mode": "RGBA",
                    "bytes": tmp_output.stat().st_size,
                },
                "pink_preview": {
                    "path": _relative(DEFAULT_PINK_PREVIEW),
                    "sha256": pink_sha,
                    "size": list(EXPECTED_SIZE),
                    "mode": "RGB",
                    "format": "JPEG",
                    "quality": PREVIEW_QUALITY,
                    "subsampling": 0,
                    "background_rgb": list(PINK_RGB),
                    "bytes": tmp_pink.stat().st_size,
                },
                "desktop_preview": {
                    "path": _relative(DEFAULT_DESKTOP_PREVIEW),
                    "sha256": desktop_sha,
                    "size": list(DESKTOP_SIZE),
                    "mode": "RGB",
                    "format": "PNG",
                    **desktop_meta,
                    "bytes": tmp_desktop.stat().st_size,
                },
                "report": {"path": _relative(DEFAULT_REPORT)},
            },
            "software": {
                "python": sys.version.split()[0],
                "numpy": np.__version__,
                "pillow": pillow_version,
                "opencv": cv2.__version__,
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
                "existing_outputs_replaced": (
                    preexisting_targets if args.overwrite else []
                ),
                "source_isnet_old_drafts_assets_release_mobile_written": False,
                "cutout_new_rgb_generated": False,
                "preview_compositing_only": True,
                "image_generation_used": False,
                "network_used": False,
                "cloud_inference_used": False,
                "dsh_used": False,
                "claude_model_config_changed": False,
            },
            "limitations": LIMITATIONS,
        }
        _save_json(tmp_report, report)

        source_sha_final = _sha256(CANONICAL_SOURCE)
        isnet_sha_final = _sha256(CANONICAL_ISNET)
        if source_sha_before != source_sha_final:
            raise SystemExit("Source screenshot changed before report commit")
        if isnet_sha_before != isnet_sha_final:
            raise SystemExit("ISNet draft changed before report commit")

        for target, temporary in (
            (DEFAULT_OUTPUT, tmp_output),
            (DEFAULT_PINK_PREVIEW, tmp_pink),
            (DEFAULT_DESKTOP_PREVIEW, tmp_desktop),
            (DEFAULT_REPORT, tmp_report),
        ):
            os.replace(temporary, target)

        # Final post-commit verification. Any mismatch is a hard failure.
        if _sha256(DEFAULT_OUTPUT) != output_sha:
            raise SystemExit("Committed PNG hash differs from the verified temp")
        if _sha256(DEFAULT_PINK_PREVIEW) != pink_sha:
            raise SystemExit(
                "Committed pink preview hash differs from the verified temp"
            )
        if _sha256(DEFAULT_DESKTOP_PREVIEW) != desktop_sha:
            raise SystemExit(
                "Committed desktop preview hash differs from the verified temp"
            )
        committed, committed_fidelity = _verify_saved_png(
            DEFAULT_OUTPUT, alpha_filtered, source
        )
        if not committed_fidelity["all_pixels_rgb_identical_to_source"]:
            raise SystemExit("Committed PNG RGB fidelity check failed")
        _verify_saved_pink(DEFAULT_PINK_PREVIEW)
        _verify_saved_desktop(DEFAULT_DESKTOP_PREVIEW)
        if _sha256(CANONICAL_SOURCE) != source_sha_before:
            raise SystemExit("Source screenshot changed after outputs were committed")
        if _sha256(CANONICAL_ISNET) != isnet_sha_before:
            raise SystemExit("ISNet draft changed after outputs were committed")

        committed_stats = _alpha_stats(committed[..., 3], include_histogram=False)
        print(
            json.dumps(
                {
                    "output": _relative(DEFAULT_OUTPUT),
                    "pink_preview": _relative(DEFAULT_PINK_PREVIEW),
                    "desktop_preview": _relative(DEFAULT_DESKTOP_PREVIEW),
                    "report": _relative(DEFAULT_REPORT),
                    "foreground_pixels": committed_stats["foreground_pixels"],
                    "alpha_min": committed_stats["alpha_min"],
                    "alpha_max": committed_stats["alpha_max"],
                    "four_corners": committed_stats["four_corners"],
                    "all_pixels_rgb_identical_to_source": committed_fidelity[
                        "all_pixels_rgb_identical_to_source"
                    ],
                    "components_retained": component_details[
                        "components_retained"
                    ],
                    "components_rejected": component_details[
                        "components_rejected"
                    ],
                    "output_size": list(EXPECTED_SIZE),
                    "desktop_preview_size": list(DESKTOP_SIZE),
                    "desktop_preview_content_size": desktop_meta["content_size"],
                    "source_sha_unchanged": source_sha_before == source_sha_final,
                    "isnet_draft_sha_unchanged": (
                        isnet_sha_before == isnet_sha_final
                    ),
                },
                ensure_ascii=False,
                indent=2,
            )
        )
    finally:
        for temporary in (tmp_output, tmp_pink, tmp_desktop, tmp_report):
            temporary.unlink(missing_ok=True)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())

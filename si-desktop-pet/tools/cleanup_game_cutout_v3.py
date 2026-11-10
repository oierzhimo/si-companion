#!/usr/bin/env python3
"""Create the static si-game-cutout-draft-v3.png cleanup draft.

Fixed inputs, read only:
  - art-source/v1.3/si-game-cutout-draft-v2.png   base RGBA (RGB and alpha)
  - qa/si-body-reference-071.png                  original screenshot, RGB check
  - qa/si-game-cutout-cleanup-trial-v3.png        expected alpha, exact match

Fixed outputs, never any other path:
  - art-source/v1.3/si-game-cutout-draft-v3.png
  - qa/si-game-cutout-draft-v3-desktop-size.png
  - qa/si-game-cutout-draft-v3-pink-preview.jpg
  - qa/si-game-cutout-draft-v3-report.json

Only two alpha rectangles are cleared; every RGB byte stays as in v2/source:
  y=695:735, x<155       -> alpha 0
  y=1470:1540, x=430:530 -> alpha 0

This is a static draft, not an animation frame, atlas, or runtime asset.
Shoulder, hair, and shoe edges still need manual review.

A rerun refuses to overwrite any of the four outputs unless --overwrite is
passed. --overwrite can only replace those four fixed files. Inputs, older
drafts, assets, release, mobile, and D:\\终末地小祀 are never write targets.

Run with an interpreter that has numpy and Pillow, for example:
  .venv\\Scripts\\python.exe tools\\cleanup_game_cutout_v3.py
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

V2_CUTOUT = ROOT / "art-source" / "v1.3" / "si-game-cutout-draft-v2.png"
REFERENCE = ROOT / "qa" / "si-body-reference-071.png"
TRIAL = ROOT / "qa" / "si-game-cutout-cleanup-trial-v3.png"

OUT_CUTOUT = ROOT / "art-source" / "v1.3" / "si-game-cutout-draft-v3.png"
OUT_DESKTOP = ROOT / "qa" / "si-game-cutout-draft-v3-desktop-size.png"
OUT_PINK = ROOT / "qa" / "si-game-cutout-draft-v3-pink-preview.jpg"
OUT_REPORT = ROOT / "qa" / "si-game-cutout-draft-v3-report.json"

WRITE_TARGETS = (OUT_CUTOUT, OUT_DESKTOP, OUT_PINK, OUT_REPORT)
READ_ONLY_INPUTS = (V2_CUTOUT, REFERENCE, TRIAL)

EXPECTED_SIZE = (920, 1540)
EXPECTED_SHAPE = (EXPECTED_SIZE[1], EXPECTED_SIZE[0], 4)
PINK_RGB = (255, 105, 180)
DESKTOP_SIZE = (384, 416)
DESKTOP_BACKGROUND_RGB = (232, 232, 232)
PREVIEW_QUALITY = 95
SCHEMA = "si-game-cutout-draft-report/v3"
DRAFT_STATUS = (
    "静态草稿：本文件只把 v2 的两块 alpha 区域清零，RGB 未改。"
    "肩部、发丝、鞋边等仍需人工逐处检查；它不是动画帧、图集或运行素材，"
    "不能直接用于动画或发布。"
)

# name, y0, y1, x0, x1, notation. Bounds are half-open: x1/y1 are excluded.
CLEAR_REGIONS = (
    ("lower_left_residue", 695, 735, 0, 155, "y=695:735, x<155"),
    ("lower_right_residue", 1470, 1540, 430, 530, "y=1470:1540, x=430:530"),
)

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
    V2_CUTOUT,
    REFERENCE,
    TRIAL,
    ROOT / "qa" / "si-game-cutout-cleanup-trial-v3-desktop.png",
    ROOT / "art-source" / "v1.3" / "si-game-cutout-draft-v1.png",
    ROOT / "art-source" / "v1.3" / "si-game-cutout-isnet-draft-v1.png",
    ROOT / "art-source" / "v1.3" / "si-game-cutout-model-draft-v1.png",
)

FORBIDDEN_ROOTS = (Path(r"D:\终末地小祀"),)

LIMITATIONS = [
    "只清理两块固定矩形内的 alpha，其他像素一律未动；肩部、发丝、鞋边等仍需用全尺寸 RGBA、粉色底和浅灰底预览逐处人工检查。",
    "这是静态草稿，不是动画帧、图集或运行素材；qa/si-game-cutout-draft-v3-desktop-size.png 只是 384x416 的尺寸对照图。",
    "所有 RGB 都逐字节继承 v2 和原始截图，包括透明像素下方的 RGB；工具不重绘、不生成、不修饰脸、肤色、墨纹或尾巴。",
    "粉色底 JPG 有压缩损失，只用于快速查看；验收应以 RGBA PNG 为准。",
    "不读取也不修改人工试验的桌面图；桌面预览按 v2 的 384x416 浅灰底设置重新生成。",
    "不修改原图、v2、人工试验、旧草稿、assets、release、mobile 或 D:\\终末地小祀；默认拒绝覆盖四个固定输出。",
]


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _relative(path: Path) -> str:
    return path.resolve().relative_to(ROOT.resolve()).as_posix()


def _is_under(path: Path, root: Path) -> bool:
    try:
        path.resolve().relative_to(root.resolve())
    except (OSError, ValueError):
        return False
    return True


def _guard_inputs() -> None:
    for path in READ_ONLY_INPUTS:
        if path.is_symlink():
            raise SystemExit(f"Input must not be a symlink: {path}")
        if not path.is_file():
            raise SystemExit(f"Expected fixed input is missing: {path}")
    resolved = [path.resolve() for path in READ_ONLY_INPUTS]
    if len(set(resolved)) != len(resolved):
        raise SystemExit("The three fixed inputs do not resolve to distinct files")
    targets = {path.resolve() for path in WRITE_TARGETS}
    for path in READ_ONLY_INPUTS:
        if path.resolve() in targets:
            raise SystemExit(f"Input collides with an output path: {_relative(path)}")


def _guard_targets(overwrite: bool) -> list[str]:
    """Allow only the four fixed v3 targets, and refuse by default."""
    resolved = [path.resolve() for path in WRITE_TARGETS]
    if len(set(resolved)) != len(resolved):
        raise SystemExit("Output targets are not distinct")

    inputs = {path.resolve() for path in READ_ONLY_INPUTS}
    protected_files = {path.resolve() for path in PROTECTED_FILES}
    for path in WRITE_TARGETS:
        if not _is_under(path, ROOT):
            raise SystemExit(f"Write target escapes the project: {path}")
        for forbidden in FORBIDDEN_ROOTS:
            if _is_under(path, forbidden):
                raise SystemExit(f"Refusing to write into forbidden path: {forbidden}")
        for protected in PROTECTED_DIRS:
            if _is_under(path, protected):
                raise SystemExit(
                    f"Refusing to write into protected path: {_relative(protected)}"
                )
        if path.resolve() in inputs or path.resolve() in protected_files:
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
        raise SystemExit(
            "Refusing to overwrite existing output(s): "
            f"{', '.join(existing)}. Pass --overwrite to replace only the four "
            "fixed si-game-cutout-draft-v3 outputs."
        )
    return existing


def _read_rgba(
    path: Path, label: str, require_opaque_alpha: bool = False
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
    if array.shape != EXPECTED_SHAPE:
        raise SystemExit(
            f"{label} array shape is {array.shape}, expected {EXPECTED_SHAPE}"
        )
    if require_opaque_alpha and not np.all(array[..., 3] == 255):
        raise SystemExit(f"{label} alpha is not uniformly 255")
    return array, sha_before


def _assert_inputs_unchanged(expected: dict[Path, str]) -> dict[Path, str]:
    actual = {path: _sha256(path) for path in expected}
    for path, digest in expected.items():
        if actual[path] != digest:
            raise SystemExit(f"Input changed during the run: {_relative(path)}")
    return actual


def _alpha_stats(alpha: np.ndarray) -> dict[str, Any]:
    total = int(alpha.size)
    transparent = int(np.count_nonzero(alpha == 0))
    opaque = int(np.count_nonzero(alpha == 255))
    ys, xs = np.nonzero(alpha)
    bbox = (
        [int(xs.min()), int(ys.min()), int(xs.max()) + 1, int(ys.max()) + 1]
        if xs.size
        else None
    )
    return {
        "total_pixels": total,
        "transparent_pixels": transparent,
        "partial_pixels": total - transparent - opaque,
        "foreground_pixels": total - transparent,
        "opaque_pixels": opaque,
        "alpha_min": int(alpha.min()),
        "alpha_max": int(alpha.max()),
        "alpha_mean": round(float(alpha.mean()), 6),
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
    }


def _rgb_mismatch(first: np.ndarray, second: np.ndarray) -> int:
    return int(
        np.count_nonzero(np.any(first[..., :3] != second[..., :3], axis=2))
    )


def _composite_on_solid(
    rgba: np.ndarray, background: tuple[int, int, int]
) -> np.ndarray:
    rgb = rgba[..., :3].astype(np.float32)
    alpha = rgba[..., 3].astype(np.float32)[..., None] / np.float32(255.0)
    background_array = np.asarray(background, dtype=np.float32)
    return np.clip(
        np.rint(rgb * alpha + background_array * (1.0 - alpha)), 0, 255
    ).astype(np.uint8)


def _desktop_preview(rgba: np.ndarray) -> tuple[np.ndarray, dict[str, Any]]:
    """Match the v2 preview: fit in 384x416, keep aspect, center on light grey."""
    height, width = rgba.shape[:2]
    canvas_width, canvas_height = DESKTOP_SIZE
    scale = min(canvas_width / width, canvas_height / height)
    target_width = max(1, int(round(width * scale)))
    target_height = max(1, int(round(height * scale)))

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
            "Static 384x416 size check only; not an animation frame, atlas, "
            "or runtime desktop-pet asset."
        ),
    }


def _temp_path(path: Path) -> Path:
    descriptor, name = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=str(path.parent)
    )
    os.close(descriptor)
    return Path(name)


def _save_png(path: Path, array: np.ndarray) -> None:
    Image.fromarray(array).save(path, format="PNG", optimize=True)


def _save_jpeg(path: Path, rgb: np.ndarray) -> None:
    Image.fromarray(rgb).save(
        path, format="JPEG", quality=PREVIEW_QUALITY, subsampling=0, optimize=True
    )


def _save_json(path: Path, payload: dict[str, Any]) -> None:
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )


def _verify_rgb_preview(path: Path, label: str, size: tuple[int, int]) -> None:
    with Image.open(path) as image:
        if image.mode != "RGB":
            raise SystemExit(f"{label} mode is {image.mode}, expected RGB")
        if image.size != size:
            raise SystemExit(f"{label} size is {image.width}x{image.height}")
        image.load()


def _verify_desktop(path: Path) -> None:
    _verify_rgb_preview(path, "Desktop preview", DESKTOP_SIZE)
    with Image.open(path) as image:
        array = np.array(image, dtype=np.uint8).copy()
    background = np.asarray(DESKTOP_BACKGROUND_RGB, dtype=np.uint8)
    for corner in (array[0, 0], array[0, -1], array[-1, 0], array[-1, -1]):
        if not np.array_equal(corner, background):
            raise SystemExit("Desktop preview corners are not the fixed background")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Create the static draft-v3 cutout by clearing exactly two alpha "
            "rectangles in draft-v2; all RGB stays unchanged."
        ),
        epilog=(
            "By default any existing draft-v3 output is refused. --overwrite "
            "can replace only the four fixed draft-v3 outputs; inputs, older "
            "drafts, assets, release, mobile, and D:\\终末地小祀 are never "
            "written. The manual cleanup trial is read only to verify its alpha."
        ),
    )
    parser.add_argument(
        "--overwrite",
        action="store_true",
        help=(
            "Replace only the four fixed draft-v3 outputs; never inputs, old "
            "drafts, runtime assets, release, or mobile files"
        ),
    )
    args = parser.parse_args(argv)

    _guard_inputs()
    preexisting_targets = _guard_targets(args.overwrite)

    v2, v2_sha = _read_rgba(V2_CUTOUT, "v2 cutout")
    reference, reference_sha = _read_rgba(
        REFERENCE, "Original screenshot", require_opaque_alpha=True
    )
    trial, trial_sha = _read_rgba(TRIAL, "Manual cleanup trial")
    shas_before = {
        V2_CUTOUT: v2_sha,
        REFERENCE: reference_sha,
        TRIAL: trial_sha,
    }

    v2_rgb_mismatch = _rgb_mismatch(v2, reference)
    if v2_rgb_mismatch:
        raise SystemExit(
            f"v2 RGB differs from the original screenshot at {v2_rgb_mismatch} pixels"
        )
    trial_rgb_mismatch = _rgb_mismatch(trial, v2)
    if trial_rgb_mismatch:
        raise SystemExit(
            f"Manual trial RGB differs from v2 at {trial_rgb_mismatch} pixels"
        )

    v2_alpha = v2[..., 3]
    output_alpha = v2_alpha.copy()
    inside_regions = np.zeros(output_alpha.shape, dtype=bool)
    regions_report: list[dict[str, Any]] = []
    for name, y0, y1, x0, x1, notation in CLEAR_REGIONS:
        before = output_alpha[y0:y1, x0:x1].copy()
        output_alpha[y0:y1, x0:x1] = 0
        after = output_alpha[y0:y1, x0:x1]
        inside_regions[y0:y1, x0:x1] = True
        regions_report.append(
            {
                "name": name,
                "notation": notation,
                "bounds_half_open": {"x": [x0, x1], "y": [y0, y1]},
                "pixels": int(before.size),
                "v2_alpha_nonzero_pixels": int(np.count_nonzero(before)),
                "alpha_pixels_cleared": int(np.count_nonzero(before != after)),
                "v3_alpha_nonzero_pixels": int(np.count_nonzero(after)),
            }
        )

    union_pixels = int(np.count_nonzero(inside_regions))
    if union_pixels != sum(
        (y1 - y0) * (x1 - x0) for _, y0, y1, x0, x1, _ in CLEAR_REGIONS
    ):
        raise SystemExit("The two cleanup regions overlap")

    changed_mask = output_alpha != v2_alpha
    changed_total = int(np.count_nonzero(changed_mask))
    changed_outside = int(np.count_nonzero(changed_mask & ~inside_regions))
    alpha_only_decreased = bool(np.all(output_alpha <= v2_alpha))

    if changed_outside:
        raise SystemExit(
            f"Alpha changed outside the two fixed regions at {changed_outside} pixels"
        )
    if changed_total == 0:
        raise SystemExit("The cleanup changed no alpha pixels")
    if np.any(output_alpha[inside_regions]):
        raise SystemExit("The cleanup regions are not fully transparent")
    if not alpha_only_decreased:
        raise SystemExit("The cleanup increased alpha somewhere")

    corners = {
        "top_left": int(output_alpha[0, 0]),
        "top_right": int(output_alpha[0, -1]),
        "bottom_left": int(output_alpha[-1, 0]),
        "bottom_right": int(output_alpha[-1, -1]),
    }
    if any(corners.values()):
        raise SystemExit(f"All four output corners must be transparent; got {corners}")

    trial_alpha = trial[..., 3]
    trial_mismatch = int(np.count_nonzero(output_alpha != trial_alpha))
    if trial_mismatch:
        raise SystemExit(
            "Computed alpha differs from the manual trial at "
            f"{trial_mismatch} pixels"
        )

    output = v2.copy()
    output[..., 3] = output_alpha
    output_rgb_mismatch = _rgb_mismatch(output, reference)
    if output_rgb_mismatch:
        raise SystemExit(
            f"Output RGB differs from the original screenshot at "
            f"{output_rgb_mismatch} pixels"
        )

    pink_rgb = _composite_on_solid(output, PINK_RGB)
    desktop_rgb, desktop_meta = _desktop_preview(output)

    output_stats = _alpha_stats(output_alpha)
    _assert_inputs_unchanged(shas_before)

    tmp_cutout = _temp_path(OUT_CUTOUT)
    tmp_desktop = _temp_path(OUT_DESKTOP)
    tmp_pink = _temp_path(OUT_PINK)
    tmp_report = _temp_path(OUT_REPORT)
    try:
        _save_png(tmp_cutout, output)
        saved, _ = _read_rgba(tmp_cutout, "Temporary cutout")
        if not np.array_equal(saved, output):
            raise SystemExit("Saved PNG does not round-trip exactly")
        if _rgb_mismatch(saved, reference):
            raise SystemExit("Saved PNG RGB fidelity check failed")
        if not np.array_equal(saved[..., 3], trial_alpha):
            raise SystemExit("Saved PNG alpha differs from the manual trial")

        _save_jpeg(tmp_pink, pink_rgb)
        _verify_rgb_preview(tmp_pink, "Pink preview", EXPECTED_SIZE)
        _save_png(tmp_desktop, desktop_rgb)
        _verify_desktop(tmp_desktop)

        actual_shas = _assert_inputs_unchanged(shas_before)
        if not args.overwrite:
            for path in WRITE_TARGETS:
                if path.exists():
                    raise SystemExit(
                        f"Refusing to overwrite file created during the run: {path}"
                    )

        cutout_sha = _sha256(tmp_cutout)
        desktop_sha = _sha256(tmp_desktop)
        pink_sha = _sha256(tmp_pink)

        report = {
            "schema": SCHEMA,
            "created_at_utc": datetime.now(timezone.utc).isoformat(),
            "draft_status": DRAFT_STATUS,
            "method_origin": {
                "path": _relative(TRIAL),
                "role": (
                    "manual cleanup trial; its alpha is the exact expected "
                    "result and its RGB is checked against v2 (read only)"
                ),
                "read_by_this_tool": True,
            },
            "summary": {
                "output_size": list(EXPECTED_SIZE),
                "changed_alpha_pixels": changed_total,
                "alpha_pixels_cleared": changed_total,
                "regions_cleaned": len(regions_report),
                "alpha_identical_to_manual_trial": True,
                "all_four_corners_transparent": True,
                "all_pixels_rgb_identical_to_source": True,
                "foreground_pixels": output_stats["foreground_pixels"],
                "foreground_coverage_percent": output_stats[
                    "foreground_coverage_percent"
                ],
                "desktop_preview_size": list(DESKTOP_SIZE),
                "desktop_preview_content_size": desktop_meta["content_size"],
            },
            "tool": {
                "path": _relative(TOOL_PATH),
                "sha256": _sha256(TOOL_PATH),
            },
            "inputs": {
                "v2_cutout": {
                    "path": _relative(V2_CUTOUT),
                    "role": "base RGBA: RGB plus initial alpha",
                    "sha256_before_run": v2_sha,
                    "sha256_after_run": actual_shas[V2_CUTOUT],
                    "unchanged": True,
                    "size": list(EXPECTED_SIZE),
                    "mode": "RGBA",
                    "bytes": V2_CUTOUT.stat().st_size,
                    "alpha": _alpha_stats(v2_alpha),
                    "rgb_mismatch_pixels_vs_reference": v2_rgb_mismatch,
                    "rgb_identical_to_reference": True,
                },
                "source_screenshot": {
                    "path": _relative(REFERENCE),
                    "role": "original screenshot; RGB verification only",
                    "sha256_before_run": reference_sha,
                    "sha256_after_run": actual_shas[REFERENCE],
                    "unchanged": True,
                    "size": list(EXPECTED_SIZE),
                    "mode": "RGBA",
                    "alpha_all_255": True,
                    "bytes": REFERENCE.stat().st_size,
                },
                "manual_trial": {
                    "path": _relative(TRIAL),
                    "role": "expected alpha; read only",
                    "sha256_before_run": trial_sha,
                    "sha256_after_run": actual_shas[TRIAL],
                    "unchanged": True,
                    "size": list(EXPECTED_SIZE),
                    "mode": "RGBA",
                    "bytes": TRIAL.stat().st_size,
                    "alpha": _alpha_stats(trial_alpha),
                    "rgb_mismatch_pixels_vs_v2": trial_rgb_mismatch,
                    "rgb_identical_to_v2": True,
                },
                "fixed_inputs_only": True,
                "new_rgb_generated": False,
                "desktop_trial_image_read": False,
            },
            "cleanup": {
                "description": (
                    "Copy v2 unchanged, then set alpha to 0 inside exactly two "
                    "half-open rectangles. No other pixel is modified."
                ),
                "region_coordinate_convention": (
                    "x and y are half-open [start, end); y=695:735 covers rows "
                    "695 through 734"
                ),
                "regions": regions_report,
                "union_pixels": union_pixels,
                "changed_pixels_total": changed_total,
                "changed_pixels_outside_regions": changed_outside,
                "alpha_changes_limited_to_two_regions": True,
                "alpha_only_decreased": alpha_only_decreased,
                "alpha_identical_to_manual_trial": True,
                "alpha_mismatch_pixels_vs_manual_trial": trial_mismatch,
            },
            "pixel_fidelity": {
                "all_pixels_compared": int(np.prod(EXPECTED_SIZE)),
                "output_rgb_mismatch_pixels_vs_reference": output_rgb_mismatch,
                "output_rgb_identical_to_reference": True,
                "v2_rgb_mismatch_pixels_vs_reference": v2_rgb_mismatch,
                "manual_trial_rgb_mismatch_pixels_vs_v2": trial_rgb_mismatch,
                "note": (
                    "Every output RGB byte, including RGB under transparent "
                    "pixels, is unchanged from v2 and the original screenshot."
                ),
            },
            "checks": {
                "v2_sha_unchanged_before_and_after": True,
                "source_screenshot_sha_unchanged_before_and_after": True,
                "manual_trial_sha_unchanged_before_and_after": True,
                "v2_rgb_identical_to_source": True,
                "manual_trial_rgb_identical_to_v2": True,
                "output_rgba_round_trip": True,
                "output_alpha_matches_computed_alpha": True,
                "output_alpha_identical_to_manual_trial": True,
                "all_rgb_identical_to_source": True,
                "all_four_corners_transparent": True,
                "alpha_changes_limited_to_two_regions": True,
                "alpha_changes_only_remove_alpha": True,
                "pink_preview_round_trip": True,
                "desktop_preview_round_trip": True,
                "desktop_preview_size_384x416": True,
            },
            "outputs": {
                "cutout": {
                    "path": _relative(OUT_CUTOUT),
                    "sha256": cutout_sha,
                    "size": list(EXPECTED_SIZE),
                    "mode": "RGBA",
                    "bytes": tmp_cutout.stat().st_size,
                },
                "desktop_preview": {
                    "path": _relative(OUT_DESKTOP),
                    "sha256": desktop_sha,
                    "size": list(DESKTOP_SIZE),
                    "mode": "RGB",
                    "format": "PNG",
                    "bytes": tmp_desktop.stat().st_size,
                    **desktop_meta,
                },
                "pink_preview": {
                    "path": _relative(OUT_PINK),
                    "sha256": pink_sha,
                    "size": list(EXPECTED_SIZE),
                    "mode": "RGB",
                    "format": "JPEG",
                    "quality": PREVIEW_QUALITY,
                    "subsampling": 0,
                    "background_rgb": list(PINK_RGB),
                    "bytes": tmp_pink.stat().st_size,
                },
                "report": {"path": _relative(OUT_REPORT)},
            },
            "software": {
                "python": sys.version.split()[0],
                "numpy": np.__version__,
                "pillow": pillow_version,
            },
            "non_destructive": {
                "fixed_inputs_only": True,
                "fixed_write_targets_only": True,
                "write_targets": [_relative(path) for path in WRITE_TARGETS],
                "read_only_inputs": [_relative(path) for path in READ_ONLY_INPUTS],
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
                "v2_reference_trial_old_drafts_assets_release_mobile_written": False,
                "manual_trial_desktop_image_read": False,
                "new_rgb_generated": False,
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
        _assert_inputs_unchanged(shas_before)

        for target, temporary in (
            (OUT_CUTOUT, tmp_cutout),
            (OUT_DESKTOP, tmp_desktop),
            (OUT_PINK, tmp_pink),
            (OUT_REPORT, tmp_report),
        ):
            os.replace(temporary, target)

        # Final post-commit verification. Any mismatch is a hard failure.
        if _sha256(OUT_CUTOUT) != cutout_sha:
            raise SystemExit("Committed PNG hash differs from the verified temp")
        if _sha256(OUT_DESKTOP) != desktop_sha:
            raise SystemExit("Committed desktop preview hash differs from temp")
        if _sha256(OUT_PINK) != pink_sha:
            raise SystemExit("Committed pink preview hash differs from temp")

        committed, _ = _read_rgba(OUT_CUTOUT, "Committed cutout")
        if not np.array_equal(committed, output):
            raise SystemExit("Committed PNG differs from the computed RGBA")
        if _rgb_mismatch(committed, reference):
            raise SystemExit("Committed PNG RGB fidelity check failed")
        _verify_rgb_preview(OUT_PINK, "Pink preview", EXPECTED_SIZE)
        _verify_desktop(OUT_DESKTOP)
        _assert_inputs_unchanged(shas_before)

        committed_stats = _alpha_stats(committed[..., 3])
        print(
            json.dumps(
                {
                    "output": _relative(OUT_CUTOUT),
                    "desktop_preview": _relative(OUT_DESKTOP),
                    "pink_preview": _relative(OUT_PINK),
                    "report": _relative(OUT_REPORT),
                    "changed_alpha_pixels": changed_total,
                    "changed_pixels_outside_regions": changed_outside,
                    "alpha_identical_to_manual_trial": True,
                    "all_four_corners_transparent": committed_stats[
                        "all_four_corners_transparent"
                    ],
                    "all_rgb_identical_to_source": True,
                    "foreground_pixels": committed_stats["foreground_pixels"],
                    "output_size": list(EXPECTED_SIZE),
                    "desktop_preview_size": list(DESKTOP_SIZE),
                    "desktop_preview_content_size": desktop_meta["content_size"],
                    "inputs_sha_unchanged": True,
                },
                ensure_ascii=False,
                indent=2,
            )
        )
    finally:
        for temporary in (tmp_cutout, tmp_desktop, tmp_pink, tmp_report):
            temporary.unlink(missing_ok=True)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())

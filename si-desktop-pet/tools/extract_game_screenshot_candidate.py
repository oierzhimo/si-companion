#!/usr/bin/env python3
"""Build a first-pass transparent cutout from the supplied in-game screenshot.

This is a local, non-destructive QA tool. It never writes to the source screenshot,
an old character candidate, or the runtime atlas. Only the alpha channel of the
output is synthesized; every output RGB pixel remains the source screenshot's RGB.

Run with the interpreter that has numpy/Pillow, for example:
  "E:\\新建文件夹\\VoiceStudio\\project\\.venv\\Scripts\\python.exe" \
      tools\\extract_game_screenshot_candidate.py

cv2 is loaded from qa/segmentation-deps automatically.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
CV2_DEPS = ROOT / "qa" / "segmentation-deps"
if str(CV2_DEPS) not in sys.path:
    sys.path.insert(0, str(CV2_DEPS))
try:
    import cv2
except ImportError as exc:
    raise SystemExit(
        "cv2 is unavailable. Expected OpenCV in qa/segmentation-deps, and "
        "numpy/Pillow in the active interpreter."
    ) from exc

CANONICAL_INPUT = ROOT / "qa" / "si-body-reference-071.png"
DEFAULT_OUTPUT = ROOT / "art-source" / "v1.3" / "si-game-cutout-draft-v1.png"
DEFAULT_REPORT = ROOT / "qa" / "si-game-cutout-draft-v1-report.json"
EXPECTED_SIZE = (920, 1540)
DRAFT_PREFIX = "si-game-cutout-draft-"
GRABCUT_ITERATIONS = 6
MIN_COMPONENT_AREA = 24

# Never write into runtime assets, release payloads, mobile copies, dependencies,
# or the original/reference art. Existing files may only be replaced when their
# names already carry the draft prefix, so old candidates stay untouched.
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

# Coordinates fit the one allowed 920x1540 source. These polygons are deliberately
# broad probable-foreground hints, not final silhouettes. They cover the head and
# horns, torso/arms, both long coat panels, both legs/boots, and the central tail.
PROBABLE_FOREGROUND_POLYGONS = (
    # Head, ears, side hair, and the asymmetric horn tips.
    ((335, 150), (365, 65), (405, 40), (445, 85), (475, 85), (520, 40),
     (558, 120), (570, 230), (545, 320), (510, 370), (400, 370), (350, 310)),
    # Neck, shoulders, upper body, arms, and hair falling over them.
    ((300, 350), (390, 360), (420, 380), (500, 380), (540, 350), (620, 350),
     (670, 520), (660, 760), (610, 850), (560, 740), (540, 620), (380, 620),
     (360, 740), (310, 850), (255, 760), (245, 520)),
    # Left skirt, long hanging coat panel, hair, and ink strands.
    ((30, 610), (180, 560), (330, 610), (365, 760), (345, 1020), (290, 1360),
     (240, 1425), (30, 1370), (10, 900)),
    # Right skirt, long hanging coat panel, hair, and ink strands.
    ((570, 610), (710, 560), (875, 610), (905, 930), (890, 1380), (760, 1440),
     (650, 1370), (580, 1000), (555, 760)),
    # Left leg and boot.
    ((315, 780), (440, 790), (455, 1030), (440, 1250), (435, 1390),
     (425, 1520), (305, 1520), (305, 1370), (320, 1200)),
    # Right leg and boot.
    ((470, 790), (600, 780), (610, 1200), (620, 1370), (625, 1520),
     (500, 1520), (475, 1390), (465, 1250)),
    # Tail hanging between the legs.
    ((430, 760), (500, 760), (505, 1010), (490, 1230), (465, 1300),
     (435, 1220), (420, 1010)),
)

# Definite foreground seeds: small ellipses and brush strokes placed only inside
# unambiguous character areas. These anchor white cloth, skin, and the thin horns
# without asking GrabCut to invent any color.
FOREGROUND_ELLIPSES = (
    (456, 250, 24, 30),    # face
    (456, 198, 32, 13),    # crown / straight bangs
    (456, 360, 12, 12),    # neck
    (456, 460, 28, 35),    # chest / bodice
    (456, 560, 28, 30),    # waist
    (456, 690, 42, 45),    # front skirt
    (390, 1000, 15, 80),   # left leg
    (532, 1000, 15, 80),   # right leg
    (466, 980, 9, 85),     # tail
    (378, 1435, 23, 28),   # left boot
    (568, 1435, 23, 28),   # right boot
    (405, 115, 5, 14),     # left horn shaft
    (520, 115, 5, 14),     # right horn shaft
)
FOREGROUND_STROKES = (
    (220, 850, 130, 1320, 14),  # inside left hanging coat panel
    (700, 850, 805, 1320, 14),  # inside right hanging coat panel
)

# Scenery/UI that is clearly not the character. Keeping these as definite background
# stops the top interface and border from becoming part of the cutout.
DEFINITE_BACKGROUND_BOXES = (
    (0, 0, 920, 42),       # top strip
    (0, 0, 330, 340),      # upper-left interface
    (600, 0, 920, 330),    # upper-right sky
    (0, 0, 25, 1540),      # left border
    (895, 0, 920, 1540),   # right border
    (0, 0, 920, 2),        # border
    (0, 1538, 920, 1540),
)

REVIEW_REGIONS = {
    "horns": [345, 40, 565, 180],
    "left_dark_wings_and_hair": [210, 320, 400, 920],
    "right_dark_wings_and_hair": [540, 320, 840, 960],
    "tail_between_legs": [410, 750, 515, 1320],
    "boots": [300, 1370, 640, 1525],
}

LIMITATIONS = [
    "人工多边形与种子取自 920x1540 预览的保守估计，不是逐像素描边；第一版用于快速建立透明底稿。",
    "暗墨翅膀、细发丝与深蓝背景颜色接近时仍可能被 GrabCut 漏掉；工具不会为缺失区域生成新 RGB，只把源像素的 alpha 置零。",
    "本轮输出二值 alpha（0/255），未做柔边、matte 或背景色溢出处理；透明像素保留源 RGB 但不可见。",
    "最终仍需人工查看鞋、角、衣摆尖端和两侧墨翼；若局部缺失，应增补种子/多边形后生成下一版。",
]


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _resolve_cli(value: str) -> Path:
    path = Path(value).expanduser()
    return path if path.is_absolute() else ROOT / path


def _guard_input(path: Path) -> Path:
    resolved = path.resolve()
    if resolved != CANONICAL_INPUT.resolve():
        raise SystemExit(
            f"Input is restricted to {CANONICAL_INPUT.relative_to(ROOT).as_posix()}"
        )
    if not resolved.is_file():
        raise SystemExit(f"Input does not exist: {resolved}")
    return resolved


def _guard_target(path: Path, suffix: str, kind: str, overwrite: bool) -> Path:
    resolved = path.resolve()
    if not resolved.is_relative_to(ROOT):
        raise SystemExit(f"{kind} must stay inside the project: {resolved}")
    if resolved.suffix.lower() != suffix:
        raise SystemExit(f"{kind} must end in {suffix}: {resolved}")
    if resolved == CANONICAL_INPUT.resolve():
        raise SystemExit(f"{kind} cannot be the source screenshot")
    for protected in PROTECTED_DIRS:
        if resolved.is_relative_to(protected.resolve()):
            raise SystemExit(f"Refusing to write into protected path: {protected}")
    if resolved.exists():
        if resolved.is_dir():
            raise SystemExit(f"{kind} target is a directory: {resolved}")
        if not resolved.name.startswith(DRAFT_PREFIX):
            raise SystemExit(f"Refusing to overwrite non-draft {kind}: {resolved.name}")
        if not overwrite:
            raise SystemExit(f"{kind} exists; pass --overwrite to replace this draft")
    return resolved


def _paint_seeds(shape: tuple[int, int]) -> np.ndarray:
    seeds = np.zeros(shape, np.uint8)
    for cx, cy, rx, ry in FOREGROUND_ELLIPSES:
        cv2.ellipse(seeds, (cx, cy), (rx, ry), 0, 0, 360, 1, -1)
    for x1, y1, x2, y2, thickness in FOREGROUND_STROKES:
        cv2.line(seeds, (x1, y1), (x2, y2), 1, thickness)
        radius = max(1, thickness // 2)
        cv2.circle(seeds, (x1, y1), radius, 1, -1)
        cv2.circle(seeds, (x2, y2), radius, 1, -1)
    return seeds


def _initial_trimap(shape: tuple[int, int], seeds: np.ndarray) -> np.ndarray:
    mask = np.full(shape, cv2.GC_PR_BGD, np.uint8)
    for points in PROBABLE_FOREGROUND_POLYGONS:
        cv2.fillPoly(mask, [np.asarray(points, np.int32)], cv2.GC_PR_FGD)
    for x0, y0, x1, y1 in DEFINITE_BACKGROUND_BOXES:
        mask[y0:y1, x0:x1] = cv2.GC_BGD
    mask[seeds.astype(bool)] = cv2.GC_FGD
    return mask


def _drop_tiny_components(fg: np.ndarray, seeds: np.ndarray) -> np.ndarray:
    count, labels, stats, _ = cv2.connectedComponentsWithStats(fg, connectivity=8)
    kept = np.zeros_like(fg)
    for label in range(1, count):
        component = labels == label
        if stats[label, cv2.CC_STAT_AREA] >= MIN_COMPONENT_AREA or np.any(seeds[component]):
            kept[component] = 1
    return kept


def _segment(rgb: np.ndarray, seeds: np.ndarray) -> tuple[np.ndarray, dict]:
    shape = rgb.shape[:2]
    # The source RGB view skips the alpha byte, so make a contiguous 3-channel
    # copy before handing it to OpenCV. The output still uses the untouched RGBA.
    rgb = np.ascontiguousarray(rgb)
    bgr = cv2.cvtColor(rgb, cv2.COLOR_RGB2BGR)
    mask = _initial_trimap(shape, seeds)
    counts = np.bincount(mask.ravel(), minlength=4)

    # Fixed RNG seed and iteration count make the same source/seeds reproducible.
    cv2.setRNGSeed(0)
    bgd_model = np.zeros((1, 65), np.float64)
    fgd_model = np.zeros((1, 65), np.float64)
    cv2.grabCut(
        bgr,
        mask,
        None,
        bgd_model,
        fgd_model,
        GRABCUT_ITERATIONS,
        cv2.GC_INIT_WITH_MASK,
    )

    fg = np.isin(mask, (cv2.GC_FGD, cv2.GC_PR_FGD)).astype(np.uint8)
    fg = cv2.morphologyEx(fg, cv2.MORPH_CLOSE, np.ones((3, 3), np.uint8))
    fg = _drop_tiny_components(fg, seeds)
    fg[seeds.astype(bool)] = 1
    fg[0, :] = 0
    fg[-1, :] = 0
    fg[:, 0] = 0
    fg[:, -1] = 0
    trimap = {
        "iterations": GRABCUT_ITERATIONS,
        "random_seed": 0,
        "initial_background_pixels": int(counts[cv2.GC_BGD]),
        "initial_probable_background_pixels": int(counts[cv2.GC_PR_BGD]),
        "initial_probable_foreground_pixels": int(counts[cv2.GC_PR_FGD]),
        "initial_foreground_pixels": int(counts[cv2.GC_FGD]),
        "definite_foreground_seed_pixels": int(np.count_nonzero(seeds)),
    }
    return fg, trimap


def _save_png_atomic(path: Path, rgba: np.ndarray) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    try:
        Image.fromarray(rgba).save(temporary, format="PNG", optimize=True)
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def _write_json_atomic(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    try:
        temporary.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def _alpha_report(image: np.ndarray) -> dict:
    alpha = image[..., 3]
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
        "binary_alpha": bool(np.all((alpha == 0) | (alpha == 255))),
        "histogram_256": histogram,
        "four_corners": {
            "top_left": int(alpha[0, 0]),
            "top_right": int(alpha[0, -1]),
            "bottom_left": int(alpha[-1, 0]),
            "bottom_right": int(alpha[-1, -1]),
        },
        "all_four_corners_transparent": bool(
            alpha[0, 0] == alpha[0, -1] == alpha[-1, 0] == alpha[-1, -1] == 0
        ),
        "foreground_bbox_xyxy": bbox,
        "foreground_coverage_percent": round((total - transparent) / total * 100, 4),
        "foreground_on_image_border": bool(
            np.any(alpha[0, :]) or np.any(alpha[-1, :])
            or np.any(alpha[:, 0]) or np.any(alpha[:, -1])
        ),
    }


def _rgb_fidelity(output: np.ndarray, source: np.ndarray) -> dict:
    foreground = output[..., 3] > 0
    foreground_count = int(np.count_nonzero(foreground))
    output_rgb = output[..., :3]
    source_rgb = source[..., :3]

    # Compare after reopening the saved PNG, not merely before encoding. This
    # catches any accidental palette conversion, compositing, or color rewrite.
    if foreground_count:
        fg_different = np.any(output_rgb[foreground] != source_rgb[foreground], axis=1)
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
        "note": "Output RGBA starts as a copy of the source; only channel 3 is replaced.",
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Non-destructive first-pass GrabCut cutout of si-body-reference-071.",
        epilog=(
            "The tool prepends qa/segmentation-deps for cv2. Use the Python "
            "environment that contains numpy/Pillow. It never writes to the source "
            "screenshot, old candidates, or runtime assets; an existing target may "
            "only be replaced if it is already a si-game-cutout-draft-* file."
        ),
    )
    parser.add_argument(
        "--input",
        default=str(CANONICAL_INPUT.relative_to(ROOT)),
        help="Must resolve to qa/si-body-reference-071.png",
    )
    parser.add_argument(
        "--output",
        default=str(DEFAULT_OUTPUT.relative_to(ROOT)),
        help="PNG target; existing non-draft files are never overwritten",
    )
    parser.add_argument(
        "--report",
        default=str(DEFAULT_REPORT.relative_to(ROOT)),
        help="JSON report target; existing non-draft files are never overwritten",
    )
    parser.add_argument(
        "--overwrite",
        action="store_true",
        help="Replace an existing file only when it already uses the draft prefix",
    )
    args = parser.parse_args(argv)

    input_path = _guard_input(_resolve_cli(args.input))
    output_path = _guard_target(_resolve_cli(args.output), ".png", "Output", args.overwrite)
    report_path = _guard_target(_resolve_cli(args.report), ".json", "Report", args.overwrite)
    if output_path == report_path:
        raise SystemExit("Output and report must be different files")

    source_hash_before = _sha256(input_path)
    with Image.open(input_path) as source_image:
        if source_image.mode != "RGBA":
            raise SystemExit(f"Expected an RGBA source, got {source_image.mode}")
        if source_image.size != EXPECTED_SIZE:
            raise SystemExit(
                f"Expected {EXPECTED_SIZE[0]}x{EXPECTED_SIZE[1]}, got "
                f"{source_image.width}x{source_image.height}"
            )
        source = np.array(source_image)

    if not np.all(source[..., 3] == 255):
        raise SystemExit("Source alpha is not uniformly 255; refusing this screenshot")

    seeds = _paint_seeds(source.shape[:2])
    foreground, trimap = _segment(source[..., :3], seeds)
    alpha = (foreground * 255).astype(np.uint8)

    # Pixel fidelity: RGB is copied verbatim; only the alpha channel is replaced.
    output = source.copy()
    output[..., 3] = alpha
    _save_png_atomic(output_path, output)
    source_hash_after = _sha256(input_path)
    if source_hash_before != source_hash_after:
        raise SystemExit("Source screenshot changed during the run; refusing to report")

    with Image.open(output_path) as saved_image:
        if saved_image.mode != "RGBA" or saved_image.size != EXPECTED_SIZE:
            raise SystemExit("Saved PNG did not round-trip as 920x1540 RGBA")
        saved = np.array(saved_image)
    if not np.array_equal(saved[..., 3], alpha):
        raise SystemExit("Saved alpha differs from the computed alpha")

    alpha_report = _alpha_report(saved)
    fidelity = _rgb_fidelity(saved, source)
    if alpha_report["foreground_pixels"] == 0:
        raise SystemExit("Segmentation produced no foreground pixels")
    if not fidelity["foreground_rgb_identical_to_source"]:
        raise SystemExit("Foreground RGB fidelity check failed")
    if not alpha_report["all_four_corners_transparent"]:
        raise SystemExit("Corner alpha check failed")

    report = {
        "schema": "si-game-cutout-draft-report/v1",
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "tool": "tools/extract_game_screenshot_candidate.py",
        "source": {
            "path": input_path.relative_to(ROOT).as_posix(),
            "sha256": source_hash_before,
            "sha256_after_run": source_hash_after,
            "size": [int(source.shape[1]), int(source.shape[0])],
            "mode": "RGBA",
            "alpha_all_255": True,
        },
        "output": {
            "path": output_path.relative_to(ROOT).as_posix(),
            "sha256": _sha256(output_path),
            "size": [int(saved.shape[1]), int(saved.shape[0])],
            "mode": "RGBA",
            "bytes": output_path.stat().st_size,
        },
        "report": {
            "path": report_path.relative_to(ROOT).as_posix(),
        },
        "segmentation": {
            "method": "cv2.grabCut with manual conservative polygons and seed strokes",
            "trimap_mode": "GC_INIT_WITH_MASK",
            "opencv_version": cv2.__version__,
            **trimap,
            "postprocess": "3x3 close, tiny-component cleanup (<24 px), force seeds, clear 1 px border",
            "probable_foreground_polygons": [
                [list(point) for point in polygon]
                for polygon in PROBABLE_FOREGROUND_POLYGONS
            ],
            "definite_foreground_ellipses_xy_rx_ry": [
                list(item) for item in FOREGROUND_ELLIPSES
            ],
            "definite_foreground_strokes_xyxy_thickness": [
                list(item) for item in FOREGROUND_STROKES
            ],
            "definite_background_boxes_xyxy": [
                list(box) for box in DEFINITE_BACKGROUND_BOXES
            ],
        },
        "alpha_distribution": alpha_report,
        "pixel_fidelity": fidelity,
        "review_regions_xyxy_approx": REVIEW_REGIONS,
        "non_destructive": {
            "allowed_input": CANONICAL_INPUT.relative_to(ROOT).as_posix(),
            "input_matches_allowlist": True,
            "source_unchanged_during_run": source_hash_before == source_hash_after,
            "write_targets": [
                output_path.relative_to(ROOT).as_posix(),
                report_path.relative_to(ROOT).as_posix(),
            ],
            "old_candidates_or_runtime_assets_written": False,
        },
        "limitations": LIMITATIONS,
    }
    _write_json_atomic(report_path, report)

    print(
        json.dumps(
            {
                "output": report["output"]["path"],
                "report": report["report"]["path"],
                "foreground_pixels": alpha_report["foreground_pixels"],
                "four_corners": alpha_report["four_corners"],
                "foreground_rgb_identical": fidelity["foreground_rgb_identical_to_source"],
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

#!/usr/bin/env python3
"""v4 人工透明蒙版草稿：只改 alpha，固定输入/输出，默认拒绝覆盖。

输入（只读，920x1540 RGBA）：art-source/v1.3/si-game-cutout-draft-v3.png、
  qa/si-body-reference-071.png。
输出：art-source/v1.3/si-game-cutout-draft-v4.png；qa/si-game-cutout-draft-v4-pink-preview.jpg、-desktop-size.png、-report.json。
"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

from PIL import Image, ImageChops, ImageDraw

ROOT = Path(__file__).resolve().parents[1]
SIZE = (920, 1540)
DESKTOP_SIZE = (384, 416)
PINK = (255, 105, 180)
LIGHT_GRAY = (232, 232, 232)
SOURCE = ROOT / "art-source" / "v1.3" / "si-game-cutout-draft-v3.png"
REFERENCE = ROOT / "qa" / "si-body-reference-071.png"
OUT_CUTOUT = ROOT / "art-source" / "v1.3" / "si-game-cutout-draft-v4.png"
OUT_PINK = ROOT / "qa" / "si-game-cutout-draft-v4-pink-preview.jpg"
OUT_DESKTOP = ROOT / "qa" / "si-game-cutout-draft-v4-desktop-size.png"
OUT_REPORT = ROOT / "qa" / "si-game-cutout-draft-v4-report.json"
OUTPUTS = (OUT_CUTOUT, OUT_PINK, OUT_DESKTOP, OUT_REPORT)

SHOULDER = [(543, 531), (583, 536), (610, 549), (635, 570),
            (625, 578), (574, 562), (545, 558)]
LEFT_RECT = (0, 730, 65, 850)              # 半开 [x0, y0, x1, y1)
LEFT_POLY = [(80, 825), (95, 825), (130, 735), (120, 735)]
RIGHT_BOOT_RECT = (525, 1500, 620, 1540)  # 半开；仅右靴下


def rel(path: Path) -> str:
    return path.relative_to(ROOT).as_posix()


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def load_rgba(path: Path) -> Image.Image:
    with Image.open(path) as image:
        if image.size != SIZE or image.mode != "RGBA":
            raise ValueError(f"{rel(path)}: 需要 920x1540 RGBA，实际 {image.size} {image.mode}")
        image.load()
        return image.copy()


def half_open_rect(draw: ImageDraw.ImageDraw, box: tuple[int, int, int, int], fill: int) -> None:
    x0, y0, x1, y1 = box
    draw.rectangle((x0, y0, x1 - 1, y1 - 1), fill=fill)


def poly_mask(points: list[tuple[int, int]]) -> Image.Image:
    mask = Image.new("L", SIZE, 0)
    ImageDraw.Draw(mask).polygon(points, fill=255)
    return mask


def rect_mask(box: tuple[int, int, int, int]) -> Image.Image:
    mask = Image.new("L", SIZE, 0)
    half_open_rect(ImageDraw.Draw(mask), box, 255)
    return mask


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--force", action="store_true", help="明确允许覆盖四个固定输出")
    args = parser.parse_args()
    existing = [rel(path) for path in OUTPUTS if path.exists()]
    if existing and not args.force:
        parser.error("拒绝覆盖已有输出：" + ", ".join(existing) + "；确需覆盖请加 --force")

    hashes_before = {SOURCE: sha256(SOURCE), REFERENCE: sha256(REFERENCE)}
    src = load_rgba(SOURCE)
    ref = load_rgba(REFERENCE)
    alpha = src.getchannel("A").copy()
    original_alpha = alpha.copy()

    # 仅改 alpha：右肩补回，左侧细线两块清零，右靴下残片清零。
    draw = ImageDraw.Draw(alpha)
    draw.polygon(SHOULDER, fill=255)
    half_open_rect(draw, LEFT_RECT, 0)
    draw.polygon(LEFT_POLY, fill=0)
    half_open_rect(draw, RIGHT_BOOT_RECT, 0)
    out = src.copy()
    out.putalpha(alpha)

    masks = {
        "right_shoulder_alpha_255": poly_mask(SHOULDER),
        "left_stray_rect_alpha_0": rect_mask(LEFT_RECT),
        "left_stray_polygon_alpha_0": poly_mask(LEFT_POLY),
        "right_boot_rect_alpha_0": rect_mask(RIGHT_BOOT_RECT),
    }
    allowed = Image.new("L", SIZE, 0)
    for mask in masks.values():
        allowed = ImageChops.lighter(allowed, mask)
    changed = ImageChops.difference(original_alpha, alpha).point(lambda v: 255 if v else 0)
    counts = {name: ImageChops.multiply(changed, mask).histogram()[255] for name, mask in masks.items()}
    total_changed = changed.histogram()[255]
    outside = ImageChops.subtract(changed, allowed)
    outside_changed = outside.histogram()[255]
    alpha_limited = outside.getbbox() is None
    counts_match = sum(counts.values()) == total_changed
    rgb_same = src.convert("RGB").tobytes() == out.convert("RGB").tobytes()
    ref_rgb_same = ref.convert("RGB").tobytes() == out.convert("RGB").tobytes()

    if not (rgb_same and ref_rgb_same):
        raise RuntimeError("输出 RGB 与源图或参考原图不一致")
    if not alpha_limited:
        raise RuntimeError(f"alpha 在声明区域外发生改动：{outside_changed} 像素")
    if not counts_match:
        raise RuntimeError("各区域改动像素数之和与总改动数不一致")

    out.save(OUT_CUTOUT, format="PNG")
    pink = Image.new("RGB", SIZE, PINK)
    pink.paste(out, (0, 0), out)
    pink.save(OUT_PINK, format="JPEG", quality=95, subsampling=0)

    flat = Image.new("RGB", SIZE, LIGHT_GRAY)
    flat.paste(out, (0, 0), out)
    scale = min(DESKTOP_SIZE[0] / SIZE[0], DESKTOP_SIZE[1] / SIZE[1])
    content_size = (round(SIZE[0] * scale), round(SIZE[1] * scale))
    content = flat.resize(content_size, Image.Resampling.LANCZOS)
    desktop = Image.new("RGB", DESKTOP_SIZE, LIGHT_GRAY)
    desktop.paste(content, ((DESKTOP_SIZE[0] - content.width) // 2,
                            (DESKTOP_SIZE[1] - content.height) // 2))
    desktop.save(OUT_DESKTOP, format="PNG")

    with Image.open(OUT_CUTOUT) as saved:
        saved.load()
        saved_rgb_same = saved.convert("RGB").tobytes() == src.convert("RGB").tobytes()
        saved_alpha_same = (saved.mode == "RGBA" and saved.size == SIZE
                            and saved.getchannel("A").tobytes() == alpha.tobytes())
    for path, expected in ((OUT_PINK, SIZE), (OUT_DESKTOP, DESKTOP_SIZE)):
        with Image.open(path) as preview:
            if preview.size != expected or preview.mode != "RGB":
                raise RuntimeError(f"预览输出尺寸或模式错误：{rel(path)}")
    if not (saved_rgb_same and saved_alpha_same):
        raise RuntimeError("输出 PNG 的 RGB/alpha 回读校验失败")

    hashes_after = {path: sha256(path) for path in hashes_before}
    hashes_same = hashes_after == hashes_before
    if not hashes_same:
        raise RuntimeError("输入文件哈希在运行前后不一致")

    report = {
        "artifact_kind": "human_alpha_mask_draft",
        "status": "人工透明蒙版草稿；仍需视觉复核；不是运行时素材，也不是完整动画。",
        "visual_review_required": True, "runtime_asset": False, "complete_animation": False,
        "coordinate_convention": "矩形为半开 [x0,y0,x1,y1)；polygon 使用 Pillow ImageDraw 坐标。",
        "scope": "1) 右肩内部黑色布/硬饰补回 alpha=255；2) 左侧游离细线两块区域 alpha=0；3) 右靴下方背景残片 alpha=0（仅右靴下，不碰左靴）。",
        "input_size": list(SIZE), "input_mode": "RGBA",
        "inputs": [
            {"path": rel(SOURCE), "role": "源 RGBA；仅改 alpha", "sha256_before": hashes_before[SOURCE], "sha256_after": hashes_after[SOURCE], "unchanged": hashes_same},
            {"path": rel(REFERENCE), "role": "只读参考；未参与合成", "sha256_before": hashes_before[REFERENCE], "sha256_after": hashes_after[REFERENCE], "unchanged": hashes_same},
        ],
        "manual_alpha_edits": [
            {"id": "right_shoulder_inner_black_cloth", "operation": "alpha=255", "regions": [{"type": "polygon", "points": [list(p) for p in SHOULDER]}], "alpha_changed_pixels": counts["right_shoulder_alpha_255"]},
            {"id": "left_stray_thin_line", "operation": "alpha=0", "regions": [{"type": "rectangle_half_open", "box": list(LEFT_RECT)}, {"type": "polygon", "points": [list(p) for p in LEFT_POLY]}], "alpha_changed_pixels": counts["left_stray_rect_alpha_0"] + counts["left_stray_polygon_alpha_0"]},
            {"id": "right_boot_background_residue", "operation": "alpha=0", "regions": [{"type": "rectangle_half_open", "box": list(RIGHT_BOOT_RECT)}], "alpha_changed_pixels": counts["right_boot_rect_alpha_0"]},
        ],
        "changed_pixel_statistics": {"total_alpha_changed_pixels": total_changed, "by_region": counts, "alpha_changed_outside_declared_regions": outside_changed},
        "verification": {"all_rgb_bytes_identical_to_source": rgb_same, "all_rgb_bytes_identical_to_reference": ref_rgb_same, "saved_png_rgb_identical_to_source": saved_rgb_same, "saved_png_alpha_matches_computed_alpha": saved_alpha_same, "alpha_changes_only_within_declared_regions": alpha_limited, "region_changed_pixels_sum_to_total": counts_match, "all_input_sha256_unchanged_before_after": hashes_same},
        "outputs": {"cutout": {"path": rel(OUT_CUTOUT), "size": list(SIZE), "mode": "RGBA"}, "pink_preview": {"path": rel(OUT_PINK), "size": list(SIZE), "mode": "RGB", "background_rgb": list(PINK)}, "desktop_size": {"path": rel(OUT_DESKTOP), "size": list(DESKTOP_SIZE), "mode": "RGB", "background_rgb": list(LIGHT_GRAY), "content_size": list(content_size)}, "report": {"path": rel(OUT_REPORT)}},
    }
    OUT_REPORT.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""Render a review-only breathing / weight-shift preview of the v18 candidate.

The source drawing is never redrawn.  Each frame is the same RGBA cutout with a
small, continuous shear (weight shift) and a vertical breathing scale, both
anchored at the sole line.  This is a geometric preview, not skeletal animation.
"""
from __future__ import annotations

import argparse
import math
import os
import sys
from pathlib import Path

try:
    from PIL import Image, ImageChops, ImageDraw, ImageFont, ImageMath, PngImagePlugin
except ImportError as exc:  # pragma: no cover - dependency is recorded in requirements.txt
    raise SystemExit(
        "Pillow is required to render the v18 motion preview. "
        "Use the repository virtual environment; do not install packages for this preview."
    ) from exc

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_SOURCE = ROOT / "art-source" / "v1.3" / "si-standing-candidate-v18.png"
DEFAULT_OUTPUT = ROOT / "qa" / "v13-motion-preview-v18.png"
DEFAULT_KEYFRAMES = ROOT / "qa" / "v13-motion-keyframes-v18.png"

CANVAS_WIDTH = 384
CANVAS_HEIGHT = 416
FPS = 24.0
DURATION_SECONDS = 8.0
FRAME_COUNT = int(round(FPS * DURATION_SECONDS))
SUPERSAMPLE = 2

# Safe space around the visible cutout.  The largest motion is 3.2 px sideways
# and 1.8 px upward, so six pixels of vertical margin cannot be clipped.
SAFE_X = 8
SAFE_TOP = 6
SAFE_BOTTOM = 6
MAX_LEAN_PX = 3.2
MAX_BREATH_PX = 1.8

# Static contact sheet layout.  The tests use this public output geometry.
KEYFRAME_OUTER = 24
KEYFRAME_GAP = 22
KEYFRAME_TITLE_Y = 24
KEYFRAME_SUBTITLE_Y = 62
KEYFRAME_LABEL_Y = 94
KEYFRAME_PANEL_Y = 124
KEYFRAME_WIDTH = KEYFRAME_OUTER * 2 + CANVAS_WIDTH * 4 + KEYFRAME_GAP * 3
KEYFRAME_HEIGHT = 606

INK = (35, 57, 48)
MUTED = (87, 108, 98)
RULE = (174, 190, 178)
PANEL_BORDER = (112, 137, 121)
SHEET_BG = (236, 241, 235)
CHECKER_LIGHT = (249, 251, 247)
CHECKER_DARK = (226, 235, 227)
WARNING = (145, 78, 57)


class PreviewError(RuntimeError):
    """A user-facing error with no partial production output."""


def _premultiply(image: Image.Image) -> Image.Image:
    """Premultiply RGBA so filtering cannot pull black out of transparent pixels."""
    r, g, b, a = image.convert("RGBA").split()
    return Image.merge(
        "RGBA",
        (
            ImageChops.multiply(r, a),
            ImageChops.multiply(g, a),
            ImageChops.multiply(b, a),
            a,
        ),
    )


def _unpremultiply(image: Image.Image) -> Image.Image:
    """Convert a filtered premultiplied RGBA image back to straight alpha."""
    r, g, b, a = image.convert("RGBA").split()
    alpha_safe = a.point(lambda value: max(value, 1)).convert("F")

    def restore(channel: Image.Image) -> Image.Image:
        values = ImageMath.lambda_eval(
            lambda args: args["channel"] * 255.0 / args["alpha"],
            channel=channel.convert("F"),
            alpha=alpha_safe,
        )
        return values.convert("L")

    return Image.merge("RGBA", (restore(r), restore(g), restore(b), a))


def _load_source(path: Path) -> tuple[Image.Image, tuple[int, int, int, int]]:
    if not path.exists():
        raise PreviewError(f"source image not found: {path}")
    if not path.is_file():
        raise PreviewError(f"source path is not a file: {path}")

    try:
        with Image.open(path) as image:
            image.load()
            if "A" not in image.getbands():
                raise PreviewError(
                    f"source image has no alpha channel; a transparent cutout is required: {path}"
                )
            source = image.convert("RGBA")
    except PreviewError:
        raise
    except Exception as exc:
        raise PreviewError(f"could not read source image {path}: {exc}") from exc

    bbox = source.getchannel("A").getbbox()
    if bbox is None:
        raise PreviewError(f"source image contains no visible pixels: {path}")
    if bbox[2] - bbox[0] < 2 or bbox[3] - bbox[1] < 2:
        raise PreviewError(f"source visible content is too small to preview: {path}")
    return source, bbox


def _build_base(
    source: Image.Image, source_bbox: tuple[int, int, int, int]
) -> tuple[Image.Image, int, int]:
    """Place a uniformly scaled, premultiplied cutout on the supersampled canvas."""
    bbox_width = source_bbox[2] - source_bbox[0]
    bbox_height = source_bbox[3] - source_bbox[1]
    scale = min(
        (CANVAS_WIDTH - SAFE_X * 2) / bbox_width,
        (CANVAS_HEIGHT - SAFE_TOP - SAFE_BOTTOM) / bbox_height,
    )
    if scale <= 0:
        raise PreviewError("source image does not fit the requested preview canvas")

    work_size = (CANVAS_WIDTH * SUPERSAMPLE, CANVAS_HEIGHT * SUPERSAMPLE)
    scaled_size = (
        max(1, round(source.width * scale * SUPERSAMPLE)),
        max(1, round(source.height * scale * SUPERSAMPLE)),
    )
    scaled = _premultiply(source).resize(scaled_size, Image.Resampling.LANCZOS)

    content_left = (CANVAS_WIDTH - bbox_width * scale) / 2.0
    content_bottom = CANVAS_HEIGHT - SAFE_BOTTOM
    left = round((content_left - source_bbox[0] * scale) * SUPERSAMPLE)
    top = round((content_bottom - source_bbox[3] * scale) * SUPERSAMPLE)

    base = Image.new("RGBA", work_size, (0, 0, 0, 0))
    base.paste(scaled, (left, top))
    visible = base.getchannel("A").getbbox()
    if visible is None:
        raise PreviewError("scaled source became empty during rendering")
    if visible[0] < 1 or visible[1] < 1 or visible[2] > work_size[0] - 1 or visible[3] > work_size[1] - 1:
        raise PreviewError("scaled source touches the canvas edge; refusing a clipped preview")

    ground = visible[3] - 1
    person_height = visible[3] - visible[1]
    if person_height < 2:
        raise PreviewError("scaled source has no usable height")
    return base, ground, person_height


def _motion_profiles(count: int, duration: float) -> tuple[list[float], list[float]]:
    """Two slow, loop-closing signals: weight shift and breathing.

    The lateral signal mixes one and two cycles per loop; breathing uses three
    cycles.  Both start from the neutral pose at t=0 and close at t=duration.
    """
    omega = 2.0 * math.pi / duration
    seconds = [index * duration / count for index in range(count)]
    raw_lean = [
        0.82 * math.sin(omega * t) + 0.18 * math.sin(2.0 * omega * t)
        for t in seconds
    ]
    peak = max(abs(value) for value in raw_lean)
    if peak < 1e-9:
        raise PreviewError("could not derive a non-zero weight-shift profile")
    lean = [value / peak for value in raw_lean]
    breath = [0.5 - 0.5 * math.cos(3.0 * omega * t) for t in seconds]
    return lean, breath


def _keyframe_indices(lean: list[float], breath: list[float]) -> tuple[int, int, int, int]:
    """Choose start, left-lean, right-lean and a distinct breath-high frame."""
    right = max(range(len(lean)), key=lean.__getitem__)
    left = min(range(len(lean)), key=lean.__getitem__)
    breath_peak = max(breath)
    peak_frames = [
        index for index, value in enumerate(breath) if value >= breath_peak - 1e-9
    ]
    breath_high = min(peak_frames, key=lambda index: abs(lean[index]))
    used = {0, left, right}
    if breath_high in used:
        alternatives = sorted(
            (index for index in range(len(lean)) if index not in used),
            key=lambda index: (-breath[index], abs(lean[index])),
        )
        if not alternatives:
            raise PreviewError("could not select four distinct review keyframes")
        breath_high = alternatives[0]
    return 0, left, right, breath_high


def _render_frames(
    base: Image.Image,
    ground: int,
    person_height: int,
    lean: list[float],
    breath: list[float],
) -> list[Image.Image]:
    """Apply one smooth, affine foot-anchored warp per frame."""
    frames: list[Image.Image] = []
    for lean_value, breath_value in zip(lean, breath):
        breath_scale = 1.0 + (MAX_BREATH_PX * SUPERSAMPLE / person_height) * breath_value
        shear = MAX_LEAN_PX * SUPERSAMPLE / person_height * lean_value

        # PIL maps destination pixels back to source pixels.
        # Forward: y_out = ground + s * (y_in - ground)
        #          x_out = x_in + shear * (ground - y_in)
        # At the sole line both transforms are exactly identity.
        matrix = (
            1.0,
            shear / breath_scale,
            -shear * ground / breath_scale,
            0.0,
            1.0 / breath_scale,
            ground * (1.0 - 1.0 / breath_scale),
        )
        warped = base.transform(
            base.size,
            Image.Transform.AFFINE,
            matrix,
            resample=Image.Resampling.BICUBIC,
            fillcolor=(0, 0, 0, 0),
        )
        filtered = warped.resize(
            (CANVAS_WIDTH, CANVAS_HEIGHT), Image.Resampling.LANCZOS
        )
        frames.append(_unpremultiply(filtered))
    return frames


def _frame_durations(count: int, fps: float) -> list[int]:
    """Integer millisecond APNG durations whose total is the exact loop length."""
    endpoints = [round(index * 1000.0 / fps) for index in range(count + 1)]
    return [endpoints[index + 1] - endpoints[index] for index in range(count)]


def _font_file(bold: bool = False) -> Path | None:
    windows = Path(os.environ.get("WINDIR", r"C:\Windows")) / "Fonts"
    names = (
        ("msyhbd.ttc", "msyh.ttc", "simhei.ttf", "simsun.ttc")
        if bold
        else ("msyh.ttc", "simhei.ttf", "simsun.ttc")
    )
    for name in names:
        path = windows / name
        if path.is_file():
            return path
    return None


def _load_font(size: int, bold: bool = False) -> tuple[ImageFont.ImageFont, bool]:
    path = _font_file(bold)
    if path is not None:
        return ImageFont.truetype(str(path), size), True
    try:
        return ImageFont.load_default(size=size), False
    except TypeError:  # older Pillow fallback
        return ImageFont.load_default(), False


def _draw_checker(
    draw: ImageDraw.ImageDraw, rectangle: tuple[int, int, int, int], cell: int = 12
) -> None:
    x0, y0, x1, y1 = rectangle
    for row, y in enumerate(range(y0, y1, cell)):
        for column, x in enumerate(range(x0, x1, cell)):
            color = CHECKER_LIGHT if (row + column) % 2 == 0 else CHECKER_DARK
            draw.rectangle(
                (x, y, min(x + cell - 1, x1 - 1), min(y + cell - 1, y1 - 1)),
                fill=color,
            )


def _write_keyframes(
    frames: list[Image.Image],
    indices: tuple[int, int, int, int],
    path: Path,
) -> None:
    title_font, has_cjk = _load_font(26, bold=True)
    subtitle_font, _ = _load_font(14)
    label_font, _ = _load_font(18, bold=True)
    badge_font, _ = _load_font(12)
    footer_font, _ = _load_font(13)

    if has_cjk:
        title = "v18 站立呼吸 / 重心微动作关键帧"
        subtitle = "同一张候选图的连续几何微动 · 非骨骼动画 · 面部仍待用户反馈"
        labels = ("起点 / START", "左重心 / LEFT", "右重心 / RIGHT", "呼吸高点 / BREATH HIGH")
        badge = "源图 v18 · 未获用户批准 · 运行素材未变"
        footer = (
            "384×416 · 24 fps（41/42 ms 帧）· 8.00 s 无缝循环 · 192 帧 · "
            "双脚锚定 · 仅几何微动，非骨骼动画"
        )
    else:
        title = "v18 Standing Breath / Weight-Shift Keyframes"
        subtitle = "Continuous geometric micro-motion from one candidate drawing; not skeletal animation."
        labels = ("START", "LEFT LEAN", "RIGHT LEAN", "BREATH HIGH")
        badge = "SOURCE v18 · NOT APPROVED · RUNTIME UNCHANGED"
        footer = (
            "384×416 · 24 fps nominal (41/42 ms frames) · 8.00 s seamless loop · "
            "192 frames · feet anchored · geometric micro-motion, not skeletal animation"
        )

    sheet = Image.new("RGB", (KEYFRAME_WIDTH, KEYFRAME_HEIGHT), SHEET_BG)
    draw = ImageDraw.Draw(sheet)
    draw.text((KEYFRAME_OUTER, KEYFRAME_TITLE_Y), title, font=title_font, fill=INK, anchor="lt")
    draw.text(
        (KEYFRAME_OUTER, KEYFRAME_SUBTITLE_Y),
        subtitle,
        font=subtitle_font,
        fill=MUTED,
        anchor="lt",
    )
    draw.text(
        (KEYFRAME_WIDTH - KEYFRAME_OUTER, KEYFRAME_TITLE_Y + 4),
        badge,
        font=badge_font,
        fill=WARNING,
        anchor="rt",
    )

    for panel_index, frame_index in enumerate(indices):
        panel_x = KEYFRAME_OUTER + panel_index * (CANVAS_WIDTH + KEYFRAME_GAP)
        panel_y = KEYFRAME_PANEL_Y
        _draw_checker(
            draw,
            (panel_x, panel_y, panel_x + CANVAS_WIDTH, panel_y + CANVAS_HEIGHT),
        )

        ground_y = panel_y + CANVAS_HEIGHT - SAFE_BOTTOM
        draw.line(
            (panel_x, ground_y, panel_x + CANVAS_WIDTH - 1, ground_y),
            fill=RULE,
            width=1,
        )
        sheet.paste(frames[frame_index], (panel_x, panel_y), frames[frame_index])
        draw.rectangle(
            (panel_x - 1, panel_y - 1, panel_x + CANVAS_WIDTH, panel_y + CANVAS_HEIGHT),
            outline=PANEL_BORDER,
            width=1,
        )

        time_s = frame_index * DURATION_SECONDS / FRAME_COUNT
        label = f"{labels[panel_index]} · {time_s:.2f}s"
        draw.text(
            (panel_x + CANVAS_WIDTH / 2, KEYFRAME_LABEL_Y),
            label,
            font=label_font,
            fill=INK,
            anchor="mt",
        )

    draw.line(
        (KEYFRAME_OUTER, 554, KEYFRAME_WIDTH - KEYFRAME_OUTER, 554),
        fill=RULE,
        width=1,
    )
    draw.text(
        (KEYFRAME_OUTER, 568),
        footer,
        font=footer_font,
        fill=MUTED,
        anchor="lt",
    )

    info = PngImagePlugin.PngInfo()
    info.add_text(
        "Description",
        "v18 standing breathing / weight-shift review keyframes. "
        "Same source drawing, geometric deformation only; not skeletal animation; not approved.",
    )
    info.add_text("Source", "art-source/v1.3/si-standing-candidate-v18.png")
    _atomic_save(sheet, path, pnginfo=info, compress_level=9)


def _atomic_save(image: Image.Image, path: Path, **save_options: object) -> None:
    """Write via a temporary sibling so a failed save does not replace a prior file."""
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise PreviewError(f"cannot create output directory {path.parent}: {exc}") from exc

    temporary = path.with_name(f".{path.name}.tmp")
    try:
        image.save(temporary, format="PNG", **save_options)
        os.replace(temporary, path)
    except Exception as exc:
        try:
            temporary.unlink(missing_ok=True)
        except OSError:
            pass
        raise PreviewError(f"could not write {path}: {exc}") from exc


def _write_apng(frames: list[Image.Image], durations: list[int], path: Path) -> None:
    info = PngImagePlugin.PngInfo()
    info.add_text(
        "Description",
        "v18 standing breathing / weight-shift micro-motion preview. "
        "Same source drawing, foot-anchored geometric deformation; not skeletal animation; not approved.",
    )
    info.add_text("Source", "art-source/v1.3/si-standing-candidate-v18.png")
    _atomic_save(
        frames[0],
        path,
        save_all=True,
        append_images=frames[1:],
        duration=durations,
        loop=0,
        disposal=0,  # APNG_DISPOSE_OP_NONE
        blend=0,  # APNG_BLEND_OP_SOURCE: transparent pixels replace old pixels
        pnginfo=info,
        compress_level=9,
    )


def _validate_paths(source: Path, output: Path, keyframes: Path) -> None:
    source_resolved = source.resolve()
    output_resolved = output.resolve()
    keyframes_resolved = keyframes.resolve()
    if output_resolved == source_resolved:
        raise PreviewError("refusing to overwrite the read-only source image")
    if keyframes_resolved == source_resolved:
        raise PreviewError("refusing to overwrite the read-only source image")
    if output_resolved == keyframes_resolved:
        raise PreviewError("--output and --keyframes must be different paths")
    for label, path in (("--output", output), ("--keyframes", keyframes)):
        if path.suffix.lower() != ".png":
            raise PreviewError(f"{label} must end in .png: {path}")


def render_preview(source: Path, output: Path, keyframes: Path) -> dict[str, object]:
    """Render both review artifacts and return a small, printable summary."""
    _validate_paths(source, output, keyframes)
    source_image, source_bbox = _load_source(source)
    base, ground, person_height = _build_base(source_image, source_bbox)
    lean, breath = _motion_profiles(FRAME_COUNT, DURATION_SECONDS)
    indices = _keyframe_indices(lean, breath)
    frames = _render_frames(base, ground, person_height, lean, breath)
    durations = _frame_durations(FRAME_COUNT, FPS)
    if len(frames) != FRAME_COUNT or sum(durations) != round(DURATION_SECONDS * 1000):
        raise PreviewError("internal timing check failed; refusing to write an inconsistent preview")

    _write_apng(frames, durations, output)
    _write_keyframes(frames, indices, keyframes)
    return {
        "frames": len(frames),
        "duration_ms": sum(durations),
        "loop": 0,
        "keyframes": indices,
        "keyframe_times_s": [
            index * DURATION_SECONDS / FRAME_COUNT for index in indices
        ],
    }


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Render a review-only v18 standing breathing / weight-shift APNG and "
            "a four-pose keyframe contact sheet. The source file is read-only."
        )
    )
    parser.add_argument("--source", type=Path, default=DEFAULT_SOURCE)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--keyframes", type=Path, default=DEFAULT_KEYFRAMES)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    try:
        summary = render_preview(args.source, args.output, args.keyframes)
    except PreviewError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    print(
        f"wrote {args.output} "
        f"({summary['frames']} frames, {summary['duration_ms']} ms, loop 0)"
    )
    print(f"wrote {args.keyframes} (keyframe indices: {summary['keyframes']})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

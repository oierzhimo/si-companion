#!/usr/bin/env python3
"""Play the review-only v18 APNG with Pillow decoding and a small Qt window.

The APNG produced for v18 is a real 192-frame animation.  Qt's PNG image
reader only reports its first frame on this machine, so this tool deliberately
does not use QMovie or QImageReader.  Pillow decodes every frame and its real
41/42 ms duration; each RGBA frame is then copied into an independently owned
QImage for painting.

This is a pending-review preview.  It is not a skeletal animation, not an
approved v18 design, and not a 1.3 final.
"""
from __future__ import annotations

import argparse
import os
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path

try:
    from PIL import Image
except ImportError as exc:  # pragma: no cover - dependency is recorded in requirements.txt
    raise SystemExit(
        "Pillow is required to decode the APNG preview. "
        "Use the repository virtual environment; do not install packages for this preview."
    ) from exc

try:
    from PySide6.QtCore import Qt, QTimer
    from PySide6.QtGui import QGuiApplication, QImage, QPainter
    from PySide6.QtWidgets import QApplication, QWidget
except ImportError as exc:  # pragma: no cover - dependency is recorded in requirements.txt
    raise SystemExit(
        "PySide6 is required to show the APNG preview. "
        "Use the repository virtual environment; do not install packages for this preview."
    ) from exc

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_SOURCE = ROOT / "qa" / "v13-motion-preview-v18.png"
V18_PORTRAIT = ROOT / "art-source" / "v1.3" / "si-standing-candidate-v18.png"

CANVAS_WIDTH = 384
CANVAS_HEIGHT = 416
SCREEN_MARGIN = 24

REVIEW_TITLE = "祀 · 站立微动作待审预览（未获用户确认）"
REVIEW_NOTE = (
    "来源由启动命令的 --source 选择；同一张图的几何微动作，非骨骼动画，"
    "不是 1.3 定稿。Esc 或右键退出；按住左键拖动。"
)
REVIEW_BANNER = (
    "祀 · 站立微动作待审预览（来源由命令选择；几何微动作，非骨骼动画，"
    "非 1.3 定稿）。Esc 或右键退出；按住左键拖动。"
)


class PreviewError(RuntimeError):
    """A user-facing failure that leaves the source and prior outputs intact."""


@dataclass
class MotionClip:
    """Decoded APNG frames and their real per-frame durations."""

    source: Path
    frames: list[QImage]
    durations_ms: list[int]

    def __post_init__(self) -> None:
        if len(self.frames) != len(self.durations_ms):
            raise PreviewError("decoded frame count and duration count do not match")
        if len(self.frames) < 2:
            raise PreviewError("the preview source must contain more than one frame")
        if any(duration <= 0 for duration in self.durations_ms):
            raise PreviewError("the preview source contains a non-positive frame duration")

    @property
    def frame_count(self) -> int:
        return len(self.frames)

    @property
    def total_duration_ms(self) -> int:
        return sum(self.durations_ms)

    def release(self) -> None:
        """Drop references to all decoded images as soon as they are no longer needed."""
        self.frames.clear()
        self.durations_ms.clear()


def qimage_from_rgba(image: Image.Image) -> QImage:
    """Copy a straight-alpha Pillow RGBA frame into an independently owned QImage."""
    rgba = image.convert("RGBA")
    width, height = rgba.size
    data = rgba.tobytes("raw", "RGBA")
    borrowed = QImage(
        data,
        width,
        height,
        width * 4,
        QImage.Format.Format_RGBA8888,
    )
    if borrowed.isNull():
        raise PreviewError("Qt could not construct an image from the decoded RGBA frame")
    # QImage(data, ...) borrows its buffer.  copy() makes Qt own the pixels before
    # the temporary bytes object goes out of scope.
    owned = borrowed.copy()
    if owned.isNull() or owned.width() != width or owned.height() != height:
        raise PreviewError("Qt could not copy the decoded RGBA frame")
    return owned


def decode_apng(path: Path) -> MotionClip:
    """Decode every APNG frame and its real duration with Pillow.

    Pillow composites APNG disposal/blend operations while seeking, so the
    resulting RGBA frames are complete canvas-sized images.  Decoding all 192
    frames costs about 117 MiB (123 MB) for the v18 canvas and avoids O(n^2)
    re-seeking during playback.
    """
    path = Path(path)
    if not path.exists():
        raise PreviewError(f"APNG source not found: {path}")
    if not path.is_file():
        raise PreviewError(f"APNG source is not a file: {path}")

    frames: list[QImage] = []
    durations: list[int] = []
    try:
        with Image.open(path) as image:
            if image.format != "PNG":
                raise PreviewError(f"source is not a PNG/APNG file: {path}")
            if not getattr(image, "is_animated", False):
                raise PreviewError(
                    "source is a static PNG, not an animated APNG; "
                    "refusing to present its first frame as motion"
                )
            frame_count = int(getattr(image, "n_frames", 1))
            if frame_count < 2:
                raise PreviewError("source APNG contains fewer than two frames")
            if image.size != (CANVAS_WIDTH, CANVAS_HEIGHT):
                raise PreviewError(
                    f"APNG is rendered at {image.width}x{image.height}, but the "
                    f"review window requires {CANVAS_WIDTH}x{CANVAS_HEIGHT}; "
                    "it will not be stretched"
                )

            for index in range(frame_count):
                image.seek(index)
                raw_duration = image.info.get("duration")
                if raw_duration is None:
                    raise PreviewError(f"APNG frame {index} has no duration metadata")
                try:
                    duration = int(round(float(raw_duration)))
                except (TypeError, ValueError) as exc:
                    raise PreviewError(
                        f"APNG frame {index} has an invalid duration: {raw_duration!r}"
                    ) from exc
                if duration <= 0:
                    raise PreviewError(f"APNG frame {index} has a non-positive duration")

                rgba = image.convert("RGBA")
                if rgba.size != (CANVAS_WIDTH, CANVAS_HEIGHT):
                    raise PreviewError(
                        f"APNG frame {index} is {rgba.width}x{rgba.height}; "
                        "refusing to stretch it"
                    )
                frames.append(qimage_from_rgba(rgba))
                durations.append(duration)
    except PreviewError:
        frames.clear()
        raise
    except Exception as exc:
        frames.clear()
        raise PreviewError(f"could not decode APNG {path}: {exc}") from exc

    return MotionClip(source=path.resolve(), frames=frames, durations_ms=durations)


def _path_key(path: Path) -> str:
    """Return a normalized absolute key that respects Windows case folding."""
    expanded = Path(path).expanduser()
    try:
        resolved = expanded.resolve(strict=False)
    except OSError:
        resolved = Path(os.path.abspath(os.fspath(expanded)))
    return os.path.normcase(str(resolved))


def _same_file(first: Path, second: Path) -> bool:
    if _path_key(first) == _path_key(second):
        return True
    try:
        return first.exists() and second.exists() and os.path.samefile(first, second)
    except OSError:
        return False


def validate_screenshot_target(target: Path, source: Path) -> None:
    """Refuse targets that would destroy the APNG or the v18 source portrait."""
    target = Path(target)
    source = Path(source)
    if target.suffix.lower() != ".png":
        raise PreviewError(f"the screenshot target must end in .png: {target}")
    if target.exists() and target.is_dir():
        raise PreviewError(f"the screenshot target is a directory: {target}")

    protected = (
        ("source APNG", source),
        ("v18 source portrait", V18_PORTRAIT),
        ("default preview APNG", DEFAULT_SOURCE),
    )
    for label, protected_path in protected:
        if _same_file(target, protected_path):
            raise PreviewError(
                f"refusing to overwrite the {label}: {protected_path}"
            )


def _atomic_save_png(image: QImage, target: Path) -> None:
    """Write a PNG through a sibling temporary file so failures do not clobber output."""
    target = Path(target)
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise PreviewError(f"cannot create screenshot directory {target.parent}: {exc}") from exc

    try:
        descriptor, temporary_name = tempfile.mkstemp(
            prefix=f".{target.stem}.",
            suffix=".png",
            dir=str(target.parent),
        )
        os.close(descriptor)
    except OSError as exc:
        raise PreviewError(f"cannot create a temporary screenshot beside {target}: {exc}") from exc

    temporary = Path(temporary_name)
    try:
        if not image.save(str(temporary), "PNG"):
            raise PreviewError(f"Qt could not write the screenshot PNG: {target}")
        os.replace(temporary, target)
    except PreviewError:
        raise
    except Exception as exc:
        raise PreviewError(f"could not write screenshot {target}: {exc}") from exc
    finally:
        try:
            temporary.unlink(missing_ok=True)
        except OSError:
            pass


def _ensure_offscreen_application(app: QGuiApplication | None = None) -> QGuiApplication:
    """Create a headless Qt application, forcing the offscreen QPA for screenshots."""
    instance = app or QGuiApplication.instance()
    if instance is not None:
        return instance
    os.environ["QT_QPA_PLATFORM"] = "offscreen"
    return QGuiApplication([])


def screenshot_apng(source: Path, target: Path, frame_index: int = 0) -> tuple[int, int, int]:
    """Write one real decoded APNG frame and return (index, duration, frame_count)."""
    source = Path(source)
    target = Path(target)
    if frame_index < 0:
        raise PreviewError("--frame-index must be zero or greater")
    validate_screenshot_target(target, source)

    # Keep a Python reference alive until Qt has finished writing the PNG.
    _application = _ensure_offscreen_application()
    clip = decode_apng(source)
    try:
        if frame_index >= clip.frame_count:
            raise PreviewError(
                f"--frame-index {frame_index} is outside the APNG range "
                f"0..{clip.frame_count - 1}"
            )
        _atomic_save_png(clip.frames[frame_index], target)
        return frame_index, clip.durations_ms[frame_index], clip.frame_count
    finally:
        clip.release()


class V13MotionPlayer(QWidget):
    """A frameless, non-topmost, transparent 384x416 APNG preview window."""

    def __init__(
        self,
        clip: MotionClip,
        initial_frame: int = 0,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        if not clip.frames or len(clip.frames) != len(clip.durations_ms):
            raise PreviewError("cannot show an empty or inconsistent motion clip")
        if initial_frame < 0 or initial_frame >= clip.frame_count:
            raise PreviewError(
                f"initial frame {initial_frame} is outside 0..{clip.frame_count - 1}"
            )

        # A new list owns this window's references.  Closing the window clears
        # them without disturbing a caller that still owns the MotionClip.
        self.frames = list(clip.frames)
        self.durations = list(clip.durations_ms)
        self.source = clip.source
        self._current_index = initial_frame
        self._drag_offset = None
        self._placed = False
        self._closed = False

        self.setWindowTitle(f"{REVIEW_TITLE} · {clip.source.stem}")
        self.setAccessibleName(REVIEW_TITLE)
        self.setAccessibleDescription(REVIEW_NOTE)
        self.setToolTip(REVIEW_NOTE)
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint | Qt.WindowType.Window
        )
        # Explicitly leave the window out of the always-on-top band.
        self.setWindowFlag(Qt.WindowType.WindowStaysOnTopHint, False)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setAttribute(Qt.WidgetAttribute.WA_NoSystemBackground, True)
        self.setAutoFillBackground(False)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setMouseTracking(False)
        self.setFixedSize(CANVAS_WIDTH, CANVAS_HEIGHT)

        # One single-shot timer per displayed frame preserves the real 41/42 ms
        # sequence instead of approximating every frame with one fixed interval.
        self.timer: QTimer | None = QTimer(self)
        self.timer.setSingleShot(True)
        self.timer.setTimerType(Qt.TimerType.PreciseTimer)
        self.timer.timeout.connect(self._advance_frame)

    @property
    def frame_count(self) -> int:
        return len(self.frames)

    @property
    def current_index(self) -> int:
        return self._current_index

    @property
    def current_image(self) -> QImage | None:
        if not self.frames or self._current_index >= len(self.frames):
            return None
        return self.frames[self._current_index]

    def showEvent(self, event) -> None:
        super().showEvent(event)
        if not self._placed:
            self._position_near_screen_edge()
            self._placed = True
        if self.timer is not None and not self.timer.isActive() and self.frames:
            self.timer.start(self.durations[self._current_index])
        # The window takes focus only after the administrator launches it; no
        # global shortcut, keyboard grab or input hook is installed.
        self.activateWindow()
        self.setFocus(Qt.FocusReason.ActiveWindowFocusReason)

    def _position_near_screen_edge(self) -> None:
        screen = QGuiApplication.primaryScreen()
        if screen is None:
            return
        available = screen.availableGeometry()
        x = available.right() - self.width() + 1 - SCREEN_MARGIN
        y = available.bottom() - self.height() + 1 - SCREEN_MARGIN
        max_x = available.left() + max(0, available.width() - self.width())
        max_y = available.top() + max(0, available.height() - self.height())
        self.move(
            min(max(x, available.left()), max_x),
            min(max(y, available.top()), max_y),
        )

    def _advance_frame(self) -> None:
        if self._closed or not self.frames:
            return
        self._current_index = (self._current_index + 1) % len(self.frames)
        self.update()
        if self.timer is not None:
            self.timer.start(self.durations[self._current_index])

    def paintEvent(self, event) -> None:
        del event
        painter = QPainter(self)
        try:
            image = self.current_image
            if image is None:
                painter.setCompositionMode(
                    QPainter.CompositionMode.CompositionMode_Source
                )
                painter.fillRect(self.rect(), Qt.GlobalColor.transparent)
                return
            # Source replaces every pixel, including transparent ones, so a
            # previous pose cannot survive beneath the next frame.
            painter.setCompositionMode(
                QPainter.CompositionMode.CompositionMode_Source
            )
            painter.drawImage(0, 0, image)
        finally:
            painter.end()

    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.RightButton:
            self.close()
            event.accept()
            return
        if event.button() == Qt.MouseButton.LeftButton:
            self._drag_offset = (
                event.globalPosition().toPoint() - self.frameGeometry().topLeft()
            )
            self.setCursor(Qt.CursorShape.ClosedHandCursor)
            self.setFocus(Qt.FocusReason.MouseFocusReason)
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event) -> None:
        if (
            self._drag_offset is not None
            and event.buttons() & Qt.MouseButton.LeftButton
        ):
            self.move(event.globalPosition().toPoint() - self._drag_offset)
            event.accept()
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton and self._drag_offset is not None:
            self._drag_offset = None
            self.unsetCursor()
            event.accept()
            return
        super().mouseReleaseEvent(event)

    def contextMenuEvent(self, event) -> None:
        # Right-click is the exit gesture; do not open a context menu.
        self.close()
        event.accept()

    def keyPressEvent(self, event) -> None:
        if event.key() == Qt.Key.Key_Escape:
            self.close()
            event.accept()
            return
        super().keyPressEvent(event)

    def closeEvent(self, event) -> None:
        self._closed = True
        self.release_resources()
        super().closeEvent(event)

    def release_resources(self) -> None:
        """Stop timing and drop every decoded image; safe to call more than once."""
        timer = self.timer
        self.timer = None
        if timer is not None:
            timer.stop()
            timer.deleteLater()
        self.frames.clear()
        self.durations.clear()
        self._current_index = 0
        self._drag_offset = None
        self.unsetCursor()


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Show a pending-review standing micro-motion APNG with Pillow decoding "
            "and QPainter.  The source is chosen by --source; the source and v18 "
            "portrait are read-only.  This is a geometric micro-motion, not a "
            "skeletal animation and not an approved 1.3 final."
        )
    )
    parser.add_argument(
        "--source",
        type=Path,
        default=DEFAULT_SOURCE,
        help=f"APNG to decode (default: {DEFAULT_SOURCE})",
    )
    parser.add_argument(
        "--screenshot",
        type=Path,
        default=None,
        help="write one real frame to this PNG in offscreen mode and exit",
    )
    parser.add_argument(
        "--frame-index",
        type=int,
        default=None,
        help="zero-based frame for --screenshot (default: 0); also the initial window frame",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    try:
        if args.screenshot is not None:
            index = 0 if args.frame_index is None else args.frame_index
            used_index, duration, frame_count = screenshot_apng(
                args.source, args.screenshot, index
            )
            print(
                f"{REVIEW_TITLE}\n"
                f"wrote {args.screenshot} "
                f"(frame {used_index}/{frame_count - 1}, {duration} ms, offscreen)"
            )
            return 0

        initial_index = 0 if args.frame_index is None else args.frame_index
        clip = decode_apng(args.source)
        try:
            application = QApplication.instance()
            if application is None:
                application = QApplication([])
            window = V13MotionPlayer(clip, initial_frame=initial_index)
        finally:
            clip.release()

        print(REVIEW_BANNER, flush=True)
        window.show()
        try:
            return application.exec()
        finally:
            window.release_resources()
    except PreviewError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())

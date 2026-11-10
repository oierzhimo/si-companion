#!/usr/bin/env python3
"""Index two fixed, read-only Endfield recordings into contact sheets.

Samples 15 timestamps per video from 0 to (estimated duration - 0.5 s), saves
384x216 captioned JPEG frames plus a 5x3 contact sheet, and writes index.json.
Sources are read-only; nothing outside the output directory is written and
existing output is refused unless --overwrite. cv2 comes from qa/segmentation-deps.
"""
import argparse, hashlib, json, sys
from datetime import datetime, timezone
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "qa" / "segmentation-deps"))
import cv2

SOURCE_ROOT = Path(r"D:\终末地小祀")
FIXED = (SOURCE_ROOT / "Endfield 2026-10-16 18-45-37.mp4",
         SOURCE_ROOT / "Endfield 2026-10-16 18-52-11.mp4")
DEFAULT_OUTPUT = ROOT / "qa" / "game-video-index-v1"
SAMPLES, COLUMNS, ROWS = 15, 5, 3
FRAME_SIZE, JPEG_QUALITY, END_MARGIN_SEC = (384, 216), 92, 0.5
PROTECTED = (ROOT / "assets", ROOT / "release", ROOT / "mobile", ROOT / "qa" / "segmentation-deps")
DURATION_NOTE = (
    "frame_count / fps is only an estimate of duration. These captures may be VFR, "
    "so no exact duration is available from the OpenCV probes; requested times are "
    "approximately uniform and pos_msec/pos_frames record what decoding returned."
)
FONT = ImageFont.load_default(size=18)  # requirements.txt pins Pillow 12.3.0


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _guard_output(path: Path, overwrite: bool) -> Path:
    out = path.resolve()
    if (not out.is_relative_to(ROOT) or out == ROOT
            or any(out == p.resolve() or out.is_relative_to(p.resolve()) for p in PROTECTED)):
        raise SystemExit(f"Refusing output path: {out}")
    if out.exists() and (not out.is_dir() or not overwrite):
        raise SystemExit(f"Output directory exists: {out}; pass --overwrite to reuse it")
    return out


def _timecode(seconds: float) -> str:
    ms = int(round(seconds * 1000))
    return f"{ms // 60000:02d}:{ms // 1000 % 60:02d}.{ms % 1000:03d}"


def _probe(path: Path) -> dict:
    if not path.is_file():
        raise SystemExit(f"Fixed source is missing: {path}")
    cap = cv2.VideoCapture(str(path))
    if not cap.isOpened():
        raise SystemExit(f"Cannot open source video: {path}")
    fps, frames = cap.get(cv2.CAP_PROP_FPS), int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
    width, height = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH)), int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
    cap.release()
    if min(fps, frames, width, height) <= 0:
        raise SystemExit(f"Unusable metadata for {path.name}: fps={fps} frames={frames}")
    return {"width": width, "height": height, "fps": fps, "frame_count": frames,
            "duration_est": frames / fps}


def _render(bgr, caption: str) -> Image.Image:
    image = Image.fromarray(cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB)).resize(
        FRAME_SIZE, Image.Resampling.LANCZOS)
    ImageDraw.Draw(image).text((8, FRAME_SIZE[1] - 28), caption, font=FONT,
                               fill="white", stroke_width=2, stroke_fill="black")
    return image


def _decode(path: Path, probe: dict) -> tuple[list[dict], list[Image.Image]]:
    span = probe["duration_est"] - END_MARGIN_SEC
    if span <= 0:
        raise SystemExit(f"Estimated duration too short: {path.name}")
    cap = cv2.VideoCapture(str(path))
    if not cap.isOpened():
        raise SystemExit(f"Cannot reopen source video: {path}")
    samples, images = [], []
    try:
        for i in range(SAMPLES):
            seconds = span * i / (SAMPLES - 1)
            target = min(max(int(round(seconds * probe["fps"])), 0), probe["frame_count"] - 1)
            cap.set(cv2.CAP_PROP_POS_FRAMES, target)
            ok, frame = cap.read()
            if not ok or frame is None:
                raise SystemExit(f"Failed to decode {path.name} at {seconds:.3f}s (frame {target})")
            pos_msec, pos_frames = cap.get(cv2.CAP_PROP_POS_MSEC), cap.get(cv2.CAP_PROP_POS_FRAMES)
            images.append(_render(frame, f"{i:02d}  {_timecode(seconds)}  {seconds:.2f}s"))
            samples.append({"index": i, "requested_sec": round(seconds, 6),
                            "requested_frame": target, "pos_msec": round(pos_msec, 3),
                            "pos_frames": pos_frames})
    finally:
        cap.release()
    return samples, images


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Read-only index of the fixed reference videos")
    parser.add_argument("--output", default=str(DEFAULT_OUTPUT.relative_to(ROOT)))
    parser.add_argument("--overwrite", action="store_true")
    args = parser.parse_args(argv)
    requested = Path(args.output).expanduser()
    output = _guard_output(requested if requested.is_absolute() else ROOT / requested, args.overwrite)

    probes = {path: _probe(path) for path in FIXED}
    before = {path: _sha256(path) for path in FIXED}
    ordered = sorted(FIXED, key=lambda p: probes[p]["duration_est"], reverse=True)
    labels = dict(zip(ordered, ("long", "short")))
    decoded = {path: _decode(path, probes[path]) for path in FIXED}
    after = {path: _sha256(path) for path in FIXED}
    if before != after:
        raise SystemExit("A source video changed during the run; refusing to continue")

    output.mkdir(parents=True, exist_ok=True)
    sources = []
    for path in FIXED:
        label = labels[path]
        samples, images = decoded[path]
        for i, image in enumerate(images):
            image.save(output / f"{label}-{i:02d}.jpg", format="JPEG", quality=JPEG_QUALITY)
        sheet = Image.new("RGB", (COLUMNS * FRAME_SIZE[0], ROWS * FRAME_SIZE[1]), "black")
        for i, image in enumerate(images):
            sheet.paste(image, (i % COLUMNS * FRAME_SIZE[0], i // COLUMNS * FRAME_SIZE[1]))
        sheet.save(output / f"{label}-contact.jpg", format="JPEG", quality=JPEG_QUALITY)
        meta = probes[path]
        sources.append({
            "label": label, "path": str(path), "bytes": path.stat().st_size,
            "sha256_before": before[path], "sha256_after": after[path],
            "opencv": {"version": cv2.__version__, **meta},
            "duration_estimate": {"seconds": round(meta["duration_est"], 6),
                                  "method": "frame_count / fps", "is_estimate": True},
            "samples": [{**sample, "frame_file": f"{label}-{sample['index']:02d}.jpg"}
                        for sample in samples],
            "contact_sheet": {"file": f"{label}-contact.jpg", "columns": COLUMNS, "rows": ROWS,
                              "size": [sheet.width, sheet.height]}})

    report = {"schema": "si-game-video-index/v1",
              "created_at_utc": datetime.now(timezone.utc).isoformat(),
              "output_dir": output.relative_to(ROOT).as_posix(),
              "duration_note": DURATION_NOTE,
              "label_assignment": {"long": str(ordered[0]), "short": str(ordered[1])},
              "sources": sources, "non_destructive": "sources read-only; writes stay inside output_dir"}
    (output / "index.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n",
                                       encoding="utf-8")
    print(f"{report['output_dir']}: {len(FIXED)} videos, {SAMPLES} frames + 1 sheet each")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

#!/usr/bin/env python3
"""Read-only 1.35 s, ~12 fps GIF of Sì's real in-game expression (v3 window).

Fixed source: D:\\终末地小祀\\Endfield 2026-10-16 18-52-11.mp4.  Sequential
51.85-53.2 s decode; frames are picked by real CAP_PROP_POS_MSEC times, never
by frame_count / average fps (VFR).  Native 2560x1440 frames are cropped to
(600,150,2040,1440) and scaled proportionally to 384 px wide.  Writes a 1.35 s
GIF, a timecoded 3x2 contact sheet and JSON; existing outputs are refused
unless --overwrite.  Crop, resize and palette quantization only.  Raw in-game
reference, not a desktop-pet animation.

The earlier v1 window 49-53 s was actually produced, but the source cuts to
another character at 50.58-51.35 s, so v1 is not a continuous Sì expression
reference and was withdrawn.  v2 (51.7-53.2 s) was actually produced and
checked across all 18 frames: frame 0 at 51.700 s is still the previous
character and only frame 1 (~51.800 s) onward shows Sì, so v2 is withdrawn as
well.  Both v1 and v2 outputs are kept as failure evidence; this script now
writes v3 and never deletes or overwrites them.  v3 still has to be checked
across all 16 frames to confirm every frame shows Sì; this script does not
claim that check has passed.

--truecolor-only writes only the two fixed qa/si-expression-reference-v3-
truecolor.* outputs: the same read-only source, window, frame picking and
SHA-256 before/after checks, but the 16 RGB frames go into an 8-bit RGB APNG
without palette quantization or dithering, 1350 ms total, loop 0, disposal 0,
blend 0, and no reversed or tweened frames.  After writing, every frame is
decoded back from the APNG and its RGB bytes are compared with the original
to_image() crop+LANCZOS result; any difference fails the run and is recorded
as pixel_roundtrip_equal=false.  This mode has not been run yet, so it is not
claimed to pass.  The default GIF mode is unchanged.
"""
import argparse, hashlib, json, sys
from datetime import datetime, timezone
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "qa" / "segmentation-deps"))
import cv2

SOURCE = Path(r"D:\终末地小祀\Endfield 2026-10-16 18-52-11.mp4")
QA = ROOT / "qa"
GIF_PATH = QA / "si-expression-reference-v3.gif"
CONTACT_PATH = QA / "si-expression-reference-v3-contact.jpg"
JSON_PATH = QA / "si-expression-reference-v3.json"
TRUECOLOR_PNG_PATH = QA / "si-expression-reference-v3-truecolor.png"
TRUECOLOR_JSON_PATH = QA / "si-expression-reference-v3-truecolor.json"
START_SEC, END_SEC, TARGET_FPS, TARGET_FRAMES = 51.85, 53.2, 12.0, 16
CROP = (600, 150, 2040, 1440)  # left, top, right, bottom on the native 2560x1440 frame
OUT_WIDTH, MIN_FRAMES = 384, 16
GIF_TOTAL_MS, COLUMNS, ROWS, JPEG_QUALITY = 1350, 3, 2, 92
APNG_DISPOSAL, APNG_BLEND = 0, 0  # APNG OP_NONE (no disposal) / OP_SOURCE (no blending)
FONT = ImageFont.load_default(size=18)  # requirements.txt pins Pillow 12.3.0

def sha256(path):
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()

def timecode(seconds):
    ms = int(round(seconds * 1000))
    return f"{ms // 60000:02d}:{ms // 1000 % 60:02d}.{ms % 1000:03d}"

def to_image(frame):
    height, width = frame.shape[:2]
    if (width, height) != (2560, 1440):
        raise SystemExit(f"Decoded frame is {width}x{height}; expected native 2560x1440")
    image = Image.fromarray(cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)).crop(CROP)
    return image.resize((OUT_WIDTH, round(image.height * OUT_WIDTH / image.width)), Image.Resampling.LANCZOS)

def decode(path):
    cap = cv2.VideoCapture(str(path))
    if not cap.isOpened():
        cap.release()
        raise SystemExit(f"Cannot open source video: {path}")
    images, samples, last_ms, reason = [], [], None, "end_of_video"
    try:
        width, height = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH)), int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
        meta = {"width": width, "height": height, "reported_fps": round(cap.get(cv2.CAP_PROP_FPS), 6),
                "frame_count": int(cap.get(cv2.CAP_PROP_FRAME_COUNT))}
        if (width, height) != (2560, 1440):
            raise SystemExit(f"Source is {width}x{height}; the fixed crop needs native 2560x1440")
        cap.set(cv2.CAP_PROP_POS_MSEC, (START_SEC - 1.0) * 1000.0)
        lo, hi, wanted = START_SEC * 1000.0, END_SEC * 1000.0, TARGET_FRAMES
        while len(images) < wanted:
            ok, frame = cap.read()
            if not ok or frame is None:
                break
            t_ms = last_ms = cap.get(cv2.CAP_PROP_POS_MSEC)
            if t_ms > hi:
                reason = "requested_end"
                break
            if t_ms < lo + len(images) * 1000.0 / TARGET_FPS:
                continue
            images.append(to_image(frame))
            samples.append({"index": len(images) - 1, "pos_msec": round(t_ms, 3),
                            "source_sec": round(t_ms / 1000.0, 3), "timecode": timecode(t_ms / 1000.0)})
        else:
            reason = "requested_end"
    finally:
        cap.release()
    if last_ms is None:
        raise SystemExit(f"No decodable frames between {START_SEC} and {END_SEC} s in {path}")
    last_sec, ended = round(last_ms / 1000.0, 3), reason == "end_of_video"
    return images, samples, meta, {"reason": reason, "last_decoded_sec": last_sec,
                                   "real_end_sec": last_sec if ended else None,
                                   "end_shortfall_sec": round(max(0.0, END_SEC - last_sec), 3) if ended else 0.0}

def gif_durations(count):
    units = GIF_TOTAL_MS // 10
    base, extra = divmod(units, count)
    return [10 * (base + (i + 1) * extra // count - i * extra // count) for i in range(count)]

def contact_sheet(images, samples):
    width, height = images[0].size
    sheet = Image.new("RGB", (COLUMNS * width, ROWS * height), "black")
    picks = [round(i * (len(images) - 1) / (COLUMNS * ROWS - 1)) for i in range(COLUMNS * ROWS)]
    for cell, index in enumerate(picks):
        tile = images[index].copy()
        ImageDraw.Draw(tile).text((8, height - 26), f"{index:02d}  src {timecode(samples[index]['source_sec'])}",
                                  font=FONT, fill="white", stroke_width=2, stroke_fill="black")
        sheet.paste(tile, (cell % COLUMNS * width, cell // COLUMNS * height))
    return sheet, picks

def write_truecolor(images, samples, meta, before, after, stop):
    """Write only the unquantized RGB APNG plus its report, then verify it frame by frame."""
    durations = gif_durations(len(images))
    repeated = [i for i in range(1, len(images)) if images[i].tobytes() == images[i - 1].tobytes()]
    if repeated:
        raise SystemExit(f"Frames {repeated} repeat the previous frame byte for byte; Pillow's APNG writer would "
                         f"merge them, so a faithful {len(images)}-frame APNG cannot be written; nothing was written")
    images[0].save(TRUECOLOR_PNG_PATH, format="PNG", save_all=True, append_images=images[1:], duration=durations,
                   loop=0, disposal=APNG_DISPOSAL, blend=APNG_BLEND)
    mismatched, readback_durations = [], []
    with Image.open(TRUECOLOR_PNG_PATH) as apng:
        count, loop_readback = apng.n_frames, apng.info.get("loop")
        for index, expected in enumerate(images):
            if index >= count:
                mismatched.append(index)
                continue
            apng.seek(index)
            readback_durations.append(apng.info.get("duration"))
            actual = apng.convert("RGB")
            if actual.size != expected.size or actual.tobytes() != expected.tobytes():
                mismatched.append(index)
    equal = count == len(images) and not mismatched
    timing_ok = (len(readback_durations) == len(images) and None not in readback_durations
                 and sum(readback_durations) == GIF_TOTAL_MS)
    report = {
        "schema": "si-expression-reference/v3-truecolor",
        "source": {"path": str(SOURCE), "bytes": SOURCE.stat().st_size, "sha256_before": before,
                   "sha256_after": after, "opencv_version": cv2.__version__, **meta},
        "window": {"requested_start_sec": START_SEC, "requested_end_sec": END_SEC, "target_fps": TARGET_FPS,
                   "target_frames": TARGET_FRAMES, "min_frames": MIN_FRAMES,
                   "stop_reason": stop["reason"], "last_decoded_sec": stop["last_decoded_sec"],
                   "real_end_sec": stop["real_end_sec"], "end_shortfall_sec": stop["end_shortfall_sec"]},
        "processing": {"method": "same read-only decode and frame picking as the v3 GIF; frames are saved as plain RGB, "
                                 "no palette quantization, no dithering, no retouch, no reversed or tweened frames",
                       "crop_px": list(CROP), "crop_size_px": [CROP[2] - CROP[0], CROP[3] - CROP[1]],
                       "output_size_px": list(images[0].size), "resize": "proportional to 384 px wide, LANCZOS"},
        "frames": samples,
        "apng": {"file": TRUECOLOR_PNG_PATH.name, "pixel_mode": "RGB", "frame_count": count,
                 "loop": 0, "disposal": APNG_DISPOSAL, "blend": APNG_BLEND,
                 "duration_total_ms": sum(durations), "durations_ms": durations,
                 "quantization": "none", "reverse_or_tween_frame_added": False,
                 "frame_encoding": "lossless PNG; Pillow may store a frame as a sub-rectangle update, which is "
                                   "covered by disposal 0 / blend 0 (replace, no blending) and by the byte-exact "
                                   "read-back check below",
                 "readback_frame_count": count, "readback_loop": loop_readback,
                 "readback_durations_ms": readback_durations,
                 "timing_roundtrip_equal": timing_ok},
        "verification": {"pixel_roundtrip_equal": equal, "frames_expected": len(images),
                         "frames_checked": len(images) - len(mismatched), "mismatched_frames": mismatched,
                         "method": "each APNG frame is decoded back and its RGB bytes are compared with the original "
                                   "to_image() crop+LANCZOS result; byte equality is required"},
        "reference": {"kind": "raw in-game expression reference", "is_desktop_pet_animation": False,
                      "used_generative_model": False,
                      "note": "原始实机表情参考（v3 窗口 51.85-53.2 秒，1.35 秒 / 16 帧）的真彩 APNG 版本，"
                              "不是桌宠动画，也不是生成模型输出；仅裁切、等比缩放，未量化、未加反向或补间帧。"
                              "GIF 版本经过 256 色量化，色彩以本真彩 APNG 和原片为准。",
                      "visual_review": "v3 窗口的 16 帧已由主协作者于 2026-10-27 逐帧核对，全部为祀；"
                                       "本真彩 APNG 复用同一窗口与取帧逻辑，但本模式输出本身尚未逐帧视觉复核。",
                      "v1_status": "v1（49-53 秒）因 50.58-51.35 秒切到另一角色而撤回，旧输出保留为失败证据。",
                      "v2_status": "v2（51.7-53.2 秒 / 18 帧）首帧 51.700 秒仍是上一角色，已撤回；"
                                   "旧输出保留为失败证据，本脚本不删除、不覆盖。",
                      "scope_note": "可作为原始表情动作参考；不构成桌宠动画，也不作为定稿依据。"},
        "non_destructive": "source video read-only; writes stay under qa/"}
    TRUECOLOR_JSON_PATH.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    if not (equal and timing_ok):
        raise SystemExit(f"Truecolor APNG verification failed: pixel_roundtrip_equal={equal}, "
                         f"timing_roundtrip_equal={timing_ok}, mismatched frames {mismatched}; "
                         f"report written to {TRUECOLOR_JSON_PATH.relative_to(ROOT)}")
    print(f"{TRUECOLOR_PNG_PATH.relative_to(ROOT)}: {count} RGB frames, {sum(durations)} ms, "
          f"source {samples[0]['timecode']}..{samples[-1]['timecode']}, pixel_roundtrip_equal=True")
    return 0

def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--overwrite", action="store_true", help="replace the fixed outputs of the selected mode")
    parser.add_argument("--truecolor-only", action="store_true",
                        help="write only the unquantized RGB APNG and its JSON; no GIF, no contact sheet")
    args = parser.parse_args(argv)
    outputs = (TRUECOLOR_PNG_PATH, TRUECOLOR_JSON_PATH) if args.truecolor_only else (GIF_PATH, CONTACT_PATH, JSON_PATH)
    existing = [p for p in outputs if p.exists()]
    if existing and not args.overwrite:
        raise SystemExit("Refusing to overwrite: " + ", ".join(map(str, existing)) + "; pass --overwrite")
    if not SOURCE.is_file():
        raise SystemExit(f"Fixed source is missing: {SOURCE}")
    before = sha256(SOURCE)
    images, samples, meta, stop = decode(SOURCE)
    after = sha256(SOURCE)
    if after != before:
        raise SystemExit("The source video changed during decoding; refusing to write outputs")
    if len(images) < MIN_FRAMES:
        raise SystemExit(f"Only {len(images)} frames selected in {START_SEC}-{END_SEC}s; need at least "
                         f"{MIN_FRAMES}; last decoded {stop['last_decoded_sec']}s, real end {stop['real_end_sec']}s")
    if args.truecolor_only:
        return write_truecolor(images, samples, meta, before, after, stop)
    sheet, picks = contact_sheet(images, samples)
    paletted = [image.quantize(colors=256, method=Image.Quantize.MEDIANCUT,
                               dither=Image.Dither.FLOYDSTEINBERG) for image in images]
    durations = gif_durations(len(paletted))
    paletted[0].save(GIF_PATH, save_all=True, append_images=paletted[1:], duration=durations,
                     loop=0, disposal=2, optimize=False)
    sheet.save(CONTACT_PATH, format="JPEG", quality=JPEG_QUALITY)
    report = {
        "schema": "si-expression-reference/v3",
        "source": {"path": str(SOURCE), "bytes": SOURCE.stat().st_size, "sha256_before": before,
                   "sha256_after": after, "opencv_version": cv2.__version__, **meta},
        "window": {"requested_start_sec": START_SEC, "requested_end_sec": END_SEC, "target_fps": TARGET_FPS,
                   "target_frames": TARGET_FRAMES, "min_frames": MIN_FRAMES,
                   "stop_reason": stop["reason"], "last_decoded_sec": stop["last_decoded_sec"],
                   "real_end_sec": stop["real_end_sec"], "end_shortfall_sec": stop["end_shortfall_sec"]},
        "processing": {"method": "sequential 51.85-53.2 s decode at ~12 fps by CAP_PROP_POS_MSEC; no average-fps seek, "
                                 "no retouch, no generated model, no duplicated or interpolated frames",
                       "crop_px": list(CROP), "crop_size_px": [CROP[2] - CROP[0], CROP[3] - CROP[1]],
                       "output_size_px": list(images[0].size), "resize": "proportional to 384 px wide, LANCZOS"},
        "frames": samples,
        "gif": {"file": GIF_PATH.name, "frame_count": len(paletted), "loop": 0, "duration_total_ms": sum(durations),
                "durations_ms": durations,
                "quantization": "per-frame 256-colour MEDIANCUT with Floyd-Steinberg dithering",
                "reverse_or_tween_frame_added": False},
        "contact_sheet": {"file": CONTACT_PATH.name, "columns": COLUMNS, "rows": ROWS, "frame_indices": picks,
                          "size_px": list(sheet.size), "labels": "source timecode per cell"},
        "reference": {"kind": "raw in-game expression reference", "is_desktop_pet_animation": False,
                      "used_generative_model": False,
                      "note": "原始实机表情参考（v3 窗口 51.85-53.2 秒，1.35 秒 / 16 帧），不是桌宠动画，也不是生成模型输出；"
                              "仅裁切、等比缩放与 GIF 调色板量化。未逐帧确认全部 16 帧画面均为祀。",
                      "timeline_note": "GIF keeps the requested 1.35 s timeline; real_end_sec records any shorter tail.",
                      "v1_status": "v1（49-53 秒）曾实际运行并输出 qa/si-expression-reference-v1.*，"
                                   "但原片在 50.58-51.35 秒切到另一角色，不能作为祀的连续表情参考，已撤回；"
                                   "旧 v1 输出保留为失败证据，本脚本不删除、不覆盖。",
                      "v2_status": "v2（51.7-53.2 秒，1.5 秒 / 18 帧）已实际运行并逐帧检查全部 18 帧："
                                   "第 0 帧（51.700 秒）仍是上一角色，从帧 1（约 51.800 秒）起才是祀，"
                                   "首帧跨镜未消除，v2 已撤回；qa/si-expression-reference-v2.* 保留为失败证据，"
                                   "本脚本不删除、不覆盖。",
                      "verification_required": "v3 需逐帧视觉复核全部 16 帧是否均为祀；"
                                               "本脚本不预先声称该复核已通过，也不声称 v3 可直接用于定稿。"},
        "non_destructive": "source video read-only; writes stay under qa/"}
    JSON_PATH.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"{GIF_PATH.relative_to(ROOT)}: {len(paletted)} frames, {sum(durations)} ms, "
          f"source {samples[0]['timecode']}..{samples[-1]['timecode']}, "
          f"stop={stop['reason']}, real_end={stop['real_end_sec']}")
    return 0

if __name__ == "__main__":
    raise SystemExit(main())

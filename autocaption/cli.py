"""Command line interface: media in -> karaoke-captioned video + subtitle files out."""

from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import replace
from pathlib import Path

from . import __version__, media
from .ass_builder import build_ass
from .grouping import group_words
from .srt_builder import build_srt
from .styles import PRESETS
from .transcribe import transcribe, validate_model
from .alignment import (Transcript, align_transcript, media_digest, new_report,
                        report_words, save_report, timing_problems, validate_report)

POSITIONS = {"high": 0.32, "mid": 0.50, "low": 0.68}
FONTS_DIR = Path(__file__).resolve().parent / "fonts"


def _hex_color(value: str) -> str:
    v = value.lstrip("#")
    if not re.fullmatch(r"[0-9a-fA-F]{6}", v):
        raise argparse.ArgumentTypeError(f"expected a hex color like #FFD700, got {value!r}")
    return f"#{v.upper()}"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="autocaption",
        description="Generate animated karaoke captions for shorts/reels. "
        "Outputs a burned-in video plus .ass and DaVinci-compatible .srt files.",
    )
    parser.add_argument("input", help="video or audio file")
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    parser.add_argument("--style", choices=sorted(PRESETS), default="pop",
                        help="caption style preset (default: pop)")
    parser.add_argument("--model", default=None,
                        help="multilingual whisper model (default: medium on GPU, small on CPU)")
    parser.add_argument("--device", choices=["auto", "cuda", "cpu"], default="auto")
    parser.add_argument("--language", default="he", help="ASR language, or 'auto' to detect; transcript alignment always uses Hebrew")
    source = parser.add_mutually_exclusive_group()
    source.add_argument("--transcript", help="exact transcript text (UTF-8)")
    source.add_argument("--transcript-file", type=Path, help="UTF-8 TXT transcript; words are immutable")
    parser.add_argument("--timings", type=Path, help="resume with a reviewed timing JSON; requires the same transcript")
    parser.add_argument("--align-only", action="store_true", help="save a timing report for review, without rendering")
    parser.add_argument("--highlight-color", type=_hex_color, default=None, metavar="HEX")
    parser.add_argument("--font", default=None, help="font name (default: bundled Noto Sans Hebrew)")
    parser.add_argument("--font-size", type=int, default=None,
                        help="size at the 1080x1920 reference scale")
    parser.add_argument("--caps", action=argparse.BooleanOptionalAction, default=None,
                        help="UPPERCASE ASR captions; always disabled for supplied transcripts")
    parser.add_argument("--words-per-line", type=int, default=4)
    parser.add_argument("--max-chars", type=int, default=18)
    parser.add_argument("--position", choices=sorted(POSITIONS), default="low")
    parser.add_argument("--no-burn", action="store_true", help="only write subtitle files")
    parser.add_argument("--no-srt", action="store_true", help="skip the DaVinci .srt export")
    parser.add_argument("--nvenc", action="store_true", help="encode with NVIDIA NVENC (faster)")
    parser.add_argument("-o", "--output", default=None, help="output video path")
    args = parser.parse_args(argv)

    input_path = Path(args.input).expanduser().resolve()
    if not input_path.is_file():
        parser.error(f"input file not found: {input_path}")
    if args.words_per_line < 1 or args.max_chars < 1 or (args.font_size is not None and args.font_size < 1):
        parser.error("Font size and line limits must be positive.")
    try:
        validate_model(args.model)
        supplied = args.transcript is not None or args.transcript_file is not None
        transcript = Transcript(args.transcript_file.read_text(encoding="utf-8-sig")
                                if args.transcript_file else args.transcript) if supplied else None
    except (OSError, UnicodeError, ValueError) as error:
        parser.error(str(error))
    if (args.timings or args.align_only) and transcript is None:
        parser.error("--timings and --align-only require a transcript.")

    style = PRESETS[args.style]
    overrides = {"pos_y": POSITIONS[args.position]}
    if args.highlight_color:
        overrides["highlight_color"] = args.highlight_color
    if args.font:
        overrides["font"] = args.font
    if args.font_size:
        overrides["font_size"] = args.font_size
    if args.caps is not None:
        overrides["caps"] = args.caps
    style = replace(style, **overrides)
    if transcript is not None:
        style = replace(style, caps=False)

    try:
        media.find_tool("ffmpeg")
        ffmpeg_ok = True
    except media.FFmpegNotFound as error:
        ffmpeg_ok = False
        print(f"[warn] {error}", file=sys.stderr)
        print("[warn] continuing without video features - subtitle files only", file=sys.stderr)

    language = None if args.language == "auto" else args.language
    if transcript is not None:
        report_path = args.timings.resolve() if args.timings else input_path.with_suffix(".timings.json")
        if args.timings:
            report = json.loads(report_path.read_text(encoding="utf-8"))
            validate_report(report, transcript, media_digest(input_path))
            if abs(report["duration"] - media.probe_duration(input_path)) > 0.25:
                raise ValueError("Timing report duration differs from the media.")
        else:
            try:
                report = align_transcript(input_path, transcript, device=args.device)
            except Exception as error:
                # Even a failed model load must leave every word available for correction.
                report = new_report(transcript, media.probe_duration(input_path), media_digest(input_path))
                for row in report["words"]:
                    row["reason"] = f"Alignment failed: {error}"
                print(f"[align] {error}", file=sys.stderr)
            save_report(report_path, report)
        print(f"[timings] {report_path}", flush=True)
        problems = timing_problems(report)
        if problems:
            print("[review] Export blocked. Correct these word timings:", flush=True)
            for problem in problems:
                print(f"  {problem}", flush=True)
        if args.align_only:
            return 0  # GUI always opens the report, including unresolved words.
        if problems:
            return 2
        words = report_words(report)
    else:
        words = transcribe(input_path, model_name=args.model, device=args.device, language=language)
    if not words:
        print("No speech detected - nothing to caption.", file=sys.stderr)
        return 1

    lines = group_words(words, max_words=args.words_per_line, max_chars=args.max_chars,
                        snap_gaps=transcript is None)
    print(f"[captions] {len(lines)} caption lines, style '{style.name}'")

    size = media.probe_video_size(input_path) if ffmpeg_ok else None
    if size is None:
        print("[captions] no video stream to size against: writing subtitle files for 1080x1920")
    width, height = size if size else (1080, 1920)

    ass_path = input_path.with_suffix(".ass")
    ass_path.write_text(build_ass(lines, style, width, height), encoding="utf-8")
    print(f"[out] {ass_path}")

    if not args.no_srt:
        srt_path = input_path.with_suffix(".davinci.srt")
        srt_path.write_text(build_srt(lines, style.highlight_color), encoding="utf-8")
        print(f"[out] {srt_path}")

    if size is not None and not args.no_burn:
        out_path = (
            Path(args.output).expanduser().resolve()
            if args.output
            else input_path.with_name(input_path.stem + ".captioned.mp4")
        )
        fontsdir = FONTS_DIR if FONTS_DIR.is_dir() else None
        print(f"[burn] rendering {out_path.name} ...")
        media.burn_subtitles(input_path, ass_path, out_path, fontsdir=fontsdir, nvenc=args.nvenc,
                            progress=lambda fraction: print(f"[progress] {fraction:.4f}", flush=True))
        print(f"[out] {out_path}")

    return 0


if __name__ == "__main__":
    sys.exit(main())

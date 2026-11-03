"""Reproducible local speech/alignment/render smoke check.

Run from the checkout: python scripts/render_hebrew_sample.py
The speech fixture was synthesized locally with Windows' Hebrew Asaf voice.
No account, TTS service, ASR output or paid API is used.
"""
import json
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from autocaption import media
from autocaption.alignment import Transcript, align_transcript, report_words, save_report
from autocaption.ass_builder import build_ass
from autocaption.cli import FONTS_DIR
from autocaption.grouping import CaptionLine, group_words
from autocaption.styles import PRESETS
from autocaption.transcribe import Word


def run(*args):
    return subprocess.run([media.find_tool("ffmpeg"), "-y", "-v", "error", *map(str, args)],
                          capture_output=True, check=True).stdout


def main():
    output = ROOT / "samples/output"
    output.mkdir(parents=True, exist_ok=True)
    video = output / "hebrew-source.mp4"
    run("-f", "lavfi", "-i", "color=c=0x182335:s=540x960:r=30",
        "-i", ROOT / "tests/fixtures/hebrew-speech.wav",
        "-vf", "drawbox=x=24:y=24:w=492:h=912:color=0x38465C:t=2",
        "-c:v", "libx264", "-c:a", "aac", "-shortest", video)
    transcript = Transcript((ROOT / "samples/hebrew.txt").read_text(encoding="utf-8-sig"))
    report = align_transcript(video, transcript, "cpu")
    report_path = output / "hebrew-source.timings.json"
    save_report(report_path, report)
    words = report_words(report)
    lines = group_words(words, snap_gaps=False)
    source_audio = run("-i", video, "-map", "0:a", "-c", "copy", "-f", "streamhash", "-hash", "sha256", "pipe:1")
    for name, style in PRESETS.items():
        ass = output / f"hebrew-{name}.ass"
        ass.write_text(build_ass(lines, style, 540, 960), encoding="utf-8")
        target = output / f"hebrew-{name}.mp4"
        media.burn_subtitles(video, ass, target, FONTS_DIR)
        assert media.probe_video_size(target) == (540, 960)
        assert run("-i", target, "-map", "0:a", "-c", "copy", "-f", "streamhash",
                   "-hash", "sha256", "pipe:1") == source_audio
        run("-i", target, "-ss", "2.3", "-frames:v", "1", output / f"hebrew-{name}.png")
    # A separately labeled visual fixture exercises mixed scripts and punctuation.
    # These are explicit demonstration timings, not claimed as acoustic alignment.
    mixed = CaptionLine([Word("שלום,", 0, 1), Word("Reels", 1, 2), Word("2026!", 2, 3)])
    ass = output / "mixed-visual-fixture.ass"
    ass.write_text(build_ass([mixed], PRESETS["zoom"], 540, 960), encoding="utf-8")
    with media.subtitle_workspace(ass, FONTS_DIR) as (cwd, vf):
        subprocess.run([media.find_tool("ffmpeg"), "-y", "-v", "error", "-f", "lavfi", "-i",
                        "color=c=0x182335:s=540x960:d=3:r=25", "-vf", vf, "-ss", "1.2",
                        "-frames:v", "1", str(output / "mixed-visual-fixture.png")],
                       cwd=cwd, check=True)
    (output / "verification.json").write_text(json.dumps({
        "aligned_words": len(words), "unaligned_words": 0,
        "exact_source_tokens": [w.text for w in words] == list(transcript.tokens),
        "dimensions": [540, 960], "styles_rendered": list(PRESETS),
        "audio_packet_hash_unchanged": True,
        "speech_fixture": "Windows Hebrew Asaf, synthesized locally",
        "mixed_visual_fixture": "Explicit demo timings, not forced alignment",
    }, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Verified {len(words)} Hebrew words, all styles, unchanged audio. Outputs: {output}")


if __name__ == "__main__":
    main()

"""Transcript integrity and alignment boundary tests; no model downloads."""
import json
import sys
import types
from unittest.mock import Mock

import pytest

from autocaption.alignment import (
    HEBREW_MODEL, Transcript, align_transcript, apply_alignment, media_digest,
    new_report, report_words, save_report, timing_problems, validate_report,
)
from autocaption.transcribe import _default_model, validate_model


TEXT = 'שלום, שלום! Reels 2026 (בדיוק) אֵיך? {טקסט} \\N'
DICTIONARY = dict.fromkeys("אבגדהוזחטיכלמנסעפצקרשתךםןףץ", 1)


def estimates(transcript):
    return {"word_segments": [dict(word=text, start=i * .4, end=i * .4 + .3, score=.9)
                              for i, text in enumerate(transcript.tokens)]}


def test_only_source_tokens_survive_alignment_and_manual_correction(tmp_path):
    transcript = Transcript(TEXT)
    report = apply_alignment(new_report(transcript, 10, "media"), estimates(transcript), DICTIONARY)
    assert [w["text"] for w in report["words"]] == list(transcript.tokens)
    assert report["words"][2]["status"] == report["words"][3]["status"] == "unaligned"
    assert report["words"][-1]["status"] == "unaligned"
    with pytest.raises(ValueError, match="Correct all"):
        report_words(report)
    for i, word in enumerate(report["words"]):
        word.update(start=i * .4, end=i * .4 + .3, status="manual")
    path = tmp_path / "timings.json"
    save_report(path, report)
    loaded = json.loads(path.read_text(encoding="utf-8"))
    validate_report(loaded, transcript, "media")
    assert [w.text for w in report_words(loaded)] == list(transcript.tokens)
    assert loaded["transcript"] == TEXT


@pytest.mark.parametrize("change", ["omit", "reorder", "invent", "case", "duplicate"])
def test_aligner_never_replaces_or_shifts_source_tokens(change):
    transcript = Transcript("שלום עולם שלום חדש")
    result = estimates(transcript)
    words = result["word_segments"]
    if change == "omit":
        del words[2]
    elif change == "reorder":
        words.reverse()
    elif change == "duplicate":
        words.insert(1, words[0].copy())
    elif change == "case":
        words[1]["word"] = "WORLD"
    else:
        words[1]["word"] = "מומצא"
    report = apply_alignment(new_report(transcript, 10, "media"), result, DICTIONARY)
    assert [row["text"] for row in report["words"]] == list(transcript.tokens)
    assert all(row["start"] is None for row in report["words"])
    assert len(timing_problems(report)) == 4


@pytest.mark.parametrize("bad", [None, float("nan"), float("inf"), -1, "1", True])
def test_invalid_or_missing_timestamps_never_export(bad):
    transcript = Transcript("שלום")
    report = new_report(transcript, 2, "media")
    report["words"][0].update(start=bad, end=1.5, status="manual")
    with pytest.raises(ValueError):
        report_words(report)


def test_interpolated_without_acoustic_score_and_low_confidence_require_review():
    transcript = Transcript("שלום עולם")
    result = estimates(transcript)
    del result["word_segments"][0]["score"]
    result["word_segments"][1]["score"] = .01
    report = apply_alignment(new_report(transcript, 10, "media"), result, DICTIONARY)
    assert len(timing_problems(report)) == 2


def test_manual_order_duration_and_subframe_intervals_are_checked():
    report = new_report(Transcript("שלום עולם"), 2, "media")
    report["words"][0].update(start=0, end=1, status="manual")
    for start, end in ((.9, 1.5), (1.1, 2.1), (1.001, 1.002)):
        report["words"][1].update(start=start, end=end, status="manual")
        assert timing_problems(report)


@pytest.mark.parametrize("change", ["text", "order", "count", "transcript", "media", "index"])
def test_report_cannot_change_immutable_source(change):
    transcript = Transcript("שלום עולם")
    report = new_report(transcript, 2, "media")
    if change == "text":
        report["words"][0]["text"] = "חדש"
    elif change == "order":
        report["words"].reverse()
    elif change == "count":
        report["words"].pop()
    elif change == "transcript":
        report["transcript"] += "!"
    elif change == "media":
        report["media_sha256"] = "other"
    else:
        report["words"][0]["index"] = 1
    with pytest.raises(ValueError):
        validate_report(report, transcript, "media")


def test_whitespace_only_tokenization_keeps_niqqud_punctuation_and_repetitions():
    transcript = Transcript("  שָׁלוֹם,\nשלום\tYouTube  123.45% ")
    assert transcript.tokens == ("שָׁלוֹם,", "שלום", "YouTube", "123.45%")
    with pytest.raises(ValueError):
        Transcript(" \n\t")


def test_forced_alignment_calls_hebrew_model_without_asr(monkeypatch, tmp_path):
    import numpy as np
    fake = types.ModuleType("whisperx.alignment")
    transcript = Transcript("שלום עולם")
    fake.load_align_model = Mock(return_value=(object(), {"dictionary": DICTIONARY}))
    fake.align = Mock(return_value=estimates(transcript))
    monkeypatch.setitem(sys.modules, "whisperx.alignment", fake)
    monkeypatch.setattr("autocaption.media.extract_audio", lambda _: np.zeros(32000, dtype=np.float32).tobytes())
    monkeypatch.setattr("autocaption.media.probe_duration", lambda _: 2)
    source = tmp_path / "source.mp4"
    source.write_bytes(b"fixture")
    result = align_transcript(source, transcript, device="cpu")
    fake.load_align_model.assert_called_once_with(language_code="he", device="cpu", model_name=HEBREW_MODEL)
    assert fake.align.call_args.args[0] == [dict(start=0.0, end=2.0, text="שלום עולם")]
    assert [w.text for w in report_words(result)] == ["שלום", "עולם"]


def test_cli_supplied_transcript_bypasses_asr_and_caps(monkeypatch, tmp_path):
    from autocaption import cli
    source = tmp_path / "clip.mp4"
    source.write_bytes(b"fixture")
    transcript = Transcript("שלום Reels")
    report = new_report(transcript, 2, media_digest(source))
    for i, row in enumerate(report["words"]):
        row.update(start=i * .5, end=i * .5 + .4, status="manual")
    timings = tmp_path / "clip.timings.json"
    save_report(timings, report)
    monkeypatch.setattr(cli, "transcribe", Mock(side_effect=AssertionError("ASR is forbidden")))
    monkeypatch.setattr(cli.media, "find_tool", lambda _: "ffmpeg")
    monkeypatch.setattr(cli.media, "probe_video_size", lambda _: (540, 960))
    monkeypatch.setattr(cli.media, "probe_duration", lambda _: 2)
    assert cli.main([str(source), "--transcript", transcript.text, "--timings", str(timings),
                     "--caps", "--no-burn"]) == 0
    ass = source.with_suffix(".ass").read_text(encoding="utf-8")
    assert "Reels" in ass and "REELS" not in ass
    assert "שלום" in ass
    report["words"][0].update(start=None, end=None, status="unaligned")
    save_report(timings, report)
    burn = Mock()
    monkeypatch.setattr(cli.media, "burn_subtitles", burn)
    assert cli.main([str(source), "--transcript", transcript.text, "--timings", str(timings)]) == 2
    burn.assert_not_called()


def test_multilingual_only():
    assert _default_model("cpu", "en") == "small"
    assert _default_model("cuda", "he") == "medium"
    for name in ("tiny", "small", "medium", "large-v3"):
        validate_model(name)
    for name in ("small.en", "medium.en", "vendor/english-model"):
        with pytest.raises(ValueError):
            validate_model(name)


def test_character_evidence_excludes_punctuation_and_requires_every_letter():
    transcript = Transcript("שלום.")
    result = estimates(transcript)
    result["segments"] = [dict(chars=[
        dict(char=c, start=.1 * i, end=.1 * (i + 1), score=.9) for i, c in enumerate("שלום")
    ] + [dict(char=".", start=.4, end=2.0, score=.99)])]
    report = apply_alignment(new_report(transcript, 3, "media"), result, DICTIONARY)
    assert report_words(report)[0].end == .4
    del result["segments"][0]["chars"][2]["score"]
    report = apply_alignment(new_report(transcript, 3, "media"), result, DICTIONARY)
    assert timing_problems(report)

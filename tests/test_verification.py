from copy import deepcopy

import pytest

from autocaption.alignment import Transcript, new_report
from autocaption.verification import apply_verification, comparison_key, verification_problems


def report_for(text="שלום, שלום! Reels 2026"):
    report = new_report(Transcript(text), 10, "same-video")
    for i, row in enumerate(report["words"]):
        row.update(start=i+.2, end=i+.6, status="aligned", score=.9)
    return report


def evidence_for(report):
    return {"media_sha256": report["media_sha256"], "words": [
        dict(text=w["text"], start=w["start"], end=w["end"], probability=.9)
        for w in report["words"]]}


def test_exact_agreement_keeps_every_source_byte_and_time():
    report = report_for("שָׁלוֹם, שלום! Reels 2026")
    original = deepcopy(report)
    evidence = evidence_for(report)
    evidence["words"][0]["text"] = "שלום"
    apply_verification(report, evidence)
    assert verification_problems(report) == []
    assert report["transcript"] == original["transcript"]
    for row, before in zip(report["words"], original["words"]):
        assert {key:row[key] for key in before} == before
    assert comparison_key("שָׁלוֹם,") == "שלום"


@pytest.mark.parametrize("change,reason", [("shift", "timing_disagreement"),
                                           ("replace", "not_heard"), ("quiet", "low_confidence")])
def test_disagreement_is_flagged_without_changing_times(change, reason):
    report = report_for("שלום עולם")
    evidence = evidence_for(report)
    if change == "shift":
        evidence["words"][0].update(start=.6, end=1.0)
    elif change == "replace":
        evidence["words"][0]["text"] = "שונה"
    else:
        evidence["words"][0]["probability"] = .3
    apply_verification(report, evidence)
    assert report["words"][0]["check"]["reason"] == reason
    assert report["words"][0]["start"] == .2
    assert report["words"][0]["text"] == "שלום"
    assert verification_problems(report) == [0]


def test_repeated_words_never_reuse_one_recognized_occurrence():
    report = report_for("שלום שלום")
    report["words"][1].update(start=.7, end=.9)
    evidence = evidence_for(report)
    evidence["words"].pop()
    apply_verification(report, evidence)
    assert verification_problems(report) == [1]


def test_empty_speech_invalid_timing_and_foreign_cache_fail_closed():
    report = report_for()
    apply_verification(report, {"media_sha256":"same-video", "words":[]})
    assert verification_problems(report) == [0, 1, 2, 3]
    report["words"][0].update(start=None,end=None)
    evidence = evidence_for(report)
    apply_verification(report, evidence)
    assert report["words"][0]["check"]["reason"] == "no_timing"
    with pytest.raises(ValueError):
        apply_verification(report, {"media_sha256":"different-video", "words":[]})


def test_rechecking_preserves_a_listened_decision_only_at_its_confirmed_times():
    report = report_for("שלום")
    row = report["words"][0]
    row["check"] = dict(status="listened", confirmed_start=row["start"], confirmed_end=row["end"])
    empty = {"media_sha256":"same-video", "words":[]}
    apply_verification(report, empty)
    assert verification_problems(report) == []
    assert row["check"]["automatic"]["reason"] == "not_heard"
    row["start"] = .3
    apply_verification(report, empty)
    assert verification_problems(report) == [0]


@pytest.mark.parametrize("start_delta,end_delta,matched", [(0,0,True),(.08,.08,True),(.081,0,False),(0,-.081,False),(.15,.15,False)])
def test_both_word_edges_must_agree_within_eighty_ms(start_delta, end_delta, matched):
    report = report_for("שלום")
    # Long enough that overlap is not the limiting criterion in this test.
    report["words"][0]["end"] = 1.2
    evidence = evidence_for(report)
    evidence["words"][0]["start"] += start_delta
    evidence["words"][0]["end"] += end_delta
    apply_verification(report, evidence)
    check = report["words"][0]["check"]
    assert (check["status"] == "matched") == matched
    assert check["start_delta_ms"] == round(start_delta*1000)
    assert check["end_delta_ms"] == round(end_delta*1000)


def test_short_words_need_relative_overlap_and_weak_phonetic_evidence_is_flagged():
    report = report_for("כן")
    report["words"][0].update(start=.2,end=.22)
    evidence = evidence_for(report)
    evidence["words"][0]["end"] = .29  # Within 80 ms, but over four times the duration.
    apply_verification(report, evidence)
    assert report["words"][0]["check"]["reason"] == "timing_disagreement"
    report["words"][0]["score"] = .2
    apply_verification(report, evidence_for(report))
    assert report["words"][0]["check"]["reason"] == "weak_alignment"


def test_loose_old_checks_and_stale_times_cannot_unlock_export():
    report = report_for("שלום עולם")
    apply_verification(report, evidence_for(report))
    assert verification_problems(report) == []
    report["verification"]["version"] = 1
    assert verification_problems(report) == [0,1]
    assert all(row["check"]["reason"] == "recheck_required" for row in report["words"])
    apply_verification(report, evidence_for(report))
    report["words"][1]["start"] += .01
    assert verification_problems(report) == [1]


def test_recognized_evidence_uses_acoustic_edges_without_hiding_unaligned_tokens(tmp_path, monkeypatch):
    from autocaption import verification
    from autocaption.alignment import media_digest
    from autocaption.transcribe import Word
    video=tmp_path/"speech.mp4"
    video.write_bytes(b"fixture")
    raw=[Word("שלום עולם",0,1,.95),Word("רחוק",1.1,1.3,.95)]
    def align(path, source, **kwargs):
        assert source.text == "שלום עולם רחוק"  # Recognizer's words, no supplied transcript.
        report=new_report(source,4,media_digest(path))
        report["words"][0].update(start=.2,end=.6,score=.9,status="aligned")
        report["words"][1].update(start=.65,end=.9,score=.2,status="aligned")
        report["words"][2].update(start=2.5,end=3,score=.9,status="aligned")
        return report
    monkeypatch.setattr(verification,"align_transcript",align)
    evidence=verification.build_evidence(video,raw,"small")
    assert [word["text"] for word in evidence["words"]] == ["שלום","עולם","רחוק"]
    assert evidence["words"][0]["start"] == .2 and evidence["words"][0]["raw_start"] == 0
    assert evidence["words"][1]["start"] is None  # Weak sound evidence retained, never silently used.
    assert evidence["words"][2]["start"] is None  # Cannot jump to another occurrence far away.
    assert raw[0].text == "שלום עולם" and raw[0].start == 0

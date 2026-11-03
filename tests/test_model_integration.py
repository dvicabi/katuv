from pathlib import Path

import pytest

from autocaption.alignment import Transcript, align_transcript, report_words
from autocaption.models import IVRIT_TURBO, IVRIT_LARGE


def test_real_hebrew_forced_alignment(request):
    if not request.config.getoption("--run-model"):
        pytest.skip("Use --run-model for the real Hebrew model test")
    root = Path(__file__).resolve().parents[1]
    transcript = Transcript((root / "samples/hebrew.txt").read_text(encoding="utf-8-sig"))
    report = align_transcript(root / "tests/fixtures/hebrew-speech.wav", transcript, "cpu")
    words = report_words(report)
    assert [word.text for word in words] == list(transcript.tokens)
    assert len(words) == 13
    assert 0 <= words[0].start < .6
    assert 7 <= words[-1].end <= 8.5
    # Punctuation must not absorb the silence between the first two sentences.
    assert words[1].end < 1.4 < words[2].start


def test_real_independent_hebrew_recognition_rejects_an_unspoken_word(request, tmp_path):
    if not request.config.getoption("--run-model"):
        pytest.skip("Use --run-model for the real multilingual verification test")
    import json
    from autocaption.verification import speech_evidence, apply_verification, verification_problems
    root = Path(__file__).resolve().parents[1]
    video = root / "autocaption/assets/hebrew-source.mp4"
    report = json.loads(video.with_suffix(".timings.json").read_text(encoding="utf-8"))
    evidence = speech_evidence(video, tmp_path/"evidence.json")
    original = [(w["text"],w["start"],w["end"]) for w in report["words"]]
    apply_verification(report,evidence)
    assert len(verification_problems(report)) <= 5
    assert [(w["text"],w["start"],w["end"]) for w in report["words"]] == original
    report["words"][4]["text"] = "אבטיחים"
    apply_verification(report,evidence)
    assert report["words"][4]["check"]["reason"] == "not_heard"
    assert report["words"][4]["text"] == "אבטיחים"


@pytest.mark.parametrize("model_name", [IVRIT_TURBO, IVRIT_LARGE])
def test_real_ivrit_models_preserve_exact_words_and_work_offline(request, tmp_path, monkeypatch, model_name):
    if not request.config.getoption("--run-hebrew-models"):
        pytest.skip("Use --run-hebrew-models to test the Hebrew fine-tunes")
    import json
    import huggingface_hub
    from autocaption.models import cached_model_path, prepare_model
    from autocaption.verification import speech_evidence, apply_verification
    prepare_model(model_name)
    assert cached_model_path(model_name)
    # Real inference must be possible after installation without any Hub access.
    monkeypatch.setattr(huggingface_hub, "snapshot_download", lambda *a, **kw: pytest.fail("Unexpected download"))
    root = Path(__file__).resolve().parents[1]
    video = root / "autocaption/assets/hebrew-source.mp4"
    evidence = speech_evidence(video, tmp_path/"evidence.json", model_name)
    assert len(evidence["words"]) >= 11
    assert any("שלום" in word["text"] for word in evidence["words"])
    assert all(0 <= word["start"] < word["end"] <= 8.88 for word in evidence["words"])
    report = json.loads(video.with_suffix(".timings.json").read_text(encoding="utf-8"))
    original = [(word["text"], word["start"], word["end"]) for word in report["words"]]
    apply_verification(report, evidence)
    assert report["verification"]["model"] == model_name
    assert [(word["text"], word["start"], word["end"]) for word in report["words"]] == original
    assert sum(word["check"]["status"] == "matched" for word in report["words"]) >= 7
    report["words"][4]["text"] = "אבטיחים"
    apply_verification(report, evidence)
    assert report["words"][4]["check"]["reason"] == "not_heard"

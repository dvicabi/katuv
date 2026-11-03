"""Model routing, offline loading and evidence isolation, without downloads."""
from copy import deepcopy
from unittest.mock import Mock

import pytest

from autocaption import models, transcribe, verification
from autocaption.alignment import Transcript, new_report, media_digest


def test_catalog_is_curated_and_rejects_english_and_arbitrary_models(monkeypatch):
    monkeypatch.setattr(models, "cached_model_path", lambda name: None)
    catalog = models.model_catalog()
    assert len(catalog) == 7
    assert {item["id"] for item in catalog if item["group"] == "hebrew"} == {models.IVRIT_TURBO, models.IVRIT_LARGE}
    for item in catalog:
        models.validate_model(item["id"])
        assert models.model_repo(item["id"]) == item["repo"]
        assert not item["cached"]
    for name in ("small.en", "distil-large-v3", "vendor/whisper", "../../model"):
        with pytest.raises(ValueError):
            transcribe.transcribe("unused.mp4", model_name=name)


def test_cuda_fallback_keeps_the_selected_hebrew_model(monkeypatch):
    monkeypatch.setattr(transcribe, "_cuda_usable", lambda: True)
    run = Mock(side_effect=[RuntimeError("CUDA unavailable"), [transcribe.Word("שלום", .2, .6)]])
    monkeypatch.setattr(transcribe, "_run", run)
    words = transcribe.transcribe("video.mp4", model_name=models.IVRIT_TURBO, verbose=False)
    assert words[0].text == "שלום"
    assert [call.args[1:4] for call in run.call_args_list] == [
        (models.IVRIT_TURBO, "cuda", "float16"), (models.IVRIT_TURBO, "cpu", "int8")]


def test_complete_cache_is_loaded_offline_but_partial_cache_is_not_ready(monkeypatch, tmp_path):
    import huggingface_hub

    for name in ("model.bin", "config.json", "tokenizer.json", "vocabulary.json"):
        (tmp_path / name).write_bytes(b"fixture")
    monkeypatch.setattr(huggingface_hub, "try_to_load_from_cache", lambda repo, name: str(tmp_path/name))
    download = Mock(side_effect=AssertionError("No network for a cached model"))
    monkeypatch.setattr(huggingface_hub, "snapshot_download", download)
    assert models.prepare_model(models.IVRIT_TURBO) == str(tmp_path)
    assert not download.called
    (tmp_path/"config.json").unlink()
    assert models.cached_model_path(models.IVRIT_TURBO) is None


def test_first_download_uses_windows_safe_single_worker(monkeypatch, tmp_path):
    import huggingface_hub

    monkeypatch.setattr(models, "cached_model_path", lambda name: None)
    download = Mock(return_value=str(tmp_path))
    monkeypatch.setattr(huggingface_hub, "snapshot_download", download)
    assert models.prepare_model(models.IVRIT_LARGE) == str(tmp_path)
    assert download.call_args.args == (models.IVRIT_LARGE,)
    assert download.call_args.kwargs["max_workers"] == 1
    assert "model.bin" in download.call_args.kwargs["allow_patterns"]


def test_evidence_is_cached_per_video_and_model_and_never_replaces_words(monkeypatch, tmp_path):
    video = tmp_path/"video.mp4"
    video.write_bytes(b"video A")
    recognize = Mock(return_value=[transcribe.Word("שונה", .2, .6, .99)])
    monkeypatch.setattr(verification, "transcribe", recognize)
    def align(path, source, **kwargs):
        report = new_report(source, 3, media_digest(path))
        for row in report["words"]:
            row.update(start=.2,end=.6,score=.9,status="aligned")
        return report
    monkeypatch.setattr(verification, "align_transcript", align)
    for name in ("small", models.IVRIT_TURBO, "small", models.IVRIT_TURBO):
        evidence = verification.speech_evidence(video, tmp_path/models.evidence_cache_name(name), name)
        assert evidence["model"] == name
    assert recognize.call_count == 2
    assert [call.kwargs["model_name"] for call in recognize.call_args_list] == ["small", models.IVRIT_TURBO]
    assert all(call.kwargs["language"] == "he" for call in recognize.call_args_list)
    source = "שָׁלוֹם, Reels 2026!"
    report = new_report(Transcript(source), 3, evidence["media_sha256"])
    report["words"][0].update(start=.2, end=.6, status="manual")
    original = deepcopy(report)
    verification.apply_verification(report, evidence)
    assert report["verification"]["model"] == models.IVRIT_TURBO
    assert report["transcript"] == source
    assert report["words"][0]["check"]["reason"] == "not_heard"
    for row, before in zip(report["words"], original["words"]):
        assert {key: row[key] for key in before} == before
    # Even an explicitly reused cache path must not supply another model's evidence.
    verification.speech_evidence(video, tmp_path/models.evidence_cache_name("small"), models.IVRIT_TURBO)
    assert recognize.call_count == 3
    video.write_bytes(b"video B")
    verification.speech_evidence(video, tmp_path/models.evidence_cache_name(models.IVRIT_TURBO), models.IVRIT_TURBO)
    assert recognize.call_count == 4

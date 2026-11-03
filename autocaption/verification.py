"""Independent local speech evidence. Never supplies or modifies caption words.

Agreement is an estimate, not proof of what a person said. Strict matching leaves
ambiguous spelling, numbers, missing speech and timing disagreement for listening.
"""
from __future__ import annotations

from bisect import bisect_left, bisect_right
from dataclasses import asdict
from pathlib import Path
import json
import time
import unicodedata

from .alignment import Transcript, align_transcript, media_digest, valid_interval, new_report
from .transcribe import transcribe
from .models import DEFAULT_MODEL, validate_model

MODEL = DEFAULT_MODEL  # No prompt/transcript is passed to the recognizer.
VERSION = 3
CHECK_VERSION = 2
TOLERANCE = .08  # Compare BOTH word boundaries; not an assertion of ground-truth accuracy.


def build_evidence(video: Path, words: list, model_name: str, aligned: dict | None = None, progress=None, device="auto") -> dict:
    """Locate independently recognized words acoustically, not by ASR's coarse segment edges.

    No user transcript enters this path. Both timelines use the same Hebrew
    acoustic aligner, so agreement is supporting evidence, never independent proof.
    Auto-transcript mode can reuse its fresh alignment of this exact recognized text.
    """
    raw = [dict(asdict(word), text=token) for word in words for token in word.text.split()]
    fingerprint = media_digest(video)
    result = dict(version=VERSION, model=model_name, media_sha256=fingerprint,
                  timing_method="hebrew_forced_alignment", words=[])
    if not raw:
        return result
    source = Transcript(" ".join(word["text"] for word in raw))
    if aligned is None:
        if progress:
            progress("ממקמים את המילים שזוהו לפי צלילי הדיבור, ואז משווים לתמליל שלכם")
        try:
            aligned = align_transcript(video, source, device=device, guidance=raw, progress=progress)
        except Exception as error:
            # A failed refinement must not discard useful local recognition.
            # Raw boundaries remain explicitly coarse, never "matched" evidence.
            aligned = new_report(source, max(word["end"] for word in raw), fingerprint)
            result["alignment_error"] = str(error)
    from .alignment import validate_report
    validate_report(aligned, source, fingerprint)
    for original, row in zip(raw, aligned["words"]):
        item = dict(original, raw_start=original["start"], raw_end=original["end"],
                    start=None, end=None, alignment_score=row.get("score"))
        score = row.get("score")
        if (row.get("status") == "aligned" and isinstance(score, (int,float)) and .35 <= score <= 1
                and valid_interval(row.get("start"), row.get("end"), aligned["duration"])
                and abs(row["start"]-original["start"]) <= .75 and abs(row["end"]-original["end"]) <= .75):
            item.update(start=row["start"], end=row["end"])
        result["words"].append(item)
    return result


def comparison_key(text: str) -> str:
    # This key is used only for comparison. The immutable source is never touched.
    return "".join(c for c in unicodedata.normalize("NFD", text).casefold() if c.isalnum())


def speech_evidence(video: Path, cache: Path | None = None, model_name: str = MODEL, progress=None, device="auto") -> dict:
    validate_model(model_name)
    fingerprint = media_digest(video)
    if cache and cache.exists():
        cached = json.loads(cache.read_text(encoding="utf-8"))
        if (cached.get("media_sha256") == fingerprint and cached.get("version") == VERSION
                and cached.get("model") == model_name and cached.get("timing_method") == "hebrew_forced_alignment"):
            return cached
    # VAD trained on speech can discard sung phrases over music. Recognition is
    # timing evidence only; lexical matching still protects the source words.
    words = transcribe(video, model_name=model_name, language="he", verbose=False, progress=progress, device=device,
                       vad_filter=False)
    result = build_evidence(video, words, model_name, progress=progress, device=device)
    if cache:
        staged = cache.with_suffix(".tmp")
        staged.write_text(json.dumps(result, ensure_ascii=False, allow_nan=False), encoding="utf-8")
        staged.replace(cache)
    return result


def apply_verification(report: dict, evidence: dict) -> dict:
    if evidence.get("media_sha256") != report["media_sha256"]:
        raise ValueError("Speech evidence belongs to a different video")
    listened = {row["index"]: dict(row["check"]) for row in report["words"]
                if row.get("check", {}).get("status") == "listened"
                and row["check"].get("confirmed_start") == row.get("start")
                and row["check"].get("confirmed_end") == row.get("end")
                and valid_interval(row.get("start"), row.get("end"), report["duration"])}
    by_word = {}
    for i, item in enumerate(evidence["words"]):
        if not valid_interval(item.get("start"), item.get("end"), report["duration"]):
            continue
        key = comparison_key(item["text"])
        if key:
            by_word.setdefault(key, []).append((item["start"], i, item))
    for candidates in by_word.values():
        candidates.sort()
    used = set()
    last_position = -1
    for row in report["words"]:
        check = dict(status="uncertain", reason="not_heard", suggested=None)
        row["check"] = check
        start, end = row.get("start"), row.get("end")
        if not valid_interval(start, end, report["duration"]):
            check["reason"] = "no_timing"
            continue
        candidates = by_word.get(comparison_key(row["text"]), [])
        # Bounded time search avoids matching another occurrence several seconds away.
        lo = bisect_left(candidates, (start - 1.0, -1))
        hi = bisect_right(candidates, (start + 1.0, float("inf")))
        nearby = [candidate for candidate in candidates[lo:hi]
                  if candidate[1] not in used and candidate[1] > last_position]
        if not nearby:
            continue
        _, position, candidate = min(nearby, key=lambda item:
                                    abs(item[2]["start"] - start) + abs(item[2]["end"] - end))
        used.add(position)
        # Consume each occurrence once, even when it needs review.
        last_position = position
        confidence = candidate.get("probability")
        check["confidence"] = confidence
        check["suggested"] = dict(start=candidate["start"], end=candidate["end"])
        check["start_delta_ms"] = round((candidate["start"] - start) * 1000)
        check["end_delta_ms"] = round((candidate["end"] - end) * 1000)
        if not isinstance(confidence, (int, float)) or isinstance(confidence, bool) or not .70 <= confidence <= 1:
            check["reason"] = "low_confidence"
            continue
        score = row.get("score")
        if row.get("status") == "aligned" and score is not None and not .35 <= score <= 1:
            check["reason"] = "weak_alignment"
            continue
        overlap = max(0, min(end, candidate["end"]) - max(start, candidate["start"]))
        union = max(end, candidate["end"]) - min(start, candidate["start"])
        check["overlap_ratio"] = round(overlap / union, 3)
        if (abs(start - candidate["start"]) > TOLERANCE + 1e-9
                or abs(end - candidate["end"]) > TOLERANCE + 1e-9
                or overlap / union < .65):
            check["reason"] = "timing_disagreement"
            continue
        check.update(status="matched", reason="agreement", confirmed_start=start, confirmed_end=end)
    report["verification"] = dict(version=CHECK_VERSION, model=evidence.get("model", MODEL), checked_at=time.time(),
                                   tolerance_seconds=TOLERANCE)
    for row in report["words"]:
        if row["index"] in listened:
            check = listened[row["index"]]
            check["automatic"] = row["check"]
            row["check"] = check
    return report


def apply_refinement(report: dict, fresh: dict, evidence: dict) -> dict:
    from .alignment import validate_report
    from .automatic_timing import complete_timings
    validate_report(fresh, Transcript(report['transcript']), report['media_sha256'])
    for i, row in enumerate(report['words']):
        vocal_anchor = (row.get('status') == 'aligned' and row.get('timing_method') == 'vocal_consensus'
                        and row.get('acoustic_retry', {}).get('supporting_crops', 0) >= 2)
        if (row.get('status') == 'manual' or vocal_anchor) and row.get('gap_fill'):
            row['end'] = row.pop('gap_fill')['end']
        if row.get('status') != 'manual' and not vocal_anchor:
            report['words'][i] = dict(fresh['words'][i])
    report['alignment'] = fresh.get('alignment', {})
    complete_timings(report, evidence)
    return apply_verification(report, evidence)


def verify_report(video: Path, report: dict, cache: Path | None = None, model_name: str = MODEL, progress=None, device="auto", auto_timing=False, resolve_manual_conflicts=False, include_noncommercial=False) -> dict:
    evidence = speech_evidence(video, cache, model_name, progress, device)
    if auto_timing and cache and report.get('full_audit'):
        from .singing_alignment import recover_singing
        return recover_singing(video,report,evidence,cache.parent/'singing-recovery',model_name,
                               progress=progress,resolve_manual_conflicts=resolve_manual_conflicts,
                               include_noncommercial=include_noncommercial)
    if auto_timing:
        # A recheck must actually re-align automatic timings, not merely compare
        # against the old result. Explicit manual edits are immutable anchors.
        fresh = align_transcript(video, Transcript(report['transcript']), device=device,
                                 guidance=evidence['words'], progress=progress)
        apply_refinement(report, fresh, evidence)
        from .singing_timing import repair_estimates
        repair_estimates(video, report, evidence, device=device, progress=progress,
                         cache_dir=cache.parent / 'vocal-analysis' if cache else None,
                         resolve_manual_conflicts=resolve_manual_conflicts)
        apply_verification(report, evidence)
        if cache and report['duration']>=30 and len(verification_problems(report))>len(report['words'])*.25:
            from .singing_alignment import recover_singing
            return recover_singing(video,report,evidence,cache.parent/'singing-recovery',model_name,
                                   progress=progress,resolve_manual_conflicts=resolve_manual_conflicts,
                                   include_noncommercial=include_noncommercial)
        return report
    return apply_verification(report, evidence)


def refresh_checks(report: dict) -> dict:
    """Old, looser checks cannot silently count as the new precision policy."""
    from .timing_audit import refresh_audit
    refresh_audit(report)
    for row in report["words"]:
        check = row.get("check", {})
        status = check.get("status")
        if status not in ("matched", "listened"):
            continue
        current = (check.get("confirmed_start") == row.get("start") and
                   check.get("confirmed_end") == row.get("end") and
                   valid_interval(row.get("start"), row.get("end"), report["duration"]))
        if status == "matched":
            current = current and report.get("verification", {}).get("version") == CHECK_VERSION
        if not current:
            row["check"] = dict(status="uncertain", reason="recheck_required", suggested=None)
    return report


def verification_problems(report: dict) -> list[int]:
    refresh_checks(report)
    return [row["index"] for row in report["words"]
            if row.get("check", {}).get("status") not in ("matched", "listened")]

"""Best-effort timing completion. Source words never come from the recognizer.

Acoustic anchors win; recognized words can supply missing anchors in source order.
Remaining words receive explicit estimates, not fabricated verification results.
"""
from __future__ import annotations

from bisect import bisect_left, insort
import math
import unicodedata

from .alignment import valid_interval, display_rows
from .alignment_windows import matching_pairs


def _key(text):
    return "".join(c for c in unicodedata.normalize("NFD", text).casefold() if c.isalnum())


def recover_gap_provenance(report, baseline):
    """Migrate only provable old automatic extensions; never infer from status alone."""
    from .alignment import Transcript, validate_report
    validate_report(baseline, Transcript(report['transcript']), report['media_sha256'])
    ordered = display_rows(baseline)
    next_start = {left['index']: right['start'] for left, right in zip(ordered, ordered[1:])}
    for row, before in zip(report['words'], baseline['words']):
        if (row.get('status') == 'manual' and not row.get('manual_timing')
                and row.get('check', {}).get('status') != 'listened'
                and row['start'] == before['start']
                and row['end'] == next_start.get(row['index']) and row['end'] > before['end']):
            row['status'] = before['status']
            row['gap_fill'] = dict(end=before['end'])
    return report


def complete_timings(report: dict, evidence: dict | None = None) -> dict:
    rows, duration = display_rows(report), report["duration"]
    count, limit = len(rows), math.floor(duration * 100 + 1e-7)
    if count > limit:
        raise ValueError("הטקסט ארוך מדי למשך הסרטון. יש לבחור את הסרטון המתאים לטקסט.")
    candidates = {}
    for i, row in enumerate(rows):
        if valid_interval(row.get("start"), row.get("end"), duration):
            candidates[i] = (row["start"], row["end"], None)

    if evidence:
        if evidence.get("media_sha256") != report["media_sha256"]:
            raise ValueError("Speech evidence belongs to a different video")
        heard = evidence["words"]
        # Sequence matching consumes repeated occurrences once, monotonically.
        matches = matching_pairs([_key(w["text"]) for w in rows], [_key(w["text"]) for w in heard])
        for i, position in matches:
            item = heard[position]
            row = rows[i]
            if not _key(row["text"]) or row.get("status") in ("manual", "aligned") and i in candidates:
                continue
            probability = item.get("probability") or 0
            if probability < .45:
                continue
            start, end = item.get("start"), item.get("end")
            method = "acoustic_evidence"
            if not valid_interval(start, end, duration):
                start, end = item.get("raw_start"), item.get("raw_end")
                method = "recognizer_estimate"
            if valid_interval(start, end, duration):
                candidates[i] = (start, end, method)

    # Manual edits and existing acoustic anchors take priority over estimates.
    # Reserve centiseconds only for actual missing words, not at every adjacent
    # boundary: e.g. .603 -> .603 needs no extra frame or rounding gap.
    def room(begin, finish, missing):
        return (finish >= begin if not missing else
                math.floor(finish*100+1e-7) - math.ceil(begin*100-1e-7) >= missing)

    manual = [i for i, row in enumerate(rows) if row.get("status") == "manual" and i in candidates]
    anchors = {-1: (0., 0., None), count: (limit/100, limit/100, None)}
    for left, right in zip([-1] + manual, manual + [count]):
        begin = candidates[left][1] if left >= 0 else 0.
        finish = candidates[right][0] if right < count else limit/100
        if not room(begin, finish, right-left-1):
            raise ValueError("התזמון הידני חופף למילים אחרות. אפשר להזיז את גבולות המילה.")
        if right < count:
            anchors[right] = candidates[right]
    ordered = sorted(anchors)
    for acoustic in (True, False):
        for i, candidate in candidates.items():
            if i in anchors or (rows[i].get("status") == "aligned") != acoustic:
                continue
            position = bisect_left(ordered, i)
            left, right = ordered[position-1], ordered[position]
            start, stop, _ = candidate
            if room(anchors[left][1], start, i-left-1) and room(stop, anchors[right][0], right-i-1):
                anchors[i] = candidate
                insort(ordered, i)

    resolved = dict(anchors)
    for left, right in zip(ordered, ordered[1:]):
        missing = list(range(left+1, right))
        if not missing:
            continue
        start = math.ceil(anchors[left][1]*100-1e-7)
        end = math.floor(anchors[right][0]*100+1e-7)
        weights = [max(1, len(_key(rows[i]["text"]))) for i in missing]
        # Do not stretch leading/trailing words across a long silent tail.
        natural = max(len(missing), round(sum(weights)*6.5))
        if left == -1 and right != count:
            start = max(start, end-natural)
        elif right == count and left != -1:
            end = min(end, start+natural)
        spare, total, used = end-start-len(missing), sum(weights), 0
        cursor = start
        for j, (i, weight) in enumerate(zip(missing, weights)):
            used += weight
            stop = start + j + 1 + math.floor(spare*used/total)
            resolved[i] = (cursor/100, stop/100, "neighbor_estimate")
            cursor = stop

    changed = False
    for i, row in enumerate(rows):
        start, end, method = resolved[i]
        if method is None and row.get("status") in ("aligned", "manual", "estimated"):
            continue
        method = method or "alignment_estimate"
        if (row.get("start"), row.get("end"), row.get("timing_method")) == (start, end, method):
            continue
        row.setdefault("original_timing", {key: row.get(key) for key in ("start", "end", "status", "score", "reason")})
        row.update(start=start, end=end, status="estimated", timing_method=method)
        row["check"] = dict(status="uncertain", reason="automatic_estimate", suggested=None)
        changed = True
    if changed:
        report["automatic_timing"] = dict(version=1, estimated=sum(w["status"] == "estimated" for w in rows))
    return report

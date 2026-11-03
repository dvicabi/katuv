"""Transcript-owned captions. WhisperX supplies timings, never display text."""
from __future__ import annotations

import hashlib
import json
import math
import re
from dataclasses import dataclass
from pathlib import Path

from .transcribe import Word
from .styles import quantize

HEBREW_MODEL = "imvladikon/wav2vec2-xls-r-300m-hebrew"


@dataclass(frozen=True)
class Transcript:
    text: str

    def __post_init__(self):
        if not self.tokens:
            raise ValueError("The transcript is empty. Paste text or choose a UTF-8 TXT file.")

    @property
    def tokens(self) -> tuple[str, ...]:
        # Only whitespace separates tokens. Punctuation, case, niqqud, numbers,
        # duplicate words and spelling remain byte-for-byte as supplied.
        return tuple(re.findall(r"\S+", self.text))

    @property
    def digest(self) -> str:
        return hashlib.sha256(self.text.encode("utf-8")).hexdigest()


def media_digest(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def new_report(transcript: Transcript, duration: float, fingerprint: str) -> dict:
    return {
        "version": 1, "transcript": transcript.text,
        "transcript_sha256": transcript.digest, "media_sha256": fingerprint,
        "duration": duration, "model": HEBREW_MODEL,
        "words": [dict(index=i, text=text, start=None, end=None, score=None,
                       status="unaligned", reason="No alignment returned")
                  for i, text in enumerate(transcript.tokens)],
    }


def apply_alignment(report: dict, result: dict, dictionary: dict) -> dict:
    """Fail closed if the aligner's token sequence differs, including duplicates.

    Positional correspondence is accepted only after checking the ENTIRE sequence.
    Never zip a shortened output onto the source or use fuzzy matching.
    """
    rows = report["words"]
    aligned = result.get("word_segments", [])
    if [w.get("word") for w in aligned] != [w["text"] for w in rows]:
        for row in rows:
            row["reason"] = "Aligner returned a different token sequence; set timings manually"
        return report
    # Current WhisperX treats unknown punctuation as acoustic wildcards. Use
    # actual supported letter spans so a full stop cannot absorb a long pause.
    # Character timings precede WhisperX's interpolation step.
    chars = [c for segment in result.get("segments", []) for c in segment.get("chars", []) or []]
    if chars:
        normalized = " ".join(row["text"] for row in rows)
        if "".join(c["char"] for c in chars) != normalized:
            for row in rows:
                row["reason"] = "Character alignment differs from the source; set timings manually"
            return report
        offset = 0
        aligned = []
        for row in rows:
            span = chars[offset:offset + len(row["text"])]
            letters = [c for c in span if c["char"].isalnum() and c["char"].lower() in dictionary]
            usable = [c for c in letters if all(isinstance(c.get(k), (int, float))
                      and math.isfinite(c[k]) for k in ("start", "end", "score"))]
            estimate = {}
            if letters and len(usable) == len(letters):
                estimate = dict(start=min(c["start"] for c in usable),
                                end=max(c["end"] for c in usable),
                                score=sum(c["score"] for c in usable) / len(usable))
            aligned.append(estimate)
            offset += len(row["text"]) + 1
    previous_end = 0.0
    for row, estimate in zip(rows, aligned):
        text = row["text"]
        start, end, score = estimate.get("start"), estimate.get("end"), estimate.get("score")
        supported = [c for c in text if c.isalnum() and c.lower() in dictionary]
        unsupported = [c for c in text if c.isalnum() and c.lower() not in dictionary]
        if not supported or unsupported:
            row["reason"] = "Contains letters/numbers outside the Hebrew model vocabulary; set timings manually"
        elif not valid_interval(start, end, report["duration"]) or start < previous_end:
            row["reason"] = "Missing, overlapping or invalid alignment timing"
        elif not isinstance(score, (int, float)) or not math.isfinite(score) or score < 0.15:
            row["reason"] = "Low alignment confidence; review and set timings manually"
        else:
            row.update(start=start, end=end, score=score, status="aligned", reason="")
            previous_end = end
    return report


def valid_interval(start, end, duration: float) -> bool:
    return (all(isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v)
                for v in (start, end, duration))
            and 0 <= start < end <= duration + 0.001
            and quantize(end * 100) > quantize(start * 100))


def validate_report(report: dict, transcript: Transcript, fingerprint: str) -> None:
    if (report.get("version") != 1 or report.get("transcript") != transcript.text
            or report.get("transcript_sha256") != transcript.digest
            or report.get("media_sha256") != fingerprint):
        raise ValueError("Timing report does not match this exact transcript and media. Align again.")
    rows = report.get("words", [])
    if ([row.get("text") for row in rows] != list(transcript.tokens)
            or [row.get("index") for row in rows] != list(range(len(transcript.tokens)))):
        raise ValueError("Timing report words were changed, omitted or reordered. Align again.")


def display_rows(report: dict) -> list[dict]:
    """Source rows stay immutable; only an explicit manual swap opts into time order."""
    rows = report["words"]
    if report.get("allow_reordered_timings") is True:
        return sorted(rows, key=lambda row: (row["start"] if isinstance(row.get("start"), (int, float))
                                             and math.isfinite(row["start"]) else math.inf, row["index"]))
    return rows


def timing_problems(report: dict) -> list[str]:
    problems = []
    previous_end = 0.0
    for row in display_rows(report):
        start, end = row.get("start"), row.get("end")
        if row.get("status") not in ("aligned", "manual", "estimated") or not valid_interval(start, end, report["duration"]):
            problems.append(f"#{row['index'] + 1} {row['text']}: {row.get('reason') or 'invalid timing'}")
        elif start < previous_end:
            problems.append(f"#{row['index'] + 1} {row['text']}: timing overlaps the previous word")
        else:
            previous_end = end
    return problems


def report_words(report: dict) -> list[Word]:
    problems = timing_problems(report)
    if problems:
        raise ValueError("Correct all word timings before export:\n" + "\n".join(problems))
    return [Word(row["text"], row["start"], row["end"]) for row in display_rows(report)]


def save_report(path: Path, report: dict) -> None:
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False), encoding="utf-8")


def align_transcript(media_path: Path, transcript: Transcript, device: str = "auto", guidance=None, progress=None) -> dict:
    # Import just the alignment module: no ASR, diarization, API, or transcript generation.
    import numpy as np
    import torch
    from whisperx.alignment import align, load_align_model
    from .media import extract_audio, probe_duration

    device = ("cuda" if torch.cuda.is_available() else "cpu") if device == "auto" else device
    audio = np.frombuffer(extract_audio(media_path), dtype=np.float32).copy()
    duration = len(audio) / 16000
    if duration <= 0:
        raise ValueError("The input has no decodable audio.")
    report = new_report(transcript, probe_duration(media_path), media_digest(media_path))
    print(f"[align] Hebrew forced alignment on {device}; first use downloads {HEBREW_MODEL}", flush=True)
    model, metadata = load_align_model(language_code="he", device=device, model_name=HEBREW_MODEL)
    if guidance is not None:
        from .alignment_windows import alignment_windows
        breaks, offset = [], 0
        for line in transcript.text.splitlines():
            offset += len(line.split())
            breaks.append(offset)
        windows = alignment_windows(transcript.tokens, guidance, duration, breaks)
        for number, window in enumerate(windows):
            if progress:
                progress(f"מדייקים לפי הקול: קטע {number + 1} מתוך {len(windows)}")
            first, stop = window['first'], window['stop']
            source = Transcript(' '.join(transcript.tokens[first:stop]))
            # Each independent call validates its complete token and character
            # sequence. A failed window cannot shift or discard later words.
            result = align([dict(start=window['start'], end=window['end'], text=source.text)],
                           model, metadata, audio, device,
                           interpolate_method="nearest", return_char_alignments=True)
            partial = apply_alignment(new_report(source, report['duration'], report['media_sha256']),
                                      result, metadata['dictionary'])
            for offset, row in enumerate(partial['words']):
                row['index'] = first + offset
                if row['status'] == 'aligned':
                    row['timing_method'] = 'forced_alignment_window'
                report['words'][first + offset] = row
        # Resolve large disagreements locally, especially sustained sung syllables.
        # This is still forced alignment of the exact source token, never ASR text.
        from .alignment_windows import matching_pairs, key
        pairs = matching_pairs([key(t) for t in transcript.tokens], [key(w['text']) for w in guidance])
        refinements = 0
        for index, position in pairs:
            row, heard = report['words'][index], guidance[position]
            start, end = heard.get('raw_start', heard.get('start')), heard.get('raw_end', heard.get('end'))
            if ((heard.get('probability') or 0) < .8 or not valid_interval(start, end, duration)
                    or end-start > 15):
                continue
            if (row['status'] == 'aligned' and abs(row['start']-start) <= .8 and abs(row['end']-end) <= .8):
                continue
            source = Transcript(row['text'])
            if progress:
                progress(f"מדייקים מילה מול הקול: {index+1} מתוך {len(transcript.tokens)}")
            result = align([dict(start=max(0., start-.5), end=min(duration, end+.5), text=source.text)],
                           model, metadata, audio, device, interpolate_method='nearest', return_char_alignments=True)
            candidate = apply_alignment(new_report(source, report['duration'], report['media_sha256']),
                                        result, metadata['dictionary'])['words'][0]
            if candidate['status'] == 'aligned':
                previous = next((w for w in reversed(report['words'][:index]) if w['status'] == 'aligned'), None)
                following = next((w for w in report['words'][index+1:] if w['status'] == 'aligned'), None)
                # A one-word retry has less context than the phrase. It cannot
                # jump across another word and force the completer to squeeze
                # several words into a few hundredths of a second.
                if ((previous and candidate['start'] <= previous['start'])
                        or (following and candidate['start'] >= following['start'])):
                    continue
                candidate.update(index=index, timing_method='forced_alignment_local')
                report['words'][index] = candidate
                refinements += 1
        for left, right in zip(report['words'], report['words'][1:]):
            if (left['status'] == right['status'] == 'aligned' and left['end'] > right['start']
                    and valid_interval(left['start'], right['start'], duration)):
                left['acoustic_end'] = left['end']
                left['end'] = right['start']
        report['alignment'] = dict(version=2, method='anchored_windows', windows=len(windows), refinements=refinements)
        return report
    # Whitespace is normalized only in the alignment request, not in the source.
    result = align([dict(start=0.0, end=duration, text=" ".join(transcript.tokens))],
                   model, metadata, audio, device,
                   interpolate_method="nearest", return_char_alignments=True)
    # WhisperX may interpolate missing timestamps; apply_alignment accepts only
    # words with a real finite acoustic score and supported letters/numbers.
    return apply_alignment(report, result, metadata["dictionary"])

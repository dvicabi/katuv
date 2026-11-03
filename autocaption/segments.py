"""Whole-passage captions. Timing and paragraph boundaries are user controlled."""
import re

from .alignment import Transcript, valid_interval
from .grouping import CaptionLine
from .transcribe import Word


def validate_segments(segments, source, duration):
    if not segments or [token for cue in segments for token in cue["text"].split()] != list(Transcript(source).tokens):
        raise ValueError("אפשר לשנות את החלוקה והשורות, אבל יש לשמור את כל המילים ובאותו הסדר.")
    previous = 0
    for i, cue in enumerate(segments):
        if not cue["text"].strip() or not valid_interval(cue["start"], cue["end"], duration):
            raise ValueError(f"קטע {i+1}: יש לבחור התחלה וסיום בתוך הסרטון.")
        if cue["start"] < previous:
            raise ValueError(f"קטע {i+1}: ההתחלה חופפת לקטע הקודם.")
        previous = cue["end"]


def prepare_segments(layout, duration, report=None, existing=None):
    paragraphs = [part.strip() for part in re.split(r"\n\s*\n", layout.replace("\r\n", "\n")) if part.strip()]
    total = len(Transcript(layout).tokens)
    offset, cues = 0, []
    # Manually swapped word slots cannot seed source-ordered passage boundaries.
    rows = report["words"] if report and not report.get("allow_reordered_timings") and report["transcript"].split() == layout.split() else []
    for i, text in enumerate(paragraphs):
        count = len(text.split())
        start, end = round(duration*offset/total, 2), round(duration*(offset+count)/total, 2)
        end = min(end, duration)
        if rows and valid_interval(rows[offset].get("start"), rows[offset+count-1].get("end"), duration):
            start, end = rows[offset]["start"], rows[offset+count-1]["end"]
        old = existing[i] if existing and i < len(existing) else None
        if old and old["text"] == text:
            start, end = old["start"], old["end"]
        cues.append(dict(text=text, start=start, end=end, label=old["label"] if old and old["text"] == text else f"קטע {i+1}"))
        offset += count
    validate_segments(cues, layout, duration)
    return cues


def segment_lines(cues):
    return [CaptionLine([Word(cue["text"], cue["start"], cue["end"])], block=True) for cue in cues]

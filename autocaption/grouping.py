"""Group whisper words into short caption lines suited to shorts/reels."""

from __future__ import annotations

from dataclasses import dataclass

from .transcribe import Word

SENTENCE_END = ".!?…"


@dataclass
class CaptionLine:
    words: list[Word]
    block: bool = False  # Whole manually timed passage, with explicit line breaks.

    @property
    def start(self) -> float:
        return self.words[0].start

    @property
    def end(self) -> float:
        return self.words[-1].end

    @property
    def text(self) -> str:
        return " ".join(w.text for w in self.words)


def group_words(
    words: list[Word],
    max_words: int = 4,
    max_chars: int = 18,
    gap_break: float = 0.6,
    snap_gaps: bool = True,
    fixed_count: bool = False,
) -> list[CaptionLine]:
    if max_words < 1 or max_chars < 1:
        raise ValueError("Caption line limits must be positive.")
    lines: list[CaptionLine] = []
    current: list[Word] = []

    def flush() -> None:
        if current:
            lines.append(CaptionLine(_snap_gaps(current.copy()) if snap_gaps else current.copy()))
            current.clear()

    for word in words:
        if current:
            line_chars = sum(len(w.text) for w in current) + len(current) - 1
            if (
                len(current) >= max_words
                or (not fixed_count and (word.start - current[-1].end > gap_break
                    or line_chars + 1 + len(word.text) > max_chars
                    or current[-1].text[-1] in SENTENCE_END))
            ):
                flush()
        current.append(word)
    flush()
    return lines


def _snap_gaps(words: list[Word]) -> list[Word]:
    """Extend each word to the start of the next so the highlight never blinks off."""
    snapped: list[Word] = []
    for i, w in enumerate(words):
        if i + 1 < len(words):
            end = max(words[i + 1].start, w.start + 0.02)
        else:
            end = w.end
        snapped.append(Word(w.text, w.start, end))
    return snapped

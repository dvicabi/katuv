"""Caption style presets."""

from __future__ import annotations

from dataclasses import dataclass
import math


@dataclass
class StylePreset:
    name: str
    mode: str  # "pop" | "karaoke"
    font: str = "Noto Sans Hebrew"
    font_size: int = 130  # at the 1080x1920 reference resolution
    text_color: str = "#FFFFFF"
    highlight_color: str = "#FFD700"
    outline_color: str = "#000000"
    outline: float = 4.0
    shadow: float = 1.5
    caps: bool = False
    pos_y: float = 0.68  # vertical position as a fraction of frame height
    pos_x: float = .5
    rotation: float = 0  # clockwise degrees, like the browser editor
    fade_in_ms: int = 65
    fade_out_ms: int = 75
    pop_scale: int = 127  # maximum scale, also used by the no-clipping guard
    pop_ms: int = 220
    # (fraction of pulse duration, scale %, easing exponent for this segment).
    # Start/end at 100 so changing active words does not snap the line's layout.
    pulse: tuple = ((0, 100, 1), (.25, 127, .65), (.62, 96, 1.5), (.82, 102, .8), (1, 100, 1))
    entry_y: float = 20  # pixels at 1080x1920; movement stays inside line times
    entry_ms: int = 130
    exit_y: float = -8
    exit_ms: int = 90
    color_ms: int = 40
    backdrop_opacity: float = 0
    word_animation: bool = True


PRESETS: dict[str, StylePreset] = {
    "pop": StylePreset(name="pop", mode="pop"),
    "karaoke": StylePreset(name="karaoke", mode="karaoke", pop_scale=100,
                           pulse=((0, 100, 1), (1, 100, 1)), entry_y=16),
    "zoom": StylePreset(name="zoom", mode="pop", pop_scale=119, pop_ms=240,
                        pulse=((0, 100, 1), (.32, 119, .65), (.7, 99, 1.4), (1, 100, .8))),
    "float": StylePreset(name="float", mode="pop", pop_scale=100, pop_ms=0,
                         pulse=((0, 100, 1), (1, 100, 1)), entry_y=38,
                         entry_ms=230, fade_in_ms=100, fade_out_ms=100, color_ms=80),
}

# Older saved exports remain on disk, but must be regenerated to use this motion.
MOTION_REVISION = 5


def quantize(value: float) -> int:
    """Positive half-up rounding, shared with the browser's Math.round."""
    return math.floor(value + .500000001)


def pulse_frames(style: StylePreset, duration_ms: int) -> list[tuple[int, float]]:
    """Sample a smoothstep spring into linear libass transforms.

    Each crest has zero velocity. The browser interpolates these same samples,
    so short words, including those shorter than one video frame, stay bounded.
    """
    length = min(style.pop_ms, quantize(duration_ms * .78))
    frames = {0: 100.0}
    if length < 8:
        return list(frames.items())
    for (a, start, _), (b, end, _) in zip(style.pulse, style.pulse[1:]):
        for step in range(1, 13):
            t = step / 12
            time = quantize((a + (b-a)*t)*length)
            if time > 0:
                frames[time] = round(start + (end-start)*t*t*(3-2*t), 5)
    return sorted(frames.items())


def motion_spec(style: StylePreset) -> dict:
    """Shared by libass generation and browser previews; milliseconds throughout."""
    return {"curve": "smoothstep-12", **{key: getattr(style, key) for key in (
        "mode", "pulse", "pop_ms", "pop_scale", "entry_y", "entry_ms",
        "exit_y", "exit_ms", "fade_in_ms", "fade_out_ms", "color_ms")}}

"""Generate .ass subtitles with per-word karaoke / pop effects."""

from __future__ import annotations

import unicodedata
import math
from pathlib import Path

from PIL import ImageFont

from .grouping import CaptionLine
from .styles import StylePreset, pulse_frames, quantize

REF_W, REF_H = 1080, 1920  # reference resolution the preset sizes are tuned for

HEADER_TEMPLATE = """\
[Script Info]
ScriptType: v4.00+
PlayResX: {width}
PlayResY: {height}
ScaledBorderAndShadow: yes
WrapStyle: 2

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Caption,{font},{font_size},{primary},{secondary},{outline_color},{back_color},{bold},0,0,0,100,100,0,{angle},1,{outline:.1f},{shadow:.1f},5,40,40,0,-1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""


def _style_color(color: str, alpha: int = 0) -> str:
    """'#RRGGBB' -> ASS style colour '&HAABBGGRR' (ASS is blue-green-red ordered)."""
    c = color.lstrip("#")
    r, g, b = c[0:2], c[2:4], c[4:6]
    return f"&H{alpha:02X}{b}{g}{r}".upper()


def _inline_color(color: str) -> str:
    """'#RRGGBB' -> inline override colour '&HBBGGRR&'."""
    c = color.lstrip("#")
    r, g, b = c[0:2], c[2:4], c[4:6]
    return f"&H{b}{g}{r}&".upper()


def ass_time(seconds: float) -> str:
    cs = max(0, quantize(seconds * 100))
    h, rem = divmod(cs, 360_000)
    m, rem = divmod(rem, 6_000)
    s, cs = divmod(rem, 100)
    return f"{h}:{m:02d}:{s:02d}.{cs:02d}"


def _display(text: str, caps: bool) -> str:
    t = text.upper() if caps else text
    # Break literal backslash escapes without changing visible glyphs.
    # libass supports escaped braces; never substitute punctuation.
    return t.replace("\\", "\\\u2060").replace("{", r"\{").replace("}", r"\}")


def _direction(line: CaptionLine) -> str:
    # Keep logical order; libass/FriBidi handles punctuation and LTR runs.
    return "\u200f" if any(unicodedata.bidirectional(c) in ("R", "AL") for c in line.text) else ""


def _fit_size(line: CaptionLine, style: StylePreset, width: int, height: int) -> int:
    """Fit complete words, including peak zoom, instead of clipping long tokens."""
    requested = max(1, round(style.font_size * height / REF_H))
    if line.block:
        w, h = _block_metrics(line, requested)
        angle = math.radians(style.rotation)
        bound_w = abs(math.cos(angle))*w + abs(math.sin(angle))*h
        bound_h = abs(math.sin(angle))*w + abs(math.cos(angle))*h
        return max(1, min(requested, int(requested*width*.88/max(1, bound_w)),
                          int(requested*height*.80/max(1, bound_h))))
    fonts = Path(__file__).resolve().parent / "fonts"
    bundled = {"Noto Sans Hebrew": fonts / "NotoSansHebrew.ttf", "Anton": fonts / "Anton-Regular.ttf"}
    try:
        font = ImageFont.truetype(str(bundled.get(style.font, style.font)), requested)
        lengths = [font.getlength(w.text.upper() if style.caps else w.text) for w in line.words]
        length = sum(lengths) + max(0, len(lengths) - 1) * font.getlength(" ")
        peak = length + max(lengths, default=0) * (max(100, style.pop_scale) / 100 - 1)
        # libass sizes by ascent + descent; Pillow/CSS use unitsPerEm.
        # Fit actual glyph widths, not an extra 36% of empty horizontal space.
        if style.font == "Noto Sans Hebrew":
            peak *= 1000/1360
    except OSError:
        # A custom system font may not resolve through Pillow. Conservative em
        # bounds keep all text visible; libass still renders the selected font.
        peak = sum(not unicodedata.combining(c) for c in line.text) * requested * max(1, style.pop_scale / 100)
    angle = math.radians(style.rotation)
    bound_w = abs(math.cos(angle)) * peak + abs(math.sin(angle)) * requested * style.pop_scale / 100
    bound_h = abs(math.sin(angle)) * peak + abs(math.cos(angle)) * requested * style.pop_scale / 100
    return max(1, min(requested, int(requested * width * .88 / max(1, bound_w)),
                      int(requested * height * .80 / max(1, bound_h))))


def caption_geometry(line: CaptionLine, style: StylePreset, width: int, height: int) -> tuple[float, float, float, float]:
    """Keep the full line and its peak animation inside the original video frame."""
    size = _fit_size(line, style, width, height)
    if line.block:
        text_width, text_height = _block_metrics(line, size)
    else:
        text_width, text_height = None, size*style.pop_scale/100
    try:
        font = ImageFont.truetype(str(Path(__file__).parent / "fonts/NotoSansHebrew.ttf"), size)
        lengths = [] if line.block else [font.getlength(w.text) for w in line.words]
        if not line.block:
            text_width = sum(lengths) + max(0, len(lengths)-1) * font.getlength(" ")
            text_width += max(lengths, default=0) * (max(100, style.pop_scale) / 100 - 1)
            if style.font == "Noto Sans Hebrew":
                text_width *= 1000/1360
    except OSError:
        if not line.block:
            text_width = len(line.text) * size * max(1, style.pop_scale / 100)
    angle = math.radians(style.rotation)
    box_w = abs(math.cos(angle)) * text_width + abs(math.sin(angle)) * text_height
    box_h = abs(math.sin(angle)) * text_width + abs(math.cos(angle)) * text_height
    scale = height / REF_H
    padding = size * .18 if style.backdrop_opacity else 0
    margin = ((style.outline + style.shadow) * scale + min(width, height) * .015
              + padding * (abs(math.cos(angle)) + abs(math.sin(angle))))
    half_w = min(width / 2, box_w / 2 + margin)
    half_h = box_h / 2 + margin
    top = min(height / 2, half_h + max(0, -style.exit_y) * scale)
    bottom = min(height / 2, half_h + max(0, style.entry_y) * scale)
    return (max(half_w, min(width-half_w, width*style.pos_x)),
            max(top, min(height-bottom, height*style.pos_y)),
            text_width + padding*2,
            text_height + padding*2)


def _block_metrics(line: CaptionLine, size: int) -> tuple[float, float]:
    rows = line.text.splitlines() or [line.text]
    font = ImageFont.truetype(str(Path(__file__).parent / "fonts/NotoSansHebrew.ttf"), size)
    return max(font.getlength(row) for row in rows)*1000/1360, len(rows)*size


def caption_center(line: CaptionLine, style: StylePreset, width: int, height: int) -> tuple[float, float]:
    return caption_geometry(line, style, width, height)[:2]


def build_ass(
    lines: list[CaptionLine],
    style: StylePreset,
    width: int,
    height: int,
) -> str:
    scale = height / REF_H
    if style.mode == "karaoke":
        # \kf sweeps SecondaryColour -> PrimaryColour as each word is spoken
        primary, secondary = style.highlight_color, style.text_color
    else:
        primary, secondary = style.text_color, style.highlight_color

    header = HEADER_TEMPLATE.format(
        width=width,
        height=height,
        font=style.font,
        bold=-1 if style.font == "Noto Sans Hebrew" else 0,
        font_size=round(style.font_size * scale),
        primary=_style_color(primary),
        secondary=_style_color(secondary),
        outline_color=_style_color(style.outline_color),
        back_color=_style_color("#000000", alpha=128),
        outline=style.outline * scale,
        shadow=style.shadow * scale,
        angle=-style.rotation,
    )

    events: list[str] = []
    for line in lines:
        size_tag = f"\\fs{_fit_size(line, style, width, height)}"
        if style.backdrop_opacity:
            events.extend(_backdrop_events(line, style, width, height))
        if line.block or not style.word_animation:
            text = r"\N".join(_direction(line)+_display(row, style.caps) for row in line.text.splitlines())
            events.append(f"Dialogue: 1,{ass_time(line.start)},{ass_time(line.end)},Caption,,0,0,0,,"
                          f"{{{_line_motion(line,style,line.start,line.end,width,height)}{size_tag}"
                          f"\\c{_inline_color(style.text_color)}}}{text}")
        elif style.mode == "karaoke":
            events.append(_karaoke_event(line, style,
                          _line_motion(line, style, line.start, line.end, width, height) + size_tag))
        else:
            events.extend(_pop_events(line, style, width, height, size_tag))
    return header + "\n".join(events) + "\n"


def _line_motion(line: CaptionLine, style: StylePreset, start: float, end: float,
                 width: int, height: int) -> str:
    """Line entrance/exit only. Never restart the entrance on every word."""
    duration = max(1, quantize(end * 100) * 10 - quantize(start * 100) * 10)
    first = quantize(start * 100) == quantize(line.start * 100)
    last = quantize(end * 100) == quantize(line.end * 100)
    enter = min(style.entry_ms, max(1, quantize((line.words[0].end - line.start) * 450)))
    leave = min(style.exit_ms, max(1, quantize((line.end - line.words[-1].start) * 350)))
    fade_in = min(style.fade_in_ms, enter) if first else 0
    fade_out = min(style.fade_out_ms, leave) if last else 0
    x, y = caption_center(line, style, width, height)
    scale = height / REF_H
    if first and style.entry_y and enter:
        position = f"\\move({x:.2f},{y + style.entry_y * scale:.2f},{x:.2f},{y:.2f},0,{enter})"
    elif last and style.exit_y and leave:
        position = f"\\move({x:.2f},{y:.2f},{x:.2f},{y + style.exit_y * scale:.2f},{max(0, duration - leave)},{duration})"
    else:
        position = f"\\pos({x:.2f},{y:.2f})"
    return position + (f"\\fad({fade_in},{fade_out})" if fade_in or fade_out else "")


def _active_motion(style: StylePreset, duration_ms: int) -> str:
    """An eased spring settles before the word ends; color also returns gently."""
    color_ms = max(1, min(style.color_ms, quantize(duration_ms * .2)))
    tags = (f"\\c{_inline_color(style.text_color)}"
            f"\\t(0,{color_ms},0.7,\\c{_inline_color(style.highlight_color)})"
            f"\\t({max(color_ms, duration_ms-color_ms)},{duration_ms},1.3,\\c{_inline_color(style.text_color)})")
    frames = pulse_frames(style, duration_ms)
    if len(frames) > 1:
        tags += "\\fscx100\\fscy100"
        previous = 0
        for target, size in frames[1:]:
            tags += f"\\t({previous},{target},1,\\fscx{size:g}\\fscy{size:g})"
            previous = target
    return tags


def _intervals(line: CaptionLine):
    intervals = []
    for i, word in enumerate(line.words):
        intervals.append((word.start, word.end, i))
        if i + 1 < len(line.words) and quantize(line.words[i+1].start * 100) > quantize(word.end * 100):
            intervals.append((word.end, line.words[i+1].start, None))
    return intervals


def _rounded_rect(w: float, h: float) -> str:
    """A real rounded vector backdrop; no raster images or substituted glyphs."""
    r = min(h*.24, w*.12)
    k = r*.55228475
    return (f"m {r:.2f} 0 l {w-r:.2f} 0 "
            f"b {w-r+k:.2f} 0 {w:.2f} {r-k:.2f} {w:.2f} {r:.2f} l {w:.2f} {h-r:.2f} "
            f"b {w:.2f} {h-r+k:.2f} {w-r+k:.2f} {h:.2f} {w-r:.2f} {h:.2f} l {r:.2f} {h:.2f} "
            f"b {r-k:.2f} {h:.2f} 0 {h-r+k:.2f} 0 {h-r:.2f} l 0 {r:.2f} "
            f"b 0 {r-k:.2f} {r-k:.2f} 0 {r:.2f} 0")


def _backdrop_events(line: CaptionLine, style: StylePreset, width: int, height: int) -> list[str]:
    _, _, w, h = caption_geometry(line, style, width, height)
    drawing = _rounded_rect(w, h)
    intervals = [(line.start, line.end, None)] if style.mode == "karaoke" or line.block or not style.word_animation else _intervals(line)
    alpha = quantize(255*(1-style.backdrop_opacity))
    return [f"Dialogue: 0,{ass_time(start)},{ass_time(end)},Caption,,0,0,0,,"
            f"{{{_line_motion(line,style,start,end,width,height)}\\an5\\bord0\\shad0"
            f"\\1c&H181411&\\1a&H{alpha:02X}&\\p1}}{drawing}"
            for start, end, _ in intervals]


def _pop_events(line: CaptionLine, style: StylePreset, width: int, height: int,
                size_tag: str = "") -> list[str]:
    """Word pulses plus neutral gap intervals. Exact acoustic times are untouched."""
    events: list[str] = []
    for start, end, active in _intervals(line):
        duration = max(1, quantize(end * 100) * 10 - quantize(start * 100) * 10)
        parts: list[str] = []
        for j, other in enumerate(line.words):
            text = _display(other.text, style.caps)
            if active == j:
                parts.append(f"{{{_active_motion(style, duration)}}}{text}{{\\r{size_tag}}}")
            else:
                parts.append(text)
        motion = _line_motion(line, style, start, end, width, height)
        events.append(
            f"Dialogue: 1,{ass_time(start)},{ass_time(end)},Caption,,0,0,0,,"
            f"{{{motion}{size_tag}}}{_direction(line)}{' '.join(parts)}"
        )
    return events


def _karaoke_event(line: CaptionLine, style: StylePreset, pos_tag: str) -> str:
    """One event per line; \\kf sweeps the highlight through each word."""
    parts: list[str] = []
    previous_end_cs = quantize(line.start * 100)
    for word in line.words:
        start_cs, end_cs = quantize(word.start * 100), quantize(word.end * 100)
        gap_cs = start_cs - previous_end_cs
        if gap_cs > 0:
            parts.append(f"{{\\k{gap_cs}}}\u2060")
        duration_cs = max(1, end_cs - start_cs)
        parts.append(f"{{\\kf{duration_cs}}}{_display(word.text, style.caps)}")
        previous_end_cs = end_cs
    return (
        f"Dialogue: 1,{ass_time(line.start)},{ass_time(line.end)},Caption,,0,0,0,,"
        f"{{{pos_tag}}}{_direction(line)}{' '.join(parts)}"
    )

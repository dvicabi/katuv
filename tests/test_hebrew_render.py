"""Real libass/FFmpeg regressions, including bidi geometry and audio packet identity."""
from dataclasses import replace
from io import BytesIO
import json
import subprocess

import pytest
from PIL import Image

from autocaption import media
from autocaption.ass_builder import _display, build_ass
from autocaption.cli import FONTS_DIR
from autocaption.grouping import CaptionLine, group_words
from autocaption.styles import PRESETS
from autocaption.transcribe import Word


@pytest.fixture
def ffmpeg():
    try:
        return media.find_tool("ffmpeg")
    except media.FFmpegNotFound:
        pytest.skip("FFmpeg with libass is required for render integration tests")


def frame(ffmpeg, ass, output_dir, when):
    path = output_dir / "caption.ass"
    path.write_text(ass, encoding="utf-8")
    with media.subtitle_workspace(path, FONTS_DIR) as (cwd, vf):
        result = subprocess.run(
            [ffmpeg, "-v", "error", "-f", "lavfi", "-i", "color=c=0x1a2436:s=540x960:r=25:d=3",
             "-vf", vf, "-ss", str(when), "-frames:v", "1", "-f", "image2pipe", "-c:v", "png", "pipe:1"],
            cwd=cwd, capture_output=True, check=True)
    return Image.open(BytesIO(result.stdout)).convert("RGB")


def yellow_pixels(image):
    return [(x, y) for y in range(540, 780) for x in range(image.width)
            if (lambda c: c[0] > 150 and c[1] > 110 and c[2] < 90)(image.getpixel((x, y)))]


@pytest.mark.parametrize("style", ["pop", "zoom", "float"])
@pytest.mark.parametrize("tokens,mixed", [(("אחד", "שני", "שלוש"), False),
                                         (("שלום,", "Reels", "2026!"), True)])
def test_actual_rtl_highlight_positions_and_mixed_ltr_runs(ffmpeg, tmp_path, style, tokens, mixed):
    words = [Word(text, i * .6, (i + 1) * .6) for i, text in enumerate(tokens)]
    ass = build_ass([CaptionLine(words)], PRESETS[style], 540, 960)
    centers = []
    for i in range(3):
        pixels = yellow_pixels(frame(ffmpeg, ass, tmp_path, i * .6 + .25))
        assert len(pixels) > 100, "Active glyphs must be rendered, not blank"
        centers.append(sum(x for x, _ in pixels) / len(pixels))
    if mixed:
        # Hebrew is rightmost; English and numbers form an LTR run on its left.
        assert centers[0] > centers[2] > centers[1]
    else:
        assert centers[0] > centers[1] > centers[2]


def test_karaoke_accumulates_highlight_in_hebrew_reading_order(ffmpeg, tmp_path):
    words = [Word(text, i * .6, (i + 1) * .6) for i, text in enumerate(("אחד", "שני", "שלוש"))]
    ass = build_ass([CaptionLine(words)], PRESETS["karaoke"], 540, 960)
    first = yellow_pixels(frame(ffmpeg, ass, tmp_path, .5))
    second = yellow_pixels(frame(ffmpeg, ass, tmp_path, 1.1))
    assert len(second) > len(first)
    assert sum(x for x, _ in first) / len(first) > sum(x for x, _ in second) / len(second)


def test_literal_ass_controls_do_not_rewrite_transcript_or_execute():
    literal = r"{שלום} \N \h {\c&HFF0000&}"
    escaped = _display(literal, False)
    assert r"\{שלום\}" in escaped
    assert "\\\u2060N" in escaped
    assert "(" not in escaped
    assert r"{\c&HFF0000&}" not in escaped


def test_multiline_manual_passages_render_only_in_their_intervals(ffmpeg, tmp_path):
    from autocaption.web import Settings, style_from
    from autocaption.segments import segment_lines
    import numpy as np
    cues=[dict(text="שלום לכולם\nReels 2026\nכל הבית כאן",start=.2,end=1),
          dict(text="הפזמון שלי\nכולו מופיע ביחד",start=1.5,end=2.5)]
    style=style_from(Settings(caption_mode="segments",font_size=150,position=.5).model_dump())
    ass=build_ass(segment_lines(cues),style,540,960)
    for at,visible in [(.1,False),(.5,True),(1.2,False),(1.8,True),(2.8,False)]:
        img=np.array(frame(ffmpeg,ass,tmp_path,at))
        white=(img[:,:,0]>180)&(img[:,:,1]>180)&(img[:,:,2]>180)
        assert bool(white.sum()>100)==visible
        if at==.5:
            ys,xs=np.where(white)
            assert max(ys)-min(ys)>100  # Three distinct rendered lines, not one tiny line.
            assert 10<min(xs)<max(xs)<530


def test_long_immutable_token_fits_without_crop(ffmpeg, tmp_path):
    token = "ובאינטרנציונליזציה01234567890123456789"
    ass = build_ass([CaptionLine([Word(token, 0, 2)])], PRESETS["zoom"], 540, 960)
    assert token in ass
    pixels = yellow_pixels(frame(ffmpeg, ass, tmp_path, .12))
    assert len(pixels) > 100
    assert min(x for x, _ in pixels) > 20
    assert max(x for x, _ in pixels) < 520


def test_font_has_hebrew_niqqud_latin_digits_and_punctuation():
    from fontTools.ttLib import TTFont
    font = TTFont(FONTS_DIR / "NotoSansHebrew.ttf")
    cmap = font.getBestCmap()
    assert all(ord(c) in cmap for c in 'שָׁלוֹם, Reels 2026! () {} "׳״')
    assert font["name"].getDebugName(1) == "Noto Sans Hebrew"


@pytest.mark.parametrize("x,y,rotation", [(.04,.04,-45),(.96,.96,45),(.72,.35,23)])
def test_drag_rotation_and_strong_motion_render_inside_original_frame(ffmpeg, tmp_path, x, y, rotation):
    from autocaption.web import Settings, style_from
    style = style_from(Settings(position_x=x, position=y, rotation=rotation,
                                 font_size=180, motion_strength=2, style="pop",
                                 highlight_color="#FFD700").model_dump())
    line = CaptionLine([Word("המילים",0,.6),Word("שלכם",.8,1.4)])
    rendered = frame(ffmpeg, build_ass([line],style,540,960),tmp_path,.08)
    pixels=[(i,j) for j in range(960) for i in range(540)
            if (lambda c:c[0]>170 and c[1]>110 and (c[2]<100 or c[2]>170))(rendered.getpixel((i,j)))]
    assert len(pixels)>100
    assert 1 < min(i for i,j in pixels) < max(i for i,j in pixels) < 538
    assert 1 < min(j for i,j in pixels) < max(j for i,j in pixels) < 958
    assert rendered.size == (540,960)


def test_transcript_gaps_stay_exact_and_karaoke_offsets_include_silence():
    words = [Word("שלום", 0, .2), Word("עולם", .5, 1)]
    lines = group_words(words, snap_gaps=False)
    assert lines[0].words[0].end == .2
    assert r"\k30" in build_ass(lines, PRESETS["karaoke"], 540, 960)


def white_pixels(image):
    return [(x, y) for y in range(540, 780) for x in range(image.width)
            if (lambda c: min(c) > 175 and max(c)-min(c) < 25)(image.getpixel((x, y)))]


@pytest.mark.parametrize("style", ["zoom", "karaoke"])
def test_rounded_card_is_behind_real_hebrew_glyphs_and_follows_motion(ffmpeg, tmp_path, style):
    words = [Word("שלום,",0,.6),Word("Reels",.8,1.4),Word("2026!",1.5,2.2)]
    preset = replace(PRESETS[style],backdrop_opacity=.78)
    ass = build_ass([CaptionLine(words)],preset,540,960)
    image = frame(ffmpeg,ass,tmp_path,.32)
    dark=[(x,y) for y in range(540,780) for x in range(540)
          if max(image.getpixel((x,y)))<40]
    assert len(dark)>3000 and len(yellow_pixels(image))>100 and len(white_pixels(image))>100
    assert min(x for x,y in dark)>0 and max(x for x,y in dark)<539
    assert min(y for x,y in dark)>540 and max(y for x,y in dark)<779
    # Rounded corners remain the original background rather than a square box.
    left,top=min(x for x,y in dark),min(y for x,y in dark)
    assert max(image.getpixel((left,top)))>40
    assert [(w.text,w.start,w.end) for w in words]==[("שלום,",0,.6),("Reels",.8,1.4),("2026!",1.5,2.2)]


def test_renderer_and_editor_share_half_centisecond_boundaries():
    from autocaption.ass_builder import ass_time
    from autocaption.alignment import valid_interval
    assert ass_time(.125)=="0:00:00.13"
    assert ass_time(.145)=="0:00:00.15"
    assert not valid_interval(.125,.134,1)
    assert valid_interval(.125,.135,1)


@pytest.mark.parametrize("style", ["zoom", "pop"])
def test_spring_visibly_grows_then_settles_without_extending_word_times(ffmpeg, tmp_path, style):
    words = [Word("מילים", 0, .6), Word("בתנועה", .8, 1.4)]
    ass = build_ass([CaptionLine(words)], PRESETS[style], 540, 960)
    peak = yellow_pixels(frame(ffmpeg, ass, tmp_path, .08))
    settled = yellow_pixels(frame(ffmpeg, ass, tmp_path, .32))
    width = lambda pixels: max(x for x, _ in pixels)-min(x for x, _ in pixels)
    assert width(peak) > width(settled)*1.07
    # The acoustic pause remains unhighlighted while the line stays readable.
    gap = frame(ffmpeg, ass, tmp_path, .68)
    assert len(white_pixels(gap)) > 300
    assert len(yellow_pixels(gap)) == 0
    assert "0:00:00.60,0:00:00.80" in ass
    assert [(w.start, w.end) for w in words] == [(0, .6), (.8, 1.4)]


def test_float_entrance_moves_the_actual_hebrew_glyphs_upward(ffmpeg, tmp_path):
    ass = build_ass([CaptionLine([Word("כניסה", 0, .6), Word("חלקה", .8, 1.4)])],
                    PRESETS["float"], 540, 960)
    early = yellow_pixels(frame(ffmpeg, ass, tmp_path, .08))
    settled = yellow_pixels(frame(ffmpeg, ass, tmp_path, .32))
    center_y = lambda pixels: sum(y for _, y in pixels)/len(pixels)
    assert center_y(early) > center_y(settled)+5


@pytest.mark.parametrize("style", sorted(PRESETS))
def test_peak_motion_keeps_a_long_immutable_word_inside_the_video(ffmpeg, tmp_path, style):
    token = "ובאינטרנציונליזציה01234567890123456789"
    ass = build_ass([CaptionLine([Word(token, 0, 1)])], PRESETS[style], 540, 960)
    rendered = frame(ffmpeg, ass, tmp_path, .08)
    pixels = white_pixels(rendered) + yellow_pixels(rendered)
    assert token in ass and len(pixels) > 100
    assert min(x for x, _ in pixels) > 20 and max(x for x, _ in pixels) < 520


def probe(path):
    result = subprocess.run([media.find_tool("ffprobe"), "-v", "error", "-show_streams",
                             "-show_format", "-of", "json", str(path)], capture_output=True, check=True)
    return json.loads(result.stdout)


def audio_packets(ffmpeg, path):
    return subprocess.run([ffmpeg, "-v", "error", "-i", str(path), "-map", "0:a", "-c", "copy",
                           "-f", "streamhash", "-hash", "sha256", "pipe:1"],
                          capture_output=True, check=True).stdout


@pytest.mark.parametrize("rotation", [0, 90])
@pytest.mark.parametrize('resolution', ['source', '720'])
def test_burn_preserves_framing_all_audio_and_progress_with_unicode_paths(ffmpeg, tmp_path, rotation, resolution):
    folder = tmp_path / "שלום O'Brien [reels]"
    folder.mkdir()
    original = folder / "original.mp4"
    source = folder / "source.mp4"
    output = folder / "captioned.mp4"
    subprocess.run([ffmpeg, "-v", "error", "-y", "-f", "lavfi", "-i", "testsrc2=s=320x568:r=25:d=1.2",
                    "-f", "lavfi", "-i", "sine=frequency=440:duration=1.2",
                    "-f", "lavfi", "-i", "sine=frequency=880:duration=1.2",
                    "-map", "0:v", "-map", "1:a", "-map", "2:a", "-c:v", "libx264",
                    "-preset", "ultrafast", "-c:a", "aac", str(original)], check=True, capture_output=True)
    help_text = subprocess.run([ffmpeg, "-h", "full"], capture_output=True).stdout
    rotation_args = ["-display_rotation:v:0", str(rotation)] if b"-display_rotation" in help_text else []
    subprocess.run([ffmpeg, "-v", "error", "-y", *rotation_args, "-i", str(original), "-map", "0", "-c", "copy",
                    "-metadata:s:v:0", f"rotate={rotation}", str(source)], check=True, capture_output=True)
    before_bytes = source.read_bytes()
    width, height = media.probe_video_size(source)
    expected_size = (568, 320) if rotation else (320, 568)
    assert (width, height) == expected_size
    ass = folder / "כתוביות.ass"
    ass.write_text(build_ass([CaptionLine([Word("שלום!", 0, 1.2)])], PRESETS["zoom"], width, height), encoding="utf-8")
    progress = []
    output_size = media.export_dimensions(width, height, resolution)
    media.burn_subtitles(source, ass, output, FONTS_DIR, progress=progress.append,
                         output_size=None if resolution == 'source' else output_size)
    assert media.probe_video_size(output) == output_size
    before, after = probe(source), probe(output)
    assert before["streams"][0]["nb_frames"] == after["streams"][0]["nb_frames"]
    assert before["streams"][0]["avg_frame_rate"] == after["streams"][0]["avg_frame_rate"]
    assert len([s for s in after["streams"] if s["codec_type"] == "audio"]) == 2
    from fractions import Fraction
    source_ratio = Fraction(before['streams'][0]['display_aspect_ratio'].replace(':','/'))
    assert Fraction(after['streams'][0]['display_aspect_ratio'].replace(':','/')) == (1/source_ratio if rotation else source_ratio)
    assert audio_packets(ffmpeg, source) == audio_packets(ffmpeg, output)
    assert source.read_bytes() == before_bytes
    assert progress[-1] == 1 and progress == sorted(progress)
    assert not list(folder.glob(".autocaption-*"))
    with pytest.raises(ValueError, match="different"):
        media.burn_subtitles(source, ass, source, FONTS_DIR)


def test_downscale_keeps_hebrew_caption_position_and_size_proportional(ffmpeg, tmp_path):
    import io
    from PIL import Image
    source = tmp_path/'source1080.mp4'
    subprocess.run([ffmpeg,'-v','error','-y','-f','lavfi','-i','color=s=1080x1920:r=25:d=0.2',
                    '-c:v','libx264','-preset','ultrafast',str(source)],check=True,capture_output=True)
    ass = tmp_path/'captions.ass'
    ass.write_text(build_ass([CaptionLine([Word('שלום!',0,.2)])],PRESETS['zoom'],1080,1920),encoding='utf8')
    bounds=[]
    for resolution in ('source','720'):
        target=tmp_path/(resolution+'.mp4')
        size=media.export_dimensions(1080,1920,resolution)
        media.burn_subtitles(source,ass,target,FONTS_DIR,output_size=None if resolution=='source' else size)
        assert media.probe_video_size(target)==size
        picture=subprocess.check_output([ffmpeg,'-v','error','-i',str(target),'-ss','0.08',
            '-frames:v','1','-f','image2pipe','-c:v','png','pipe:1'])
        frame_image=Image.open(io.BytesIO(picture)).convert('L')
        box=frame_image.point(lambda v:255 if v>80 else 0).getbbox()
        assert box is not None
        bounds.append([box[0]/size[0],box[1]/size[1],box[2]/size[0],box[3]/size[1]])
    assert max(abs(a-b) for a,b in zip(*bounds))<.004


def test_alignment_audio_preserves_initial_delay_on_video_timeline(ffmpeg, tmp_path):
    import numpy as np
    source = tmp_path / "delayed.mp4"
    subprocess.run([ffmpeg, "-v", "error", "-y", "-f", "lavfi", "-i", "color=s=320x568:r=25:d=3",
                    "-itsoffset", "1", "-f", "lavfi", "-i", "sine=frequency=440:duration=1",
                    "-map", "0:v", "-map", "1:a", "-c:v", "libx264", "-preset", "ultrafast",
                    "-c:a", "aac", str(source)], check=True, capture_output=True)
    samples = np.frombuffer(media.extract_audio(source), dtype=np.float32)
    assert np.max(np.abs(samples[:int(.8 * 16000)])) < .001
    assert np.max(np.abs(samples[int(1.1 * 16000):int(1.5 * 16000)])) > .01
    assert media.probe_duration(source) >= 3

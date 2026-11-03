"""Render all four real subtitle animations, plus a labeled comparison video."""
from pathlib import Path
import json
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from autocaption import media
from autocaption.alignment import Transcript, media_digest, report_words, validate_report
from autocaption.ass_builder import build_ass, HEADER_TEMPLATE, _style_color
from autocaption.cli import FONTS_DIR
from autocaption.grouping import group_words
from autocaption.web import Settings, style_from


def run(*args):
    return subprocess.run([media.find_tool("ffmpeg"),"-y","-v","error",*map(str,args)],
                          check=True,capture_output=True).stdout


def main():
    out = ROOT / "samples/output"
    video = out / "hebrew-source.mp4"
    report = json.loads(video.with_suffix(".timings.json").read_text(encoding="utf-8"))
    validate_report(report, Transcript(report["transcript"]), media_digest(video))
    lines = group_words(report_words(report), max_words=4, snap_gaps=False)
    audio = run("-i",video,"-map","0:a","-c","copy","-f","streamhash","-hash","sha256","pipe:1")
    order = [("float","כניסה חלקה"),("karaoke","צבע זורם"),("pop","קפיצה קצבית"),("zoom","קפיצה רכה")]
    for name,_ in order:
        style = style_from(Settings(style=name,position=.55,font_size=150).model_dump())
        ass = out/f"motion-{name}.ass"
        ass.write_text(build_ass(lines,style,540,960),encoding="utf-8")
        target = out/f"motion-{name}.mp4"
        media.burn_subtitles(video,ass,target,FONTS_DIR)
        assert media.probe_video_size(target) == (540,960)
        assert run("-i",target,"-map","0:a","-c","copy","-f","streamhash","-hash","sha256","pipe:1") == audio
    card = style_from(Settings(style="zoom", position=.55, font_size=160,
                              backdrop_opacity=.78, outline=0, highlight_color="#FFD54A").model_dump())
    card_ass = out / "motion-card.ass"
    card_ass.write_text(build_ass(lines,card,540,960),encoding="utf-8")
    card_video = out / "motion-card.mp4"
    media.burn_subtitles(video,card_ass,card_video,FONTS_DIR)
    assert media.probe_video_size(card_video)==(540,960)
    assert run("-i",card_video,"-map","0:a","-c","copy","-f","streamhash","-hash","sha256","pipe:1")==audio
    run("-i",card_video,"-ss","2.26","-frames:v",1,out/"motion-card.png")
    labels = HEADER_TEMPLATE.format(width=1296,height=660,font="Noto Sans Hebrew",font_size=36,
               primary=_style_color("#D4F76B"),secondary=_style_color("#FFFFFF"),
               outline_color=_style_color("#000000"),back_color=_style_color("#000000",128),
               bold=-1,angle=0,outline=0,shadow=0)
    for i,(_,label) in enumerate(order):
        labels += f"Dialogue: 0,0:00:00.00,0:00:10.00,Caption,,0,0,0,,{{\\pos({162+324*i},42)}}\u200f{label}\n"
    label_path = out/"motion-labels.ass"
    label_path.write_text(labels,encoding="utf-8")
    inputs=[]
    for name,_ in order:
        inputs.extend(["-i",str(out/f"motion-{name}.mp4")])
    filters=";".join(f"[{i}:v]scale=324:576,pad=324:660:0:84:color=0x121b14[v{i}]" for i in range(4))
    filters += ";[v0][v1][v2][v3]hstack=inputs=4[v]"
    mosaic = out/"motion-comparison-base.mp4"
    run(*inputs,"-filter_complex",filters,"-map","[v]","-map","0:a","-c:v","libx264","-crf",18,"-c:a","copy",mosaic)
    target=out/"motion-comparison.mp4"
    media.burn_subtitles(mosaic,label_path,target,FONTS_DIR)
    run("-i",target,"-ss","2.26","-frames:v",1,out/"motion-comparison.png")
    (out/"motion-verification.json").write_text(json.dumps(dict(styles=[s for s,_ in order],
        source_words_unchanged=True,individual_export_dimensions=[540,960],audio_packet_hash_unchanged=True,
        comparison="Labeled montage only; individual exports retain the original framing"),indent=2),encoding="utf-8")
    print(target)


if __name__ == "__main__":
    main()

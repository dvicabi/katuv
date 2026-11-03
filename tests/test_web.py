"""Web workflow tests: real files/rendering, controlled alignment, immutable input."""
import subprocess
import time

from fastapi.testclient import TestClient
import pytest

from autocaption import media
from autocaption.alignment import media_digest, new_report
from autocaption.web import create_app
from autocaption.verification import apply_verification


def agree(report, model="small"):
    apply_verification(report, {"model":model, "media_sha256":report["media_sha256"], "words":[
        dict(text=row["text"], start=row["start"], end=row["end"], probability=.95) for row in report["words"]]})


@pytest.fixture
def video_bytes(tmp_path):
    try:
        ffmpeg = media.find_tool("ffmpeg")
    except media.FFmpegNotFound:
        pytest.skip("FFmpeg is required")
    path = tmp_path / "source.mp4"
    subprocess.run([ffmpeg, "-y", "-v", "error", "-f", "lavfi", "-i", "color=s=320x568:r=25:d=2",
                    "-f", "lavfi", "-i", "sine=frequency=440:duration=2", "-c:v", "libx264",
                    "-preset", "ultrafast", "-c:a", "aac", "-shortest", str(path)],
                   check=True, capture_output=True)
    return path.read_bytes()


@pytest.fixture
def client(tmp_path):
    with TestClient(create_app(tmp_path / "studio")) as client:
        yield client


def upload(client, video_bytes):
    response = client.post("/api/projects", files={"video": ("שלי.mp4", video_bytes, "video/mp4")})
    assert response.status_code == 200, response.text
    return response.json()


def finished(client, project_id):
    deadline = time.monotonic() + 20
    while time.monotonic() < deadline:
        data = client.get("/api/projects/" + project_id).json()
        if data["state"] not in ("aligning", "verifying", "exporting"):
            return data
        time.sleep(.03)
    raise AssertionError("Background operation did not finish")


@pytest.mark.parametrize('resolution,expected', [('720',(720,1278)), ('1080',(1080,1918)), ('2160',(2160,3834))])
def test_selected_export_resolution_is_real_and_survives_reload(client, video_bytes, monkeypatch, resolution, expected):
    import io
    from PIL import Image
    source = 'שלום, Reels 2026!'
    def align(path, transcript, **kwargs):
        report = new_report(transcript, 2, media_digest(path))
        for i, row in enumerate(report['words']):
            row.update(start=.1+i*.5, end=.5+i*.5, status='aligned', score=.9)
        return report
    monkeypatch.setattr('autocaption.web.align_transcript', align)
    monkeypatch.setattr('autocaption.web.verify_report', lambda *a, **kw: None)
    data = upload(client, video_bytes)
    route = '/api/projects/'+data['id']
    assert data['settings']['export_resolution'] == 'source'
    assert {o['value'] for o in data['export_options']} == {'source','720','1080','1440','2160'}
    client.post(route+'/align', json={'mode':'exact','transcript':source})
    data = finished(client, data['id'])
    original_words = data['report']['words']
    settings = dict(data['settings'], export_resolution=resolution)
    assert client.put(route+'/settings', json=settings).status_code == 200
    assert client.get(route).json()['settings']['export_resolution'] == resolution
    assert client.put(route+'/settings', json=dict(settings, export_resolution='99999')).status_code == 422
    preview = client.get(route+'/preview?at=0.3')
    assert preview.status_code == 200
    assert Image.open(io.BytesIO(preview.content)).size == expected
    assert client.post(route+'/export').status_code == 202
    deadline = time.monotonic()+60
    while time.monotonic() < deadline:
        result = client.get(route).json()
        if result['state'] != 'exporting':
            break
        time.sleep(.1)
    assert result['state'] == 'done', result
    folder = client.app.state.studio.folder(data['id'])
    assert media.probe_video_size(folder/'captioned.mp4') == expected
    assert result['report']['words'] == original_words
    assert result['transcript'] == source
    assert (folder/'video.mp4').read_bytes() == video_bytes
    assert client.get(route+'/file/export').status_code == 200
    # Choosing another resolution invalidates the old downloadable file.
    assert client.put(route+'/settings', json=dict(settings, export_resolution='source')).status_code == 200
    assert not client.get(route).json()['export_ready']
    assert client.get(route+'/file/export').status_code == 409


def test_web_exact_transcript_to_real_export(client, video_bytes, monkeypatch):
    original = "שלום, שלום! Reels 2026"
    def align(path, source, **kwargs):
        assert source.text == original
        report = new_report(source, 2, media_digest(path))
        for i, row in enumerate(report["words"][:2]):
            row.update(start=i*.4, end=i*.4+.3, status="aligned", reason="")
        return report
    monkeypatch.setattr("autocaption.web.align_transcript", align)
    monkeypatch.setattr("autocaption.web.transcribe", lambda *a, **kw: pytest.fail("ASR cannot run in exact mode"))
    def verify(path, report, cache, **kwargs):
        agree(report, kwargs["model_name"])
    monkeypatch.setattr("autocaption.web.verify_report", verify)
    data = upload(client, video_bytes)
    route = "/api/projects/" + data["id"]
    assert client.post(route + "/align", json={"transcript": original, "mode": "exact"}).status_code == 202
    data = finished(client, data["id"])
    assert [w["text"] for w in data["report"]["words"]] == original.split()
    assert data["problems"] == []
    assert len(data["lines"]) > 0
    assert sum(w["status"] == "estimated" for w in data["report"]["words"]) == 2
    assert client.patch(route + "/timings", json={"words": [{"index": 0, "text": "שינוי", "start": 0, "end": .3}]}).status_code == 422
    assert client.patch(route + "/timings", json={"words": [
        {"index": 2, "start": .8, "end": 1.1, "listened": True},
        {"index": 3, "start": 1.2, "end": 1.5, "listened": True}
    ]}).status_code == 200
    settings = dict(data["settings"], style="karaoke", highlight_color="#FFD54A")
    assert client.put(route + "/settings", json=settings).status_code == 200
    preview = client.get(route + "/preview?at=0.2")
    assert preview.status_code == 200 and preview.content.startswith(b"\x89PNG")
    assert client.post(route + "/export").status_code == 202
    result = finished(client, data["id"])
    assert result["state"] == "done", result
    assert result["export_ready"]
    export = client.get(route + "/file/export")
    assert export.status_code == 200 and len(export.content) > 1000
    assert client.get(route + "/file/video").content == video_bytes
    ass = client.get(route + "/file/ass").text
    assert "Reels" in ass and "REELS" not in ass and "2026" in ass
    assert client.get(route + "/file/report").json()["transcript"] == original
    assert client.put(route + "/settings", json=dict(settings, font_size=140)).status_code == 200
    assert client.get(route + "/file/export").status_code == 409


def test_range_scrubbing_waveform_and_local_assets(client, video_bytes):
    data = upload(client, video_bytes)
    base = "/api/projects/" + data["id"]
    response = client.get(base + "/file/video", headers={"Range": "bytes=0-23"})
    assert response.status_code == 206
    assert response.content == video_bytes[:24]
    values = client.get(base + "/waveform").json()
    assert len(values) == 600 and max(values) == 1
    page = client.get("/")
    assert page.status_code == 200 and 'lang="he"' in page.text and 'dir="rtl"' in page.text
    for path in ("/assets/studio.js", "/assets/studio.css", "/fonts/NotoSansHebrew-Regular.ttf"):
        assert client.get(path).status_code == 200
    assert client.get("/api/projects/" + "0"*32).status_code == 404
    assert client.get(base + "/file/studio.log").status_code == 422


def test_gap_extension_and_undo_do_not_lock_acoustic_words_as_manual(client, video_bytes, monkeypatch):
    def align(path, source, **kwargs):
        report = new_report(source, 2, media_digest(path))
        for i, row in enumerate(report['words']):
            row.update(start=round(.1+i*.7, 3), end=round(.5+i*.7, 3), status='aligned', score=.9)
        return report
    monkeypatch.setattr('autocaption.web.align_transcript', align)
    monkeypatch.setattr('autocaption.web.verify_report', lambda *a, **kw: None)
    data = upload(client, video_bytes)
    route = '/api/projects/'+data['id']
    client.post(route+'/align', json=dict(transcript='שלום עולם', mode='exact'))
    original = finished(client, data['id'])['report']['words'][0]
    payload = dict(edit_kind='gap_fill', words=[dict(index=0, start=.1, end=.8)])
    result = client.patch(route+'/timings', json=payload)
    assert result.status_code == 200, result.text
    row = result.json()['report']['words'][0]
    assert row['status'] == 'aligned' and row['gap_fill']['end'] == .5
    assert not row.get('manual_timing')
    payload.update(edit_kind='gap_fill_undo', words=[dict(index=0, start=.1, end=.5)])
    restored = client.patch(route+'/timings', json=payload).json()['report']['words'][0]
    assert restored['status'] == original['status'] and restored['end'] == original['end']
    payload.update(edit_kind='gap_fill', words=[dict(index=0, start=.2, end=.8)])
    assert client.patch(route+'/timings', json=payload).status_code == 422
    payload['edit_kind'] = 'manual'
    assert client.patch(route+'/timings', json=payload).json()['report']['words'][0]['manual_timing'] is True


def test_word_timeline_atomic_swaps_groups_and_render(client, video_bytes, monkeypatch):
    original = 'שלום, Reels 2026 שלום!'
    def align(path, source, **kwargs):
        report = new_report(source, 2, media_digest(path))
        for i, row in enumerate(report['words']):
            row.update(start=round(.1+i*.4, 2), end=round(.35+i*.4, 2), status='aligned', reason='')
        return report
    monkeypatch.setattr('autocaption.web.align_transcript', align)
    monkeypatch.setattr('autocaption.web.verify_report', lambda path, report, cache, **kwargs: agree(report))
    data = upload(client, video_bytes)
    route = '/api/projects/' + data['id']
    assert client.post(route+'/align', json={'transcript':original,'mode':'exact'}).status_code == 202
    data = finished(client, data['id'])
    initial = data['report']['words']
    swapped = [{'index':0,'start':.9,'end':1.15}, {'index':2,'start':.1,'end':.35}]
    assert client.patch(route+'/timings', json={'words':swapped}).status_code == 422
    assert client.get(route).json()['report']['words'] == initial
    response = client.patch(route+'/timings', json={'words':swapped,'allow_reorder':True})
    assert response.status_code == 200, response.text
    data = response.json()
    assert data['report']['allow_reordered_timings'] is True
    assert [row['text'] for row in data['report']['words']] == original.split()
    assert [row['index'] for row in data['report']['words']] == list(range(4))
    assert data['report']['transcript'] == original and data['problems'] == []
    assert data['lines'][0]['indexes'] == [2,1,0,3]
    # Move a chronological range in one request; a rejected group changes nothing.
    group = [{'index':1,'start':.55,'end':.8}, {'index':0,'start':.95,'end':1.2}]
    assert client.patch(route+'/timings', json={'words':group}).status_code == 200
    before = client.get(route).json()['report']
    assert client.patch(route+'/timings', json={'words':[{'index':1,'start':.95,'end':1.2}]}).status_code == 422
    assert client.get(route).json()['report'] == before
    settings = dict(data['settings'], words_per_line=1)
    assert client.put(route+'/settings', json=settings).status_code == 200
    assert client.get(route+'/preview?at=0.2').status_code == 200
    assert client.post(route+'/export').status_code == 202
    exported = finished(client, data['id'])
    assert exported['state'] == 'done' and exported['export_ready'], exported
    srt = client.get(route+'/file/srt').text
    assert srt.index('2026') < srt.index('Reels') < srt.index('שלום,') < srt.index('שלום!')
    assert client.get(route+'/file/video').content == video_bytes
    assert client.get(route+'/file/report').json()['transcript'] == original
    # Restore every original slot (Undo) without replacing or reordering source tokens.
    restore = [{key:row[key] for key in ('index','start','end')} for row in initial]
    response = client.patch(route+'/timings', json={'words':restore})
    assert response.status_code == 200
    assert response.json()['lines'][0]['indexes'] == [0]


def test_word_boundary_borrow_saves_neighbours_atomically(client, video_bytes, monkeypatch):
    original = 'שלום, עולם! היום'
    def align(path, source, **kwargs):
        report = new_report(source, 2, media_digest(path))
        for row, start, end in zip(report['words'], [.1, .6, 1.1], [.6, 1.1, 1.6]):
            row.update(start=start, end=end, status='aligned', reason='')
        return report
    monkeypatch.setattr('autocaption.web.align_transcript', align)
    monkeypatch.setattr('autocaption.web.verify_report', lambda path, report, cache, **kwargs: agree(report))
    data = upload(client, video_bytes)
    route = '/api/projects/'+data['id']
    assert client.post(route+'/align', json={'transcript':original,'mode':'exact'}).status_code == 202
    data = finished(client, data['id'])
    initial = data['report']['words']
    # Moving the first end alone would overlap. The actual gesture saves both sides.
    first = {'index':0,'start':.1,'end':.8}
    assert client.patch(route+'/timings', json={'words':[first]}).status_code == 422
    assert client.get(route).json()['report']['words'] == initial
    response = client.patch(route+'/timings', json={'words':[first, {'index':1,'start':.8,'end':1.1}]})
    assert response.status_code == 200, response.text
    result = response.json()
    assert result['problems'] == [] and not result['report'].get('allow_reordered_timings')
    assert result['report']['transcript'] == original
    assert [row['text'] for row in result['report']['words']] == original.split()
    assert [(r['start'],r['end']) for r in result['report']['words']] == [(.1,.8),(.8,1.1),(1.1,1.6)]
    assert result['report']['words'][2] == initial[2]
    assert client.put(route+'/settings',json=dict(data['settings'],words_per_line=1)).status_code == 200
    assert client.post(route+'/export').status_code == 202
    result = finished(client,data['id'])
    assert result['state'] == 'done' and result['export_ready']
    srt = client.get(route+'/file/srt').text
    assert '00:00:00,100 --> 00:00:00,800' in srt
    assert '00:00:00,800 --> 00:00:01,100' in srt
    assert srt.index('שלום,') < srt.index('עולם!') < srt.index('היום')


def test_filled_word_gaps_persist_and_render_across_caption_lines(client, video_bytes):
    import json
    import shutil
    from pathlib import Path
    from PIL import Image
    from autocaption.alignment import Transcript, save_report
    if not shutil.which('node'):
        pytest.skip('Node is required for the browser gap-fill contract')
    data = upload(client, video_bytes)
    studio = client.app.state.studio
    original = 'שלום, Reels 2026!'
    report = new_report(Transcript(original), 2, data['fingerprint'])
    for row, (start, end) in zip(report['words'], [(.1, .3), (.6, .9), (1.3, 1.6)]):
        row.update(start=start, end=end, status='manual')
    folder = studio.folder(data['id'])
    save_report(folder/'timings.json', report)
    studio.update(data['id'], transcript=original, state='review')
    # Exercise the production browser transform, atomic save and real renderer.
    filled = json.loads(subprocess.check_output([shutil.which('node'), '-e',
        "const W=require('./autocaption/web_static/word-editing.js');"
        "console.log(JSON.stringify(W.fillGaps(JSON.parse(process.argv[1]),2)))",
        json.dumps(report['words'])], cwd=Path(__file__).resolve().parents[1], text=True, encoding='utf-8'))
    route = '/api/projects/'+data['id']
    response = client.patch(route+'/timings', json={'words':[
        {k:row[k] for k in ('index','start','end')} for row in filled]})
    assert response.status_code == 200, response.text
    saved = client.get(route).json()['report']
    assert saved['transcript'] == original
    assert [row['text'] for row in saved['words']] == original.split()
    assert [(row['start'],row['end']) for row in saved['words']] == [(.1,.6),(.6,1.3),(1.3,1.6)]
    assert client.put(route+'/settings',json=dict(data['settings'],words_per_line=1)).status_code == 200
    assert client.post(route+'/export').status_code == 202
    assert finished(client,data['id'])['export_ready']
    srt = client.get(route+'/file/srt').text
    assert '00:00:00,100 --> 00:00:00,600' in srt
    assert '00:00:00,600 --> 00:00:01,300' in srt
    frame = folder/'filled-gap.png'
    subprocess.run([media.find_tool('ffmpeg'),'-y','-v','error','-ss','0.45','-i',str(folder/'captioned.mp4'),
                    '-frames:v','1',str(frame)],check=True,capture_output=True)
    with Image.open(frame) as image:
        assert sum(image.convert('L').histogram()[101:]) > 20


def test_alignment_failure_estimates_all_words_without_blocking(client, video_bytes, monkeypatch):
    monkeypatch.setattr("autocaption.web.verify_report", lambda *a, **kw: (_ for _ in ()).throw(RuntimeError("verification unavailable")))
    monkeypatch.setattr("autocaption.web.align_transcript", lambda *a, **kw: (_ for _ in ()).throw(RuntimeError("model unavailable")))
    data = upload(client, video_bytes)
    base = "/api/projects/" + data["id"]
    assert client.post(base + "/align", json={"transcript": "אחד שני שלוש", "mode": "exact"}).status_code == 202
    result = finished(client, data["id"])
    assert result["state"] == "review"
    assert [w["text"] for w in result["report"]["words"]] == ["אחד", "שני", "שלוש"]
    assert result["problems"] == []
    assert all(w["status"] == "estimated" for w in result["report"]["words"])
    assert client.post(base + "/export").status_code == 202
    assert finished(client, data["id"])["export_ready"]


def test_empty_exact_mode_does_not_fall_back_to_asr(client, video_bytes):
    data = upload(client, video_bytes)
    assert client.post("/api/projects/"+data["id"]+"/align", json={"transcript":" \n", "mode":"exact"}).status_code == 422
    assert client.get("/api/projects/"+data["id"]).json()["state"] == "ready"


def test_manual_verses_export_without_word_alignment_and_keep_exact_source(client, video_bytes, monkeypatch):
    monkeypatch.setattr("autocaption.web.align_transcript", lambda *a, **kw: pytest.fail("No alignment in passage mode"))
    monkeypatch.setattr("autocaption.web.transcribe", lambda *a, **kw: pytest.fail("No ASR in passage mode"))
    data = upload(client, video_bytes)
    route = "/api/projects/"+data["id"]
    source = "שלום, Reels\nבשנת 2026!\n\nזה הפזמון."
    prepared = client.post(route+"/segments/prepare", json={"transcript":source})
    assert prepared.status_code == 200, prepared.text
    data = prepared.json()
    assert data["report"] is None and data["settings"]["caption_mode"] == "segments"
    assert len(data["segments"]) == 2
    cues = data["segments"]
    cues[0].update(start=.2,end=.8,label="בית 1")
    cues[1].update(start=1.1,end=1.8,label="פזמון")
    assert client.put(route+"/segments", json={"segments":cues}).status_code == 200
    assert client.get(route+"/preview?at=.4").status_code == 200
    assert client.post(route+"/export").status_code == 202
    assert finished(client,data["id"])["export_ready"]
    ass = client.get(route+"/file/ass").text
    assert ass.count("Dialogue: 1,") == 2 and r"\N" in ass
    assert r"\kf" not in ass and r"\fscx" not in ass
    assert "0:00:00.20,0:00:00.80" in ass and "0:00:01.10,0:00:01.80" in ass
    srt = client.get(route+"/file/srt").text.replace("\r\n", "\n")
    assert "שלום, Reels\nבשנת 2026!" in srt and "<font" not in srt
    assert client.get(route).json()["transcript"] == source
    assert client.get(route+"/file/video").content == video_bytes
    # Mode switching preserves the manually chosen boundaries.
    settings = dict(data["settings"],caption_mode="words")
    assert client.put(route+"/settings",json=settings).status_code == 200
    restored = client.post(route+"/segments/prepare",json={}).json()
    assert restored["segments"] == cues
    # Neither timing edits nor paragraph edits may omit, invent or reorder words.
    for edited in ([dict(cues[0],text="טקסט אחר"),cues[1]], list(reversed(cues)), [cues[0]]):
        assert client.put(route+"/segments",json={"segments":edited}).status_code == 422
    for edited in ([cues[0],dict(cues[1],start=.7)], [dict(cues[0],end=3),cues[1]]):
        assert client.put(route+"/segments",json={"segments":edited}).status_code == 422
    assert client.post(route+"/segments/prepare",json={"transcript":"שלום בלבד"}).status_code == 422
    assert client.get(route).json()["segments"] == cues


def test_timeline_split_merge_persists_and_renders_exact_hebrew_parts(client, video_bytes):
    data = upload(client, video_bytes)
    route = "/api/projects/"+data["id"]
    source = "שלום, Reels\nבשנת 2026!\n\nזה הפזמון."
    original = client.post(route+"/segments/prepare", json={"transcript":source}).json()["segments"]
    original[0].update(start=.1, end=1.2, label="בית")
    original[1].update(start=1.3, end=1.9, label="פזמון")
    # The timeline slices before a chosen source word, retaining the separator.
    parts = [dict(original[0], text="שלום, Reels\n", end=.65),
             dict(original[0], text="בשנת 2026!", start=.65, label="בית · המשך"), original[1]]
    assert parts[0]["text"].endswith("\n")
    saved = client.put(route+"/segments", json={"segments":parts})
    assert saved.status_code == 200, saved.text
    assert client.get(route).json()["segments"] == parts
    assert client.get(route).json()["transcript"] == source
    assert client.get(route+"/preview?at=.8").status_code == 200
    assert client.post(route+"/export").status_code == 202
    assert finished(client,data["id"])["export_ready"]
    srt = client.get(route+"/file/srt").text.replace("\r\n", "\n")
    assert "00:00:00,100 --> 00:00:00,650" in srt
    assert "00:00:00,650 --> 00:00:01,200" in srt
    assert srt.count("שלום, Reels") == 1 and srt.count("בשנת 2026!") == 1
    # Joining the split halves retains their outer bounds and exact original text.
    joined = [dict(parts[0], text=parts[0]["text"]+parts[1]["text"], end=parts[1]["end"]), parts[2]]
    assert joined == original
    assert client.put(route+"/segments", json={"segments":joined}).status_code == 200
    assert not client.get(route).json()["export_ready"]
    assert client.post(route+"/export").status_code == 202
    assert finished(client,data["id"])["export_ready"]
    ass = client.get(route+"/file/ass").text
    assert ass.count("Dialogue: 1,") == 2
    assert "0:00:00.10,0:00:01.20" in ass
    assert client.get(route).json()["transcript"] == source
    assert client.get(route+"/file/video").content == video_bytes


def test_word_count_is_exact_even_with_long_words_punctuation_and_gaps(client, video_bytes):
    from autocaption.alignment import Transcript, save_report
    data = upload(client, video_bytes)
    studio = client.app.state.studio
    source = "המילים הארוכות. ממשיכות להופיע ביחד בסרטון!"
    report = new_report(Transcript(source), 2, data["fingerprint"])
    for i,row in enumerate(report["words"]):
        row.update(start=i*.3,end=i*.3+.2,status="aligned")
    save_report(studio.folder(data["id"])/"timings.json", report)
    studio.update(data["id"],transcript=source,state="review")
    route = "/api/projects/"+data["id"]
    for count,expected in [(1,[1]*6),(3,[3,3]),(6,[6])]:
        changed = client.put(route+"/settings",json=dict(data["settings"],words_per_line=count)).json()
        assert [len(line["indexes"]) for line in changed["lines"]] == expected
        _, lines, _ = studio.subtitles(data["id"])
        assert [len(line.words) for line in lines] == expected


def test_turning_word_animation_off_keeps_whole_line_without_pulses(client, video_bytes):
    from autocaption.alignment import Transcript, save_report
    data=upload(client,video_bytes)
    studio=client.app.state.studio
    report=new_report(Transcript("שלום עולם"),2,data["fingerprint"])
    for i,row in enumerate(report["words"]):
        row.update(start=.1+i*.6,end=.5+i*.6,status="aligned")
    save_report(studio.folder(data["id"])/"timings.json",report)
    studio.update(data["id"],transcript=report["transcript"],state="review")
    for style in ("zoom","karaoke"):
        studio.update(data["id"],settings=dict(data["settings"],style=style,word_animation=False))
        _,_,ass=studio.subtitles(data["id"])
        assert ass.count("Dialogue: 1,") == 1
        assert r"\kf" not in ass and r"\fscx" not in ass


def test_old_partial_report_recovers_visible_lines_and_export_without_approvals(client, video_bytes):
    from autocaption.alignment import Transcript, save_report
    data = upload(client, video_bytes)
    studio = client.app.state.studio
    folder = studio.folder(data["id"])
    report = new_report(Transcript("שלום, 2026!"), 2, data["fingerprint"])
    report["words"][0].update(start=.2, end=.6, status="aligned")
    save_report(folder/"timings.json", report)
    studio.update(data["id"], transcript=report["transcript"], state="review")
    route = "/api/projects/" + data["id"]
    recovered = client.get(route).json()
    assert recovered["problems"] == []
    assert [i for line in recovered["lines"] for i in line["indexes"]] == [0,1]
    assert (folder/"timings.before-automatic.json").exists()
    assert recovered["report"]["words"][1]["original_timing"]["start"] is None
    assert client.get(route).json()["report"] == recovered["report"]
    # A manual edit needs no listening checkbox, but invalid bounds still fail.
    assert client.patch(route+"/timings", json={"words":[{"index":1,"start":.7,"end":1.4}]}).status_code == 200
    assert client.patch(route+"/timings", json={"words":[{"index":1,"start":.5,"end":3}]}).status_code == 422
    assert client.get(route).json()["report"]["words"][1]["start"] == .7
    assert client.post(route+"/export").status_code == 202
    assert finished(client,data["id"])["export_ready"]


def test_cross_origin_and_unknown_hosts_cannot_access_local_files(client):
    assert client.get("/api/projects", headers={"Origin":"https://unrelated.example"}).status_code == 403
    assert client.get("/api/projects", headers={"Host":"evil.example"}).status_code == 403
    assert client.post("/api/projects", files={"video":("bad.txt",b"hello","text/plain")}).status_code == 422


def test_projects_and_settings_survive_restart(tmp_path, video_bytes):
    root = tmp_path / "projects"
    with TestClient(create_app(root)) as first:
        data = upload(first, video_bytes)
        settings = dict(data["settings"], font_size=150)
        assert first.put("/api/projects/"+data["id"]+"/settings", json=settings).status_code == 200
    with TestClient(create_app(root)) as second:
        saved = second.get("/api/projects/"+data["id"]).json()
        assert saved["settings"]["font_size"] == 150
        assert saved["name"] == "שלי.mp4"


def test_motion_contract_and_obsolete_exports(client, video_bytes):
    from autocaption.styles import PRESETS, MOTION_REVISION
    specs = client.get("/api/motion").json()
    assert set(specs) == set(PRESETS)
    assert specs["float"]["pop_scale"] == 100
    assert specs["zoom"]["pulse"][-1][1] == 100
    data = upload(client, video_bytes)
    studio = client.app.state.studio
    (studio.folder(data["id"]) / "captioned.mp4").write_bytes(video_bytes)
    studio.update(data["id"], export_valid=True, motion_revision=MOTION_REVISION-1)
    route = "/api/projects/"+data["id"]
    assert not client.get(route).json()["export_ready"]
    assert client.get(route+"/file/export").status_code == 409
    settings = dict(data["settings"], style="float")
    assert client.put(route+"/settings", json=settings).status_code == 200


def test_uncertain_verification_does_not_block_export_and_listening_is_optional(client, video_bytes, monkeypatch):
    from autocaption.alignment import Transcript, save_report
    data = upload(client, video_bytes)
    studio = client.app.state.studio
    report = new_report(Transcript("שלום"), 2, data["fingerprint"])
    report["words"][0].update(start=.3, end=.6, status="aligned")
    save_report(studio.folder(data["id"])/"timings.json", report)
    studio.update(data["id"], transcript="שלום", state="review")
    route = "/api/projects/"+data["id"]
    assert client.post(route+"/export").status_code == 202
    assert finished(client, data["id"])["export_ready"]
    assert client.get(route+"/preview?at=.4").status_code == 200
    def verify(path, report, cache, **kwargs):
        report["words"][0]["check"] = dict(status="uncertain", reason="not_heard")
    monkeypatch.setattr("autocaption.web.verify_report", verify)
    assert client.post(route+"/verify").status_code == 202
    checked = finished(client, data["id"])
    assert checked["checks_needed"] == [0]
    assert checked["report"]["words"][0]["text"] == "שלום"
    payload = {"words": [{"index": 0, "start": .3, "end": .6}]}
    assert client.patch(route+"/timings", json=payload).json()["checks_needed"] == [0]
    payload["words"][0]["listened"] = True
    assert client.patch(route+"/timings", json=payload).json()["checks_needed"] == []
    payload["words"][0].update(start=.32, listened=False)
    assert client.patch(route+"/timings", json=payload).json()["checks_needed"] == [0]


def test_model_selection_persists_routes_both_modes_and_preserves_exact_source(client, video_bytes, monkeypatch):
    from copy import deepcopy
    from autocaption.models import IVRIT_TURBO, IVRIT_LARGE, evidence_cache_name
    from autocaption.transcribe import Word
    catalog = client.get("/api/models").json()
    assert catalog["default"] == "auto" and len(catalog["models"]) == 7
    data = upload(client, video_bytes)
    route = "/api/projects/" + data["id"]
    studio = client.app.state.studio
    assert data["speech_model"] == "auto"
    for model in ("small.en", "ivrit-ai/unknown", "../../model"):
        assert client.put(route+"/model", json={"speech_model":model}).status_code == 422
    assert client.put(route+"/model", json={"speech_model":IVRIT_TURBO}).json()["speech_model"] == IVRIT_TURBO
    assert studio.load(data["id"])["speech_model"] == IVRIT_TURBO
    calls = []
    def recognize(path, **kwargs):
        calls.append(kwargs)
        return [Word("שלום,", .2, .6), Word("ישראל!", .7, 1.2)]
    def verify(path, report, cache, **kwargs):
        assert cache.name == evidence_cache_name(kwargs["model_name"])
        calls.append(kwargs)
        agree(report, kwargs["model_name"])
    def align_auto(path, source, **kwargs):
        assert source.text == "שלום, ישראל!"
        report = new_report(source, 2, media_digest(path))
        for i, row in enumerate(report["words"]):
            row.update(start=.25+i*.5, end=.6+i*.5, status="aligned", score=.9)
        return report
    monkeypatch.setattr("autocaption.web.align_transcript", align_auto)
    monkeypatch.setattr("autocaption.web.transcribe", recognize)
    monkeypatch.setattr("autocaption.web.verify_report", verify)
    assert client.post(route+"/align", json={"mode":"auto"}).status_code == 202
    data = finished(client, data["id"])
    assert data["transcript"] == "שלום, ישראל!"
    assert data["report"]["transcription_model"] == IVRIT_TURBO
    assert data["report"]["words"][0]["start"] == .25  # Forced alignment, not the ASR's .2.
    assert len(calls) == 2 and all(call["model_name"] == IVRIT_TURBO for call in calls)
    assert calls[0]["language"] == "he"
    before = deepcopy(data["report"])
    changed = client.put(route+"/model", json={"speech_model":IVRIT_LARGE}).json()
    assert changed["report"] == before  # Selecting does not claim a new verification.
    assert changed["report"]["verification"]["model"] == IVRIT_TURBO
    assert client.post(route+"/verify").status_code == 202
    assert finished(client, data["id"])["report"]["verification"]["model"] == IVRIT_LARGE
    original = "שָׁלוֹם, Reels 2026!"
    def align(path, source, **kwargs):
        assert source.text == original
        report = new_report(source, 2, media_digest(path))
        for i, row in enumerate(report["words"]):
            row.update(start=.1+i*.4, end=.4+i*.4, status="aligned")
        return report
    monkeypatch.setattr("autocaption.web.align_transcript", align)
    monkeypatch.setattr("autocaption.web.transcribe", lambda *a, **kw: pytest.fail("No ASR text in exact mode"))
    assert client.post(route+"/align", json={"mode":"exact", "transcript":original}).status_code == 202
    exact = finished(client, data["id"])
    assert exact["transcript"] == original
    assert [row["text"] for row in exact["report"]["words"]] == original.split()
    assert exact["report"]["verification"]["model"] == IVRIT_LARGE
    studio.update(data["id"], state="verifying")
    assert client.put(route+"/model", json={"speech_model":"small"}).status_code == 409
    studio.update(data["id"], state="review")


def test_old_projects_keep_default_model_and_failed_recheck_keeps_listened_words(client, video_bytes, monkeypatch):
    from autocaption.alignment import Transcript, save_report
    from autocaption.models import IVRIT_TURBO, SpeechModelError
    data = upload(client, video_bytes)
    studio = client.app.state.studio
    folder = studio.folder(data["id"])
    legacy = studio.load(data["id"])
    del legacy["speech_model"]
    studio.write(folder/"project.json", legacy)
    assert studio.snapshot(data["id"])["speech_model"] == "auto"
    report = new_report(Transcript("שלום עולם"), 2, data["fingerprint"])
    for i, row in enumerate(report["words"]):
        row.update(start=.1+i, end=.6+i, status="manual")
        row["check"] = dict(status="listened" if i == 0 else "matched", confirmed_start=row["start"], confirmed_end=row["end"])
    save_report(folder/"timings.json", report)
    studio.update(data["id"], transcript=report["transcript"], speech_model=IVRIT_TURBO)
    def failed(*args, **kwargs):
        raise SpeechModelError("אין מספיק זיכרון. בחרו מודל קל יותר.")
    monkeypatch.setattr("autocaption.web.verify_report", failed)
    route = "/api/projects/"+data["id"]
    assert client.post(route+"/verify").status_code == 202
    result = finished(client, data["id"])
    assert result["checks_needed"] == [1]
    assert result["report"]["words"][0]["check"]["status"] == "listened"
    assert result["report"]["verification"]["status"] == "failed"
    assert result["report"]["verification"]["model"] == IVRIT_TURBO
    assert "מודל קל יותר" in result["report"]["verification"]["detail"]
    assert client.post(route+"/export").status_code == 202
    assert finished(client, data["id"])["export_ready"]


def test_automatic_selection_reports_actual_fallback_model_without_touching_source(client, video_bytes, monkeypatch):
    from autocaption.alignment import Transcript, save_report
    from autocaption.models import IVRIT_LARGE, IVRIT_TURBO, SpeechModelError
    from autocaption.hardware import choose_model
    plan = choose_model(dict(ram_total_gb=64, ram_available_gb=48, cpu_cores=6))
    monkeypatch.setattr("autocaption.web.resolve_selection", lambda _:plan)
    data = upload(client, video_bytes)
    studio = client.app.state.studio
    report = new_report(Transcript("שָׁלוֹם, Reels 2026!"), 2, data["fingerprint"])
    for i,row in enumerate(report["words"]):
        row.update(start=.1+i*.4, end=.4+i*.4, status="manual")
    source = [(row["text"],row["start"],row["end"]) for row in report["words"]]
    save_report(studio.folder(data["id"])/"timings.json", report)
    studio.update(data["id"], transcript=report["transcript"])
    calls=[]
    def verify(path, report, cache, **kwargs):
        calls.append(kwargs["model_name"])
        if kwargs["model_name"] == IVRIT_LARGE:
            raise SpeechModelError("המודל לא נטען") from MemoryError("allocation failed")
        agree(report, kwargs["model_name"])
    monkeypatch.setattr("autocaption.web.verify_report", verify)
    route="/api/projects/"+data["id"]
    assert client.post(route+"/verify").status_code == 202
    result=finished(client,data["id"])
    assert calls == [IVRIT_LARGE, IVRIT_TURBO]
    assert result["speech_model"] == "auto"
    assert result["model_selection"]["model"] == IVRIT_TURBO
    assert result["model_selection"]["fallback_from"] == IVRIT_LARGE
    assert result["report"]["verification"]["model"] == IVRIT_TURBO
    assert [(row["text"],row["start"],row["end"]) for row in result["report"]["words"]] == source


def test_auto_transcript_keeps_multitoken_asr_words_and_completes_failed_alignment(client, video_bytes, monkeypatch):
    from autocaption.transcribe import Word
    data=upload(client,video_bytes)
    monkeypatch.setattr("autocaption.web.transcribe", lambda *a, **kw:[Word("שלום עולם", .1, 1),Word("2026!",1.1,1.5)])
    def failed(path, source, **kwargs):
        assert source.text == "שלום עולם 2026!"
        raise RuntimeError("Forced alignment unavailable")
    monkeypatch.setattr("autocaption.web.align_transcript", failed)
    monkeypatch.setattr("autocaption.web.verify_report", lambda *a, **kw:None)
    route="/api/projects/"+data["id"]
    assert client.post(route+"/align",json={"mode":"auto"}).status_code == 202
    result=finished(client,data["id"])
    assert [row["text"] for row in result["report"]["words"]] == ["שלום","עולם","2026!"]
    assert all(row["start"] is not None for row in result["report"]["words"])
    assert result["problems"] == []
    assert client.post(route+"/export").status_code == 202
    assert finished(client, data["id"])["export_ready"]


def test_edit_parameters_reach_actual_ass_and_persist(client, video_bytes):
    from autocaption.alignment import Transcript, save_report
    data = upload(client, video_bytes)
    route = "/api/projects/"+data["id"]
    settings = dict(data["settings"], position_x=.72, position=.34, rotation=23,
                    motion_strength=1.7, motion_speed=.6, text_color="#F9D4AA", backdrop_opacity=.78)
    saved = client.put(route+"/settings", json=settings).json()
    assert saved["settings"] == settings
    studio = client.app.state.studio
    report = new_report(Transcript("שלום"), 2, data["fingerprint"])
    report["words"][0].update(start=.2, end=1.8, status="manual")
    save_report(studio.folder(data["id"])/"timings.json", report)
    studio.update(data["id"], transcript="שלום", state="review")
    _, _, ass = studio.subtitles(data["id"])
    assert ",0,-23.0,1," in ass and "fscx132.3" in ass
    assert "&H00AAD4F9" in ass
    assert r"\p1" in ass and r"\1a&H38&" in ass
    for key,value in [("rotation",46),("position_x",0),("motion_speed",0),("motion_strength",3),("backdrop_opacity",1)]:
        assert client.put(route+"/settings", json=dict(settings, **{key:value})).status_code == 422


def test_zoomed_waveform_uses_absolute_audio_times_and_reuses_decode(client, video_bytes, monkeypatch):
    import numpy as np
    data = upload(client, video_bytes)
    route = "/api/projects/"+data["id"]+"/waveform"
    audio = np.zeros(32000,dtype=np.float32)
    audio[8800:8960] = 1  # 10 ms sound at .55 s; absent in other windows.
    calls = []
    def decode(path):
        calls.append(path)
        return audio.tobytes()
    monkeypatch.setattr("autocaption.web.media.extract_audio",decode)
    values = client.get(route+"?start=0.5&end=0.7").json()
    assert len(values)==600 and max(values)==1
    assert all(value==0 for value in values[:145]+values[185:])
    assert max(client.get(route+"?start=1&end=1.5").json())==0
    assert len(calls)==1
    for query in ("?start=-1&end=.7","?start=.7&end=.5","?start=nan&end=.7","?start=0", "?start=0&end=20"):
        assert client.get(route+query).status_code==422

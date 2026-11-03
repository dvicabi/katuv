"""Run with the installed interpreter and -I (no source-tree imports)."""
import argparse
import hashlib
import json
from pathlib import Path
import subprocess
import time
import urllib.request


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--url', default='http://127.0.0.1:8771')
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--align', action='store_true')
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    import autocaption
    from autocaption import media
    from autocaption.alignment import Transcript, align_transcript, report_words
    from autocaption.paths import project_dir
    package = Path(autocaption.__file__).parent
    assert 'site-packages' in package.parts, package
    def request(path, body=None):
        data = None if body is None else json.dumps(body).encode()
        req = urllib.request.Request(args.url+path, data=data, headers={'Content-Type':'application/json'})
        with urllib.request.urlopen(req, timeout=60) as response:
            return response.read()
    assert json.loads(request('/api/projects')) == [], 'Use a fresh project directory'
    project = json.loads(request('/api/demo', {}))
    route = '/api/projects/'+project['id']
    (args.output/'preview.png').write_bytes(request(route+'/preview?at=0.4'))
    request(route+'/export', {})
    deadline = time.monotonic()+60
    while time.monotonic() < deadline:
        result = json.loads(request(route))
        if result['state'] != 'exporting':
            break
        time.sleep(.2)
    assert result['state'] == 'done', result.get('message')
    original = package/'assets/hebrew-source.mp4'
    exported = args.output/'hebrew-installed.mp4'
    exported.write_bytes(request(route+'/file/export'))
    assert request(route+'/file/video') == original.read_bytes()
    def audio_hash(path):
        return subprocess.check_output([media.find_tool('ffmpeg'), '-v','error','-i',str(path),
            '-map','0:a:0','-c','copy','-f','hash','-hash','sha256','-']).decode().strip()
    assert audio_hash(original) == audio_hash(exported)
    assert media.probe_video_size(original) == media.probe_video_size(exported)
    findings = dict(package=str(package), version=autocaption.__version__, projects=str(project_dir()),
        words=len(project['report']['words']), export_bytes=exported.stat().st_size,
        audio_hash=audio_hash(exported), source_sha256=hashlib.sha256(original.read_bytes()).hexdigest())
    if args.align:
        source = Transcript(project['transcript'])
        report = align_transcript(original, source, 'cpu')
        words = report_words(report)
        assert [w.text for w in words] == list(source.tokens)
        assert len(words) == 13 and 0 <= words[0].start < .6 and 7 <= words[-1].end <= 8.5
        findings['real_hebrew_alignment'] = True
        findings['first_start'] = words[0].start
        findings['last_end'] = words[-1].end
        (args.output/'alignment.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf8')
    (args.output/'report.json').write_text(json.dumps(findings,ensure_ascii=False,indent=2),encoding='utf8')
    print(json.dumps(findings,ensure_ascii=False,indent=2))


if __name__ == '__main__':
    main()

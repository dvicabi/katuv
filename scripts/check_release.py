"""Reject private files and incomplete wheels before distributing the ZIP."""
import hashlib
import io
import json
from pathlib import Path
import sys
import zipfile


def check(path):
    with zipfile.ZipFile(path) as archive:
        entries = archive.namelist()
        manifest_path = next(name for name in entries if name.endswith('/release.json'))
        prefix = manifest_path.removesuffix('release.json')
        manifest = json.loads(archive.read(manifest_path))
        allowed = {'Install.cmd','Install-Katuv.ps1','Start-Katuv.ps1','Katuv.Common.ps1','Uninstall-Katuv.ps1','requirements-windows.txt',
                   'START-HERE.html','LICENSE.txt','THIRD_PARTY.md','downloads.json',manifest['wheel']}
        assert set(manifest['files']) == allowed
        assert set(entries) == {prefix+name for name in allowed|{'release.json'}}
        for name, digest in manifest['files'].items():
            assert hashlib.sha256(archive.read(prefix+name)).hexdigest() == digest, name
        for name in ('Install-Katuv.ps1','Start-Katuv.ps1','Katuv.Common.ps1','Uninstall-Katuv.ps1'):
            assert archive.read(prefix+name).startswith(b'\xef\xbb\xbf'), 'PowerShell 5.1 requires UTF-8 BOM'
        with zipfile.ZipFile(io.BytesIO(archive.read(prefix+manifest['wheel']))) as wheel:
            files = set(wheel.namelist())
            assert 'autocaption/mobile.py' not in files
            assert 'autocaption/web_static/pair.html' not in files
            for name in ('web_static/index.html','web_static/setup.js','web_static/setup.css',
                'web_static/studio.js','fonts/NotoSansHebrew-Regular.ttf','fonts/NotoSansHebrew-OFL.txt',
                'assets/hebrew-source.mp4','assets/hebrew-source.timings.json','assets/downloads.json'):
                assert 'autocaption/'+name in files, name
            for name in files:
                assert name.startswith(('autocaption/', f'autocaption-{manifest["version"]}.dist-info/'))
                assert not any(part in name for part in ('.autocaption-web', '.venv', '.test-output', '__pycache__'))
                if name.lower().endswith(('.mp4','.wav','.jpg','.png')):
                    assert name == 'autocaption/assets/hebrew-source.mp4', name
                if name.endswith('.py'):
                    assert name.count('/') == 1, name
            report = json.loads(wheel.read('autocaption/assets/hebrew-source.timings.json'))
            assert report['media_sha256'] == hashlib.sha256(wheel.read('autocaption/assets/hebrew-source.mp4')).hexdigest()
            assert report['transcript'].startswith('שלום לכולם.')
            assert f'__version__ = "{manifest["version"]}"' in wheel.read('autocaption/__init__.py').decode()
    print(f'Clean release verified: {Path(path).name} ({len(files)} wheel files)')


if __name__ == '__main__':
    check(sys.argv[1])

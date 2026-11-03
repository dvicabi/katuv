"""Exercise installed imports and a real Hebrew export before activating a runtime."""
import argparse
import json
from pathlib import Path
import tempfile


def check(expected_version):
    from . import __version__, media
    from .bootstrap import ensure_ffmpeg
    from .paths import user_home
    from .web import create_app
    from whisperx.alignment import load_align_model  # Validate inference dependencies without downloading weights.
    if __version__ != expected_version:
        raise RuntimeError('Installed application version does not match the release')
    ensure_ffmpeg(print)
    assets = Path(__file__).with_name('assets')
    with tempfile.TemporaryDirectory(prefix='installation-check-', dir=user_home()) as folder:
        app = create_app(Path(folder))
        studio = app.state.studio
        try:
            with (assets / 'hebrew-source.mp4').open('rb') as source:
                project = studio.create(source, 'דוגמת התקנה.mp4')
            report = json.loads((assets / 'hebrew-source.timings.json').read_text(encoding='utf-8'))
            studio.write(studio.folder(project['id']) / 'timings.json', report)
            studio.update(project['id'], transcript=report['transcript'])
            studio.export(project['id'])
            if not studio.snapshot(project['id'])['export_ready']:
                raise RuntimeError('Installed renderer did not produce a valid export')
            output = studio.folder(project['id']) / 'captioned.mp4'
            if media.probe_video_size(output) != media.probe_video_size(assets / 'hebrew-source.mp4'):
                raise RuntimeError('Installed renderer changed the video dimensions')
        finally:
            studio.executor.shutdown(wait=True, cancel_futures=True)
    print('Installed runtime and Hebrew renderer verified: ' + __version__)


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--expected-version', required=True)
    check(parser.parse_args().expected_version)

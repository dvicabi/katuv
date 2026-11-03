"""First-run preparation. Downloads code/models only, never user media."""
import argparse
import hashlib
import errno
import logging
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import threading
import urllib.request
import urllib.error
import zipfile

from .paths import tools_dir, project_dir


def preparation_error(error):
    if isinstance(error, OSError) and (error.errno == errno.ENOSPC or getattr(error, 'winerror', None) == 112):
        return 'אין מספיק מקום פנוי. פנו מקום בכונן ונסו שוב.'
    if isinstance(error, (urllib.error.URLError, TimeoutError, ConnectionError)):
        return 'ההורדה נקטעה. בדקו את החיבור לאינטרנט ונסו שוב; רכיבים שהושלמו נשמרו.'
    return 'לא ניתן להשלים את הכנת הרכיבים. בדקו אינטרנט ומקום פנוי ונסו שוב. פרטים ביומן היישום.'


def download(url, destination, expected_sha256, progress=None):
    destination = Path(destination)
    staged = destination.with_suffix('.part')
    digest = hashlib.sha256()
    try:
        with urllib.request.urlopen(url, timeout=60) as response, staged.open('wb') as output:
            total = int(response.headers.get('Content-Length', 0))
            completed = 0
            while chunk := response.read(1024*1024):
                output.write(chunk)
                digest.update(chunk)
                completed += len(chunk)
                if progress:
                    progress(completed, total)
        if digest.hexdigest() != expected_sha256.lower():
            raise ValueError('הקובץ שהורד לא עבר בדיקת תקינות. נסו שוב; הקובץ לא הופעל.')
        staged.replace(destination)
    finally:
        staged.unlink(missing_ok=True)


def safe_extract(archive, destination):
    destination = Path(destination).resolve()
    with zipfile.ZipFile(archive) as bundle:
        for member in bundle.infolist():
            target = (destination/member.filename).resolve()
            if not target.is_relative_to(destination) or '\\' in member.filename or ':' in member.filename:
                raise ValueError('נתיב לא תקין בחבילת ההתקנה')
        bundle.extractall(destination)


def ensure_ffmpeg(progress=lambda _: None):
    from .media import find_tool, FFmpegNotFound
    try:
        paths = find_tool('ffmpeg'), find_tool('ffprobe')
        for path in paths:
            result = subprocess.run([path, '-version'], capture_output=True, timeout=15,
                                    creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
            if result.returncode:
                raise FFmpegNotFound('רכיב הווידאו המותקן אינו פועל.')
        filters = subprocess.run([paths[0], '-filters'], capture_output=True, timeout=15,
            creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0)).stdout.decode(errors='replace')
        if not any(' ass ' in line for line in filters.splitlines()):
            raise FFmpegNotFound('רכיב הווידאו המותקן אינו כולל libass לכתוביות.')
        return paths
    except FFmpegNotFound:
        if sys.platform != 'win32':
            raise FFmpegNotFound('יש להתקין FFmpeg באמצעות מנהל החבילות של המחשב: brew install ffmpeg או apt install ffmpeg.')
    tools = tools_dir()
    tools.mkdir(parents=True, exist_ok=True)
    spec = json.loads((Path(__file__).with_name('assets')/'downloads.json').read_text())['ffmpeg_windows']
    with tempfile.TemporaryDirectory(dir=tools, prefix='download-') as temporary:
        archive = Path(temporary)/'ffmpeg.zip'
        progress('מורידים את רכיב הווידאו…')
        download(spec['url'], archive, spec['sha256'],
                 lambda done, total: progress(f'מורידים את רכיב הווידאו: {round(100*done/total)}%' if total else 'מורידים את רכיב הווידאו…'))
        safe_extract(archive, temporary)
        folder = next((p.parent.parent for p in Path(temporary).glob('ffmpeg*/bin/ffmpeg.exe')), None)
        if folder is None or not (folder/'bin/ffprobe.exe').is_file():
            raise ValueError('חבילת הווידאו אינה מלאה')
        target = tools/folder.name
        if target.exists():
            raise ValueError('קיימת התקנה חלקית של רכיב הווידאו. יש לבדוק את תיקיית הרכיבים.')
        shutil.move(str(folder), str(target))
    return str(target/'bin/ffmpeg.exe'), str(target/'bin/ffprobe.exe')


def alignment_cached(name):
    from huggingface_hub import try_to_load_from_cache
    files = [try_to_load_from_cache(name, f) for f in ('config.json', 'preprocessor_config.json', 'vocab.json')]
    weights = [try_to_load_from_cache(name, f) for f in ('model.safetensors', 'pytorch_model.bin')]
    return all(isinstance(p, str) and Path(p).is_file() for p in files) and any(isinstance(p, str) and Path(p).is_file() for p in weights)


def prepare_models(progress=lambda _: None, include_noncommercial=False):
    from .hardware import resolve_selection, AUTO_MODEL
    from .models import prepare_model, model_label, IVRIT_TURBO
    from .alignment import HEBREW_MODEL
    from .singing_alignment import SECONDARY_HEBREW_MODEL
    from huggingface_hub import snapshot_download
    plan = resolve_selection(AUTO_MODEL)
    progress('מכינים את '+model_label(plan['model'])+'; ההורדה הראשונה עשויה לקחת זמן…')
    prepare_model(plan['model'])
    acoustic = [HEBREW_MODEL]
    if plan['hardware']['ram_available_gb'] >= 8:
        acoustic.append(SECONDARY_HEBREW_MODEL)
        if plan['model'] != IVRIT_TURBO:
            progress('מכינים מודל עברי נוסף להשוואת תזמון השירה…')
            prepare_model(IVRIT_TURBO)
    for name in acoustic:
        if not alignment_cached(name):
            progress('מורידים מודל התאמה לעברית…')
            snapshot_download(name, allow_patterns=['*.json', '*.safetensors', 'pytorch_model.bin'], max_workers=1)
    if plan['hardware']['ram_available_gb'] >= 6:
        import gc
        import torchaudio
        progress('מכינים הפרדת שירה ממוזיקה…')
        separator = torchaudio.pipelines.HDEMUCS_HIGH_MUSDB_PLUS.get_model()
        del separator
        gc.collect()
    if include_noncommercial:
        # This option is explicitly selected by the owner. These optional weights
        # are never bundled or silently downloaded with the general release.
        import gc
        from torchaudio.pipelines import MMS_FA
        from .singing_phrases import PHONIKUD_REPO, PHONIKUD_FILE
        from huggingface_hub import hf_hub_download
        progress('מכינים מודל MMS לשימוש לא מסחרי…')
        model = MMS_FA.get_model(with_star=False)
        del model
        gc.collect()
        hf_hub_download(PHONIKUD_REPO, PHONIKUD_FILE)
        # Phonikud also needs its tokenizer offline.
        from phonikud_onnx import Phonikud
        model = Phonikud(hf_hub_download(PHONIKUD_REPO, PHONIKUD_FILE, local_files_only=True))
        del model
    return plan


def diagnostics():
    from .media import find_tool
    from .hardware import resolve_selection, AUTO_MODEL
    from .models import cached_model_path, model_label
    from .alignment import HEBREW_MODEL
    checks = []
    for name in ('ffmpeg', 'ffprobe'):
        try:
            path = find_tool(name)
            run = subprocess.run([path, '-version'], capture_output=True, timeout=15,
                                 creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
            ok = run.returncode == 0
            checks.append(dict(id=name, ok=ok, title='רכיב הווידאו' if name=='ffmpeg' else 'קריאת סרטונים', detail='מותקן' if ok else 'ההתקנה דורשת תיקון'))
            if name == 'ffmpeg' and ok:
                filters = subprocess.run([path, '-filters'], capture_output=True, timeout=15,
                    creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0)).stdout.decode(errors='replace')
                ass = any(' ass ' in line for line in filters.splitlines())
                checks.append(dict(id='rtl', ok=ass, title='כתוביות וציור עברית', detail='מנוע ASS זמין' if ass else 'נדרשת גרסת FFmpeg עם libass'))
        except Exception:
            checks.append(dict(id=name, ok=False, title=name, detail='חסר רכיב וידאו; לחצו על הכנת המחשב'))
    try:
        plan = resolve_selection(AUTO_MODEL)
        cached = cached_model_path(plan['model']) is not None
        checks.append(dict(id='speech', ok=cached, title=model_label(plan['model']), detail='מוכן לעבודה מקומית' if cached else 'יירד בהכנת המחשב או בשימוש הראשון'))
        checks.append(dict(id='alignment', ok=alignment_cached(HEBREW_MODEL), title='התאמת מילים לעברית', detail='התמליל שלכם נשאר מקור המילים'))
    except Exception as exc:
        plan = None
        checks.append(dict(id='speech', ok=False, title='מנוע העברית', detail='ההתקנה דורשת תיקון: '+str(exc)))
    root = project_dir()
    root.mkdir(parents=True, exist_ok=True)
    free = round(shutil.disk_usage(root).free/1024**3, 1)
    checks.append(dict(id='disk', ok=free >= 10, title='מקום פנוי', detail=f'{free} GB פנויים; ההתקנה הראשונה והמודלים עשויים לצרוך כמה ג׳יגה־בייט'))
    return dict(checks=checks, ready=all(c['ok'] for c in checks), plan=plan, projects_path=str(root))


class Preparation:
    def __init__(self):
        self.lock = threading.Lock()
        self.state = dict(state='idle', message='', error=None)

    def snapshot(self):
        with self.lock:
            return dict(self.state)

    def start(self, studio, include_noncommercial=False):
        from fastapi import HTTPException
        with studio.lock, self.lock:
            studio.available()
            if self.state['state']=='running':
                return dict(self.state)
            if studio.busy():
                raise HTTPException(409, 'יש עיבוד פעיל. המתינו לסיום לפני הכנת המחשב.')
            studio.write(studio.root / '.preferences.json', dict(include_noncommercial=include_noncommercial))
            studio.preparing = True
            self.state = dict(state='running', message='מכינים את המחשב…', error=None)
        def update(message):
            with self.lock:
                self.state['message'] = message
        def work():
            try:
                ensure_ffmpeg(update)
                prepare_models(update, include_noncommercial)
                with self.lock:
                    self.state = dict(state='done', message='המחשב מוכן. המודלים שנבחרו נשמרו לשימוש מקומי.', error=None)
            except Exception as exc:
                logging.exception('Model preparation failed')
                with self.lock:
                    self.state = dict(state='error', message='ההכנה נעצרה. אפשר לנסות שוב; ההורדות שהושלמו נשמרו.', error=preparation_error(exc))
            finally:
                with studio.lock:
                    studio.preparing = False
        try:
            studio.executor.submit(work)
        except Exception:
            with studio.lock, self.lock:
                studio.preparing = False
                self.state = dict(state='error', message='היישום נסגר. פתחו אותו שוב ונסו מחדש.', error=None)
            raise
        return self.snapshot()


def main():
    # Windows redirected consoles otherwise use the machine's legacy code page.
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, 'reconfigure'):
            stream.reconfigure(encoding='utf-8')
    parser = argparse.ArgumentParser(description='הכנת כתוב לשימוש במחשב חדש')
    parser.add_argument('--prepare', action='store_true')
    parser.add_argument('--models', action='store_true')
    parser.add_argument('--include-noncommercial', action='store_true')
    args = parser.parse_args()
    if args.prepare:
        ensure_ffmpeg(print)
    if args.models:
        prepare_models(print, args.include_noncommercial)
    print(json.dumps(diagnostics(), ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()

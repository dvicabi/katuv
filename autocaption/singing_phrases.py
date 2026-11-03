"""Context-safe retries for sung Hebrew. All pronunciation text is analysis-only."""
from collections import defaultdict
from statistics import median
import re
import unicodedata

PHRASE_VERSION = 2
PHONIKUD_REPO = 'Phonikud/phonikud-onnx'
PHONIKUD_FILE = 'phonikud-1.0.int8.onnx'


def phrase_windows(report, measurements, targets):
    anchors = {}
    for index, options in measurements.items():
        models = defaultdict(list)
        for word in options:
            if word['score'] >= (.2 if word['family'] == 'ctc' else .5):
                models[word['model']].append(word)
        if models:
            anchors[index] = {edge: median(median(w[edge] for w in peers)
                for peers in models.values()) for edge in ('start', 'end')}
    windows = []
    for index in targets:
        first, stop = max(0, index-2), min(len(report['words']), index+3)
        # A chorus ending before an instrumental break is not useful context
        # for the next verse. Likewise, never cut a target at a saved manual end.
        for left in range(first, stop-1):
            if left in anchors and left+1 in anchors and anchors[left+1]['start']-anchors[left]['end'] > 4:
                if left < index:
                    first = left+1
                else:
                    stop = left+1
                    break
        while first <= index < stop:
            if first not in anchors:
                first += 1
                continue
            if stop-1 not in anchors:
                stop -= 1
                continue
            start = max(0., anchors[first]['start']-1.2)
            end = min(report['duration'], anchors[stop-1]['end']+1.2)
            if .8 < end-start <= 29:
                windows.append(dict(first=first, stop=stop, start=round(start, 3),
                                    end=round(end, 3), target=index))
                break
            if first == index and stop == index+1:
                break
            center = anchors.get(index, dict(start=(start+end)/2))['start']
            if first < index and (stop == index+1 or center-start > end-center):
                first += 1
            else:
                stop -= 1
    return windows


def pronunciation_text(rows):
    # This conventional abbreviation already has an analysis-only expansion in
    # singing_precision. Keep its source index and exact display spelling.
    return [w['text'] if w['text'] not in ('ה׳', "ה'") else 'השם' for w in rows]


def hebrew_spellings(rows):
    return {w['index']: ''.join(c for c in unicodedata.normalize('NFD', text) if c.isalnum())
            for w, text in zip(rows, pronunciation_text(rows))}


def phonetic_tokens(rows, diacritizer, phonemize):
    expected = pronunciation_text(rows)
    pointed = diacritizer.add_diacritics(' '.join(expected)).split()
    def bare(value):
        return ''.join(c for c in unicodedata.normalize('NFD', value)
                       if unicodedata.category(c) != 'Mn' and c != '|')
    if len(pointed) != len(rows) or any(bare(a) != bare(b) for a, b in zip(pointed, expected)):
        raise ValueError('Pronunciation model changed source letters or word boundaries')
    result = []
    for row, token in zip(rows, pointed):
        ipa = phonemize(token)
        latin = ipa.translate(str.maketrans({'ʃ':'sh', 'χ':'kh', 'ʁ':'r', 'ʔ':'',
                                           'j':'y', 'ɡ':'g', 'ʒ':'zh', 'ˈ':''}))
        latin = re.sub('[^a-z]', '', latin.lower())
        if not latin or any(c.isdigit() for c in row['text']):
            raise ValueError('Word is outside the Hebrew phonetic analysis alphabet')
        result.append(dict(index=row['index'], text=row['text'], pointed=token, analysis=latin))
    return result


def phrase_cache_path(cache_dir, report):
    import hashlib
    from pathlib import Path
    key = hashlib.sha256(f'{PHRASE_VERSION}:{report["media_sha256"]}:{report["transcript_sha256"]}'.encode()).hexdigest()
    return Path(cache_dir)/f'phrases-{key}.json'


def apply_phrases(report, base_records, bundle, resolve_manual_conflicts=False):
    from copy import deepcopy
    from .singing_alignment import collect_measurements, finish_recovery
    from .timing_audit import acoustic_end
    if (bundle.get('version') != PHRASE_VERSION or bundle.get('media_sha256') != report['media_sha256']
            or bundle.get('transcript_sha256') != report['transcript_sha256']):
        raise ValueError('Phrase evidence belongs to different source audio or text')
    before = deepcopy(report)
    working = deepcopy(report)
    extra = collect_measurements(report, bundle['records'])
    finish_recovery(working, collect_measurements(report, base_records+bundle['records']),
                    bundle.get('errors', []), resolve_manual_conflicts)
    changed = [i for i, (a, b) in enumerate(zip(before['words'], working['words']))
               if a['start'] != b['start'] or acoustic_end(a) != acoustic_end(b)]
    for row in working['words']:
        check = row.get('audio_control')
        if check:
            check['matches_current_timing'] = (check['tested_start'] == row['start']
                                               and check['tested_end'] == acoustic_end(row))
    working['phrase_recovery'] = dict(version=PHRASE_VERSION, targets=bundle['targets'],
        measured=sorted(extra), changed=changed, errors=bundle.get('errors', []), human_listened=False)
    report.clear()
    report.update(working)
    return report


def refine_phrases(video, report, base_records, cache_dir, progress=None, resolve_manual_conflicts=False, include_noncommercial=False):
    import gc, json, importlib.util
    from pathlib import Path
    targets = [w['index'] for w in report['words'] if w.get('audit', {}).get('status') != 'supported']
    cache_dir = Path(cache_dir)
    path = phrase_cache_path(cache_dir, report)
    if include_noncommercial:
        path = path.with_name(path.stem + '-noncommercial.json')
    if path.exists():
        from .singing_precision import permitted_bundle
        bundle = permitted_bundle(json.loads(path.read_text(encoding='utf8')), include_noncommercial)
        return apply_phrases(report, base_records, bundle, resolve_manual_conflicts)
    if not targets:
        return report
    from .models import IVRIT_LARGE, IVRIT_TURBO, cached_model_path
    available = [(name, cached_model_path(name)) for name in (IVRIT_LARGE, IVRIT_TURBO)]
    available = [(name, local) for name, local in available if local]
    # Optional models are used only when installed locally; no upload, no hidden
    # multi-gigabyte downloads, and the standard Hebrew alignment stays available.
    import torch
    from huggingface_hub import try_to_load_from_cache
    from .alignment import HEBREW_MODEL
    from .singing_alignment import SECONDARY_HEBREW_MODEL, ctc_measurements
    hebrew_available = []
    for name in (HEBREW_MODEL, SECONDARY_HEBREW_MODEL):
        weights = [try_to_load_from_cache(name, filename)
                   for filename in ('model.safetensors', 'pytorch_model.bin')]
        if any(isinstance(file, str) and Path(file).is_file() for file in weights):
            hebrew_available.append(name)
    pointed_model = try_to_load_from_cache(PHONIKUD_REPO, PHONIKUD_FILE)
    phonetic_available = (include_noncommercial and isinstance(pointed_model, str) and Path(pointed_model).is_file()
        and importlib.util.find_spec('phonikud_onnx') is not None
        and importlib.util.find_spec('phonikud') is not None
        and (Path(torch.hub.get_dir())/'checkpoints/model.pt').is_file())
    if not available and not phonetic_available and not hebrew_available:
        return report
    import numpy as np
    from .media import extract_audio
    from .singing_alignment import collect_measurements, attention_measurements
    audio = np.frombuffer(extract_audio(Path(video)), dtype=np.float32).copy()
    vocal_file = cache_dir/f'vocals-{report["media_sha256"]}-v1.npy'
    vocals = np.load(vocal_file, allow_pickle=False) if vocal_file.exists() else audio
    if vocals.shape != audio.shape or not np.isfinite(vocals).all():
        vocals = audio
    windows = phrase_windows(report, collect_measurements(report, base_records), targets)
    records, errors = [], []
    torch.set_num_threads(min(6, torch.get_num_threads()))
    def announce(message):
        if progress:
            progress(message)
    def measure(family, name, view, window, operation, sound):
        import hashlib
        key = hashlib.sha256(json.dumps([PHRASE_VERSION, report['media_sha256'],
            report['transcript_sha256'], family, name, view, window], sort_keys=True).encode()).hexdigest()
        file = cache_dir/f'phrase-measurement-{key}.json'
        try:
            if file.exists():
                record = json.loads(file.read_text(encoding='utf8'))
            else:
                rows = report['words'][window['first']:window['stop']]
                words = operation(sound, rows, window['start'], window['end'])
                record = dict(family=family, model=name, view=view, window=window, words=words)
                staged = file.with_suffix('.tmp')
                staged.write_text(json.dumps(record, ensure_ascii=False, allow_nan=False), encoding='utf8')
                staged.replace(file)
            records.append(record)
        except Exception as exc:
            errors.append(dict(target=window['target'], model=name, message=str(exc)))
    if phonetic_available:
        from phonikud_onnx import Phonikud
        from phonikud import phonemize
        from torchaudio.pipelines import MMS_FA
        from .singing_precision import multilingual_measurements, MULTILINGUAL_MODEL
        diacritizer = Phonikud(pointed_model)
        pronunciations = {}
        for window in windows:
            rows = report['words'][window['first']:window['stop']]
            try:
                pronunciations[window['target']] = phonetic_tokens(rows, diacritizer, phonemize)
            except Exception as exc:
                errors.append(dict(target=window['target'], stage='pronunciation', message=str(exc)))
        del diacritizer
        gc.collect()
        model = MMS_FA.get_model(with_star=False).eval()
        tokenizer, aligner = MMS_FA.get_tokenizer(), MMS_FA.get_aligner()
        for n, window in enumerate(windows):
            tokens = pronunciations.get(window['target'])
            if not tokens:
                continue
            announce(f'מתאימים הגייה לשירה: {n+1} מתוך {len(windows)}')
            pronunciation = dict(zip([w['index'] for w in report['words'][window['first']:window['stop']]],
                                     pronunciation_text(report['words'][window['first']:window['stop']])))
            for view, sound in (('vocals-phonetic', vocals), ('original-phonetic', audio)):
                measure('ctc', MULTILINGUAL_MODEL, view, window,
                    lambda a, r, s, e: multilingual_measurements(model, tokenizer, aligner, None,
                        a, r, s, e, pronunciation, romanized=[w['analysis'] for w in tokens]), sound)
        del model
        gc.collect()
    # Retry the Hebrew checkpoints with the original full spelling. In songs,
    # removing a written ו from הכול can remove a useful long-vowel anchor.
    # The normalized-spelling observations remain in the original evidence.
    for name in hebrew_available:
        try:
            from whisperx.alignment import load_align_model
            model, metadata = load_align_model(language_code='he', device='cpu', model_name=name)
            for n, window in enumerate(windows):
                announce(f'משווים גבולות במודל עברי: {n+1} מתוך {len(windows)}')
                for view, sound in (('vocals-phrase-spelling', vocals), ('original-phrase-spelling', audio)):
                    def operation(a, rows, start, end):
                        return ctc_measurements(model, metadata, a, rows, start, end, hebrew_spellings(rows))
                    measure('ctc', name, view, window, operation, sound)
            del model
            gc.collect()
        except Exception as exc:
            errors.append(dict(model=name, stage='phrase-spelling', message=str(exc)))
    from faster_whisper import WhisperModel
    from faster_whisper.tokenizer import Tokenizer
    for name, local in available:
        model = WhisperModel(local, device='cpu', compute_type='int8', cpu_threads=6)
        tokenizer = Tokenizer(model.hf_tokenizer, model.model.is_multilingual, task='transcribe', language='he')
        for n, window in enumerate(windows):
            announce(f'בודקים את המילה בתוך המשפט: {n+1} מתוך {len(windows)}')
            for view, sound in (('vocals-phrase', vocals), ('original-phrase', audio)):
                measure('attention', name, view, window,
                    lambda a, r, s, e: attention_measurements(model, tokenizer, a, r, s, e,
                        pronunciation=dict(zip([w['index'] for w in r], pronunciation_text(r)))), sound)
        del model
        gc.collect()
    bundle = dict(version=PHRASE_VERSION, media_sha256=report['media_sha256'],
        transcript_sha256=report['transcript_sha256'], targets=targets, records=records, errors=errors)
    staged = path.with_suffix('.tmp')
    staged.write_text(json.dumps(bundle, ensure_ascii=False, allow_nan=False), encoding='utf8')
    staged.replace(path)
    return apply_phrases(report, base_records, bundle, resolve_manual_conflicts)

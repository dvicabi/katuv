"""A bounded acoustic retry for words that otherwise need interpolation.

Separated vocals are a disposable analysis view. Captions still contain the exact
source tokens; the original video/audio are never replaced. Multiple crops must
agree before a retry can replace an estimate (or a conflicting manual boundary).
"""
from __future__ import annotations

from copy import deepcopy
import hashlib
import json
from pathlib import Path
import subprocess

from .alignment import Transcript, apply_alignment, new_report, valid_interval, HEBREW_MODEL
from .alignment_windows import key, matching_pairs


def retry_spans(report):
    """Use source lines for singing context, bounded even for an unbroken paste."""
    spans, offset = [], 0
    for line in report['transcript'].splitlines():
        count = len(line.split())
        for first in range(offset, offset + count, 12):
            stop = min(first + 12, offset + count)
            if any(w['status'] == 'estimated' for w in report['words'][first:stop]):
                spans.append((first, stop))
        offset += count
    return spans


def stable_candidates(trials, duration, all_candidates=False):
    """Keep real model boundaries supported by another crop, never interpolation."""
    candidates = {}
    for trial in trials:
        for row in trial:
            if (row.get('status') == 'aligned' and (row.get('score') or 0) >= .25
                    and valid_interval(row.get('start'), row.get('end'), duration)):
                candidates.setdefault(row['index'], []).append(row)
    stable = {}
    for index, options in candidates.items():
        supported = []
        for row in options:
            peers = [other for other in options if other is not row
                     and abs(other['start'] - row['start']) <= .08
                     and abs(other['end'] - row['end']) <= .16]
            if peers:
                supported.append((len(peers), row['score'], row))
        if supported:
            choices = [dict(row, timing_method='vocal_consensus', acoustic_retry=dict(
                model='HDEMUCS_HIGH_MUSDB_PLUS', supporting_crops=1 + peers))
                for peers, _, row in sorted(supported, key=lambda item: item[:2], reverse=True)]
            stable[index] = choices if all_candidates else choices[0]
    return stable


def apply_candidates(report, candidates, resolve_manual_conflicts=False, targets=None):
    """Choose a coherent acoustic path, rather than incompatible per-word winners.

    Exact source order and manual locks are hard constraints. Resolving estimates
    is first priority, then model confidence and recognizer proximity rank valid
    paths. Small (<=80 ms) cross-crop overlaps can share a boundary, with provenance.
    """
    rows = report['words']
    choices = {i: v if isinstance(v, list) else [v] for i,v in candidates.items()}
    for i, options in choices.items():
        if not 0 <= i < len(rows) or any(w.get('index') != i or w.get('text') != rows[i]['text']
                                        for w in options):
            raise ValueError('Acoustic proposals cannot change source tokens or indexes')
    targets = ({i for i in choices if rows[i]['status'] == 'estimated'} if targets is None else set(targets))
    active = {i for i in choices if i in targets and (rows[i]['status'] != 'manual' or resolve_manual_conflicts)}
    # Only neighbours whose current slots conflict with a target's possible
    # path may change. This excludes unrelated manual and automatic edits.
    while True:
        expanded = set(active)
        for i in active:
            for option in choices[i]:
                for j in (i-1, i+1):
                    if (j in choices and (rows[j]['status'] != 'manual' or resolve_manual_conflicts)
                            and ((j < i and rows[j]['end'] > option['start'])
                                 or (j > i and option['end'] > rows[j]['start']))):
                        expanded.add(j)
        if expanded == active:
            break
        active = expanded
    states = [((0, 0.), [])]
    for i, original in enumerate(rows):
        options = [original] + choices.get(i, []) if i in active else [original]
        next_states = []
        for option in options:
            changed = option is not original
            quality = (1 - (option.get('score') or 0)) + option.get('guidance_distance', 0) * .2
            quality += (.2 if original['status'] == 'manual' else .05) if changed else 0
            unresolved = int(i in targets and not changed)
            best = None
            for (missing, cost), path in states:
                adjusted = None
                if path and path[-1]['end'] > option['start']:
                    left = path[-1]
                    overlap = left['end'] - option['start']
                    if (overlap > .08 or left['status'] != 'aligned' or
                            option['start'] - left['start'] < .06):
                        continue
                    adjusted = dict(left, end=option['start'], boundary_adjustment=dict(acoustic_end=left['end']))
                rank = (missing + unresolved, cost + quality)
                if best is None or rank < best[0]:
                    new_path = path[:-1] + [adjusted] if adjusted else path
                    best = (rank, new_path + [option])
            if best:
                next_states.append(best)
        states = next_states
        if not states:
            return []
    _, path = min(states, key=lambda state: state[0])
    proposed = {i: dict(w) for i, w in enumerate(path) if w is not rows[i]}
    for i, row in proposed.items():
        row['before_acoustic_retry'] = deepcopy(rows[i])
        if rows[i]['status'] == 'manual':
            row['replaced_manual_timing'] = dict(start=rows[i]['start'], end=rows[i]['end'])
        rows[i] = row
    return sorted(proposed)


class VocalAligner:
    """Lazy models, local cache, bounded clips, no new service or subscription."""
    def __init__(self, video, fingerprint, device, progress, cache_dir):
        import torch
        import torchaudio
        from whisperx.alignment import load_align_model
        self.torch, self.torchaudio = torch, torchaudio
        self.device = ('cuda' if torch.cuda.is_available() else 'cpu') if device == 'auto' else device
        self.video, self.fingerprint, self.cache_dir = video, fingerprint, cache_dir
        if progress:
            progress('מבודדים את קול הזמר לתזמון בלבד; בהפעלה הראשונה יורד מודל של כ־319 MB')
        self.bundle = torchaudio.pipelines.HDEMUCS_HIGH_MUSDB_PLUS
        # Separation runs in bounded CPU clips, leaving GPU memory to alignment.
        self.separator = self.bundle.get_model().eval()
        self.model, self.metadata = load_align_model(language_code='he', device=self.device, model_name=HEBREW_MODEL)

    def vocals(self, start, end):
        import numpy as np
        from .media import find_tool
        signature = hashlib.sha256(json.dumps([self.fingerprint, start, end, 'hdemucs-plus-v1']).encode()).hexdigest()[:24]
        cached = self.cache_dir / f'vocals-{signature}.npy' if self.cache_dir else None
        length = round((end - start) * 16000)
        if cached and cached.exists():
            audio = np.load(cached, allow_pickle=False)
            if audio.shape == (length,) and np.isfinite(audio).all():
                return audio
        raw = subprocess.run([find_tool('ffmpeg'), '-v', 'error', '-i', str(self.video),
                              '-ss', str(start), '-t', str(end - start), '-map', '0:a:0',
                              '-ac', '2', '-ar', str(self.bundle.sample_rate), '-f', 'f32le', 'pipe:1'],
                             capture_output=True, check=True,
                             creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0)).stdout
        torch = self.torch
        wave = torch.from_numpy(np.frombuffer(raw, dtype=np.float32).copy().reshape(-1, 2).T)
        mean, std = wave.mean(), wave.std().clamp_min(1e-8)
        with torch.inference_mode():
            vocals = self.separator(((wave - mean) / std).unsqueeze(0))[0, self.separator.sources.index('vocals')] * std + mean
            audio = self.torchaudio.functional.resample(vocals.mean(0), self.bundle.sample_rate, 16000).numpy()
        # Resampling rounds lengths. Preserve the media time origin exactly.
        audio = np.pad(audio[:length], (0, max(0, length - len(audio))))
        if cached:
            cached.parent.mkdir(parents=True, exist_ok=True)
            np.save(cached, audio, allow_pickle=False)
        return audio

    def trials(self, report, first, stop, start, end):
        from whisperx.alignment import align
        audio = self.vocals(start, end)
        source = Transcript(' '.join(w['text'] for w in report['words'][first:stop]))
        trials = []
        for trim in (0., .13, .27):
            if end - start - 2 * trim < .5:
                continue
            result = align([dict(text=source.text, start=trim, end=len(audio) / 16000 - trim)],
                           self.model, self.metadata, audio, self.device,
                           interpolate_method='nearest', return_char_alignments=True)
            partial = apply_alignment(new_report(source, len(audio) / 16000, report['media_sha256']),
                                      result, self.metadata['dictionary'])
            for row in partial['words']:
                row['index'] += first
                if row['status'] == 'aligned':
                    # Reject words clipped by the acoustic crop boundaries.
                    if row['start'] <= trim + .02 or row['end'] >= len(audio) / 16000 - trim - .02:
                        row['status'] = 'unaligned'
                    row['start'] = round(row['start'] + start, 3)
                    row['end'] = round(row['end'] + start, 3)
            trials.append(partial['words'])
        return trials


def repair_estimates(video: Path, report: dict, evidence: dict, device='auto', progress=None,
                     cache_dir: Path | None = None, resolve_manual_conflicts=False):
    spans = retry_spans(report)
    if not spans or report.get('allow_reordered_timings'):
        return report
    original = deepcopy(report)
    working = deepcopy(report)
    for row in working['words']:
        if row.get('gap_fill'):
            row['end'] = row.pop('gap_fill')['end']
    pairs = dict(matching_pairs([key(w['text']) for w in working['words']],
                                [key(w['text']) for w in evidence['words']]))
    aligner, changed, errors, candidates = None, set(), [], {}
    # A second pass supplies neighbouring words when a line on its own is
    # ambiguous. Stable candidates can also release a conflicting old anchor.
    original_spans = list(spans)
    spans += [(max(0, a-2), min(len(working['words']), b+2)) for a,b in original_spans]
    line_starts, offset = [], 0
    for line in report['transcript'].splitlines():
        if line.split():
            line_starts.append(offset)
        offset += len(line.split())
    for first, stop in original_spans:
        previous = [i for i in line_starts if i < first]
        if previous and stop - previous[-1] <= 24:
            spans.append((previous[-1], stop))
    for first, stop in spans:
        rows = working['words']
        if not any(w['status'] == 'estimated' for w in rows[first:stop]):
            continue
        def edge(i, end=False):
            row = rows[i]
            if i in pairs:
                heard = evidence['words'][pairs[i]]
                a, b = heard.get('raw_start'), heard.get('raw_end')
                if valid_interval(a, b, report['duration']):
                    return b if end else a
            return row['end' if end else 'start']
        start = max(0., edge(first) - .65)
        end = min(report['duration'], edge(stop-1, True) + .65)
        if not .6 <= end - start <= 20:
            continue
        if progress:
            progress(f'מדייקים שירה אוטומטית: מילים {first+1}–{stop} מתוך {len(rows)}')
        try:
            if aligner is None:
                aligner = VocalAligner(video, report['media_sha256'], device, progress, cache_dir)
            trials = aligner.trials(working, first, stop, start, end)
            stable = stable_candidates(trials, report['duration'], all_candidates=True)
            for i, options in stable.items():
                for candidate in options:
                    if i in pairs:
                        heard = evidence['words'][pairs[i]]
                        a, b = heard.get('raw_start'), heard.get('raw_end')
                        if ((heard.get('probability') or 0) >= .45 and valid_interval(a, b, report['duration'])
                                and b-a < 3):
                            if candidate['start'] > b+.35 or candidate['end'] < a-.35:
                                continue  # Strong path to a different sung syllable.
                            candidate['guidance_distance'] = abs(candidate['start']-a) + abs(candidate['end']-b)
                    candidates.setdefault(i, []).append(candidate)
            changed.update(apply_candidates(working, candidates, resolve_manual_conflicts))
        except Exception as error:
            errors.append(str(error))
            # A missing download/runtime cannot prevent preview or export.
            if aligner is None:
                break
    if changed:
        report['words'] = working['words']
    remaining = sum(w['status'] == 'estimated' for w in report['words'])
    repaired = [i for i in sorted(changed) if original['words'][i]['status'] == 'estimated'
                and report['words'][i]['status'] == 'aligned']
    report['singing_repair'] = dict(version=1, repaired=repaired, changed=sorted(changed),
                                  remaining=remaining, errors=errors)
    report['automatic_timing'] = dict(version=1, estimated=remaining)
    return report

"""Evidence-based whole-recording audit, independent of the saved alignment label.

Model agreement is supporting evidence, not ground truth or human listening.
Recognition is never used to supply displayed words.
"""
from itertools import combinations
from statistics import median

from .alignment import valid_interval
from .alignment_windows import key, matching_pairs


def comparison(text):
    token = key(text)
    # Standard plene/defective spelling of kol; comparison only.
    return {'כול': 'כל', 'הכול': 'הכל', 'והכול': 'והכל', 'שהכול': 'שהכל'}.get(token, token)


def speech_references(report, tracks):
    references = {}
    source = [comparison(w['text']) for w in report['words']]
    for track in tracks:
        heard = track['words']
        for i, j in matching_pairs(source, [comparison(w['text']) for w in heard]):
            w = heard[j]
            if ((w.get('probability') or 0) >= .45 and
                    valid_interval(w.get('start'), w.get('end'), report['duration'])):
                references.setdefault(i, []).append(dict(w, model=track['model'], view=track['view']))
    return references


def reference_cluster(observations):
    """Require actual pairwise agreement, not transitive chains of loose matches."""
    groups = []
    for size in range(1, len(observations)+1):
        for group in combinations(observations, size):
            if (max(x['start'] for x in group)-min(x['start'] for x in group) <= .4
                    and max(x['end'] for x in group)-min(x['end'] for x in group) <= .6):
                groups.append(group)
    if not groups:
        return None
    group = max(groups, key=lambda g: (len({x['model'] for x in g}), len(g), sum(x['probability'] for x in g)))
    return dict(start=median(x['start'] for x in group), end=median(x['end'] for x in group),
                models=sorted({x['model'] for x in group}), views=sorted({x['view'] for x in group}),
                observations=list(group))


def source_windows(report, references):
    """Bound every source line from new recognition, never the saved timings."""
    anchors = {i: reference_cluster(v) for i,v in references.items()}
    windows, offset = [], 0
    for line in report['transcript'].splitlines():
        count = len(line.split())
        for first in range(offset, offset+count, 10):
            stop = min(first+10, offset+count)
            inside = [i for i in anchors if first <= i < stop]
            before = [i for i in anchors if i < first]
            after = [i for i in anchors if i >= stop]
            if not inside:
                start = anchors[max(before)]['end']-.2 if before else 0.
                end = anchors[min(after)]['start']+.2 if after else report['duration']
            else:
                a, b = min(inside), max(inside)
                start = anchors[a]['start']-.65-(a-first)*1.4
                if a-first >= 3 and before:
                    start = anchors[max(before)]['end']-.2
                end = anchors[b]['end']+.65+(stop-b-1)*1.4
                if before:
                    start = max(start, anchors[max(before)]['end']-.25)
                if after:
                    end = min(end, anchors[min(after)]['start']+.25)
            start, end = max(0., start), min(report['duration'], end)
            windows.append(dict(first=first, stop=stop, start=start, end=end,
                                usable=.6 <= end-start <= 30))
        offset += count
    return windows


def acoustic_end(row):
    return row.get('gap_fill', {}).get('end', row['end'])


def supported_proposals(report, references, candidates):
    """Accept only stable acoustic candidates close to two recognizer models."""
    supported = {}
    for i, options in candidates.items():
        ref = reference_cluster(references.get(i, []))
        if not ref or len(ref['models']) < 2:
            continue
        accepted = [dict(w, guidance_distance=abs(w['start']-ref['start'])+abs(w['end']-ref['end']))
                    for w in options if (w.get('score') or 0) >= .35
                    and abs(w['start']-ref['start']) <= .65 and abs(w['end']-ref['end']) <= .85]
        if accepted:
            supported[i] = accepted
    return supported


def attach_audit(report, tracks, references, candidates):
    supported = supported_proposals(report, references, candidates)
    counts = dict(supported=0, disagreement=0, uncertain=0)
    for i, row in enumerate(report['words']):
        options = supported.get(i, [])
        audit = dict(status='uncertain', start=row['start'], end=acoustic_end(row),
                     recognition=references.get(i, []), acoustic_candidates=len(candidates.get(i, [])))
        if options:
            closest = min(options, key=lambda w: abs(w['start']-row['start'])+abs(w['end']-acoustic_end(row)))
            start_delta = closest['start']-row['start']
            end_delta = closest['end']-acoustic_end(row)
            audit.update(status='supported' if abs(start_delta) <= .2 and abs(end_delta) <= .3 else 'disagreement',
                         start_delta_ms=round(start_delta*1000), end_delta_ms=round(end_delta*1000),
                         proposed=dict(start=closest['start'], end=closest['end'], score=closest['score']))
        audit['display_hold_seconds'] = round(row['end']-acoustic_end(row), 3)
        row['audit'] = audit
        counts[audit['status']] += 1
    report['full_audit'] = dict(version=1, total=len(report['words']), counts=counts,
                              recognition_tracks=[dict(model=t['model'], view=t['view']) for t in tracks],
                              start_tolerance=.2, end_tolerance=.3,
                              method='fresh_recognition_and_vocal_alignment', human_listened=False)
    return report


def refresh_audit(report):
    """Changing a time invalidates its audit; a display-only hold does not."""
    if not report.get('full_audit'):
        return report
    counts = dict(supported=0, disagreement=0, uncertain=0, stale=0)
    for row in report['words']:
        audit = row.get('audit')
        if audit and (audit['start'] != row['start'] or audit['end'] != acoustic_end(row)):
            audit['status'] = 'stale'
        counts[audit['status'] if audit else 'stale'] += 1
    report['full_audit']['counts'] = counts
    return report

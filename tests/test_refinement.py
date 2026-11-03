"""Regression coverage for song drift, automatic re-alignment and edit provenance."""
from copy import deepcopy
from unittest.mock import Mock
import sys
import types

import numpy as np

from autocaption.alignment import Transcript, new_report, media_digest, align_transcript
from autocaption.alignment_windows import alignment_windows, matching_pairs
from autocaption.automatic_timing import recover_gap_provenance
from autocaption import verification


def heard(text, start):
    return [dict(text=t, start=start+i, end=start+i+.6, probability=.95)
            for i, t in enumerate(text.split())]


def test_noisy_first_chorus_does_not_steal_the_clean_second_occurrence():
    source = 'פתיחה היום אנחנו יחד שמחים מאוד תמיד בית שני עולם יפה היום אנחנו יחד שמחים מאוד תמיד סוף'.split()
    recognized = 'פתיחה היום טעות יחד שמחים שגיאה תמיד בית שגוי עולם יפה היום אנחנו יחד שמחים מאוד תמיד סוף'.split()
    pairs = dict(matching_pairs(source, recognized))
    assert pairs[1] == 1 and pairs[3] == 3 and pairs[6] == 6
    assert pairs[11] == 11 and pairs[16] == 16


def test_repeated_choruses_keep_their_own_late_windows_and_missing_line_is_bounded():
    tokens = 'בית ראשון פזמון חוזר חסרה שורה בית שני פזמון חוזר'.split()
    guidance = heard('בית ראשון פזמון חוזר', 5) + heard('בית שני פזמון חוזר', 80)
    windows = alignment_windows(tokens, guidance, 110, [2,4,6,8,10])
    assert all(0 <= w['start'] < w['end'] <= 110 and w['end']-w['start'] <= 30 for w in windows)
    early = next(w for w in windows if w['first'] == 2)
    late = next(w for w in windows if w['first'] == 8)
    assert early['end'] < 40 < late['start']
    assert not any(w['first'] == 4 for w in windows)  # No unsupported 70-second search.
    assert alignment_windows(['ללא', 'קול'], [], 200) == []


def test_one_failed_window_cannot_discard_other_source_words(monkeypatch, tmp_path):
    video = tmp_path/'audio.wav'
    video.write_bytes(b'fixture')
    source = Transcript('שלום עולם\nשוב היום')
    fake = types.ModuleType('whisperx.alignment')
    fake.load_align_model = Mock(return_value=(object(), {'dictionary': dict.fromkeys('שלוםעבחיה', 1)}))
    def align(segments, *args, **kwargs):
        text = segments[0]['text']
        if text.startswith(('שלום', 'עולם')):
            return {'word_segments': []}  # Failed local alignment.
        return {'word_segments': [dict(word=t, start=40+i, end=40+i+.5, score=.9) for i,t in enumerate(text.split())]}
    fake.align = align
    monkeypatch.setitem(sys.modules, 'whisperx.alignment', fake)
    monkeypatch.setattr('autocaption.media.extract_audio', lambda _: np.zeros(1600000, dtype=np.float32).tobytes())
    monkeypatch.setattr('autocaption.media.probe_duration', lambda _: 100)
    result = align_transcript(video, source, 'cpu', guidance=heard('שלום עולם', 1)+heard('שוב היום', 40))
    assert [w['text'] for w in result['words']] == list(source.tokens)
    assert [w['index'] for w in result['words']] == [0,1,2,3]
    assert [w['status'] for w in result['words']] == ['unaligned','unaligned','aligned','aligned']


def test_recheck_changes_old_automatic_times_but_preserves_manual_edits(monkeypatch, tmp_path):
    video = tmp_path/'audio.wav'
    video.write_bytes(b'fixture')
    report = new_report(Transcript('שלום עולם היום'), 10, media_digest(video))
    for i, row in enumerate(report['words']):
        row.update(start=i*2, end=i*2+.5, status='aligned', score=.8)
    report['words'][1]['status'] = 'manual'
    manual = deepcopy(report['words'][1])
    fresh = deepcopy(report)
    for row in fresh['words']:
        row.update(start=row['start']+.2, end=row['end']+.2, status='aligned')
    fresh['alignment'] = dict(version=2, method='anchored_windows')
    evidence = dict(media_sha256=report['media_sha256'], words=heard('שלום עולם היום', .2))
    monkeypatch.setattr(verification, 'speech_evidence', lambda *a, **k: evidence)
    align = Mock(return_value=fresh)
    monkeypatch.setattr(verification, 'align_transcript', align)
    verification.verify_report(video, report, auto_timing=True)
    assert report['words'][0]['start'] == .2
    assert report['words'][2]['start'] == 4.2
    assert all(report['words'][1][k] == v for k,v in manual.items())
    assert align.call_args.kwargs['guidance'] == evidence['words']
    assert report['transcript'] == 'שלום עולם היום'


def test_large_acoustic_disagreement_gets_a_local_source_only_retry(monkeypatch, tmp_path):
    video = tmp_path/'audio.wav'
    video.write_bytes(b'fixture')
    fake = types.ModuleType('whisperx.alignment')
    fake.load_align_model = Mock(return_value=(object(), {'dictionary': dict.fromkeys('שלום', 1)}))
    fake.align = Mock(side_effect=[
        {'word_segments': [dict(word='שלום,', start=2.1, end=2.3, score=.8)]},
        {'word_segments': [dict(word='שלום,', start=3.1, end=3.9, score=.9)]}])
    monkeypatch.setitem(sys.modules, 'whisperx.alignment', fake)
    monkeypatch.setattr('autocaption.media.extract_audio', lambda _: np.zeros(96000, dtype=np.float32).tobytes())
    monkeypatch.setattr('autocaption.media.probe_duration', lambda _: 6)
    guidance = [dict(text='שלום', start=3, end=4, probability=.95)]
    result = align_transcript(video, Transcript('שלום,'), 'cpu', guidance=guidance)
    assert result['words'][0]['text'] == 'שלום,'
    assert result['words'][0]['start'] == 3.1
    assert result['words'][0]['timing_method'] == 'forced_alignment_local'
    assert fake.align.call_args.args[0] == [dict(start=2.5, end=4.5, text='שלום,')]


def test_local_retry_cannot_move_a_word_before_its_predecessor(monkeypatch, tmp_path):
    video = tmp_path/'audio.wav'
    video.write_bytes(b'fixture')
    source = Transcript('שלום עולם היום')
    fake = types.ModuleType('whisperx.alignment')
    fake.load_align_model = Mock(return_value=(object(), {'dictionary': dict.fromkeys('שלוםעהי', 1)}))
    phrase = {'word_segments': [dict(word=t, start=s, end=s+.5, score=.9)
                               for t,s in zip(source.tokens, (1,3,5))]}
    fake.align = Mock(side_effect=[phrase, {'word_segments': [dict(word='עולם', start=.7, end=1.1, score=.95)]}])
    monkeypatch.setitem(sys.modules, 'whisperx.alignment', fake)
    monkeypatch.setattr('autocaption.media.extract_audio', lambda _: np.zeros(128000, dtype=np.float32).tobytes())
    monkeypatch.setattr('autocaption.media.probe_duration', lambda _: 8)
    guidance = [dict(text=t,start=s,end=s+.5,probability=.95) for t,s in zip(source.tokens, (1,1.6,5))]
    result = align_transcript(video, source, 'cpu', guidance=guidance)
    assert [w['start'] for w in result['words']] == [1,3,5]
    assert result['alignment']['refinements'] == 0


def test_gap_fill_migration_requires_exact_backup_match_and_keeps_real_edits():
    before = new_report(Transcript('א ב ג ד'), 10, 'video')
    for i, row in enumerate(before['words']):
        row.update(start=i*2, end=i*2+1, status='aligned', score=.8)
    current = deepcopy(before)
    for row in current['words'][:3]:
        row.update(end=row['end']+1, status='manual')
    current['words'][1]['manual_timing'] = True
    current['words'][2]['start'] += .1
    original = deepcopy(current)
    recover_gap_provenance(current, before)
    assert current['words'][0]['status'] == 'aligned'
    assert current['words'][0]['end'] == 2
    assert current['words'][0]['gap_fill']['end'] == 1
    assert current['words'][1:] == original['words'][1:]


def test_speech_evidence_does_not_drop_singing_with_speech_vad(monkeypatch, tmp_path):
    video = tmp_path/'audio.wav'
    video.write_bytes(b'fixture')
    transcribe = Mock(return_value=[])
    monkeypatch.setattr(verification, 'transcribe', transcribe)
    verification.speech_evidence(video)
    assert transcribe.call_args.kwargs['vad_filter'] is False

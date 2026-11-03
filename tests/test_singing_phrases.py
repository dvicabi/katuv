from copy import deepcopy
import json
from types import SimpleNamespace
import pytest

from autocaption.alignment import Transcript, new_report, timing_problems
from autocaption.singing_alignment import collect_measurements, finish_recovery
from autocaption.singing_phrases import (phonetic_tokens, pronunciation_text, phrase_windows,
    phrase_cache_path, apply_phrases, refine_phrases, hebrew_spellings, PHRASE_VERSION)


def report_and_records():
    report = new_report(Transcript('שלום עולם'), 20, 'media')
    for i, row in enumerate(report['words']):
        row.update(start=i+1., end=i+1.5, status='estimated', score=.3, audit=dict(status='uncertain'))
    records = [dict(family='ctc', model=name, view='vocals', window=dict(target=1), words=[
        dict(index=i, text=row['text'], start=i+10., end=i+10.5, score=.9)
        for i, row in enumerate(report['words'])]) for name in ('first', 'second')]
    bundle = dict(version=PHRASE_VERSION, media_sha256='media', transcript_sha256=report['transcript_sha256'],
                  targets=[1], records=records, errors=[])
    return report, bundle


def test_cached_phrase_retry_replays_audio_measurements_without_loading_models(tmp_path):
    report, bundle = report_and_records()
    phrase_cache_path(tmp_path, report).write_text(json.dumps(bundle), encoding='utf8')
    refine_phrases('missing.mp4', report, [], tmp_path)
    assert report['words'][1]['start'] == 11
    assert report['words'][0]['start'] == 1
    assert report['phrase_recovery']['measured'] == [1]
    assert report['transcript'] == 'שלום עולם'
    assert report['words'][0]['audit']['status'] == 'uncertain'


@pytest.mark.parametrize('field', ['version', 'media_sha256', 'transcript_sha256'])
def test_foreign_phrase_cache_is_rejected_atomically(field):
    report, bundle = report_and_records()
    before = deepcopy(report)
    bundle[field] = 'other'
    with pytest.raises(ValueError, match='different source'):
        apply_phrases(report, [], bundle)
    assert report == before


def test_phonetic_analysis_preserves_letters_punctuation_and_source_rows():
    rows = new_report(Transcript('שלום, ה׳'), 10, 'm')['words']
    before = deepcopy(rows)
    assert pronunciation_text(rows) == ['שלום,', 'השם']
    model = SimpleNamespace(add_diacritics=lambda _: 'שָׁלוֹם, הַ|שֵּׁם')
    measured = phonetic_tokens(rows, model, lambda p: 'ʃalˈom' if p.startswith('ש') else 'haʃˈem')
    assert [w['analysis'] for w in measured] == ['shalom', 'hashem']
    assert [w['text'] for w in measured] == ['שלום,', 'ה׳']
    assert rows == before


@pytest.mark.parametrize('result', ['שלום חדש', 'שלום', 'שלום עולם חדש'])
def test_diacritizer_cannot_substitute_omit_or_add_words(result):
    report, _ = report_and_records()
    model = SimpleNamespace(add_diacritics=lambda _: result)
    with pytest.raises(ValueError, match='source letters or word boundaries'):
        phonetic_tokens(report['words'], model, lambda _: 'word')


def test_phrase_windows_cross_neither_instrumental_break_nor_manual_anchor():
    report = new_report(Transcript('שלום עולם עכשיו שרים שוב'), 100, 'm')
    measured = {i: [dict(family='ctc', model=model, start=start, end=start+.8, score=.9)
        for model in ('a', 'b')] for i, start in enumerate((10., 11., 80., 81., 82.))}
    for row in report['words']:
        row.update(start=0., end=.1, status='manual')
    windows = phrase_windows(report, measured, [1, 2])
    assert windows[0]['stop'] == 2 and windows[0]['end'] == 13
    assert windows[1]['first'] == 2 and windows[1]['start'] == 78.8
    assert all(w['end']-w['start'] <= 29 for w in windows)


def test_joint_search_charges_for_cutting_a_supported_previous_word():
    report = new_report(Transcript('שלום עולם'), 10, 'm')
    for i, row in enumerate(report['words']):
        row.update(start=i+1., end=i+1.5, status='estimated', score=.3)
    measured = {0: [dict(index=0, text='שלום', family='ctc', model=name, view='vocals',
        start=1., end=3., score=.9) for name in ('a', 'b')], 1: [
        dict(index=1, text='עולם', family='ctc', model='a', view='vocals', start=2.6, end=4., score=.9),
        dict(index=1, text='עולם', family='ctc', model='b', view='vocals', start=3.05, end=4., score=.8)]}
    finish_recovery(report, measured, [])
    assert report['words'][0]['end'] == 3
    assert report['words'][1]['start'] == 3.05
    assert not timing_problems(report)


def test_retiming_invalidates_old_interval_mask_test():
    report, bundle = report_and_records()
    report['words'][1]['audio_control'] = dict(tested_start=2., tested_end=2.5, matches_current_timing=True)
    apply_phrases(report, [], bundle)
    assert report['words'][1]['audio_control']['matches_current_timing'] is False


def test_added_pronunciation_views_do_not_become_an_independent_model():
    report, bundle = report_and_records()
    for record in bundle['records']:
        record['model'] = 'one-acoustic-checkpoint'
    apply_phrases(report, [], bundle)
    assert report['words'][1]['audit']['status'] == 'uncertain'


def test_full_hebrew_spelling_retains_vowel_letters_in_analysis():
    rows = new_report(Transcript('הַכּוֹל, והכול ה׳'), 10, 'm')['words']
    before = deepcopy(rows)
    assert hebrew_spellings(rows) == {0: 'הכול', 1: 'והכול', 2: 'השם'}
    assert rows == before


def test_attention_pronunciation_never_replaces_displayed_tokens():
    import numpy as np
    from autocaption.singing_alignment import attention_measurements
    class Tokenizer:
        sot_sequence = []
        def encode(self, text):
            return [ord(c) for c in text]
        def decode(self, tokens):
            return ''.join(chr(c) for c in tokens)
    def align(encoded, sequence, batches, frames):
        tokens = batches[0]
        return [SimpleNamespace(alignments=[(i, i+5) for i in range(len(tokens)+1)],
                                text_token_probs=[.9]*len(tokens))]
    model = SimpleNamespace(feature_extractor=lambda _: np.zeros((80, 100)), encode=lambda _: None,
                            tokens_per_second=50, model=SimpleNamespace(align=align))
    rows = new_report(Transcript('ה׳ אחד'), 10, 'm')['words']
    before = deepcopy(rows)
    measured = attention_measurements(model, Tokenizer(), np.zeros(32000), rows, 0, 2, {0:'השם'})
    assert [w['text'] for w in measured] == ['ה׳', 'אחד']
    assert measured[0]['analysis'] == 'השם'
    assert rows == before


def test_strong_middle_phonemes_do_not_certify_weak_word_onsets():
    from autocaption.singing_alignment import boundary_support
    report, bundle = report_and_records()
    options = collect_measurements(report, bundle['records'])[1]
    # Whole-word evidence previously looked strong for both models.
    assert boundary_support(options, 11., 'start')[0]
    detailed = [dict(w, start_score=.001, end_score=.9) for w in options]
    assert not boundary_support(options+detailed, 11., 'start')[0]
    assert boundary_support(options+detailed, 11.5, 'end')[0]


def test_measured_edge_probabilities_audit_onsets_separately_from_word_average():
    from autocaption.singing_alignment import boundary_support
    report, bundle = report_and_records()
    old = collect_measurements(report, bundle['records'])[1]
    detailed = [dict(w, start=12., end=12.5, start_score=.9, end_score=.9) for w in old]
    assert boundary_support(old+detailed, 12., 'start')[0]
    assert not boundary_support(old+detailed, 11., 'start')[0]

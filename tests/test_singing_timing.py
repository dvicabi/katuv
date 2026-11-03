from copy import deepcopy
from unittest.mock import Mock

from autocaption.alignment import Transcript, new_report, timing_problems
from autocaption.singing_timing import stable_candidates, apply_candidates, repair_estimates, retry_spans


def row(i, start, end, score=.8):
    return dict(index=i, text=['שלום,', 'עולם', 'היום', 'מחר'][i], start=start, end=end,
                score=score, status='aligned', reason='')


def report():
    result = new_report(Transcript('שלום, עולם היום מחר'), 8, 'video')
    result['words'] = [row(i, i, i+.8) for i in range(4)]
    result['words'][0]['status'] = 'estimated'
    result['words'][1]['status'] = 'manual'
    result['words'][3]['status'] = 'manual'
    return result


def test_one_crop_or_unstable_acoustics_cannot_be_promoted():
    a = row(0, .3, .6)
    assert stable_candidates([[a]], 8) == {}
    assert stable_candidates([[a], [row(0, 1.3, 1.6)]], 8) == {}
    assert stable_candidates([[a], [row(0, .31, .62, .1)]], 8) == {}
    chosen = stable_candidates([[a], [row(0, .31, .62, .9)]], 8)[0]
    assert chosen['start'] == .31 and chosen['text'] == 'שלום,'
    assert chosen['timing_method'] == 'vocal_consensus'
    assert chosen['acoustic_retry']['supporting_crops'] == 2


def test_only_conflicting_manual_and_automatic_neighbours_are_repaired():
    data = report()
    before = deepcopy(data)
    proposals = {0: row(0, .5, 1.2), 1: row(1, 1.25, 2.1), 2: row(2, 2.12, 2.8), 3: row(3, 3.5, 3.9)}
    assert apply_candidates(data, proposals) == []
    assert data == before  # Default protects manual anchors.
    assert apply_candidates(data, proposals, True) == [0, 1, 2]
    assert data['words'][3] == before['words'][3]  # Unrelated manual edit.
    assert data['words'][1]['replaced_manual_timing'] == dict(start=1, end=1.8)
    assert data['words'][0]['before_acoustic_retry'] == before['words'][0]
    assert not timing_problems(data)


def test_failed_neighbour_is_not_squeezed_or_relabelled():
    data = report()
    before = deepcopy(data)
    assert apply_candidates(data, {0: row(0, .5, 1.2)}, True) == []
    assert data == before


def test_consistent_phrase_beats_conflicting_highest_scoring_individual_words():
    data = report()
    data['words'][1]['status'] = 'estimated'
    # Per-word maxima overlap. A valid alternative resolves both words.
    proposals = {0: [row(0, .3, 1.4, .99), row(0, .3, .9, .75)],
                 1: [row(1, 1., 1.7, .9)]}
    assert apply_candidates(data, proposals) == [0, 1]
    assert data['words'][0]['end'] == .9
    assert not timing_problems(data)


def test_small_cross_crop_overlap_is_recorded_without_losing_an_onset():
    data = report()
    data['words'][1]['status'] = 'estimated'
    apply_candidates(data, {0: row(0, .3, 1.04), 1: row(1, 1., 1.7)})
    assert data['words'][0]['start'] == .3 and data['words'][1]['start'] == 1.
    assert data['words'][0]['end'] == 1.
    assert data['words'][0]['boundary_adjustment']['acoustic_end'] == 1.04
    assert not timing_problems(data)


def test_later_general_recheck_keeps_successful_vocal_alignment():
    from autocaption.verification import apply_refinement
    data = report()
    anchor = data['words'][0]
    anchor.update(status='aligned', timing_method='vocal_consensus', acoustic_retry=dict(supporting_crops=3))
    fresh = deepcopy(data)
    fresh['words'][0].update(start=.2, end=.9)
    apply_refinement(data, fresh, dict(media_sha256='video', words=[]))
    assert data['words'][0]['start'] == 0
    assert data['words'][0]['end'] == .8


def test_long_transcripts_retry_bounded_source_blocks():
    data = new_report(Transcript(' '.join(['שלום,'] * 70)), 100, 'video')
    for w in data['words']:
        w['status'] = 'estimated'
    spans = retry_spans(data)
    assert spans[0][0] == 0 and spans[-1][1] == 70
    assert all(b-a <= 12 for a, b in spans)


def test_failed_model_retains_estimates_and_exact_transcript(monkeypatch, tmp_path):
    data = report()
    before = deepcopy(data)
    monkeypatch.setattr('autocaption.singing_timing.VocalAligner', Mock(side_effect=RuntimeError('offline download')))
    repair_estimates(tmp_path/'video.mp4', data, dict(words=[]), resolve_manual_conflicts=True)
    assert data['words'] == before['words']
    assert data['transcript'] == before['transcript']
    assert data['singing_repair']['remaining'] == 1
    assert data['singing_repair']['errors'] == ['offline download']


def test_automatic_retry_is_lazy_and_preserves_explicit_manual_reordering(monkeypatch, tmp_path):
    factory = Mock(side_effect=AssertionError('Model must not load'))
    monkeypatch.setattr('autocaption.singing_timing.VocalAligner', factory)
    data = report()
    data['allow_reordered_timings'] = True
    assert repair_estimates(tmp_path/'video.mp4', data, dict(words=[])) == data
    data.pop('allow_reordered_timings')
    data['words'][0]['status'] = 'aligned'
    assert repair_estimates(tmp_path/'video.mp4', data, dict(words=[])) == data
    factory.assert_not_called()

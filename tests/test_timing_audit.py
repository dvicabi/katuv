from copy import deepcopy

import pytest

from autocaption.alignment import Transcript, new_report, timing_problems
from autocaption.timing_audit import speech_references, reference_cluster, source_windows, attach_audit, refresh_audit
from autocaption.singing_timing import apply_candidates


def fixture():
    r=new_report(Transcript('שלום עולם'),20,'video')
    for i,w in enumerate(r['words']):w.update(start=i+1.,end=i+1.5,status='aligned',score=.9)
    tracks=[dict(model=m,view='original',words=[dict(text=w['text'],start=w['start'],end=w['end'],probability=.95)
                                             for w in r['words']]) for m in ('large','turbo')]
    candidates={i:[dict(w)] for i,w in enumerate(r['words'])}
    return r,tracks,candidates


def test_aligned_label_does_not_count_as_verification():
    r,tracks,candidates=fixture()
    tracks[0]['words'][0].update(start=8,end=8.5)
    tracks[1]['words'][0].update(start=8.03,end=8.53)
    candidates[0][0].update(start=8.02,end=8.52)
    attach_audit(r,tracks,speech_references(r,tracks),candidates)
    assert r['words'][0]['status']=='aligned'
    assert r['words'][0]['audit']['status']=='disagreement'
    assert r['words'][0]['audit']['start_delta_ms']==7020
    assert r['full_audit']['total']==2 and not r['full_audit']['human_listened']


def test_two_audio_views_of_one_model_are_not_two_recognizers():
    r,tracks,candidates=fixture()
    tracks[1].update(model='large',view='vocals')
    attach_audit(r,tracks,speech_references(r,tracks),candidates)
    assert r['full_audit']['counts']['uncertain']==2


def test_all_source_words_are_audited_even_if_recognizers_omit_one():
    r,tracks,candidates=fixture()
    for t in tracks:t['words'].pop()
    attach_audit(r,tracks,speech_references(r,tracks),candidates)
    assert [w['text'] for w in r['words']]==['שלום','עולם']
    assert r['words'][1]['audit']['status']=='uncertain'


def test_actual_boundary_change_invalidates_audit_but_display_hold_does_not():
    r,tracks,candidates=fixture()
    attach_audit(r,tracks,speech_references(r,tracks),candidates)
    r['words'][0].update(gap_fill=dict(end=1.5),end=2.)
    refresh_audit(r)
    assert r['words'][0]['audit']['status']=='supported'
    r['words'][0]['start']=1.2
    refresh_audit(r)
    assert r['words'][0]['audit']['status']=='stale'


def test_bounds_use_new_audio_evidence_instead_of_wrong_old_alignment():
    r,tracks,_=fixture()
    for t in tracks:
        for w in t['words']:w['start']+=10;w['end']+=10
    windows=source_windows(r,speech_references(r,tracks))
    assert windows[0]['start']>10 and windows[0]['stop']==2


def test_transitive_matches_cannot_create_a_false_consensus():
    obs=[dict(start=s,end=s+.3,probability=.9,model=str(i),view='original') for i,s in enumerate([1,1.3,1.6])]
    cluster=reference_cluster(obs)
    assert len(cluster['models'])==2


def test_audit_can_correct_previously_aligned_words_without_rewriting_source():
    r,_,_=fixture()
    before=deepcopy(r)
    proposal=dict(r['words'][0],start=1.2,end=1.7)
    assert apply_candidates(r,{0:[proposal]},targets={0})==[0]
    assert r['words'][0]['start']==1.2 and r['transcript']==before['transcript']
    assert r['words'][1]==before['words'][1] and not timing_problems(r)
    proposal['text']='מילה אחרת'
    with pytest.raises(ValueError,match='source tokens'):
        apply_candidates(r,{0:[proposal]},targets={0})

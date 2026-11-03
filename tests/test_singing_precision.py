from copy import deepcopy
import json
import numpy as np
import pytest
from autocaption.alignment import Transcript,new_report
from autocaption.singing_alignment import collect_measurements,boundary_candidates
from autocaption.singing_precision import (precision_windows,pronunciation_options,apply_precision,
    precision_cache_path,refine_uncertain_singing,audio_control)


def data():
    report=new_report(Transcript('שלום עולם'),20,'media')
    for i,w in enumerate(report['words']):w.update(start=i+1.,end=i+1.5,status='estimated',score=.3,audit=dict(status='uncertain'))
    records=[dict(family='ctc',model=model,view='vocals',words=[dict(index=i,text=w['text'],start=i+10.,end=i+10.5,score=.9)
        for i,w in enumerate(report['words'])]) for model in ('first','second')]
    bundle=dict(version=1,media_sha256='media',transcript_sha256=report['transcript_sha256'],targets=[0,1],records=records,errors=[])
    return report,bundle


def test_precision_cache_replays_measurements_without_model_or_video_access(tmp_path):
    report,bundle=data();original=deepcopy(report)
    precision_cache_path(tmp_path,report).write_text(json.dumps(bundle),encoding='utf8')
    refine_uncertain_singing('does-not-exist.mp4',report,[],tmp_path)
    assert [w['start'] for w in report['words']]==[10,11]
    assert report['transcript']==original['transcript']
    assert report['precision_recovery']['changed']==[0,1]


def test_cached_mms_measurements_require_explicit_opt_in(tmp_path, monkeypatch):
    from autocaption.singing_precision import MULTILINGUAL_MODEL
    report, bundle = data()
    for record in bundle['records']:
        record['model'] = MULTILINGUAL_MODEL
    path = precision_cache_path(tmp_path, report)
    path.write_text(json.dumps(bundle), encoding='utf-8')
    path.with_name(path.stem + '-noncommercial.json').write_text(json.dumps(bundle), encoding='utf-8')
    monkeypatch.setattr('autocaption.singing_phrases.refine_phrases', lambda video, report, *a: report)
    normal = deepcopy(report)
    refine_uncertain_singing('missing.mp4', normal, [], tmp_path)
    assert normal['precision_recovery']['measured'] == []
    assert [w['start'] for w in normal['words']] == [1., 2.]
    refine_uncertain_singing('missing.mp4', report, [], tmp_path, include_noncommercial=True)
    assert report['precision_recovery']['measured'] == [0, 1]


@pytest.mark.parametrize('field',['media_sha256','transcript_sha256','version'])
def test_foreign_precision_cache_is_rejected_atomically(field):
    report,bundle=data();before=deepcopy(report);bundle[field]='different'
    with pytest.raises(ValueError,match='different source'):apply_precision(report,[],bundle)
    assert report==before


def test_context_rows_are_not_counted_as_local_word_measurements():
    report,bundle=data()
    for record in bundle['records']:record['window']=dict(target=1)
    assert set(collect_measurements(report,bundle['records']))=={1}


def test_local_windows_ignore_wrong_saved_or_manual_times():
    report,bundle=data();report['words'][0]['start']=0
    measured=collect_measurements(report,bundle['records'])
    windows=precision_windows(report,measured,[0])
    assert windows[0]['start']==9.2 and windows[0]['end']==12.3
    assert precision_windows(report,{},[0])==[]


def test_abbreviation_is_analysis_only_and_a_single_letter_is_not_expanded():
    report=new_report(Transcript('ה׳ אחד ה'),10,'m')
    assert pronunciation_options(report['words'])==[{0:'השם'},{0:'אדוני'}]
    row=dict(index=0,text='ה׳',start=1.,end=1.6,score=.8,analysis='השם')
    assert collect_measurements(report,[dict(family='ctc',model='he',view='vocals',words=[row])])[0][0]['text']=='ה׳'
    assert [w['text'] for w in report['words']]==['ה׳','אחד','ה']
    assert pronunciation_options(report['words'][2:])==[{}]


def test_combined_boundaries_are_observed_and_cannot_join_different_occurrences():
    options=[dict(index=0,text='שלום',family='ctc',model=m,view='vocals',start=a,end=b,score=.9)
             for m,a,b in [('a',1.,2.4),('b',1.05,2.),('c',1.5,2.05)]]
    combined=boundary_candidates(options)
    assert any(w['start']==1. and w['end']==2. for w in combined)
    assert all(w['start'] in {x['start'] for x in options} and w['end'] in {x['end'] for x in options} for w in combined)
    far=[dict(w,start=w['start']+10,end=w['end']+10,model=w['model']+'far') for w in options]
    assert not any(w['start']<3 and w['end']>10 for w in boundary_candidates(options+far))


def test_audio_control_masks_a_copy_and_records_actual_model_response(monkeypatch):
    from autocaption import singing_precision as module
    report,_=data();source=np.ones(20*16000,dtype=np.float32);seen=[]
    def measure(model,tokenizer,aligner,romanizer,audio,rows,start,end,pronunciation):
        seen.append(audio.copy())
        return [dict(index=w['index'],text=w['text'],start=.6,end=1.1,score=.2 if np.any(audio==0) else .9) for w in rows]
    monkeypatch.setattr(module,'multilingual_measurements',measure)
    record,check=audio_control(None,None,None,None,source,report,0)
    assert np.all(source==1) and np.all(seen[0]==1)
    assert (seen[1]==0).sum()==8000
    assert check['score_drop']==pytest.approx(.7) and not check['human_listened']
    assert record['words'][0]['text']=='שלום'


def test_audio_control_does_not_certify_new_times_after_retiming():
    report,bundle=data();bundle['audio_controls']=[dict(index=0,text='שלום',tested_start=1.,tested_end=1.5,score_drop=.8)]
    apply_precision(report,[],bundle)
    assert not report['words'][0]['audio_control']['matches_current_timing']


def test_foreign_audio_control_is_rejected_without_partial_updates():
    report,bundle=data();before=deepcopy(report)
    bundle['audio_controls']=[dict(index=0,text='different',tested_start=1.,tested_end=1.5)]
    with pytest.raises(ValueError,match='word identity'):apply_precision(report,[],bundle)
    assert report==before


def test_manual_timings_remain_locked_unless_existing_opt_in_is_set():
    report,bundle=data();report['words'][0]['status']='manual'
    locked=deepcopy(report);apply_precision(locked,[],bundle)
    assert locked['words'][0]['start']==1.
    apply_precision(report,[],bundle,True)
    assert report['words'][0]['start']==10. and report['words'][0]['replaced_manual_timing']['start']==1.

from copy import deepcopy
import json
from unittest.mock import Mock

import numpy as np
import pytest

from autocaption.alignment import Transcript,new_report,timing_problems
from autocaption.singing_alignment import (NormalizedCTC,analysis_token,collect_measurements,
    boundary_support,recover_path,audit_recovery,finish_recovery,recovery_cache_path,recover_singing,
    attention_measurements,tempo_measurements)


def fixture():
    report=new_report(Transcript('שלום, עולם'),20,'media')
    for i,row in enumerate(report['words']):row.update(start=i+1.,end=i+1.5,status='aligned',score=.8)
    records=[dict(family=family,model=model,view='vocals',words=[dict(index=i,text=w['text'],
            start=10+i,end=10.6+i,score=.9) for i,w in enumerate(report['words'])])
             for family,model in [('ctc','ctc-hebrew'),('attention','whisper-hebrew')]]
    return report,records


def test_normalization_respects_model_input_contract_and_silence():
    import torch
    seen=[]
    model=NormalizedCTC(lambda wave:seen.append(wave) or wave)
    model(torch.tensor([[.1,.2,.3],[0.,0.,0.]]))
    assert torch.allclose(seen[0][0].mean(),torch.tensor(0.),atol=1e-6)
    assert seen[0][0].var(unbiased=False).item()==pytest.approx(1.,abs=1e-4)
    assert torch.isfinite(seen[0]).all() and seen[0][1].sum()==0


def test_punctuation_is_analysis_only_and_no_latin_letters_or_digits_are_discarded():
    assert analysis_token('שָׁלוֹם,')=='שלום'
    assert analysis_token('AI2026!')=='ai2026'


def test_same_checkpoint_many_crops_is_not_independent_verification():
    report,records=fixture()
    options=collect_measurements(report,[records[0]]*8)[0]
    assert boundary_support(options,10,'start')==(False,[])


def test_multiple_attention_models_alone_do_not_fabricate_acoustic_support():
    report,records=fixture()
    records=[records[1],dict(records[1],model='another-whisper')]
    assert not boundary_support(collect_measurements(report,records)[0],10,'start')[0]


def test_one_tempo_outlier_cannot_verify_a_word_across_an_instrumental_break():
    report=new_report(Transcript('שלום'),100,'media')
    report['words'][0].update(start=86.,end=87.,status='estimated',score=.8)
    def observation(family,model,view,start,end,score):
        return dict(index=0,text='שלום',family=family,model=model,view=view,
                    start=start,end=end,score=score)
    options=[observation('ctc',model,view,86.3,86.9,.65)
             for model in ('hebrew-a','hebrew-b') for view in ('original','vocals')]
    options += [observation('ctc','hebrew-a','vocals-tempo-1.35',73.7,87.,.3)]
    options += [observation('attention',model,'vocals',73.6,87.,.9)
                for model in ('whisper-a','whisper-b')]
    assert not boundary_support(options,73.6,'start')[0]
    finish_recovery(report,{0:options},[])
    assert report['words'][0]['start']==86.3
    assert report['words'][0]['audit']['status']=='supported'


def test_clipped_zero_length_and_nan_measurements_are_rejected():
    report,records=fixture()
    records[0]['words'][0]['edge']=True
    records[0]['words'][1]['end']=records[0]['words'][1]['start']
    records[1]['words'][0]['score']=float('nan')
    assert set(collect_measurements(report,records))=={1}


def test_one_letter_abbreviation_does_not_become_a_twenty_ms_spoken_word():
    report=new_report(Transcript('ה׳'),10,'media')
    row=dict(index=0,text='ה׳',start=1,end=1.15,score=.99)
    assert not collect_measurements(report,[dict(family='ctc',model='he',view='vocals',words=[row])])


def test_foreign_or_reordered_tokens_cannot_enter_measurements():
    report,records=fixture()
    records[0]['words'][0]['text']='invented'
    with pytest.raises(ValueError,match='source words'):collect_measurements(report,records)


def test_joint_recovery_releases_bad_automatic_anchors_and_keeps_exact_source():
    report,records=fixture();before=deepcopy(report)
    measurements=collect_measurements(report,records)
    assert recover_path(report,measurements)==[0,1]
    assert [w['start'] for w in report['words']]==[10,11]
    assert [w['text'] for w in report['words']]==['שלום,','עולם']
    assert report['transcript']==before['transcript'] and not timing_problems(report)
    audit_recovery(report,measurements)
    assert report['full_audit']['counts']['supported']==2


def test_manual_boundary_requires_persistent_opt_in_and_retains_backup():
    report,records=fixture();report['words'][0]['status']='manual'
    measurements=collect_measurements(report,records)
    locked=deepcopy(report)
    recover_path(locked,measurements)
    assert locked['words'][0]['start']==1 and locked['words'][0]['end']==1.5
    recover_path(report,measurements,resolve_manual_conflicts=True)
    assert report['words'][0]['replaced_manual_timing']==dict(start=1.,end=1.5)
    assert report['words'][0]['before_singing_recovery']['start']==1


def test_failed_measurement_keeps_word_and_does_not_verify_it():
    report,records=fixture()
    for rec in records:rec['words'].pop()
    measurements=collect_measurements(report,records)
    # The unresolved following word blocks an incompatible move. Both survive.
    finish_recovery(report,measurements,[])
    assert len(report['words'])==2 and not timing_problems(report)
    assert report['words'][1]['audit']['status']=='uncertain'


def test_display_hold_does_not_count_as_acoustic_duration():
    report,records=fixture()
    report['words'][0].update(gap_fill=dict(end=1.5),end=2.)
    finish_recovery(report,collect_measurements(report,records),[])
    assert report['words'][0]['end']==11 and report['words'][0]['gap_fill']['end']==10.6
    assert report['words'][0]['audit']['end']==10.6


def test_cache_is_bound_to_media_text_and_selected_model(tmp_path):
    report,records=fixture()
    path=recovery_cache_path(tmp_path,report,'model')
    path.write_text(json.dumps(dict(version=1,media_sha256='media',transcript_sha256=report['transcript_sha256'],records=records)),encoding='utf8')
    recover_singing('unused.mp4',report,dict(media_sha256='media'),tmp_path,'model')
    assert report['full_audit']['counts']['supported']==2
    assert recovery_cache_path(tmp_path,dict(report,media_sha256='other'),'model')!=path
    assert recovery_cache_path(tmp_path,report,'other-model')!=path
    with pytest.raises(ValueError,match='different video'):
        recover_singing('unused.mp4',report,dict(media_sha256='other'),tmp_path,'model')


def test_tempo_retry_converts_times_back_to_original_video(monkeypatch):
    import autocaption.singing_alignment as module
    row=dict(index=0,text='שלום',start=.4,end=.8,score=.9)
    monkeypatch.setattr(module,'ctc_measurements',lambda *a,**k:[dict(row)])
    monkeypatch.setattr('subprocess.run',Mock(return_value=Mock(stdout=np.zeros(16000,dtype=np.float32).tobytes())))
    monkeypatch.setattr('autocaption.media.find_tool',lambda _: 'ffmpeg')
    measured=tempo_measurements(None,None,np.zeros(16000*12,dtype=np.float32),[row],10,11,factor=1.35)
    assert measured[0]['start']==10.54 and measured[0]['end']==11.08
    assert measured[0]['text']=='שלום'


def test_automatic_recheck_uses_recovery_instead_of_overwriting_audited_words(monkeypatch,tmp_path):
    from autocaption import verification
    report,_=fixture();report['full_audit']=dict(version=1)
    evidence=dict(media_sha256='media',words=[])
    monkeypatch.setattr(verification,'speech_evidence',lambda *a,**k:evidence)
    recover=Mock(return_value=report)
    monkeypatch.setattr('autocaption.singing_alignment.recover_singing',recover)
    verification.verify_report(tmp_path/'video.mp4',report,tmp_path/'evidence.json',auto_timing=True,resolve_manual_conflicts=True)
    assert recover.call_args.kwargs['resolve_manual_conflicts'] is True

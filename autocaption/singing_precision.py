"""Additional acoustic measurements for uncertain sung words; display text is immutable."""
from statistics import median

PRECISION_VERSION=1
MULTILINGUAL_MODEL='facebook/mms-fa'


def permitted_bundle(bundle, include_noncommercial):
    """Cached restricted-model evidence is opt-in too, not just model downloads."""
    if include_noncommercial:
        return bundle
    return dict(bundle, records=[r for r in bundle['records'] if r.get('model') != MULTILINGUAL_MODEL],
                audio_controls=[])


def precision_windows(report, measurements, targets):
    """Small overlapping phrases anchored in model measurements, not saved user edits."""
    anchors={}
    for i,options in measurements.items():
        models={}
        for w in options:
            if w['score'] >= (.2 if w['family']=='ctc' else .5):
                models.setdefault(w['model'],[]).append(w)
        if models:
            anchors[i]={edge:median(median(w[edge] for w in peers) for peers in models.values())
                        for edge in ('start','end')}
    windows=[]
    for i in targets:
        first,stop=max(0,i-1),min(len(report['words']),i+2)
        if first not in anchors or stop-1 not in anchors:
            continue  # Keep unresolved words; do not invent a crop from manual times.
        start=max(0.,anchors[first]['start']-.8)
        end=min(report['duration'],anchors[stop-1]['end']+.8)
        if .6<end-start<=29:
            windows.append(dict(first=first,stop=stop,start=round(start,3),end=round(end,3),target=i))
    return windows


def pronunciation_options(rows):
    """Conventional readings of a Hebrew abbreviation, for audio analysis only."""
    abbreviated=[w for w in rows if w['text'] in ('ה׳',"ה'")]
    if not abbreviated:
        return [{}]
    # Do not expand an arbitrary single letter or substitute recognizer output.
    return [{w['index']:reading for w in abbreviated} for reading in ('השם','אדוני')]


def multilingual_measurements(model, tokenizer, aligner, romanizer, audio, rows, start, end,
                             pronunciation=None, romanized=None):
    import re
    import numpy as np
    import torch
    normalized=[]
    if romanized is not None and len(romanized) != len(rows):
        raise ValueError('Phonetic analysis must preserve source word count')
    for index, row in enumerate(rows):
        text=(pronunciation or {}).get(row['index'],row['text'])
        word=(romanized[index] if romanized is not None else
              re.sub('[^a-z]', '',romanizer.romanize_string(text,lcode='heb').lower()))
        # The multilingual alphabet cannot time digits or empty punctuation.
        # Fail this crop explicitly, leaving the original rows untouched.
        if not word or re.fullmatch('[a-z]+', word) is None or any(c.isdigit() for c in text):
            raise ValueError('Source word cannot be represented by the multilingual aligner')
        normalized.append(word)
    clip=torch.from_numpy(np.asarray(audio[round(start*16000):round(end*16000)]).copy()).unsqueeze(0)
    end=start+clip.shape[1]/16000
    with torch.inference_mode():
        emissions,_=model(clip)
        spans=aligner(emissions[0],tokenizer(normalized))
    if len(spans)!=len(rows) or any(not chars for chars in spans):
        raise ValueError('Incomplete multilingual alignment; source rows retained')
    ratio=clip.shape[1]/16000/emissions.shape[1]
    measured=[]
    for row,chars,analysis in zip(rows,spans,normalized):
        lo,hi=start+chars[0].start*ratio,start+chars[-1].end*ratio
        measured.append(dict(index=row['index'],text=row['text'],analysis=analysis,
            pronunciation=(pronunciation or {}).get(row['index']),start=round(lo,3),end=round(hi,3),
            score=sum(c.score*len(c) for c in chars)/sum(len(c) for c in chars),
            start_score=chars[0].score,end_score=chars[-1].score,
            edge=lo<=start+.04 or hi>=end-.04))
    return measured


def precision_cache_path(cache_dir,report):
    import hashlib
    from pathlib import Path
    key=hashlib.sha256(f'{PRECISION_VERSION}:{report["media_sha256"]}:{report["transcript_sha256"]}'.encode()).hexdigest()
    return Path(cache_dir)/f'precision-{key}.json'


def audio_control(model,tokenizer,aligner,romanizer,audio,report,index):
    """Measure dependence on the selected sound, without editing the source audio.

    A probability drop is diagnostic evidence, not an accuracy certificate. Low
    confidence or unchanged probability stays visible in the report.
    """
    from .timing_audit import acoustic_end
    first,stop=max(0,index-2),min(len(report['words']),index+3)
    rows=report['words'][first:stop];word=report['words'][index]
    start=max(0.,rows[0]['start']-.6);end=min(report['duration'],acoustic_end(rows[-1])+.6)
    if not .6<end-start<=29:raise ValueError('Audio control needs a bounded phrase')
    original=audio[round(start*16000):round(end*16000)].copy();muted=original.copy()
    lo,hi=word['start'],acoustic_end(word)
    muted[max(0,round((lo-start)*16000)):min(len(muted),round((hi-start)*16000))]=0
    pronunciation=pronunciation_options(rows)[0]
    values=[]
    for sound in (original,muted):
        measured=multilingual_measurements(model,tokenizer,aligner,romanizer,sound,rows,0,len(sound)/16000,pronunciation)
        found=next(w for w in measured if w['index']==index)
        values.append(dict(found,start=round(start+found['start'],3),end=round(start+found['end'],3)))
    a,b=values
    record=dict(family='ctc',model=MULTILINGUAL_MODEL,view='vocals-control-baseline',
                window=dict(first=first,stop=stop,start=start,end=end,target=index),words=[a])
    check=dict(index=index,text=word['text'],tested_start=lo,tested_end=hi,
               original_score=a['score'],muted_score=b['score'],score_drop=a['score']-b['score'],
               original_path=dict(start=a['start'],end=a['end']),muted_path=dict(start=b['start'],end=b['end']),
               mask_test_only=True,human_listened=False)
    return record,check


def apply_precision(report,base_records,bundle,resolve_manual_conflicts=False):
    from copy import deepcopy
    from .singing_alignment import collect_measurements,finish_recovery
    from .timing_audit import acoustic_end
    if (bundle.get('version')!=PRECISION_VERSION or bundle.get('media_sha256')!=report['media_sha256']
            or bundle.get('transcript_sha256')!=report['transcript_sha256']):
        raise ValueError('Precision evidence belongs to different source audio or text')
    before=deepcopy(report)
    working=deepcopy(report)
    extra=collect_measurements(report,bundle['records'])
    finish_recovery(working,collect_measurements(report,base_records+bundle['records']),
                    bundle.get('errors',[]),resolve_manual_conflicts)
    for check in bundle.get('audio_controls',[]):
        i=check['index']
        if not 0<=i<len(report['words']) or check['text']!=report['words'][i]['text']:
            raise ValueError('Audio control cannot change source word identity')
        row=working['words'][i]
        row['audio_control']=dict(check,matches_current_timing=(check['tested_start']==row['start']
                                                          and check['tested_end']==acoustic_end(row)))
    working['precision_recovery']=dict(version=PRECISION_VERSION,targets=bundle['targets'],
        measured=sorted(extra),changed=[i for i,(a,b) in enumerate(zip(before['words'],working['words']))
        if a['start']!=b['start'] or acoustic_end(a)!=acoustic_end(b)],
        errors=bundle.get('errors',[]),human_listened=False)
    report.clear();report.update(working)
    return report


def refine_uncertain_singing(video,report,base_records,cache_dir,progress=None,resolve_manual_conflicts=False,include_noncommercial=False):
    """Retry uncertain words automatically, then cache real measurements, never a verdict."""
    import gc,json,hashlib,importlib.util
    from pathlib import Path
    from .singing_alignment import collect_measurements,ctc_measurements,SECONDARY_HEBREW_MODEL
    from .alignment import HEBREW_MODEL
    from .media import extract_audio
    targets=[w['index'] for w in report['words'] if w.get('audit',{}).get('status')!='supported']
    if not targets:
        return report
    cache_dir=Path(cache_dir);path=precision_cache_path(cache_dir,report)
    if include_noncommercial:
        path = path.with_name(path.stem + '-noncommercial.json')
    def finish(bundle):
        bundle = permitted_bundle(bundle, include_noncommercial)
        apply_precision(report,base_records,bundle,resolve_manual_conflicts)
        from .singing_phrases import refine_phrases
        return refine_phrases(video,report,base_records+bundle['records'],cache_dir,
                              progress,resolve_manual_conflicts,include_noncommercial)
    if path.exists():
        return finish(json.loads(path.read_text(encoding='utf8')))
    import numpy as np
    import torch
    from whisperx.alignment import load_align_model
    torch.set_num_threads(min(6,torch.get_num_threads()))
    def announce(message):
        if progress:progress(message)
    def save(file,data):
        staged=file.with_suffix('.tmp');staged.write_text(json.dumps(data,ensure_ascii=False,allow_nan=False),encoding='utf8');staged.replace(file)
    audio=np.frombuffer(extract_audio(Path(video)),dtype=np.float32).copy()
    vocal_file=cache_dir/f'vocals-{report["media_sha256"]}-v1.npy'
    vocals=np.load(vocal_file,allow_pickle=False) if vocal_file.exists() else audio
    if vocals.shape!=audio.shape or not np.isfinite(vocals).all():vocals=audio
    windows=precision_windows(report,collect_measurements(report,base_records),targets)
    records,errors=[],[]
    def measure(name,view,window,pronunciation,operation,sound):
        key=hashlib.sha256(json.dumps([PRECISION_VERSION,report['media_sha256'],report['transcript_sha256'],
            name,view,window,pronunciation],sort_keys=True).encode()).hexdigest()
        file=cache_dir/f'precision-measurement-{key}.json'
        try:
            if file.exists():record=json.loads(file.read_text(encoding='utf8'))
            else:
                rows=report['words'][window['first']:window['stop']]
                words=operation(sound,rows,window['start'],window['end'],pronunciation)
                record=dict(family='ctc',model=name,view=view,window=window,words=words)
                save(file,record)
            records.append(record)
        except Exception as exc:
            errors.append(dict(model=name,target=window['target'],message=str(exc)))
    # MMS weights have a separate CC-BY-NC license. Use them only when already
    # installed; the ordinary Hebrew alignment pipeline remains available.
    mms_cached=include_noncommercial and (Path(torch.hub.get_dir())/'checkpoints/model.pt').is_file()
    if mms_cached and importlib.util.find_spec('uroman') is not None:
        try:
            import uroman
            from torchaudio.pipelines import MMS_FA
            model=MMS_FA.get_model(with_star=False).eval();tokenizer=MMS_FA.get_tokenizer();aligner=MMS_FA.get_aligner();romanizer=uroman.Uroman()
            for n,window in enumerate(windows):
                announce(f'בודקים שוב מול צלילי השירה: {n+1} מתוך {len(windows)}')
                for pronunciation in pronunciation_options(report['words'][window['first']:window['stop']]):
                    for view,sound in (('vocals-local',vocals),('original-local',audio)):
                        measure(MULTILINGUAL_MODEL,view,window,pronunciation,
                            lambda a,r,s,e,p:multilingual_measurements(model,tokenizer,aligner,romanizer,a,r,s,e,p),sound)
            del model;gc.collect()
        except Exception as exc:errors.append(dict(model=MULTILINGUAL_MODEL,message=str(exc)))
    for name in (HEBREW_MODEL,SECONDARY_HEBREW_MODEL):
        try:
            model,metadata=load_align_model(language_code='he',device='cpu',model_name=name)
            for n,window in enumerate(windows):
                announce(f'מדייקים מילים במקטעים קצרים: {n+1} מתוך {len(windows)}')
                for pronunciation in pronunciation_options(report['words'][window['first']:window['stop']]):
                    measure(name,'vocals-local',window,pronunciation,
                        lambda a,r,s,e,p:ctc_measurements(model,metadata,a,r,s,e,p),vocals)
            del model;gc.collect()
        except Exception as exc:errors.append(dict(model=name,message=str(exc)))
    controls=[]
    if mms_cached and importlib.util.find_spec('uroman') is not None:
        try:
            from copy import deepcopy
            from .singing_alignment import finish_recovery
            proposal=deepcopy(report)
            finish_recovery(proposal,collect_measurements(proposal,base_records+records),errors,resolve_manual_conflicts)
            import uroman
            from torchaudio.pipelines import MMS_FA
            model=MMS_FA.get_model(with_star=False).eval();tokenizer=MMS_FA.get_tokenizer();aligner=MMS_FA.get_aligner();romanizer=uroman.Uroman()
            for n,i in enumerate(targets):
                announce(f'בודקים תמיכה בקול בפועל: {n+1} מתוך {len(targets)}')
                try:
                    record,check=audio_control(model,tokenizer,aligner,romanizer,vocals,proposal,i)
                    records.append(record);controls.append(check)
                except Exception as exc:errors.append(dict(target=i,stage='audio-control',message=str(exc)))
            del model;gc.collect()
        except Exception as exc:errors.append(dict(model=MULTILINGUAL_MODEL,stage='audio-control',message=str(exc)))
    bundle=dict(version=PRECISION_VERSION,media_sha256=report['media_sha256'],transcript_sha256=report['transcript_sha256'],
                targets=targets,records=records,audio_controls=controls,errors=errors)
    save(path,bundle)
    return finish(bundle)

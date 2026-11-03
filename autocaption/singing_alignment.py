"""Local, transcript-owned recovery for sung words.

CTC phone evidence and Whisper's forced token/attention path are different
measurements. Neither generates display text. Model support is not ground truth.
"""
from collections import defaultdict
from copy import deepcopy
from itertools import combinations
from statistics import median
import unicodedata
import math

from .alignment import valid_interval, Transcript, validate_report, timing_problems
from .timing_audit import acoustic_end, reference_cluster

SECONDARY_HEBREW_MODEL = 'imvladikon/wav2vec2-large-xlsr-53-hebrew'
RECOVERY_VERSION = 1


class NormalizedCTC:
    """The Hebrew HF checkpoints request zero-mean, unit-variance input."""
    def __init__(self, model):
        self.model = model

    def __call__(self, wave):
        wave = (wave-wave.mean(dim=-1, keepdim=True)) / (
            wave.var(dim=-1, keepdim=True, unbiased=False)+1e-7).sqrt()
        return self.model(wave)


def analysis_token(text):
    """Remove non-acoustic punctuation/niqqud in the analysis view only."""
    token=''.join(c for c in unicodedata.normalize('NFD', text).lower() if c.isalnum())
    return {'כול':'כל','הכול':'הכל','והכול':'והכל','שהכול':'שהכל'}.get(token,token)


def contextual_windows(report, windows, references):
    anchors = {i: reference_cluster(v) for i,v in references.items() if v}
    result = []
    for w in windows:
        first, stop = max(0,w['first']-2), min(len(report['words']),w['stop']+2)
        start = min(w['start'], anchors[first]['start']-.7 if first in anchors else w['start']-2)
        end = max(w['end'], anchors[stop-1]['end']+.7 if stop-1 in anchors else w['end']+2)
        if end-start > 29:
            first,stop = w['first'],w['stop']
            start,end = w['start']-.15,w['end']+.15
        start,end = max(0.,start),min(report['duration'],end)
        if .5 < end-start <= 30:
            result.append(dict(first=first,stop=stop,start=start,end=end,
                               core_first=w['first'],core_stop=w['stop']))
    return result


def ctc_measurements(model, metadata, audio, rows, start, end, pronunciation=None):
    import numpy as np
    import torch
    import torchaudio
    vocabulary = metadata['dictionary']
    tokens,bounds = [],[]
    for row in rows:
        word = (pronunciation or {}).get(row['index'],analysis_token(row['text']))
        if not word or any(c not in vocabulary for c in word):
            # Preserve the row in the report; an unsupported spelling cannot
            # silently become a different word or shift positional correspondence.
            raise ValueError('Source word outside Hebrew acoustic vocabulary')
        if tokens:
            tokens.append(vocabulary['|'])
        first = len(tokens)
        tokens.extend(vocabulary[c] for c in word)
        bounds.append((first,len(tokens)))
    clip = torch.from_numpy(np.asarray(audio[round(start*16000):round(end*16000)]).copy()).unsqueeze(0)
    end = start+clip.shape[-1]/16000
    with torch.inference_mode():
        emissions = NormalizedCTC(model)(clip).logits.log_softmax(-1).cpu()
    blank = vocabulary.get('[pad]',vocabulary.get('<pad>',0))
    labels,scores = torchaudio.functional.forced_align(emissions,torch.tensor([tokens]),blank=blank)
    spans = torchaudio.functional.merge_tokens(labels[0],scores[0].exp(),blank=blank)
    if [s.token for s in spans] != tokens:
        raise ValueError('Acoustic path changed the source token sequence')
    ratio = (end-start)/emissions.shape[1]
    result = []
    for row,(a,b) in zip(rows,bounds):
        chars = spans[a:b]
        lo,hi = start+chars[0].start*ratio,start+chars[-1].end*ratio
        result.append(dict(index=row['index'],text=row['text'],analysis=(pronunciation or {}).get(row['index'],analysis_token(row['text'])),start=round(lo,3),end=round(hi,3),
                           score=sum(c.score for c in chars)/len(chars),
                           start_score=chars[0].score,end_score=chars[-1].score,
                           edge=lo<=start+.04 or hi>=end-.04))
    return result


def attention_measurements(model, tokenizer, audio, rows, start, end, pronunciation=None):
    import numpy as np
    from faster_whisper.audio import pad_or_trim
    tokens,bounds = [],[0]
    analysis = [(pronunciation or {}).get(row['index'], row['text']) for row in rows]
    for word in analysis:
        tokens.extend(tokenizer.encode(' '+word))
        bounds.append(len(tokens))
    if tokenizer.decode(tokens) != ' '+' '.join(analysis):
        raise ValueError('Tokenizer changed the source text')
    clip = audio[round(start*16000):round(end*16000)]
    encoded = model.encode(pad_or_trim(model.feature_extractor(clip),3000))
    alignment = model.model.align(encoded,tokenizer.sot_sequence,[tokens],round(len(clip)/160))[0]
    path = np.array(alignment.alignments)
    jumps = np.pad(np.diff(path[:,0]),(1,0),constant_values=1).astype(bool)
    times = path[jumps,1]/model.tokens_per_second
    if len(times) <= bounds[-1]:
        raise ValueError('Incomplete forced token path')
    result = []
    for row,word,(a,b) in zip(rows,analysis,zip(bounds,bounds[1:])):
        lo,hi = float(start+times[a]),float(start+times[b])
        result.append(dict(index=row['index'],text=row['text'],analysis=word,start=round(lo,3),end=round(hi,3),
                           score=float(np.mean(alignment.text_token_probs[a:b])),
                           edge=lo<=start+.04 or hi>=end-.04))
    return result


def collect_measurements(report, records):
    collected = defaultdict(list)
    for record in records:
        for row in record['words']:
            i = row['index']
            if not 0 <= i < len(report['words']) or row['text'] != report['words'][i]['text']:
                raise ValueError('Measurements cannot change source words or indexes')
            target=record.get('window',{}).get('target')
            if target is not None and i!=target:
                continue  # Context words stabilize the crop; they are not new measurements of its edges.
            abbreviation = len(analysis_token(row['text']))==1 and any(c in row['text'] for c in ('׳',"'"))
            score=row.get('score')
            if (not isinstance(score,(int,float)) or not math.isfinite(score) or not 0<=score<=1):
                continue
            expanded = row.get('pronunciation',row.get('analysis')) in ('השם','אדוני') and row['text'] in ('ה׳',"ה'")
            if (row.get('edge') or record['family']=='ctc' and abbreviation and not expanded
                    or not valid_interval(row.get('start'),row.get('end'),report['duration'])
                    or row['end']-row['start']<.06
                    or row.get('score',0)<.02):
                continue
            collected[i].append(dict(row,family=record['family'],model=record['model'],view=record['view']))
    return dict(collected)


def tempo_measurements(model, metadata, audio, rows, start, end, factor=1.35):
    import subprocess
    import numpy as np
    from .media import find_tool
    clip=np.asarray(audio[round(start*16000):round(end*16000)],dtype=np.float32)
    result=subprocess.run([find_tool('ffmpeg'),'-v','error','-f','f32le','-ar','16000','-ac','1',
        '-i','pipe:0','-af',f'atempo={factor}','-f','f32le','pipe:1'],input=clip.tobytes(),
        capture_output=True,check=True,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
    sped=np.frombuffer(result.stdout,dtype=np.float32).copy()
    measured=ctc_measurements(model,metadata,sped,rows,0,len(sped)/16000)
    for row in measured:
        row['start']=round(start+row['start']*factor,3)
        row['end']=round(start+row['end']*factor,3)
    return measured


def boundary_groups(options, edge, ctc_threshold=.2, attention_threshold=.5, use_edge_scores=True):
    """A strong interior consonant cannot certify a weak first/last phoneme.

    Older caches contain only whole-word scores. Once a checkpoint supplies
    actual edge probabilities, use those for that checkpoint's boundary checks;
    do not let its less-specific historical scores outvote the measured edge.
    """
    key = edge+'_score'
    detailed = {w['model'] for w in options if use_edge_scores and w['family']=='ctc' and key in w}
    grouped = defaultdict(list)
    for w in options:
        if w['family']=='ctc' and w['model'] in detailed and key not in w:
            continue
        score = w.get(key, w['score']) if use_edge_scores else w['score']
        threshold = ctc_threshold if w['family']=='ctc' else attention_threshold
        if (isinstance(score, (int, float)) and math.isfinite(score) and 0<=score<=1
                and score >= threshold and w['score'] >= threshold):
            grouped[(w['family'], w['model'])].append(w)
    return grouped


def boundary_support(options, value, edge, use_edge_scores=True):
    from statistics import median
    tolerance = .2 if edge=='start' else .3
    grouped = boundary_groups(options, edge, use_edge_scores=use_edge_scores)
    # One anomalous crop (especially a tempo retry spanning an instrumental
    # break) cannot speak for a checkpoint whose other measurements disagree.
    strong = [peers[0] for peers in grouped.values()
              if median(abs(w[edge]-value) for w in peers)<=tolerance]
    # Repeating the same checkpoint/crop cannot manufacture independent support.
    pairs = [(a,b) for a,b in combinations(strong,2) if a['model']!=b['model']
             and 'ctc' in (a['family'],b['family'])]
    return bool(pairs), sorted({x['model'] for pair in pairs for x in pair})


def candidate_cost(candidate, options):
    """Each model has one vote; duplicates and overlapping crops get no bonus."""
    from statistics import median
    grouped = defaultdict(list)
    for w in options:
        if w['score'] >= (.12 if w['family']=='ctc' else .35):
            grouped[(w['family'],w['model'])].append(w)
    distances = []
    for peers in grouped.values():
        distances.append(median(min(2.,abs(candidate['start']-w['start']))
                                + .2*min(2.,abs(candidate['end']-w['end'])) for w in peers))
    distance = sum(distances)/max(1,len(distances))
    # Retain the complete acoustic paths for selecting a best estimate. An
    # elided initial consonant can have low probability in an otherwise useful
    # path. Direct edge scores control verification, not deletion of that path.
    supported,_ = boundary_support(options,candidate['start'],'start',use_edge_scores=False)
    cost = distance + .25*(1-candidate.get('score',0))
    cost += 0 if supported else .3
    cost += 0 if boundary_support(options,candidate['end'],'end',use_edge_scores=False)[0] else .15
    if candidate.get('legacy'):
        cost += .5
    return cost


def boundary_candidates(options):
    """Combine measured edges of the same occurrence, never interpolate a time.

    A recognizer may place the onset well and include silence at the end. Another
    may isolate the end but miss the onset. Both edges must independently pass
    the existing cross-model checks. Distant occurrences cannot be joined.
    """
    starts=[w for w in options if boundary_support(options,w['start'],'start')[0]]
    ends=[w for w in options if boundary_support(options,w['end'],'end')[0]]
    starts=sorted(starts,key=lambda w:candidate_cost(w,options))[:4]
    ends=sorted(ends,key=lambda w:candidate_cost(w,options))[:4]
    combined=[];seen={(w['start'],w['end']) for w in options}
    for a in starts:
        for b in ends:
            if (a['start']>=b['end'] or abs(a['start']-b['start'])>.6
                    or abs(a['end']-b['end'])>.8 or (a['start'],b['end']) in seen):
                continue
            seen.add((a['start'],b['end']))
            combined.append(dict(index=a['index'],text=a['text'],start=a['start'],end=b['end'],
                score=min(a['score'],b['score']),family='combined',model=a['model']+' + '+b['model'],
                view='measured-boundaries',boundary_sources=dict(start=a,end=b)))
    return combined


def recover_path(report, measurements, resolve_manual_conflicts=False):
    """Jointly move the source-ordered words; no old automatic anchor can block recovery.

    Acoustic onsets are never interpolated. If measurements fail, retain the
    previous interval explicitly. Ends may share the next onset with provenance.
    """
    before = deepcopy(report)
    rows = report['words']
    states = [(0.,[])]
    for i,row in enumerate(rows):
        old = dict(row,end=acoustic_end(row),legacy=True,score=row.get('score') or 0)
        old.pop('gap_fill',None)
        options = measurements.get(i,[])
        locked = row.get('status')=='manual' and not resolve_manual_conflicts
        choices = [old] if locked else options+boundary_candidates(options)+[old]
        next_states = []
        for candidate in choices:
            if candidate['text']!=row['text'] or candidate['index']!=i:
                raise ValueError('Recovery cannot change source words')
            cost = candidate_cost(candidate,options)
            best = None
            for total,path in states:
                truncation_cost = 0.
                if path:
                    left = path[-1]
                    boundary = left['end'] if left.get('status')=='manual' and not resolve_manual_conflicts else left['start']+.04
                    if candidate['start'] < boundary:
                        continue
                    if candidate['start'] < left['end']:
                        # Score the interval that will actually be displayed.
                        # Previously the search scored the full measured end,
                        # then silently cut it at the following word's onset.
                        # That could reward a weak early onset which destroyed
                        # a well-supported held syllable in the preceding word.
                        shortened = dict(left, end=candidate['start'])
                        previous_options = measurements.get(i-1, [])
                        truncation_cost = (candidate_cost(shortened, previous_options)
                                           - candidate_cost(left, previous_options))
                rank = total+cost+truncation_cost
                if best is None or rank<best[0]:best=(rank,path+[candidate])
            if best:next_states.append(best)
        if not next_states:
            raise ValueError('No coherent source-ordered acoustic path; original report retained')
        states = next_states
    path = min(states,key=lambda s:s[0])[1]
    updated = []
    for i,candidate in enumerate(path):
        original = before['words'][i]
        row = deepcopy(original)
        lo,hi = candidate['start'],candidate['end']
        if i+1<len(path):hi=min(hi,path[i+1]['start'])
        if not valid_interval(lo,hi,report['duration']):
            raise ValueError('Invalid recovered interval; original report retained')
        changed = lo!=original['start'] or hi!=acoustic_end(original)
        row.pop('gap_fill',None)
        row.pop('boundary_adjustment',None)
        row.update(start=lo,end=hi)
        if changed:
            row['before_singing_recovery']={k:deepcopy(original[k]) for k in
                ('index','text','start','end','status','timing_method','gap_fill') if k in original}
            if original['status']=='manual':row['replaced_manual_timing']=dict(start=original['start'],end=acoustic_end(original))
        if not candidate.get('legacy'):
            row.update(status='aligned' if candidate['family']=='ctc' else 'estimated',score=candidate['score'],
                       timing_method='singing_'+candidate['family'],reason='',
                       recovery_measurement={k:candidate[k] for k in ('family','model','view','start','end','score')})
            if candidate.get('boundary_sources'):
                row['recovery_measurement']['boundary_sources']=deepcopy(candidate['boundary_sources'])
        if hi!=candidate['end']:row['boundary_adjustment']=dict(acoustic_end=candidate['end'])
        updated.append(row)
    trial=dict(report,words=updated)
    validate_report(trial,Transcript(before['transcript']),before['media_sha256'])
    if timing_problems(trial):raise ValueError('Recovered timings are invalid')
    report['words']=updated
    return [i for i,(a,b) in enumerate(zip(before['words'],updated)) if a['start']!=b['start'] or acoustic_end(a)!=b['end']]


def audit_recovery(report, measurements):
    counts=dict(supported=0,disagreement=0,uncertain=0)
    for i,row in enumerate(report['words']):
        options=measurements.get(i,[])
        start_ok,start_models=boundary_support(options,row['start'],'start')
        end_ok,end_models=boundary_support(options,acoustic_end(row),'end')
        status='supported' if start_ok and end_ok else 'uncertain'
        row['audit']=dict(status=status,start=row['start'],end=acoustic_end(row),
                          start_supported=start_ok,end_supported=end_ok,
                          start_models=start_models,end_models=end_models,
                          method='independent_forced_paths',measurements=options,
                          display_hold_seconds=round(row['end']-acoustic_end(row),3))
        counts[status]+=1
    report['full_audit']=dict(version=2,total=len(report['words']),counts=counts,
                              method='independent_forced_paths',human_listened=False,
                              start_tolerance=.2,end_tolerance=.3,
                              onsets_supported=sum(w['audit']['start_supported'] for w in report['words']))
    return report


def recovery_cache_path(cache_dir, report, model_name):
    import hashlib
    from pathlib import Path
    key=hashlib.sha256(f'{RECOVERY_VERSION}:{report["media_sha256"]}:{report["transcript_sha256"]}:{model_name}'.encode()).hexdigest()
    return Path(cache_dir)/f'bundle-{key}.json'


def finish_recovery(report, measurements, errors, resolve_manual_conflicts=False):
    original=deepcopy(report)
    working=deepcopy(report)
    changed=recover_path(working,measurements,resolve_manual_conflicts)
    audit_recovery(working,measurements)
    if any(w.get('gap_fill') for w in original['words']):
        for left,right in zip(working['words'],working['words'][1:]):
            if left['end']<right['start']:
                left['gap_fill']=dict(end=left['end']);left['end']=right['start']
                left['audit']['display_hold_seconds']=round(left['end']-left['gap_fill']['end'],3)
    working['singing_recovery']=dict(version=RECOVERY_VERSION,changed=changed,errors=errors,
                                    measured=len(measurements),source_immutable=True)
    working['automatic_timing']=dict(version=1,estimated=sum(w['status']=='estimated' for w in working['words']))
    report.clear();report.update(working)
    return report


def recover_singing(video, report, evidence, cache_dir, model_name, progress=None,
                    resolve_manual_conflicts=False, include_noncommercial=False):
    """Automatic retries, with fingerprinted on-disk measurements and no user gates."""
    import gc
    import hashlib
    import json
    from pathlib import Path
    if report.get('allow_reordered_timings'):
        return report
    if evidence.get('media_sha256')!=report['media_sha256']:
        raise ValueError('Speech evidence belongs to a different video')
    cache_dir=Path(cache_dir)
    cache_dir.mkdir(parents=True,exist_ok=True)
    bundle_path=recovery_cache_path(cache_dir,report,model_name)
    if bundle_path.exists():
        bundle=json.loads(bundle_path.read_text(encoding='utf8'))
        if (bundle.get('media_sha256')==report['media_sha256']
                and bundle.get('transcript_sha256')==report['transcript_sha256']
                and bundle.get('version')==RECOVERY_VERSION):
            finish_recovery(report,collect_measurements(report,bundle['records']),
                            bundle.get('errors',[]),resolve_manual_conflicts)
            from .singing_precision import refine_uncertain_singing
            return refine_uncertain_singing(video,report,bundle['records'],cache_dir,
                                            progress,resolve_manual_conflicts,include_noncommercial)
    import numpy as np
    import torch
    from whisperx.alignment import load_align_model
    from faster_whisper import WhisperModel
    from faster_whisper.tokenizer import Tokenizer
    from .alignment import HEBREW_MODEL
    from .media import extract_audio
    from .models import cached_model_path, IVRIT_LARGE, IVRIT_TURBO, prepare_model
    from .singing_timing import VocalAligner
    from .timing_audit import speech_references, source_windows

    torch.set_num_threads(min(6,torch.get_num_threads()))
    def announce(message):
        if progress:progress(message)
    def save(path,data):
        staged=path.with_suffix('.tmp')
        staged.write_text(json.dumps(data,ensure_ascii=False,allow_nan=False),encoding='utf8')
        staged.replace(path)
    # Fresh raw recognition bounds phrases. Source-owned words alone enter both
    # forced paths; recognition is never substituted into the displayed text.
    heard=[dict(w,start=w.get('raw_start',w.get('start')),end=w.get('raw_end',w.get('end')))
           for w in evidence['words']]
    references=speech_references(report,[dict(model=model_name,view='original',words=heard)])
    for i,w in enumerate(report['words']):
        if w.get('audit',{}).get('recognition'):
            references[i]=w['audit']['recognition']
    windows=contextual_windows(report,source_windows(report,references),references)
    audio=np.frombuffer(extract_audio(Path(video)),dtype=np.float32).copy()
    vocals_path=cache_dir/f'vocals-{report["media_sha256"]}-v1.npy'
    vocals=np.load(vocals_path,allow_pickle=False) if vocals_path.exists() else None
    if vocals is not None and (vocals.shape!=audio.shape or not np.isfinite(vocals).all()):
        vocals=None
    if vocals is None:
        announce('מבודדים את השירה לתזמון אוטומטי; האודיו המקורי נשאר בסרטון')
        separator=VocalAligner(Path(video),report['media_sha256'],'cpu',progress,cache_dir/'separation')
        vocals=np.zeros_like(audio)
        for offset in range(0,len(audio),160000):
            stop=min(len(audio),offset+160000)
            start=max(0,offset/16000-1);end=min(len(audio)/16000,stop/16000+1)
            clip=separator.vocals(start,end)
            lo=round((offset/16000-start)*16000)
            vocals[offset:stop]=clip[lo:lo+stop-offset]
            announce(f'מבודדים שירה: {round(100*stop/len(audio))}%')
        np.save(vocals_path,vocals,allow_pickle=False)
        del separator;gc.collect()
    records,errors=[],[]
    def measure(family,name,view,window,operation):
        first,stop=window['first'],window['stop']
        signature=hashlib.sha256(json.dumps([RECOVERY_VERSION,report['media_sha256'],
            report['transcript_sha256'],family,name,view,window],sort_keys=True).encode()).hexdigest()
        path=cache_dir/f'{signature}.json'
        if path.exists():
            record=json.loads(path.read_text(encoding='utf8'))
            records.append(record);return
        rows=report['words'][first:stop]
        sound=audio if view=='original' else vocals
        start,end=window['start'],min(len(sound)/16000,window['end'])
        try:
            measured=operation(sound,rows,start,end)
            record=dict(family=family,model=name,view=view,window=window,words=measured)
            save(path,record);records.append(record)
        except Exception as error:
            errors.append(dict(first=first,stop=stop,model=name,message=str(error)))
    # Models are released between passes. No .en checkpoint or remote inference.
    for model_id in (HEBREW_MODEL,SECONDARY_HEBREW_MODEL):
        announce('טוענים מודל התאמת צלילים לעברית; בפעם הראשונה המודל נשמר במחשב')
        try:
            model,metadata=load_align_model(language_code='he',device='cpu',model_name=model_id)
            for n,window in enumerate(windows):
                announce(f'מתאימים צלילים למילים: קטע {n+1} מתוך {len(windows)}')
                measure('ctc',model_id,'vocals',window,
                        lambda sound,rows,a,b:ctc_measurements(model,metadata,sound,rows,a,b))
                measure('ctc',model_id,'vocals-tempo-1.35',window,
                        lambda sound,rows,a,b:tempo_measurements(model,metadata,sound,rows,a,b))
            del model;gc.collect()
        except Exception as error:
            errors.append(dict(model=model_id,message=str(error)))
    for name in dict.fromkeys([model_name,IVRIT_TURBO]):
        announce('משווים את התמליל המדויק לשירה; אין יצירת מילים חדשות')
        try:
            model=WhisperModel(prepare_model(name),device='cpu',compute_type='int8',cpu_threads=6)
            tokenizer=Tokenizer(model.hf_tokenizer,model.model.is_multilingual,task='transcribe',language='he')
            for n,window in enumerate(windows):
                announce(f'בודקים תזמון מילה מול השירה: קטע {n+1} מתוך {len(windows)}')
                for view in (('vocals','original') if name==model_name else ('vocals',)):
                    measure('attention',name,view,window,
                            lambda sound,rows,a,b:attention_measurements(model,tokenizer,sound,rows,a,b))
            del model;gc.collect()
        except Exception as error:
            errors.append(dict(model=name,message=str(error)))
    measurements=collect_measurements(report,records)
    if not measurements:
        report['singing_recovery']=dict(version=RECOVERY_VERSION,changed=[],errors=errors)
        return report
    save(bundle_path,dict(version=RECOVERY_VERSION,media_sha256=report['media_sha256'],
                         transcript_sha256=report['transcript_sha256'],records=records,errors=errors))
    finish_recovery(report,measurements,errors,resolve_manual_conflicts)
    from .singing_precision import refine_uncertain_singing
    return refine_uncertain_singing(video,report,records,cache_dir,progress,resolve_manual_conflicts,include_noncommercial)

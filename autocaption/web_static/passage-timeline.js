"use strict";
// Pointer gestures edit the same cue draft used by preview and MP4 export.
class CaptionPassageTimeline {
 constructor(options){
  this.options=options;this.selected=0;this.history=[];this.future=[];this.zoom=1;this.active=null;
  for(const name of ["Timeline","Scroll","Canvas","Track","Ruler","Wave","Playhead","Selection","Status","Undo","Redo","Focus","Fit"]){
   this[name.toLowerCase()]=document.getElementById("passage"+name);
  }
  this.canvas.addEventListener("pointerdown",e=>this.begin(e));
  this.canvas.addEventListener("pointermove",e=>{if(this.active?.id===e.pointerId){this.active.x=e.clientX;this.move();}});
  this.canvas.addEventListener("pointerup",e=>{if(this.active?.id===e.pointerId){this.active.x=e.clientX;this.move();this.finish();}});
  this.canvas.addEventListener("pointercancel",()=>this.finish(true));
  this.canvas.addEventListener("lostpointercapture",()=>this.finish(true));
  this.canvas.addEventListener("keydown",e=>this.key(e));
  document.addEventListener("keydown",e=>{if(e.key==="Escape"&&this.active){e.preventDefault();this.finish(true);}});
  this.zoomControl=document.getElementById("passageZoom");
  this.zoomControl.addEventListener("input",()=>this.setZoom(+this.zoomControl.value));
  this.fit.addEventListener("click",()=>this.setZoom(1));
  this.focus.addEventListener("click",()=>{
   const {cues,duration}=this.options.read(),cue=cues[this.selected];
   if(cue)this.setZoom(Math.min(24,Math.max(1,duration/((cue.end-cue.start)*1.6))));
  });
  this.undo.addEventListener("click",()=>this.undoEdit());
  this.redo.addEventListener("click",()=>this.undoEdit(true));
  for(const name of ["Split","MergePrevious","MergeNext","CutTime","ActionHint","SplitDialog","SplitTitle","SplitAt","SplitWord","SplitSuggestion","SplitLeft","SplitRight","SplitLeftTime","SplitRightTime","SplitConfirm"]){
   this[name[0].toLowerCase()+name.slice(1)]=document.getElementById("passage"+name);
  }
  this.split.addEventListener("click",()=>this.openSplit());
  this.mergePrevious.addEventListener("click",()=>this.mergeSelected(-1));
  this.mergeNext.addEventListener("click",()=>this.mergeSelected(1));
  this.splitWord.addEventListener("change",()=>this.showSplitPreview());
  this.splitConfirm.addEventListener("click",()=>this.confirmSplit());
  this.splitDialog.addEventListener("close",()=>{this.splitDraft=null;this.options.commit();this.refresh();});
  new ResizeObserver(()=>this.refresh()).observe(this.scroll);
  new ResizeObserver(()=>{if(!this.timeline.hidden)document.documentElement.style.setProperty("--passage-height",this.timeline.offsetHeight+"px");}).observe(this.timeline);
 }
 reset(){this.finish(true);this.selected=0;this.history=[];this.future=[];this.zoom=1;this.zoomControl.value=1;this.scroll.scrollLeft=0;this.refresh();}
 invalidate(){this.history=[];this.future=[];if(this.splitDialog?.open)this.splitDialog.close();}
 select(index){this.selected=index;this.refresh();}
 label(cue,index){return cue.label||"קטע "+(index+1);}
 clock(value){const cs=Math.round(value*100);return Number.isFinite(value)?Math.floor(cs/6000).toString().padStart(2,"0")+":"+((cs%6000)/100).toFixed(2).padStart(5,"0"):"—";}
 refresh(){
  if(this.timeline.hidden)return;
  const {cues,duration,disabled,wave,status}=this.options.read();
  if(!duration)return;
  this.selected=Math.max(0,Math.min(this.selected,cues.length-1));
  const width=Math.max(1,this.scroll.clientWidth)*this.zoom,scale=width/duration;
  this.canvas.style.width=width+"px";
  if(this.track.children.length!==cues.length){
   this.track.replaceChildren(...cues.map((cue,index)=>{
    const clip=document.createElement("div");clip.className="passage-clip";clip.dataset.index=index;
    for(const kind of ["start","move","end"]){
     const b=document.createElement("button");b.type="button";b.dataset.kind=kind;b.dataset.index=index;
     b.className=kind==="move"?"passage-body":"passage-handle "+kind;
     if(kind==="move"){const title=document.createElement("strong"),time=document.createElement("small");b.append(title,time);}
     else b.textContent="Ⅱ";
     clip.append(b);
    }
    return clip;
   }));
  }
  cues.forEach((cue,index)=>{
   const clip=this.track.children[index],valid=Number.isFinite(cue.start)&&Number.isFinite(cue.end)&&cue.end>cue.start;
   clip.hidden=!valid;if(!valid)return;
   clip.style.left=cue.start*scale+"px";clip.style.width=Math.max(2,(cue.end-cue.start)*scale)+"px";
   clip.classList.toggle("selected",index===this.selected);
   clip.classList.toggle("compact",(cue.end-cue.start)*scale<130);
   clip.classList.toggle("chorus",/פזמון/.test(cue.label));
   const label=this.label(cue,index),times=this.clock(cue.start)+" – "+this.clock(cue.end);
   clip.querySelector("strong").textContent=label;clip.querySelector("small").textContent=times;
   for(const b of clip.children){
    const kind=b.dataset.kind;b.disabled=disabled;b.title=(kind==="move"?"גררו להזזה":kind==="start"?"גררו לשינוי ההתחלה":"גררו לשינוי הסיום")+" · "+times;
    b.setAttribute("aria-label",(kind==="move"?"הזזת ":kind==="start"?"תחילת ":"סיום ")+label+", "+times);
    b.setAttribute("aria-describedby","passageStatus");
   }
  });
  const cue=cues[this.selected];
  this.selection.replaceChildren();
  if(cue){const label=document.createElement("span"),times=document.createElement("bdi");label.textContent=this.label(cue,this.selected)+" · ";times.dir="ltr";times.textContent=this.clock(cue.start)+" – "+this.clock(cue.end);this.selection.append(label,times);}
  this.undo.disabled=disabled||!this.history.length||!!this.active;
  this.redo.disabled=disabled||!this.future.length||!!this.active;
  const locked=disabled||!!this.active;
  this.mergePrevious.disabled=locked||!CaptionEditing.mergePassages(cues,this.selected-1);
  this.mergeNext.disabled=locked||!CaptionEditing.mergePassages(cues,this.selected);
  this.mergePrevious.title=this.selected>0?"חיבור עם "+this.label(cues[this.selected-1],this.selected-1)+", כולל הרווח ביניהם":"אין קטע קודם";
  this.mergeNext.title=this.selected+1<cues.length?"חיבור עם "+this.label(cues[this.selected+1],this.selected+1)+", כולל הרווח ביניהם":"אין קטע הבא";
  for(const control of [this.zoomControl,this.focus,this.fit])control.disabled=disabled||!!this.active;
  this.status.textContent=this.active?(this.active.limited?"הגעתם לגבול הסרטון או לקטע הסמוך":this.active.kind==="seek"?"תצוגה חיה על הסרטון":"תצוגה חיה · שחררו לשמירה · Esc לביטול"):status;
  const rulerKey=width+":"+duration;
  if(this.rulerKey!==rulerKey){
   this.rulerKey=rulerKey;this.ruler.replaceChildren();
   const step=[.1,.2,.5,1,2,5,10,15,30,60,120,300,600].find(v=>v*scale>=65)||Math.ceil(duration/10);
   for(let t=0;t<=duration;t+=step){const tick=document.createElement("span");tick.style.left=t*scale+"px";tick.textContent=this.clock(t).replace(/\.00$/,"");this.ruler.append(tick);}
  }
  if(this.lastWave!==wave){
   this.lastWave=wave;this.wave.replaceChildren();
   if(wave?.length){const path=document.createElementNS("http://www.w3.org/2000/svg","path");path.setAttribute("d",wave.map((v,i)=>{const h=Math.max(.5,v*18);return `M${(i+.5)*1200/wave.length} ${20-h}v${h*2}`;}).join(""));this.wave.append(path);}
  }
  this.playback();this.options.historyChanged?.();
 }
 playback(){
  if(this.timeline.hidden)return;
  const {time,duration,cues}=this.options.read();
  this.playhead.style.left=(duration?time/duration*100:0)+"%";
  [...this.track.children].forEach((clip,i)=>clip.classList.toggle("playing",time>=cues[i]?.start&&time<cues[i]?.end));
  const index=cues.findIndex(cue=>time>=cue.start&&time<cue.end),cue=cues[index];
  const splittable=cue&&CaptionEditing.cutTime(cue,time)!==null&&CaptionEditing.textWords(cue.text).length>1&&cues.length<1000;
  this.split.disabled=this.options.read().disabled||!!this.active||!splittable;
  this.cutTime.textContent=this.clock(time);
  this.actionHint.textContent=splittable?"פיצול של "+this.label(cue,index)+" בנקודת הניגון":cue&&CaptionEditing.textWords(cue.text).length<2?"לפיצול צריך לפחות שתי מילים בקטע.":cues.length>=1000?"אפשר לשמור עד 1,000 קטעים.":"מקמו את הנגן בתוך קטע כדי לפצל.";
 }
 openSplit(){
  const {cues,time,words,disabled}=this.options.read();
  if(disabled||this.active||cues.length>=1000)return;
  const index=cues.findIndex(cue=>time>=cue.start&&time<cue.end),cue=cues[index],at=CaptionEditing.cutTime(cue,time);
  const suggestion=CaptionEditing.suggestSplit(cues,index,time,words);
  if(at===null||!suggestion)return;
  this.options.begin();this.selected=index;this.refresh();
  this.splitDraft={index,at,signature:JSON.stringify(cues)};
  this.splitTitle.textContent="פיצול "+this.label(cue,index);
  this.splitAt.textContent=this.clock(at);
  const matches=CaptionEditing.textWords(cue.text);
  this.splitWord.replaceChildren(...matches.slice(1).map((_,i)=>{
   const option=document.createElement("option");option.value=i+1;
   option.textContent=(i+2)+". "+matches.slice(i+1,i+5).map(word=>word[0]).join(" ");return option;
  }));
  this.splitWord.value=suggestion.wordIndex;
  this.splitSuggestion.textContent=suggestion.aligned?"המילה הוצעה לפי התזמון הקיים. אפשר לבחור מילה אחרת.":"החלוקה המוצעת משוערת לפי המיקום בקטע. אפשר לבחור מילה אחרת.";
  this.showSplitPreview();this.splitDialog.showModal();this.splitWord.focus();
 }
 showSplitPreview(){
  if(!this.splitDraft)return;
  const {index,at}=this.splitDraft,next=CaptionEditing.splitPassage(this.options.read().cues,index,at,+this.splitWord.value);
  this.splitConfirm.disabled=!next;if(!next)return;
  this.splitLeft.textContent=next[index].text;this.splitRight.textContent=next[index+1].text;
  this.splitLeftTime.textContent=this.clock(next[index].start)+" – "+this.clock(at);
  this.splitRightTime.textContent=this.clock(at)+" – "+this.clock(next[index+1].end);
 }
 rememberStructure(){
  const {cues,time}=this.options.read();this.options.edited?.();this.future=[];this.history.push(CaptionShortcuts.mark({cues:cues.map(c=>({...c})),selected:this.selected,time}));
  if(this.history.length>30)this.history.shift();
 }
 confirmSplit(){
  const {cues,disabled}=this.options.read(),draft=this.splitDraft;
  if(disabled||!draft||JSON.stringify(cues)!==draft.signature)return;
  const next=CaptionEditing.splitPassage(cues,draft.index,draft.at,+this.splitWord.value);
  if(!next)return;
  this.rememberStructure();this.selected=draft.index+1;this.options.replace(next,this.selected);
  this.preview(next[this.selected],"start");this.splitDialog.close();this.refresh();
 }
 mergeSelected(direction){
  const {cues,disabled,time}=this.options.read();if(disabled||this.active)return;
  const index=this.selected+(direction<0?-1:0),next=CaptionEditing.mergePassages(cues,index);
  if(!next)return;
  this.options.begin();this.rememberStructure();this.selected=index;this.options.replace(next,index);
  const cue=next[index],pad=Math.min(.18,(cue.end-cue.start)/2);
  this.options.preview(Math.max(cue.start+pad,Math.min(cue.end-pad,time)));this.options.commit();this.refresh();
 }
 setZoom(value){
  if(this.active)return;
  const {duration,cues,time}=this.options.read(),cue=cues[this.selected],center=cue?(cue.start+cue.end)/2:time;
  this.zoom=value;this.zoomControl.value=value;this.refresh();
  this.scroll.scrollLeft=center/duration*this.canvas.clientWidth-this.scroll.clientWidth/2;
 }
 timeAt(x){const rect=this.canvas.getBoundingClientRect();return CaptionEditing.timelineTime(x,rect.left,rect.width,this.options.read().duration);}
 begin(e){
  if(e.button!==0||this.active||this.options.read().disabled)return;
  const target=e.target.closest("button[data-kind]"),{cues,time}=this.options.read();
  const index=target?+target.dataset.index:this.selected,original=cues[index];
  if(target&&!original)return;
  e.preventDefault();target?.focus({preventScroll:true});this.selected=index;
  this.active={id:e.pointerId,kind:target?.dataset.kind||"seek",index,original:original?{start:original.start,end:original.end}:null,
   from:this.timeAt(e.clientX),fromX:e.clientX,x:e.clientX,previousTime:time,changed:false,moved:false};
  this.canvas.setPointerCapture(e.pointerId);this.options.begin();this.refresh();
  if(target)this.preview(original,target.dataset.kind);else this.move();
  this.autoScroll();
 }
 autoScroll(){
  if(!this.active)return;
  const rect=this.scroll.getBoundingClientRect(),x=this.active.x;
  const speed=!this.active.moved?0:x<rect.left+28?-Math.min(18,(rect.left+28-x)/3):x>rect.right-28?Math.min(18,(x-rect.right+28)/3):0;
  if(speed){const previous=this.scroll.scrollLeft;this.scroll.scrollLeft+=speed;if(previous!==this.scroll.scrollLeft)this.move();}
  this.frame=requestAnimationFrame(()=>this.autoScroll());
 }
 move(){
  const a=this.active;if(!a)return;
  if(Math.abs(a.x-a.fromX)>=3)a.moved=true;
  if(a.kind==="seek"){this.options.preview(this.timeAt(a.x));this.playback();return;}
  if(!a.moved)return; // Selecting a millisecond-aligned edge must not round or edit it.
  const {cues,duration}=this.options.read();
  const delta=this.timeAt(a.x)-a.from,next=CaptionEditing.passage(cues,a.index,a.kind,delta,duration,a.original);
  if(!next)return;
  const edge=a.kind==="end"?"end":"start";a.limited=Math.abs(next[edge]-a.original[edge]-delta)>.015;
  const current=cues[a.index];if(current.start===next.start&&current.end===next.end){this.refresh();return;}
  a.changed=next.start!==a.original.start||next.end!==a.original.end;
  this.options.change(a.index,next);this.preview(next,a.kind);this.refresh();
 }
 preview(range,kind){
  const pad=Math.min(.18,(range.end-range.start)/2);
  const offset=this.active?this.active.previousTime-this.active.original.start:pad;
  const time=kind==="end"?range.end-pad:kind==="start"?range.start+pad:Math.max(range.start+pad,Math.min(range.end-pad,range.start+offset));
  this.options.preview(time);this.playback();this.options.historyChanged?.();
 }
 finish(cancel=false){
  const a=this.active;if(!a)return;
  this.active=null;cancelAnimationFrame(this.frame);
  if(this.canvas.hasPointerCapture(a.id))this.canvas.releasePointerCapture(a.id);
  if(a.kind!=="seek"){
   if(cancel){this.options.change(a.index,a.original);this.options.preview(a.previousTime);}
   else if(a.changed){this.options.edited?.();this.future=[];this.history.push(CaptionShortcuts.mark({index:a.index,before:a.original,after:{...this.options.read().cues[a.index]}}));if(this.history.length>30)this.history.shift();}
  }
  this.options.commit();
  this.refresh();
 }
 key(e){
  const target=e.target.closest("button[data-kind]");
  if(!target||!["ArrowLeft","ArrowRight"].includes(e.key)||this.active||this.options.read().disabled)return;
  e.preventDefault();e.stopPropagation();
  const index=+target.dataset.index,kind=target.dataset.kind,{cues,duration}=this.options.read(),cue=cues[index];
  const next=CaptionEditing.passage(cues,index,kind,(e.key==="ArrowLeft"?-1:1)*(e.altKey?.01:e.shiftKey?1:.1),duration);
  if(!next||next.start===cue.start&&next.end===cue.end)return;
  this.selected=index;this.options.edited?.();this.future=[];this.history.push(CaptionShortcuts.mark({index,before:{start:cue.start,end:cue.end},after:next}));
  this.options.begin();this.options.change(index,next);this.preview(next,kind);this.options.commit();this.refresh();
 }
 undoEdit(redo=false){
  if(this.options.read().disabled||this.active)return;
  const stack=redo?this.future:this.history,last=stack.pop();if(!last)return;
  const {cues,time}=this.options.read();
  const current=last.cues?{cues:cues.map(c=>({...c})),selected:this.selected,time}
   :{index:last.index,before:{start:cues[last.index].start,end:cues[last.index].end}};
  (redo?this.history:this.future).push(CaptionShortcuts.mark(current));
  if(last.cues){
   this.selected=last.selected;this.options.begin();this.options.replace(last.cues,last.selected);
   this.options.preview(last.time);this.options.commit();this.refresh();return;
  }
  this.selected=last.index;this.options.begin();this.options.change(last.index,last.before);this.preview(last.before,"start");this.options.commit();this.refresh();
 }
}
if(typeof module!=="undefined")module.exports=CaptionPassageTimeline;

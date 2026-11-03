"use strict";
class CaptionWordTimeline {
 constructor(options){
  this.options=options;this.selected=new Set();this.mode="single";this.anchor=null;this.awaitRange=false;
  this.history=[];this.future=[];this.active=null;this.zoom=1;this.message="";this.collision="borrow";this.continuous=true;
  for(const [name,id] of Object.entries({root:"timelineDetails",scroll:"timelineScroll",canvas:"timelineCanvas",track:"wordTrack",ruler:"ruler",wave:"waveform",playhead:"playhead",zoomControl:"timelineZoom",selection:"wordSelection",status:"wordTimelineStatus",undo:"wordUndo",redo:"wordRedo",from:"wordRangeFrom",to:"wordRangeTo",ranges:"wordRangeControls"}))this[name]=document.getElementById(id);
  this.scrollbar=new CaptionTimelineScrollbar(this.scroll,this.canvas,()=>this.options.read().duration);
  this.canvas.addEventListener("pointerdown",e=>this.begin(e));
  this.canvas.addEventListener("pointermove",e=>{if(this.active?.id===e.pointerId){this.active.x=e.clientX;this.active.y=e.clientY;this.move();}});
  this.canvas.addEventListener("pointerup",e=>{if(this.active?.id===e.pointerId){this.active.x=e.clientX;this.active.y=e.clientY;this.move();this.finish();}});
  for(const name of ["pointercancel","lostpointercapture"])this.canvas.addEventListener(name,()=>this.finish(true));
  this.canvas.addEventListener("keydown",e=>this.key(e));
  document.addEventListener("keydown",e=>{if(e.key==="Escape"&&this.active){e.preventDefault();this.finish(true);}});
  document.querySelectorAll("[data-word-select]").forEach(b=>b.addEventListener("click",()=>this.setMode(b.dataset.wordSelect)));
  this.collisionControl=document.getElementById("wordCollision");
  this.collisionControl.addEventListener("change",()=>{this.collision=this.collisionControl.value;this.message="";this.refresh();});
  this.zoomControl.addEventListener("input",()=>this.setZoom(+this.zoomControl.value));
  document.getElementById("wordFit").addEventListener("click",()=>this.setZoom(1));
  document.getElementById("wordFocus").addEventListener("click",()=>{
   const {rows,duration}=this.options.read(),chosen=CaptionWordEditing.ordered(rows).filter(r=>this.selected.has(r.index));
   if(chosen.length)this.setZoom(Math.min(120,Math.max(1,duration/Math.max(1.5,(chosen.at(-1).end-chosen[0].start)*1.7))));
  });
  for(const control of [this.from,this.to])control.addEventListener("change",()=>{this.selected=new Set(CaptionWordEditing.range(this.options.read().rows,+this.from.value,+this.to.value));this.anchor=+this.from.value;this.awaitRange=false;this.refresh();});
  document.getElementById("wordCloseGaps").addEventListener("click",()=>this.closeGaps());
  this.undo.addEventListener("click",()=>this.restore(false));this.redo.addEventListener("click",()=>this.restore(true));
  new ResizeObserver(()=>this.refresh()).observe(this.scroll);
  new ResizeObserver(()=>{if(!this.root.hidden)document.documentElement.style.setProperty("--passage-height",this.root.offsetHeight+"px");}).observe(this.root);
 }
 reset(){this.selected.clear();this.history=[];this.future=[];this.anchor=null;this.awaitRange=false;this.message="";this.active=null;this.zoom=Math.min(120,Math.max(1,this.options.read().duration/8));this.zoomControl.value=this.zoom;this.initialScroll=true;this.scroll.scrollLeft=0;this.refresh();}
 invalidate(){this.history=[];this.future=[];this.refresh();}
 select(index,reveal=false){
  if(this.active)return;this.selected=new Set([index]);this.anchor=index;this.refresh();
  if(reveal){const {rows,duration}=this.options.read(),row=rows.find(r=>r.index===index);if(row)this.scroll.scrollLeft=(row.start+row.end)/2/duration*this.canvas.clientWidth-this.scroll.clientWidth/2;}
 }
 focusWord(index,kind="move"){this.track.querySelector('button[data-index="'+index+'"][data-kind="'+kind+'"]')?.focus({preventScroll:true});}
 setMode(mode){
  if(this.active||this.options.read().disabled)return;
  this.mode=mode;const {rows}=this.options.read(),first=[...this.selected][0]??CaptionWordEditing.ordered(rows)[0]?.index;
  if(mode==="single")this.selected=new Set(first===undefined?[]:[first]);
  if(mode==="tail")this.selected=new Set(CaptionWordEditing.tail(rows,first));
  this.anchor=first;this.awaitRange=mode==="range";this.message="";this.refresh();
 }
 choose(index,e){
  const {rows}=this.options.read();
  if(e.shiftKey&&this.anchor!==null){this.selected=new Set(CaptionWordEditing.range(rows,this.anchor,index));return false;}
  if(e.ctrlKey||e.metaKey||this.mode==="multiple"){
   const had=this.selected.has(index);this.selected.add(index);if(this.anchor===null)this.anchor=index;return had;
  }
  if(this.mode==="tail"){if(!this.selected.has(index))this.selected=new Set(CaptionWordEditing.tail(rows,index));this.anchor=[...this.selected][0];return false;}
  if(this.mode==="range"){
   if(this.awaitRange&&this.anchor!==null){this.selected=new Set(CaptionWordEditing.range(rows,this.anchor,index));this.awaitRange=false;}
   else if(!this.selected.has(index)){this.selected=new Set([index]);this.anchor=index;this.awaitRange=true;}
   return false;
  }
  if(!this.selected.has(index))this.selected=new Set([index]);this.anchor=index;return false;
 }
 refresh(){
  if(this.root.hidden)return;
  const {rows,duration,disabled,wave,saving}=this.options.read();if(!duration)return;
  const order=CaptionWordEditing.ordered(rows),ids=order.map(r=>r.index);
  this.selected=new Set([...this.selected].filter(id=>ids.includes(id)));
  const width=Math.max(1,this.scroll.clientWidth)*this.zoom,scale=width/duration;this.canvas.style.width=width+"px";
  if(this.track.children.length!==rows.length){
   this.track.replaceChildren(...rows.map(row=>{
    const clip=document.createElement("div");clip.className="passage-clip word-clip";clip.dataset.index=row.index;
    for(const kind of ["start","move","end"]){
     const b=document.createElement("button");b.type="button";b.dataset.kind=kind;b.dataset.index=row.index;b.className=kind==="move"?"passage-body":"passage-handle "+kind;
     if(kind==="move"){const title=document.createElement("strong");title.dir="auto";b.append(title,document.createElement("small"));}else b.textContent="Ⅱ";
     clip.append(b);
    }return clip;
   }));
  }
  rows.forEach((row,i)=>{
   const clip=this.track.children[i],valid=CaptionWordEditing.valid(row,duration),picked=this.selected.has(row.index);clip.hidden=!valid;if(!valid)return;
   const size=(row.end-row.start)*scale;clip.style.left=row.start*scale+"px";clip.style.width=Math.max(2,size)+"px";
   clip.classList.toggle("selected",picked);clip.classList.toggle("compact",size<120);clip.classList.toggle("tiny",size<44);
   clip.classList.toggle("swap-target",this.active?.swap===row.index);clip.classList.toggle("dragging",!!this.active?.moved&&picked);
   clip.classList.toggle("borrow-target",this.active?.borrowed?.includes(row.index)||false);
   clip.querySelector("strong").textContent=row.text;clip.querySelector("small").textContent=CaptionTimecode.format(row.start);
   for(const b of clip.children){const kind=b.dataset.kind;b.disabled=disabled||kind!=="move"&&this.selected.size>1;
    b.setAttribute("aria-label",(kind==="move"?"הזזת המילה ":kind==="start"?"תחילת המילה ":"סיום המילה ")+row.text+" ("+(row.index+1)+")");
    if(kind==="move")b.setAttribute("aria-pressed",picked);b.title=row.text+" · "+CaptionTimecode.format(row.start)+" – "+CaptionTimecode.format(row.end);
   }
  });
  const chosen=order.filter(row=>this.selected.has(row.index)),first=chosen[0],last=chosen.at(-1),locked=disabled||!!this.active;
  this.selection.textContent=chosen.length===1?'״'+first.text+'״ · \u2066'+CaptionTimecode.format(first.start)+' – '+CaptionTimecode.format(first.end)+'\u2069':chosen.length?chosen.length+' מילים · ״'+first.text+'״ עד ״'+last.text+'״':"בחרו מילים על הציר";
  for(const b of document.querySelectorAll("[data-word-select]")){b.setAttribute("aria-pressed",b.dataset.wordSelect===this.mode);b.disabled=locked;}
  const key=order.map(r=>r.index+":"+r.text).join("\n");
  if(this.rangeKey!==key){this.rangeKey=key;for(const select of [this.from,this.to])select.replaceChildren(...order.map(row=>{const o=document.createElement("option");o.value=row.index;o.textContent=(row.index+1)+". "+row.text;return o;}));}
  if(first){this.from.value=first.index;this.to.value=last.index;}
  this.ranges.hidden=this.mode!=="range";this.from.disabled=locked;this.to.disabled=locked;
  this.undo.disabled=locked||!this.history.length;this.redo.disabled=locked||!this.future.length;
  this.zoomControl.disabled=locked;
  this.collisionControl.disabled=locked;
  for(const id of ["wordFocus","wordFit","wordCloseGaps"])document.getElementById(id).disabled=locked;
  const a=this.active;
  this.status.textContent=saving?"שומרים את התזמון…":a?.swap!==undefined&&a.swap!==null?"שחררו להחלפת הזמנים · Esc לביטול":a?.moved?(a.limited?(this.collision==="borrow"?"הגעתם לגבול הסרטון או למשך המזערי של המילה הסמוכה":"הגעתם למילה סמוכה או לגבול הסרטון"):a.borrowed?.length?"זמן המילה הסמוכה מתקצר יחד עם הגרירה · שחררו לשמירה":"תצוגה חיה · שחררו לשמירה · Esc לביטול"):this.message||
   (disabled?"שמרו או בטלו את התיקון הפתוח כדי לגרור בציר":this.mode==="multiple"?"לחצו להוספת מילים לבחירה, וגררו אחת מהן":this.mode==="tail"?"בחרו מילה — היא וכל המילים שאחריה יזוזו יחד":this.mode==="range"?"בחרו מילת התחלה וסיום, או לחצו על שתיהן בציר":this.collision==="borrow"?"גררו מילה או קצה שלה לתוך השכנה — הגבול ביניהן יתעדכן יחד":"גררו מילה להזזה · על מילה אחרת: החלפת זמנים · הקצוות משנים התחלה וסיום");
  if(this.rulerKey!==width+":"+duration){
   this.rulerKey=width+":"+duration;this.ruler.replaceChildren();
   const step=[.1,.2,.5,1,2,5,10,15,30,60,120,300].find(v=>v*scale>=65)||Math.ceil(duration/10);
   for(let t=0;t<=duration;t+=step){const tick=document.createElement("span");tick.style.left=t*scale+"px";tick.textContent=CaptionTimecode.format(t).replace(/\.00$/,"");this.ruler.append(tick);}
  }
  if(this.lastWave!==wave){this.lastWave=wave;this.wave.replaceChildren();if(wave?.length){const path=document.createElementNS("http://www.w3.org/2000/svg","path");path.setAttribute("d",wave.map((v,i)=>`M${(i+.5)*1200/wave.length} ${24-Math.max(.5,v*20)}v${Math.max(1,v*40)}`).join(""));this.wave.append(path);}}
  if(this.initialScroll&&order.length){this.scroll.scrollLeft=Math.max(0,order[0].start*scale-this.scroll.clientWidth*.15);this.initialScroll=false;}
  this.playback();this.scrollbar?.sync();this.options.historyChanged?.();
 }
 playback(){if(this.root.hidden)return;const {rows,time,duration}=this.options.read();this.playhead.style.left=(duration?time/duration*100:0)+"%";[...this.track.children].forEach((clip,i)=>clip.classList.toggle("playing",time>=rows[i]?.start&&time<rows[i]?.end));}
 setZoom(value){if(this.active)return;const {rows,time,duration}=this.options.read(),chosen=CaptionWordEditing.ordered(rows).filter(r=>this.selected.has(r.index));const center=chosen.length?(chosen[0].start+chosen.at(-1).end)/2:time;this.zoom=value;this.zoomControl.value=value;this.refresh();this.scroll.scrollLeft=center/duration*this.canvas.clientWidth-this.scroll.clientWidth/2;}
 timeAt(x){const r=this.canvas.getBoundingClientRect();return CaptionEditing.timelineTime(x,r.left,r.width,this.options.read().duration);}
 begin(e){
  if(e.button!==0||this.active||this.options.read().disabled)return;
  const target=e.target.closest("button[data-kind]"),{rows,time}=this.options.read();
  if(target?.disabled)return;
  e.preventDefault();target?.focus({preventScroll:true});
  const index=target?+target.dataset.index:null,kind=target?.dataset.kind||"seek";
  const removeOnClick=target?this.choose(index,e):false;
  if(kind!=="move"&&kind!=="seek")this.selected=new Set([index]);
  this.active={id:e.pointerId,index,kind,rows:CaptionWordEditing.copy(rows),previousTime:time,from:this.timeAt(e.clientX),fromX:e.clientX,x:e.clientX,y:e.clientY,moved:false,removeOnClick,swap:null,borrowed:[]};
  this.canvas.setPointerCapture(e.pointerId);this.options.begin();this.message="";
  if(target)this.preview(rows.find(r=>r.index===index),kind);else this.options.preview(this.timeAt(e.clientX));
  this.refresh();this.autoScroll();
 }
 autoScroll(){if(!this.active)return;const r=this.scroll.getBoundingClientRect(),x=this.active.x;const speed=!this.active.moved?0:x<r.left+28?-Math.min(18,(r.left+28-x)/3):x>r.right-28?Math.min(18,(x-r.right+28)/3):0;if(speed){const old=this.scroll.scrollLeft;this.scroll.scrollLeft+=speed;if(old!==this.scroll.scrollLeft)this.move();}this.frame=requestAnimationFrame(()=>this.autoScroll());}
 move(){
  const a=this.active;if(!a)return;if(Math.abs(a.x-a.fromX)>=3)a.moved=true;
  if(a.kind==="seek"){this.options.preview(this.timeAt(a.x));return;}if(!a.moved)return;
  const {duration}=this.options.read(),at=this.timeAt(a.x),track=this.track.getBoundingClientRect();
  const target=this.collision==="swap"&&a.kind==="move"&&this.selected.size===1&&a.y>=track.top&&a.y<=track.bottom?a.rows.find(r=>r.index!==a.index&&at>=r.start&&at<r.end):null;
  a.swap=target?.index??null;
  const result=target?{rows:(this.continuous?CaptionWordEditing.fillGaps(CaptionWordEditing.swap(a.rows,a.index,target.index,duration),duration):CaptionWordEditing.swap(a.rows,a.index,target.index,duration)),limited:false}:CaptionWordEditing.move(a.rows,[...this.selected],a.kind,at-a.from,duration,this.collision==="borrow",this.continuous);
  if(!result?.rows)return;a.limited=result.limited;a.borrowed=result.borrowed||[];
  this.options.change(result.rows,!!target);this.preview(result.rows.find(r=>r.index===a.index),a.kind);this.refresh();
 }
 preview(row,kind){if(!row)return;const pad=Math.min(.06,(row.end-row.start)/2);this.options.preview(kind==="end"?row.end-pad:row.start+pad);}
 async finish(cancel=false){
  const a=this.active;if(!a)return;this.active=null;cancelAnimationFrame(this.frame);
  if(this.canvas.hasPointerCapture(a.id))this.canvas.releasePointerCapture(a.id);
  if(a.kind==="seek"){this.options.discard();this.refresh();return;}
  if(cancel){this.options.change(a.rows,false);this.options.discard();this.options.preview(a.previousTime);this.refresh();return;}
  if(!a.moved){if(a.removeOnClick)this.selected.delete(a.index);else if(this.mode==="tail")this.selected=new Set(CaptionWordEditing.tail(a.rows,a.index));this.options.discard();this.refresh();return;}
  const after=CaptionWordEditing.copy(this.options.read().rows);
  if(!CaptionWordEditing.changed(a.rows,after).length){this.options.discard();this.refresh();return;}
  const ok=await this.options.commit(a.swap!==null);
  if(ok){this.options.edited?.();this.history.push(CaptionShortcuts.mark({before:a.rows,after,selected:[...this.selected],time:a.previousTime}));if(this.history.length>30)this.history.shift();this.future=[];this.message=a.swap!==null?"הזמנים הוחלפו ונשמרו · אפשר לבטל":a.borrowed.length?"הגבולות נשמרו יחד עם המילים הסמוכות · אפשר לבטל":"התזמון נשמר · אפשר לבטל";}
  else this.message="השינוי לא נשמר. התזמון הקודם הוחזר — אפשר לנסות שוב.";
  this.refresh();this.focusWord(a.index,a.kind);
 }
 async key(e){
  const target=e.target.closest("button[data-kind]");if(!target||this.active||this.options.read().disabled)return;
  const index=+target.dataset.index;
  if(e.key==="Enter"){e.preventDefault();const remove=this.choose(index,e);if(remove)this.selected.delete(index);this.refresh();return;}
  if(!["ArrowLeft","ArrowRight"].includes(e.key))return;e.preventDefault();e.stopPropagation();
  if(!this.selected.has(index))this.selected=new Set([index]);
  const {rows,duration,time}=this.options.read(),before=CaptionWordEditing.copy(rows),kind=target.dataset.kind;
  const result=CaptionWordEditing.move(rows,kind==="move"?[...this.selected]:[index],kind,(e.key==="ArrowLeft"?-1:1)*(e.altKey?.01:e.shiftKey?1:.1),duration,this.collision==="borrow",this.continuous);
  if(!result||!CaptionWordEditing.changed(rows,result.rows).length)return;
  this.options.begin();this.options.change(result.rows,false);this.preview(result.rows.find(r=>r.index===index),kind);
  if(await this.options.commit(false)){this.options.edited?.();this.history.push(CaptionShortcuts.mark({before,after:result.rows,selected:[...this.selected],time}));if(this.history.length>30)this.history.shift();this.future=[];this.message="התזמון נשמר · אפשר לבטל";}this.refresh();this.focusWord(index,kind);
 }
 async closeGaps(){
  const {rows,duration,time,disabled}=this.options.read();if(disabled||this.active||!rows.length)return;
  const before=CaptionWordEditing.copy(rows),after=CaptionWordEditing.fillGaps(rows,duration);
  if(!CaptionWordEditing.changed(before,after).length)return;
  this.options.begin();this.options.change(after,false);
  if(await this.options.commit(false,"gap_fill")){
   this.options.edited?.();this.history.push(CaptionShortcuts.mark({before,after,selected:[...this.selected],time,gapFill:true}));
   if(this.history.length>30)this.history.shift();this.future=[];
   this.message="הרווחים הושלמו עד המילה הבאה · Ctrl+Z לביטול";
  }
  this.refresh();
 }
 async restore(redo){
  if(this.active||this.options.read().disabled)return;const stack=redo?this.future:this.history,item=stack.at(-1);if(!item)return;
  this.selected=new Set(item.selected);this.options.begin();this.options.change(redo?item.after:item.before,true);this.options.preview(item.time);
  if(await this.options.commit(true,item.gapFill?(redo?"gap_fill":"gap_fill_undo"):"manual")){stack.pop();(redo?this.history:this.future).push(CaptionShortcuts.mark(item));this.message=redo?"השינוי הוחזר":"השינוי בוטל";}this.refresh();
 }
}
if(typeof module!=="undefined")module.exports=CaptionWordTimeline;

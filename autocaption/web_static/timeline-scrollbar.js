"use strict";
// Navigate the visible window only. Word timings, selection and playback stay put.
class CaptionTimelineScrollbar {
 constructor(scroll,canvas,readDuration){
  this.scroll=scroll;this.readDuration=readDuration;
  this.range=document.getElementById("wordScrollPosition");
  this.before=document.getElementById("wordScrollLeft");
  this.after=document.getElementById("wordScrollRight");
  this.window=document.getElementById("wordScrollWindow");
  this.range.addEventListener("input",()=>{this.scroll.scrollLeft=+this.range.value*this.limit();this.sync();});
  this.before.addEventListener("click",()=>this.shift(-.8));
  this.after.addEventListener("click",()=>this.shift(.8));
  document.getElementById("wordScrollBar").addEventListener("keydown",e=>{
   if(e.altKey||e.ctrlKey||e.metaKey)return;
   const steps={ArrowLeft:-.1,ArrowRight:.1,PageUp:-.8,PageDown:.8};
   if(!(e.key in steps)&&e.key!=="Home"&&e.key!=="End")return;
   e.preventDefault();e.stopPropagation();
   if(e.key==="Home"||e.key==="End"){this.scroll.scrollLeft=e.key==="Home"?0:this.limit();this.sync();}
   else this.shift(steps[e.key]);
  });
  scroll.addEventListener("scroll",()=>this.sync(),{passive:true});
  this.observer=new ResizeObserver(()=>this.sync());
  for(const node of [scroll,canvas,this.range])this.observer.observe(node);
  this.sync();
 }
 limit(){return Math.max(0,this.scroll.scrollWidth-this.scroll.clientWidth);}
 shift(pages){this.scroll.scrollLeft+=this.scroll.clientWidth*pages;this.sync();}
 sync(){
  const width=this.scroll.clientWidth,total=this.scroll.scrollWidth,duration=this.readDuration();
  if(!width||!total)return;
  const limit=this.limit(),left=Math.max(0,Math.min(limit,this.scroll.scrollLeft));
  this.range.disabled=limit<=1;this.range.value=limit?left/limit:0;
  this.before.disabled=left<=1;this.after.disabled=left>=limit-1;
  const thumb=Math.min(this.range.clientWidth,Math.max(48,this.range.clientWidth*width/total));
  this.range.style.setProperty("--scroll-thumb-width",thumb+"px");
  const start=CaptionTimecode.format(duration*left/total),end=CaptionTimecode.format(Math.min(duration,duration*(left+width)/total));
  this.window.textContent=start+" – "+end;
  this.range.setAttribute("aria-valuetext",limit>1?"מוצג מ־"+start+" עד "+end:"כל הסרטון מוצג");
 }
}

"use strict";
const CaptionWordEditing=(()=>{
 const ordered=rows=>[...rows].sort((a,b)=>(Number.isFinite(a.start)?a.start:Infinity)-(Number.isFinite(b.start)?b.start:Infinity)||a.index-b.index);
 const valid=(row,duration)=>row&&Number.isFinite(row.start)&&Number.isFinite(row.end)&&row.start>=0&&row.end<=duration+.000001&&Math.round(row.end*100)>Math.round(row.start*100);
 const copy=rows=>rows.map(row=>({...row}));
 function fillGaps(rows,duration){
  const next=copy(rows),sorted=ordered(next);
  // Missing/invalid words must be corrected, never bridged over or hidden.
  if(sorted.some(row=>!valid(row,duration)))return next;
  for(let i=0;i+1<sorted.length;i++)if(sorted[i].end<sorted[i+1].start)sorted[i].end=sorted[i+1].start;
  return next;
 }
 function range(rows,from,to){
  const ids=ordered(rows).map(row=>row.index),a=ids.indexOf(from),b=ids.indexOf(to);
  return a<0||b<0?[]:ids.slice(Math.min(a,b),Math.max(a,b)+1);
 }
 function tail(rows,from){const ids=ordered(rows).map(row=>row.index),at=ids.indexOf(from);return at<0?[]:ids.slice(at);}
 function swap(rows,from,to,duration){
  const a=rows.find(row=>row.index===from),b=rows.find(row=>row.index===to);
  if(from===to||!valid(a,duration)||!valid(b,duration))return null;
  return rows.map(row=>row.index===from?{...row,start:b.start,end:b.end}:row.index===to?{...row,start:a.start,end:a.end}:{...row});
 }
 // Leave at least one rendered hundredth in a neighbour, including existing
 // sub-hundredth alignment anchors. A word can be shortened, never consumed.
 const earliestEnd=row=>Math.min(row.end,(Math.round(row.start*100)+1)/100);
 const latestStart=row=>Math.max(row.start,(Math.round(row.end*100)-1)/100);
 function move(rows,ids,kind,delta,duration,borrow=false,continuous=false){
  const selected=new Set(ids),sorted=ordered(rows);
  if(!selected.size||!Number.isFinite(delta)||!Number.isFinite(duration)||duration<=0||
   ids.some(id=>!valid(rows.find(row=>row.index===id),duration)))return null;
  const finish=next=>{
   const byId=new Map(next.map(row=>[row.index,row]));
   if(borrow)for(let i=0;i<sorted.length;i++){
    const row=sorted[i];if(selected.has(row.index))continue;
    const current=byId.get(row.index),before=sorted[i-1],after=sorted[i+1];
    if(before&&selected.has(before.index))current.start=Math.max(current.start,byId.get(before.index).end);
    if(after&&selected.has(after.index))current.end=Math.min(current.end,byId.get(after.index).start);
   }
   // A continuous end handle edits the shared boundary in both directions.
   // Shortening a word gives that time to the next word instead of reopening a gap.
   if(continuous&&kind==="end"){
    const at=sorted.findIndex(row=>row.index===ids[0]),after=sorted[at+1];
    if(after)byId.get(after.index).start=byId.get(ids[0]).end;
   }
   if(continuous){next=fillGaps(next,duration);for(const row of next)byId.set(row.index,row);}
   // Validate in the original chronological order: borrowing cannot reorder,
   // overlap, erase or silently skip any word, even at fractional boundaries.
   if(sorted.some((row,i)=>!valid(byId.get(row.index),duration)||i&&byId.get(row.index).start<byId.get(sorted[i-1].index).end))return null;
   return {rows:next,borrowed:next.filter((row,i)=>!selected.has(row.index)&&(row.start!==rows[i].start||row.end!==rows[i].end)).map(row=>row.index)};
  };
  if(kind!=="move"){
   if(ids.length!==1||!["start","end"].includes(kind))return null;
   const pos=sorted.findIndex(row=>row.index===ids[0]),row=sorted[pos];
   const before=sorted[pos-1],after=sorted[pos+1];
   const next=CaptionEditing.edge(row.start,row.end,kind,row[kind]+delta,
    before?(borrow?earliestEnd(before):before.end):0,after?(borrow?latestStart(after):after.start):duration);
   const result=next&&finish(rows.map(r=>r.index===row.index?{...r,...next}:{...r}));
   return result?{...result,limited:Math.abs(next[kind]-row[kind]-delta)>.015}:null;
  }
  let low=-Infinity,high=Infinity;
  for(let i=0;i<sorted.length;i++){
   const row=sorted[i];if(!selected.has(row.index))continue;
   low=Math.max(low,-row.start);high=Math.min(high,duration-row.end);
   if(i&&!selected.has(sorted[i-1].index))low=Math.max(low,(borrow?earliestEnd(sorted[i-1]):sorted[i-1].end)-row.start);
   if(i+1<sorted.length&&!selected.has(sorted[i+1].index))high=Math.min(high,(borrow?latestStart(sorted[i+1]):sorted[i+1].start)-row.end);
  }
  if(!Number.isFinite(low)||!Number.isFinite(high)||low>high)return null;
  const offset=Math.max(low,Math.min(high,Math.round(delta*100)/100));
  // A single shared offset keeps durations and gaps intact, including millisecond anchors.
  const result=finish(rows.map(row=>selected.has(row.index)?{...row,start:+(row.start+offset).toFixed(9),end:+(row.end+offset).toFixed(9)}:{...row}));
  return result?{...result,limited:Math.abs(offset-delta)>.015}:null;
 }
 const changed=(before,after)=>after.filter((row,i)=>row.start!==before[i].start||row.end!==before[i].end);
 return {ordered,range,tail,swap,move,copy,changed,valid,fillGaps};
})();
if(typeof module!=="undefined")module.exports=CaptionWordEditing;

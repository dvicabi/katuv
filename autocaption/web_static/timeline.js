(function(root){
 "use strict";
 function group(rows,maxWords,duration){
  const lines=[];let current=[];
  const flush=()=>{if(current.length){lines.push(current);current=[];}};
  for(const row of rows){
   // A corrupt legacy row must never blank the other captions. The server
   // completes missing timings; it still retains every source word in its report.
   if(!Number.isFinite(row.start)||!Number.isFinite(row.end)||row.start<0||row.end<=row.start||row.end>duration+.001){flush();continue;}
   if(current.length>=maxWords)flush();
   current.push(row);
  }
  flush();return lines;
 }
 function firstTime(lines){
  if(!lines.length)return null;
  const row=lines[0][0];
  return row.start+Math.min(.12,(row.end-row.start)/2);
 }
 // Match the renderer's splitlines(): a retained final line separator does
 // not add a blank display row. Keep it in the stored cue for exact rejoining.
 function segments(cues){return cues.map((cue,index)=>[{...cue,text:cue.text.replace(/\r?\n$/u,""),index,block:true}]);}
 const api={group,firstTime,segments};
 if(typeof module!=="undefined"&&module.exports)module.exports=api;
 else root.CaptionTimeline=api;
})(typeof globalThis!=="undefined"?globalThis:this);

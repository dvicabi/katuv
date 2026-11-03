"use strict";
// All edits are bounded to the video and to neighbouring immutable words.
const CaptionEditing = (() => {
 const clamp=(value,min,max)=>Math.max(min,Math.min(max,value));
 const hundredth=value=>Math.round((value+Number.EPSILON)*100)/100;
 function edge(start,end,which,value,min,max){
  const low=which==="start"?min:start+.01,high=which==="start"?end-.01:max;
  const a=Math.ceil((low-1e-9)*100)/100,b=Math.floor((high+1e-9)*100)/100;
  if(![start,end,value,min,max].every(Number.isFinite)||a>b)return null;
  const next=clamp(hundredth(value),a,b);
  return which==="start"?{start:next,end}:{start,end:next};
 }
 function shift(start,end,delta,min,max){
  if(![start,end,delta,min,max].every(Number.isFinite)||end<=start||end-start>max-min)return null;
  const offset=clamp(delta,min-start,max-end);
  return {start:+(start+offset).toFixed(6),end:+(end+offset).toFixed(6)};
 }
 function snap(value,pixels,length,enabled=true){return enabled&&Math.abs(value-.5)*length<=pixels?.5:value;}
 // Move a whole passage or trim one edge without changing adjacent passages.
 function passage(cues,index,kind,delta,duration,original=cues[index]){
  if(!original||!Number.isFinite(delta)||!Number.isFinite(duration))return null;
  const min=index?cues[index-1].end:0,max=index+1<cues.length?cues[index+1].start:duration;
  if(!Number.isFinite(min)||!Number.isFinite(max)||min>max)return null;
  if(kind==="move")return shift(original.start,original.end,hundredth(delta),min,max);
  if(kind!=="start"&&kind!=="end")return null;
  return edge(original.start,original.end,kind,original[kind]+delta,min,max);
 }
 function timelineTime(clientX,left,width,duration){
  return width>0&&duration>0?clamp((clientX-left)/width*duration,0,duration):0;
 }
 const textWords=text=>[...text.matchAll(/\S+/gu)];
 function cutTime(cue,time){
  if(!cue||![cue.start,cue.end,time].every(Number.isFinite))return null;
  const at=hundredth(time);
  return at>cue.start&&at<cue.end&&Math.round(at*100)>Math.round(cue.start*100)&&Math.round(at*100)<Math.round(cue.end*100)?at:null;
 }
 function splitPassage(cues,index,time,wordIndex){
  const cue=cues[index],at=cutTime(cue,time);
  if(at===null||cues.length>=1000)return null;
  const matches=textWords(cue.text);
  if(!Number.isInteger(wordIndex)||wordIndex<1||wordIndex>=matches.length)return null;
  // Keep the separator on the left so merging the two parts restores even
  // the original whitespace, line breaks, punctuation and Unicode exactly.
  const offset=matches[wordIndex].index;
  const left={...cue,text:cue.text.slice(0,offset),end:at};
  const right={...cue,text:cue.text.slice(offset),start:at,label:((cue.label||"קטע "+(index+1))+" · המשך").slice(0,80)};
  return [...cues.slice(0,index),left,right,...cues.slice(index+1)].map(c=>({...c}));
 }
 function mergePassages(cues,index){
  const left=cues[index],right=cues[index+1];
  if(!left||!right||left.end>right.start)return null;
  const separator=/\s$/u.test(left.text)||/^\s/u.test(right.text)?"":"\n";
  const text=left.text+separator+right.text;
  if(text.length>50000)return null;
  return [...cues.slice(0,index),{...left,text,end:right.end},...cues.slice(index+2)].map(c=>({...c}));
 }
 function suggestSplit(cues,index,time,rows=[]){
  const cue=cues[index],matches=cue?textWords(cue.text):[];
  if(matches.length<2||cutTime(cue,time)===null)return null;
  const tokens=cues.flatMap(c=>textWords(c.text).map(w=>w[0]));
  const offset=cues.slice(0,index).reduce((n,c)=>n+textWords(c.text).length,0),anchors=rows.slice(offset,offset+matches.length);
  // Only consult timings when they still belong to this exact text and range.
  // Moved passages or missing timings use a clearly labelled proportional guess.
  const aligned=rows.length===tokens.length&&rows.every((row,i)=>row.text===tokens[i])&&anchors.every((row,i)=>
   Number.isFinite(row.start)&&Number.isFinite(row.end)&&row.end>row.start&&row.start>=cue.start-.01&&row.end<=cue.end+.01&&(!i||row.start>=anchors[i-1].end));
  let wordIndex=clamp(Math.round((time-cue.start)/(cue.end-cue.start)*matches.length),1,matches.length-1);
  if(aligned){wordIndex=1;for(let i=2;i<anchors.length;i++)if(Math.abs(anchors[i].start-time)<Math.abs(anchors[wordIndex].start-time))wordIndex=i;}
  return {wordIndex,aligned};
 }
 return {edge,shift,snap,passage,timelineTime,textWords,cutTime,splitPassage,mergePassages,suggestSplit};
})();
if(typeof module!=="undefined")module.exports=CaptionEditing;

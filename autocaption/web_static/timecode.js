"use strict";
const CaptionTimecode=(()=>{
 const stored=new WeakMap();
 function format(seconds){
  if(!Number.isFinite(seconds)||seconds<0)return "";
  const cs=Math.round(seconds*100+1e-8);
  return String(Math.floor(cs/6000)).padStart(2,"0")+":"+String(Math.floor(cs/100)%60).padStart(2,"0")+"."+String(cs%100).padStart(2,"0");
 }
 function parse(text){
  const match=/^(\d{1,6}):([0-5]\d)(?:[.,](\d{1,2}))?$/.exec(text.trim());
  if(!match)return NaN;
  return (+match[1]*6000 + +match[2]*100 + +(match[3]||"").padEnd(2,"0"))/100;
 }
 function write(input,seconds){
  input.value=format(seconds);stored.set(input,{text:input.value,seconds});
  input.removeAttribute("aria-invalid");
 }
 function read(input,min=0,max=Infinity){
  const previous=stored.get(input);
  let seconds=previous&&previous.text===input.value?previous.seconds:parse(input.value);
  // Keep untouched alignment precision. A typed hundredth at a neighbouring
  // boundary or video end resolves to that exact bound, avoiding invisible overlaps.
  if(Number.isFinite(seconds)){
   if(seconds<min&&format(seconds)===format(min))seconds=min;
   if(seconds>max&&format(seconds)===format(max))seconds=max;
  }
  return Number.isFinite(seconds)?seconds:NaN;
 }
 function canTogglePlayback({tag="",type="",timecode=false,editable=false,modal=false,composing=false,modified=false}={}){
  if(editable||modal||composing||modified||["TEXTAREA","SELECT"].includes(tag))return false;
  return tag!=="INPUT"||timecode||type==="range";
 }
 return {format,parse,write,read,canTogglePlayback};
})();
if(typeof module!=="undefined")module.exports=CaptionTimecode;

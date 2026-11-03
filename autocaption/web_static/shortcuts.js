"use strict";
const CaptionShortcuts=(()=>{
 let sequence=0;
 // Non-enumerable metadata never becomes a project setting or a saved word.
 function mark(item){Object.defineProperty(item,"editOrder",{value:++sequence,writable:true,configurable:true});return item;}
 function action({code="",key="",ctrlKey=false,metaKey=false,shiftKey=false,altKey=false,editable=false,modal=false,composing=false}={}){
  if(editable||modal||composing)return null;
  const command=ctrlKey||metaKey;
  if(command&&!altKey){
   if(code==="KeyZ"||!code&&key.toLowerCase()==="z")return {kind:shiftKey?"redo":"undo"};
   if(code==="KeyY"||!code&&key.toLowerCase()==="y")return {kind:"redo"};
   return null;
  }
  if(command)return null;
  if(["ArrowLeft","ArrowRight"].includes(key))return {kind:"seek",delta:(key==="ArrowLeft"?-1:1)*(altKey?.01:shiftKey?5:.5)};
  if(altKey||shiftKey)return null;
  if(code==="KeyJ"||code==="KeyL")return {kind:"seek",delta:code==="KeyJ"?-5:5};
  if(code==="KeyK")return {kind:"play"};
  if(key==="Home"||key==="End")return {kind:key==="Home"?"first":"last"};
  if(key==="PageUp"||key==="PageDown")return {kind:"word",direction:key==="PageUp"?-1:1};
  if(code==="BracketLeft"||code==="BracketRight")return {kind:"capture",edge:code==="BracketLeft"?"start":"end"};
  return null;
 }
 return {mark,action};
})();
if(typeof module!=="undefined")module.exports=CaptionShortcuts;

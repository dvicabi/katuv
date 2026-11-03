global.CaptionShortcuts=require('../autocaption/web_static/shortcuts.js');
const assert=require('node:assert/strict');
global.CaptionEditing=require('../autocaption/web_static/editing.js');
global.CaptionWordEditing=require('../autocaption/web_static/word-editing.js');
const W=CaptionWordEditing;
const source=['שלום,','Reels','2026','שלום!','כן'].map((text,index)=>({index,text,start:1+index*2+.003,end:1.503+index*2,status:'aligned'}));
const snapshot=JSON.stringify(source);
assert.deepEqual(W.range(source,3,1),[1,2,3]);assert.deepEqual(W.tail(source,2),[2,3,4]);
let result=W.move(source,[1,2,3],'move',.4,12);
assert.deepEqual(result.rows.map(r=>r.text),source.map(r=>r.text));
for(const i of [1,2,3]){assert.ok(Math.abs(result.rows[i].start-source[i].start-.4)<1e-8);assert.ok(Math.abs(result.rows[i].end-source[i].end-.4)<1e-8);}
assert.deepEqual(result.rows[0],source[0]);assert.deepEqual(result.rows[4],source[4]);
assert.equal(W.move(source,[0,2],'move',5,12).rows[0].end,source[1].start);
assert.equal(W.move(source,[0,2],'move',-5,12).rows[0].start,0);
assert.equal(W.move(source,[1,2],'end',.1,12),null);
assert.equal(W.move([{...source[0],start:null}],[0],'move',1,12),null);
result=W.move(source,[2],'start',-10,12);assert.equal(result.rows[2].start,3.51); // First valid hundredth after the neighbour.
const swapped=W.swap(source,0,3,12);
assert.equal(swapped[0].start,source[3].start);assert.equal(swapped[3].end,source[0].end);
assert.deepEqual(swapped.map(r=>r.text),source.map(r=>r.text));
assert.deepEqual(W.ordered(swapped).map(r=>r.index),[3,1,2,0,4]);
assert.deepEqual(W.range(swapped,3,0),[3,1,2,0]);assert.deepEqual(W.tail(swapped,0),[0,4]);
assert.deepEqual(W.swap(swapped,0,3,12),source);
assert.equal(JSON.stringify(source),snapshot);
// Borrowing shares only the crossed boundary, in either direction. Text and
// the untouched side of each neighbour must remain exact.
const touching=['אחת,','שתיים','שלוש!'].map((text,index)=>({index,text,start:index+.5,end:index+1.5,status:'aligned'}));
const times=rows=>rows.map(({start,end})=>[start,end]);
const filled=W.fillGaps(source,12);
assert.equal(JSON.stringify(source),snapshot);
assert.deepEqual(filled.map(r=>[r.index,r.text,r.start]),source.map(r=>[r.index,r.text,r.start]));
assert.equal(filled.at(-1).end,source.at(-1).end); // No invented trailing speech.
for(let i=0;i+1<filled.length;i++)assert.equal(filled[i].end,filled[i+1].start);
assert.deepEqual(W.fillGaps(filled,12),filled);
const missing=source.map((r,i)=>i===2?{...r,start:null,end:null}:r);
assert.deepEqual(W.fillGaps(missing,12),missing); // Never bridge an unaligned token.
const reorderedFilled=W.ordered(W.fillGaps(swapped,12));
for(let i=0;i+1<reorderedFilled.length;i++)assert.equal(reorderedFilled[i].end,reorderedFilled[i+1].start);
assert.deepEqual(times(W.move(touching,[1],'end',-.2,5,true,true).rows),[[.5,1.5],[1.5,2.3],[2.3,3.5]]);
assert.deepEqual(times(W.move(touching,[1],'start',.2,5,true,true).rows),[[.5,1.7],[1.7,2.5],[2.5,3.5]]);
assert.deepEqual(times(W.move(touching,[1],'move',-.2,5,true,true).rows),[[.5,1.3],[1.3,2.5],[2.5,3.5]]);
for(const baseline of [touching,filled])for(let mask=1;mask<(1<<baseline.length);mask++)for(const delta of [-50,-.01,.23,50]){
 const ids=baseline.filter((r,i)=>mask&(1<<i)).map(r=>r.index);
 const result=W.move(baseline,ids,'move',delta,12,true,true);assert.ok(result);
 const sorted=W.ordered(result.rows);
 for(let i=0;i<sorted.length;i++){
  assert.ok(W.valid(sorted[i],12));
  if(i)assert.equal(sorted[i-1].end,sorted[i].start);
 }
 assert.deepEqual(result.rows.map(r=>[r.index,r.text]),baseline.map(r=>[r.index,r.text]));
}
const borrow=(kind,delta,ids=[1])=>W.move(touching,ids,kind,delta,5,true);
assert.deepEqual(times(borrow('end',.3).rows),[[.5,1.5],[1.5,2.8],[2.8,3.5]]);
assert.deepEqual(times(borrow('start',-.4).rows),[[.5,1.1],[1.1,2.5],[2.5,3.5]]);
assert.deepEqual(times(borrow('move',.2).rows),[[.5,1.5],[1.7,2.7],[2.7,3.5]]);
assert.deepEqual(times(borrow('move',-.2).rows),[[.5,1.3],[1.3,2.3],[2.5,3.5]]);
assert.deepEqual(borrow('end',.3).borrowed,[2]);
assert.deepEqual(borrow('start',-.4).borrowed,[0]);
assert.deepEqual(times(borrow('end',50).rows),[[.5,1.5],[1.5,3.49],[3.49,3.5]]);
assert.deepEqual(times(borrow('start',-50).rows),[[.5,.51],[.51,2.5],[2.5,3.5]]);
assert.deepEqual(times(borrow('move',.2,[0,1]).rows),[[.7,1.7],[1.7,2.7],[2.7,3.5]]);
const reorderedTouching=W.swap(touching,0,2,5);
assert.equal(W.move(reorderedTouching,[1],'start',-.3,5,true).rows[2].end,1.2);
assert.deepEqual(W.move(source,[1],'move',0,12,true).rows,source);
assert.deepEqual(W.move(touching,[1],'end',-.2,5,true).borrowed,[]); // Shrinking does not stretch an unrelated word.
assert.deepEqual(W.move(touching,[1],'end',.3,5,true).rows.map(r=>r.text),touching.map(r=>r.text));
// Every selection remains ordered and in bounds, including very large drags.
for(let n=1;n<8;n++)for(const delta of [-50,-1.123,-.01,0,.01,.37,50]){
 const ids=touching.filter((r,i)=>n&(1<<i)).map(r=>r.index),result=W.move(touching,ids,'move',delta,5,true);
 assert.ok(result);const sorted=W.ordered(result.rows);
 for(let i=0;i<sorted.length;i++){assert.ok(W.valid(sorted[i],5));if(i)assert.ok(sorted[i].start>=sorted[i-1].end);}
 assert.deepEqual(sorted.map(r=>r.index),[0,1,2]);
 for(const id of ids)assert.ok(Math.abs(result.rows[id].end-result.rows[id].start-1)<1e-8);
}
// Groups, including disjoint selections, never overlap, lose tokens or change relative gaps.
for(let n=1;n<32;n++)for(const delta of [-50,-1.123,0,.37,50]){
 const ids=source.filter((r,i)=>n&(1<<i)).map(r=>r.index),r=W.move(source,ids,'move',delta,12).rows;
 const sorted=W.ordered(r);for(let i=0;i<sorted.length;i++){assert.ok(W.valid(sorted[i],12));if(i)assert.ok(sorted[i].start>=sorted[i-1].end-1e-8);}
 const offsets=ids.map(id=>r[id].start-source[id].start);assert.ok(offsets.every(d=>Math.abs(d-offsets[0])<1e-8));
 assert.deepEqual(r.map(row=>[row.index,row.text]),source.map(row=>[row.index,row.text]));
}
for(let n=1;n<32;n++)for(const delta of [-50,-.123,0,.37,50]){
 const ids=source.filter((r,i)=>n&(1<<i)).map(r=>r.index),result=W.move(source,ids,'move',delta,12,true);
 assert.ok(result,'fractional borrowing '+n+' '+delta);
 const sorted=W.ordered(result.rows);
 for(let i=0;i<sorted.length;i++){assert.ok(W.valid(sorted[i],12));if(i)assert.ok(sorted[i].start>=sorted[i-1].end);}
 assert.deepEqual(sorted.map(r=>r.index),[0,1,2,3,4]);
}
// Exercise the gesture controller: swap preview, one atomic save, cancel and undo/redo.
global.cancelAnimationFrame=()=>{};
const Timeline=require('../autocaption/web_static/word-timeline.js');
let rows=W.copy(source),currentTime=0,commits=0,capture=null,reordered=false;
const editor=Object.create(Timeline.prototype);
Object.assign(editor,{selected:new Set(),mode:'single',collision:'swap',anchor:null,history:[],future:[],active:null,
 refresh(){},playback(){},autoScroll(){},focusWord(){},
 canvas:{getBoundingClientRect:()=>({left:0,width:1200}),setPointerCapture:id=>capture=id,hasPointerCapture:id=>capture===id,releasePointerCapture:()=>capture=null},
 track:{getBoundingClientRect:()=>({top:60,bottom:120})},
 options:{read:()=>({rows,duration:12,time:currentTime,disabled:false}),begin(){},discard(){rows=W.copy(saved);},
 change:(next,swap)=>{rows=next;reordered=swap;},preview:time=>currentTime=time,
 async commit(swap){commits++;saved=W.copy(rows);reordered=swap;return true;}}});
let saved=W.copy(rows);
function down(index,x,mods={}){editor.begin({button:0,pointerId:1,clientX:x,clientY:80,...mods,preventDefault(){},target:{closest:()=>({dataset:{index:String(index),kind:'move'},focus(){}})}});}
(async()=>{
 down(0,120);editor.active.x=720;editor.move();assert.equal(rows[0].start,source[3].start);assert.equal(commits,0);assert.equal(reordered,true);
 await editor.finish();assert.equal(commits,1);assert.equal(editor.history.length,1);
 await editor.restore(false);assert.deepEqual(rows,source);await editor.restore(true);assert.deepEqual(rows,swapped);
 down(0,720);editor.active.x=320;editor.move();await editor.finish(true);assert.deepEqual(rows,swapped);
 editor.mode='multiple';editor.selected=new Set([1,2]);down(1,320);editor.active.x=350;editor.move();assert.ok(Math.abs(rows[2].start-swapped[2].start-.3)<1e-8);await editor.finish();
 assert.equal(editor.selected.size,2);
 // Clicking, with no actual drag, must retain millisecond timings.
 const before=JSON.stringify(rows);editor.mode='single';down(2,550);await editor.finish();assert.equal(JSON.stringify(rows),before);
 // Default collision mode borrows from a neighbour before the cursor reaches
 // that neighbour. Undo/cancel restore both boundaries, with one atomic save.
 rows=W.copy(touching);saved=W.copy(rows);editor.collision='borrow';editor.selected=new Set([1]);editor.history=[];editor.future=[];
 down(1,200);editor.active.x=220;editor.move();assert.deepEqual(times(rows),[[.5,1.5],[1.7,2.7],[2.7,3.5]]);
 assert.deepEqual(editor.active.borrowed,[2]);assert.equal(reordered,false);
 await editor.finish();assert.equal(editor.history.length,1);
 await editor.restore(false);assert.deepEqual(rows,touching);
 await editor.restore(true);assert.deepEqual(times(rows),[[.5,1.5],[1.7,2.7],[2.7,3.5]]);
 down(1,200);editor.active.x=160;editor.move();await editor.finish(true);assert.deepEqual(times(rows),[[.5,1.5],[1.7,2.7],[2.7,3.5]]);
 // Fill existing gaps once, atomically. Undo/redo must restore both reports,
 // and a continuous drag must not open another hole in the captions.
 rows=W.copy(source);saved=W.copy(rows);editor.continuous=true;editor.history=[];editor.future=[];
 const beforeFill=commits;await editor.closeGaps();assert.equal(commits,beforeFill+1);
 assert.deepEqual(rows,filled);await editor.restore(false);assert.deepEqual(rows,source);
 await editor.restore(true);assert.deepEqual(rows,filled);
 await editor.closeGaps();assert.equal(commits,beforeFill+3); // Idempotent: no fourth save.
 down(1,350);editor.active.x=330;editor.move();
 for(let i=0;i+1<rows.length;i++)assert.equal(rows[i].end,rows[i+1].start);
 await editor.finish();await editor.restore(false);assert.deepEqual(rows,filled);
 await editor.restore(true);
 for(let i=0;i+1<rows.length;i++)assert.equal(rows[i].end,rows[i+1].start);
 assert.deepEqual(rows.map(r=>r.text),source.map(r=>r.text));
 console.log('Word timeline: swaps, groups, range/tail selection, bounds, cancellation and undo/redo passed.');
})().catch(error=>{console.error(error);process.exitCode=1;});

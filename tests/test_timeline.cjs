global.CaptionShortcuts=require('../autocaption/web_static/shortcuts.js');
const assert=require('node:assert/strict');
const {group,firstTime}=require('../autocaption/web_static/timeline.js');
const rows=[
 {index:0,text:'שלום',start:.3,end:.6,status:'aligned'},
 {index:1,text:'Reels',start:null,end:null,status:'unaligned'},
 {index:2,text:'בעברית!',start:1,end:1.8,status:'estimated'}
];
assert.deepEqual(group(rows,4,2).flat().map(w=>w.index),[0,2]);
assert.ok(firstTime(group(rows,4,2))>.3); // Opening lands on visible, non-zero-opacity text.
assert.equal(firstTime([]),null);
rows[1]={...rows[1],start:.65,end:.9,status:'estimated'};
assert.deepEqual(group(rows,4,2).flat().map(w=>w.text),['שלום','Reels','בעברית!']);
assert.equal(group(rows,1,2).length,3);
const many=Array.from({length:12},(_,i)=>({index:i,text:'מיליםארוכות.',start:i*2,end:i*2+1}));
assert.deepEqual(group(many,6,25).map(line=>line.length),[6,6]);
assert.deepEqual(group(many,3,25).map(line=>line.length),[3,3,3,3]);
const {segments}=require('../autocaption/web_static/timeline.js');
const cues=[{text:'שלום, Reels\n2026!',start:2,end:8},{text:'פזמון',start:10,end:15}];
assert.deepEqual(segments(cues).map(line=>[line[0].text,line[0].start,line[0].end]),cues.map(cue=>[cue.text,cue.start,cue.end]));
assert.ok(segments(cues).every(line=>line.length===1&&line[0].block));
// Passage gestures preserve immutable text, neighbours, gaps and duration.
const {passage,timelineTime}=require('../autocaption/web_static/editing.js');
const blocks=[{text:'בית\nשלום Reels 2026!',start:2,end:8},{text:'פזמון',start:10,end:15},{text:'בית הבא',start:20,end:25}];
const before=JSON.stringify(blocks);
assert.deepEqual(passage(blocks,1,'move',1.23,30),{start:11.23,end:16.23});
assert.deepEqual(passage(blocks,1,'move',100,30),{start:15,end:20});
assert.deepEqual(passage(blocks,1,'move',-100,30),{start:8,end:13});
assert.deepEqual(passage(blocks,0,'move',-100,30),{start:0,end:6});
assert.deepEqual(passage(blocks,2,'move',100,30),{start:25,end:30});
assert.deepEqual(passage(blocks,1,'start',-100,30),{start:8,end:15});
assert.deepEqual(passage(blocks,1,'start',100,30),{start:14.99,end:15});
assert.deepEqual(passage(blocks,1,'end',-100,30),{start:10,end:10.01});
assert.deepEqual(passage(blocks,1,'end',100,30),{start:10,end:20});
assert.equal(passage(blocks,1,'other',1,30),null);
assert.equal(passage(blocks,1,'move',NaN,30),null);
assert.equal(JSON.stringify(blocks),before);
const adjacent=[{start:1.003,end:5.603},{start:5.603,end:8.724},{start:8.724,end:12}];
assert.deepEqual(passage(adjacent,1,'move',10,20),{start:5.603,end:8.724});
assert.deepEqual(passage(adjacent,1,'start',-10,20),{start:5.61,end:8.724});
const original={start:10,end:15};
const moved=blocks.map(b=>({...b}));Object.assign(moved[1],passage(blocks,1,'move',1,30));
// Every pointer movement is relative to the original gesture, not compounded.
assert.deepEqual(passage(moved,1,'move',2,30,original),{start:12,end:17});
assert.equal(timelineTime(250,50,1000,100),20);
assert.equal(timelineTime(250,-750,2000,100),50); // Zoomed and scrolled canvas.
assert.equal(timelineTime(-10,0,1000,100),0);
assert.equal(timelineTime(2000,0,1000,100),100);
assert.equal(timelineTime(20,0,0,100),0);
// Exercise gesture lifecycle without a browser: preview while dragging, one
// save on release, cancel/undo restoration, and no edit from clicking an edge.
global.CaptionEditing=require('../autocaption/web_static/editing.js');
global.cancelAnimationFrame=()=>{};
const PassageTimeline=require('../autocaption/web_static/passage-timeline.js');
const editor=Object.create(PassageTimeline.prototype);
const draft=[{text:'בית בעברית',start:9.801,end:33.602},{text:'פזמון',start:40,end:60}];
let saves=0,changes=0,previewTime=10,captured=null;
Object.assign(editor,{selected:0,history:[],future:[],active:null,refresh(){},playback(){},autoScroll(){},
 canvas:{getBoundingClientRect:()=>({left:0,width:1000}),setPointerCapture:id=>captured=id,
  hasPointerCapture:id=>captured===id,releasePointerCapture:()=>captured=null},
 options:{read:()=>({cues:draft,duration:100,time:previewTime,disabled:false}),begin(){},
  change:(index,range)=>{Object.assign(draft[index],range);changes++;},
  preview:time=>previewTime=time,commit:()=>saves++}});
function down(kind,x){editor.begin({button:0,pointerId:1,clientX:x,preventDefault(){},target:{closest:()=>({dataset:{index:'0',kind},focus(){}})}});}
down('start',98.01);editor.move();editor.finish();
assert.equal(draft[0].start,9.801);assert.equal(changes,0);
saves=0;down('move',200);editor.active.x=180;editor.move();
assert.equal(draft[0].start,7.801);assert.equal(draft[0].end,31.602);
assert.equal(saves,0);assert.ok(previewTime>=7.801&&previewTime<31.602);
editor.finish();assert.equal(saves,1);assert.equal(editor.history.length,1);
editor.undoEdit();assert.equal(draft[0].start,9.801);assert.equal(draft[0].end,33.602);
down('end',336.02);editor.active.x=310;editor.move();
assert.equal(draft[0].start,9.801);assert.equal(draft[0].end,31);
editor.finish(true);assert.equal(draft[0].end,33.602);assert.equal(editor.history.length,0);
assert.equal(draft[0].text,'בית בעברית');assert.equal(draft[1].start,40);
// Splits partition original substrings; joining restores whitespace as well as
// every repeated/mixed-direction token. These operations never run ASR.
const {splitPassage,mergePassages,cutTime,suggestSplit,textWords}=CaptionEditing;
const song=[{label:'בית',start:1.003,end:8.724,text:'שלום,  Reels\nב־2026! שלום שלום\n'},
 {label:'פזמון',start:10,end:20,text:'כן, כן! 👋'}];
const songSource=JSON.stringify(song),sourceTokens=song.flatMap(c=>textWords(c.text).map(w=>w[0]));
for(let atWord=1;atWord<5;atWord++){
 const split=splitPassage(song,0,4.567,atWord);
 assert.equal(split.length,3);assert.equal(split[0].end,4.57);assert.equal(split[1].start,4.57);
 assert.equal(split[0].start,1.003);assert.equal(split[1].end,8.724);
 assert.deepEqual(split.flatMap(c=>textWords(c.text).map(w=>w[0])),sourceTokens);
 assert.deepEqual(mergePassages(split,0),song);
 assert.deepEqual(split[2],song[1]);
}
assert.equal(JSON.stringify(song),songSource);
const trimmedDisplay=segments(splitPassage(song,0,4,2));
assert.equal(trimmedDisplay[0][0].text,'שלום,  Reels');
assert.equal(JSON.stringify(song),songSource); // Stored separators remain intact.
for(const time of [1.003,1.004,8.724,8.723,NaN,Infinity,-1])assert.equal(splitPassage(song,0,time,2),null);
for(const i of [0,5,2.5,-1])assert.equal(splitPassage(song,0,4,i),null);
assert.equal(splitPassage([{text:'שלום',start:0,end:2}],0,1,1),null);
assert.equal(splitPassage(Array.from({length:1000},()=>song[0]),0,4,2),null);
assert.equal(cutTime(null,2),null);assert.equal(mergePassages(song,-1),null);assert.equal(mergePassages(song,1),null);
const merged=mergePassages(song,0);
assert.equal(merged[0].start,1.003);assert.equal(merged[0].end,20);
assert.deepEqual(textWords(merged[0].text).map(w=>w[0]),sourceTokens);
assert.equal(mergePassages([{text:'א',start:0,end:2},{text:'ב',start:2,end:3}],0)[0].text,'א\nב');
assert.equal(mergePassages([{text:'א',start:0,end:2},{text:'ב',start:1,end:3}],0),null);
assert.equal(mergePassages([{text:'א'.repeat(30000),start:0,end:2},{text:'ב'.repeat(30000),start:2,end:3}],0),null);
const timed=[{text:'א ב ג ד',start:0,end:10}];
const anchors=['א','ב','ג','ד'].map((text,i)=>({text,start:[0,2,3,8][i],end:[1,2.5,4,9][i]}));
assert.deepEqual(suggestSplit(timed,0,3.1,anchors),{wordIndex:2,aligned:true});
assert.deepEqual(suggestSplit(timed,0,3.1),{wordIndex:1,aligned:false});
assert.equal(suggestSplit(timed,0,3.1,anchors.map(r=>({...r,text:'אחר'}))).aligned,false);
assert.equal(suggestSplit([{...timed[0],start:20,end:30}],0,25,anchors).aligned,false);
assert.equal(suggestSplit(timed,0,3.1,anchors.map((r,i)=>i===2?{...r,start:null}:r)).aligned,false);
// Structural undo can be interleaved with existing drag undo without stale indexes.
editor.options.replace=next=>draft.splice(0,draft.length,...next.map(c=>({...c})));
editor.selected=0;const originalDraft=JSON.stringify(draft);
editor.rememberStructure();editor.options.replace(splitPassage(draft,0,20,1));editor.selected=1;
const splitDraft=JSON.stringify(draft);previewTime=21;editor.mergeSelected(-1);
assert.equal(previewTime,21);
assert.equal(draft.length,2);editor.undoEdit();assert.equal(JSON.stringify(draft),splitDraft);
editor.undoEdit();assert.equal(JSON.stringify(draft),originalDraft);
editor.undoEdit(true);assert.equal(JSON.stringify(draft),splitDraft);
editor.undoEdit(true);assert.equal(draft.length,2);
editor.undoEdit();assert.equal(JSON.stringify(draft),splitDraft);
editor.undoEdit();assert.equal(JSON.stringify(draft),originalDraft);

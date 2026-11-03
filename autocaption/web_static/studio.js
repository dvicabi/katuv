"use strict";
const $ = (id) => document.getElementById(id);
const all = (selector) => [...document.querySelectorAll(selector)];
// Keep a single instance of every working control while giving the editor a
// bounded inspector and a persistent export action.
$("designPane").append($("designOptions"));
$("timingPane").prepend($("segmentsEditor"));
$("timingPane").append($("reviewNotice"),document.querySelector(".voice-check"),$("timingDetails"),document.querySelector(".text-review"));
$("exportToolbar").append($("exportOptions"),document.querySelector(".download-action"),$("downloadBanner"));
document.querySelector(".source-row").after($("jobBanner"));
const heroWave=document.querySelector(".hero-wave");
for(let i=0;i<39;i++){const bar=document.createElement("i");bar.style.setProperty("--bar",(5+Math.abs(Math.sin(i*1.8))*20)+"px");bar.style.setProperty("--delay",(-i*.07)+"s");heroWave.append(bar);}
const defaults = {style:"zoom",font_size:110,words_per_line:4,position:.7,position_x:.5,rotation:0,motion_strength:1,motion_speed:1,highlight_color:"#D4F76B",text_color:"#FFFFFF",outline:3,backdrop_opacity:0,caption_mode:"words",word_animation:true,export_resolution:"source"};
const state = {project:null,settings:{...defaults},mode:"exact",editing:true,selected:null,wave:[],busy:false,uploading:false,poll:null,reportKey:"",captionKey:"",stopAt:null,settingsTimer:null,settingsRequest:Promise.resolve(),settingsDirty:false,step:0,motions:{},demosPaused:matchMedia("(prefers-reduced-motion: reduce)").matches};
const video = $("video");
const previewPanel=document.querySelector(".preview-panel"),previewAnchor=document.createComment("preview position");
previewPanel.before(previewAnchor);
const measure = document.createElement("canvas").getContext("2d");
let toastTimer, frameUrl, lineCache=[];
let styleUndo=[],styleRedo=[],lastStyleEdit=0,drag=null,captionLayout=null;
let focusWindow=null,focusDrag=null,draftLines=null;
let speechModels=[],modelSaving=false,automaticSelection=null;
let cueDraft=[],cueDirty=false,cueTimer,cueRevision=0,cueRequest=Promise.resolve();
let wordDraft=null,wordDraftReordered=false,wordSaving=false,wordGesture=false;
function isSegments(){return state.settings.caption_mode==="segments";}
function hasCaptions(p=state.project){return !!p&&(p.settings?.caption_mode==="segments"?!!p.segments?.length:!!p.report);}
const looks={clean:{outline:1.5,backdrop_opacity:0,highlight_color:"#D4F76B"},card:{outline:0,backdrop_opacity:.78,highlight_color:"#FFD54A"},bold:{outline:6,backdrop_opacity:0,highlight_color:"#FF779E"}};
const processingStates=["aligning","verifying","exporting"];
// Drafts stay on this computer. A refresh must not discard pasted source text.
const local = {
 get(key){try{return localStorage.getItem(key);}catch{return null;}},
 set(key,value){try{localStorage.setItem(key,value);}catch{}},
 remove(key){try{localStorage.removeItem(key);}catch{}}
};
function saveDraft(){
 if(state.project&&state.editing&&!busy()) local.set("katuv-draft-"+state.project.id,JSON.stringify({text:$("transcript").value,mode:state.mode,fingerprint:state.project.fingerprint}));
}
function clearDraft(){if(state.project)local.remove("katuv-draft-"+state.project.id);}
function durationLabel(seconds){
 const value=Math.round(seconds);
 return value<60?value+" שניות":Math.floor(value/60)+" דקות ו־"+value%60+" שניות";
}
function fieldError(id,message){$(id).textContent=message;$(id).hidden=!message;}
function wordTime(edge){const limits=timingLimits();return CaptionTimecode.read($(edge==="start"?"wordStart":"wordEnd"),limits.min,limits.max);}
function markTimeInput(input){
 if(input.value&&!Number.isFinite(CaptionTimecode.read(input)))input.setAttribute("aria-invalid","true");else input.removeAttribute("aria-invalid");
}
function timingFeedback(){
 ["Start","End"].forEach(edge=>{const input=$("word"+edge),time=wordTime(edge.toLowerCase());markTimeInput(input);$("captured"+edge).textContent=Number.isFinite(time)?timeLabel(time):"טרם נקבע";});
 updateFocusRange();
 const start=wordTime("start"),end=wordTime("end");
 const limits=timingLimits();
 draftLines=state.selected!==null&&$("wordStart").value!==""&&$("wordEnd").value!==""&&end>start&&start>=limits.min&&end<=limits.max
  ?lineCache.map(line=>line.map(row=>row.index===state.selected?{...row,start,end}:row)):null;
 renderSubtitle();
 updateControls();
}
function timingDirty(){
 if(isSegments())return false;
 const row=words()[state.selected];if(!row)return false;
 return ["start","end"].some(edge=>{
  const value=$(edge==="start"?"wordStart":"wordEnd").value;
  const time=wordTime(edge);
  return value===""?row[edge]!==null:!Number.isFinite(time)||row[edge]===null||Math.abs(time-row[edge])>.000001;
 });
}

function notice(message, error=false) {
 clearTimeout(toastTimer);
 if(error){$("errorMessage").textContent=message;$("errorBanner").hidden=false;$("errorBanner").scrollIntoView({block:"nearest",behavior:"smooth"});return;}
 $("toast").textContent=message;$("toast").hidden=false;
 toastTimer=setTimeout(()=>$("toast").hidden=true,4000);
}

async function api(path, options={}) {
  const response = await fetch(path, {cache:"no-store",...options,
    headers:options.body?{"Content-Type":"application/json",...options.headers}:options.headers});
  if (!response.ok) {
    let body; try {body=await response.json();} catch {body={detail:"לא הצלחנו להשלים את הפעולה."};}
    let detail=typeof body.detail==="string"?body.detail:"הנתונים לא תקינים. בדקו את הערכים ונסו שוב.";
    if(detail.startsWith("Correct all")) detail="נשארו מילים שצריך למקם בזמן הנכון. פתחו את התיקון לפני הכנת הסרטון להורדה.";
    if(!/[\u0590-\u05ff]/u.test(detail)) detail="לא הצלחנו להשלים את הפעולה. בדקו את הסרטון והטקסט ונסו שוב.";
    throw new Error(detail);
  }
  return response.json();
}
const projectApi = (suffix) => "/api/projects/"+state.project.id+suffix;
const busy = () => state.uploading || state.busy || wordSaving || wordGesture;
const words = () => wordDraft || state.project?.report?.words || [];
const displayWords = () => state.project?.report?.allow_reordered_timings||wordDraftReordered?CaptionWordEditing.ordered(words()):words();
const tokens = (text) => text.match(/\S+/gu)||[];
function timeLabel(seconds, fraction=true) {
  const value=CaptionTimecode.format(Math.max(0,Number.isFinite(seconds)?seconds:0));
  return fraction?value:value.slice(0,-3);
}
function intervalValid(row) {
  return Number.isFinite(row.start)&&Number.isFinite(row.end)&&row.start>=0&&row.end>row.start&&row.end<=state.project.duration+.001&&Math.round(row.end*100)>Math.round(row.start*100);
}
function invalidIndexes() {
  const invalid=new Set(); let end=0;
  displayWords().forEach(row=>{
    if(!intervalValid(row)||!["aligned","manual","estimated"].includes(row.status)||row.start<end) invalid.add(row.index);
    else end=row.end;
  });
  return invalid;
}
function reviewIndexes(){return new Set([...invalidIndexes(),...words().filter(row=>!["matched","listened"].includes(row.check?.status)).map(row=>row.index)]);}
function estimatedIndexes(){return new Set([...invalidIndexes(),...words().filter(row=>row.status==="estimated").map(row=>row.index)]);}
function auditStatus(row){
 if(!state.project?.report?.full_audit)return null;
 const audit=row.audit,end=row.gap_fill?.end??row.end;
 return audit&&audit.start===row.start&&audit.end===end?audit.status:"stale";
}
function attentionIndexes(){return state.project?.report?.full_audit?new Set([...invalidIndexes(),...words().filter(row=>auditStatus(row)!=="supported").map(row=>row.index)]):estimatedIndexes();}
function suggestedTiming(row){if(!row)return null;return state.project?.report?.full_audit?(auditStatus(row)==="disagreement"?row.audit.proposed:null):(row.check?.status==="matched"?null:row.check?.suggested);}
function showInspector(pane,resetScroll=false){
 const timing=pane==="timing";
 $("designPane").hidden=timing;$("timingPane").hidden=!timing;
 $("designTab").setAttribute("aria-selected",!timing);$("timingTab").setAttribute("aria-selected",timing);
 $("designTab").tabIndex=timing?-1:0;$("timingTab").tabIndex=timing?0:-1;
 $("designOptions").open=!timing;$("timingDetails").open=timing;
 document.body.dataset.inspector=pane;
 requestAnimationFrame(()=>{
  fitViewer();renderSubtitle();
  if(resetScroll){
   if(matchMedia("(max-width:760px)").matches)$(timing?"timingPane":"designPane").scrollIntoView({block:"start",behavior:"smooth"});
   else document.querySelector(".content-panel").scrollTop=document.querySelector(".source-row").offsetHeight;
  }
 });
}
function checkDescription(row){
 const audit=auditStatus(row);
 if(audit==="supported")return "מספר מודלים תומכים בתזמון לפי השירה וצלילי המילה. זו תמיכה אוטומטית, לא אישור אחרי האזנה אנושית.";
 if(audit==="disagreement")return "הבדיקה המלאה מצאה פער: "+Math.abs(row.audit.start_delta_ms)+" אלפיות שנייה בהתחלה ו־"+Math.abs(row.audit.end_delta_ms)+" בסיום. התזמון לא אומת.";
 if(audit==="uncertain")return row.audit?.method==="independent_forced_paths"?(row.audit.start_supported?"תחילת המילה נתמכת בבדיקות. סיום השירה עדיין פחות ודאי; התזמון חושב אוטומטית ואין צורך באישור ידני.":"התזמון חושב אוטומטית מתוך השירה. המודלים עדיין חלוקים לגבי הגבולות; המילה נשמרת ומוצגת, ללא צורך באישור ידני."):"המילה נבדקה, אך הזיהוי וההתאמה הקולית לא הסכימו מספיק כדי לאמת את התזמון. כל המילים נשמרו.";
 if(audit==="stale")return "התזמון השתנה מאז הבדיקה המלאה; תוצאת הבדיקה הקודמת אינה מאמתת אותו.";
 if(row.status==="estimated")return "התזמון הושלם אוטומטית לפי הדיבור והמילים הסמוכות. אפשר לשנות אותו כאן אם רוצים; אין צורך באישור.";
 if(row.check?.status==="matched")return "המילה זוהתה בקול והתזמון שלה נתמך בהתאמת צלילי הדיבור. הפער בין ההתאמות קטן מ־80 אלפיות שנייה או שווה להן. כדאי גם להאזין.";
 if(row.check?.status==="listened")return "אישרתם את המילה אחרי האזנה.";
 const reasons={not_heard:"המילה לא זוהתה בביטחון באזור הזמן הזה. האזינו ובדקו אם היא נאמרת ומתי.",no_timing:"עדיין אין למילה זמן בטוח. קבעו התחלה וסיום לפי מה ששומעים.",low_confidence:"זיהוי המילה אינו בטוח. האזינו לפני האישור.",timing_disagreement:"המילה זוהתה, אבל הבדיקות מציעות זמנים שונים. האזינו והשוו.",unavailable:"בדיקת הקול לא הושלמה. נסו שוב או האזינו ואשרו ידנית.",timing_changed:"התזמון השתנה. האזינו לו ואשרו, או הריצו שוב את בדיקת הקול."};
 reasons.weak_alignment="התאמת צלילי המילה אינה בטוחה. האזינו וקבעו את ההתחלה והסיום שלה.";
 reasons.recheck_required="התזמון הקיים נשמר. בדיקה נוספת זמינה לפי בחירתכם.";
 if(row.check?.reason==="timing_disagreement"&&Number.isFinite(row.check.start_delta_ms)&&Number.isFinite(row.check.end_delta_ms))return "התאמת התמליל והתאמת המילים שזוהו מציעות זמנים שונים: הפרש של "+Math.abs(row.check.start_delta_ms)+" אלפיות שנייה בהתחלה ו־"+Math.abs(row.check.end_delta_ms)+" בסיום. האזינו ובחרו את התזמון הנכון.";
 return reasons[row.check?.reason]||"התזמון נוצר אוטומטית. ניתן לדייק אותו כאן, אם רוצים.";
}
function updateControls() {
 const p=state.project, has=!!p, processing=busy(), reviewed=hasCaptions()&&!state.editing;
 const pending=timingDirty()||!!wordDraft||(isSegments()&&cueDirty),invalid=isSegments()?new Set():invalidIndexes(), step=!has?1:reviewed?3:2, ready=reviewed&&p.export_ready&&!processing&&!invalid.size&&!pending;
 const modelTarget=reviewed?$("timingPane"):$("inputMode");
 if($("modelPicker").parentElement!==modelTarget){
  if(reviewed)document.querySelector(".voice-check").before($("modelPicker"));
  else document.querySelector(".next-action").before($("modelPicker"));
 }
 showModel();
 document.body.dataset.step=step;
 document.body.classList.toggle("is-busy",processing);
 document.body.classList.toggle("has-video",has);
 $("welcome").hidden=has;$("workspace").hidden=!has;
 $("inputMode").hidden=reviewed;$("reviewMode").hidden=!reviewed;
 $("exportToolbar").hidden=!reviewed;
 $("reviewBadge").hidden=true;$("reviewBadge").textContent=invalid.size;
 $("wordCount").textContent=tokens($("transcript").value).length+" מילים";
 $("alignButton").disabled=processing||!has||modelSaving;
 $("exportButton").disabled=processing||!reviewed||invalid.size>0||pending;
 $("exportResolution").disabled=processing||!reviewed;
 $("exportButton").parentElement.hidden=ready;
 $("exportHint").textContent=pending?"יש תיקון תזמון שלא נשמר. שמרו או בטלו אותו בלשונית מילים ותזמון.":invalid.size?"יש חפיפה בתזמון הידני. אפשר לתקן אותה בלשונית מילים ותזמון.":"הסרטון יישמר עם הכתוביות. בלי סימן מים.";
 $("frameButton").disabled=processing||!reviewed||invalid.size>0||pending;
 $("discardTiming").hidden=!pending;$("discardTiming").disabled=processing;
 $("frameButton").hidden=!reviewed;
 ["playButton","muteButton","seek","currentTime"].forEach(id=>$(id).disabled=!has||state.uploading);
 ["videoInput","newButton","historyButton","editTranscript","uploadButton","chooseVideo","demoButton","uploadText","textInput","cancelEdit","exactMode","autoMode","previewMotion","verifyButton","useSuggested","listenedWord"].forEach(id=>$(id).disabled=processing);
 $("verifyButton").disabled=processing||pending||modelSaving;
 $("resolveManualConflicts").disabled=processing;
 ["saveTiming","setStart","setEnd","wordStart","wordEnd"].forEach(id=>$(id).disabled=processing);
 $("undoStyle").disabled=processing||!styleUndo.length;$("redoStyle").disabled=processing||!styleRedo.length;
 $("canvasUndo").disabled=processing||!styleUndo.length;
 updateHistoryButtons();
 all("[data-seek]").forEach(b=>b.disabled=!has||state.uploading);
 all("#canvasEditBar button,#wordWave button,.timing-nudge button").filter(el=>el.id!=="canvasUndo").forEach(el=>el.disabled=processing);
 all(".style-panel input,.style-panel button").filter(el=>!["undoStyle","redoStyle"].includes(el.id)).forEach(el=>el.disabled=processing);

 const estimated=words().filter(row=>row.status==="estimated").length,manual=words().filter(row=>row.status==="manual").length;
 const audited=!!p?.report?.full_audit,supported=words().filter(row=>auditStatus(row)==="supported").length;
 $("verificationStatus").textContent=p?.state==="verifying"?"מחשבים מחדש לפי הקול, בקטעים קצרים…":audited?
  "בדיקה מלאה: "+supported+" עם תמיכה חוזרת · "+(words().length-supported)+" עדיין לא אומתו":
  words().length+" מילים תוזמנו · טרם בוצעה בדיקה מלאה"+(estimated?" · "+estimated+" בתזמון משוער":"");
 $("verifyButton").textContent="שיפור תזמון אוטומטי";
 $("transcript").readOnly=processing;
 $("cancelEdit").hidden=!hasCaptions();
 $("alignHint").textContent=hasCaptions()&&state.editing?"יצירת כתוביות מחדש תחליף את התיקונים הקודמים. אפשר גם לבטל ולחזור.":state.mode==="auto"?"ניצור טקסט מהדיבור בעברית. תוכלו לבדוק אותו לפני ההורדה.":"אנחנו נמצא מתי כל מילה נאמרת בסרטון.";
 $("timingStatus").textContent=invalid.size?invalid.size+" מילים צריכות תיקון":words().length+" מילים מוכנות";
 $("reviewNotice").classList.remove("warning");
 $("reviewNotice").textContent=audited?"נבדקו כל "+words().length+" המילים מול האודיו. "+(words().length-supported)+" עדיין ללא אימות מספיק. הן מסומנות בנפרד; אין צורך לאשר כל מילה כדי לייצא.":"התזמונים נוצרו אוטומטית. עצם ההתאמה אינה מבטיחה שהמילה מופיעה בדיוק ברגע השירה. אפשר לנגן, לעצב ולהוריד.";
 $("timingSummary").textContent="דיוק ידני של מילה · לא חובה";
 $("nextUnaligned").hidden=!attentionIndexes().size;
 $("nextUnaligned").textContent=audited?"למילה הבאה שלא אומתה ←":"למילה הבאה עם תזמון משוער ←";
 $("timelineDetails").hidden=!reviewed||isSegments();
 $("passageTimeline").hidden=!reviewed||!isSegments();
 document.body.classList.toggle("has-passage-timeline",reviewed);
 passageTimeline.refresh();
 wordTimeline.refresh();
 $("segmentsEditor").hidden=!isSegments();
 $("timingTabLabel").textContent=isSegments()?"קטעים ותזמון":"מילים ותזמון";
 ["reviewNotice","timingDetails","modelPicker"].forEach(id=>$(id).hidden=reviewed&&isSegments());
 document.querySelector(".voice-check").hidden=isSegments();
 $("wordCountControls").hidden=isSegments();$("wordAnimationControl").hidden=isSegments();
 $("wordCaptionMode").setAttribute("aria-pressed",!isSegments());$("segmentCaptionMode").setAttribute("aria-pressed",isSegments());
 $("wordCaptionMode").disabled=processing;$("segmentCaptionMode").disabled=processing;$("startSegments").disabled=processing;$("startSegments").hidden=state.mode!=="exact";
 all("#segmentsEditor input,#segmentsEditor textarea,#segmentsEditor button").forEach(el=>el.disabled=processing);
 if(isSegments()&&cueDirty)$("exportHint").textContent="תזמון הקטעים נשמר אוטומטית כשכל הזמנים תקינים.";
 $("exactMode").classList.toggle("selected",state.mode==="exact");
 $("autoMode").classList.toggle("selected",state.mode==="auto");
 $("exactMode").setAttribute("aria-selected",state.mode==="exact");
 $("autoMode").setAttribute("aria-selected",state.mode==="auto");
 ["transcript","exactNote","exactExtras"].forEach(id=>$(id).hidden=state.mode!=="exact");
 $("autoNote").hidden=state.mode!=="auto";
 document.querySelector(".input-label").hidden=state.mode!=="exact";
 $("bottomStatus").textContent=processing?"הסרטון בעבודה — אפשר להמתין כאן":pending?"תיקון התזמון עדיין לא נשמר":reviewed?(invalid.size?"נשאר תיקון קטן לפני ההורדה":"הסרטון שמור במחשב שלכם"):has?"הטקסט נשמר אוטומטית בדפדפן הזה":"מתאים לסרטונים בעברית";
 $("previewHint").textContent=reviewed?"נגנו כדי לבדוק את הכתוביות. אפשר לשנות את העיצוב בכל רגע.":"אפשר להאזין לסרטון בזמן שמוסיפים את הטקסט.";
 $("stepLabel").textContent=["","מילים טובות ראויות להיראות.","שלב 2 מתוך 3","הסטודיו שלכם"][step];
 $("pageTitle").textContent=["","המילים שלכם.\nהקצב שלכם.","מה אומרים בסרטון?","עכשיו, תור הסגנון שלכם."][step];
 $("pageHint").textContent=["","מסרטון לכתוביות שאי אפשר לפספס. בעברית, בדיוק בזמן ובסגנון שלכם.","הטקסט שלכם הוא המקור. אנחנו נמצא את הרגע של כל מילה.","הכתוביות כבר על הסרטון. בחרו סגנון, גררו למקום והורידו."][step];
 [1,2,3].forEach(i=>{const el=$("step"+i);el.classList.toggle("current",i===step);el.classList.toggle("complete",i<step);if(i===step)el.setAttribute("aria-current","step");else el.removeAttribute("aria-current");});
 if(ready)showDownload();else $("downloadBanner").hidden=true;
 if(step!==state.step){const previous=state.step;state.step=step;if(step===3)showInspector(isSegments()?"timing":"design");requestAnimationFrame(()=>{fitViewer();if(previous){$("pageTitle").focus({preventScroll:true});$("main").scrollIntoView({block:"start"});}});}
}

function modelName(id){return speechModels.find(model=>model.id===id)?.label||id;}
function showModel(){
 const id=state.project?.speech_model||"auto",automatic=id==="auto";
 const plan=state.project?.model_selection?.automatic?state.project.model_selection:automaticSelection;
 const actual=automatic?plan?.model:id,model=speechModels.find(model=>model.id===actual);
 $("speechModel").value=id;
 $("speechModel").disabled=busy()||modelSaving||timingDirty()||!speechModels.length;
 $("autoModelResult").hidden=!automatic||!plan;
 $("autoModelName").textContent=model?.label||"בודקים את המחשב…";
 if(model){
  $("modelDescription").textContent=automatic?plan.reason:model.description;
  $("modelCached").textContent=model.cached?"מוכן במחשב":"הורדה בשימוש הראשון";
  $("modelCached").classList.toggle("ready",model.cached);
  $("modelSize").textContent="גודל ההורדה: "+model.download;
  const hw=plan?.hardware;
  $("modelHardware").textContent=automatic&&hw?(plan.device==="cuda"?"עיבוד בכרטיס המסך":"עיבוד במעבד")+" · "+hw.cpu_cores+" ליבות · "+hw.ram_available_gb+" GB זיכרון פנוי מתוך "+hw.ram_total_gb:model.hardware;
  $("modelDownload").textContent=model.cached?"המודל כבר במחשב ואפשר להשתמש בו גם בלי אינטרנט.":"רק המודל שבחרתם יורד, בשימוש הראשון. אחר כך אפשר לעבוד בלי אינטרנט.";
 }
 $("modelPurpose").textContent=state.mode==="exact"?"הטקסט שלכם נשאר מילה במילה. המודל בודק את הדיבור; התאמת התזמון נעשית בנפרד עם מודל עברי.":"המודל ייצור טקסט מהדיבור בעברית. הסרטון נשאר במחשב שלכם.";
 const verification=state.project?.report?.verification;
 $("modelProvenance").hidden=!verification;
 if(verification){
  const different=verification.model!==actual;
  $("modelProvenance").textContent=(verification.status==="failed"?"הבדיקה לא הושלמה עם ":"הבדיקה האחרונה: ")+modelName(verification.model)+(different?". הבחירה החדשה תשמש בבדיקה הבאה; המילים והתזמונים נשמרו.":"");
 }
}
async function refreshModels(){
 const catalog=await api("/api/models");speechModels=catalog.models;automaticSelection=catalog.automatic;
 $("speechModel").replaceChildren();
 const automatic=document.createElement("option");automatic.value="auto";automatic.textContent="אוטומטי — התאמה למחשב שלי";$("speechModel").append(automatic);
 for(const [group,label] of [["hebrew","אומנו במיוחד לעברית"],["multilingual","רב־לשוניים, כולל עברית"]]){
  const options=document.createElement("optgroup");options.label=label;
  speechModels.filter(model=>model.group===group).forEach(model=>{const option=document.createElement("option");option.value=model.id;option.textContent=model.label;options.append(option);});
  $("speechModel").append(options);
 }
 showModel();
}

function showSettings() {
  const s=state.settings;
  $("exportResolution").value=s.export_resolution;
  const output=state.project?.export_options?.find(option=>option.value===s.export_resolution);
  $("exportResolutionInfo").textContent=output?`רוחב × גובה: \u200e${output.width} × ${output.height}\u200e · ${output.upscaled?"הגדלה אינה מוסיפה פרטים למקור":"יחס התמונה נשמר"}`:"";
  document.documentElement.style.setProperty("--highlight",s.highlight_color);
  all("[data-style]").forEach(b=>{b.classList.toggle("selected",b.dataset.style===s.style);b.setAttribute("aria-pressed",b.dataset.style===s.style);});
  all("[data-color]").forEach(b=>{const selected=b.dataset.color.toUpperCase()===s.highlight_color.toUpperCase();b.classList.toggle("selected",selected);b.setAttribute("aria-pressed",selected);});
  all("[data-size]").forEach(b=>{b.classList.toggle("selected",+b.dataset.size===s.font_size);b.setAttribute("aria-pressed",+b.dataset.size===s.font_size);});
  all("[data-words]").forEach(b=>{b.classList.toggle("selected",+b.dataset.words===s.words_per_line);b.setAttribute("aria-pressed",+b.dataset.words===s.words_per_line);});
  $("wordAnimation").checked=s.word_animation;
  all("[data-position]").forEach(b=>b.classList.toggle("selected",+b.dataset.position===s.position));
  $("colorHex").textContent=s.highlight_color.toUpperCase();
  $("customColor").value=s.highlight_color;
  $("fontSize").value=s.font_size;
  $("fontSizeValue").value=s.font_size;
  $("outline").checked=s.outline>0;
  $("textColor").value=s.text_color;
  $("backdropOpacity").value=s.backdrop_opacity;$("backdropValue").value=s.backdrop_opacity?Math.round(s.backdrop_opacity*100)+"%":"ללא רקע";
  all("[data-look]").forEach(b=>b.setAttribute("aria-pressed",Object.entries(looks[b.dataset.look]).every(([key,value])=>s[key]===value)));
  for(const [id,key] of [["motionStrength","motion_strength"],["motionSpeed","motion_speed"],["rotation","rotation"],["positionX","position_x"],["positionY","position"]])$(id).value=s[key];
  $("motionStrengthValue").value=Math.round(s.motion_strength*100)+"%";
  $("motionSpeedValue").value=s.motion_speed===1?"רגילה":s.motion_speed.toFixed(2)+"×";
  $("rotationValue").value=s.rotation+"°";
  $("positionXValue").value=Math.round(s.position_x*100)+"%";$("positionYValue").value=Math.round(s.position*100)+"%";
  state.captionKey="";
  rebuildLines();
  renderSubtitle();
}
function changeSettings(change, remember=true) {
  if(busy())return;
  if(remember&&!drag){invalidateRedo();if(Date.now()-lastStyleEdit>450){styleUndo.push(CaptionShortcuts.mark({...state.settings}));styleUndo=styleUndo.slice(-30);}styleRedo=[];lastStyleEdit=Date.now();}
  state.settingsDirty=true;
  Object.assign(state.settings,change);
  showSettings();
  if(state.project) {
    state.project.export_ready=false;
    $("downloadBanner").hidden=true;
    $("savedStatus").textContent="שומרים…";
    clearTimeout(state.settingsTimer);
    state.settingsTimer=setTimeout(()=>persistSettings().catch(e=>notice(e.message,true)),350);
    updateControls();
  }
}
function persistSettings() {
 clearTimeout(state.settingsTimer);
 if(!state.project||!state.settingsDirty)return state.settingsRequest;
 const id=state.project.id,settings={...state.settings};state.settingsDirty=false;
 state.settingsRequest=state.settingsRequest.catch(()=>{}).then(async()=>{
  try{
   const data=await api("/api/projects/"+id+"/settings",{method:"PUT",body:JSON.stringify(settings)});
   if(state.project?.id===id){state.project.settings=data.settings;$("savedStatus").textContent=state.settingsDirty?"שומרים…":"נשמר במחשב";}
  }catch(error){if(state.project?.id===id)state.settingsDirty=true;throw error;}
 });
 return state.settingsRequest;
}

function loadProject(data) {
  const changed=state.project?.id!==data.id;
  const reveal=changed||(!state.project?.report&&!!data.report);
  const fillOnOpen=reveal||["aligning","verifying"].includes(state.project?.state);
  const refined=!changed&&["aligning","verifying"].includes(state.project?.state)&&!["aligning","verifying"].includes(data.state);
  state.project=data;
  state.mode=data.mode;
  state.settings={...defaults,...data.settings};
  state.settingsDirty=false;
  state.editing=!hasCaptions(data);
  state.busy=processingStates.includes(data.state);
  if(changed) {
    $("resolveManualConflicts").checked=!!data.resolve_manual_conflicts;
    wordDraft=null;wordDraftReordered=false;wordGesture=false;
    styleUndo=[];styleRedo=[];drag=null;
    focusWindow=null;draftLines=null;
    clearTimeout(state.settingsTimer);
    $("timingCard").hidden=true;
    $("downloadBanner").hidden=true;
    video.pause();
    video.src="/api/projects/"+data.id+"/file/video";
    video.load();
    state.wave=[];
    state.selected=null;
    state.reportKey="";
    local.set("katuv-project",data.id);
    api(projectApi("/waveform")).then(wave=>{
      if(state.project?.id===data.id){state.wave=wave;drawWave();passageTimeline.refresh();wordTimeline.refresh();}
    }).catch(()=>{}); // Waveform is optional; alignment reports missing audio clearly.
  }
  $("transcript").value=data.transcript;
  if(changed&&!state.busy){try{
   const draft=JSON.parse(local.get("katuv-draft-"+data.id));
   if(draft&&draft.fingerprint===data.fingerprint&&(draft.text!==data.transcript||draft.mode!==data.mode)){$("transcript").value=draft.text;state.mode=draft.mode;state.editing=true;}
  }catch{}}
  if(changed){$("styleDetails").open=false;$("timelineDetails").open=false;showInspector(isSegments()?"timing":"design");}
  fieldError("transcriptError","");$("transcript").removeAttribute("aria-invalid");
  $("sourceName").textContent=data.name;
  $("sourceInfo").textContent=durationLabel(data.duration)+" · "+(data.bytes/1024**2).toFixed(1)+" מ״ב";
  $("projectTitle").textContent=data.name.replace(/\.mp4$/i,"");
  $("savedStatus").textContent=state.busy?"בעבודה":"נשמר";
  $("totalTime").textContent=timeLabel(data.duration);
  $("seek").max=data.duration;
  $("aspectLabel").textContent=data.width+" × "+data.height;
  $("timelineEmpty").hidden=true;
  restoreSegments(data,changed);
  if(changed)wordTimeline.reset();
  else if(refined){wordDraft=null;wordDraftReordered=false;wordTimeline.invalidate();}
  showSettings();
  renderWords();
  updateControls();
  drawTimeline();
  fitViewer();
  if(reveal)revealFirstCaption();
  setJob(data);
  if(data.export_ready) showDownload();
  if(fillOnOpen&&data.report&&!state.busy&&!state.editing&&!isSegments())queueMicrotask(()=>{
   if(state.project?.id===data.id)wordTimeline.closeGaps();
  });
}
function renderWords() {
  const key=JSON.stringify(state.project?.report);
  if(state.reportKey===key) return;
  state.reportKey=key;
  const invalid=attentionIndexes();
  $("wordChips").replaceChildren();
  words().forEach(row=>{
    const b=document.createElement("button"); b.type="button";
    b.className="word-chip"+(invalid.has(row.index)?" unaligned":auditStatus(row)==="supported"||["matched","listened"].includes(row.check?.status)?" checked":"");
    b.dataset.index=row.index;
    b.textContent=row.text;
    b.title=checkDescription(row)+" "+timeLabel(row.start)+" – "+timeLabel(row.end);
    b.setAttribute("aria-label",row.text+(invalid.has(row.index)?" — התזמון טרם אומת":""));
    b.addEventListener("click",()=>selectWord(row.index,true));
    $("wordChips").append(b);
  });
  if(state.selected!==null&&words()[state.selected])selectWord(state.selected,false);

}
function selectWord(index, seek) {
  const row=words()[index];if(!row)return;
  if(index!==state.selected&&timingDirty()){notice("קודם שמרו או בטלו את תיקון התזמון של המילה שבחרתם.");return;}
  state.selected=index;
  $("timingCard").hidden=false;
  $("selectedWord").textContent=row.text;
  $("timingDetails").open=true;
  showInspector("timing");
  $("timingReason").textContent=checkDescription(row);
  $("useSuggested").hidden=!suggestedTiming(row);
  $("listenedWord").checked=false;
  fieldError("timingError","");
  CaptionTimecode.write($("wordStart"),row.start);
  CaptionTimecode.write($("wordEnd"),row.end);
  makeFocusWindow(row);
  timingFeedback();
  all(".word-chip,.timeline-word").forEach(b=>{b.classList.toggle("selected",+b.dataset.index===index);b.setAttribute("aria-pressed",+b.dataset.index===index);});
  if(seek)wordTimeline.select(index,true);
  if(seek&&Number.isFinite(row.start)) {
    video.currentTime=row.start;
    const canvas=$("timelineCanvas"),scroller=$("timelineScroll");
    const x=row.start/state.project.duration*canvas.offsetWidth;
    if(x<scroller.scrollLeft||x>scroller.scrollLeft+scroller.clientWidth)scroller.scrollLeft=Math.max(0,x-scroller.clientWidth/3);
  }
}
async function saveTiming() {
 if(state.selected===null||busy())return;
 const a=$("wordStart").value,b=$("wordEnd").value,start=wordTime("start"),end=wordTime("end"),index=state.selected;
 let problem="";
 if(a===""||b==="")problem="צריך לקבוע גם מתי המילה מתחילה וגם מתי היא נגמרת.";
 else if(!Number.isFinite(start)||!Number.isFinite(end))problem="יש להזין דקות:שניות.מאיות, למשל 03:26.16.";
 else if(!Number.isFinite(start)||!Number.isFinite(end)||start<0||end>state.project.duration)problem="הזמנים צריכים להיות בתוך הסרטון. אפשר לבחור אותם בעזרת כפתורי ההתחלה והסיום.";
 else if(Math.round(end*100)<=Math.round(start*100))problem="סיום המילה צריך להיות אחרי ההתחלה שלה. נגנו עוד קצת ובחרו שוב את הסיום.";
 else if(start<timingLimits().min)problem="ההתחלה חופפת למילה הקודמת. בחרו זמן מאוחר יותר, או תקנו קודם את סוף המילה הקודמת.";
 else if(end>timingLimits().max)problem="הסיום חופף למילה הבאה. בחרו זמן מוקדם יותר, או תקנו קודם את תחילת המילה הבאה.";
 fieldError("timingError",problem);if(problem)return;
 const before=CaptionWordEditing.copy(words()),time=video.currentTime;
 let after=before.map(row=>row.index===index?{...row,start,end}:({...row}));
 const ordered=CaptionWordEditing.ordered(after),at=ordered.findIndex(row=>row.index===index);
 if(end!==before[index].end&&ordered[at+1])ordered[at+1].start=end;
 after=CaptionWordEditing.fillGaps(after,state.project.duration);
 state.busy=true;updateControls();
 $("saveTiming").disabled=true;
 try{
  await persistSettings();
  const data=await api(projectApi("/timings"),{method:"PATCH",body:JSON.stringify({words:after.filter(row=>row.index===index||row.start!==before[row.index].start||row.end!==before[row.index].end).map(row=>({index:row.index,start:row.start,end:row.end,listened:row.index===index&&$("listenedWord").checked}))})});
  loadProject(data);
  invalidateRedo();wordTimeline.history.push(CaptionShortcuts.mark({before,after:CaptionWordEditing.copy(data.report.words),selected:[index],time}));wordTimeline.future=[];
  notice("התזמון נשמר. אפשר להמשיך לעצב או להוריד.");
 }finally{state.busy=false;$("saveTiming").disabled=false;updateControls();}
}

function timingLimits(){
 const ordered=displayWords(),index=ordered.findIndex(row=>row.index===state.selected);
 return {min:Number.isFinite(ordered[index-1]?.end)?ordered[index-1].end:0,
  max:Number.isFinite(ordered[index+1]?.start)?ordered[index+1].start:state.project?.duration||0};
}
function updateFocusRange(){
 if(!focusWindow)return;
 const span=focusWindow.end-focusWindow.start;
 const percent=t=>Math.max(0,Math.min(100,(t-focusWindow.start)/span*100));
 const a=wordTime("start"),b=wordTime("end"),hasStart=Number.isFinite(a),hasEnd=Number.isFinite(b);
 $("focusStart").hidden=!hasStart;$("focusEnd").hidden=!hasEnd;$("focusRange").hidden=!hasStart||!hasEnd||b<=a;
 $("focusStart").style.left=percent(hasStart?a:focusWindow.start)+"%";$("focusEnd").style.left=percent(hasEnd?b:focusWindow.end)+"%";
 $("focusRange").style.left=percent(hasStart?a:focusWindow.start)+"%";$("focusRange").style.width=hasStart&&hasEnd?Math.max(0,percent(b)-percent(a))+"%":"0%";
 $("focusTime").textContent=timeLabel(focusWindow.start)+" – "+timeLabel(focusWindow.end);
}
function makeFocusWindow(row){
 const at=row.start??video.currentTime,end=row.end??Math.min(state.project.duration,at+.4);
 const window={start:Math.max(0,at-.5),end:Math.min(state.project.duration,Math.max(at+1,end+.5))};
 // A malformed long word still gets a bounded, useful listening window.
 window.end=Math.min(window.end,window.start+10);focusWindow=window;
 $("focusWave").replaceChildren();$("wordWave").classList.add("loading");
 const id=state.project.id;
 api(projectApi("/waveform?start="+window.start+"&end="+window.end)).then(values=>{
  if(focusWindow!==window||state.project?.id!==id)return;
  const path=document.createElementNS("http://www.w3.org/2000/svg","path");
  path.setAttribute("d",values.map((value,i)=>{const h=Math.max(.4,value*29);return "M"+(i+.5)+" "+(32-h)+"v"+(h*2);}).join(""));
  $("focusWave").replaceChildren(path);$("wordWave").classList.remove("loading");
 }).catch(()=>{if(focusWindow===window){$("wordWave").classList.remove("loading");$("focusTime").textContent="גל הקול לא זמין — אפשר להאזין";}});
}
function editTiming(values,edge){
 if(!values||busy())return;
 CaptionTimecode.write($("wordStart"),values.start);CaptionTimecode.write($("wordEnd"),values.end);
 $("listenedWord").checked=false;fieldError("timingError","");video.pause();
 video.currentTime=values[edge||"start"];timingFeedback();
 $("timingReason").textContent="תצוגת תיקון — האזינו לטווח שבחרתם ושמרו בסיום. הטקסט עצמו נשאר בדיוק.";
}
for(const edge of ["start","end"]){
 const handle=$(edge==="start"?"focusStart":"focusEnd");
 handle.addEventListener("pointerdown",e=>{
  if(busy()||e.button!==0||!focusWindow)return;e.preventDefault();video.pause();
  focusDrag={id:e.pointerId,edge,rect:$("wordWave").getBoundingClientRect(),window:focusWindow};handle.setPointerCapture(e.pointerId);
 });
 handle.addEventListener("pointermove",e=>{
  if(!focusDrag||focusDrag.id!==e.pointerId||focusDrag.edge!==edge)return;
  const value=focusDrag.window.start+(e.clientX-focusDrag.rect.left)/focusDrag.rect.width*(focusDrag.window.end-focusDrag.window.start),limits=timingLimits();
  editTiming(CaptionEditing.edge(wordTime("start"),wordTime("end"),edge,value,limits.min,limits.max),edge);
 });
 for(const event of ["pointerup","pointercancel"])handle.addEventListener(event,e=>{focusDrag=null;if(handle.hasPointerCapture(e.pointerId))handle.releasePointerCapture(e.pointerId);});
 handle.addEventListener("keydown",e=>{
  if(!["ArrowLeft","ArrowRight"].includes(e.key)||busy())return;e.preventDefault();e.stopPropagation();
  const limits=timingLimits(),value=wordTime(edge)+(e.key==="ArrowRight"?1:-1)*(e.shiftKey?.05:.01);
  editTiming(CaptionEditing.edge(wordTime("start"),wordTime("end"),edge,value,limits.min,limits.max),edge);
 });
}
for(const [id,delta] of [["nudgeEarlier",-.05],["nudgeLater",.05]])$(id).addEventListener("click",()=>{
 if($("wordStart").value===""||$("wordEnd").value==="")return;
 const limits=timingLimits();editTiming(CaptionEditing.shift(wordTime("start"),wordTime("end"),delta,limits.min,limits.max));
});
$("wordWave").addEventListener("click",e=>{
 if(!focusWindow||e.target.closest("button"))return;
 const rect=$("wordWave").getBoundingClientRect();video.pause();video.currentTime=focusWindow.start+(e.clientX-rect.left)/rect.width*(focusWindow.end-focusWindow.start);
});

function segmentProblem(){
 let end=0;
 for(let i=0;i<cueDraft.length;i++){
  const cue=cueDraft[i];
  if(!Number.isFinite(cue.start)||!Number.isFinite(cue.end))return "קטע "+(i+1)+": יש להזין זמן בפורמט דקות:שניות.מאיות, למשל 03:26.16.";
  if(!Number.isFinite(cue.start)||!Number.isFinite(cue.end)||cue.start<0||cue.end>state.project.duration||Math.round(cue.end*100)<=Math.round(cue.start*100))return "קטע "+(i+1)+": הסיום צריך להיות אחרי ההתחלה ובתוך הסרטון.";
  if(cue.start<end)return "קטע "+(i+1)+": ההתחלה חופפת לסיום הקטע הקודם.";
  end=cue.end;
 }
 return "";
}
function segmentTime(index,key,input){
 return CaptionTimecode.read(input,key==="start"?(cueDraft[index-1]?.end??0):0,
  key==="end"?(cueDraft[index+1]?.start??state.project.duration):state.project.duration);
}
function restoreSegments(data,changed){
 if(changed){clearTimeout(cueTimer);cueDirty=false;cueRevision++;}
 if(cueDirty&&!changed)return;
 cueDraft=(data.segments||[]).map(cue=>({...cue}));
 try{
  const draft=JSON.parse(local.get("katuv-segments-"+data.id));
  if(draft?.source===data.transcript&&draft.fingerprint===data.fingerprint){cueDraft=draft.cues;cueDirty=true;}
 }catch{}
 passageTimeline.invalidate();
 if(changed)passageTimeline.reset();
 renderSegments();
}
function renderSegments(){
 $("segmentCards").replaceChildren();
 $("segmentLayout").value=cueDraft.map(cue=>cue.text).join("\n\n");
 cueDraft.forEach((cue,i)=>{
  const card=document.createElement("article");card.className="segment-card";
  const name=document.createElement("input");name.className="segment-name";name.value=cue.label;name.maxLength=80;name.setAttribute("aria-label","שם קטע "+(i+1));
  name.addEventListener("input",()=>editCue(i,"label",name.value));card.append(name);
  const excerpt=document.createElement("p");excerpt.className="segment-excerpt";excerpt.textContent=cue.text.split("\n")[0];card.append(excerpt);
  const text=document.createElement("p");text.className="segment-text";text.dir="auto";text.textContent=cue.text;
  const times=document.createElement("div");times.className="segment-times";
  for(const [key,title] of [["start","התחלה"],["end","סיום"]]){
   const wrap=document.createElement("div"),label=document.createElement("label"),input=document.createElement("input");
   input.id="segment-"+i+"-"+key;input.type="text";input.dataset.timecode="";input.placeholder="00:00.00";input.dir="ltr";input.spellcheck=false;input.autocomplete="off";
   CaptionTimecode.write(input,cue[key]);
   input.setAttribute("aria-label",title+" של קטע "+(i+1));input.setAttribute("aria-describedby","segmentTimecodeHint");label.htmlFor=input.id;label.textContent=title;
   input.addEventListener("input",()=>{markTimeInput(input);editCue(i,key,segmentTime(i,key,input));});
   input.addEventListener("blur",()=>{const time=segmentTime(i,key,input);if(Number.isFinite(time))CaptionTimecode.write(input,time);});
   input.addEventListener("keydown",e=>{if(e.key==="Enter"){e.preventDefault();input.blur();}});
   const field=document.createElement("div"),copy=document.createElement("button");field.className="timecode-field";
   copy.type="button";copy.className="timecode-copy";copy.textContent="⧉";copy.title="העתקת זמן ה"+title;copy.setAttribute("aria-label","העתקת "+title+" של קטע "+(i+1));
   copy.addEventListener("click",()=>copyTime(segmentTime(i,key,input),input));field.append(input,copy);
   const capture=document.createElement("button");capture.className="secondary";capture.textContent=title+" כאן";capture.setAttribute("aria-label",title+" כאן לקטע "+(i+1));
   capture.addEventListener("click",()=>{video.pause();CaptionTimecode.write(input,video.currentTime);editCue(i,key,segmentTime(i,key,input));});
   wrap.append(label,field,capture);times.append(wrap);
  }
  card.append(times);
  const preview=document.createElement("button");preview.className="text-button segment-preview";preview.textContent="הצגת הקטע על הסרטון";preview.setAttribute("aria-label","הצגת קטע "+(i+1)+" על הסרטון");
  preview.addEventListener("click",()=>{const cue=cueDraft[i];if(Number.isFinite(cue.start)&&cue.end>cue.start){passageTimeline.select(i);video.pause();video.currentTime=cue.start+Math.min(.12,(cue.end-cue.start)/2);renderSubtitle();}});
  card.append(preview);
  const disclosure=document.createElement("details"),summary=document.createElement("summary");disclosure.className="segment-words";summary.textContent="כל המילים בקטע";disclosure.append(summary,text);card.append(disclosure);
  $("segmentCards").append(card);
 });
 fieldError("segmentsError",cueDirty?segmentProblem():"");
 $("segmentSaveStatus").textContent=cueDirty?"טיוטת תזמון במחשב":"נשמר במחשב";

}
function editCue(index,key,value,{timeline=false,save=true}={}){
 Object.assign(cueDraft[index],typeof key==="object"?key:{[key]:value});
 if(!timeline)passageTimeline.invalidate();
 else for(const edge of ["start","end"]){const input=$("segment-"+index+"-"+edge);if(input)CaptionTimecode.write(input,cueDraft[index][edge]);}
 updateCueDraft(save);
}
function replaceCues(cues,selected){
 cueDraft=cues.map(cue=>({...cue}));passageTimeline.selected=selected;
 renderSegments();updateCueDraft(false);
}
function updateCueDraft(save=true){
 cueDirty=true;cueRevision++;
 local.set("katuv-segments-"+state.project.id,JSON.stringify({source:state.project.transcript,fingerprint:state.project.fingerprint,cues:cueDraft}));
 state.project.export_ready=false;state.captionKey="";rebuildLines();renderSubtitle();updateControls();
 const problem=segmentProblem();fieldError("segmentsError",problem);
 $("segmentSaveStatus").textContent=problem?"הטיוטה נשמרה · יש להשלים את הזמנים":"שומרים…";
 clearTimeout(cueTimer);if(!problem&&save)cueTimer=setTimeout(()=>guard(saveSegments),500);
 passageTimeline.refresh();
}
function saveSegments(){
 clearTimeout(cueTimer);
 if(!cueDirty)return cueRequest;
 const problem=segmentProblem();if(problem){fieldError("segmentsError",problem);return Promise.reject(new Error(problem));}
 const id=state.project.id,revision=cueRevision,cues=cueDraft.map(cue=>({...cue}));
 cueRequest=cueRequest.catch(()=>{}).then(async()=>{
  const data=await api("/api/projects/"+id+"/segments",{method:"PUT",body:JSON.stringify({segments:cues})});
  if(state.project?.id!==id)return;
  state.project.segments=data.segments;state.project.export_ready=false;
  if(revision===cueRevision){cueDirty=false;local.remove("katuv-segments-"+id);$("segmentSaveStatus").textContent="נשמר במחשב";}
  updateControls();
 }).catch(error=>{$("segmentSaveStatus").textContent="הטיוטה נשמרה במחשב · לחצו לשמירה חוזרת";passageTimeline.refresh();throw error;});
 return cueRequest;
}
function syncWordDraft(){
 draftLines=null;state.captionKey="";
 const row=words()[state.selected];
 if(row){CaptionTimecode.write($("wordStart"),row.start);CaptionTimecode.write($("wordEnd"),row.end);updateFocusRange();}
 rebuildLines();renderSubtitle();
}
function discardWordDraft(){wordDraft=null;wordDraftReordered=false;wordGesture=false;syncWordDraft();updateControls();}
async function commitWordDraft(reorder,edit_kind="manual"){
 const id=state.project.id,base=state.project.report.words;
 const changes=wordDraft?CaptionWordEditing.changed(base,wordDraft):[];
 if(!changes.length){discardWordDraft();return true;}
 const allow_reorder=!!reorder&&wordDraft.some((row,i)=>i>0&&row.start<wordDraft[i-1].start);
 wordGesture=false;wordSaving=true;updateControls();
 try{
  await persistSettings();
  const data=await api("/api/projects/"+id+"/timings",{method:"PATCH",body:JSON.stringify({allow_reorder,edit_kind,
   words:changes.map(row=>({index:row.index,start:row.start,end:row.end,listened:false}))})});
  wordDraft=null;wordDraftReordered=false;
  if(state.project?.id===id)loadProject(data);
  return true;
 }catch(error){discardWordDraft();notice(error.message,true);return false;}
 finally{wordSaving=false;syncWordDraft();updateControls();}
}
const wordTimeline=new CaptionWordTimeline({
 read:()=>({rows:words(),duration:state.project?.duration||0,time:video.currentTime,wave:state.wave,saving:wordSaving,
  disabled:state.uploading||state.busy||wordSaving||(!wordGesture&&timingDirty())}),
 begin:()=>{video.pause();state.stopAt=null;wordGesture=true;updateControls();},
 change:(rows,reordered)=>{wordDraft=rows;wordDraftReordered=reordered;syncWordDraft();},
 discard:discardWordDraft,
 preview:time=>{video.currentTime=time;updatePlayback();},
 commit:commitWordDraft,historyChanged:updateHistoryButtons,edited:invalidateRedo
});
const passageTimeline=new CaptionPassageTimeline({
 read:()=>({cues:cueDraft,duration:state.project?.duration||0,time:video.currentTime,wave:state.wave,words:words(),
  disabled:busy()||!!segmentProblem(),status:$("segmentSaveStatus").textContent}),
 begin:()=>{clearTimeout(cueTimer);video.pause();state.stopAt=null;},
 change:(index,times)=>editCue(index,times,null,{timeline:true,save:false}),
 replace:replaceCues,
 preview:time=>{video.currentTime=time;updatePlayback();},
 commit:()=>guard(saveSegments),historyChanged:updateHistoryButtons,edited:invalidateRedo
});
const passageAnchor=document.createComment("passage timeline position"),passageMobile=matchMedia("(max-width:760px)");
$("passageTimeline").before(passageAnchor);
const wordTimelineAnchor=document.createComment("word timeline position");$("timelineDetails").before(wordTimelineAnchor);
function placePassageTimeline(){
 if(passageMobile.matches)$("workspace").append($("passageTimeline"),$("timelineDetails"));
 else {passageAnchor.after($("passageTimeline"));wordTimelineAnchor.after($("timelineDetails"));}
 passageTimeline.refresh();
 wordTimeline.refresh();
}
passageMobile.addEventListener("change",placePassageTimeline);placePassageTimeline();
async function prepareSegments(layout=""){
 if(!state.project||busy())return;
 await persistSettings();
 if(cueDirty&&!segmentProblem())await saveSegments();
 state.busy=true;updateControls();
 try{
  const data=await api(projectApi("/segments/prepare"),{method:"POST",body:JSON.stringify({transcript:layout})});
  cueDirty=false;local.remove("katuv-segments-"+data.id);clearDraft();
  loadProject(data);showInspector("timing",true);revealFirstCaption();
 }finally{state.busy=false;updateControls();}
}
$("segmentCaptionMode").addEventListener("click",()=>guard(()=>prepareSegments()));
$("startSegments").addEventListener("click",()=>guard(()=>prepareSegments($("transcript").value)));
$("applySegmentLayout").addEventListener("click",()=>guard(()=>prepareSegments($("segmentLayout").value)));
$("saveSegments").addEventListener("click",()=>guard(saveSegments));
$("wordCaptionMode").addEventListener("click",()=>{
 if(busy())return;
 state.editing=!state.project?.report;state.settings.caption_mode="words";
 changeSettings({caption_mode:"words"});updateControls();showInspector("design");
 guard(async()=>{await persistSettings();await wordTimeline.closeGaps();});
});

function rebuildLines() {
  lineCache=isSegments()?CaptionTimeline.segments(cueDraft):CaptionTimeline.group(displayWords(),state.settings.words_per_line,state.project?.duration||0);
}
function revealFirstCaption(){
 const at=CaptionTimeline.firstTime(lineCache);
 if(at!==null&&video.readyState>=1&&video.paused){video.currentTime=at;renderSubtitle();}
}

function fitViewer() {
  const stage=$("dropStage"), ratio=state.project?state.project.width/state.project.height:9/16;
  if(!state.project||!stage.clientWidth)return;
  const stageStyle=getComputedStyle(stage);
  const available=Math.max(1,stage.clientHeight-parseFloat(stageStyle.paddingTop)-parseFloat(stageStyle.paddingBottom));
  const availableWidth=Math.max(1,stage.clientWidth-parseFloat(stageStyle.paddingLeft)-parseFloat(stageStyle.paddingRight));
  const maxHeight=Math.min(available,parseFloat(getComputedStyle($("videoShell")).maxHeight)||available);
  const height=Math.min(available,maxHeight,availableWidth/ratio);
  $("videoShell").style.width=height*ratio+"px";
  $("videoShell").style.height=height+"px";
  $("videoShell").style.aspectRatio=String(ratio);
  document.documentElement.style.setProperty("--preview-height",document.querySelector(".preview-panel").offsetHeight+"px");
  state.captionKey="";
  renderSubtitle();
}
function measureCaption(line) {
  const s=state.settings,p=state.project;
  const requested=Math.max(1,Math.round(s.font_size*p.height/1920));
  measure.font="700 "+requested+"px Hebrew";
  const lengths=line.map(w=>measure.measureText(w.text).width);
  const spec=CaptionMotion.customize(state.motions[s.style],s);
  const layout=line[0].block?CaptionMotion.blockLayout(line[0].text.split("\n").map(text=>measure.measureText(text).width),requested,p.width,p.height,s,spec):CaptionMotion.layout(lengths,measure.measureText(" ").width,requested,p.width,p.height,s,spec);
  // libass sizes glyphs by ascent + descent, CSS by unitsPerEm. Noto's
  // bundled metrics are 1068 + 292 / 1000; keep the live preview at export scale.
  return {...layout,font:layout.size*(1000/1360)*$("videoShell").clientWidth/p.width};
}
function paintMotion(container, rows, time, settings, spec, pixelScale) {
  const motion=CaptionMotion.line(spec,rows,time);
  container.style.opacity=motion.opacity;
  container.style.translate="0 "+motion.y*pixelScale+"px";
  [...container.children].forEach((span,i)=>{
    const row=rows[i],start=CaptionMotion.cs(row.start),end=CaptionMotion.cs(row.end);
    const elapsed=(time-start)*1000,duration=Math.max(1,(end-start)*1000);
    span.style.backgroundImage="none";
    const animate=settings.word_animation!==false&&settings.caption_mode!=="segments"&&!row.block;
    span.classList.toggle("karaoke-fill",animate&&spec.mode==="karaoke");
    if(!animate){span.style.color=settings.text_color;span.style.fontSize="100%";}
    else if(spec.mode==="karaoke"){
      const progress=Math.max(0,Math.min(100,elapsed/duration*100));
      span.style.backgroundImage=`linear-gradient(to right, ${settings.highlight_color} ${progress}%, ${settings.text_color} ${progress}%)`;
      span.style.color="transparent";span.style.fontSize="100%";
    }else{
      const amount=CaptionMotion.colorAmount(spec,elapsed,duration);
      span.style.color=CaptionMotion.mix(settings.text_color,settings.highlight_color,amount);
      span.style.fontSize=CaptionMotion.pulse(spec,elapsed,duration)*100+"%";
    }
  });
  return motion;
}
function addMotionWords(container,rows){
  container.replaceChildren();rows.forEach((row,i)=>{
    if(i)container.append(document.createTextNode(" "));
    const span=document.createElement("span");span.textContent=row.text;container.append(span);
  });
}
function renderSubtitle() {
  const caption=$("caption"),s=state.settings,spec=CaptionMotion.customize(state.motions[s.style],s);
  $("captionBox").hidden=true;
  $("captionBackdrop").hidden=true;
  $("canvasEditBar").hidden=!state.project||state.editing||!$("designOptions").open||busy();
  $("canvasWord").textContent="לכתובית הקרובה";$("canvasWord").dataset.index="";
  if(!state.project||!spec||!hasCaptions()||state.busy&&state.project.state==="aligning"){caption.textContent="";state.captionKey="";return;}
  const now=video.currentTime;
  const displayLines=!isSegments()&&document.body.dataset.inspector==="timing"&&draftLines?draftLines:lineCache;
  const line=displayLines.find(rows=>now>=CaptionMotion.cs(rows[0].start)&&now<CaptionMotion.cs(rows.at(-1).end));
  if(!line){caption.textContent="";state.captionKey="";return;}
  const key=line[0].index+":"+line.map(w=>w.text).join(" ")+":"+JSON.stringify(s)+":"+caption.parentElement.clientWidth;
  if(state.captionKey!==key) {
    caption.classList.toggle("block-caption",!!line[0].block);
    addMotionWords(caption,line);
    captionLayout=measureCaption(line);
    caption.style.fontSize=captionLayout.font+"px";
    caption.style.top=captionLayout.y/state.project.height*100+"%";
    caption.style.left=captionLayout.x/state.project.width*100+"%";
    caption.style.transform=`translate(-50%,-50%) rotate(${s.rotation}deg)`;
    caption.style.setProperty("--stroke",s.outline*$("videoShell").clientHeight/1920+"px");
    state.captionKey=key;
  }
  const motion=paintMotion(caption,line,now,s,spec,$("videoShell").clientHeight/1920);
  const ratio=$("videoShell").clientWidth/state.project.width;
  if(s.backdrop_opacity>0){
    const bg=$("captionBackdrop"),w=(captionLayout.boxW+2*captionLayout.padding)*ratio,h=(captionLayout.boxH+2*captionLayout.padding)*ratio;
    bg.hidden=false;bg.style.width=w+"px";bg.style.height=h+"px";bg.style.borderRadius=Math.min(h*.24,w*.12)+"px";
    bg.style.left=caption.style.left;bg.style.top=caption.style.top;bg.style.transform=caption.style.transform;
    bg.style.translate=caption.style.translate;bg.style.opacity=motion.opacity*s.backdrop_opacity;
  }
  const active=line.find(row=>now>=CaptionMotion.cs(row.start)&&now<CaptionMotion.cs(row.end));
  const target=active||line.find(row=>row.start>=now)||line.at(-1);
  $("canvasWord").textContent=isSegments()?"תזמון: "+(target.label||"קטע "+(target.index+1)):"תזמון: "+target.text;$("canvasWord").dataset.index=target.index;
  if($("designOptions").open&&!busy()){
    const box=$("captionBox"),ratio=$("videoShell").clientWidth/state.project.width;
    box.hidden=false;box.style.left=caption.style.left;box.style.top=caption.style.top;box.style.transform=caption.style.transform;
    box.style.width=Math.max(44,(captionLayout.boxW+captionLayout.padding*2)*ratio+12)+"px";box.style.height=Math.max(32,(captionLayout.boxH+captionLayout.padding*2)*ratio+8)+"px";
  }
}
const demoWords=[{text:"כל",start:.25,end:.84},{text:"מילה",start:.94,end:1.65},{text:"בזמן.",start:1.7,end:2.55}];
const demoCards=all("[data-demo]");
demoCards.forEach(card=>addMotionWords(card,demoWords));
function animateDemos(timestamp){
  if(!$("designOptions").open||document.hidden)return;
  const now=state.demosPaused?1.3:(timestamp/1000)%3.1;
  demoCards.forEach(card=>{const spec=CaptionMotion.customize(state.motions[card.dataset.demo],state.settings);if(spec)paintMotion(card,demoWords,now,state.settings,spec,.28);});
}
function demoButtonLabel(){
  $("toggleDemos").textContent=state.demosPaused?"ניגון הדוגמאות":"עצירת הדוגמאות";
  $("toggleDemos").setAttribute("aria-pressed",state.demosPaused);
}
function drawWave() {
  const wave=state.wave;if(!wave.length){$("waveform").replaceChildren();return;}
  const path=document.createElementNS("http://www.w3.org/2000/svg","path");
  path.setAttribute("d",wave.map((value,i)=>{const h=Math.max(.7,value*20);return "M"+(i*2+1)+" "+(24-h)+"v"+(h*2);}).join(""));
  $("waveform").replaceChildren(path);
}
function drawTimeline() {
  wordTimeline.refresh();
}
function updatePlayback() {
  const now=video.currentTime;
  $("currentTime").textContent=timeLabel(now);
  $("seek").value=now;
  passageTimeline.playback();
  wordTimeline.playback();
  if(focusWindow){const fraction=(now-focusWindow.start)/(focusWindow.end-focusWindow.start);$("focusPlayhead").hidden=fraction<0||fraction>1;$("focusPlayhead").style.left=Math.max(0,Math.min(100,fraction*100))+"%";}
  if(state.project) {
    $("playhead").style.left=now/state.project.duration*100+"%";
    all(".timeline-word,.word-chip").forEach(b=>{
      const w=words()[+b.dataset.index],speaking=w&&now>=w.start&&now<w.end;
      b.classList.toggle(b.classList.contains("timeline-word")?"active":"speaking",speaking);
    });
  }
  if(state.stopAt!==null&&now>=state.stopAt){video.pause();state.stopAt=null;}
  renderSubtitle();
}
function tick(timestamp) {if(!video.paused)updatePlayback();animateDemos(timestamp);requestAnimationFrame(tick);}
function togglePlay() {
  if(!state.project)return;
  if(video.paused)video.play().catch(()=>notice("הדפדפן לא מצליח לנגן את הסרטון הזה. נסו לפתוח ב־Chrome או לבחור קובץ MP4 אחר.",true));
  else video.pause();
}
async function copyTime(seconds,input=null){
 const text=CaptionTimecode.format(seconds);
 if(!text)return notice("יש להזין זמן תקין להעתקה, למשל 03:26.16.");
 try{
  await navigator.clipboard.writeText(text);
 }catch{
  // Local browsers without Clipboard API can still copy from a user gesture.
  const previous=document.activeElement,field=document.createElement("textarea");
  field.value=text;field.readOnly=true;field.style.cssText="position:fixed;left:-9999px;top:0";document.body.append(field);field.select();
  let copied=false;try{copied=document.execCommand("copy");}catch{}finally{field.remove();previous?.focus({preventScroll:true});}
  if(!copied){input?.focus();input?.select();return notice(input?"הזמן סומן. אפשר להעתיק עם Ctrl+C.":"ההעתקה לא התאפשרה. אפשר לסמן את הזמן ולהעתיק עם Ctrl+C.");}
 }
 notice("הועתק: \u2066"+text+"\u2069");
}
function setJob(data,track=true) {
  const ongoing=processingStates.includes(data.state);
  const starting=ongoing&&$("jobBanner").hidden;
  $("jobBanner").hidden=!ongoing;
  if(!ongoing)return;
  if(starting){document.querySelector(".content-panel").scrollTop=0;$("jobBanner").scrollIntoView({block:"nearest",behavior:"smooth"});}
  const alignment=data.state!=="exporting";
  $("jobTitle").textContent=data.state==="verifying"?"בודקים את המילים מול הקול":alignment?(data.mode==="exact"?"מוצאים את הרגע של כל מילה":"מקשיבים ויוצרים כתוביות"):"מכינים את הסרטון להורדה";
  $("jobDetail").textContent=alignment?(data.message||"זה עשוי לקחת כמה דקות, במיוחד בפעם הראשונה. הכול מתבצע במחשב שלכם."):Math.round(data.progress*100)+"% הושלמו. מוסיפים את הכתוביות ושומרים את הקול המקורי.";
  $("jobProgress").parentElement.classList.toggle("indeterminate",alignment);
  $("jobProgress").style.width=alignment?"30%":data.progress*100+"%";
  if(track)schedulePoll();
}
function schedulePoll() {
  clearTimeout(state.poll);
  state.poll=setTimeout(async()=>{
    if(!state.project)return;
    const id=state.project.id;
    try {
      const data=await api("/api/projects/"+id);
      if(state.project?.id!==id)return;
      loadProject(data);
      if(!processingStates.includes(data.state)) {
        refreshModels().catch(()=>{});
        if(data.state==="error")notice(data.message,true);
        else if(data.message&&data.state==="review")notice(data.message,invalidIndexes().size>0);
      }
    } catch(e){notice(e.message,true);state.poll=setTimeout(schedulePoll,2000);}
  },900);
}
function showDownload() {
  $("downloadLink").href=projectApi("/file/export");
  $("downloadBanner").hidden=false;
}
async function startAlignment() {
 if(busy()||!state.project)return;
 if(state.mode==="exact"&&[...$("transcript").value].length>50000){
  fieldError("transcriptError","הטקסט ארוך מדי לסרטון אחד. פצלו את העבודה לסרטונים קצרים יותר. הטקסט שהדבקתם נשאר כאן במלואו.");
  $("transcript").setAttribute("aria-invalid","true");$("transcript").focus();return;
 }
 if(state.mode==="exact"&&!tokens($("transcript").value).length){
  fieldError("transcriptError","קודם מדביקים כאן את מה שנאמר בסרטון. אין לכם טקסט? בחרו ״אין לי טקסט״.");
  $("transcript").setAttribute("aria-invalid","true");$("transcript").focus();return;
 }
 saveDraft();state.busy=true;updateControls();
 $("errorBanner").hidden=true;setJob({...state.project,state:"aligning",mode:state.mode,message:""},false);
 try{
  await persistSettings();video.pause();state.captionKey="";$("caption").textContent="";
  await api(projectApi("/align"),{method:"POST",body:JSON.stringify({mode:state.mode,transcript:state.mode==="exact"?$("transcript").value:""})});
  clearDraft();state.project.state="aligning";state.project.export_ready=false;state.project.message="";
  updateControls();setJob({...state.project,mode:state.mode});
 }catch(error){state.busy=false;$("jobBanner").hidden=true;updateControls();throw error;}
}

async function exportVideo() {
 if(isSegments()&&cueDirty)await saveSegments();
 if(timingDirty()){showInspector("timing",true);notice("שמרו או בטלו את תיקון התזמון לפני ההורדה.");return;}
 if(busy()||!state.project)return;
 if(!isSegments()&&invalidIndexes().size){showInspector("timing",true);return;}
 state.busy=true;updateControls();$("errorBanner").hidden=true;setJob({...state.project,state:"exporting",progress:0},false);
 try{
  await persistSettings();await api(projectApi("/export"),{method:"POST"});
  state.project.state="exporting";state.project.progress=0;state.project.export_ready=false;
  updateControls();setJob(state.project);
 }catch(error){state.busy=false;$("jobBanner").hidden=true;updateControls();throw error;}
}

$("exportResolution").addEventListener("change",()=>changeSettings({export_resolution:$("exportResolution").value}));

function upload(file) {
  if(busy())return;
  if(!file)return;
  if(!/\.mp4$/i.test(file.name)){notice("הקובץ שבחרתם אינו סרטון MP4. בחרו סרטון עם הסיומת .mp4.",true);return;}
  if(file.size>2*1024**3){notice("הסרטון גדול מ־2 ג׳יגה־בייט. בחרו קטע קצר יותר.",true);return;}
  saveDraft();
  video.pause();
  state.uploading=true;updateControls();
  $("uploadProgress").hidden=false;$("uploadProgress").firstElementChild.style.width="0";
  $("videoLoading").hidden=false;$("uploadStatus").hidden=false;$("errorBanner").hidden=true;
  const draft=$("transcript").value, settings={...state.settings}, mode=state.mode;
  const form=new FormData();form.append("video",file);
  const xhr=new XMLHttpRequest();xhr.open("POST","/api/projects");xhr.responseType="json";
  xhr.upload.onprogress=e=>{if(e.lengthComputable)$("uploadProgress").firstElementChild.style.width=e.loaded/e.total*100+"%";};
  xhr.onload=async()=>{
    state.uploading=false;$("uploadProgress").hidden=true;$("videoLoading").hidden=true;$("uploadStatus").hidden=true;
    if(xhr.status<200||xhr.status>=300){notice(typeof xhr.response?.detail==="string"?xhr.response.detail:"לא ניתן לפתוח את הקובץ",true);updateControls();return;}
    loadProject(xhr.response);
    state.mode=mode;state.settings=settings;showSettings();
    $("transcript").value=draft;state.settingsDirty=true;saveDraft();updateControls();
    notice("הסרטון נפתח. עכשיו מוסיפים את המילים.");
  };
  xhr.onerror=()=>{state.uploading=false;$("uploadProgress").hidden=true;$("videoLoading").hidden=true;$("uploadStatus").hidden=true;notice("החיבור ליישום המקומי נותק. פתחו אותו מחדש.",true);updateControls();};
  xhr.send(form);
}
async function previewFrame() {
  await persistSettings();
  if(isSegments()&&cueDirty)await saveSegments();
  video.pause();
  $("frameButton").disabled=true;
  notice("מכינים תמונה עם הכתוביות הסופיות…");
  try {
    const at=Math.min(video.currentTime,state.project.duration-.04);
    const result=await fetch(projectApi("/preview?at="+at));
    if(!result.ok)throw new Error("לא ניתן ליצור תצוגה כרגע. בדקו שכל המילים מתוזמנות.");
    if(frameUrl)URL.revokeObjectURL(frameUrl);
    frameUrl=URL.createObjectURL(await result.blob());
    $("frameImage").src=frameUrl;$("frameDialog").showModal();$("toast").hidden=true;
  } finally {updateControls();}
}
async function showProjects() {
  await persistSettings();saveDraft();
  const projects=await api("/api/projects");
  $("projectList").replaceChildren();
  if(!projects.length){const p=document.createElement("p");p.textContent="עדיין אין כאן סרטונים. התחילו מבחירת סרטון מהמחשב.";p.className="muted";$("projectList").append(p);}
  projects.forEach(project=>{
    const b=document.createElement("button");b.className="project-item";
    const copy=document.createElement("span"),name=document.createElement("strong"),detail=document.createElement("small");
    name.textContent=project.name;name.dir="auto";detail.textContent=durationLabel(project.duration)+" · "+new Intl.DateTimeFormat("he-IL",{day:"2-digit",month:"2-digit",year:"numeric",timeZone:"Asia/Jerusalem"}).format(new Date(project.updated*1000));
    if(project.state==="damaged"){detail.textContent=project.message;b.disabled=true;}
    copy.append(name,detail);b.append(copy,document.createTextNode("←"));
    b.addEventListener("click",()=>guard(async()=>{loadProject(await api("/api/projects/"+project.id));$("historyDialog").close();}));
    $("projectList").append(b);
  });
  $("historyDialog").showModal();
}
function reset() {
  saveDraft();
  clearTimeout(cueTimer);cueDraft=[];cueDirty=false;cueRevision++;
  clearTimeout(state.poll);clearTimeout(state.settingsTimer);
  video.pause();video.removeAttribute("src");video.load();
  state.project=null;state.editing=true;state.selected=null;state.wave=[];state.reportKey="";state.captionKey="";
  focusWindow=null;draftLines=null;
  state.settings={...defaults};state.mode="exact";lineCache=[];local.remove("katuv-project");
  $("transcript").value="";$("caption").textContent="";$("timingCard").hidden=true;$("downloadBanner").hidden=true;
  $("sourceName").textContent="הוסיפו את הסרטון שלכם";$("sourceInfo").textContent="גררו לכאן או בחרו קובץ MP4";
  $("projectTitle").textContent="";$("savedStatus").textContent="מקומי ופרטי";
  $("totalTime").textContent="00:00.0";$("aspectLabel").textContent="9:16";$("timelineEmpty").hidden=false;
  $("playhead").style.left="0";$("waveform").replaceChildren();
  state.settingsDirty=false;$("errorBanner").hidden=true;fieldError("transcriptError","");
  $("transcript").removeAttribute("aria-invalid");$("jobBanner").hidden=true;
  showSettings();drawTimeline();updateControls();fitViewer();
}
async function guard(action) {try{await action();}catch(error){notice((error instanceof TypeError?"החיבור לאפליקציה נותק. פתחו שוב את קובץ AutoCaption ונסו פעם נוספת.":error.message)||"משהו השתבש. נסו שוב.",true);}}

["uploadButton","chooseVideo"].forEach(id=>$(id).addEventListener("click",()=>$("videoInput").click()));
$("videoInput").addEventListener("change",e=>{upload(e.target.files[0]);e.target.value="";});
$("uploadText").addEventListener("click",()=>$("textInput").click());
$("textInput").addEventListener("change",e=>guard(async()=>{
 const file=e.target.files[0];if(!file)return;e.target.value="";
 if(file.size>1024*1024)throw new Error("קובץ הטקסט גדול מדי. העתיקו רק את המילים של הסרטון והדביקו אותן בתיבה.");
 try{
  const data=new Uint8Array(await file.arrayBuffer());
  const encoding=data[0]===255&&data[1]===254?"utf-16le":data[0]===254&&data[1]===255?"utf-16be":"utf-8";
  const text=new TextDecoder(encoding,{fatal:true}).decode(data);
  if(text.length>50000)throw new Error("long");
  $("transcript").value=text;
 }catch{throw new Error("לא הצלחנו לקרוא את קובץ הטקסט הזה. פתחו אותו במחשב, העתיקו את המילים והדביקו אותן כאן.");}
 state.mode="exact";fieldError("transcriptError","");$("transcript").removeAttribute("aria-invalid");saveDraft();updateControls();notice("הטקסט נוסף. כדאי לבדוק שכל המילים כאן.");
}));
$("transcript").addEventListener("input",()=>{fieldError("transcriptError","");$("transcript").removeAttribute("aria-invalid");saveDraft();updateControls();});
["exact","auto"].forEach(mode=>$(mode+"Mode").addEventListener("click",()=>{if(busy())return;state.mode=mode;fieldError("transcriptError","");saveDraft();updateControls();}));
$("editTranscript").addEventListener("click",()=>{state.editing=true;video.pause();saveDraft();updateControls();renderSubtitle();});
$("cancelEdit").addEventListener("click",()=>{clearDraft();loadProject(state.project);});
$("alignButton").addEventListener("click",()=>guard(startAlignment));
$("speechModel").addEventListener("change",()=>guard(async()=>{
 if(busy()||modelSaving||!state.project||timingDirty())return;
 const id=state.project.id,model=$("speechModel").value;
 modelSaving=true;updateControls();
 try{
  const data=await api("/api/projects/"+id+"/model",{method:"PUT",body:JSON.stringify({speech_model:model})});
  // Do not reload the project: pasted drafts, selected words and playback stay in place.
  if(state.project?.id===id){state.project.speech_model=data.speech_model;state.project.model_selection=data.model_selection;notice(model==="auto"?"מעכשיו נבחר את המודל לפי המחשב, לפני כל יצירה או בדיקה.":"המודל נבחר. נשתמש בו ביצירה או בבדיקה הבאה.");}
 }finally{modelSaving=false;updateControls();}
}));
$("exportButton").addEventListener("click",()=>guard(exportVideo));
$("saveTiming").addEventListener("click",()=>guard(saveTiming));
$("discardTiming").addEventListener("click",()=>{selectWord(state.selected,false);updateControls();notice("חזרנו לתזמון השמור.");});
$("designTab").addEventListener("click",()=>showInspector("design",true));
$("timingTab").addEventListener("click",()=>showInspector("timing",true));
$("guidesButton").addEventListener("click",()=>{const active=$("videoShell").classList.toggle("show-guides");$("guidesButton").setAttribute("aria-pressed",active);});
for(const id of ["designTab","timingTab"])$(id).addEventListener("keydown",e=>{if(["ArrowRight","ArrowLeft","Home","End"].includes(e.key)){e.preventDefault();const pane=e.key==="Home"?"design":e.key==="End"?"timing":id==="designTab"?"timing":"design";showInspector(pane,true);$(pane==="design"?"designTab":"timingTab").focus();}});
$("verifyButton").addEventListener("click",()=>guard(async()=>{
 if(busy()||!state.project?.report)return;
 await persistSettings();video.pause();
 await api(projectApi("/verify"),{method:"POST",body:JSON.stringify({resolve_manual_conflicts:$("resolveManualConflicts").checked})});
 state.busy=true;state.project.state="verifying";state.project.export_ready=false;state.project.message="";
 updateControls();setJob(state.project);
}));
$("frameButton").addEventListener("click",()=>guard(previewFrame));
$("demoButton").addEventListener("click",()=>guard(async()=>{
 state.uploading=true;updateControls();$("uploadStatus").textContent="פותחים סרטון לדוגמה…";$("uploadStatus").hidden=false;
 try{loadProject(await api("/api/demo",{method:"POST"}));}
 finally{state.uploading=false;$("uploadStatus").hidden=true;$("uploadStatus").textContent="פותחים את הסרטון…";updateControls();}
}));
$("newButton").addEventListener("click",()=>guard(async()=>{await persistSettings();reset();}));
$("historyButton").addEventListener("click",()=>guard(showProjects));
$("helpButton").addEventListener("click",()=>$("helpDialog").showModal());
$("dismissError").addEventListener("click",()=>$("errorBanner").hidden=true);
all(".close-dialog").forEach(b=>b.addEventListener("click",()=>b.closest("dialog").close()));
$("downloadLink").addEventListener("click",()=>$("downloadHelp").hidden=false);
all("[data-style]").forEach(b=>b.addEventListener("click",()=>changeSettings({style:b.dataset.style})));
all("[data-look]").forEach(b=>b.addEventListener("click",()=>changeSettings(looks[b.dataset.look])));
all("[data-color]").forEach(b=>b.addEventListener("click",()=>changeSettings({highlight_color:b.dataset.color})));
all("[data-size]").forEach(b=>b.addEventListener("click",()=>changeSettings({font_size:+b.dataset.size})));
$("wordAnimation").addEventListener("change",e=>changeSettings({word_animation:e.target.checked}));
all("[data-words]").forEach(b=>b.addEventListener("click",()=>changeSettings({words_per_line:+b.dataset.words})));
all("[data-position]").forEach(b=>b.addEventListener("click",()=>changeSettings({position:+b.dataset.position})));
$("customColor").addEventListener("input",e=>changeSettings({highlight_color:e.target.value}));
$("fontSize").addEventListener("input",e=>changeSettings({font_size:+e.target.value}));
$("textColor").addEventListener("input",e=>changeSettings({text_color:e.target.value}));
$("backdropOpacity").addEventListener("input",e=>changeSettings({backdrop_opacity:+e.target.value}));
for(const [id,key] of [["motionStrength","motion_strength"],["motionSpeed","motion_speed"],["rotation","rotation"],["positionX","position_x"],["positionY","position"]])$(id).addEventListener("input",e=>changeSettings({[key]:+e.target.value}));
function restoreStyle(from,to){if(busy()||!from.length)return;to.push(CaptionShortcuts.mark({...state.settings}));changeSettings(from.pop(),false);updateControls();}
$("undoStyle").addEventListener("click",()=>restoreStyle(styleUndo,styleRedo));
$("redoStyle").addEventListener("click",()=>restoreStyle(styleRedo,styleUndo));
$("canvasUndo").addEventListener("click",()=>restoreStyle(styleUndo,styleRedo));
$("canvasSmaller").addEventListener("click",()=>changeSettings({font_size:Math.max(40,state.settings.font_size-5)}));
$("canvasLarger").addEventListener("click",()=>changeSettings({font_size:Math.min(200,state.settings.font_size+5)}));
$("canvasCenter").addEventListener("click",()=>changeSettings({position_x:.5,rotation:0}));
$("canvasWord").addEventListener("click",()=>{
 if($("canvasDialog").open)$("canvasDialog").close();
 if(isSegments()){showInspector("timing",true);const i=+$("canvasWord").dataset.index;$("segmentCards").children[i]?.scrollIntoView({block:"nearest"});return;}
 if($("canvasWord").dataset.index!==""){video.pause();selectWord(+$("canvasWord").dataset.index,true);showInspector("timing",true);}
 else{const row=words().find(row=>row.start>video.currentTime)||words().find(row=>intervalValid(row));if(row){video.pause();video.currentTime=row.start+Math.min(.1,(row.end-row.start)/2);}}
});
$("expandCanvas").addEventListener("click",()=>{
 video.pause();$("canvasDialog").append(previewPanel);$("closeCanvas").hidden=false;$("expandCanvas").hidden=true;
 $("canvasDialog").showModal();requestAnimationFrame(fitViewer);
});
$("closeCanvas").addEventListener("click",()=>$("canvasDialog").close());
$("canvasDialog").addEventListener("close",()=>{
 video.pause();previewAnchor.after(previewPanel);$("closeCanvas").hidden=true;$("expandCanvas").hidden=false;requestAnimationFrame(fitViewer);
});
$("outline").addEventListener("change",e=>changeSettings({outline:e.target.checked?3:0}));
$("toggleDemos").addEventListener("click",()=>{state.demosPaused=!state.demosPaused;demoButtonLabel();});
$("previewMotion").addEventListener("click",()=>{
 if(busy()||!state.project||!lineCache.length)return;
 const line=lineCache.find(rows=>video.currentTime<rows.at(-1).end)||lineCache[0];
 video.currentTime=Math.max(0,line[0].start-.08);state.stopAt=line.at(-1).end;
 video.play().catch(()=>notice("לא הצלחנו לנגן. נסו ללחוץ על ניגון הסרטון.",true));
});
demoButtonLabel();
$("resetStyle").addEventListener("click",()=>changeSettings({...defaults,caption_mode:state.settings.caption_mode}));
$("designOptions").addEventListener("toggle",()=>{
 document.body.classList.toggle("designing",$("designOptions").open);
 requestAnimationFrame(fitViewer);
 if($("designOptions").open&&lineCache.length&&!$("caption").textContent){video.pause();video.currentTime=lineCache[0][0].start+Math.min(.15,(lineCache[0][0].end-lineCache[0][0].start)/2);}
 renderSubtitle();
});
const captionBox=$("captionBox");
captionBox.addEventListener("pointerdown",e=>{
 if(busy()||!captionLayout||e.button!==0)return;
 e.preventDefault();video.pause();
 const rect=$("videoShell").getBoundingClientRect();
 invalidateRedo();styleUndo.push(CaptionShortcuts.mark({...state.settings}));styleUndo=styleUndo.slice(-30);styleRedo=[];
 drag={id:e.pointerId,x:e.clientX,y:e.clientY,rect,settings:{...state.settings},
  centerX:captionLayout.x/state.project.width,centerY:captionLayout.y/state.project.height,
  mode:e.target.closest("#rotateCaption")?"rotate":e.target.closest(".resize-caption")?"resize":"move"};
 drag.radius=Math.max(16,Math.hypot(e.clientX-rect.left-drag.centerX*rect.width,e.clientY-rect.top-drag.centerY*rect.height));
 drag.angle=Math.atan2(e.clientY-rect.top-drag.centerY*rect.height,e.clientX-rect.left-drag.centerX*rect.width);
 captionBox.setPointerCapture(e.pointerId);captionBox.classList.add("dragging");
});
captionBox.addEventListener("pointermove",e=>{
 if(!drag||e.pointerId!==drag.id)return;
 if(drag.mode==="resize"){const radius=Math.hypot(e.clientX-drag.rect.left-drag.centerX*drag.rect.width,e.clientY-drag.rect.top-drag.centerY*drag.rect.height);changeSettings({font_size:Math.round(Math.max(40,Math.min(200,drag.settings.font_size*radius/drag.radius)))},false);}
 else if(drag.mode==="rotate"){
  const angle=Math.atan2(e.clientY-drag.rect.top-drag.centerY*drag.rect.height,e.clientX-drag.rect.left-drag.centerX*drag.rect.width);
  const delta=Math.atan2(Math.sin(angle-drag.angle),Math.cos(angle-drag.angle))*180/Math.PI;
  let rotation=Math.max(-45,Math.min(45,Math.round(drag.settings.rotation+delta)));
  if(e.shiftKey)rotation=Math.round(rotation/15)*15;else if(Math.abs(rotation)<3)rotation=0;
  changeSettings({rotation},false);
 }else{
  const x=CaptionEditing.snap(drag.centerX+(e.clientX-drag.x)/drag.rect.width,6,drag.rect.width,!e.altKey),y=CaptionEditing.snap(drag.centerY+(e.clientY-drag.y)/drag.rect.height,6,drag.rect.height,!e.altKey);
  $("snapX").hidden=x!==.5;$("snapY").hidden=y!==.5;
  changeSettings({position_x:Math.max(.04,Math.min(.96,x)),position:Math.max(.04,Math.min(.96,y))},false);
 }
});
function finishDrag(e){if(!drag||e.pointerId!==drag.id)return;drag=null;$("snapX").hidden=true;$("snapY").hidden=true;captionBox.classList.remove("dragging");if(captionBox.hasPointerCapture(e.pointerId))captionBox.releasePointerCapture(e.pointerId);guard(persistSettings);}
captionBox.addEventListener("pointerup",finishDrag);captionBox.addEventListener("pointercancel",finishDrag);
captionBox.addEventListener("keydown",e=>{
 if(e.target.id==="rotateCaption"&&["ArrowLeft","ArrowRight"].includes(e.key)){e.preventDefault();e.stopPropagation();changeSettings({rotation:Math.max(-45,Math.min(45,state.settings.rotation+(e.key==="ArrowRight"?1:-1)*(e.shiftKey?15:1)))});return;}
 const step=e.shiftKey?.05:.01,s=state.settings;
 const changes={ArrowLeft:{position_x:Math.max(.04,s.position_x-step)},ArrowRight:{position_x:Math.min(.96,s.position_x+step)},ArrowUp:{position:Math.max(.04,s.position-step)},ArrowDown:{position:Math.min(.96,s.position+step)},"+":{font_size:Math.min(200,s.font_size+5)},"-":{font_size:Math.max(40,s.font_size-5)}};
 if(changes[e.key]){e.preventDefault();e.stopPropagation();changeSettings(changes[e.key]);}
});
$("playButton").addEventListener("click",togglePlay);video.addEventListener("click",togglePlay);
function playbackLabel(){
 const playing=!video.paused;$("playButton").classList.toggle("playing",playing);
 $("playButton").querySelector("use").setAttribute("href",playing?"#i-pause":"#i-play");
 const label=playing?"עצירת הסרטון":"ניגון הסרטון";$("playLabel").textContent=label;$("playButton").setAttribute("aria-label",label);
}
video.addEventListener("play",playbackLabel);
video.addEventListener("pause",()=>{playbackLabel();updatePlayback();});
video.addEventListener("timeupdate",updatePlayback);
video.addEventListener("loadedmetadata",()=>{fitViewer();revealFirstCaption();updatePlayback();});
video.addEventListener("error",()=>{if(video.getAttribute("src"))notice("הדפדפן לא הצליח לנגן את הסרטון. נסו לפתוח ב־Chrome או לבחור סרטון MP4 אחר.",true);});
$("seek").addEventListener("input",e=>{video.currentTime=+e.target.value;state.stopAt=null;});
$("speed").addEventListener("change",e=>video.playbackRate=+e.target.value);
$("muteButton").addEventListener("click",()=>{video.muted=!video.muted;$("muteButton").style.opacity=video.muted?.5:1;$("muteButton").setAttribute("aria-label",video.muted?"הפעלת הקול":"השתקה");});
$("timingDetails").addEventListener("toggle",()=>{document.body.classList.toggle("repairing",$("timingDetails").open);requestAnimationFrame(fitViewer);});
$("auditionWord").addEventListener("click",()=>{
 const start=wordTime("start"),end=wordTime("end");
 if(!$("wordStart").value||!$("wordEnd").value||!intervalValid({start,end}))return notice("קודם קובעים למילה התחלה וסיום בעזרת הנגן.");
 video.currentTime=Math.max(0,start-.12);state.stopAt=Math.min(state.project.duration,end+.12);video.play().catch(()=>{});
});
$("useSuggested").addEventListener("click",()=>{
 const suggested=suggestedTiming(words()[state.selected]);if(!suggested)return;
 CaptionTimecode.write($("wordStart"),suggested.start);CaptionTimecode.write($("wordEnd"),suggested.end);$("listenedWord").checked=false;
 video.currentTime=Math.max(0,suggested.start-.12);video.pause();timingFeedback();fieldError("timingError","");
 notice("ההצעה הועתקה לשדות. האזינו ובדקו לפני השמירה.");
});
function captureTime(edge){if(state.selected!==null){video.pause();CaptionTimecode.write($(edge==="start"?"wordStart":"wordEnd"),video.currentTime);$("listenedWord").checked=false;fieldError("timingError","");timingFeedback();}}
$("setStart").addEventListener("click",()=>captureTime("start"));$("setEnd").addEventListener("click",()=>captureTime("end"));
["wordStart","wordEnd"].forEach(id=>{
 const input=$(id),edge=id==="wordStart"?"start":"end";
 input.addEventListener("input",()=>{$("listenedWord").checked=false;fieldError("timingError","");timingFeedback();});
 input.addEventListener("blur",()=>{const time=wordTime(edge);if(Number.isFinite(time))CaptionTimecode.write(input,time);});
 input.addEventListener("keydown",e=>{if(e.key==="Enter"){e.preventDefault();input.blur();}});
});
all("[data-copy-time]").forEach(button=>button.addEventListener("click",()=>copyTime(wordTime(button.dataset.copyTime==="wordStart"?"start":"end"),$(button.dataset.copyTime))));
$("currentTime").addEventListener("click",()=>copyTime(video.currentTime));
$("nextUnaligned").addEventListener("click",()=>{const remaining=[...attentionIndexes()];if(remaining.length)selectWord(remaining.find(i=>i>state.selected)??remaining[0],true);});
document.addEventListener("keydown",e=>{
 if(e.code!=="Space"||!state.project||$("playButton").disabled||video.readyState<1)return;
 const target=e.target,control=target.closest("input,textarea,select")||target;
 if(!CaptionTimecode.canTogglePlayback({tag:control.tagName,type:control.type,timecode:control.hasAttribute("data-timecode"),
  editable:target.isContentEditable||!!target.closest('[role="textbox"]:not(input)'),
  modal:!!document.querySelector("dialog[open]:not(#canvasDialog)"),composing:e.isComposing,modified:e.ctrlKey||e.altKey||e.metaKey}))return;
 e.preventDefault();e.stopPropagation();if(!e.repeat){state.stopAt=null;togglePlay();}
},true);
function invalidateRedo(){styleRedo=[];wordTimeline.future=[];passageTimeline.future=[];}
function historyTarget(redo){
 const candidates=[{name:"style",stack:redo?styleRedo:styleUndo},
  isSegments()?{name:"passage",stack:redo?passageTimeline.future:passageTimeline.history}
   :{name:"word",stack:redo?wordTimeline.future:wordTimeline.history}];
 return candidates.filter(c=>c.stack.length).sort((a,b)=>(b.stack.at(-1).editOrder||0)-(a.stack.at(-1).editOrder||0))[0]?.name;
}
function updateHistoryButtons(){
 const blocked=!state.project||busy()||timingDirty()||!!passageTimeline.active||!!drag;
 $("undoEdit").disabled=blocked||!historyTarget(false);$("redoEdit").disabled=blocked||!historyTarget(true);
}
function restoreEdit(redo){
 if(!state.project||busy()||timingDirty()||passageTimeline.active||drag)return;
 const target=historyTarget(redo);
 if(target==="word")return wordTimeline.restore(redo);
 if(target==="passage")return passageTimeline.undoEdit(redo);
 if(target==="style")return restoreStyle(redo?styleRedo:styleUndo,redo?styleUndo:styleRedo);
}
function seekBy(delta){
 if(!state.project||video.readyState<1||state.uploading)return;
 video.pause();state.stopAt=null;
 video.currentTime=Math.max(0,Math.min(state.project.duration,video.currentTime+delta));updatePlayback();
}
$("undoEdit").addEventListener("click",()=>restoreEdit(false));
$("redoEdit").addEventListener("click",()=>restoreEdit(true));
all("[data-seek]").forEach(b=>b.addEventListener("click",()=>seekBy(+b.dataset.seek)));
$("shortcutHelp").addEventListener("click",()=>$("shortcutsDialog").showModal());
document.addEventListener("keydown",e=>{
 if(e.defaultPrevented||!state.project)return;
 const editable=e.target.isContentEditable||!!e.target.closest('input,textarea,select,[role="textbox"]');
 const action=CaptionShortcuts.action({code:e.code,key:e.key,ctrlKey:e.ctrlKey,metaKey:e.metaKey,shiftKey:e.shiftKey,altKey:e.altKey,
  editable,composing:e.isComposing,modal:!!document.querySelector("dialog[open]:not(#canvasDialog)")});
 if(!action)return;
 // Widget arrow handlers keep their existing local behaviour; other buttons
 // must not swallow playback shortcuts just because they still have focus.
 e.preventDefault();
 if(action.kind==="undo"||action.kind==="redo"){if(!e.repeat)restoreEdit(action.kind==="redo");return;}
 if(wordGesture||passageTimeline.active||drag)return;
 if(action.kind==="seek")seekBy(action.delta);
 if(action.kind==="first")seekBy(-state.project.duration);
 if(action.kind==="last")seekBy(state.project.duration);
 if(action.kind==="play"&&!e.repeat&&video.readyState>=1){state.stopAt=null;togglePlay();}
 if(action.kind==="capture"&&!isSegments()&&state.selected!==null)captureTime(action.edge);
 if(action.kind==="word"){
  const rows=isSegments()?cueDraft:CaptionWordEditing.ordered(words()),at=video.currentTime;
  const row=action.direction>0?rows.find(r=>r.start>at+.01):[...rows].reverse().find(r=>r.start<at-.01);
  if(row){seekBy(row.start-at);if(!isSegments())wordTimeline.select(row.index,true);}
 }
});
["uploadZone","dropStage"].forEach(id=>{
 const target=$(id);
 target.addEventListener("dragover",e=>{e.preventDefault();target.classList.add("drag-over");});
 target.addEventListener("dragleave",()=>target.classList.remove("drag-over"));
 target.addEventListener("drop",e=>{e.preventDefault();target.classList.remove("drag-over");upload(e.dataTransfer.files[0]);});
});
new ResizeObserver(()=>{fitViewer();drawTimeline();}).observe($("dropStage"));
document.fonts.ready.then(()=>{state.captionKey="";renderSubtitle();});
window.addEventListener("beforeunload",e=>{saveDraft();if(busy()){e.preventDefault();e.returnValue="";}});
showSettings();updateControls();requestAnimationFrame(tick);
guard(async()=>{
 const id=local.get("katuv-project");
 const [motions,project]=await Promise.all([api("/api/motion"),id?api("/api/projects/"+id).catch(error=>{
  local.remove("katuv-project");notice(error.message,true);return null;
 }):Promise.resolve(null)]);
 state.motions=motions;
 if(project)loadProject(project);
});
// Hardware/model discovery must not hold the saved video and captions hostage.
refreshModels().catch(()=>{$("modelDescription").textContent="המודל ייבחר אוטומטית בזמן יצירת הכתוביות.";});

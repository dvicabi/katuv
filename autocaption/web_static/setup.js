/* Local desktop preparation. All processing and files belong to this computer. */
"use strict";
(() => {
 let preparing=false,checking=false;
 const error=e=>notice(e.message,true);
 function drawPreparation(data){
  preparing=data.state==="running";
  $("systemPrepare").disabled=preparing;
  $("systemProgress").hidden=!preparing;
  $("systemStatus").textContent=(data.message||"")+(data.error?" "+data.error:"");
 }
 async function systemCheck(){
  $("systemStatus").textContent="בודקים את המחשב…";
  const data=await api("/api/system");
  $("noncommercialModels").checked=Boolean(data.preferences?.include_noncommercial);
  $("systemChecks").replaceChildren(...data.checks.map(check=>{
   const row=document.createElement("li"),title=document.createElement("strong"),detail=document.createElement("small");
   row.dataset.ok=check.ok;title.textContent=(check.ok?"✓ ":"○ ")+check.title;detail.textContent=check.detail;
   row.append(title,detail);return row;
  }));
  drawPreparation(data.preparation);
  if(data.preparation.state==="idle")$("systemStatus").textContent=data.ready?"המחשב מוכן לסרטון שלכם.":"אפשר להשלים את הרכיבים החסרים בלחיצה אחת.";
 }
 $("systemButton").addEventListener("click",()=>{$("systemDialog").showModal();systemCheck().catch(error);});
 $("noncommercialModels").addEventListener("change",async()=>{
  const input=$("noncommercialModels");input.disabled=true;
  try{await api("/api/system/preferences",{method:"PUT",body:JSON.stringify({include_noncommercial:input.checked})});}
  catch(e){input.checked=!input.checked;error(e);}
  finally{input.disabled=false;}
 });
 $("systemPrepare").addEventListener("click",async()=>{
  $("systemPrepare").disabled=true;
  try{drawPreparation(await api("/api/system/prepare",{method:"POST",body:JSON.stringify({include_noncommercial:$("noncommercialModels").checked})}));}
  catch(e){$("systemPrepare").disabled=false;error(e);}
 });
 setInterval(async()=>{
  if(document.hidden||checking||!preparing)return;
  checking=true;
  try{drawPreparation(await api("/api/system/preparation"));if(!preparing&&$("systemDialog").open)await systemCheck();}
  catch{/* Retry polling; user actions surface connection failures. */}
  finally{checking=false;}
 },3000);
 if(!local.get("katuv-setup-1.1")){
  local.set("katuv-setup-1.1","seen");$("systemDialog").showModal();systemCheck().catch(error);
 }
})();

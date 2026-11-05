"use strict";
let demoEnabled = false, demoState = null, demoRunning = false;
const phaseCopy = {
  identity:["Identity & ownership","Provision fictional users, map roles, remove a leaver and simulate SSO/MFA."],
  inventory:["Sites & hardware","Import three declared assets and reconcile their serials with the local Redfish lab."],
  telemetry:["Collectors & vault","Enroll primary/standby collectors, rotate mock certificates and evaluate seven days of fake coverage."],
  connectors:["Enterprise systems","Test seven required mock integrations plus procurement and OEM entitlement."],
  policy:["Policy & approvals","Replay 30 synthetic historical cases and record two independent demo-owner reviews."],
  ai:["Enterprise AI access","Register demo clients, exercise the read/propose gateway and reject forbidden scopes."],
  shadow:["Shadow → operations","Evaluate eight simulated weeks, open Redfish cases and prepare evidence-backed recommendations."]
};

function showDemoView(view){
  document.querySelector(`[data-view="${view}"]`).click();
}
async function freshDemoEntry(){
  await refresh();
  if(demoState?.steps.some(s=>s.id==="inventory"&&s.complete)&&!demoState.offboarded){
    await api("/api/demo/refresh",{});
    await refresh();
  }
}
function renderHackathon(demo){
  demoState=demo;
  $("hackathon-panel").hidden=false;
  $("demo-prepare-form").hidden=demo.prepared;
  $("demo-journey").hidden=!demo.prepared;
  $("demo-evidence-panel").hidden=!demo.prepared;
  $("demo-offboarding-panel").hidden=!demo.prepared;
  $("demo-operations").hidden=!demo.prepared || !demo.steps.some(s=>s.id==="shadow"&&s.complete) || demo.offboarded;
  $("demo-phase-count").textContent=`${demo.completed || 0} / 7`;
  $("demo-progress").value=demo.completed || 0;
  $("demo-stage-label").textContent=demo.offboarded?"Demo access revoked":demo.demo_ready?"Onboarding complete · demo fleet ready":demo.prepared?"Setup in progress":"Ready to begin";
  $("page-title").textContent="Hardware Overwatch";
  $("page-subtitle").textContent="Customer onboarding, hardware evidence and operations in one workspace.";
  $("onboarding-edit").hidden=true;
  document.querySelector("#view-cases .toolbar").hidden=true;
  ["sample-bids","bids","compare-bids"].forEach(id=>$(id).hidden=true);
  document.querySelector('label[for="bids"]').hidden=true;
  document.querySelector("#view-tenders .tender-intro p:not(.eyebrow)").textContent="Service bids from the demo supplier directory, checked against mock OEM entitlement. Compare them from Onboarding → Compare service bids. All results are drafts.";
  $("demo-offboard").disabled=demo.offboarded || demoRunning;
  if(!demo.prepared)return;
  $("demo-company-title").textContent=demo.organization + " / onboarding";
  $("demo-next-label").textContent=demo.offboarded?"Evidence retained. Mock access has been revoked.":demo.next_step?"Run each phase separately, or complete the full journey.":"All seven phases are complete. Explore the operations workflow below.";
  $("demo-run-all").hidden=!demo.next_step || demo.offboarded;
  $("demo-run-all").disabled=demoRunning;
  $("demo-restart").disabled=demoRunning;
  $("demo-phase-list").innerHTML=demo.steps.map((s,i)=>`<article class="card demo-phase ${s.complete?"complete":""}"><span class="demo-phase-number">${s.complete?"✓":i+1}</span><div><h3>${esc(phaseCopy[s.id][0])}</h3><p>${esc(phaseCopy[s.id][1])}</p><small>${s.complete?"Evidence saved · fictional owner reviews recorded":s.id===demo.next_step?"Ready to run":"Waiting for the preceding phase"}</small></div><button data-demo-step="${s.id}" ${s.id!==demo.next_step||demoRunning||demo.offboarded?"disabled":""}>${s.complete?"Complete":"Run phase"}</button></article>`).join("");
  const connectors=demo.artifacts.find(a=>a.id==="connectors")?.data.certifications || [];
  $("demo-integrations").innerHTML=connectors.map(c=>`<div class="demo-integration"><span class="dot"></span><strong>${esc(c.provider)}</strong><small>${c.records} records · ${esc(c.mode)} · ${c.category==="telemetry"?"local HTTP":"mock adapter"}</small></div>`).join("");
  $("demo-artifacts").innerHTML=demo.artifacts.filter(a=>!a.id.startsWith("connector:")).map(a=>`<details class="record"><summary><strong>${esc(a.id)} · evidence</strong></summary><small>${esc(date(a.at))} · ${esc(a.source)}</small><pre>${esc(JSON.stringify(a.data,null,2))}</pre></details>`).join("") || '<div class="record muted">Evidence appears here as each phase runs.</div>';
  $("demo-cases").innerHTML=state.cases.map(c=>`<article class="card detail-body"><span class="badge ${c.state==="CLOSED"?"closed":""}">${esc(c.state.replaceAll("_"," "))}</span><h3>${esc(c.asset_id)}</h3><p>${c.state==="CLOSED"?"Recovery verified using three fresh Redfish observations.":"Thermal degradation observed. Cooling and airflow inspection recommended."}</p><small>${c.receipt_ids.length} evidence receipts · ${c.healthy_samples} recovery samples</small><div class="demo-actions"><button data-demo-case="${esc(c.id)}">Inspect case</button>${c.state!=="CLOSED"?`<button data-demo-draft="${esc(c.id)}">Draft maintenance request</button>`:""}</div></article>`).join("");
  const shadow=demo.artifacts.find(a=>a.id==="shadow")?.data;
  if(shadow)$("demo-shadow-metrics").innerHTML=`<div class="demo-metrics"><div><strong>${shadow.simulated_weeks}</strong><span>simulated weeks</span></div><div><strong>${shadow.recommendations}</strong><span>recommendations</span></div><div><strong>${shadow.precision_percent}%</strong><span>synthetic precision</span></div><div><strong>${shadow.unsafe_recommendations}</strong><span>unsafe fixture outcomes</span></div></div><p>Computed from the downloadable synthetic outcome rows. Autonomy remains T0; this is a demo metric, not a measured production result.</p>`;
  $("demo-work-orders").innerHTML=demo.work_orders.map(w=>`<div class="record"><strong>${esc(w.id)} · ${esc(w.asset)}</strong><p>${esc(w.summary)}</p><span class="badge">${esc(w.status.replaceAll("_"," "))}</span><small>Linked case: ${esc(w.case_id)} · local notice draft attached</small></div>`).join("") || '<div class="record muted">Use “Draft maintenance request” on a case to create a linked mock CMMS work order and notice.</div>';
  if(demo.proposals.length)$("demo-work-orders").innerHTML+=demo.proposals.map(p=>`<div class="record"><strong>Assistant proposal · ${esc(p.id)}</strong><p>${esc(p.summary)}</p><span class="badge">DRAFT · HUMAN REVIEW REQUIRED</span></div>`).join("");
}

async function demoAction(action,body={}){
  if(demoRunning)return null;
  demoRunning=true;
  try{
    if(demoState)renderHackathon(demoState);
    $("demo-run-status").textContent="Running " + action + "…";
    const result=await api("/api/demo/"+action,body);
    await refresh();
    $("demo-run-status").textContent="Completed. Evidence and audit records saved.";
    return result;
  }catch(error){message(error.message,true);$("demo-run-status").textContent=error.message;return null;}
  finally{demoRunning=false;if(demoState)renderHackathon(demoState);}
}
$("enter-demo").addEventListener("click",async()=>{
  try{
    const response=await fetch("/api/demo/login",{method:"POST",headers:{"Content-Type":"application/json"},body:"{}"});
    const result=await response.json();if(!response.ok)throw Error(result.error);
    token=result.token;sessionStorage.setItem("watcher-token",token);
    await freshDemoEntry();showDemoView("onboarding");
  }catch(error){message(error.message,true);}
});
$("demo-prepare-form").addEventListener("submit",e=>{e.preventDefault();demoAction("prepare",{name:$("demo-company").value,region:$("demo-region").value});});
$("demo-phase-list").addEventListener("click",e=>{const b=e.target.closest("[data-demo-step]");if(b)demoAction("step",{step:b.dataset.demoStep});});
$("demo-run-all").addEventListener("click",async()=>{
  while(demoState?.next_step&&!demoRunning){
    const step=demoState.next_step;
    const result=await demoAction("step",{step});
    if(!result)break;
  }
});
$("demo-incident").addEventListener("click",()=>demoAction("incident"));
$("demo-tender").addEventListener("click",async()=>{if(await demoAction("tender"))showDemoView("tenders");});
$("demo-recover").addEventListener("click",()=>demoAction("recover"));
$("demo-restart").addEventListener("click",()=>demoAction("restart"));
$("demo-cases").addEventListener("click",async e=>{
  const inspect=e.target.closest("[data-demo-case]"),draft=e.target.closest("[data-demo-draft]");
  if(inspect){selected=inspect.dataset.demoCase;renderCases();showDemoView("cases");}
  if(draft)await demoAction("draft",{case_id:draft.dataset.demoDraft});
});
document.querySelectorAll("[data-demo-question]").forEach(b=>b.addEventListener("click",async()=>{
  const result=await demoAction("assistant",{question:b.dataset.demoQuestion});
  if(result)$("demo-assistant-answer").textContent=result.answer + (result.receipt_ids.length?" Evidence: "+result.receipt_ids.join(", "):"");
}));
function downloadDemo(bundle){
  const url=URL.createObjectURL(new Blob([JSON.stringify(bundle,null,2)],{type:"application/json"}));
  const a=document.createElement("a");a.href=url;a.download="overwatch-demo-evidence.json";a.click();setTimeout(()=>URL.revokeObjectURL(url),1000);
}
$("demo-export").addEventListener("click",async()=>{try{downloadDemo(await api("/api/demo/export"));}catch(e){message(e.message,true);}});
$("demo-offboard").addEventListener("click",async()=>{const result=await demoAction("offboard");if(result){downloadDemo(result.export);$("demo-offboard-result").textContent="Evidence exported. Mock collector and gateway access revoked. Local history retained.";}});
window.addEventListener("DOMContentLoaded",async()=>{
  try{
    const response=await fetch("/api/demo/config");demoEnabled=(await response.json()).enabled===true;
    $("hackathon-login").hidden=!demoEnabled;
    if(demoEnabled){
      $("manual-onboarding").open=false;
      $("login-form").hidden=true;
      if(token){await freshDemoEntry();showDemoView("onboarding");}
    }
  }catch{ /* Standard operations desk remains usable without demo mode. */ }
});
// Keep the real local probe fresh during a presentation, without altering configuration.
setInterval(async()=>{
  if(demoEnabled&&token&&demoState?.steps.some(s=>s.id==="inventory"&&s.complete)&&!demoState.offboarded&&!demoRunning&&!busy){
    try{await api("/api/demo/refresh",{});await refresh();}catch(e){message(e.message,true);}
  }
},60000);

"use strict";
let token = sessionStorage.getItem("watcher-token") || "";
let state = null, selected = null, activeView = "cases", busy = false;
const $ = id => document.getElementById(id);
const esc = value => String(value ?? "").replace(/[&<>"']/g, char => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[char]));
const short = value => String(value || "").slice(-8);
const date = seconds => new Date(seconds * 1000).toLocaleString();
function message(text, error=false) { $("message").textContent=text; $("message").className=error?"error":""; $("message").hidden=false; clearTimeout(message.timer); message.timer=setTimeout(()=>$("message").hidden=true,8000); }
async function api(path, body) {
  const response = await fetch(path,{method:body===undefined?"GET":"POST",headers:{Authorization:`Bearer ${token}`,"Content-Type":"application/json"},...(body===undefined?{}:{body:JSON.stringify(body)})});
  const result = await response.json();
  if(!response.ok) throw new Error(result.error || `Request failed (${response.status})`);
  return result;
}
async function act(path, body, success) {
  if(busy) return;
  busy=true;
  try { const result=await api(path,body); message(success || "Saved."); await refresh(); return result; }
  catch(error) { message(error.message,true); }
  finally { busy=false; }
}
async function refresh() {
  if(!token) return;
  try {
    state=await api("/api/state"); $("login").hidden=true; $("workspace").hidden=false;
    $("identity").textContent=`${state.identity.actor} · ${state.identity.role}`;
    $("count-open").textContent=state.cases.filter(c=>c.state!=="CLOSED").length;
    $("count-approval").textContent=state.cases.filter(c=>c.state==="AWAITING_APPROVAL").length;
    $("count-evidence").textContent=state.receipts.length;
    $("ledger-status").textContent=state.ledger_valid?"Verified":"Mismatch";
    $("policy-status").textContent=state.policy.kill_switch||state.policy.freeze?"Execution halted by policy":"Policy active · USD simulation budget " + state.policy.budget;
    $("admin-controls").hidden=state.identity.role!=="policy_admin";
    if(!selected && state.cases.length) selected=state.cases[state.cases.length-1].id;
    renderCases(); renderEvidence(); renderTenders();
    renderOnboarding(await api("/api/onboarding"));
    if (typeof demoEnabled !== "undefined" && demoEnabled) renderHackathon(await api("/api/demo/status"));
  } catch(error) { message(error.message,true); }
}
function renderCases() {
  $("case-list").innerHTML=state.cases.length?[...state.cases].reverse().map(c=>`<button class="case-item ${c.id===selected?"active":""}" data-case="${esc(c.id)}"><strong>${esc(c.title)}</strong><div class="case-meta"><span>#${short(c.id)} · ${c.receipt_ids.length} receipts</span><span class="badge ${c.state==="CLOSED"?"closed":""}">${esc(c.state.replaceAll("_"," "))}</span></div></button>`).join(""):'<div class="empty"><span>✓</span><h2>No open observations</h2><p>Set a degraded scenario and collect telemetry to open your first case.</p></div>';
  const c=state.cases.find(c=>c.id===selected);
  if(!c) return;
  const plan=state.plans.find(p=>p.id===c.plan_id), receipts=state.receipts.filter(r=>c.receipt_ids.includes(r.id));
  const latest=receipts[receipts.length-1], approvals=state.approvals.filter(a=>a.plan_id===c.plan_id);
  const permit=[...state.permits].reverse().find(p=>p.plan_id===c.plan_id && p.status==="issued");
  const job=[...state.operations].reverse().find(o=>o.case_id===c.id);
  const steps=["DETECTED","PLANNED","AWAITING_APPROVAL","POST_CHECK","CLOSED"];
  let buttons="";
  const btn=(label,action,id,primary=false)=>`<button ${primary?'class="primary"':''} data-action="${action}" data-id="${esc(id)}">${label}</button>`;
  if(["DETECTED","HELD","ESCALATED"].includes(c.state)) buttons+=btn("Prepare repair plan","plan",c.id,true);
  if(plan?.status==="DRAFT") buttons+=btn("Verify evidence","verify",plan.id,true);
  if(c.state==="AWAITING_APPROVAL" && !state.onboarding_active) {
    if(!approvals.some(a=>a.actor===state.identity.actor)) buttons+=btn("Approve this plan","approve",plan.id,true);
    if(!permit) buttons+=btn("Request permit","permit",plan.id);
    if(permit) buttons+=btn("Execute permitted action","execute",permit.id,true);
  }
  if(state.onboarding_active) buttons+='<p class="muted">Onboarding / shadow mode: recommendations only (T0). Action permits and execution are disabled.</p>';
  if(c.state==="RECONCILING"||c.state==="EXECUTING") buttons+=btn("Reconcile outcome","reconcile",job?.id,true);
  if(c.state==="POST_CHECK") buttons+='<p class="muted">Action accepted. Set the recovered scenario and collect at least three fresh readings across the configured soak period (60 seconds by default).</p>';
  $("case-detail").innerHTML=`<div class="section-title"><h2>${esc(c.asset_id)}</h2><span class="badge">${esc(c.state.replaceAll("_"," "))}</span></div><div class="detail-body"><div class="steps">${steps.map((s,i)=>`<span class="${i<=steps.indexOf(c.state)?"done":""}">${s.replaceAll("_"," ")}</span>`).join("")}</div><p>Thermal threshold observation. Component failure is not established; inspect cooling, airflow and workload before maintenance.</p><div class="detail-label">LATEST EVIDENCE</div><div class="detail-value">${latest?`${esc(latest.body.temperature)} °C · ${esc(date(latest.body.observed_at))}`:"No readings"}</div><small>${latest?`${esc(latest.body.timestamp_basis)} timestamp · receipt ${short(latest.id)}`:""}</small><div class="detail-label">PLAN</div><div class="detail-value">${plan?`${esc(plan.action)} · ${esc(plan.currency)} ${esc(plan.amount)}`:"Not prepared"}</div>${plan?`<small>Approved scope hash</small><div class="detail-value"><code>${esc(plan.hash)}</code></div>`:""}<div class="detail-label">APPROVALS</div><div class="detail-value">${approvals.map(a=>`${esc(a.actor)} (${esc(a.role)})`).join(", ")||"No approvals recorded"}</div><div class="detail-label">RECOVERY</div><div class="detail-value">${c.healthy_samples} qualifying samples${c.state==="CLOSED"?" · outcome verified":""}</div><div class="actions">${buttons}</div></div>`;
}
function renderEvidence() {
  $("evidence-list").innerHTML=[...state.receipts].reverse().slice(0,100).map(r=>`<article class="record"><strong>${esc(r.body.asset_id)} · ${esc(r.body.temperature)} °C <span class="pill">${esc(r.environment)}</span></strong><p>Measured ${esc(date(r.body.observed_at))} · retrieved ${esc(date(r.retrieved_at))} · maximum age ${r.max_age}s</p><code>${esc(r.sha256)}</code><details><summary>Inspect receipt</summary><pre>${esc(JSON.stringify(r,null,2))}</pre></details></article>`).join("")||'<div class="empty">No evidence collected yet.</div>';
}
function renderTenders() {
  $("tender-results").innerHTML=[...state.tenders].reverse().map(t=>{
    if(t.kind==="discovery") return `<section class="card"><div class="section-title"><h2>Provider discovery</h2><span class="pill">${t.degraded?"DEGRADED":"INDICATIVE"}</span></div>${(t.leads||[]).map(l=>`<div class="tender-row"><strong>${esc(l.name)}</strong><span>${esc(l.origin)}</span><small>${esc(l.authority)}${l.domain?" · "+esc(l.domain):""}</small></div>`).join("")||'<p class="muted">No leads.</p>'}${(t.screened_out||[]).map(s=>`<div class="record"><strong>${esc(s.name)} · screened out</strong><p>${esc(s.reason)}</p></div>`).join("")}<div class="record"><small>${esc(t.source_note)} — ${esc(t.methodology)}</small></div></section>`;
    if(t.kind==="shortlist") return `<section class="card"><div class="section-title"><h2>Qualification shortlist</h2><span class="pill">DRAFT ONLY</span></div>${(t.ranked||[]).map((c,i)=>`<div class="tender-row"><strong>${i+1}. ${esc(c.provider)}</strong><span>${esc(c.score)} / 100</span><small>${c.unverified_factors.length?"unverified (scored 0): "+c.unverified_factors.map(esc).join(", "):"fully authoritative"}</small></div>`).join("")}${(t.excluded||[]).map(e=>`<div class="record"><strong>${esc(e.provider)} · excluded</strong><p>${esc(e.reason)}</p></div>`).join("")}<div class="record"><small>${esc(t.methodology)}</small></div></section>`;
    return `<section class="card"><div class="section-title"><h2>Bid comparison · ${esc(t.currency)}</h2><span class="pill">DRAFT ONLY</span></div>${(t.ranked||[]).map((b,i)=>`<div class="tender-row"><strong>${i+1}. ${esc(b.supplier)}</strong><span>${esc(b.total)} ${esc(t.currency)}</span><small>Evidence: ${esc(b.evidence_id)}</small></div>`).join("")}${(t.excluded||[]).map(b=>`<div class="record"><strong>${esc(b.supplier)} · excluded</strong><p>${esc(b.reason)}</p></div>`).join("")}<div class="record"><small>${esc(t.methodology)}</small></div></section>`;
  }).join("");
}
$("login-form").addEventListener("submit",e=>{e.preventDefault();token=$("token").value.trim();sessionStorage.setItem("watcher-token",token);$("token").value="";refresh();});
$("logout").addEventListener("click",()=>{token="";sessionStorage.removeItem("watcher-token");$("workspace").hidden=true;$("login").hidden=false;});
$("refresh").addEventListener("click",refresh);
document.querySelectorAll("[data-view]").forEach(button=>button.addEventListener("click",()=>{activeView=button.dataset.view;document.querySelectorAll(".view").forEach(el=>el.hidden=el.id!==`view-${activeView}`);document.querySelectorAll(".nav").forEach(el=>el.classList.toggle("active",el===button));$("breadcrumb").textContent=button.textContent.trim();}));
$("case-list").addEventListener("click",e=>{const button=e.target.closest("[data-case]");if(button){selected=button.dataset.case;renderCases();}});
$("case-detail").addEventListener("click",e=>{const button=e.target.closest("[data-action]");if(!button)return;const action=button.dataset.action;const key=({plan:"case_id",execute:"permit_id",reconcile:"operation_id"})[action]||"plan_id";act(`/api/${action}`,{[key]:button.dataset.id},`${button.textContent}: complete.`);});
$("set-scenario").addEventListener("click",()=>act("/api/scenario",{scenario:$("scenario").value},"Scenario set. Collect telemetry to observe it."));
$("collect").addEventListener("click",()=>act("/api/collect",{},"Telemetry collected and receipted."));
$("kill-on").addEventListener("click",()=>act("/api/controls",{kill_switch:true},"Execution halted."));
$("kill-off").addEventListener("click",()=>act("/api/controls",{kill_switch:false},"Policy resumed. Existing permits still require validation."));
$("sample-bids").addEventListener("click",()=>{$("bids").value=JSON.stringify(["Regional Service Co","OEM Service","Unverified Lead"].map((supplier,i)=>({supplier,unit_price:["100.10","130.00","50.00"][i],quantity:3,freight:"10.00",tax:"5.00",currency:"USD",qualified:i<2,screened:true,evidence_id:`simulation-bid-${i+1}`,availability_confirmed:i<2,expires_at:Math.floor(Date.now()/1000)+86400})),null,2);});
$("compare-bids").addEventListener("click",()=>{try{act("/api/tender/compare",{currency:"USD",bids:JSON.parse($("bids").value)},"Bid comparison saved as draft.");}catch(error){message(error.message,true);}});
$("assist-run").addEventListener("click",async()=>{const prompt=$("assist-prompt").value.trim();if(!prompt)return;if(busy)return;busy=true;$("assist-output").innerHTML='<p class="muted">Drafting…</p>';try{const r=await api("/api/assist",{prompt});const tools=r.tools_used.length?`<small>Tools used: ${r.tools_used.map(esc).join(", ")}</small>`:"";$("assist-output").innerHTML=`<div class="record"><span class="pill">DRAFT ONLY · ${esc(r.model)}</span><pre>${esc(r.reply)}</pre>${tools}${r.evidence.map(e=>`<details><summary>Evidence · ${esc(e.tool)}</summary><pre>${esc(JSON.stringify(e.output,null,2))}</pre></details>`).join("")}</div>`;message("Draft ready for your review.");}catch(error){$("assist-output").innerHTML="";message(error.message,true);}finally{busy=false;}});
$("discover-providers").addEventListener("click",()=>act("/api/tender/discover",{need:{query:"third-party data center server hardware maintenance and repair services"},approved_vendors:[{name:"Incumbent OEM Service"}],sanctioned:["Top Ten USA, LLC."],blocked:[]},"Provider discovery saved. Crustdata leads are indicative only."));
$("sample-candidates").addEventListener("click",()=>{const f=(s)=>["capability","coverage_sla","past_performance","price","stability","security"].reduce((o,k)=>(o[k]={verified:true,strength:s},o),{});$("candidates").value=JSON.stringify([{provider:"Evernex",qualification_verified:true,factors:{...f(0.9),past_performance:{verified:false},stability:{verified:false,corroborating:{crustdata_headcount:"501-1000"}}}},{provider:"Incumbent OEM Service",qualification_verified:true,factors:f(0.8)},{provider:"Unverified Lead",qualification_verified:false,factors:f(1.0)}],null,2);});
$("score-candidates").addEventListener("click",()=>{try{act("/api/tender/score",{candidates:JSON.parse($("candidates").value)},"Qualification shortlist saved as draft.");}catch(error){message(error.message,true);}});
$("load-audit").addEventListener("click",async()=>{try{const events=await api("/api/events");$("audit-list").innerHTML=events.reverse().slice(0,200).map(event=>`<div class="record"><strong>#${event.seq} · ${esc(event.kind)}</strong><p>${esc(date(event.at))}</p><code>${esc(event.hash)}</code><details><summary>Event payload</summary><pre>${esc(JSON.stringify(event.payload,null,2))}</pre></details></div>`).join("");}catch(error){message(error.message,true);}});
window.addEventListener("DOMContentLoaded",refresh);

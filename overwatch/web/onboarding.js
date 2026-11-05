"use strict";
let onboardingState = null;
const sectionNames = {tenant:"Tenant settings",identity:"Identity and authority import",inventory:"Site and device manifest",telemetry:"Collector coverage fixture",connectors:"Connector test fixtures",policy:"Policy configuration",ai:"Optional AI client pilot"};

function renderOnboarding(setup) {
  onboardingState = setup;
  const admin = state.identity.role === "policy_admin";
  $("onboarding-intro").textContent = setup.started
    ? `Local onboarding · revision ${setup.revision} · ${setup.stage === "SHADOW" ? "Shadow " + setup.shadow_status.toLowerCase() : "Setup in progress"}. All actions remain T0.`
    : "Start a customer onboarding rehearsal. Starting locks this workspace to T0 recommendations and revokes unused action permits.";
  $("start-onboarding").hidden = setup.started || !admin;
  $("onboarding-admin-hint").hidden = setup.started || admin;
  $("onboarding-content").hidden = !setup.started;
  $("onboarding-edit").hidden = !admin || !setup.started;
  $("production-blockers").innerHTML = setup.production_blockers.map(b=>`<li>${esc(b)}</li>`).join("");
  if (!setup.started) return;
  $("policy-status").textContent = "Onboarding / shadow mode · T0 · execution disabled";
  $("onboarding-ready").textContent = `${setup.gates.filter(g=>g.status === "passed").length} signed gates · ${setup.gates.filter(g=>g.status === "not_required").length} optional gates skipped`;
  $("onboarding-gates").innerHTML = setup.gates.map(g=> {
    const canSign = g.missing_signers.includes(state.identity.role) && !g.blockers.length && g.status !== "not_required";
    return `<article class="card onboarding-gate"><div class="section-title"><h2>${esc(g.title)}</h2><span class="badge ${g.status === "passed" ? "closed" : ""}">${esc(g.status.replaceAll("_"," "))}</span></div><div class="detail-body"><p>${esc(g.criterion)}</p><small>Evidence: simulation rehearsal${g.id === "telemetry" ? " + current local Redfish probe" : ""}</small>${g.blockers.length ? `<ul>${g.blockers.map(b=>`<li>${esc(b)}</li>`).join("")}</ul>` : ""}<p class="muted">${g.missing_signers.length ? "Awaiting: " + esc(g.missing_signers.join(", ")) : g.status === "not_required" ? "External AI access is disabled for this rehearsal." : "Owner sign-offs recorded for this revision."}</p>${g.signatures.map(s=>`<small>Signed: ${esc(s.actor)} · ${esc(date(s.at))}</small>`).join("")}${canSign ? `<button class="primary" data-sign-gate="${g.id}">Sign simulated ${esc(g.id)} gate</button>` : ""}</div></article>`;
  }).join("");
  $("probe-onboarding").hidden = !["operator","security_lead"].includes(state.identity.role);
  $("probe-result").textContent = setup.probe ? `${setup.probe.read_only ? "Read-only lab probe passed" : "Lab probe blocked"} · ${date(setup.probe.at)}${setup.probe.error ? " · " + setup.probe.error : ""}. A probe is valid for five minutes; it does not establish seven days of coverage.` : "No device probe yet. Sign identity, declare the lab, then run the read-only probe as operator.";
  $("inventory-rows").innerHTML = setup.inventory_rows.length ? setup.inventory_rows.map(r=>`<tr><td>${esc(r.serial)}</td><td>${esc(r.site || "Unclaimed")}</td><td>${esc(r.rack || "—")}</td><td>${esc(r.status.replaceAll("_"," "))}</td></tr>`).join("") : '<tr><td colspan="4">No reconciled inventory. Declare the fixture, sign identity and run the probe.</td></tr>';
  $("setup-requests").innerHTML = setup.requests.map(r=>`<article class="record"><strong>${esc(r.id)} · ${esc(r.title)} <span class="badge">${esc(r.status.replaceAll("_"," "))}</span></strong><p>Owner teams: ${esc(r.owner_roles.join(", "))} · Due ${esc(date(r.due_at))}</p><p>${esc(r.criterion)}</p><small>${r.accepted_by.length ? "Accepted by " + esc(r.accepted_by.join(", ")) : "Not yet accepted"}</small>${r.owner_roles.includes(state.identity.role) && !r.accepted_by.includes(state.identity.actor) ? `<button data-accept-request="${r.id}">Accept request</button>` : ""}</article>`).join("");
  $("shadow-status").textContent = `${setup.shadow_status} · ${setup.shadow_days} elapsed days · target 8–12 weeks. Promotion is disabled; recommendations cannot issue action permits or execute.`;
  $("start-shadow").hidden = state.identity.role !== "risk_lead" || setup.stage === "SHADOW";
  $("start-shadow").disabled = !setup.ready_for_simulated_shadow;
  $("onboarding-revision").textContent = `Revision ${setup.revision}. Saving changes invalidates every gate signature and restarts setup. Imported checks remain simulation assertions.`;
  if (!$("onboarding-config").value) fillOnboardingEditor();
}

function fillOnboardingEditor() {
  if (!onboardingState) return;
  const section = $("onboarding-section").value;
  $("onboarding-config").value = JSON.stringify(onboardingState.config?.[section] || onboardingState.templates[section], null, 2);
  $("onboarding-config").dataset.revision = onboardingState.revision;
}
$("onboarding-section").innerHTML = Object.entries(sectionNames).map(([id,label])=>`<option value="${id}">${label}</option>`).join("");
$("onboarding-section").addEventListener("change",fillOnboardingEditor);
$("reload-onboarding-config").addEventListener("click",fillOnboardingEditor);
$("start-onboarding").addEventListener("click",()=>act("/api/onboarding/start",{},"Onboarding started. This workspace now enforces T0."));
$("load-onboarding-fixture").addEventListener("click",async()=>{const result=await act("/api/onboarding/sample",{},"Simulation configuration loaded. Each owner must review and sign; no enterprise integration was connected.");if(result)fillOnboardingEditor();});
$("save-onboarding-config").addEventListener("click",()=>{
  try {
    if (Number($("onboarding-config").dataset.revision) !== onboardingState.revision) throw new Error("Configuration changed. Reload this section before editing.");
    act("/api/onboarding/configure",{section:$("onboarding-section").value,value:JSON.parse($("onboarding-config").value),revision:onboardingState.revision},"Configuration saved. Previous gate signatures were invalidated.").then(result=>{if(result)fillOnboardingEditor();});
  } catch(error) { message(error.message,true); }
});
$("probe-onboarding").addEventListener("click",()=>act("/api/onboarding/probe",{},"Probe finished. Review the result and inventory below."));
$("onboarding-gates").addEventListener("click",e=>{const button=e.target.closest("[data-sign-gate]");if(button)act("/api/onboarding/sign",{gate:button.dataset.signGate,revision:onboardingState.revision},"Simulation gate signature recorded.");});
$("setup-requests").addEventListener("click",e=>{const button=e.target.closest("[data-accept-request]");if(button)act("/api/onboarding/accept",{request_id:button.dataset.acceptRequest},"Request accepted by your team.");});
$("start-shadow").addEventListener("click",()=>act("/api/onboarding/shadow",{},"Simulated shadow mode started at T0. No promotion or write access is enabled."));

/* Isolated browser rehearsal: never signs gates in the user's running workspace. */
const {chromium} = require(process.env.PLAYWRIGHT_MODULE || "playwright");
const fs = require("node:fs");
const os = require("node:os");
const path = require("node:path");
const {spawn} = require("node:child_process");
const assert = require("node:assert/strict");
const root = path.resolve(__dirname,"..");
const run = fs.mkdtempSync(path.join(os.tmpdir(),"overwatch-onboarding-ui-"));
const port = Number(process.env.WATCHER_UI_PORT || 18082);
const child = spawn(process.env.WATCHER_PYTHON || "python3",["-m","overwatch","demo","--data",path.join(run,"data"),"--port",String(port),"--lab-port",String(port+1)],{cwd:root,stdio:["ignore","pipe","pipe"]});
let output="", browser;
child.stdout.on("data",v=>output+=v);
child.stderr.on("data",v=>output+=v);
async function waitForServer(){
  for(let i=0;i<100;i++){
    if(child.exitCode!==null)throw Error(output);
    try{if((await fetch(`http://127.0.0.1:${port}/healthz`)).ok)return;}catch{}
    await new Promise(r=>setTimeout(r,100));
  }
  throw Error("Server did not start: "+output);
}
(async()=>{
  try{
    await waitForServer();
    const tokens=JSON.parse(fs.readFileSync(path.join(run,"data/demo-credentials.json"),"utf8"));
    browser=await chromium.launch({headless:true,...(process.env.PLAYWRIGHT_EXECUTABLE?{executablePath:process.env.PLAYWRIGHT_EXECUTABLE}:{})});
    const page=await browser.newPage({viewport:{width:1440,height:1000}});
    const errors=[];
    page.on("pageerror",e=>errors.push(e.message));
    await page.goto(`http://127.0.0.1:${port}`);
    async function login(role){
      if(await page.locator("#workspace").isVisible())await page.locator("#logout").click();
      await page.locator("#token").fill(tokens[role]);
      await page.getByRole("button",{name:"Connect",exact:true}).click();
      await page.waitForFunction(role=>document.getElementById("identity").textContent.endsWith(" · "+role),role);
      await page.locator('[data-view="onboarding"]').click();
      await page.waitForFunction(()=>typeof onboardingState!=="undefined"&&onboardingState!==null);
    }
    async function action(selector,route){
      const response=page.waitForResponse(r=>r.url().endsWith(route)&&r.request().method()==="POST");
      await page.locator(selector).click();
      const result=await response;
      assert.equal(result.status(),200,await result.text());
      await page.waitForFunction(()=>!busy);
    }
    await login("policy_admin");
    await action("#start-onboarding","/api/onboarding/start");
    await page.locator("#onboarding-edit summary").click();
    await action("#load-onboarding-fixture","/api/onboarding/sample");
    assert.equal(await page.locator("#onboarding-gates article").count(),6);
    assert.equal(await page.locator("#setup-requests article").count(),7);
    await login("iam_lead");
    await action('[data-accept-request="REQ-0001"]',"/api/onboarding/accept");
    await action('[data-sign-gate="identity"]',"/api/onboarding/sign");
    await action('[data-sign-gate="connectors"]',"/api/onboarding/sign");
    await login("operator");
    await action("#probe-onboarding","/api/onboarding/probe");
    assert.match(await page.locator("#inventory-rows").innerText(),/matched/);
    await action('[data-sign-gate="telemetry"]',"/api/onboarding/sign");
    await action('[data-sign-gate="connectors"]',"/api/onboarding/sign");
    await login("infrastructure_lead");
    await action('[data-sign-gate="inventory"]',"/api/onboarding/sign");
    await action('[data-sign-gate="connectors"]',"/api/onboarding/sign");
    await login("security_lead");
    for(const gate of ["telemetry","connectors","policy"])await action(`[data-sign-gate="${gate}"]`,"/api/onboarding/sign");
    for(const role of ["maintenance_lead","change_lead","collaboration_lead"]){
      await login(role);
      await action('[data-sign-gate="connectors"]',"/api/onboarding/sign");
    }
    await login("risk_lead");
    await action('[data-sign-gate="policy"]',"/api/onboarding/sign");
    await action("#start-shadow","/api/onboarding/shadow");
    assert.match(await page.locator("#shadow-status").innerText(),/ACTIVE/);
    assert.equal(await page.evaluate(()=>onboardingState.production_ready),false);
    assert.equal(await page.evaluate(()=>onboardingState.promotion_available),false);
    await page.screenshot({path:path.join(run,"onboarding-desktop.png"),fullPage:true});
    await page.setViewportSize({width:390,height:844});
    assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),true,"mobile overflow");
    await page.screenshot({path:path.join(run,"onboarding-mobile.png"),fullPage:true});
    await page.setViewportSize({width:1440,height:1000});
    // Saving even unchanged configuration must invalidate approvals and stop shadow.
    await login("policy_admin");
    if(!await page.locator("#onboarding-section").isVisible())await page.locator("#onboarding-edit summary").click();
    await page.locator("#reload-onboarding-config").click();
    await action("#save-onboarding-config","/api/onboarding/configure");
    assert.equal(await page.evaluate(()=>onboardingState.stage),"SETUP");
    assert.deepEqual(await page.evaluate(()=>onboardingState.signatures),{});
    assert.deepEqual(errors,[]);
    const report={passed:true,checks:["administrator setup","seven requests","fixture labeling","owner login and sign-offs","read-only Redfish probe","matched inventory","all required gates","shadow T0","production blocked","mobile layout","configuration invalidates approvals","no browser exceptions"]};
    fs.writeFileSync(path.join(run,"report.json"),JSON.stringify(report,null,2));
    console.log(JSON.stringify({...report,artifacts:run}));
  }finally{
    if(browser)await browser.close();
    if(child.exitCode===null)child.kill("SIGTERM");
  }
})().catch(e=>{console.error(e);process.exitCode=1;});

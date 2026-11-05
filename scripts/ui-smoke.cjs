/* Optional browser verification. Requires a locally installed Playwright package/browser.
   Set PLAYWRIGHT_MODULE to an absolute package path if it is not in node_modules. */
const {chromium} = require(process.env.PLAYWRIGHT_MODULE || "playwright");
const fs = require("node:fs");
const path = require("node:path");
const {spawn, spawnSync} = require("node:child_process");
const assert = require("node:assert/strict");
const root = path.resolve(__dirname, "..");
const run = path.join(root, "artifacts", "ui-" + Date.now());
fs.mkdirSync(run, {recursive:true});
const data = path.join(run, "data");
const port = Number(process.env.WATCHER_UI_PORT || 18080);
const processHandle = spawn(process.env.WATCHER_PYTHON || "python", ["-m","overwatch","demo","--data",data,"--port",String(port),"--lab-port",String(port+1),"--soak-seconds","0"], {cwd:root, windowsHide:true, stdio:["ignore","pipe","pipe"]});
let startup = "", browser;
processHandle.stdout.on("data", value => startup += value);
processHandle.stderr.on("data", value => startup += value);
async function waitForServer() {
  for(let i=0;i<100;i++) {
    if(processHandle.exitCode !== null) throw new Error("Demo failed: " + startup);
    try {const response=await fetch(`http://127.0.0.1:${port}/healthz`);if(response.ok)return;} catch {}
    await new Promise(resolve=>setTimeout(resolve,100));
  }
  throw new Error("Demo did not start: " + startup);
}
(async()=>{
  try {
    await waitForServer();
    const roles = JSON.parse(fs.readFileSync(path.join(data,"demo-credentials.json"),"utf8"));
    browser = await chromium.launch({headless:true, ...(process.env.PLAYWRIGHT_CHANNEL ? {channel:process.env.PLAYWRIGHT_CHANNEL} : {})});
    const page = await browser.newPage({viewport:{width:1440,height:1000}});
    const errors=[];
    page.on("pageerror", error=>errors.push(error.message));
    await page.goto(`http://127.0.0.1:${port}`);
    await page.locator("#token").fill(roles.operator);
    await page.getByRole("button",{name:"Connect",exact:true}).click();
    await page.locator("#workspace").waitFor({state:"visible"});
    await page.locator("#scenario").selectOption("degraded");
    await page.locator("#set-scenario").click();
    await page.waitForFunction(()=>document.getElementById("message").textContent.includes("Scenario set"));
    await page.locator("#collect").click();
    await page.getByRole("button",{name:"Prepare repair plan",exact:true}).waitFor();
    await page.getByRole("button",{name:"Prepare repair plan",exact:true}).click();
    for(const label of ["Verify evidence","Approve this plan","Request permit","Execute permitted action"]) {
      const button=page.getByRole("button",{name:label,exact:true});
      await button.waitFor();
      await button.click();
    }
    await page.waitForFunction(()=>document.getElementById("case-detail").textContent.includes("Action accepted"));
    await page.screenshot({path:path.join(run,"operations-desktop.png"),fullPage:true});
    await page.locator("#scenario").selectOption("recovered");
    await page.locator("#set-scenario").click();
    await page.waitForFunction(()=>document.getElementById("message").textContent.includes("Scenario set"));
    for(let i=0;i<3;i++) {
      const response = page.waitForResponse(r=>r.url().endsWith("/api/collect") && r.request().method()==="POST");
      await page.locator("#collect").click(); await response;
      await page.waitForTimeout(100);
    }
    await page.waitForFunction(()=>document.getElementById("case-detail").textContent.includes("outcome verified"));
    await page.locator('[data-view="evidence"]').click();
    assert.ok(await page.locator("#evidence-list .record").count() >= 4);
    await page.locator('[data-view="audit"]').click();
    await page.locator("#load-audit").click();
    await page.locator("#audit-list .record").first().waitFor();
    await page.locator("#logout").click();
    await page.locator("#token").fill(roles.procurement);
    await page.getByRole("button",{name:"Connect",exact:true}).click();
    await page.locator("#workspace").waitFor({state:"visible"});
    await page.locator('[data-view="tenders"]').click();
    await page.locator("#sample-bids").click();
    await page.locator("#compare-bids").click();
    await page.locator("#tender-results .tender-row").first().waitFor();
    assert.equal(await page.locator("#tender-results .tender-row").count(),2);
    await page.screenshot({path:path.join(run,"tender-desktop.png"),fullPage:true});
    await page.setViewportSize({width:390,height:844});
    await page.screenshot({path:path.join(run,"mobile.png"),fullPage:true});
    assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth <= innerWidth),true,"mobile horizontal overflow");
    assert.deepEqual(errors,[],"browser JavaScript errors");
    const report={passed:true,checks:["operator login","degradation case","verification","approval","permit","execution","post-check remains open","recovery closure","evidence","audit","procurement login","bid exclusions","mobile width","no browser exceptions"]};
    fs.writeFileSync(path.join(run,"report.json"),JSON.stringify(report,null,2));
    console.log(JSON.stringify({passed:true,artifacts:run}));
  } finally {
    if(browser) await browser.close();
    if(processHandle.exitCode === null) {
      if(process.platform==="win32") spawnSync("taskkill",["/PID",String(processHandle.pid),"/T","/F"],{windowsHide:true,stdio:"ignore"});
      else processHandle.kill("SIGTERM");
    }
  }
})().catch(error=>{console.error(error);process.exitCode=1;});

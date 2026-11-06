// Optional real-browser integration check. Run npm run build first.
const { chromium } = require(process.env.PLAYWRIGHT_MODULE || 'playwright');
const { spawn, spawnSync } = require('node:child_process');
const fs = require('node:fs/promises');
const path = require('node:path');
const assert = require('node:assert/strict');

const root = path.resolve(__dirname, '..');
const output = path.join(root, 'artifacts', `frontend-${Date.now()}`);
const apiPort = Number(process.env.SMOKE_API_PORT || 18080);
const webPort = Number(process.env.SMOKE_WEB_PORT || 15173);
const api = `http://127.0.0.1:${apiPort}`;
const processes = [];
function start(command, args, env) {
  const child = spawn(command, args, { cwd: root, windowsHide: true, env, stdio: ['ignore', 'pipe', 'pipe'] });
  processes.push(child);
  child.on('error', error => { child.startError = error; });
  // Drain pipes without printing potentially sensitive application output.
  child.stdout.resume(); child.stderr.resume();
  return child;
}
async function ready(url, child) {
  for (let i = 0; i < 100; i++) {
    if (child.startError) throw child.startError;
    if (child.exitCode !== null) throw new Error(`Service exited: ${child.exitCode}`);
    try { if ((await fetch(url, { signal: AbortSignal.timeout(1000) })).ok) return; } catch {}
    await new Promise(resolve => setTimeout(resolve, 100));
  }
  throw new Error(`Service did not become ready: ${url}`);
}
(async () => {
  await fs.mkdir(output, { recursive: true });
  // Never make paid provider calls or use existing application credentials.
  const env = { ...process.env, GEMINI_API_KEY: '', WATCHER_CRUSTDATA_TOKEN: '' };
  let browser;
  try {
    const backend = start(process.env.PYTHON || 'python', ['-m', 'overwatch', 'demo', '--port', String(apiPort),
      '--lab-port', String(apiPort + 1), '--data', path.join(output, 'data')], env);
    const frontend = start(process.execPath, ['frontend/scripts/serve.mjs'], { ...env, HOST: '127.0.0.1', PORT: String(webPort) });
    await Promise.all([ready(`${api}/healthz`, backend), ready(`http://127.0.0.1:${webPort}`, frontend)]);
    const credentials = JSON.parse(await fs.readFile(path.join(output, 'data/demo-credentials.json'), 'utf8'));
    browser = await chromium.launch({ headless: true, ...(process.env.PLAYWRIGHT_CHANNEL ? { channel: process.env.PLAYWRIGHT_CHANNEL } : {}),
      ...(process.env.PLAYWRIGHT_EXECUTABLE ? { executablePath: process.env.PLAYWRIGHT_EXECUTABLE } : {}) });
    const page = await browser.newPage({ viewport: { width: 1440, height: 1100 } });
    const errors = [];
    page.on('pageerror', error => errors.push(error.message));
    await page.goto(`http://127.0.0.1:${webPort}`, { waitUntil: 'domcontentloaded' });
    await page.locator('#login-form button[type="submit"]').click();
    assert.equal(await page.locator('#dashboard').isVisible(), false, 'legacy debug state must stay hidden');
    await page.locator('[data-tab="onboarding"]').click();
    await page.locator('#ob-api-url').fill(api);
    await page.locator('#ob-token').fill(credentials.operator);
    await page.locator('#ob-connect').click();
    await page.waitForFunction(() => document.querySelector('#ob-conn-status').textContent.includes('Connected · operator'));
    await page.locator('[data-tab="tender"]').click();
    await page.locator('#assist-prompt').fill('Draft a maintenance summary using available evidence.');
    const call = page.waitForResponse(response => response.url() === `${api}/api/assist` && response.request().method() === 'POST');
    await page.locator('#assist-submit').click();
    const reply = await (await call).json();
    assert.equal(reply.model, 'stub');
    assert.equal(reply.status, 'DRAFT_ONLY');
    await page.waitForFunction(() => document.querySelector('#assist-status').textContent.includes('Offline stub'));
    assert.match(await page.locator('#assist-reply').textContent(), /GEMINI_API_KEY/);
    await page.screenshot({ path: path.join(output, 'desktop.png'), fullPage: true });
    await page.setViewportSize({ width: 390, height: 844 });
    await page.screenshot({ path: path.join(output, 'mobile.png'), fullPage: true });
    assert.deepEqual(errors, []);
    await fs.writeFile(path.join(output, 'report.json'), JSON.stringify({ passed: true, model: reply.model, checks: [
      'compiled dashboard loads without JavaScript errors', 'authenticated backend connection', 'offline Gemini draft flow',
      'draft-only response', 'desktop and narrow-viewport screenshots'
    ] }, null, 2));
    console.log(`Frontend smoke passed. Artifacts: ${output}`);
  } finally {
    if (browser) await browser.close();
    for (const child of processes) {
      if (!child.pid || child.exitCode !== null) continue;
      if (process.platform === 'win32') spawnSync('taskkill', ['/PID', String(child.pid), '/T', '/F'], { windowsHide: true, stdio: 'ignore' });
      else child.kill('SIGTERM');
    }
  }
})().catch(error => { console.error(error.message); process.exitCode = 1; });

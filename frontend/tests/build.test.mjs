import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs/promises';
import path from 'node:path';
import vm from 'node:vm';
import { fileURLToPath } from 'node:url';
import { build } from '../scripts/build.mjs';

const root = fileURLToPath(new URL('../', import.meta.url));

test('production build uses local CSS, preserves app code and excludes secrets', async () => {
  const previous = process.env.GEMINI_API_KEY;
  process.env.GEMINI_API_KEY = 'test-secret-never-to-be-bundled';
  try { await build(); }
  finally { if (previous === undefined) delete process.env.GEMINI_API_KEY; else process.env.GEMINI_API_KEY = previous; }
  const html = await fs.readFile(path.join(root, 'dist/index.html'), 'utf8');
  assert.ok(html.includes('WatcherMCP'));
  assert.ok(html.includes('./assets/tailwind.css'));
  assert.ok(!html.includes('cdn.tailwindcss.com'));
  assert.ok(!html.includes('test-secret-never-to-be-bundled'));
  const css = await fs.readFile(path.join(root, 'dist/assets/tailwind.css'), 'utf8');
  assert.ok(css.length > 1000, 'compiled utility stylesheet must be present');
  for (const match of html.matchAll(/<script\b([^>]*)>([\s\S]*?)<\/script>/g)) {
    if (!match[1].includes('src=')) assert.doesNotThrow(() => new vm.Script(match[2]), 'inline dashboard JavaScript must parse');
  }
  const files = await fs.readdir(path.join(root, 'dist'));
  assert.deepEqual(files.sort(), ['assets', 'index.html']);
});

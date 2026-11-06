import fs from 'node:fs/promises';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { createRequire } from 'node:module';
import { spawnSync } from 'node:child_process';

const require = createRequire(import.meta.url);
const root = fileURLToPath(new URL('../', import.meta.url));

export async function build() {
  const source = await fs.readFile(path.join(root, 'index.html'), 'utf8');
  const cdn = '<script src="https://cdn.tailwindcss.com/3.4.17"></script>';
  if (!source.includes(cdn)) throw new Error('Expected Tailwind source marker; update the build when changing the entry point');
  const output = path.join(root, 'dist');
  await fs.mkdir(path.join(output, 'assets'), { recursive: true });
  const command = require.resolve('tailwindcss/lib/cli.js');
  const built = spawnSync(process.execPath, [command, '-i', 'src/tailwind.css', '-o', 'dist/assets/tailwind.css',
    '--config', 'tailwind.config.cjs', '--minify'], { cwd: root, stdio: 'inherit', windowsHide: true });
  if (built.status !== 0) throw new Error('Tailwind build failed');
  // Explicitly copy only public source. Environment variables and backend data never enter dist.
  await fs.writeFile(path.join(output, 'index.html'), source.replace(cdn, '<link rel="stylesheet" href="./assets/tailwind.css">'));
  return output;
}

if (process.argv[1] && path.resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  console.log('Built ' + await build());
}

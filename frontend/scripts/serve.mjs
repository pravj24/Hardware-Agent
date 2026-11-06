import http from 'node:http';
import fs from 'node:fs/promises';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const root = fileURLToPath(new URL('../dist/', import.meta.url));
const host = process.env.HOST || '127.0.0.1';
const port = Number(process.env.PORT || 5173);
await fs.access(path.join(root, 'index.html')).catch(() => { throw new Error('Frontend build missing; run npm run build first'); });
const server = http.createServer(async (request, response) => {
  if (!['GET', 'HEAD'].includes(request.method)) { response.writeHead(405); response.end(); return; }
  try {
    const route = new URL(request.url, 'http://localhost').pathname;
    // This static app has exactly two public build assets; never serve repository files.
    const file = route === '/assets/tailwind.css' ? 'assets/tailwind.css' : route.includes('.') && route !== '/index.html' ? null : 'index.html';
    if (!file) { response.writeHead(404); response.end('Not found'); return; }
    const payload = await fs.readFile(path.join(root, file));
    response.writeHead(200, { 'Content-Type': file.endsWith('.css') ? 'text/css; charset=utf-8' : 'text/html; charset=utf-8',
      'Content-Length': payload.length, 'X-Content-Type-Options': 'nosniff', 'Cache-Control': 'no-cache' });
    response.end(request.method === 'HEAD' ? undefined : payload);
  } catch { response.writeHead(500); response.end('Static asset unavailable'); }
});
server.listen(port, host, () => console.log(`WatcherMCP frontend: http://${host}:${port}`));

# WatcherMCP frontend

This is the current NOC dashboard. Charts, fleet, lifecycle, tender scores, MCP inspector and ledger visuals are deterministic browser simulations. Onboarding and the Gemini draft panel call the Python backend. The visual role selector and kill switch control only the browser simulation.

## Develop and build

From the repository root with Node 20+:

```sh
npm ci
npm run dev
npm test
npm run build
npm run preview
```

Development and preview serve `http://127.0.0.1:5173`; override `HOST`/`PORT` if needed. Development rebuilds once on startup; rerun after editing source. Production output is `frontend/dist/`. The build compiles pinned Tailwind 3.4.17 locally and replaces the development CDN script. It never embeds environment variables. Source remains `index.html` with inline application JavaScript.

## Connect to the API

In another terminal run `python -m pip install -e .` then `python -m overwatch demo`. Open Onboarding, enter `http://127.0.0.1:8080` and a role token from `data/demo-credentials.json`, and connect. Operator/procurement tokens can use Tenders → Gemini drafting assistant. Configure `GEMINI_API_KEY` only in the backend environment; missing keys produce an explicit offline stub. Browser onboarding settings store the API URL and role token in localStorage; use demo credentials here, not production credentials.

## Deploy

Preferred project root: repository root. Netlify/Vercel settings run `npm run build` and publish `frontend/dist/`; the root workspace lockfile enables reproducible `npm ci`. If using `frontend/` as the project root, allow access to the repository workspace root/lockfile during dependency installation; its configuration publishes `dist/`.

Any static host can publish the built `frontend/dist/` directory. Do not deploy the unbuilt source directory. Static hosting alone runs the visual simulation; live features need a separately deployed backend. An HTTPS dashboard needs an HTTPS API, reachable from the user's browser. The included API uses loopback development serving and permissive CORS; production hosting needs authentication, TLS and a restricted origin policy.

# WatcherMCP

Evidence-first hardware operations, built around a tested permission boundary:

**Observe → gather receipts → plan → verify → approve → permit → execute → verify recovery.**

This repository is a runnable reference implementation of the Hardware Overwatch architecture. It includes a local Redfish lab, an authenticated operations desk, durable workflow state, and read-only MCP servers. It deliberately does **not** enable real BMC writes, send RFQs, issue purchase orders, or claim validated failure prediction.

## Run the dashboard and backend

Requires Python 3.11+ and Node 20+. From the cloned `WatcherMCP` directory:

```powershell
python -m pip install -e .
python -m overwatch demo
```

In a second terminal in the same directory:

```powershell
npm ci
npm run dev
```

Open **http://127.0.0.1:5173**. The Python process serves the API at **http://127.0.0.1:8080**, not the dashboard. Open **Onboarding**, enter that API URL and a role token from generated `data/demo-credentials.json`, then connect. Tokens and the signing key are excluded from Git. Stop each process with Ctrl+C. Override backend/lab ports with `--port` and `--lab-port`; use `--data another-local-directory` for a separate simulation database.

The NOC charts, lifecycle, tender visualization and audit animation are browser simulations. Onboarding and the Gemini drafting panel connect to the backend. The dashboard's visual role selector and kill switch affect the simulation; backend roles come from authenticated tokens. The retired `overwatch/web/` desk is not served.

### Exercise the backend workflow

The API implements observation → plan → verification → approval → permit → execution → recovery. The HTTP workflow tests exercise real local requests, lost responses and reconciliation. A successful simulated action leaves a case in `POST_CHECK` until three fresh observations span the configured recovery soak (60 seconds by default). `--soak-seconds 0` shortens only the lab timing. Production writes remain disabled.

Use `python -m unittest tests.test_api tests.test_workflow -v` to verify this workflow. `python -m overwatch demo --hackathon` enables the fictional three-node rehearsal API; see [the current API presentation guide](docs/hackathon-demo.md).

Provider discovery uses a keyless fixture by default. Set `WATCHER_CRUSTDATA_TOKEN` in the backend environment for live Crustdata searches. Discovery leads remain indicative; qualification and deterministic bid comparison are separate checks.

## Onboarding rehearsal

The desk now includes **Onboarding** with seven setup requests, owner sign-offs,
site/device reconciliation, a local Redfish privilege probe, readiness gates and T0
shadow mode. Start with the `policy_admin` token; the generated credentials also
include the gate owner roles. Load the explicitly labeled simulation configuration
to walk through the gates. Starting onboarding locks that database to recommendations
and disables action permits/execution; use separate data directories for the action lab.

This does not connect enterprise SSO, vaults, external systems or AI clients. Imported
coverage and integration checks are fixture assertions; production readiness remains
blocked. See [phase-by-phase status and walkthrough](docs/onboarding-status.md).

### AI assistant (dashboard, drafts only)

The **Tenders** view includes a server-side LLM assistant that drafts maintenance messages, approval-request summaries and provider/tender searches. It is the dashboard-native LLM path, distinct from the MCP servers (which expose tools to Claude Code as an external client). It runs a provider-neutral tool-use loop with three **read/draft-only** tools (`discover_providers`, `get_case_evidence`, `get_recent_tenders`); it cannot approve, send or execute — those stay on the authenticated policy/broker path — and it is instructed to use only figures returned by the tools (receipts, case facts), never invented numbers.

The backend uses **Gemini** through Google's native REST API:

```powershell
$env:GEMINI_API_KEY = "your-key"
$env:GEMINI_MODEL = "gemini-3.8-flash" # optional override
python -m overwatch demo
```

Connect an operator or procurement token in Onboarding, then open **Tenders → Gemini drafting assistant**. Without `GEMINI_API_KEY`, responses are explicitly labeled offline stubs. No provider SDK is required. The adapter preserves Gemini function-call thought signatures and IDs. Drafts are unverified model output; check claims against the returned evidence. See [Gemini setup and contracts](docs/gemini.md). You can copy `.env.example` to an ignored `.env` file; demo startup loads its backend variables without overriding existing environment values. Never place provider keys in the frontend.

## Test-driven implementation

```powershell
python -m unittest discover -s tests -v
python -m unittest discover -s deploy -p "test_*.py" -v
python -m compileall -q overwatch
npm test
npm run build
```

Node builds and tests the frontend. Three DMTF integration tests are skipped unless their environment variables are set. The main tests run real local HTTP servers and require permission to use loopback sockets.

The suite covers:

- Healthy and degraded observations, duplicate events and persistent case state.
- Receipt hashes, measurement freshness, unverified timestamps and simulation isolation.
- Wrong roles, missing approvals, two-person approval, plan changes, permit expiration and kill switches.
- Atomic single-use permits, budget reservation, ambiguous outcomes and restart reconciliation.
- False action success, pre-action observations, interrupted soak periods and verified recovery.
- Exact decimal bid totals, qualification gates, quote expiry, currencies and equivalent quantity/scope.
- API authentication, loopback host checks, CORS, role spoofing and end-to-end HTTP operation.
- MCP lifecycle, zone-specific read-only tools and architectural dependency boundaries.

See [verification protocol](docs/verification.md) for the release gates and [TDD record](docs/tdd-record.md) for the red/green development sequence.

An optional real-browser smoke runner is provided in `scripts/frontend-smoke.cjs`. Install Playwright and its browser in your development environment, then run `node scripts/frontend-smoke.cjs`. `PLAYWRIGHT_MODULE` can point to an existing package and `PLAYWRIGHT_CHANNEL=msedge` selects an installed Edge browser. The runner launches/stops its own local app, checks the built dashboard, live API connection and offline Gemini draft flow, and saves screenshots and a report under ignored `artifacts/`.

## Actual DMTF Mockup Server

The bundled stateful lab is our own small scenario server. The upstream DMTF server is a separate, pinned integration. Its source is **not copied into this repository**.

With Docker installed:

```powershell
docker compose up --build -d redfish-mock
python -m overwatch collect --url http://127.0.0.1:8000 --data data/dmtf
docker compose down
```

The Docker recipe pins upstream source and Python package versions; the Python base-image tag is not digest-pinned. Resolve a platform-specific base digest as part of production supply-chain hardening. Docker itself was not available on the development machine; the same upstream revision was checked natively.

The DMTF sample has no usable measurement timestamp in the collected thermal snapshot. Such receipts are labeled `retrieval_only` and cannot authorize an action. This is expected evidence behavior, not a reason to bypass freshness checks.

To run the upstream integration tests natively, clone the repository **outside** this project, check out the revision from `integrations/dmtf/manifest.json`, and install `integrations/dmtf/requirements.lock` into a dedicated virtual environment:

```powershell
$env:WATCHER_DMTF_REPO = 'C:\path\to\Redfish-Mockup-Server'
$env:WATCHER_DMTF_PYTHON = 'C:\path\to\dmtf-venv\Scripts\python.exe'
python -m unittest tests.test_dmtf -v
```

The tests start and stop the upstream server, check its revision and collector compatibility, and demonstrate that an accepted reset does not establish a changed power state. See [DMTF integration](docs/redfish-lab.md).

## MCP tools

For Codex or Claude Code, install the [WatcherMCP plugin](plugin/README.md) to add
all three read-only trust zones. The plugin guide includes local/GitHub marketplace
installation, Python setup and database configuration.

Start the demo or collect once to create the database, then configure your MCP client to run separate stdio processes:

```text
python -m overwatch mcp --zone fleet --db /absolute/path/to/data/overwatch.db
python -m overwatch mcp --zone records --db /absolute/path/to/data/overwatch.db
python -m overwatch mcp --zone market --db /absolute/path/to/data/overwatch.db
```

Set the client's working directory to this checkout, or install this project into its Python environment with `python -m pip install -e .`. An example configuration is in [examples/mcp-config.json](examples/mcp-config.json).

| Zone | Tools |
|---|---|
| Fleet | `fleet.get_observations` |
| Records | `records.get_cases`, `records.get_plans` |
| Market | `market.get_tenders` (simulation comparisons only) |

These are a minimal MCP 2025-06-18 stdio implementation. Tools return at most the latest 100 matching records. There are no model-accessible approval, signing, policy-update or execution tools. The broker is invoked by the authenticated workflow/API path. These MCP processes do not call or bill an LLM provider (the separate Gemini assistant can); a connected model may use the read tools for analysis, while plans in this release use deterministic templates.

## Repository layout

```text
overwatch/
  service.py       Cases, verifier, approvals, permit lifecycle, broker orchestration
  policy.py        Decimal amounts, role policy, canonical plan hashes, permit signing
  store.py         SQLite transactions and hash-chained events
  redfish.py       Link-following collector and simulation-only action adapter
  simulator.py     Stateful local Redfish scenarios and action outcomes
  api.py           Authenticated loopback backend API
  mcp.py           Read-only stdio tools split by trust zone
  tender.py        Deterministic bid eligibility and comparison
  assist.py        Bounded Gemini drafting loop; read/draft-only tools
  gemini.py        Native Gemini REST transport and function-call translation
  discovery.py     Crustdata provider discovery (live client + keyless fixture), compliance screen
  scoring.py       Weighted qualification rubric; unverified factors score zero
frontend/          NOC dashboard, Gemini draft panel and static build tooling
tests/             Behavioral, HTTP, MCP, architecture and upstream tests
integrations/dmtf/ Pinned upstream manifest, dependency lock and Docker recipe
docs/              Architecture, verification, limitations and decisions
examples/          MCP and tender inputs
.github/workflows/ Windows/Linux tests and upstream integration CI
```

## Frontend deployment

Run `npm ci` and `npm run build`; publish **frontend/dist/**. Root `package.json`, lockfile, Netlify and Vercel configuration support deploying from the repository root. `npm run preview` serves the built output locally. Tailwind is compiled during the build; production does not load its CDN script.

Deploying static files does not deploy the Python API or provision model credentials. A hosted dashboard needs a separately deployed HTTPS API with production authentication and a restricted CORS policy; the bundled loopback demo server is a development service. See [frontend deployment instructions](frontend/README.md) and [parallel work ownership](docs/parallel-work.md).

## Production boundary

This is a working foundation, **not a production-certified fleet controller**. The local HTTP server is for loopback development. Policy and broker classes currently share a process, and HMAC signing is local. SQLite's ledger is tamper-evident but not WORM or independently anchored.

Before enabling production actions: separate the policy/signing service and broker identities, introduce SSO and customer vaults, use a production database and immutable evidence store, implement audited OEM-specific adapters and durable job infrastructure, validate prediction models on real labels, and pass physical-hardware recovery tests. Live Crustdata, CMMS, ERP, provider qualification and RFQ delivery require credentials, reviewed schemas and partner integration work. See the [implementation status](docs/implementation-status.md).

Production read collection requires HTTPS with certificate verification:

```powershell
python -m overwatch collect --url https://your-authorized-bmc --environment production --data data/production-readonly
```

An optional `WATCHER_REDFISH_AUTHORIZATION` environment variable supplies an authorization header, and `--ca-file` accepts a customer CA. The collector does not log the header. Production and simulation databases are deliberately incompatible. This release rejects execution plans in production mode.

## Team Members and Emails: 

1. Krishna Khetre: krishnakd24@iitk.ac.in
2. Praval Jiddu: praval24@iitk.ac.in
3. Saigourav Sahoo: saigourav24@iitk.ac.in
4. Vaibhav Kalyan: vkalyan24@iitk.ac.in

# WatcherMCP

Evidence-first hardware operations, built around a tested permission boundary:

**Observe → gather receipts → plan → verify → approve → permit → execute → verify recovery.**

This repository is a runnable reference implementation of the Hardware Overwatch architecture. It includes a local Redfish lab, an authenticated operations desk, durable workflow state, and read-only MCP servers. It deliberately does **not** enable real BMC writes, send RFQs, issue purchase orders, or claim validated failure prediction.

## Run the operations desk

For the **integrated hackathon demo**, run `bash scripts/run-hackathon.sh`, open
**http://127.0.0.1:8080**, and click **Enter hackathon demo**. All seven onboarding
phases connect to a three-node Redfish lab, case recommendations, mock maintenance
requests, bid comparison, an assistant, recovery checks and evidence export. Enterprise
services, owners and historical data are fictional. No external credentials are needed.
See the [complete presentation guide](docs/hackathon-demo.md).

The commands below run the original manual operations lab without the guided demo.

Requires Python 3.11 or newer. The application and default tests use only the Python standard library; no API key or package installation is needed.

```powershell
cd WatcherMCP
python -m overwatch demo
```

Open **http://127.0.0.1:8080**. Copy the `operator` token from the generated `data/demo-credentials.json` into the login form. Tokens and the signing key are local files excluded from Git. Stop the services with Ctrl+C.

The default ports are 8080 (desk) and 8001 (lab). Override them with `--port` and `--lab-port`. Use `--data another-local-directory` for a clean simulation environment.

### Walk through a case

1. Choose **Thermal degradation**, click **Set scenario**, then **Collect telemetry**.
2. Select the case and **Prepare repair plan**.
3. **Verify evidence**, then **Approve this plan**.
4. **Request permit**, then **Execute permitted action**.
5. The case stays in `POST_CHECK`: an HTTP success response has not fixed the sensor.
6. Choose **Recovered**, set the scenario, and collect at least three fresh observations spanning 60 seconds. Only then does the case close.

For a quick UI demonstration, start with `python -m overwatch demo --soak-seconds 0`; three distinct post-action samples are still required. The shortened soak is a test setting, not a production recommendation.

Try **Lost action response** to exercise `RECONCILING`, or **Stale telemetry** to see verification refuse the plan. Switch to the `policy_admin` token to halt execution. Use the `procurement` token in **Tenders** to run the tender engine: **Discover providers** widens the shortlist beyond the incumbent OEM through Crustdata (indicative leads only; sanctioned/blocked suppliers are screened out with a reason), **Score candidates** ranks verified providers on the weighted rubric with unverified factors scoring zero, and **Compare bids** normalises the sample bids. The role comes from the token configuration, never from a request body.

Discovery runs against a keyless fixture of real captured third-party-maintainer records by default; set `WATCHER_CRUSTDATA_TOKEN` before `python -m overwatch demo` to query live Crustdata company search instead. If Crustdata is unavailable, discovery degrades to the approved vendor list only and says so.

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

The provider is chosen from the environment — Zhipu AI first, then Claude, else an offline stub:

```powershell
# Option A — Zhipu AI / GLM (no SDK needed; pure stdlib over the OpenAI-compatible API)
$env:ZHIPU_API_KEY = "..."           # defaults: model glm-4.6, endpoint api.z.ai
#   ZHIPU_MODEL and ZHIPU_BASE_URL override (set ZHIPU_BASE_URL to the
#   open.bigmodel.cn endpoint for the China platform)

# Option B — Claude (official anthropic SDK, lazily imported)
pip install "watchermcp[assist]"     # or: pip install anthropic
$env:ANTHROPIC_API_KEY = "sk-ant-..."  # model claude-opus-5-5

python -m overwatch demo
```

Without any key the assistant runs in an offline stub so the endpoint still responds; the live tool-calling loop needs a key. Zhipu requires no extra install (stdlib `urllib`); only the Claude path needs the `anthropic` SDK, imported lazily, so the app, demo and default tests keep running with no dependency installed.

## Test-driven implementation

```powershell
python -m unittest discover -s tests -v
python -m compileall -q overwatch
```

Node is needed only for the optional JavaScript syntax check. Three DMTF integration tests are skipped unless their environment variables are set. The main tests run real local HTTP servers and require permission to use loopback sockets.

The suite covers:

- Healthy and degraded observations, duplicate events and persistent case state.
- Receipt hashes, measurement freshness, unverified timestamps and simulation isolation.
- Wrong roles, missing approvals, two-person approval, plan changes, permit expiration and kill switches.
- Atomic single-use permits, budget reservation, ambiguous outcomes and restart reconciliation.
- False action success, pre-action observations, interrupted soak periods and verified recovery.
- Exact decimal bid totals, qualification gates, quote expiry, currencies and equivalent quantity/scope.
- API authentication, origin/host checks, role spoofing and end-to-end HTTP operation.
- MCP lifecycle, zone-specific read-only tools and architectural dependency boundaries.

See [verification protocol](docs/verification.md) for the release gates and [TDD record](docs/tdd-record.md) for the red/green development sequence.

An optional real-browser smoke runner is provided in `scripts/ui-smoke.cjs`. Install Playwright and its browser in your development environment, then run `node scripts/ui-smoke.cjs`. `PLAYWRIGHT_MODULE` can point to an existing package and `PLAYWRIGHT_CHANNEL=msedge` selects an installed Edge browser. The runner launches/stops its own local app, checks the full workflow and tender UI, and saves screenshots and a report under ignored `artifacts/`.

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

These are a minimal MCP 2025-06-18 stdio implementation. Tools return at most the latest 100 matching records. There are no model-accessible approval, signing, policy-update or execution tools. The broker is invoked by the authenticated workflow/API path. No LLM provider is called or billed; a connected model may use the read tools for analysis, while plans in this release use deterministic templates.

## Repository layout

```text
overwatch/
  service.py       Cases, verifier, approvals, permit lifecycle, broker orchestration
  policy.py        Decimal amounts, role policy, canonical plan hashes, permit signing
  store.py         SQLite transactions and hash-chained events
  redfish.py       Link-following collector and simulation-only action adapter
  simulator.py     Stateful local Redfish scenarios and action outcomes
  api.py           Authenticated loopback API and UI hosting
  mcp.py           Read-only stdio tools split by trust zone
  tender.py        Deterministic bid eligibility and comparison
  assist.py        Dashboard LLM assistant (Zhipu/GLM or Claude); read/draft-only tools
  discovery.py     Crustdata provider discovery (live client + keyless fixture), compliance screen
  scoring.py       Weighted qualification rubric; unverified factors score zero
  web/             Operations desk
tests/             Behavioral, HTTP, MCP, architecture and upstream tests
integrations/dmtf/ Pinned upstream manifest, dependency lock and Docker recipe
docs/              Architecture, verification, limitations and decisions
examples/          MCP and tender inputs
.github/workflows/ Windows/Linux tests and upstream integration CI
```

## Production boundary

This is a working foundation, **not a production-certified fleet controller**. The local HTTP server is for loopback development. Policy and broker classes currently share a process, and HMAC signing is local. SQLite's ledger is tamper-evident but not WORM or independently anchored.

Before enabling production actions: separate the policy/signing service and broker identities, introduce SSO and customer vaults, use a production database and immutable evidence store, implement audited OEM-specific adapters and durable job infrastructure, validate prediction models on real labels, and pass physical-hardware recovery tests. Live Crustdata, CMMS, ERP, provider qualification and RFQ delivery require credentials, reviewed schemas and partner integration work. See the [implementation status](docs/implementation-status.md).

Production read collection requires HTTPS with certificate verification:

```powershell
python -m overwatch collect --url https://your-authorized-bmc --environment production --data data/production-readonly
```

An optional `WATCHER_REDFISH_AUTHORIZATION` environment variable supplies an authorization header, and `--ca-file` accepts a customer CA. The collector does not log the header. Production and simulation databases are deliberately incompatible. This release rejects execution plans in production mode.

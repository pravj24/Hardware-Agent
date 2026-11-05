# WatcherMCP Web Service Deployment (`deploy/`)

This standalone `deploy/` folder exposes the three read-only WatcherMCP trust zones (`fleet`, `records`, and `market`) over **Streamable HTTP** (`POST /mcp/<zone>`) and **HTTP + SSE** (`GET /mcp/<zone>/sse` + `POST /mcp/<zone>/messages`), along with ready-to-use MCP directory configurations (`smithery.yaml`, `glama.json`, `mcp.json`, and `Dockerfile`).

## Folder Contents

| File | Purpose |
| :--- | :--- |
| `mcp_web_server.py` | Zero-dependency Python HTTP/SSE MCP web gateway wrapping `overwatch.mcp.MCPServer` and `overwatch.store.Store` |
| `Dockerfile` | Container image build for hosting all three MCP zones (`fleet`, `records`, `market`) or a single zone over HTTP |
| `smithery.yaml` | Container & HTTP transport configuration for the Smithery MCP registry |
| `glama.json` | Server manifest and tool metadata for the Glama MCP directory |
| `mcp.json` | Client connection config (`streamable-http` and `sse`) for MCP Inspector, Cursor, and Claude |
| `test_mcp_web.py` | End-to-end `unittest` suite verifying HTTP JSON-RPC, SSE bootstrap, trust-zone isolation, and Bearer token auth |

## 1. Drop Into the Repository

Copy the `deploy/` directory into the root of your [`WatcherMCP`](https://github.com/pravj24/WatcherMCP) repository:

```text
WatcherMCP/
├── deploy/
│   ├── __init__.py
│   ├── Dockerfile
│   ├── README.md
│   ├── glama.json
│   ├── mcp.json
│   ├── mcp_web_server.py
│   ├── smithery.yaml
│   └── test_mcp_web.py
├── overwatch/
└── pyproject.toml
```

## 2. Run Locally as a Web Service

From the `WatcherMCP` repository root:

```bash
python deploy/mcp_web_server.py --port 8090 --seed-demo
```

* If `data/overwatch.db` does not exist yet, `--seed-demo` initializes the SQLite ledger and seeds simulation records (`rcpt_seed_001`, `case_seed_001`, `plan_seed_001`, `tndr_seed_001`) so every zone works immediately.
* To serve a single isolated zone per process/container, pass `--zone fleet`, `--zone records`, or `--zone market`.
* To require Bearer authentication, pass `--token <secret>` or set `WATCHER_MCP_TOKEN=<secret>`.

## 3. Web Endpoints

| Endpoint | Method | Description |
| :--- | :--- | :--- |
| `/health` | `GET` | Verifies service status and cryptographic SQLite event-chain integrity (`store.verify_ledger()`) |
| `/mcp` | `GET` | Lists active trust zones, transport URLs, and read-only tool names |
| `/mcp/fleet` | `POST` | Streamable HTTP JSON-RPC endpoint for `fleet.get_observations` |
| `/mcp/records` | `POST` | Streamable HTTP JSON-RPC endpoint for `records.get_cases` and `records.get_plans` |
| `/mcp/market` | `POST` | Streamable HTTP JSON-RPC endpoint for `market.get_tenders` |
| `/mcp/<zone>/sse` | `GET` | Opens an SSE stream and emits `/mcp/<zone>/messages?session_id=<id>` |

### Quick `curl` Smoke Check

```bash
# Health & ledger verification
curl -s http://127.0.0.1:8090/health

# Call fleet.get_observations over HTTP JSON-RPC
curl -s -X POST http://127.0.0.1:8090/mcp/fleet \
  -H "Content-Type: application/json" \
  -d '{"jsonrpc":"2.0","id":1,"method":"tools/call","params":{"name":"fleet.get_observations","arguments":{}}}'
```

## 4. Run with Docker

From the `WatcherMCP` repository root:

```bash
docker build -f deploy/Dockerfile -t watchermcp-web .
docker run --rm -p 8090:8090 watchermcp-web
```

## 5. OAuth 2.1 + onboarding (optional)

Enable the OAuth authorization server so Claude Code can connect over OAuth (instead of a static `--token`) and a user can onboard **through the MCP interface**:

```bash
python deploy/mcp_web_server.py --port 8090 --oauth --dashboard-url http://127.0.0.1:8080
```

This serves OAuth discovery (`/.well-known/oauth-protected-resource`, `/.well-known/oauth-authorization-server`), dynamic client registration (`/register`), PKCE authorize/token (`/authorize`, `/token`), and issues audience-bound, short-lived bearer tokens; `/mcp/*` then requires a valid token and returns `401` with a `WWW-Authenticate` challenge otherwise. When OAuth is on, `tools/list` also advertises **`overwatch.whoami`**, which returns the caller's role, scopes and onboarding gate status plus a link to the dashboard. It is **demo-grade**: `/authorize` auto-approves `--demo-role` instead of a real login; production terminates TLS at the ingress and swaps that step for the customer's SSO/OIDC. This is the gateway the onboarding Phase 6 ("AI clients") gate certifies. Add in Claude Code with `claude mcp add --transport http overwatch-fleet http://127.0.0.1:8090/mcp/fleet`.

## 6. Run the Verification Tests

```bash
python -m unittest discover -s deploy -p "test_*.py" -v
```

# WatcherMCP — Codex and Claude Code plugin

Adds the Hardware Overwatch **read-only MCP trust zones** to Codex and Claude Code as three stdio MCP servers:

| Server | Zone | Tools |
|---|---|---|
| `watcher-fleet` | fleet | `fleet.get_observations` |
| `watcher-records` | records | `records.get_cases`, `records.get_plans` |
| `watcher-market` | market | `market.get_tenders` (discovery long-lists, scored shortlists, bid comparisons) |

These tools are **read-only** — there is no approve, sign, permit or execute tool. The broker and policy service are never reachable from the plugin. A connected model can read the Overwatch evidence store (telemetry receipts, cases, plans, tender records) for analysis.

## Prerequisites

The servers run `python -m overwatch ...`, so the `overwatch` package must be importable and a database must exist:

```bash
python -m pip install -e . # from a checkout of github.com/pravj24/WatcherMCP
python -m overwatch demo  # once, to create data/overwatch.db (Ctrl+C after it starts)
```

Use Python 3.11 or newer. The `python` executable on the client's PATH must be the
environment where you installed WatcherMCP. If using a virtual environment, activate
it before launching the client.

Point the plugin at your database with `WATCHER_MCP_DB`. Use an **absolute path** so
it also works when the client runs the plugin from its installation cache or another
project. Without this variable, the CLI defaults to `data/overwatch.db` relative to
the server process's working directory. An explicit `--db` argument takes precedence.

```bash
export WATCHER_MCP_DB=/absolute/path/to/WatcherMCP/data/overwatch.db
```

In PowerShell:

```powershell
$env:WATCHER_MCP_DB = 'C:\absolute\path\to\WatcherMCP\data\overwatch.db'
```

The variable must be available to the Codex process when it starts. Restart an
already-running desktop client after changing its environment. No API key is needed.

## Install in Codex

From this checkout, register the local marketplace and install the plugin:

```bash
codex plugin marketplace add .
codex plugin add watchermcp@watchermcp
```

Once the plugin changes are published to GitHub, install from the repository instead:

```bash
codex plugin marketplace add pravj24/WatcherMCP
codex plugin add watchermcp@watchermcp
```

Start a new Codex session, run `/mcp`, and check that `watcher-fleet`,
`watcher-records`, and `watcher-market` are available (the client may namespace their
names). Ask: **"Summarize the latest hardware observations and open cases, citing
receipt and case IDs."** An empty database returns empty lists; collect telemetry
in the demo to create evidence first.

On clients with a plugin browser, select the **WatcherMCP** marketplace and install
**WatcherMCP** there. If `codex plugin` is unavailable, update to a Codex release
with plugin support. CLI commands were checked with Codex CLI 0.160.0.

Codex reads `.codex-plugin/plugin.json` and its explicit `.mcp.codex.json` server
configuration. That configuration forwards `WATCHER_MCP_DB` with `env_vars`; Python
reads the variable directly, so it does not depend on shell expansion in JSON args.
Claude Code continues to use `.claude-plugin/plugin.json` and `.mcp.json`.
Both use the repository's existing `.claude-plugin/marketplace.json`, which Codex
supports as a compatibility marketplace. See the
[official OpenAI plugin packaging documentation](https://developers.openai.com/plugins/build/plugins).

This package is for repository/local installation. It has not been submitted to
the public plugin directory, and it does not install Python or create a database.

## Install in Claude Code

```text
# In Claude Code:
/plugin marketplace add pravj24/WatcherMCP
/plugin install watchermcp@watchermcp
```

Then check the servers with `/mcp`. Each zone is a separate stdio process started by Claude Code.

## Troubleshooting

- **`No module named overwatch`:** run `python -m pip install -e .` from the source
  checkout in the Python environment used to launch Codex, then restart the client.
- **`database not found`:** initialize the database with the demo, set
  `WATCHER_MCP_DB` to its absolute path, then restart the client.
- **No records:** collect telemetry in the operations desk; the MCP tools read
  existing records and do not collect observations or create plans themselves.

Run the plugin's process-level checks from the repository root:

```bash
python -m unittest tests.test_plugin tests.test_mcp tests.test_architecture -v
```

## Remote (HTTP + OAuth) alternative

For a central deployment, run the HTTP gateway instead of stdio and add it with the remote transport:

```bash
python deploy/mcp_web_server.py --port 8090 --oauth
# then, in Claude Code:
claude mcp add --transport http overwatch-fleet http://127.0.0.1:8090/mcp/fleet
```

OAuth discovery, PKCE and the `overwatch.whoami` onboarding tool are described in [`deploy/README.md`](../deploy/README.md).

# WatcherMCP — Claude Code plugin

Adds the Hardware Overwatch **read-only MCP trust zones** to Claude Code as three stdio MCP servers:

| Server | Zone | Tools |
|---|---|---|
| `watcher-fleet` | fleet | `fleet.get_observations` |
| `watcher-records` | records | `records.get_cases`, `records.get_plans` |
| `watcher-market` | market | `market.get_tenders` (discovery long-lists, scored shortlists, bid comparisons) |

These tools are **read-only** — there is no approve, sign, permit or execute tool. The broker and policy service are never reachable from the plugin. A connected model can read the Overwatch evidence store (telemetry receipts, cases, plans, tender records) for analysis.

## Prerequisites

The servers run `python -m overwatch ...`, so the `overwatch` package must be importable and a database must exist:

```bash
pip install -e .          # from a checkout of github.com/pravj24/WatcherMCP
python -m overwatch demo  # once, to create data/overwatch.db (Ctrl+C after it starts)
```

Point the plugin at your database with the `WATCHER_MCP_DB` environment variable (absolute path recommended); it defaults to `data/overwatch.db` relative to Claude Code's working directory.

```bash
export WATCHER_MCP_DB=/absolute/path/to/WatcherMCP/data/overwatch.db
```

## Install

```text
# In Claude Code:
/plugin marketplace add pravj24/WatcherMCP
/plugin install watchermcp@watchermcp
```

Then check the servers with `/mcp`. Each zone is a separate stdio process started by Claude Code.

## Remote (HTTP + OAuth) alternative

For a central deployment, run the HTTP gateway instead of stdio and add it with the remote transport:

```bash
python deploy/mcp_web_server.py --port 8090 --oauth
# then, in Claude Code:
claude mcp add --transport http overwatch-fleet http://127.0.0.1:8090/mcp/fleet
```

OAuth discovery, PKCE and the `overwatch.whoami` onboarding tool are described in [`deploy/README.md`](../deploy/README.md).

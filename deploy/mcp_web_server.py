"""Web service wrapper exposing WatcherMCP trust zones (`fleet`, `records`, `market`) over HTTP & SSE.

Drop the `deploy/` directory into the root of the WatcherMCP repository and run:
    python deploy/mcp_web_server.py --port 8090 --seed-demo

Supports:
- Streamable HTTP JSON-RPC (`POST /mcp/fleet`, `POST /mcp/records`, `POST /mcp/market`, or `POST /mcp` in single-zone mode)
- HTTP + SSE transport (`GET /mcp/<zone>/sse` + `POST /mcp/<zone>/messages?session_id=<id>`)
- Session management via `Mcp-Session-Id` header plus automatic stateless/re-init fallback
- Health & ledger integrity check (`GET /health`) and service discovery (`GET /`, `GET /mcp`)
- Optional Bearer token authentication (`--token` or `WATCHER_MCP_TOKEN`)
"""
from __future__ import annotations

import argparse
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import queue
import secrets
import sys
import threading
import time
from urllib.parse import parse_qs, urlparse

# Ensure repository root is importable when executed as `python deploy/mcp_web_server.py`
REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from overwatch.mcp import MCPServer, TOOL_SETS  # noqa: E402
from overwatch.store import Store, canonical, digest  # noqa: E402
from overwatch.onboarding import GATES, CONNECTORS  # noqa: E402
from deploy.oauth import OAuthProvider  # noqa: E402

VALID_ZONES = tuple(TOOL_SETS.keys())
MAX_BODY_BYTES = 1_000_000
WHOAMI_TOOL = {
    "name": "overwatch.whoami",
    "description": "Your authenticated role, scopes and onboarding gate status; links to the dashboard to complete onboarding. Read-only.",
    "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False},
    "annotations": {"readOnlyHint": True, "destructiveHint": False, "idempotentHint": True, "openWorldHint": False},
}


def onboarding_whoami(store: Store, claims: dict, dashboard_url: str) -> dict:
    """Identity + per-role onboarding gate status, read from the store. No writes."""
    role = (claims or {}).get("role")
    state = store.read("onboarding", "setup")
    owned = []
    for gate, (title, owners, _days, _criterion) in GATES.items():
        gate_owners = list(CONNECTORS.values()) if gate == "connectors" else owners
        if role in gate_owners:
            signed = bool(state) and any(s.get("role") == role for s in state.get("signatures", {}).get(gate, []))
            owned.append({"gate": gate, "title": title, "signed_by_your_role": signed})
    return {
        "identity": {"role": role, "scope": (claims or {}).get("scope"), "subject": (claims or {}).get("sub")},
        "onboarding": {"started": bool(state), "stage": state.get("stage") if state else None,
                       "your_gates": owned, "dashboard_url": dashboard_url},
        "notice": "Read-only MCP identity. Sign gates and complete onboarding in the operations dashboard; the MCP interface cannot approve, sign or execute.",
    }


def seed_demo_records(store: Store, now: float | None = None) -> dict[str, int]:
    """Populate initial simulation receipts, cases, plans, and tenders if the database is empty."""
    ts = float(now if now is not None else time.time())
    with store.transaction() as tx:
        if not tx.get("settings", "policy"):
            tx.put("settings", {
                "id": "policy",
                "environment": "simulation",
                "budget": "10000.00",
                "spent": "0.00",
                "kill_switch": False,
                "freeze": False,
                "version": "1.0",
            })
            tx.event("initialized", {"environment": "simulation", "policy": "1.0"}, ts)

        if not tx.all("receipts"):
            body = {
                "asset_id": "rack1-node01",
                "temperature": 84.5,
                "observed_at": ts - 15.0,
                "source": "https://127.0.0.1:8001/redfish/v1/Chassis/1U/Thermal",
                "event_id": "evt-seed-001",
                "timestamp_basis": "device",
            }
            receipt = {
                "id": "rcpt_seed_001",
                "body": body,
                "sha256": digest(body),
                "environment": "simulation",
                "authority": "device",
                "retrieved_at": ts - 10.0,
                "max_age": 300,
                "fixture": True,
            }
            tx.put("receipts", receipt)
            tx.event("observation", {"receipt_id": receipt["id"], "hash": receipt["sha256"], "case_id": "case_seed_001"}, ts - 10.0)

        if not tx.all("cases"):
            case = {
                "id": "case_seed_001",
                "asset_id": "rack1-node01",
                "state": "PLANNED",
                "receipt_ids": ["rcpt_seed_001"],
                "plan_id": "plan_seed_001",
                "created_at": ts - 10.0,
                "updated_at": ts - 5.0,
                "healthy_since": None,
                "healthy_samples": 0,
                "last_observed_at": ts - 15.0,
                "title": "Thermal anomaly: rack1-node01",
            }
            tx.put("cases", case)

        if not tx.all("plans"):
            plan_body = {
                "case_id": "case_seed_001",
                "asset_id": "rack1-node01",
                "environment": "simulation",
                "action": "thermal.throttle_workload",
                "estimated_cost_usd": "0.00",
                "receipt_ids": ["rcpt_seed_001"],
            }
            plan = {
                "id": "plan_seed_001",
                "case_id": "case_seed_001",
                "asset_id": "rack1-node01",
                "status": "PROPOSED",
                "plan_hash": digest(plan_body),
                "body": plan_body,
                "created_at": ts - 5.0,
            }
            tx.put("plans", plan)

        if not tx.all("tenders"):
            tender = {
                "id": "tndr_seed_001",
                "case_id": "case_seed_001",
                "asset_id": "rack1-node01",
                "component": "Hot-swap fan module FRU-402",
                "recommendation": "proceed_to_procurement",
                "selected_vendor": "Apex DataCenter Parts",
                "unit_price_usd": "185.00",
                "created_at": ts - 2.0,
            }
            tx.put("tenders", tender)

    return {kind: len(store.list(kind)) for kind in ("receipts", "cases", "plans", "tenders")}


class ZoneSessionRegistry:
    """Manages per-zone MCPServer instances for Streamable HTTP and SSE clients."""

    def __init__(self, store: Store, active_zones: tuple[str, ...] = VALID_ZONES, stateless_fallback: bool = True):
        for z in active_zones:
            if z not in TOOL_SETS:
                raise ValueError(f"Invalid MCP zone: {z}")
        self.store = store
        self.active_zones = tuple(active_zones)
        self.stateless_fallback = stateless_fallback
        self._lock = threading.Lock()
        self._sessions: dict[tuple[str, str], MCPServer] = {}
        self._default_sessions: dict[str, str] = {}
        self._sse_queues: dict[tuple[str, str], queue.Queue] = {}

    def _new_ready_server(self, zone: str) -> MCPServer:
        server = MCPServer(self.store, zone)
        server.handle({
            "jsonrpc": "2.0",
            "id": "_boot",
            "method": "initialize",
            "params": {"protocolVersion": "2025-06-18"},
        })
        server.handle({"jsonrpc": "2.0", "method": "notifications/initialized"})
        return server

    def create_sse_session(self, zone: str) -> tuple[str, queue.Queue]:
        with self._lock:
            session_id = secrets.token_urlsafe(16)
            self._sessions[(zone, session_id)] = MCPServer(self.store, zone)
            q: queue.Queue = queue.Queue()
            self._sse_queues[(zone, session_id)] = q
            return session_id, q

    def remove_sse_session(self, zone: str, session_id: str) -> None:
        with self._lock:
            self._sse_queues.pop((zone, session_id), None)
            self._sessions.pop((zone, session_id), None)

    def dispatch(self, zone: str, payload: dict, session_id: str | None = None) -> tuple[dict | None, str]:
        """Dispatch a JSON-RPC payload to the target zone's MCPServer and return (response, session_id)."""
        if zone not in self.active_zones:
            raise KeyError(f"Zone '{zone}' is not enabled on this server")

        method = payload.get("method") if isinstance(payload, dict) else None

        with self._lock:
            if method == "initialize":
                sid = session_id or secrets.token_urlsafe(16)
                server = MCPServer(self.store, zone)
                self._sessions[(zone, sid)] = server
                self._default_sessions[zone] = sid
            else:
                sid = session_id or self._default_sessions.get(zone)
                server = self._sessions.get((zone, sid)) if sid else None
                if server is None:
                    sid = session_id or secrets.token_urlsafe(16)
                    if self.stateless_fallback and method in {"tools/list", "tools/call", "ping"}:
                        server = self._new_ready_server(zone)
                    else:
                        server = MCPServer(self.store, zone)
                    self._sessions[(zone, sid)] = server
                    self._default_sessions.setdefault(zone, sid)

            response = server.handle(payload)
            sse_queue = self._sse_queues.get((zone, sid))
            if sse_queue is not None and response is not None:
                sse_queue.put(response)
            return response, sid


class MCPWebServer:
    """Threaded HTTP & SSE server wrapping WatcherMCP read-only trust zones."""

    def __init__(
        self,
        store: Store,
        *,
        host: str = "0.0.0.0",
        port: int = 8090,
        zones: tuple[str, ...] = VALID_ZONES,
        token: str | None = None,
        stateless_fallback: bool = True,
        oauth: OAuthProvider | None = None,
        dashboard_url: str = "http://127.0.0.1:8080",
    ):
        self.store = store
        self.host = host
        self.port = port
        self.zones = tuple(zones)
        self.token = token
        self.oauth = oauth
        self.dashboard_url = dashboard_url
        self.registry = ZoneSessionRegistry(store, active_zones=self.zones, stateless_fallback=stateless_fallback)
        self._httpd: ThreadingHTTPServer | None = None
        self._thread: threading.Thread | None = None

    @property
    def url(self) -> str:
        bind_host = "127.0.0.1" if self.host in {"0.0.0.0", "::"} else self.host
        actual_port = self._httpd.server_port if self._httpd else self.port
        return f"http://{bind_host}:{actual_port}"

    def _make_handler(self):
        parent = self

        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def log_message(self, fmt, *args):
                return

            def _send_cors_headers(self):
                self.send_header("Access-Control-Allow-Origin", "*")
                self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
                self.send_header("Access-Control-Allow-Headers", "Content-Type, Authorization, Mcp-Session-Id, Accept")
                self.send_header("Access-Control-Expose-Headers", "Mcp-Session-Id")

            def _send_json(self, status: int, data: dict, extra_headers: dict[str, str] | None = None):
                body = canonical(data).encode("utf-8")
                self.send_response(status)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self._send_cors_headers()
                if extra_headers:
                    for k, v in extra_headers.items():
                        self.send_header(k, v)
                self.end_headers()
                self.wfile.write(body)

            def _check_auth(self) -> bool:
                auth = self.headers.get("Authorization", "")
                self.claims = None
                if parent.token and auth == f"Bearer {parent.token}":
                    self.claims = {"role": "static-token", "scope": "mcp:read"}
                    return True
                if parent.oauth is not None:
                    try:
                        self.claims = parent.oauth.validate(auth)
                        return True
                    except Exception as exc:
                        resource = parent.oauth.public_url + "/.well-known/oauth-protected-resource"
                        self._send_json(HTTPStatus.UNAUTHORIZED,
                                        {"error": "unauthorized", "message": str(exc)},
                                        extra_headers={"WWW-Authenticate": f'Bearer resource_metadata="{resource}"'})
                        return False
                if parent.token:
                    self._send_json(HTTPStatus.UNAUTHORIZED,
                                    {"error": "Unauthorized", "message": "Valid Bearer token required"})
                    return False
                return True

            def _resolve_zone_and_route(self, path: str) -> tuple[str | None, str]:
                parts = [p for p in path.strip("/").split("/") if p]
                if not parts:
                    return None, "index"
                if parts in (["health"], ["healthz"]):
                    return None, "health"
                if parts == ["mcp"]:
                    if len(parent.zones) == 1:
                        return parent.zones[0], "rpc"
                    return None, "index"
                if len(parts) >= 2 and parts[0] == "mcp" and parts[1] in TOOL_SETS:
                    zone = parts[1]
                    if len(parts) == 2:
                        return zone, "rpc"
                    if len(parts) == 3 and parts[2] == "sse":
                        return zone, "sse"
                    if len(parts) == 3 and parts[2] == "messages":
                        return zone, "messages"
                return None, "not_found"

            def do_OPTIONS(self):
                self.send_response(HTTPStatus.NO_CONTENT)
                self.send_header("Content-Length", "0")
                self._send_cors_headers()
                self.end_headers()

            def do_GET(self):
                parsed = urlparse(self.path)
                zone, route = self._resolve_zone_and_route(parsed.path)

                if route == "health":
                    ledger_ok = parent.store.verify_ledger()
                    status = HTTPStatus.OK if ledger_ok else HTTPStatus.SERVICE_UNAVAILABLE
                    self._send_json(status, {
                        "status": "ok" if ledger_ok else "degraded",
                        "service": "watchermcp-web",
                        "protocolVersion": "2025-06-18",
                        "zones": list(parent.zones),
                        "ledger_valid": ledger_ok,
                    })
                    return

                if parent.oauth is not None:
                    if parsed.path.startswith("/.well-known/oauth-protected-resource"):
                        self._send_json(HTTPStatus.OK, parent.oauth.protected_resource())
                        return
                    if parsed.path == "/.well-known/oauth-authorization-server":
                        self._send_json(HTTPStatus.OK, parent.oauth.metadata())
                        return
                    if parsed.path == "/authorize":
                        try:
                            location = parent.oauth.authorize({k: v[0] for k, v in parse_qs(parsed.query).items()})
                        except Exception as exc:
                            self._send_json(HTTPStatus.BAD_REQUEST, {"error": str(exc)})
                            return
                        self.send_response(HTTPStatus.FOUND)
                        self.send_header("Location", location)
                        self.send_header("Content-Length", "0")
                        self._send_cors_headers()
                        self.end_headers()
                        return

                if not self._check_auth():
                    return

                if route == "index":
                    zone_endpoints = {
                        z: {
                            "http": f"/mcp/{z}",
                            "sse": f"/mcp/{z}/sse",
                            "messages": f"/mcp/{z}/messages",
                            "tools": list(TOOL_SETS[z].keys()),
                        }
                        for z in parent.zones
                    }
                    self._send_json(HTTPStatus.OK, {
                        "name": "watchermcp-web",
                        "version": "0.1.0",
                        "protocolVersion": "2025-06-18",
                        "mode": "read-only",
                        "zones": zone_endpoints,
                    })
                    return

                if route == "rpc" and zone in parent.zones:
                    accept = self.headers.get("Accept", "")
                    if "text/event-stream" not in accept:
                        self._send_json(HTTPStatus.OK, {
                            "name": f"watchermcp-{zone}",
                            "zone": zone,
                            "protocolVersion": "2025-06-18",
                            "transport": ["streamable-http", "sse"],
                            "endpoints": {
                                "rpc": f"/mcp/{zone}",
                                "sse": f"/mcp/{zone}/sse",
                                "messages": f"/mcp/{zone}/messages",
                            },
                            "tools": list(TOOL_SETS[zone].keys()),
                        })
                        return
                    route = "sse"

                if route == "sse" and zone in parent.zones:
                    session_id, msg_queue = parent.registry.create_sse_session(zone)
                    self.send_response(HTTPStatus.OK)
                    self.send_header("Content-Type", "text/event-stream; charset=utf-8")
                    self.send_header("Cache-Control", "no-cache")
                    self.send_header("Connection", "keep-alive")
                    self.send_header("Mcp-Session-Id", session_id)
                    self._send_cors_headers()
                    self.end_headers()
                    try:
                        endpoint_uri = f"/mcp/{zone}/messages?session_id={session_id}"
                        self.wfile.write(f"event: endpoint\ndata: {endpoint_uri}\n\n".encode("utf-8"))
                        self.wfile.flush()
                        query = parse_qs(parsed.query)
                        if query.get("once") == ["1"]:
                            return
                        while True:
                            try:
                                message = msg_queue.get(timeout=15.0)
                                data = canonical(message)
                                self.wfile.write(f"event: message\ndata: {data}\n\n".encode("utf-8"))
                                self.wfile.flush()
                            except queue.Empty:
                                self.wfile.write(b": keepalive\n\n")
                                self.wfile.flush()
                    except (BrokenPipeError, ConnectionResetError, OSError):
                        pass
                    finally:
                        self.close_connection = True
                        parent.registry.remove_sse_session(zone, session_id)
                    return

                self._send_json(HTTPStatus.NOT_FOUND, {"error": "Not found"})

            def _handle_oauth_post(self, path):
                try:
                    length = int(self.headers.get("Content-Length", "0"))
                except ValueError:
                    length = 0
                raw = self.rfile.read(length) if 0 < length <= MAX_BODY_BYTES else b""
                if path == "/register":
                    self._send_json(HTTPStatus.CREATED, parent.oauth.register())
                    return
                form = {k: v[0] for k, v in parse_qs(raw.decode("utf-8", "replace")).items()}
                try:
                    self._send_json(HTTPStatus.OK, parent.oauth.token(form))
                except Exception as exc:
                    self._send_json(HTTPStatus.BAD_REQUEST, {"error": "invalid_grant", "error_description": str(exc)})

            def do_POST(self):
                parsed = urlparse(self.path)
                if parent.oauth is not None and parsed.path in ("/register", "/token"):
                    self._handle_oauth_post(parsed.path)
                    return
                if not self._check_auth():
                    return

                zone, route = self._resolve_zone_and_route(parsed.path)
                if route not in {"rpc", "messages"} or zone not in parent.zones:
                    self._send_json(HTTPStatus.NOT_FOUND, {"error": "Unknown MCP zone endpoint"})
                    return

                try:
                    length = int(self.headers.get("Content-Length", "0"))
                except ValueError:
                    length = 0
                if length <= 0 or length > MAX_BODY_BYTES:
                    self._send_json(HTTPStatus.BAD_REQUEST, {
                        "jsonrpc": "2.0",
                        "id": None,
                        "error": {"code": -32700, "message": "Invalid or oversized request body"},
                    })
                    return

                raw_body = self.rfile.read(length)
                try:
                    payload = json.loads(raw_body.decode("utf-8"))
                except (UnicodeDecodeError, json.JSONDecodeError):
                    self._send_json(HTTPStatus.BAD_REQUEST, {
                        "jsonrpc": "2.0",
                        "id": None,
                        "error": {"code": -32700, "message": "Parse error"},
                    })
                    return

                query = parse_qs(parsed.query)
                session_id = (
                    self.headers.get("Mcp-Session-Id")
                    or (query.get("session_id", [None])[0])
                )

                method = payload.get("method") if isinstance(payload, dict) else None
                params = payload.get("params") if isinstance(payload, dict) else None
                if parent.oauth is not None and method == "tools/call" and isinstance(params, dict) \
                        and params.get("name") == "overwatch.whoami":
                    result = onboarding_whoami(parent.store, getattr(self, "claims", None), parent.dashboard_url)
                    extra = {"Mcp-Session-Id": session_id} if session_id else {}
                    self._send_json(HTTPStatus.OK, {"jsonrpc": "2.0", "id": payload.get("id"),
                        "result": {"content": [{"type": "text", "text": canonical(result)}], "isError": False}},
                        extra_headers=extra)
                    return

                response, active_sid = parent.registry.dispatch(zone, payload, session_id=session_id)
                if parent.oauth is not None and method == "tools/list" and isinstance(response, dict) \
                        and isinstance(response.get("result"), dict) and isinstance(response["result"].get("tools"), list):
                    response["result"]["tools"].append(WHOAMI_TOOL)
                headers = {"Mcp-Session-Id": active_sid}
                if response is None:
                    self.send_response(HTTPStatus.ACCEPTED)
                    self.send_header("Content-Length", "0")
                    self._send_cors_headers()
                    for k, v in headers.items():
                        self.send_header(k, v)
                    self.end_headers()
                    return

                self._send_json(HTTPStatus.OK, response, extra_headers=headers)

        return Handler

    def start(self) -> "MCPWebServer":
        self._httpd = ThreadingHTTPServer((self.host, self.port), self._make_handler())
        self.port = self._httpd.server_port
        self._thread = threading.Thread(target=self._httpd.serve_forever, daemon=True)
        self._thread.start()
        return self

    def close(self) -> None:
        if self._httpd is not None:
            self._httpd.shutdown()
            self._httpd.server_close()
            self._httpd = None
        if self._thread is not None:
            self._thread.join(timeout=2.0)
            self._thread = None


def parse_zones(zone_arg: str) -> tuple[str, ...]:
    if zone_arg == "all":
        return VALID_ZONES
    parts = tuple(z.strip() for z in zone_arg.split(",") if z.strip())
    for z in parts:
        if z not in VALID_ZONES:
            raise ValueError(f"Unsupported zone '{z}'. Choose from: {', '.join(VALID_ZONES)} or 'all'.")
    return parts


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="WatcherMCP HTTP/SSE Web Service Gateway")
    parser.add_argument(
        "--host",
        default=os.environ.get("WATCHER_MCP_HOST", "0.0.0.0"),
        help="Bind host (default: 0.0.0.0)",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=int(os.environ.get("PORT", os.environ.get("WATCHER_MCP_PORT", "8090"))),
        help="Bind port (default: $PORT or 8090)",
    )
    parser.add_argument(
        "--zone",
        default=os.environ.get("WATCHER_MCP_ZONE", "all"),
        help="MCP zone(s) to serve: 'fleet', 'records', 'market', comma-separated, or 'all' (default: all)",
    )
    parser.add_argument(
        "--db",
        type=Path,
        default=Path(os.environ.get("WATCHER_MCP_DB", "data/overwatch.db")),
        help="Path to SQLite database (default: data/overwatch.db)",
    )
    parser.add_argument(
        "--token",
        default=os.environ.get("WATCHER_MCP_TOKEN"),
        help="Optional Bearer token for HTTP authentication",
    )
    parser.add_argument(
        "--seed-demo",
        action="store_true",
        default=os.environ.get("WATCHER_MCP_SEED_DEMO", "1").lower() in {"1", "true", "yes"},
        help="Seed initial simulation records if the database is empty (default: enabled)",
    )
    parser.add_argument(
        "--no-seed-demo",
        action="store_false",
        dest="seed_demo",
        help="Disable automatic demo seeding when initializing a new database",
    )
    parser.add_argument(
        "--strict-session",
        action="store_true",
        default=os.environ.get("WATCHER_MCP_STRICT_SESSION", "0").lower() in {"1", "true", "yes"},
        help="Require explicit MCP initialize handshake before tools/list or tools/call",
    )
    parser.add_argument(
        "--oauth",
        action="store_true",
        default=os.environ.get("WATCHER_MCP_OAUTH", "0").lower() in {"1", "true", "yes"},
        help="Enable the OAuth 2.1 authorization server (discovery, PKCE, audience-bound tokens) + the overwatch.whoami onboarding tool",
    )
    parser.add_argument("--public-url", default=os.environ.get("WATCHER_MCP_PUBLIC_URL"),
                        help="Externally reachable base URL for OAuth metadata/redirects (default http://127.0.0.1:<port>)")
    parser.add_argument("--dashboard-url", default=os.environ.get("WATCHER_MCP_DASHBOARD_URL", "http://127.0.0.1:8080"),
                        help="Operations dashboard URL surfaced by the onboarding whoami tool")
    parser.add_argument("--demo-role", default=os.environ.get("WATCHER_MCP_DEMO_ROLE", "operator"),
                        help="Role auto-approved by the demo OAuth /authorize step (replace with real SSO in production)")
    args = parser.parse_args(argv)

    zones = parse_zones(args.zone)
    store = Store(args.db)
    oauth = None
    if args.oauth:
        signing = os.environ.get("WATCHER_MCP_SIGNING_KEY")
        key = bytes.fromhex(signing) if signing else secrets.token_bytes(32)
        public_url = args.public_url or f"http://127.0.0.1:{args.port}"
        oauth = OAuthProvider(key, public_url, demo_role=args.demo_role)
    if args.seed_demo:
        counts = seed_demo_records(store)
    else:
        counts = {kind: len(store.list(kind)) for kind in ("receipts", "cases", "plans", "tenders")}

    server = MCPWebServer(
        store,
        host=args.host,
        port=args.port,
        zones=zones,
        token=args.token,
        stateless_fallback=not args.strict_session,
        oauth=oauth,
        dashboard_url=args.dashboard_url,
    ).start()

    print(f"WatcherMCP Web Service running at {server.url}", flush=True)
    print(f"Database: {Path(args.db).resolve()} (records: {json.dumps(counts)})", flush=True)
    for z in zones:
        print(f"  [{z}] Streamable HTTP: {server.url}/mcp/{z} | SSE: {server.url}/mcp/{z}/sse", flush=True)
    if oauth is not None:
        print(f"  OAuth 2.1 enabled (demo auto-approves role '{args.demo_role}'); discovery at "
              f"{oauth.public_url}/.well-known/oauth-authorization-server; onboarding tool: overwatch.whoami", flush=True)

    try:
        while True:
            time.sleep(0.5)
    except KeyboardInterrupt:
        return 0
    finally:
        server.close()


if __name__ == "__main__":
    raise SystemExit(main())

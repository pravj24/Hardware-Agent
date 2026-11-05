"""Authenticated loopback operations API for the simulation workbench.

Uses the standard-library HTTP server for local development only. Production ingress,
SSO, process isolation, TLS termination and rate limits are deployment work, not claims
made by this server.
"""
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import hmac
import json
from pathlib import Path
import threading
from urllib.parse import urlsplit

from .discovery import discover_providers
from .errors import Rejected
from .redfish import RedfishError
from .scoring import rank_candidates
from .service import new_id
from .tender import compare_bids


class APIServer:
    def __init__(self, app, collector, *, identities, discovery=None, host="127.0.0.1", port=8080):
        if host not in {"127.0.0.1", "localhost"}:
            raise ValueError("development API must bind to loopback")
        self.app, self.collector, self.identities, self.discovery = app, collector, identities, discovery
        self.thread = None
        api = self
        static = Path(__file__).parent / "web"

        class Handler(BaseHTTPRequestHandler):
            def setup(self):
                super().setup()
                self.connection.settimeout(10)

            def log_message(self, *args):
                pass

            def send(self, code, body, content_type="application/json"):
                payload = json.dumps(body, allow_nan=False).encode() if content_type == "application/json" else body
                self.send_response(code)
                self.send_header("Content-Type", content_type)
                self.send_header("Content-Length", str(len(payload)))
                self.send_header("Cache-Control", "no-store")
                self.send_header("X-Content-Type-Options", "nosniff")
                self.send_header("X-Frame-Options", "DENY")
                self.send_header("Content-Security-Policy", "default-src 'self'; script-src 'self'; style-src 'self'; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'")
                self.end_headers()
                self.wfile.write(payload)

            def identity(self):
                supplied = self.headers.get("Authorization", "")
                if not supplied.startswith("Bearer "):
                    return None
                token = supplied[7:]
                return next((identity for candidate, identity in api.identities.items()
                             if hmac.compare_digest(candidate, token)), None)

            def boundary(self):
                allowed = {"127.0.0.1:" + str(api.server.server_port), "localhost:" + str(api.server.server_port)}
                if self.headers.get("Host") not in allowed:
                    return False
                origin = self.headers.get("Origin")
                return not origin or origin in {"http://" + name for name in allowed}

            def do_GET(self):
                if not self.boundary():
                    return self.send(403, {"error": "origin or host rejected"})
                route = urlsplit(self.path).path
                assets = {"/": ("index.html", "text/html; charset=utf-8"),
                          "/app.js": ("app.js", "text/javascript; charset=utf-8"),
                          "/style.css": ("style.css", "text/css; charset=utf-8")}
                if route in assets:
                    filename, kind = assets[route]
                    return self.send(200, (static / filename).read_bytes(), kind)
                if route == "/healthz":
                    return self.send(200, {"status": "ok", "environment": app.environment})
                identity = self.identity()
                if not identity:
                    return self.send(401, {"error": "valid bearer token required"})
                if route == "/api/state":
                    state = app.snapshot()
                    state["identity"] = identity
                    # Dashboard needs permit IDs, not bearer-like signed permit envelopes.
                    state["permits"] = [{"id": p["id"], "plan_id": p["payload"]["plan_id"],
                                         "status": p["status"], "expires_at": p["payload"]["expires_at"]}
                                        for p in state["permits"]]
                    return self.send(200, state)
                if route == "/api/events":
                    return self.send(200, app.store.events())
                return self.send(404, {"error": "route not found"})

            def do_POST(self):
                # Consume a bounded body before replying, including refusal paths.
                # Closing a Windows socket with unread body bytes can reset the response.
                try:
                    size = int(self.headers.get("Content-Length", "0"))
                    if not 0 < size <= 1_000_000:
                        return self.send(413, {"error": "invalid request size"})
                    raw_body = self.rfile.read(size)
                    if len(raw_body) != size:
                        return self.send(400, {"error": "incomplete request body"})
                except (ValueError, OSError):
                    return self.send(400, {"error": "invalid request body"})
                if not self.boundary():
                    return self.send(403, {"error": "origin or host rejected"})
                identity = self.identity()
                if not identity:
                    return self.send(401, {"error": "valid bearer token required"})
                route = urlsplit(self.path).path
                permissions = {
                    "/api/scenario": {"operator"}, "/api/collect": {"operator"},
                    "/api/plan": {"operator", "procurement"}, "/api/verify": {"operator", "procurement"},
                    "/api/approve": {"operator", "procurement", "finance"},
                    "/api/permit": {"operator", "procurement"}, "/api/execute": {"operator", "procurement"},
                    "/api/reconcile": {"operator", "procurement"}, "/api/controls": {"policy_admin"},
                    "/api/tender/compare": {"procurement"}, "/api/tender/discover": {"procurement"},
                    "/api/tender/score": {"procurement"}}
                if route not in permissions:
                    return self.send(404, {"error": "route not found"})
                if identity["role"] not in permissions[route]:
                    return self.send(403, {"error": "role is not authorized"})
                if self.headers.get("Content-Type", "").split(";")[0] != "application/json":
                    return self.send(415, {"error": "application/json required"})
                try:
                    body = json.loads(raw_body)
                    if not isinstance(body, dict):
                        raise ValueError("body must be an object")
                    result = api.dispatch(route, body, identity)
                    self.send(200, result)
                except (Rejected, KeyError, ValueError, TypeError) as exc:
                    self.send(422, {"error": str(exc)})
                except RedfishError as exc:
                    self.send(502, {"error": str(exc)})

        self.server = ThreadingHTTPServer((host, port), Handler)
        self.server.daemon_threads = True
        self.url = "http://127.0.0.1:" + str(self.server.server_port)

    def dispatch(self, route, body, identity):
        app = self.app
        if route == "/api/scenario":
            if app.environment != "simulation":
                raise Rejected("scenario control is simulation-only")
            return self.collector.request("POST", "/lab/scenario", {"scenario": body["scenario"]})
        if route == "/api/collect":
            try:
                readings = self.collector.collect()
            except RedfishError as exc:
                known = {r["body"]["asset_id"] for r in app.store.list("receipts")
                         if r["body"]["source"] == self.collector.base_url}
                for asset in known:
                    app.mark_dark(asset, str(exc))
                raise
            cases = []
            for reading in readings:
                case = app.observe(**reading)
                if case:
                    cases.append(case)
            return {"readings": len(readings), "cases": cases}
        if route == "/api/plan":
            return app.plan(body["case_id"], amount=body.get("amount", "0.00"), action=body.get("action", "simulate_repair"))
        if route == "/api/verify":
            return app.verify(body["plan_id"])
        if route == "/api/approve":
            return app.approve(body["plan_id"], identity["actor"], identity["role"])
        if route == "/api/permit":
            permit = app.issue_permit(body["plan_id"])
            return {"id": permit["id"], "expires_at": permit["payload"]["expires_at"]}
        if route == "/api/execute":
            permit = app.store.read("permits", body["permit_id"])
            if not permit:
                raise Rejected("permit not found")
            return app.execute(permit)
        if route == "/api/reconcile":
            return app.reconcile(body["operation_id"])
        if route == "/api/controls":
            return app.set_controls(kill_switch=body.get("kill_switch"), freeze=body.get("freeze"))
        if route == "/api/tender/compare":
            if app.environment != "simulation":
                raise Rejected("manual bid intake in this release is simulation-only")
            result = compare_bids(body["bids"], now=app.clock(), currency=body.get("currency", "USD"))
            return self._record_tender("bid_comparison", "tender_compared", result, identity)
        if route == "/api/tender/discover":
            if app.environment != "simulation":
                raise Rejected("provider discovery in this release is simulation-only")
            result = discover_providers(body["need"], approved_vendors=body.get("approved_vendors"),
                                        client=self.discovery, now=app.clock(),
                                        blocked=body.get("blocked"), sanctioned=body.get("sanctioned"))
            return self._record_tender("discovery", "providers_discovered", result, identity)
        if route == "/api/tender/score":
            if app.environment != "simulation":
                raise Rejected("qualification scoring in this release is simulation-only")
            result = rank_candidates(body["candidates"], weights=body.get("weights"))
            return self._record_tender("shortlist", "candidates_scored", result, identity)
        raise Rejected("unknown action")

    def _record_tender(self, kind, event, result, identity):
        app = self.app
        result.update(id=new_id("tender"), kind=kind, environment="simulation",
                      reviewed_by=identity["actor"], created_at=app.clock())
        with app.store.transaction() as tx:
            tx.put("tenders", result)
            tx.event(event, result, app.clock())
        return result

    def start(self):
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        return self

    def close(self):
        if self.thread:
            self.server.shutdown()
            self.thread.join(timeout=3)
        self.server.server_close()

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

from .errors import Rejected
from .redfish import RedfishError
from .service import new_id
from .tender import compare_bids
from .onboarding import Onboarding
from .hackathon import Hackathon


class APIServer:
    def __init__(self, app, collector, *, identities, host="127.0.0.1", port=8080, hackathon=False):
        if host not in {"127.0.0.1", "localhost"}:
            raise ValueError("development API must bind to loopback")
        self.app, self.collector, self.identities = app, collector, identities
        self.onboarding = Onboarding(app, collector)
        self.demo = Hackathon(app, collector, enabled=hackathon)
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
                          "/onboarding.js": ("onboarding.js", "text/javascript; charset=utf-8"),
                          "/hackathon.js": ("hackathon.js", "text/javascript; charset=utf-8"),
                          "/style.css": ("style.css", "text/css; charset=utf-8")}
                if route in assets:
                    filename, kind = assets[route]
                    return self.send(200, (static / filename).read_bytes(), kind)
                if route == "/healthz":
                    return self.send(200, {"status": "ok", "environment": app.environment})
                if route == "/api/demo/config":
                    return self.send(200, {"enabled": api.demo.enabled})
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
                if route == "/api/onboarding":
                    return self.send(200, api.onboarding.snapshot())
                if route == "/api/demo/status" and api.demo.enabled:
                    return self.send(200, api.demo.status())
                if route == "/api/demo/export" and api.demo.enabled:
                    if identity["role"] != "policy_admin":
                        return self.send(403, {"error": "demo presenter required"})
                    return self.send(200, api.demo.export(identity))
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
                # Explicit loopback hackathon mode only. Never available in normal mode.
                if urlsplit(self.path).path == "/api/demo/login":
                    if not api.demo.enabled:
                        return self.send(404, {"error": "hackathon mode disabled"})
                    presenter = next((key for key, value in api.identities.items() if value["role"] == "policy_admin"), None)
                    if not presenter:
                        return self.send(503, {"error": "demo presenter unavailable"})
                    return self.send(200, {"token": presenter, "mode": "fictional hackathon presenter"})
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
                    "/api/tender/compare": {"procurement"},
                    "/api/onboarding/start": {"policy_admin"},
                    "/api/onboarding/sample": {"policy_admin"},
                    "/api/onboarding/configure": {"policy_admin"},
                    "/api/onboarding/probe": {"operator", "security_lead"},
                    "/api/onboarding/sign": {"iam_lead", "infrastructure_lead", "operator", "security_lead",
                                             "risk_lead", "ai_owner", "maintenance_lead", "change_lead", "collaboration_lead"},
                    "/api/onboarding/accept": {"iam_lead", "infrastructure_lead", "operator", "security_lead",
                                               "risk_lead", "ai_owner", "maintenance_lead", "change_lead", "collaboration_lead"},
                    "/api/onboarding/shadow": {"risk_lead"}}
                if api.demo.enabled:
                    permissions.update({"/api/demo/" + action: {"policy_admin"} for action in
                                        ["prepare", "step", "refresh", "incident", "draft", "tender", "assistant", "recover", "offboard", "restart"]})
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
        if route.startswith("/api/demo/"):
            action = route.rsplit("/", 1)[-1]
            if action == "prepare":
                return self.demo.prepare(identity, body.get("name", "Aster Cloud"), body.get("region", "India"))
            if action == "step":
                return self.demo.step(identity, body["step"])
            if action == "draft":
                return self.demo.draft(identity, body["case_id"])
            if action == "assistant":
                return self.demo.assistant(identity, body["question"])
            if action in {"refresh", "incident", "tender", "recover", "offboard", "restart"}:
                return getattr(self.demo, action)(identity)
        if route == "/api/onboarding/start":
            return self.onboarding.start(identity)
        if route == "/api/onboarding/sample":
            return self.onboarding.configure(identity, sample=True)
        if route == "/api/onboarding/configure":
            return self.onboarding.configure(identity, section=body["section"], value=body["value"], revision=body["revision"])
        if route == "/api/onboarding/probe":
            return self.onboarding.probe(identity)
        if route == "/api/onboarding/sign":
            return self.onboarding.sign(identity, body["gate"], body["revision"])
        if route == "/api/onboarding/accept":
            return self.onboarding.accept(identity, body["request_id"])
        if route == "/api/onboarding/shadow":
            return self.onboarding.shadow(identity)
        if route == "/api/scenario":
            if app.environment != "simulation":
                raise Rejected("scenario control is simulation-only")
            return self.collector.request("POST", "/lab/scenario", {"scenario": body["scenario"]})
        if route == "/api/collect":
            setup = app.store.read("onboarding", "setup")
            if setup:
                gates, _ = self.onboarding._gates(setup)
                if not self.onboarding._identity_signed(setup) or next(g for g in gates if g["id"] == "inventory")["status"] != "passed":
                    raise Rejected("sign identity and reconcile/sign inventory before monitoring; use the onboarding probe first")
                probe = setup.get("probe") or {}
                if not probe.get("read_only") or not 0 <= app.clock() - probe.get("at", 0) <= 300:
                    raise Rejected("fresh read-only onboarding probe required before monitoring")
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
                if setup and reading["asset_id"] not in {d["serial"] for d in setup["config"]["inventory"]["devices"]}:
                    raise Rejected("undeclared asset quarantined from monitoring")
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
            result.update(id=new_id("tender"), environment="simulation", reviewed_by=identity["actor"], created_at=app.clock())
            with app.store.transaction() as tx:
                tx.put("tenders", result)
                tx.event("tender_compared", result, app.clock())
            return result
        raise Rejected("unknown action")

    def start(self):
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        return self

    def close(self):
        if self.thread:
            self.server.shutdown()
            self.thread.join(timeout=3)
        self.server.server_close()

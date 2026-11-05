"""Integrated hackathon journey. All enterprise adapters and people are fictional.

Only the bundled loopback Redfish lab is contacted. No external identity provider,
vault, messaging service, supplier or production hardware is called.
"""
import secrets
import threading

from .errors import Rejected
from .onboarding import Onboarding, GATES, CONNECTORS, DAY, ALLOWED_SCOPES
from .service import require, new_id
from .store import digest
from .tender import compare_bids

STEPS = list(GATES) + ["shadow"]
NAMES = {"iam_lead": "Asha · IAM", "infrastructure_lead": "Kabir · Infrastructure",
         "operator": "Ravi · Operations", "security_lead": "Meera · Security",
         "risk_lead": "Neha · Risk", "ai_owner": "Arjun · AI Platform",
         "maintenance_lead": "Isha · Maintenance", "change_lead": "Dev · Change",
         "collaboration_lead": "Sara · Collaboration"}
PROVIDERS = {"identity": "Demo Entra directory", "vault": "Demo customer vault",
             "telemetry": "Local Redfish simulator", "inventory": "Demo NetBox CMDB",
             "cmms": "Demo ServiceNow maintenance", "calendar": "Demo change calendar",
             "messaging": "Demo Teams channel", "procurement": "Demo supplier directory",
             "entitlement": "Demo OEM entitlement"}


def persona(role):
    return {"role": role, "actor": "fictional:" + role}


class DemoEnterprise:
    """Stateful fake enrollment and gateway contracts with real rejection paths."""
    def __init__(self, app):
        self.app, self.store = app, app.store

    def enrollment(self, site):
        token = secrets.token_urlsafe(24)
        with self.store.transaction() as tx:
            tx.put("demo_enrollments", {"id": digest(token), "site": site,
                   "expires_at": self.app.clock() + DAY, "consumed": False})
        return token

    def enroll(self, site, token):
        with self.store.transaction() as tx:
            record = require(tx.get("demo_enrollments", digest(token)), "unknown enrollment token")
            require(record["site"] == site, "enrollment is bound to another site")
            require(not record["consumed"] and record["expires_at"] > self.app.clock(), "enrollment token expired or already used")
            record["consumed"] = True
            tx.put("demo_enrollments", record)
            certificate = {"id": new_id("demo-cert"), "site": site, "status": "active",
                           "generation": 1, "expires_at": self.app.clock() + 30 * DAY,
                           "transport": "simulated outbound mTLS", "source": "synthetic"}
            tx.put("demo_certificates", certificate)
            tx.event("demo_collector_enrolled", certificate, self.app.clock())
        return certificate

    def rotate(self, certificate_id):
        with self.store.transaction() as tx:
            old = require(tx.get("demo_certificates", certificate_id), "collector certificate missing")
            require(old["status"] == "active", "certificate is not active")
            old["status"] = "rotated"
            tx.put("demo_certificates", old)
            new = {**old, "id": new_id("demo-cert"), "status": "active", "generation": old["generation"] + 1,
                   "expires_at": self.app.clock() + 30 * DAY}
            tx.put("demo_certificates", new)
            tx.event("demo_certificate_rotated", {"old": old["id"], "new": new["id"], "source": "synthetic"}, self.app.clock())
        return new

    def vault_read(self, reference):
        require(reference.startswith("vault://overwatch/lab/") and "write" not in reference and "broker" not in reference,
                "demo collector cannot access broker credentials")
        return {"reference": reference, "lease": "synthetic-read-only-lease", "access": "read-only", "source": "synthetic"}

    def gateway_token(self, scopes, audience="overwatch-demo", ttl=300):
        require(bool(scopes) and set(scopes) <= ALLOWED_SCOPES, "gateway permits read/propose scopes only")
        token = secrets.token_urlsafe(24)
        with self.store.transaction() as tx:
            tx.put("demo_gateway_tokens", {"id": digest(token), "scopes": scopes, "audience": audience,
                   "expires_at": self.app.clock() + ttl, "revoked": False, "subject": "fictional:ai-analyst"})
        return token

    def authorize(self, token, scope, audience="overwatch-demo"):
        record = require(self.store.read("demo_gateway_tokens", digest(token)), "unknown gateway token")
        require(not record["revoked"] and record["expires_at"] > self.app.clock(), "gateway token expired or revoked")
        require(record["audience"] == audience and scope in record["scopes"], "gateway audience or scope rejected")
        return record["subject"]


class Hackathon:
    def __init__(self, app, collector, *, enabled=False):
        require(not enabled or app.environment == "simulation", "hackathon mode requires simulation")
        self.app, self.store, self.collector, self.enabled = app, app.store, collector, enabled
        self.onboarding = Onboarding(app, collector)
        self.enterprise = DemoEnterprise(app)
        self.lock = threading.RLock()

    def allowed(self, identity):
        require(self.enabled and self.app.environment == "simulation", "hackathon mode is disabled")
        require(identity["role"] == "policy_admin", "demo presenter required")
        require(self.store.verify_ledger(), "ledger integrity check failed")

    def setup(self):
        return require(self.store.read("onboarding", "setup"), "start the demo first")

    def artifact(self, name, data):
        record = {"id": name, "at": self.app.clock(), "source": "synthetic", "data": data,
                  "revision": self.setup()["revision"]}
        with self.store.transaction() as tx:
            tx.put("demo_artifacts", record)
            tx.event("hackathon_evidence", {"artifact": name, "hash": digest(record), "source": "synthetic"}, self.app.clock())
        return record

    def prepare(self, identity, name="Aster Cloud", region="India"):
        self.allowed(identity)
        require(isinstance(name, str) and 1 <= len(name.strip()) <= 100, "enter an organization name (1–100 characters)")
        require(region in {"India", "EU", "US"}, "select India, EU or US")
        with self.lock:
            existing = self.store.read("onboarding", "setup")
            if existing:
                require(existing.get("hackathon"), "use a separate --data directory for the hackathon journey")
                return self.status()
            self.onboarding.start(identity)
            self.onboarding.configure(identity, sample=True)
            with self.store.transaction() as tx:
                state = tx.get("onboarding", "setup")
                state.update(hackathon=True, demo_completed={}, demo_offboarded=False)
                config = state["config"]
                config["tenant"].update(name=name.strip(), region=region, model_endpoint="Demo assistant (local templates)")
                city, zone = {"India": ("Delhi", "Asia/Kolkata"), "EU": ("Frankfurt", "Europe/Berlin"), "US": ("Virginia", "America/New_York")}[region]
                config["inventory"]["sites"][0].update(name=city + " demo datacenter", region=region, timezone=zone, criticality=1)
                config["policy"]["change_windows"] = ["Sunday 01:00–03:00 " + zone]
                config["telemetry"]["collectors"] = [{"id": "lab-edge-primary", "site": "LAB"}, {"id": "lab-edge-standby", "site": "LAB"}]
                config["inventory"]["devices"] = [{**config["inventory"]["devices"][0], "id": "node-" + letter,
                      "serial": f"LAB-SERVER-{i+1:03d}", "rack": f"LAB/H1/R1/RK{i+1}",
                      "services": [["object-storage", "customer-api", "analytics"][i]]} for i, letter in enumerate("abc")]
                config["telemetry"]["coverage"] = [{"serial": d["serial"], "continuous_days": 7} for d in config["inventory"]["devices"]]
                config["ai"] = {"enabled": True, "clients": ["Demo enterprise assistant", "Demo in-house agent"],
                                "pilot_users": 8, "audit_days": 7, "security_review": True,
                                "manifest_ref": "fixture://gateway/manifest-v1", "scopes": ["cases:read", "assets:read", "plans:propose", "requests:create"],
                                "gateway_url": "http://127.0.0.1:8090",
                                "oauth": {"pkce": True, "audience": "overwatch-demo",
                                          "metadata_url": "http://127.0.0.1:8090/.well-known/oauth-authorization-server"}}
                for category in ["procurement", "entitlement"]:
                    config["connectors"].append({"category": category, "name": PROVIDERS[category], "owner_role": "infrastructure_lead",
                           "mode": "read-only", "credential_ref": "vault://overwatch/lab/" + category,
                           "contract_test": True, "evidence_ref": "fixture://connector-test/" + category})
                tx.put("onboarding", state)
                tx.event("hackathon_prepared", {"organization": name.strip(), "initiated_by": identity["actor"], "source": "synthetic"}, self.app.clock())
            return self.status()

    def _finish_step(self, step, evidence, identity):
        self.artifact(step, evidence)
        with self.store.transaction() as tx:
            state = tx.get("onboarding", "setup")
            state["demo_completed"][step] = {"at": self.app.clock(), "initiated_by": identity["actor"], "source": "synthetic"}
            tx.put("onboarding", state)
        if step != "shadow":
            current = self.setup()
            try:
                for role in GATES[step][1]:
                    if any(s["role"] == role for s in current["signatures"].get(step, [])):
                        continue
                    self.onboarding.sign(persona(role), step, current["revision"])
                    with self.store.transaction() as tx:
                        tx.event("demo_owner_review", {"phase": step, "owner": NAMES[role], "actor": persona(role)["actor"],
                                 "initiated_by": identity["actor"], "step_up": "simulated", "source": "synthetic"}, self.app.clock())
            except Exception:
                with self.store.transaction() as tx:
                    failed = tx.get("onboarding", "setup")
                    failed["demo_completed"].pop(step, None)
                    failed["signatures"].pop(step, None)
                    tx.put("onboarding", failed)
                    tx.event("demo_phase_failed", {"step": step, "source": "synthetic"}, self.app.clock())
                raise

    def step(self, identity, step):
        self.allowed(identity)
        require(step in STEPS, "unknown demo phase")
        with self.lock:
            state = self.setup()
            require(state.get("hackathon") and not state.get("demo_offboarded"), "demo is not active")
            index = STEPS.index(step)
            require(all(k in state["demo_completed"] for k in STEPS[:index]), "complete the previous onboarding phases first")
            if step in state["demo_completed"]:
                return self.status()
            if index >= 2:
                self.refresh(identity)
            config = state["config"]
            if step == "identity":
                users = [{"id": "demo-user-" + str(i+1), "name": name, "role": role, "active": True,
                          "group": "demo-" + role} for i, (role, name) in enumerate(NAMES.items())]
                users.append({"id": "demo-leaver", "name": "Former employee", "role": "operator", "active": False, "group": None})
                evidence = {"provider": "Demo Entra ID", "users": users, "sso": "simulated sign-in passed",
                            "scim": "nine users provisioned; leaver deactivated", "mfa": "simulated step-up passed",
                            "approval_authority": config["identity"]["authority"], "role_mappings": config["identity"]["groups"]}
            elif step == "inventory":
                probe = self.onboarding.probe(persona("operator"))
                require(probe["read_only"], probe["error"] or "Redfish probe failed")
                rows = self.onboarding._checks(self.setup())[1]
                require(len(rows) == 3 and all(r["status"] == "matched" for r in rows), "all three declared lab nodes must reconcile")
                evidence = {"import_source": "Demo NetBox CMDB", "matched": len(rows), "assets": rows,
                            "probe_source": "local Redfish HTTP", "authority": {"serial": "Redfish", "rack": "demo DCIM", "owner/services": "demo CMDB"}}
            elif step == "telemetry":
                certificates = []
                for collector in config["telemetry"]["collectors"]:
                    token = self.enterprise.enrollment(collector["site"])
                    cert = self.enterprise.enroll(collector["site"], token)
                    try:
                        self.enterprise.enroll(collector["site"], token)
                        raise RuntimeError("one-time enrollment failed")
                    except Rejected:
                        pass
                    certificates.append({**self.enterprise.rotate(cert["id"]), "collector": collector["id"]})
                leases = [self.enterprise.vault_read(d["credential_ref"]) for d in config["inventory"]["devices"]]
                try:
                    self.enterprise.vault_read("vault://overwatch/lab/broker-write")
                    raise RuntimeError("vault isolation failed")
                except Rejected:
                    pass
                history = [{"day": day, "expected": 3, "reporting": 3, "excess_privilege_accounts": 0} for day in range(1, 8)]
                evidence = {"collectors": certificates, "vault_leases": leases, "coverage_history": history,
                            "coverage_percent": 100, "simulated_days": 7, "enrollment_reuse_rejected": True,
                            "broker_secret_denied": True, "note": "Certificate and coverage data are simulated; no real TLS certificates are issued."}
            elif step == "connectors":
                payloads = {
                    "identity": {"users": self.store.read("demo_artifacts", "identity")["data"]["users"]},
                    "vault": {"references": [d["credential_ref"] for d in config["inventory"]["devices"]]},
                    "telemetry": {"readings": self.collector.collect()},
                    "inventory": {"assets": config["inventory"]["devices"]},
                    "cmms": {"work_orders": [{"id": "DEMO-WO-100", "status": "closed", "summary": "Quarterly airflow inspection"}]},
                    "calendar": {"windows": config["policy"]["change_windows"]},
                    "messaging": {"channels": [{"id": "demo-infra-ops", "delivery": "local outbox only"}]},
                    "procurement": {"suppliers": ["Demo Regional Service", "Demo OEM Partner", "Demo Unverified Lead"]},
                    "entitlement": {"contracts": [{"serial": d["serial"], "status": "active", "coverage": "next business day"} for d in config["inventory"]["devices"]]},
                }
                certificates = []
                for category, payload in payloads.items():
                    require(len(payload) == 1 and isinstance(next(iter(payload.values())), list) and next(iter(payload.values())), "connector returned an invalid dataset")
                    self.artifact("connector:" + category, payload)
                    certificates.append({"category": category, "provider": PROVIDERS[category], "status": "passed",
                            "records": len(next(iter(payload.values()))), "mode": "read-only", "payload_hash": digest(payload), "source": "synthetic"})
                evidence = {"certifications": certificates, "required_passed": 7, "tender_integrations_passed": 2}
            elif step == "policy":
                replay = [{"day": i+1, "case": f"HIST-{i+1:03d}", "action": ["diagnostics", "drain", "firmware", "purchase"][i % 4],
                           "previous_tier": "T0", "proposed_tier": "T0", "decision": "recommendation only",
                           "write_permitted": False} for i in range(30)]
                evidence = {"version": config["policy"]["version"], "replay": replay, "replayed_days": 30,
                            "writes_allowed": sum(r["write_permitted"] for r in replay), "independent_reviewers": [NAMES["risk_lead"], NAMES["security_lead"]],
                            "tiers": config["policy"]["tiers"], "replay_source": "synthetic historical cases"}
            elif step == "ai":
                token = self.enterprise.gateway_token(config["ai"]["scopes"])
                self.enterprise.authorize(token, "cases:read")
                for scope, audience in [("actions:execute", "overwatch-demo"), ("cases:read", "wrong-audience")]:
                    try:
                        self.enterprise.authorize(token, scope, audience)
                        raise RuntimeError("gateway boundary failed")
                    except Rejected:
                        pass
                evidence = {"clients": config["ai"]["clients"], "scopes": config["ai"]["scopes"],
                            "pilot_users": 8, "audit_days": 7, "calls_reviewed": 56,
                            "denied_checks": ["wrong audience", "execute scope"], "manifest_hash": digest(sorted(ALLOWED_SCOPES)),
                            "notice": "Local mock gateway contract; hosted OAuth integrations and language models are simulated."}
            else:
                self.refresh(identity)
                self.onboarding.shadow(persona("risk_lead"))
                outcomes = [{"day": i+1, "recommended": i % 7 != 0, "confirmed_need": i % 7 not in {0, 1},
                             "unsafe": False, "source": "synthetic"} for i in range(56)]
                recommended = sum(o["recommended"] for o in outcomes)
                confirmed = sum(o["recommended"] and o["confirmed_need"] for o in outcomes)
                evidence = {"simulated_days": 56, "simulated_weeks": 8, "outcomes": outcomes,
                            "recommendations": recommended, "confirmed": confirmed, "precision_percent": round(100 * confirmed / recommended, 1),
                            "unsafe_recommendations": 0, "autonomy": "T0", "promotion": "not granted", "source": "synthetic evaluation dataset"}
            self._finish_step(step, evidence, identity)
            if step == "shadow":
                self.incident(identity)
            return self.status()

    def refresh(self, identity):
        self.allowed(identity)
        with self.lock:
            state = self.setup()
            require(not state.get("demo_offboarded"), "demo access revoked")
            require("inventory" in state.get("demo_completed", {}), "reconcile inventory first")
            result = self.onboarding.probe(persona("operator"), preserve_unchanged=True)
            require(result["read_only"], result["error"] or "Redfish probe failed")
            # A failed probe invalidates reviews. Healthy recovery uses explicit demo owners.
            state = self.setup()
            for gate in ["inventory", "telemetry"]:
                if gate in state["demo_completed"]:
                    for role in GATES[gate][1]:
                        if not any(s["role"] == role for s in state["signatures"].get(gate, [])):
                            self.onboarding.sign(persona(role), gate, state["revision"])
            if "shadow" in state["demo_completed"] and state["stage"] != "SHADOW":
                self.onboarding.shadow(persona("risk_lead"))
            return result

    def _operational(self, identity):
        self.allowed(identity)
        require("shadow" in self.setup().get("demo_completed", {}), "complete onboarding before operating the demo fleet")
        self.refresh(identity)
        require(self.onboarding.snapshot()["shadow_status"] == "ACTIVE", "shadow readiness is held")

    def incident(self, identity):
        with self.lock:
            self._operational(identity)
            self.collector.request("POST", "/lab/scenario", {"scenario": "degraded"})
            case_ids = []
            for reading in self.collector.collect():
                case = self.app.observe(**reading)
                if not case:
                    continue
                case_ids.append(case["id"])
                if not case["plan_id"]:
                    plan = self.app.plan(case["id"])
                    self.app.verify(plan["id"])
            self.artifact("incident", {"case_ids": case_ids, "trigger": "simulated thermal degradation", "source": "local Redfish simulator"})
            return {"case_ids": case_ids}

    def draft(self, identity, case_id):
        with self.lock:
            self._operational(identity)
            case = self.app.get_case(case_id)
            require(case["state"] != "CLOSED", "case already closed")
            require(self.store.read("demo_artifacts", "connector:cmms"), "CMMS connector has not passed its test")
            record = {"id": "DEMO-WO-" + case_id[-8:], "case_id": case_id, "asset": case["asset_id"],
                      "status": "DRAFT_AWAITING_HUMAN_REVIEW", "summary": "Inspect cooling, airflow and fan health",
                      "created_by": identity["actor"], "source": "synthetic", "external_delivery": False}
            with self.store.transaction() as tx:
                tx.put("demo_work_orders", record)
                tx.put("demo_notices", {"id": "NOTICE-" + case_id[-8:], "case_id": case_id, "status": "DRAFT",
                       "channel": "demo-infra-ops", "text": "Maintenance review requested for " + case["asset_id"], "external_delivery": False, "source": "synthetic"})
                tx.event("demo_work_order_drafted", record, self.app.clock())
            return record

    def tender(self, identity):
        with self.lock:
            self._operational(identity)
            require(all(self.store.read("demo_artifacts", "connector:" + k) for k in ["procurement", "entitlement"]), "tender connectors must be certified first")
            bids = [{"supplier": supplier, "unit_price": price, "quantity": 3, "freight": "500.00", "tax": "900.00",
                     "currency": "INR", "scope_id": "demo-fan-service-three-nodes", "qualified": i < 2,
                     "screened": True, "availability_confirmed": i < 2, "evidence_id": "fixture:quote:" + str(i),
                     "expires_at": self.app.clock() + 7 * DAY} for i, (supplier, price) in enumerate([
                         ("Demo Regional Service", "4000.00"), ("Demo OEM Partner", "5500.00"), ("Demo Unverified Lead", "2000.00")])]
            result = compare_bids(bids, now=self.app.clock(), currency="INR")
            result.update(id="demo-tender", environment="simulation", reviewed_by="fictional:procurement", source="synthetic", created_at=self.app.clock())
            with self.store.transaction() as tx:
                tx.put("tenders", result)
                tx.event("demo_tender_compared", {"id": result["id"], "recommendation": result["recommendation"], "source": "synthetic"}, self.app.clock())
            return result

    def assistant(self, identity, question):
        with self.lock:
            self._operational(identity)
            require(question in {"fleet", "evidence", "draft"}, "choose a supported demo assistant request")
            token = self.enterprise.gateway_token(["cases:read", "requests:create"])
            self.enterprise.authorize(token, "requests:create" if question == "draft" else "cases:read")
            cases, receipts = self.store.list("cases"), self.store.list("receipts")
            open_cases = [c for c in cases if c["state"] != "CLOSED"]
            result = {"mode": "deterministic demo assistant", "source": "local records", "question": question,
                      "receipt_ids": [r["id"] for r in receipts[-3:]]}
            if question == "fleet":
                result["answer"] = f"{len(open_cases)} open thermal cases across the three-node lab. Onboarding is in T0 shadow mode. Investigate cooling and airflow; hardware failure is not established."
            elif question == "evidence":
                result["answer"] = "Latest device readings: " + "; ".join(f"{r['body']['asset_id']}: {r['body']['temperature']} °C" for r in receipts[-3:]) + ". Receipts retain source, timestamps and hashes."
            else:
                require(open_cases, "no open case needs a request")
                draft = {"id": new_id("demo-proposal"), "case_id": open_cases[0]["id"], "status": "DRAFT",
                         "summary": "Inspect cooling and collect additional evidence", "source": "demo assistant", "approved": False}
                with self.store.transaction() as tx:
                    tx.put("demo_proposals", draft)
                result["answer"] = f"Created proposal {draft['id']} for human review. No approval or execution was performed."
                result["proposal"] = draft
            with self.store.transaction() as tx:
                tx.event("demo_gateway_call", {"question": question, "tool": "create_request_draft" if question == "draft" else "get_cases",
                         "user": "fictional:ai-analyst", "source": "synthetic client"}, self.app.clock())
            return result

    def recover(self, identity):
        with self.lock:
            self._operational(identity)
            require(self.app.soak_seconds == 0, "hackathon recovery requires --soak-seconds 0; three fresh samples are still mandatory")
            with self.store.transaction() as tx:
                affected = [c for c in tx.all("cases") if c["state"] not in {"CLOSED", "POST_CHECK"}]
                require(affected, "no open recommendations to resolve")
                for case in affected:
                    case.update(state="POST_CHECK", healthy_since=None, healthy_samples=0, last_observed_at=self.app.clock())
                    tx.put("cases", case)
                tx.event("demo_external_technician_repair", {"cases": [c["id"] for c in affected], "actor": "fictional:technician",
                         "source": "synthetic", "broker_executed": False}, self.app.clock())
            self.collector.request("POST", "/lab/scenario", {"scenario": "recovered"})
            for _ in range(3):
                for reading in self.collector.collect():
                    self.app.observe(**reading)
            closed = [c for c in self.store.list("cases") if c["state"] == "CLOSED"]
            with self.store.transaction() as tx:
                for order in tx.all("demo_work_orders"):
                    if order["case_id"] in {c["id"] for c in closed}:
                        order["status"] = "SIMULATED_TECHNICIAN_COMPLETED"
                        tx.put("demo_work_orders", order)
            return {"closed_cases": len(closed), "samples_per_case": 3, "broker_executed": False}

    def export(self, identity):
        self.allowed(identity)
        return {"format": "overwatch-hackathon-evidence-v1", "source": "simulation", "exported_at": self.app.clock(),
                "onboarding": self.onboarding.snapshot(), "cases": self.store.list("cases"), "receipts": self.store.list("receipts"),
                "artifacts": self.store.list("demo_artifacts"), "work_orders": self.store.list("demo_work_orders"),
                "tenders": self.store.list("tenders"), "events": self.store.events(), "ledger_valid": self.store.verify_ledger()}

    def offboard(self, identity):
        self.allowed(identity)
        with self.lock:
            bundle = self.export(identity)
            with self.store.transaction() as tx:
                state = require(tx.get("onboarding", "setup"), "start onboarding first")
                state.update(demo_offboarded=True, stage="OFFBOARDED", signatures={}, shadow_started_at=None)
                tx.put("onboarding", state)
                for kind in ["demo_gateway_tokens", "demo_certificates"]:
                    for item in tx.all(kind):
                        item.update(revoked=True, status="revoked")
                        tx.put(kind, item)
                for item in tx.all("demo_enrollments"):
                    item.update(consumed=True, expires_at=self.app.clock())
                    tx.put("demo_enrollments", item)
                tx.event("demo_offboarding", {"export_hash": digest(bundle), "access": "revoked", "deletion": "simulated; evidence retained for presentation", "source": "synthetic"}, self.app.clock())
            self.artifact("offboarding", {"export_hash": digest(bundle), "collectors": "revoked", "gateway_clients": "revoked",
                          "customer_accounts": "simulated removal confirmed", "data_deletion": "simulated confirmation; local audit retained"})
            return {"status": "OFFBOARDED", "export": bundle}

    def restart(self, identity):
        """Replay onboarding while retaining cases, receipts and the audit chain."""
        self.allowed(identity)
        with self.lock, self.store.transaction() as tx:
            state = require(tx.get("onboarding", "setup"), "prepare the demo first")
            require(state.get("hackathon"), "only a hackathon workspace can restart")
            self.onboarding._invalidate(state)
            state.update(probe=None, demo_offboarded=False)
            tx.put("onboarding", state)
            for request in tx.all("requests"):
                request["accepted_by"] = []
                tx.put("requests", request)
            for kind in ["demo_gateway_tokens", "demo_certificates"]:
                for item in tx.all(kind):
                    item.update(revoked=True, status="revoked")
                    tx.put(kind, item)
            for item in tx.all("demo_enrollments"):
                item.update(consumed=True, expires_at=self.app.clock())
                tx.put("demo_enrollments", item)
            tx.event("demo_onboarding_restarted", {"revision": state["revision"], "initiated_by": identity["actor"],
                     "retained": "cases, evidence and audit history", "source": "synthetic"}, self.app.clock())
        return self.status()

    def status(self):
        state = self.store.read("onboarding", "setup")
        if not state or not state.get("hackathon"):
            return {"enabled": self.enabled, "prepared": False, "steps": []}
        completed = state.get("demo_completed", {})
        phase_names = {**{k: v[0] for k, v in GATES.items()}, "shadow": "7 · Shadow mode and operations"}
        ready = self.onboarding.snapshot()["ready_for_simulated_shadow"]
        return {"enabled": self.enabled, "prepared": True, "organization": state["config"]["tenant"]["name"],
                "steps": [{"id": k, "name": phase_names[k], "complete": k in completed} for k in STEPS],
                "completed": len(completed), "offboarded": state.get("demo_offboarded", False),
                "demo_ready": len(completed) == 7 and ready and not state.get("demo_offboarded"), "production_ready": False,
                "artifacts": [a for a in self.store.list("demo_artifacts") if a.get("revision") == state["revision"]], "work_orders": self.store.list("demo_work_orders"),
                "notices": self.store.list("demo_notices"), "proposals": self.store.list("demo_proposals"),
                "people": NAMES, "next_step": next((k for k in STEPS if k not in completed), None)}

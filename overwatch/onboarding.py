"""Local onboarding rehearsal, never enterprise certification.

All imported assertions are simulation evidence. Actual lab probes are separate,
short-lived receipts. Production gates remain blocked until real adapters exist.
"""
import copy
import ipaddress
import math
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from .errors import Rejected
from .service import require
from .store import digest

DAY = 86400
ROLES = ["iam_lead", "infrastructure_lead", "security_lead", "risk_lead",
         "ai_owner", "maintenance_lead", "change_lead", "collaboration_lead"]
CONNECTORS = {"identity": "iam_lead", "vault": "security_lead", "telemetry": "operator",
              "inventory": "infrastructure_lead", "cmms": "maintenance_lead",
              "calendar": "change_lead", "messaging": "collaboration_lead"}
GATES = {
    "identity": ("1 · Tenant and identity", ["iam_lead"], 3,
                 "Tenant settings, six role mappings, 100% approval-authority coverage, SSO, SCIM and step-up test."),
    "inventory": ("2 · Sites and hardware", ["infrastructure_lead"], 10,
                  "At least 95% matched declared assets; zero untriaged discovered assets."),
    "telemetry": ("3 · Collectors and credentials", ["operator", "security_lead"], 21,
                  "At least 90% coverage for seven continuous days; no excess rights; collector per site (two for tier 1)."),
    "connectors": ("4 · Systems of record", list(CONNECTORS.values()), 21,
                   "All seven required connectors certified by their owners; read-only access."),
    "policy": ("5 · Policy release", ["risk_lead", "security_lead"], 24,
               "Two independent reviewers, 30-day replay, required policy settings, every action at T0."),
    "ai": ("6 · Enterprise AI clients (optional)", ["ai_owner", "security_lead"], 28,
           "Optional pilot of 5–10 users, one week of audit review; read/propose scopes only."),
}
PRODUCT_ROLES = ["technician", "change_lead", "procurement", "manager", "auditor", "administrator"]
ALLOWED_SCOPES = {"cases:read", "assets:read", "evidence:read", "tenders:read",
                  "requests:create", "plans:propose", "notices:draft", "approvals:view"}
PRODUCTION_BLOCKERS = [
    "SSO/OIDC/SAML, SCIM and fresh MFA are not connected; local tokens are rehearsal identities.",
    "Customer vault access, encryption key custody and residency enforcement are not implemented.",
    "Edge enrollment, mutual TLS, certificate rotation and redundant collectors are not deployed.",
    "Only the local Redfish fixture is probed; seven days of real fleet coverage are not established.",
    "DCIM/CMDB, CMMS, change calendar and messaging adapters are not certified.",
    "Independent policy service, live historical replay and enterprise approval authority are not integrated.",
    "External OAuth MCP gateway, client pilot and gateway audit review are not implemented.",
    "Eight to twelve weeks of real shadow outcomes and promotion evidence remain outstanding.",
]


def sample_config():
    """Explicitly requested test inputs, not observations about a customer."""
    return {
        "tenant": {"name": "Overwatch lab", "deployment": "self-hosted", "region": "India",
                   "key_management": "customer-managed", "model_endpoint": "disabled",
                   "retention_days": 365},
        "identity": {"sso_test": True, "scim_test": True, "step_up_test": True,
                     "groups": {role: "lab-" + role for role in PRODUCT_ROLES},
                     "authority": [{"person": "lab-operator", "role": "technician", "site": "LAB",
                                    "action": "diagnostics", "limit": "0.00", "reviewed": True}]},
        "inventory": {"sites": [{"id": "LAB", "name": "Local Redfish lab", "timezone": "Asia/Kolkata",
                                  "region": "India", "segments": ["127.0.0.1/32"], "criticality": 2,
                                  "calendar": "fixture://change-calendar", "facilities": "lab-facilities",
                                  "maintainers": ["lab-maintainer"]}],
                      "devices": [{"id": "node-a", "serial": "LAB-SERVER-001", "site": "LAB",
                                   "rack": "LAB/H1/R1/RK1", "address": "127.0.0.1",
                                   "credential_ref": "vault://overwatch/lab/read-only",
                                   "owner_group": "lab-operators", "services": ["lab-storage"]}]},
        "telemetry": {"collectors": [{"id": "lab-edge", "site": "LAB"}],
                      "coverage": [{"serial": "LAB-SERVER-001", "continuous_days": 7}],
                      "health_interval_seconds": 60, "sensor_interval_seconds": 30,
                      "inventory_interval_seconds": DAY, "max_sessions": 2},
        "connectors": [{"category": key, "name": "Lab " + key, "owner_role": owner,
                        "credential_ref": "vault://overwatch/lab/" + key,
                        "mode": "read-only", "contract_test": True,
                        "evidence_ref": "fixture://connector-test/" + key} for key, owner in CONNECTORS.items()],
        "policy": {"version": "lab-v1", "tiers": {"diagnostics": "T0", "drain": "T0", "firmware": "T0", "purchase": "T0"},
                   "currency": "INR", "threshold_a": "100000.00", "threshold_b": "2500000.00",
                   "change_windows": ["Sunday 01:00–03:00 Asia/Kolkata"], "freezes": [],
                   "evidence_age_days": {"quote": 30, "entitlement": 7},
                   "supplier_eligibility": ["approved vendor", "verified certification", "sanctions reviewed"],
                   "price_tolerance_percent": 2, "people_search": False, "replay_days": 30,
                   "replay_evidence": "fixture://policy-replay/30-days", "step_up_test": True},
        "ai": {"enabled": False, "clients": [], "pilot_users": 0, "audit_days": 0,
               "security_review": False, "manifest_ref": "", "scopes": ["cases:read"]},
    }


def clean_input(value):
    """Reject secret-bearing fields instead of persisting them to state or audit."""
    if isinstance(value, dict):
        for key, item in value.items():
            require(not any(word in key.lower() for word in ["password", "secret", "token", "authorization", "private_key"]),
                    "credentials must remain in the customer vault; use credential_ref")
            if key == "credential_ref":
                require(isinstance(item, str) and item.startswith("vault://") and len(item) > 8
                        and not any(c in item for c in "@?#\n"), "use a vault:// reference without credentials")
            clean_input(item)
    elif isinstance(value, list):
        for item in value:
            clean_input(item)
    elif isinstance(value, float):
        require(math.isfinite(value), "nonfinite value rejected")


class Onboarding:
    def __init__(self, app, collector=None):
        self.app, self.store, self.collector = app, app.store, collector

    def _mutation(self, identity, operation):
        require(self.app.environment == "simulation", "onboarding rehearsal is simulation-only")
        require(self.store.verify_ledger(), "ledger integrity check failed")
        with self.store.transaction() as tx:
            state = require(tx.get("onboarding", "setup"), "start onboarding first")
            result = operation(tx, state)
            tx.put("onboarding", state)
            tx.event("onboarding_updated", {"actor": identity["actor"], "role": identity["role"],
                     "revision": state["revision"], "state_hash": digest(state)}, self.app.clock())
            return result

    def start(self, identity):
        require(identity["role"] == "policy_admin", "administrator required")
        require(self.app.environment == "simulation", "onboarding rehearsal is simulation-only")
        require(self.store.verify_ledger(), "ledger integrity check failed")
        with self.store.transaction() as tx:
            existing = tx.get("onboarding", "setup")
            if existing:
                return {"started": True}
            require(not any(j["status"] in {"running", "unknown"} for j in tx.all("operations")),
                    "reconcile in-flight actions before starting T0 onboarding")
            state = dict(id="setup", revision=1, created_at=self.app.clock(), author=identity["actor"],
                         stage="SETUP", config={}, signatures={}, probe=None, fixture=False, shadow_started_at=None)
            tx.put("onboarding", state)
            for number, (gate, (title, owners, days, criterion)) in enumerate(GATES.items(), 1):
                tx.put("requests", dict(id=f"REQ-{number:04d}", gate=gate, title=title, owner_roles=owners,
                                        due_at=self.app.clock() + days * DAY, criterion=criterion, accepted_by=[]))
            tx.put("requests", dict(id="REQ-0007", gate="shadow", title="7 · Shadow mode",
                   owner_roles=["risk_lead"], due_at=self.app.clock() + 28 * DAY,
                   criterion="All required gates signed; remain at T0 during 8–12 weeks of outcome evaluation.", accepted_by=[]))
            for permit in tx.all("permits"):
                if permit["status"] == "issued":
                    permit["status"] = "revoked"
                    tx.put("permits", permit)
            tx.event("onboarding_started", {"actor": identity["actor"], "environment": "simulation", "tier": "T0"}, self.app.clock())
        return {"started": True}

    def _invalidate(self, state):
        state["revision"] += 1
        state["signatures"] = {}
        state["stage"] = "SETUP"
        state["shadow_started_at"] = None
        if state.get("hackathon"):
            state["demo_completed"] = {}

    def configure(self, identity, section=None, value=None, sample=False, revision=None):
        require(identity["role"] == "policy_admin", "administrator required")
        if not sample:
            require(section in sample_config(), "unknown configuration section")
            require(isinstance(value, type(sample_config()[section])), "invalid section structure")
            clean_input(value)
            require(len(str(value)) <= 100000, "configuration is too large")

        def update(tx, state):
            if revision is not None:
                require(revision == state["revision"], "configuration changed; reload before saving")
            self._invalidate(state)
            state["author"] = identity["actor"]
            if sample:
                state["config"] = sample_config()
            else:
                state["config"][section] = copy.deepcopy(value)
            state["fixture"] = True
            # Changing declarations must require a new, scoped probe.
            if sample or section in {"identity", "tenant", "inventory"}:
                state["probe"] = None
            tx.event("onboarding_configuration", {"section": "sample_fixture" if sample else section,
                     "actor": identity["actor"], "revision": state["revision"], "evidence_kind": "simulation"}, self.app.clock())
            return {"saved": True, "revision": state["revision"]}
        return self._mutation(identity, update)

    def _checks(self, state):
        config = state["config"]
        checks = {key: [] for key in GATES}
        inventory_rows = []

        def check(gate, condition, message):
            if not condition:
                checks[gate].append(message)

        # Malformed/incomplete drafts remain editable, but can never pass a gate.
        for gate in GATES:
            try:
                if gate == "identity":
                    tenant, identity = config.get("tenant", {}), config.get("identity", {})
                    check(gate, bool(tenant.get("name")) and bool(tenant.get("region")), "Set tenant name and data region.")
                    check(gate, tenant.get("deployment") in {"hybrid", "self-hosted", "air-gapped"}, "Choose a deployment model.")
                    check(gate, tenant.get("key_management") in {"customer-managed", "vendor-managed"}
                          and tenant.get("model_endpoint") and type(tenant.get("retention_days")) is int
                          and tenant["retention_days"] > 0, "Set encryption-key ownership, model endpoint and retention.")
                    check(gate, all(identity.get(k) is True for k in ["sso_test", "scim_test", "step_up_test"]),
                          "Record simulated SSO, SCIM and step-up test results.")
                    groups = identity.get("groups", {})
                    check(gate, all(isinstance(groups.get(r), str) and groups[r].strip() for r in PRODUCT_ROLES), "Map all six product roles to IdP groups.")
                    authority = identity.get("authority", [])
                    from .policy import money
                    check(gate, bool(authority) and all(a["role"] in groups and a["role"] in PRODUCT_ROLES
                          and a["person"] and a["site"] and a["action"] and money(a["limit"]) >= 0
                          and a["reviewed"] is True for a in authority), "100% of imported approval-authority rows must resolve and be reviewed.")
                elif gate == "inventory":
                    inventory = config.get("inventory", {})
                    sites, devices = inventory.get("sites", []), inventory.get("devices", [])
                    check(gate, bool(sites) and bool(devices), "Declare sites before devices.")
                    site_map = {s["id"]: s for s in sites}
                    check(gate, len(site_map) == len(sites), "Site identifiers must be unique.")
                    check(gate, len({d["id"] for d in devices}) == len(devices)
                          and len({d["serial"] for d in devices}) == len(devices), "Device identifiers and serials must be unique.")
                    for site in sites:
                        ZoneInfo(site["timezone"])
                        check(gate, bool(site["segments"]) and site["criticality"] in {1, 2, 3}
                              and site["region"] == config.get("tenant", {}).get("region")
                              and site["calendar"] and site["facilities"] and site["maintainers"], "Complete each site record and match tenant residency.")
                        for network in site["segments"]:
                            ipaddress.ip_network(network)
                    observed = {d["serial"]: d for d in (state.get("probe") or {}).get("devices", [])}
                    claimed = set()
                    for device in devices:
                        site = site_map[device["site"]]
                        check(gate, device["rack"].startswith(site["id"] + "/") and device["owner_group"]
                              and device["services"] and device["credential_ref"].startswith("vault://"), "Supply rack hierarchy, owner, services and vault reference.")
                        check(gate, any(ipaddress.ip_address(device["address"]) in ipaddress.ip_network(n) for n in site["segments"]),
                              "Management addresses must fall inside declared site segments.")
                        seen = observed.get(device["serial"])
                        basis = "serial"
                        if not seen:
                            matches = [v for v in observed.values() if v["address"] == device["address"]]
                            seen = matches[0] if len(matches) == 1 else None
                            basis = "address"
                        if not seen:
                            matches = [v for v in observed.values() if v.get("rack") == device["rack"]]
                            seen = matches[0] if len(matches) == 1 else None
                            basis = "rack"
                        status = "declared_not_seen" if not seen else "matched" if seen["address"] == device["address"] and seen["serial"] == device["serial"] and seen["serial"] not in claimed else "conflict"
                        if seen:
                            claimed.add(seen["serial"])
                        inventory_rows.append({**device, "status": status, "match_basis": basis if seen else None,
                                               "device_serial": seen["serial"] if seen else None})
                    inventory_rows += [{**v, "status": "seen_not_declared"} for k, v in observed.items() if k not in claimed]
                    matched = sum(r["status"] == "matched" for r in inventory_rows)
                    check(gate, bool(devices) and matched / len(devices) >= .95, "At least 95% of declared devices must match the Redfish probe.")
                    check(gate, all(r["status"] != "seen_not_declared" for r in inventory_rows), "Claim or remove untriaged discovered devices from the lab declaration.")
                elif gate == "telemetry":
                    telemetry = config.get("telemetry", {})
                    sites = config.get("inventory", {}).get("sites", [])
                    collectors = telemetry.get("collectors", [])
                    check(gate, bool(sites) and all(sum(c["site"] == s["id"] for c in collectors) >= (2 if s["criticality"] == 1 else 1)
                          for s in sites) and len({c["id"] for c in collectors}) == len(collectors), "Enroll one simulated collector per site, two for tier 1; IDs must be unique.")
                    probe = state.get("probe") or {}
                    check(gate, probe.get("read_only") is True and 0 <= self.app.clock() - probe.get("at", 0) <= 300,
                          "Run a fresh Redfish read-only privilege and telemetry probe (valid for five minutes).")
                    matched = {r["serial"] for r in inventory_rows if r["status"] == "matched"}
                    coverage = {c["serial"] for c in telemetry.get("coverage", []) if type(c.get("continuous_days")) in {int, float} and 7 <= c["continuous_days"] <= 365}
                    check(gate, bool(matched) and len(matched & coverage) / len(matched) >= .9,
                          "At least 90% of matched devices need seven continuous days of coverage (fixture history only here).")
                    check(gate, 0 < telemetry.get("max_sessions", 0) <= 2 and telemetry.get("sensor_interval_seconds", 0) >= 30
                          and telemetry.get("health_interval_seconds", 0) >= 60 and telemetry.get("inventory_interval_seconds", 0) >= DAY,
                          "Set bounded polling: sensors ≥30s, health ≥60s, inventory ≥1 day, at most two BMC sessions.")
                elif gate == "connectors":
                    connectors = config.get("connectors", [])
                    check(gate, len({c["category"] for c in connectors}) == len(connectors), "Connector categories must be unique.")
                    for category, owner in CONNECTORS.items():
                        connector = next((c for c in connectors if c["category"] == category), {})
                        check(gate, connector.get("owner_role") == owner and connector.get("mode") == "read-only"
                              and connector.get("contract_test") is True and connector.get("evidence_ref", "").startswith("fixture://")
                              and connector.get("credential_ref", "").startswith("vault://"), f"{category}: read-only fixture contract test, owner and vault reference required.")
                elif gate == "policy":
                    from .policy import money
                    policy = config.get("policy", {})
                    tiers = policy.get("tiers", {})
                    check(gate, {"diagnostics", "drain", "firmware", "purchase"} <= tiers.keys()
                          and all(v == "T0" for v in tiers.values()), "Every action class must remain T0; promotion is unavailable.")
                    check(gate, policy.get("version") and policy.get("currency") and money(policy.get("threshold_a", "-1")) > 0
                          and money(policy.get("threshold_b", "-1")) >= money(policy.get("threshold_a", "-1")), "Set policy version, currency and ordered spend thresholds.")
                    check(gate, policy.get("change_windows") and isinstance(policy.get("freezes"), list)
                          and policy.get("supplier_eligibility") and all(v > 0 for v in policy.get("evidence_age_days", {}).values())
                          and {"quote", "entitlement"} <= policy.get("evidence_age_days", {}).keys()
                          and 0 <= policy.get("price_tolerance_percent", -1) <= 2 and policy.get("people_search") is False,
                          "Set windows, freezes, evidence ages and supplier rules; tolerance ≤2%, people search off.")
                    check(gate, policy.get("replay_days", 0) >= 30 and policy.get("replay_evidence", "").startswith("fixture://")
                          and policy.get("step_up_test") is True, "Record a simulated 30-day replay and step-up test; real replay and MFA remain external work.")
                elif gate == "ai":
                    ai = config.get("ai", {})
                    if ai.get("enabled") is not False:
                        check(gate, ai.get("enabled") is True and ai.get("clients") and 5 <= ai.get("pilot_users", 0) <= 10
                              and ai.get("audit_days", 0) >= 7 and ai.get("security_review") is True and ai.get("manifest_ref", "").startswith("fixture://"),
                              "Record client registration, 5–10 pilot users, a week of audit review and a pinned manifest fixture.")
                        check(gate, bool(ai.get("scopes")) and set(ai.get("scopes", [])) <= ALLOWED_SCOPES,
                              "Only read/propose scopes are allowed; no approval, execution or policy scope.")
            except (KeyError, TypeError, ValueError, AttributeError, ZoneInfoNotFoundError, Rejected):
                checks[gate].append("Configuration is missing a required field or contains an invalid value.")
        if state.get("hackathon"):
            for gate in GATES:
                if gate not in state.get("demo_completed", {}):
                    checks[gate].append("Run this phase in the guided hackathon demo to generate its evidence.")
                if state.get("demo_offboarded"):
                    checks[gate].append("Demo tenant has been offboarded; access is revoked.")
        return checks, inventory_rows

    def _gates(self, state):
        checks, rows = self._checks(state)
        gates = []
        for gate, (title, roles, _, criterion) in GATES.items():
            optional = gate == "ai" and state["config"].get("ai", {}).get("enabled") is False
            signatures = state["signatures"].get(gate, [])
            missing = [] if optional else [r for r in roles if not any(s["role"] == r for s in signatures)]
            blockers = checks[gate]
            if gate != "identity" and not self._identity_signed(state):
                blockers = blockers + ["Identity gate must be signed first."]
            status = "not_required" if optional else "blocked" if blockers else "at_risk" if missing else "passed"
            gates.append(dict(id=gate, title=title, criterion=criterion, status=status, blockers=blockers,
                              missing_signers=missing, signatures=signatures, evidence_kind="simulation"))
        return gates, rows

    def _identity_signed(self, state):
        return not self._checks(state)[0]["identity"] and bool(state["signatures"].get("identity"))

    def sign(self, identity, gate, revision):
        require(gate in GATES, "unknown gate")
        require(identity["role"] in GATES[gate][1], "only the named gate owner can sign")

        def update(tx, state):
            require(revision == state["revision"], "configuration changed; refresh before signing")
            evaluated = next(g for g in self._gates(state)[0] if g["id"] == gate)
            require(not evaluated["blockers"], "; ".join(evaluated["blockers"]))
            require(evaluated["status"] != "not_required", "optional AI gate is disabled")
            signatures = state["signatures"].setdefault(gate, [])
            require(not any(s["actor"] == identity["actor"] for s in signatures), "an independent owner is required; this actor already signed")
            if gate == "policy":
                require(identity["actor"] != state["author"], "policy author cannot release their own configuration")
            signatures.append(dict(actor=identity["actor"], role=identity["role"], at=self.app.clock(),
                                   revision=revision, config_hash=digest(state["config"]), evidence_kind="simulation"))
            for request in tx.all("requests"):
                if request["gate"] == gate and identity["actor"] not in request["accepted_by"]:
                    request["accepted_by"].append(identity["actor"])
                    tx.put("requests", request)
            tx.event("onboarding_gate_signed", {"gate": gate, **signatures[-1]}, self.app.clock())
            return {"signed": gate}
        return self._mutation(identity, update)

    def accept(self, identity, request_id):
        def update(tx, state):
            request = require(tx.get("requests", request_id), "request not found")
            require(identity["role"] in request["owner_roles"], "request belongs to another team")
            if identity["actor"] not in request["accepted_by"]:
                request["accepted_by"].append(identity["actor"])
                tx.put("requests", request)
            tx.event("onboarding_request_accepted", {"request_id": request_id, "actor": identity["actor"]}, self.app.clock())
            return {"accepted": request_id}
        return self._mutation(identity, update)

    def probe(self, identity, *, preserve_unchanged=False):
        require(identity["role"] in {"operator", "security_lead"}, "collector operator or security lead required")
        state = require(self.store.read("onboarding", "setup"), "start onboarding first")
        require(self._identity_signed(state), "sign identity before collecting any device data")
        require(self.app.environment == "simulation" and self.collector, "local simulation collector required")
        address = urlsplit(self.collector.base_url).hostname
        require(address == "127.0.0.1", "onboarding probe supports only the configured loopback lab")
        declared = state["config"].get("inventory", {})
        require(any(d.get("address") == address for d in declared.get("devices", [])), "declare the lab device first")
        try:
            require(any(any(ipaddress.ip_address(address) in ipaddress.ip_network(n) for n in site["segments"])
                        for site in declared.get("sites", [])), "lab address is outside declared segments")
        except (TypeError, KeyError):
            raise Rejected("complete the site management segment declaration before probing") from None
        # These fixed paths are the bundled lab adapter, not arbitrary OEM account discovery.
        error, devices, readings, read_only = None, [], [], False
        try:
            require(self.collector.get("/lab/meta").get("fixture") == "watchermcp-lab-v1", "unsupported lab identity")
            account = self.collector.get("/redfish/v1/AccountService/Accounts/overwatch-reader")
            require(account.get("RoleId") == "ReadOnly", "device account must have the ReadOnly role")
            role = self.collector.get("/redfish/v1/AccountService/Roles/ReadOnly")
            privileges = set(role.get("AssignedPrivileges", []))
            require("Login" in privileges and privileges <= {"Login", "ConfigureSelf"}
                    and not role.get("OemPrivileges"), "device account has excessive or unknown rights")
            read_only = True
            readings = self.collector.collect()
            require(bool(readings), "no devices returned")
            for r in readings:
                require(r["timestamp_basis"] == "device" and 0 <= self.app.clock() - r["observed_at"] <= 300,
                        "fresh device timestamps are required")
                devices.append({"serial": r["asset_id"], "address": address})
        except (RuntimeError, ValueError) as exc:
            error = str(exc)
            read_only = False

        def update(tx, current):
            require(current["revision"] == state["revision"], "configuration changed during probe; retry")
            previous = current.get("probe") or {}
            unchanged = (preserve_unchanged and read_only and previous.get("read_only")
                         and previous.get("devices") == devices and previous.get("source") == self.collector.base_url)
            current["probe"] = dict(at=self.app.clock(), read_only=read_only, devices=devices, error=error,
                                    source=self.collector.base_url, evidence_kind="lab_probe")
            if not unchanged:
                for gate in ["inventory", "telemetry"]:
                    current["signatures"].pop(gate, None)
                if current["stage"] == "SHADOW":
                    current["stage"] = "SETUP"
                    current["shadow_started_at"] = None
            tx.event("onboarding_lab_probe", current["probe"], self.app.clock())
            return current["probe"]
        return self._mutation(identity, update)

    def shadow(self, identity):
        require(identity["role"] == "risk_lead", "risk lead must start shadow mode")
        def update(tx, state):
            require(all(g["status"] in {"passed", "not_required"} for g in self._gates(state)[0]), "all required gates must pass and be signed")
            if state["stage"] != "SHADOW":
                state["stage"], state["shadow_started_at"] = "SHADOW", self.app.clock()
                request = tx.get("requests", "REQ-0007")
                if identity["actor"] not in request["accepted_by"]:
                    request["accepted_by"].append(identity["actor"])
                    tx.put("requests", request)
                tx.event("onboarding_shadow_started", {"actor": identity["actor"], "tier": "T0", "environment": "simulation"}, self.app.clock())
            return {"stage": "SHADOW", "tier": "T0"}
        return self._mutation(identity, update)

    def snapshot(self):
        with self.store.transaction() as tx:
            state = tx.get("onboarding", "setup")
            requests = tx.all("requests")
        base = dict(production_ready=False, production_blockers=PRODUCTION_BLOCKERS, environment=self.app.environment,
                    templates=sample_config(), owner_roles=ROLES, action_tier="T0")
        if not state:
            return {**base, "started": False, "gates": [], "requests": []}
        gates, rows = self._gates(state)
        ready = all(g["status"] in {"passed", "not_required"} for g in gates)
        for request in requests:
            gate = next((g for g in gates if g["id"] == request["gate"]), None)
            request["status"] = gate["status"] if gate else "passed" if state["stage"] == "SHADOW" and ready else "blocked"
            request["blockers"] = gate["blockers"] if gate else ([] if ready else ["Finish required gates before shadow mode."])
        days = max(0, (self.app.clock() - state["shadow_started_at"]) / DAY) if state["shadow_started_at"] else 0
        return {**base, **state, "started": True, "gates": gates, "requests": requests,
                "inventory_rows": rows, "ready_for_simulated_shadow": ready, "shadow_days": round(days, 2),
                "shadow_status": "ACTIVE" if state["stage"] == "SHADOW" and ready else "HELD" if state["stage"] == "SHADOW" else "NOT_STARTED",
                "promotion_available": False, "shadow_target_weeks": "8–12"}

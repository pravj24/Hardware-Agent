import json
import tempfile
import time
import unittest
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from overwatch.api import APIServer
from overwatch.errors import Rejected
from overwatch.onboarding import GATES, Onboarding, ROLES, sample_config
from overwatch.redfish import RedfishClient, SimulationAdapter
from overwatch.service import Overwatch
from overwatch.simulator import LabServer


def identity(role, actor=None):
    return {"role": role, "actor": actor or role + "-local"}


class OnboardingTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.now = time.time()
        self.app = Overwatch(Path(self.temp.name) / "db.sqlite", b"x" * 32, clock=lambda: self.now)
        self.onboarding = Onboarding(self.app)
        self.admin = identity("policy_admin")
        self.onboarding.start(self.admin)

    def sample(self):
        self.onboarding.configure(self.admin, sample=True)

    def sign(self, gate, role, actor=None):
        self.onboarding.sign(identity(role, actor), gate, self.onboarding.snapshot()["revision"])

    def fake_probe(self):
        # Unit fixture only. HTTP tests below obtain this evidence through the lab.
        with self.app.store.transaction() as tx:
            setup = tx.get("onboarding", "setup")
            setup["probe"] = {"at": self.now, "read_only": True, "devices": [{"serial": "LAB-SERVER-001", "address": "127.0.0.1"}]}
            tx.put("onboarding", setup)

    def ready(self):
        self.sample()
        self.sign("identity", "iam_lead")
        self.fake_probe()
        for gate, (_, roles, _, _) in GATES.items():
            if gate in {"identity", "ai"}:
                continue
            for role in roles:
                self.sign(gate, role)

    def gate(self, name):
        return next(g for g in self.onboarding.snapshot()["gates"] if g["id"] == name)

    def test_empty_checklist_has_seven_requests_and_blocks_shadow(self):
        snapshot = self.onboarding.snapshot()
        self.assertEqual(len(snapshot["requests"]), 7)
        self.assertFalse(snapshot["production_ready"])
        with self.assertRaises(Rejected):
            self.onboarding.shadow(identity("risk_lead"))

    def test_sample_is_not_a_signoff_or_production_evidence(self):
        self.sample()
        self.assertEqual(self.gate("identity")["status"], "at_risk")
        self.assertEqual(self.gate("inventory")["status"], "blocked")
        self.assertEqual(self.gate("ai")["status"], "not_required")
        self.assertFalse(self.onboarding.snapshot()["production_ready"])

    def test_wrong_roles_cannot_configure_sign_or_start_shadow(self):
        self.sample()
        for call in [lambda: self.onboarding.configure(identity("viewer"), sample=True),
                     lambda: self.onboarding.sign(self.admin, "identity", 2),
                     lambda: self.onboarding.shadow(self.admin)]:
            with self.assertRaises(Rejected):
                call()

    def test_no_probe_before_identity_signoff(self):
        self.sample()
        with self.assertRaisesRegex(Rejected, "sign identity"):
            self.onboarding.probe(identity("operator"))

    def test_incomplete_mapping_and_unreviewed_authority_block_identity(self):
        self.sample()
        data = sample_config()["identity"]
        data["groups"].pop("auditor")
        data["authority"][0]["reviewed"] = False
        self.onboarding.configure(self.admin, "identity", data)
        self.assertEqual(self.gate("identity")["status"], "blocked")
        with self.assertRaises(Rejected):
            self.sign("identity", "iam_lead")

    def test_secret_fields_are_rejected_without_storing_them(self):
        with self.assertRaisesRegex(Rejected, "vault"):
            self.onboarding.configure(self.admin, "identity", {"password": "do-not-save"})
        self.assertNotIn("do-not-save", json.dumps(self.app.store.events()))
        with self.assertRaises(Rejected):
            self.onboarding.configure(self.admin, "connectors", [{"credential_ref": "https://user:password@host"}])

    def test_config_edit_revokes_signatures_and_stale_revision_is_rejected(self):
        self.sample()
        self.sign("identity", "iam_lead")
        old = self.onboarding.snapshot()["revision"]
        self.onboarding.configure(self.admin, "tenant", sample_config()["tenant"])
        self.assertEqual(self.onboarding.snapshot()["signatures"], {})
        with self.assertRaisesRegex(Rejected, "changed"):
            self.onboarding.sign(identity("iam_lead"), "identity", old)

    def test_inventory_threshold_quarantines_unknown_and_conflicting_devices(self):
        self.sample()
        self.sign("identity", "iam_lead")
        self.fake_probe()
        with self.app.store.transaction() as tx:
            setup = tx.get("onboarding", "setup")
            setup["probe"]["devices"][0]["address"] = "127.0.0.2"
            setup["probe"]["devices"].append({"serial": "UNKNOWN", "address": "127.0.0.1"})
            tx.put("onboarding", setup)
        rows = self.onboarding.snapshot()["inventory_rows"]
        self.assertEqual({r["status"] for r in rows}, {"conflict", "seen_not_declared"})
        self.assertEqual(self.gate("inventory")["status"], "blocked")

    def test_coverage_seven_days_and_collector_redundancy_are_enforced(self):
        self.sample()
        data = sample_config()["telemetry"]
        data["coverage"][0]["continuous_days"] = 6.9
        self.onboarding.configure(self.admin, "telemetry", data)
        self.sign("identity", "iam_lead")
        self.fake_probe()
        self.assertTrue(any("seven" in b for b in self.gate("telemetry")["blockers"]))
        inv = sample_config()["inventory"]
        inv["sites"][0]["criticality"] = 1
        self.onboarding.configure(self.admin, "inventory", inv)
        self.assertTrue(any("two for tier 1" in b for b in self.gate("telemetry")["blockers"]))

    def test_stale_probe_holds_shadow_and_prevents_new_signatures(self):
        self.ready()
        self.onboarding.shadow(identity("risk_lead"))
        self.now += 301
        snapshot = self.onboarding.snapshot()
        self.assertEqual(snapshot["shadow_status"], "HELD")
        self.assertFalse(snapshot["ready_for_simulated_shadow"])

    def test_missing_connector_and_write_mode_block_certification(self):
        self.sample()
        connectors = sample_config()["connectors"][:-1]
        connectors[0]["mode"] = "write"
        self.onboarding.configure(self.admin, "connectors", connectors)
        blockers = self.gate("connectors")["blockers"]
        self.assertTrue(any("identity:" in b for b in blockers))
        self.assertTrue(any("messaging:" in b for b in blockers))

    def test_policy_requires_two_people_and_cannot_be_self_released(self):
        self.sample()
        self.sign("identity", "iam_lead")
        self.sign("policy", "risk_lead", "reviewer")
        with self.assertRaisesRegex(Rejected, "independent"):
            self.sign("policy", "security_lead", "reviewer")
        with self.assertRaisesRegex(Rejected, "author"):
            self.sign("policy", "security_lead", self.admin["actor"])
        self.assertEqual(self.gate("policy")["status"], "at_risk")

    def test_tier_promotion_and_unsafe_ai_scopes_are_blocked(self):
        self.sample()
        policy = sample_config()["policy"]
        policy["tiers"]["diagnostics"] = "T2"
        self.onboarding.configure(self.admin, "policy", policy)
        self.assertEqual(self.gate("policy")["status"], "blocked")
        ai = {"enabled": True, "clients": ["lab-client"], "pilot_users": 5, "audit_days": 7,
              "security_review": True, "manifest_ref": "fixture://manifest/v1", "scopes": ["actions:execute"]}
        self.onboarding.configure(self.admin, "ai", ai)
        self.assertTrue(any("scope" in b for b in self.gate("ai")["blockers"]))

    def test_rehearsal_can_reach_shadow_but_never_production_or_execution(self):
        self.ready()
        self.onboarding.shadow(identity("risk_lead"))
        snapshot = self.onboarding.snapshot()
        self.assertEqual(snapshot["stage"], "SHADOW")
        self.assertEqual(snapshot["shadow_status"], "ACTIVE")
        self.assertFalse(snapshot["production_ready"])
        self.assertFalse(snapshot["promotion_available"])
        with self.assertRaisesRegex(Rejected, "T0"):
            self.app.issue_permit("any-plan")
        self.assertTrue(self.app.store.verify_ledger())
        reopened = Onboarding(Overwatch(self.app.store.path, b"x" * 32, clock=lambda: self.now))
        self.assertEqual(reopened.snapshot()["stage"], "SHADOW")

    def test_malformed_drafts_block_instead_of_crashing_status(self):
        self.sample()
        for section, value in [("inventory", {"devices": [None]}), ("identity", {"authority": [None]}),
                               ("telemetry", {"collectors": [None]}), ("connectors", [None])]:
            self.onboarding.configure(self.admin, section, value)
            self.assertFalse(self.onboarding.snapshot()["ready_for_simulated_shadow"])

    def test_request_owner_acceptance_is_persistent_and_role_bound(self):
        with self.assertRaises(Rejected):
            self.onboarding.accept(identity("viewer"), "REQ-0001")
        self.onboarding.accept(identity("iam_lead"), "REQ-0001")
        self.assertEqual(self.onboarding.snapshot()["requests"][0]["accepted_by"], ["iam_lead-local"])

    def test_signing_accepts_the_owner_request(self):
        self.sample()
        self.sign("identity", "iam_lead")
        self.assertEqual(self.onboarding.snapshot()["requests"][0]["accepted_by"], ["iam_lead-local"])

    def test_stale_configuration_edit_does_not_replace_current_settings(self):
        self.sample()
        before = self.onboarding.snapshot()["config"]
        with self.assertRaisesRegex(Rejected, "reload"):
            self.onboarding.configure(self.admin, "tenant", {}, revision=1)
        self.assertEqual(self.onboarding.snapshot()["config"], before)

    def test_enabled_ai_requires_both_pilot_owner_and_security_review(self):
        self.sample()
        ai = {"enabled": True, "clients": ["lab-client"], "pilot_users": 5, "audit_days": 7,
              "security_review": True, "manifest_ref": "fixture://manifest/v1", "scopes": ["cases:read", "plans:propose"]}
        self.onboarding.configure(self.admin, "ai", ai)
        self.sign("identity", "iam_lead")
        self.sign("ai", "ai_owner")
        self.assertEqual(self.gate("ai")["status"], "at_risk")
        self.sign("ai", "security_lead")
        self.assertEqual(self.gate("ai")["status"], "passed")
        self.assertFalse(self.onboarding.snapshot()["production_ready"])

    def test_fixture_onboarding_cannot_be_started_in_production(self):
        production = Overwatch(Path(self.temp.name) / "prod.sqlite", b"x" * 32, environment="production")
        with self.assertRaisesRegex(Rejected, "simulation-only"):
            Onboarding(production).start(self.admin)

    def test_direct_observation_cannot_bypass_identity_gate(self):
        with self.assertRaisesRegex(Rejected, "sign-offs"):
            self.app.observe("LAB-SERVER-001", 90, self.now, source="http://127.0.0.1:8001", event_id="unauthorized")
        self.assertFalse(self.app.store.list("receipts"))


class OnboardingHTTPTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.lab = LabServer(port=0).start()
        self.addCleanup(self.lab.close)
        self.client = RedfishClient(self.lab.url)
        self.app = Overwatch(Path(self.temp.name) / "db.sqlite", b"x" * 32, adapter=SimulationAdapter(self.client))
        roles = ROLES + ["operator", "viewer", "policy_admin"]
        self.server = APIServer(self.app, self.client, port=0, identities={r: identity(r) for r in roles}).start()
        self.addCleanup(self.server.close)

    def request(self, path, body=None, role="policy_admin"):
        req = Request(self.server.url + path, data=json.dumps(body).encode() if body is not None else None,
                      headers={"Authorization": "Bearer " + role, "Content-Type": "application/json"})
        try:
            with urlopen(req, timeout=5) as response:
                return response.status, json.load(response)
        except HTTPError as exc:
            with exc:
                return exc.code, json.load(exc)

    def initialize(self):
        self.assertEqual(self.request("/api/onboarding/start", {})[0], 200)
        self.assertEqual(self.request("/api/onboarding/sample", {})[0], 200)

    def sign(self, gate, role):
        revision = self.request("/api/onboarding")[1]["revision"]
        result = self.request("/api/onboarding/sign", {"gate": gate, "revision": revision}, role)
        self.assertEqual(result[0], 200, result)

    def test_complete_http_onboarding_probes_readonly_and_retains_t0(self):
        self.initialize()
        self.assertEqual(self.request("/api/collect", {}, "operator")[0], 422)
        self.sign("identity", "iam_lead")
        status, probe = self.request("/api/onboarding/probe", {}, "operator")
        self.assertEqual(status, 200)
        self.assertTrue(probe["read_only"])
        self.assertEqual(probe["devices"][0]["serial"], "LAB-SERVER-001")
        for gate, (_, roles, _, _) in GATES.items():
            if gate not in {"identity", "ai"}:
                for role in roles:
                    self.sign(gate, role)
        self.assertEqual(self.request("/api/onboarding/shadow", {}, "risk_lead")[0], 200)
        self.assertEqual(self.request("/api/collect", {}, "operator")[0], 200)
        self.assertEqual(self.request("/api/permit", {"plan_id": "anything"}, "operator")[0], 422)
        self.assertFalse(any(r[0] == "POST" for r in self.lab.requests))
        self.assertFalse(self.request("/api/onboarding")[1]["production_ready"])

    def test_role_spoofing_and_unauthenticated_status_are_rejected(self):
        self.assertEqual(self.request("/api/onboarding", role="wrong")[0], 401)
        self.assertEqual(self.request("/api/onboarding/start", {"role": "policy_admin"}, "viewer")[0], 403)
        self.initialize()
        self.assertEqual(self.request("/api/onboarding/sign", {"gate": "identity", "revision": 2, "role": "iam_lead"}, "operator")[0], 422)

    def test_privileged_account_stale_sensor_and_outage_block_probe(self):
        self.initialize()
        self.sign("identity", "iam_lead")
        resources = self.lab.resources
        def privileged():
            value = resources()
            value["/redfish/v1/AccountService/Roles/ReadOnly"]["AssignedPrivileges"].append("ConfigureManager")
            return value
        self.lab.resources = privileged
        self.assertFalse(self.request("/api/onboarding/probe", {}, "operator")[1]["read_only"])
        self.lab.resources = resources
        for scenario in ["stale", "dark"]:
            self.lab.set_scenario(scenario)
            result = self.request("/api/onboarding/probe", {}, "operator")[1]
            self.assertFalse(result["read_only"])
            self.assertTrue(result["error"])

    def test_start_revokes_existing_permits_and_execute_cannot_bypass_t0(self):
        self.lab.set_scenario("degraded")
        case = self.app.observe(**self.client.collect()[0])
        plan = self.app.plan(case["id"])
        self.app.verify(plan["id"])
        self.app.approve(plan["id"], "operator-local", "operator")
        permit = self.app.issue_permit(plan["id"])
        self.initialize()
        self.assertEqual(self.app.store.read("permits", permit["id"])["status"], "revoked")
        with self.assertRaisesRegex(Rejected, "T0"):
            self.app.execute(permit)
        self.assertFalse(any(r[0] == "POST" for r in self.lab.requests))

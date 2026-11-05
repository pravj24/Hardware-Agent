import json
import tempfile
import time
import unittest
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from overwatch.api import APIServer
from overwatch.errors import Rejected
from overwatch.hackathon import DemoEnterprise, Hackathon, STEPS
from overwatch.redfish import RedfishClient, SimulationAdapter
from overwatch.service import Overwatch
from overwatch.simulator import LabServer

PRESENTER = {"actor": "demo-presenter", "role": "policy_admin"}


class DemoContractsTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.now = time.time()
        self.app = Overwatch(Path(self.temp.name) / "db", b"x"*32, clock=lambda: self.now)
        self.enterprise = DemoEnterprise(self.app)

    def test_enrollment_rejects_wrong_site_reuse_and_expiry(self):
        token = self.enterprise.enrollment("LAB")
        with self.assertRaisesRegex(Rejected, "another site"):
            self.enterprise.enroll("OTHER", token)
        certificate = self.enterprise.enroll("LAB", token)
        with self.assertRaisesRegex(Rejected, "already used"):
            self.enterprise.enroll("LAB", token)
        rotated = self.enterprise.rotate(certificate["id"])
        self.assertEqual(rotated["generation"], 2)
        self.assertEqual(self.app.store.read("demo_certificates", certificate["id"])["status"], "rotated")
        token = self.enterprise.enrollment("LAB")
        self.now += 86401
        with self.assertRaisesRegex(Rejected, "expired"):
            self.enterprise.enroll("LAB", token)

    def test_gateway_enforces_scope_audience_and_expiration(self):
        token = self.enterprise.gateway_token(["cases:read"])
        self.assertEqual(self.enterprise.authorize(token, "cases:read"), "fictional:ai-analyst")
        for scope, audience in [("actions:execute", "overwatch-demo"), ("cases:read", "other")]:
            with self.assertRaises(Rejected):
                self.enterprise.authorize(token, scope, audience)
        with self.assertRaises(Rejected):
            self.enterprise.gateway_token(["actions:execute"])
        self.now += 301
        with self.assertRaisesRegex(Rejected, "expired"):
            self.enterprise.authorize(token, "cases:read")

    def test_vault_collector_cannot_resolve_write_credentials(self):
        self.assertEqual(self.enterprise.vault_read("vault://overwatch/lab/read-only")["access"], "read-only")
        with self.assertRaises(Rejected):
            self.enterprise.vault_read("vault://overwatch/lab/broker-write")

    def test_production_cannot_enable_hackathon(self):
        app = Overwatch(Path(self.temp.name) / "prod", b"x"*32, environment="production")
        with self.assertRaises(Rejected):
            Hackathon(app, None, enabled=True)


class HackathonJourneyTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.lab = LabServer(port=0, token="lab-secret", fleet_size=3).start()
        self.addCleanup(self.lab.close)
        self.client = RedfishClient(self.lab.url, lab_token="lab-secret")
        self.app = Overwatch(Path(self.temp.name) / "db", b"x"*32, adapter=SimulationAdapter(self.client), soak_seconds=0)
        self.server = APIServer(self.app, self.client, port=0, hackathon=True, identities={
            "presenter": PRESENTER, "viewer": {"actor": "auditor", "role": "viewer"}}).start()
        self.addCleanup(self.server.close)
        self.demo = self.server.demo

    def request(self, path, body=None, token="presenter", **headers):
        req = Request(self.server.url + path, data=json.dumps(body).encode() if body is not None else None,
                      headers={"Authorization": "Bearer " + token, "Content-Type": "application/json", **headers})
        try:
            with urlopen(req, timeout=10) as response:
                return response.status, json.load(response)
        except HTTPError as exc:
            with exc:
                return exc.code, json.load(exc)

    def complete(self):
        self.demo.prepare(PRESENTER)
        for step in STEPS:
            self.demo.step(PRESENTER, step)

    def test_seven_phases_connect_to_real_lab_cases_and_remain_t0(self):
        self.complete()
        status = self.demo.status()
        self.assertTrue(status["demo_ready"])
        self.assertEqual(status["completed"], 7)
        self.assertTrue(all(s["complete"] for s in status["steps"]))
        self.assertFalse(status["production_ready"])
        self.assertEqual(len(self.app.store.list("cases")), 3)
        self.assertEqual(len(self.app.store.list("plans")), 3)
        self.assertTrue(all(p["status"] == "VERIFIED" for p in self.app.store.list("plans")))
        self.assertEqual(len(self.demo.onboarding.snapshot()["inventory_rows"]), 3)
        self.assertEqual(self.app.store.read("demo_artifacts", "connectors")["data"]["required_passed"], 7)
        self.assertEqual(self.app.store.read("demo_artifacts", "shadow")["data"]["simulated_weeks"], 8)
        with self.assertRaisesRegex(Rejected, "T0"):
            self.app.issue_permit(self.app.store.list("plans")[0]["id"])
        self.assertFalse(self.app.store.list("operations"))
        self.assertTrue(self.app.store.verify_ledger())

    def test_out_of_order_and_unprepared_steps_rejected(self):
        with self.assertRaises(Rejected):
            self.demo.step(PRESENTER, "identity")
        self.demo.prepare(PRESENTER)
        with self.assertRaisesRegex(Rejected, "previous"):
            self.demo.step(PRESENTER, "policy")
        self.assertTrue(all(g["status"] == "blocked" for g in self.demo.onboarding.snapshot()["gates"]))

    def test_work_orders_bids_assistant_and_verified_recovery(self):
        self.complete()
        case = self.app.store.list("cases")[0]
        order = self.demo.draft(PRESENTER, case["id"])
        self.assertEqual(order["status"], "DRAFT_AWAITING_HUMAN_REVIEW")
        self.assertFalse(order["external_delivery"])
        tender = self.demo.tender(PRESENTER)
        self.assertEqual(tender["ranked"][0]["total"], "13400.00")
        self.assertEqual(len(tender["excluded"]), 1)
        self.assertIn("3 open", self.demo.assistant(PRESENTER, "fleet")["answer"])
        self.assertIn("91", self.demo.assistant(PRESENTER, "evidence")["answer"])
        proposal = self.demo.assistant(PRESENTER, "draft")["proposal"]
        self.assertFalse(proposal["approved"])
        recovered = self.demo.recover(PRESENTER)
        self.assertEqual(recovered["closed_cases"], 3)
        self.assertTrue(all(c["healthy_samples"] == 3 for c in self.app.store.list("cases")))
        self.assertFalse(self.app.store.list("operations"))
        self.assertFalse(any(r[0] == "POST" and "/Actions/" in r[1] for r in self.lab.requests))

    def test_faults_are_not_replaced_with_successful_fake_probe(self):
        self.demo.prepare(PRESENTER)
        self.demo.step(PRESENTER, "identity")
        self.lab.set_scenario("dark")
        with self.assertRaises(Rejected):
            self.demo.step(PRESENTER, "inventory")
        self.assertNotIn("inventory", self.demo.setup()["demo_completed"])
        self.lab.set_scenario("healthy")
        self.demo.step(PRESENTER, "inventory")
        self.assertEqual(self.demo.status()["completed"], 2)

    def test_refresh_keeps_signatures_and_shadow_start_for_unchanged_devices(self):
        self.complete()
        before = self.demo.setup()
        self.demo.refresh(PRESENTER)
        after = self.demo.setup()
        self.assertEqual(before["signatures"], after["signatures"])
        self.assertEqual(before["shadow_started_at"], after["shadow_started_at"])

    def test_export_has_evidence_no_credentials_and_offboard_revokes_access(self):
        self.complete()
        token = self.demo.enterprise.gateway_token(["cases:read"])
        export = self.demo.export(PRESENTER)
        serialized = json.dumps(export)
        self.assertTrue(export["ledger_valid"])
        self.assertNotIn("lab-secret", serialized)
        self.assertNotIn(token, serialized)
        self.assertNotIn("demo_gateway_tokens", serialized)
        result = self.demo.offboard(PRESENTER)
        self.assertEqual(result["status"], "OFFBOARDED")
        self.assertFalse(self.demo.status()["demo_ready"])
        with self.assertRaisesRegex(Rejected, "revoked"):
            self.demo.enterprise.authorize(token, "cases:read")
        with self.assertRaises(Rejected):
            self.demo.incident(PRESENTER)

    def test_http_demo_login_explicit_flag_origin_and_mutation_authorization(self):
        self.assertTrue(self.request("/api/demo/config", token="wrong")[1]["enabled"])
        self.assertEqual(self.request("/api/demo/login", {}, token="wrong")[1]["token"], "presenter")
        self.assertEqual(self.request("/api/demo/login", {}, Origin="https://foreign.invalid")[0], 403)
        self.assertEqual(self.request("/api/demo/prepare", {}, token="viewer")[0], 403)
        self.assertEqual(self.request("/api/demo/prepare", {"name": "Demo company"})[0], 200)
        self.assertEqual(self.request("/api/demo/step", {"step": "identity"})[0], 200)
        self.server.demo.enabled = False
        self.assertEqual(self.request("/api/demo/login", {})[0], 404)
        self.assertEqual(self.request("/api/demo/step", {"step": "inventory"})[0], 404)

    def test_demo_prepare_never_overwrites_an_existing_standard_workspace(self):
        self.demo.onboarding.start(PRESENTER)
        with self.assertRaisesRegex(Rejected, "separate"):
            self.demo.prepare(PRESENTER)

    def test_restart_replays_all_phases_without_deleting_evidence(self):
        self.complete()
        receipt_ids = {r["id"] for r in self.app.store.list("receipts")}
        status = self.demo.restart(PRESENTER)
        self.assertEqual(status["completed"], 0)
        self.assertEqual(status["artifacts"], [])
        self.assertEqual(status["next_step"], "identity")
        self.assertFalse(status["demo_ready"])
        for step in STEPS:
            self.demo.step(PRESENTER, step)
        self.assertTrue(self.demo.status()["demo_ready"])
        self.assertTrue(receipt_ids <= {r["id"] for r in self.app.store.list("receipts")})
        self.assertTrue(self.app.store.verify_ledger())

    def test_failed_signoff_is_not_marked_complete(self):
        self.demo.prepare(PRESENTER)
        with self.app.store.transaction() as tx:
            setup = tx.get("onboarding", "setup")
            setup["config"]["identity"]["groups"].pop("auditor")
            tx.put("onboarding", setup)
        with self.assertRaises(Rejected):
            self.demo.step(PRESENTER, "identity")
        self.assertEqual(self.demo.status()["completed"], 0)
        self.assertFalse(self.demo.setup()["signatures"])

"""Behavioral contracts written before the implementation (unittest discovery)."""
import copy
from contextlib import closing
import json
import tempfile
import threading
import unittest
from pathlib import Path

from overwatch.service import Overwatch, Rejected


class Clock:
    def __init__(self):
        self.now = 1_800_000_000.0

    def __call__(self):
        return self.now


class Device:
    """Independent test double: effects are recorded even if the reply is lost."""
    environment = "simulation"

    def __init__(self):
        self.calls = []
        self.results = {}
        self.lose_reply = False

    def execute(self, plan, key):
        self.calls.append((copy.deepcopy(plan), key))
        self.results[key] = "succeeded"
        if self.lose_reply:
            raise TimeoutError("accepted, response lost")
        return "succeeded"

    def status(self, key):
        return self.results.get(key, "unknown")


class WorkflowTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / "state.db"
        self.clock = Clock()
        self.device = Device()
        self.app = Overwatch(self.path, b"test-only-key-with-at-least-32-bytes", clock=self.clock,
                             adapter=self.device, budget="2000.00", soak_seconds=60)

    def incident(self, asset="node-1"):
        return self.app.observe(asset, 91, self.clock(), source="test-bmc", event_id=asset + str(self.clock()))

    def plan(self, asset="node-1", amount="0.00", action="simulate_repair"):
        case = self.incident(asset)
        plan = self.app.plan(case["id"], amount=amount, action=action)
        self.app.verify(plan["id"])
        return plan

    def permit(self, plan):
        if plan["action"] == "draft_requisition":
            self.app.approve(plan["id"], "buyer", "procurement")
            if float(plan["amount"]) >= 1000:
                self.app.approve(plan["id"], "finance", "finance")
        else:
            self.app.approve(plan["id"], "sre", "operator")
        return self.app.issue_permit(plan["id"])

    def test_healthy_reading_does_not_open_case(self):
        self.assertIsNone(self.app.observe("node-1", 45, self.clock(), source="bmc", event_id="healthy"))
        self.assertEqual(self.app.snapshot()["cases"], [])
        self.assertEqual(self.device.calls, [])

    def test_duplicate_event_does_not_duplicate_case_or_evidence(self):
        first = self.incident()
        second = self.incident()
        self.assertEqual(first["id"], second["id"])
        self.assertEqual(len(self.app.snapshot()["receipts"]), 1)

    def test_active_case_merges_new_observations(self):
        first = self.incident()
        self.clock.now += 10
        second = self.incident()
        self.assertEqual(first["id"], second["id"])
        self.assertEqual(len(second["receipt_ids"]), 2)

    def test_stale_measurement_is_not_freshened_by_retrieval(self):
        case = self.app.observe("node-1", 91, self.clock() - 900, source="bmc", event_id="stale")
        plan = self.app.plan(case["id"])
        with self.assertRaisesRegex(Rejected, "stale"):
            self.app.verify(plan["id"])

    def test_nonfinite_or_future_measurements_rejected(self):
        for value, timestamp in [(float("nan"), self.clock()), (91, self.clock() + 3600)]:
            with self.assertRaises(Rejected):
                self.app.observe("node", value, timestamp, source="bmc", event_id=str(value))

    def test_no_approval_no_permit_no_device_calls(self):
        plan = self.plan()
        with self.assertRaisesRegex(Rejected, "approval"):
            self.app.issue_permit(plan["id"])
        self.assertEqual(self.device.calls, [])

    def test_wrong_role_cannot_approve(self):
        plan = self.plan()
        with self.assertRaises(Rejected):
            self.app.approve(plan["id"], "admin", "policy_admin")

    def test_two_person_spend_rule(self):
        plan = self.plan(amount="1200.00", action="draft_requisition")
        self.app.approve(plan["id"], "same-user", "procurement")
        with self.assertRaises(Rejected):
            self.app.approve(plan["id"], "same-user", "finance")
        with self.assertRaises(Rejected):
            self.app.issue_permit(plan["id"])

    def test_tampered_permit_is_rejected(self):
        permit = self.permit(self.plan())
        permit["payload"]["target"] = "different-node"
        with self.assertRaises(Rejected):
            self.app.execute(permit)
        self.assertEqual(self.device.calls, [])

    def test_expired_permit_is_rejected(self):
        permit = self.permit(self.plan())
        self.clock.now += 121
        with self.assertRaisesRegex(Rejected, "expired"):
            self.app.execute(permit)

    def test_changed_plan_revokes_prior_permit(self):
        plan = self.plan()
        permit = self.permit(plan)
        self.app.plan(plan["case_id"], amount="0.00")
        with self.assertRaises(Rejected):
            self.app.execute(permit)
        self.assertEqual(self.device.calls, [])

    def test_live_kill_switch_blocks_previously_authorized_action(self):
        permit = self.permit(self.plan())
        self.app.set_controls(kill_switch=True)
        with self.assertRaisesRegex(Rejected, "halt"):
            self.app.execute(permit)

    def test_evidence_is_rechecked_immediately_before_execution(self):
        plan = self.plan()
        self.clock.now += 250
        permit = self.permit(plan)
        self.clock.now += 51
        with self.assertRaisesRegex(Rejected, "stale"):
            self.app.execute(permit)

    def test_budget_reservations_prevent_concurrent_overspend(self):
        p1 = self.plan("node-1", "1200.00", "draft_requisition")
        p2 = self.plan("node-2", "1200.00", "draft_requisition")
        self.permit(p1)
        with self.assertRaisesRegex(Rejected, "budget"):
            self.permit(p2)

    def test_single_use_permit_is_atomic_under_concurrent_calls(self):
        permit = self.permit(self.plan())
        outcomes = []
        def run():
            try:
                self.app.execute(permit)
                outcomes.append("ok")
            except Rejected:
                outcomes.append("rejected")
        threads = [threading.Thread(target=run) for _ in range(2)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        self.assertCountEqual(outcomes, ["ok", "rejected"])
        self.assertEqual(len(self.device.calls), 1)

    def test_http_success_does_not_close_case(self):
        plan = self.plan()
        result = self.app.execute(self.permit(plan))
        self.assertEqual(result["status"], "succeeded")
        self.assertEqual(self.app.get_case(plan["case_id"])["state"], "POST_CHECK")
        self.clock.now += 10
        case = self.incident()
        self.assertEqual(case["state"], "POST_CHECK")

    def test_recovery_requires_multiple_fresh_samples_and_soak(self):
        plan = self.plan()
        self.app.execute(self.permit(plan))
        for offset in [1, 31]:
            self.clock.now = 1_800_000_000 + offset
            case = self.app.observe("node-1", 50, self.clock(), source="bmc", event_id=str(offset))
            self.assertEqual(case["state"], "POST_CHECK")
        self.clock.now += 31
        case = self.app.observe("node-1", 50, self.clock(), source="bmc", event_id="recovered")
        self.assertEqual(case["state"], "CLOSED")

    def test_regression_resets_soak(self):
        plan = self.plan()
        self.app.execute(self.permit(plan))
        self.clock.now += 1
        self.app.observe("node-1", 50, self.clock(), source="bmc", event_id="good1")
        self.clock.now += 30
        self.incident()
        self.clock.now += 31
        case = self.app.observe("node-1", 50, self.clock(), source="bmc", event_id="good2")
        self.assertEqual(case["state"], "POST_CHECK")
        self.assertEqual(case["healthy_samples"], 1)

    def test_lost_response_reconciles_after_restart_without_duplicate_effect(self):
        self.device.lose_reply = True
        plan = self.plan()
        permit = self.permit(plan)
        result = self.app.execute(permit)
        self.assertEqual(result["status"], "unknown")
        restarted = Overwatch(self.path, b"test-only-key-with-at-least-32-bytes", clock=self.clock,
                              adapter=self.device)
        self.assertEqual(restarted.get_case(plan["case_id"])["state"], "RECONCILING")
        result = restarted.reconcile(result["id"])
        self.assertEqual(result["status"], "succeeded")
        self.assertEqual(len(self.device.calls), 1)
        with self.assertRaises(Rejected):
            restarted.execute(permit)

    def test_simulation_evidence_cannot_authorize_production(self):
        plan = self.plan()
        with self.assertRaises(Rejected):
            production = Overwatch(self.path, b"test-only-key-with-at-least-32-bytes",
                                   environment="production", clock=self.clock, adapter=self.device)
            production.verify(plan["id"])

    def test_amount_validation_and_no_real_action_allowlist(self):
        case = self.incident()
        for amount in ["-1", "nan", "Infinity", "0.001"]:
            with self.assertRaises(Rejected):
                self.app.plan(case["id"], amount=amount)
        with self.assertRaises(Rejected):
            self.app.plan(case["id"], action="firmware_update")

    def test_hash_chain_detects_tampering(self):
        self.plan()
        self.assertTrue(self.app.store.verify_ledger())
        import sqlite3
        with closing(sqlite3.connect(self.path)) as conn:
            conn.execute("UPDATE events SET payload = '{}' WHERE seq = 1")
            conn.commit()
        self.assertFalse(self.app.store.verify_ledger())

    def test_retrieval_timestamp_alone_cannot_authorize_action(self):
        case = self.app.observe("node-1", 91, self.clock(), source="static-mock", event_id="no-time",
                                timestamp_basis="retrieval_only")
        plan = self.app.plan(case["id"])
        with self.assertRaisesRegex(Rejected, "measurement time"):
            self.app.verify(plan["id"])

    def test_healthy_samples_before_action_completion_do_not_count(self):
        plan = self.plan()
        self.clock.now += 20
        self.app.execute(self.permit(plan))
        case = self.app.observe("node-1", 50, self.clock() - 10, source="bmc", event_id="late-delivery")
        self.assertEqual(case["healthy_samples"], 0)

    def test_long_telemetry_gap_breaks_recovery_soak(self):
        plan = self.plan()
        self.app.execute(self.permit(plan))
        self.clock.now += 1
        self.app.observe("node-1", 50, self.clock(), source="bmc", event_id="sample1")
        self.clock.now += 400
        case = self.app.observe("node-1", 50, self.clock(), source="bmc", event_id="sample2")
        self.assertEqual(case["healthy_samples"], 1)
        self.assertEqual(case["state"], "POST_CHECK")

    def test_procurement_draft_is_not_a_completed_repair(self):
        plan = self.plan(amount="100.00", action="draft_requisition")
        self.app.execute(self.permit(plan))
        self.assertEqual(self.app.get_case(plan["case_id"])["state"], "AWAITING_FULFILLMENT")

    def test_confirmed_failure_releases_reservation(self):
        plan = self.plan(amount="1500.00", action="draft_requisition")
        self.device.execute = lambda p, k: "failed"
        self.app.execute(self.permit(plan))
        second = self.plan("node-2", "1500.00", "draft_requisition")
        self.assertEqual(self.permit(second)["status"], "issued")

    def test_unknown_outcome_retains_budget_reservation(self):
        plan = self.plan(amount="1500.00", action="draft_requisition")
        self.device.lose_reply = True
        self.app.execute(self.permit(plan))
        second = self.plan("node-2", "1500.00", "draft_requisition")
        with self.assertRaisesRegex(Rejected, "budget"):
            self.permit(second)

    def test_changed_receipt_and_recomputed_hash_do_not_bypass_ledger_anchor(self):
        from overwatch.store import digest
        plan = self.plan()
        with self.app.store.transaction() as tx:
            receipt = tx.get("receipts", plan["receipt_ids"][0])
            receipt["body"]["temperature"] = 55
            receipt["sha256"] = digest(receipt["body"])
            tx.put("receipts", receipt)
        with self.assertRaisesRegex(Rejected, "anchor"):
            self.app.approve(plan["id"], "sre", "operator")

    def test_corrupted_ledger_blocks_an_existing_permit(self):
        import sqlite3
        permit = self.permit(self.plan())
        with closing(sqlite3.connect(self.path)) as conn:
            conn.execute("UPDATE events SET payload = '{}' WHERE seq = 1")
            conn.commit()
        with self.assertRaisesRegex(Rejected, "ledger"):
            self.app.execute(permit)
        self.assertEqual(self.device.calls, [])


if __name__ == "__main__":
    unittest.main()

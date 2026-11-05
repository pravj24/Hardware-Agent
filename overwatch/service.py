"""Durable case, verifier, policy and broker application services.

These logical boundaries run in one process for the reference lab. A production
deployment must isolate the signing authority and broker with separate identities.
"""
import time
import uuid

from .errors import Rejected
from .policy import (POLICY_VERSION, check_signature, finite, money, plan_hash,
                     required_roles, sign)
from .store import Store, digest


def new_id(prefix):
    return prefix + "_" + uuid.uuid4().hex


def require(value, message):
    if not value:
        raise Rejected(message)
    return value


class DisabledAdapter:
    environment = "disabled"

    def execute(self, plan, key):
        raise Rejected("no execution adapter configured")

    def status(self, key):
        return "unknown"


class Overwatch:
    def __init__(self, path, key, *, environment="simulation", clock=time.time,
                 adapter=None, budget="10000.00", soak_seconds=60):
        require(len(key) >= 32, "signing key must contain at least 32 bytes")
        require(environment in {"simulation", "production"}, "invalid environment")
        require(soak_seconds >= 0, "invalid soak period")
        self.store, self.key, self.clock = Store(path), key, clock
        self.environment, self.adapter = environment, adapter or DisabledAdapter()
        self.soak_seconds = soak_seconds
        with self.store.transaction() as tx:
            settings = tx.get("settings", "policy")
            if settings:
                require(settings["environment"] == environment, "database environment mismatch")
            else:
                tx.put("settings", dict(id="policy", environment=environment,
                       budget=str(money(budget)), spent="0.00", kill_switch=False, freeze=False,
                       version=POLICY_VERSION))
                tx.event("initialized", {"environment": environment, "policy": POLICY_VERSION}, self.clock())

    def _checked(self, fn):
        """Persist refusals outside the rolled-back operation transaction."""
        try:
            require(self.store.verify_ledger(), "ledger integrity check failed")
            return fn()
        except Rejected as exc:
            with self.store.transaction() as tx:
                tx.event("refused", {"reason": str(exc)}, self.clock())
            raise

    def observe(self, asset_id, temperature, observed_at, *, source, event_id, raw=None, timestamp_basis="device"):
        temperature, observed_at = finite(temperature), finite(observed_at)
        now = self.clock()
        require(-80 <= temperature <= 200, "temperature outside supported sensor range")
        require(0 < observed_at <= now + 5, "measurement timestamp is in the future or invalid")
        require(isinstance(asset_id, str) and 0 < len(asset_id) <= 200, "invalid asset ID")
        require(isinstance(source, str) and source and isinstance(event_id, str) and event_id, "missing source/event ID")
        event_key = digest([source, event_id, asset_id])
        with self.store.transaction() as tx:
            seen = tx.get("ingested", event_key)
            if seen:
                return tx.get("cases", seen["case_id"]) if seen["case_id"] else None
            body = dict(asset_id=asset_id, temperature=temperature, observed_at=observed_at,
                        source=source, event_id=event_id, raw=raw, timestamp_basis=timestamp_basis)
            receipt = dict(id=new_id("rcpt"), body=body, sha256=digest(body),
                           environment=self.environment, authority="device", retrieved_at=now,
                           max_age=300, fixture=self.environment == "simulation")
            tx.put("receipts", receipt)
            cases = [c for c in tx.all("cases") if c["asset_id"] == asset_id and c["state"] != "CLOSED"]
            case = cases[-1] if cases else None
            fresh = now - observed_at <= receipt["max_age"]
            if case is None and (temperature >= 80 or not fresh):
                case = dict(id=new_id("case"), asset_id=asset_id, state="DETECTED" if fresh else "HELD",
                            receipt_ids=[], plan_id=None, created_at=now, healthy_since=None,
                            healthy_samples=0, last_observed_at=0, title="Thermal anomaly: " + asset_id)
            if case:
                case["receipt_ids"].append(receipt["id"])
                case["updated_at"] = now
                if case["state"] == "POST_CHECK":
                    if not fresh or temperature >= 80:
                        case["healthy_since"], case["healthy_samples"] = None, 0
                    elif observed_at > case["last_observed_at"]:
                        # A gap larger than the freshness limit breaks continuous recovery evidence.
                        if observed_at - case["last_observed_at"] > receipt["max_age"]:
                            case["healthy_since"], case["healthy_samples"] = None, 0
                        if case["healthy_since"] is None:
                            case["healthy_since"] = observed_at
                        case["healthy_samples"] += 1
                        if case["healthy_samples"] >= 3 and observed_at - case["healthy_since"] >= self.soak_seconds:
                            case["state"], case["closed_at"] = "CLOSED", now
                            tx.event("case_closed", {"case_id": case["id"], "reason": "verified_recovery"}, now)
                case["last_observed_at"] = max(case["last_observed_at"], observed_at)
                tx.put("cases", case)
            tx.put("ingested", dict(id=event_key, case_id=case["id"] if case else None))
            tx.event("observation", {"receipt_id": receipt["id"], "hash": receipt["sha256"],
                                     "case_id": case["id"] if case else None}, now)
            return case

    def mark_dark(self, asset_id, reason):
        with self.store.transaction() as tx:
            for case in tx.all("cases"):
                if case["asset_id"] == asset_id and case["state"] != "CLOSED":
                    case["state"] = "HELD"
                    case["healthy_since"], case["healthy_samples"] = None, 0
                    tx.put("cases", case)
            tx.put("dark", dict(id=asset_id, reason=reason, at=self.clock()))
            tx.event("telemetry_dark", {"asset_id": asset_id, "reason": reason}, self.clock())

    def plan(self, case_id, *, amount="0.00", action="simulate_repair"):
        amount = str(money(amount))
        require(action in {"simulate_repair", "draft_requisition"}, "action is not allow-listed")
        require(self.environment == "simulation", "execution plans in this release are simulation-only")
        require(action != "simulate_repair" or money(amount) == 0, "repair simulation cannot spend money")
        with self.store.transaction() as tx:
            case = require(tx.get("cases", case_id), "case not found")
            require(case["state"] not in {"EXECUTING", "RECONCILING", "CLOSED", "POST_CHECK", "AWAITING_FULFILLMENT"}, "case cannot be replanned in this state")
            if case["plan_id"]:
                previous = tx.get("plans", case["plan_id"])
                previous["status"] = "SUPERSEDED"
                tx.put("plans", previous)
                for p in tx.all("permits"):
                    if p["payload"]["plan_id"] == previous["id"] and p["status"] == "issued":
                        p["status"] = "revoked"
                        tx.put("permits", p)
            plan = dict(id=new_id("plan"), case_id=case_id, action=action, target=case["asset_id"],
                        amount=amount, currency="USD", environment=self.environment,
                        receipt_ids=[case["receipt_ids"][-1]], created_at=self.clock(),
                        policy_version=POLICY_VERSION, status="DRAFT",
                        options=[{"name": "Investigate and simulate maintenance", "reversible": True},
                                 {"name": "Hold for operator diagnostics", "reversible": True}])
            plan["hash"] = plan_hash(plan)
            tx.put("plans", plan)
            case["plan_id"], case["state"] = plan["id"], "PLANNED"
            tx.put("cases", case)
            tx.event("plan_created", {"plan_id": plan["id"], "hash": plan["hash"]}, self.clock())
            return plan

    def _verify(self, tx, plan):
        require(plan["hash"] == plan_hash(plan), "plan hash mismatch")
        require(plan["environment"] == self.environment, "plan environment mismatch")
        require(plan["policy_version"] == POLICY_VERSION, "policy release mismatch")
        case = require(tx.get("cases", plan["case_id"]), "case not found")
        require(case["plan_id"] == plan["id"], "plan superseded")
        require(case["state"] != "HELD", "telemetry hold blocks action")
        require(bool(plan["receipt_ids"]), "missing evidence")
        for identifier in plan["receipt_ids"]:
            receipt = require(tx.get("receipts", identifier), "missing receipt")
            require(receipt["environment"] == self.environment, "evidence environment mismatch")
            require(receipt["sha256"] == digest(receipt["body"]), "receipt integrity mismatch")
            require(tx.receipt_anchor(identifier) == receipt["sha256"], "receipt ledger anchor mismatch")
            require(receipt["authority"] == "device", "non-authoritative telemetry")
            require(receipt["body"]["asset_id"] == plan["target"], "evidence target mismatch")
            require(receipt["body"].get("timestamp_basis") == "device", "measurement time unverified; retrieval-only evidence cannot gate action")
            require(0 <= self.clock() - receipt["body"]["observed_at"] <= receipt["max_age"], "stale evidence")
            dark = tx.get("dark", plan["target"])
            require(not dark or receipt["retrieved_at"] > dark["at"], "telemetry dark; fresh evidence required")
        return case

    def verify(self, plan_id):
        def operation():
            with self.store.transaction() as tx:
                plan = require(tx.get("plans", plan_id), "plan not found")
                # Stale receipts should give the precise rejection even if initial ingestion held the case.
                for rid in plan["receipt_ids"]:
                    receipt = require(tx.get("receipts", rid), "missing receipt")
                    require(self.clock() - receipt["body"]["observed_at"] <= receipt["max_age"], "stale evidence")
                case = self._verify(tx, plan)
                plan["status"] = "VERIFIED"
                case["state"] = "AWAITING_APPROVAL"
                tx.put("plans", plan)
                tx.put("cases", case)
                tx.event("verified", {"plan_id": plan_id, "hash": plan["hash"]}, self.clock())
                return plan
        return self._checked(operation)

    def approve(self, plan_id, actor, role):
        def operation():
            with self.store.transaction() as tx:
                plan = require(tx.get("plans", plan_id), "plan not found")
                require(plan["status"] == "VERIFIED", "verify plan before approval")
                self._verify(tx, plan)
                require(role in required_roles(plan), "role not authorized for this plan")
                approvals = [a for a in tx.all("approvals") if a["plan_id"] == plan_id]
                require(not any(a["actor"] == actor and a["role"] != role for a in approvals), "two-person approval required")
                approval = dict(id=digest([plan_id, actor, role]), plan_id=plan_id, actor=actor,
                                role=role, hash=plan["hash"], at=self.clock(), expires_at=self.clock() + 3600)
                tx.put("approvals", approval)
                tx.event("approved", approval, self.clock())
                return approval
        return self._checked(operation)

    def issue_permit(self, plan_id):
        def operation():
            with self.store.transaction() as tx:
                plan = require(tx.get("plans", plan_id), "plan not found")
                require(plan["status"] == "VERIFIED", "verified plan required")
                case = self._verify(tx, plan)
                require(case["state"] == "AWAITING_APPROVAL", "case is not awaiting authorization")
                settings = tx.get("settings", "policy")
                require(not settings["kill_switch"] and not settings["freeze"], "policy halt")
                approvals = [a for a in tx.all("approvals") if a["plan_id"] == plan_id
                             and a["hash"] == plan["hash"] and a["expires_at"] > self.clock()]
                require(required_roles(plan) <= {a["role"] for a in approvals}, "required approval missing or expired")
                permits = tx.all("permits")
                require(not any(p["payload"]["plan_id"] == plan_id and p["status"] in {"issued", "consumed"}
                                and (p["status"] == "consumed" or p["payload"]["expires_at"] > self.clock()) for p in permits),
                        "plan already has an active or consumed permit")
                reserved = sum((money(p["payload"]["amount"]) for p in permits
                                if (p["status"] == "issued" and p["payload"]["expires_at"] > self.clock())
                                or p["status"] == "consumed"), money("0"))
                require(money(settings["spent"]) + reserved + money(plan["amount"]) <= money(settings["budget"]), "budget exceeded")
                payload = dict(id=new_id("permit"), plan_id=plan_id, plan_hash=plan["hash"],
                               target=plan["target"], action=plan["action"], amount=plan["amount"],
                               currency=plan["currency"], environment=self.environment,
                               policy_version=POLICY_VERSION, issued_at=self.clock(), expires_at=self.clock() + 120,
                               approval_ids=[a["id"] for a in approvals], nonce=uuid.uuid4().hex,
                               idempotency_key=new_id("operation"))
                permit = dict(id=payload["id"], payload=payload, signature=sign(payload, self.key), status="issued")
                tx.put("permits", permit)
                tx.event("permit_issued", {"permit_id": permit["id"], "plan_hash": plan["hash"]}, self.clock())
                return permit
        return self._checked(operation)

    def execute(self, permit):
        def operation():
            check_signature(permit, self.key)
            with self.store.transaction() as tx:
                stored = require(tx.get("permits", permit["payload"]["id"]), "unknown permit")
                require(stored["payload"] == permit["payload"] and stored["signature"] == permit["signature"], "permit mismatch")
                require(stored["status"] == "issued", "permit already consumed or revoked")
                payload = stored["payload"]
                require(payload["expires_at"] > self.clock(), "permit expired")
                settings = tx.get("settings", "policy")
                require(not settings["kill_switch"] and not settings["freeze"], "policy halt")
                require(payload["environment"] == self.environment == self.adapter.environment, "execution environment mismatch")
                plan = require(tx.get("plans", payload["plan_id"]), "plan missing")
                require(payload["plan_hash"] == plan_hash(plan), "approved plan changed")
                case = self._verify(tx, plan)
                require(case["state"] == "AWAITING_APPROVAL", "case not executable")
                stored["status"] = "consumed"
                job = dict(id=payload["idempotency_key"], permit_id=stored["id"], plan_id=plan["id"],
                           case_id=case["id"], status="running", started_at=self.clock())
                case["state"] = "EXECUTING"
                tx.put("permits", stored)
                tx.put("operations", job)
                tx.put("cases", case)
                tx.event("execution_claimed", job, self.clock())
            # Network calls are outside the transaction. The claimed job survives a crash here.
            try:
                status = self.adapter.execute(plan, job["id"])
                status = status if status in {"succeeded", "failed"} else "unknown"
            except Exception:
                # A transport/adapter error cannot establish absence of a remote side effect.
                status = "unknown"
            return self._finish(job["id"], status)
        return self._checked(operation)

    def _finish(self, job_id, status):
        with self.store.transaction() as tx:
            job = require(tx.get("operations", job_id), "operation not found")
            if job["status"] in {"succeeded", "failed"}:
                return job
            job["status"], job["updated_at"] = status, self.clock()
            case = tx.get("cases", job["case_id"])
            case["state"] = {"succeeded": "POST_CHECK", "failed": "ESCALATED"}.get(status, "RECONCILING")
            if status == "succeeded":
                case["healthy_since"], case["healthy_samples"] = None, 0
                case["last_observed_at"] = max(case["last_observed_at"], self.clock())
                if tx.get("plans", job["plan_id"])["action"] == "draft_requisition":
                    case["state"] = "AWAITING_FULFILLMENT"
            if status in {"succeeded", "failed"}:
                permit = tx.get("permits", job["permit_id"])
                permit["status"] = "settled"
                tx.put("permits", permit)
                if status == "succeeded":
                    settings = tx.get("settings", "policy")
                    settings["spent"] = str(money(settings["spent"]) + money(permit["payload"]["amount"]))
                    tx.put("settings", settings)
            tx.put("operations", job)
            tx.put("cases", case)
            tx.event("execution_result", job, self.clock())
            return job

    def reconcile(self, job_id):
        job = require(self.store.read("operations", job_id), "operation not found")
        if job["status"] in {"succeeded", "failed"}:
            return job
        try:
            status = self.adapter.status(job_id)
        except Exception:
            status = "unknown"
        return self._finish(job_id, status if status in {"succeeded", "failed"} else "unknown")

    def recover_inflight(self):
        return [self.reconcile(job["id"]) for job in self.store.list("operations")
                if job["status"] in {"running", "unknown"}]

    def set_controls(self, *, kill_switch=None, freeze=None):
        with self.store.transaction() as tx:
            settings = tx.get("settings", "policy")
            for name, value in [("kill_switch", kill_switch), ("freeze", freeze)]:
                if value is not None:
                    require(isinstance(value, bool), "control must be boolean")
                    settings[name] = value
            tx.put("settings", settings)
            tx.event("policy_controls", settings, self.clock())
            return settings

    def get_case(self, case_id):
        return require(self.store.read("cases", case_id), "case not found")

    def snapshot(self):
        with self.store.transaction() as tx:
            result = {kind: tx.all(kind) for kind in ["cases", "plans", "receipts", "approvals", "permits", "operations", "tenders"]}
            result["policy"] = tx.get("settings", "policy")
        result["ledger_valid"] = self.store.verify_ledger()
        result["environment"] = self.environment
        return result

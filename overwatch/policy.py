"""Deterministic policy and signed permit primitives. No model dependencies."""
from decimal import Decimal, InvalidOperation
import hashlib
import hmac
import math

from .errors import Rejected
from .store import canonical, digest

POLICY_VERSION = "simulation-policy-v1"
PLAN_FIELDS = ("id", "case_id", "action", "target", "amount", "currency", "environment",
               "receipt_ids", "created_at", "policy_version", "options")


def money(value):
    try:
        amount = Decimal(str(value))
        if not amount.is_finite() or amount < 0 or amount > Decimal("1000000000000"):
            raise Rejected("invalid amount")
        if amount != amount.quantize(Decimal("0.01")):
            raise Rejected("amount must have at most two decimal places")
        return amount.quantize(Decimal("0.01"))
    except (InvalidOperation, ValueError, TypeError) as exc:
        raise Rejected("invalid amount") from exc


def finite(value):
    try:
        number = float(value)
        if not math.isfinite(number):
            raise ValueError()
        return number
    except (ValueError, TypeError, OverflowError) as exc:
        raise Rejected("number must be finite") from exc


def plan_hash(plan):
    return digest({key: plan[key] for key in PLAN_FIELDS})


def required_roles(plan):
    if plan["action"] == "draft_requisition":
        return {"procurement", "finance"} if money(plan["amount"]) >= 1000 else {"procurement"}
    return {"operator"}


def sign(payload, key):
    return hmac.new(key, canonical(payload).encode(), hashlib.sha256).hexdigest()


def check_signature(permit, key):
    if not isinstance(permit, dict) or not isinstance(permit.get("payload"), dict):
        raise Rejected("malformed permit")
    signature = permit.get("signature")
    if not isinstance(signature, str) or not hmac.compare_digest(signature, sign(permit["payload"], key)):
        raise Rejected("invalid permit signature")

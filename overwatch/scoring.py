"""Deterministic qualification scoring for the tender engine.

Two gates, both from the PRD:
  * A provider enters ranking only if its pre-qualification was *verified at source*
    (certs with the issuer, insurance with the insurer, references by call). Unverified
    providers are excluded, never ranked low.
  * Within a ranked provider, each factor scores from authoritative evidence only;
    an unverified factor scores zero, not partial.

Crustdata signals are indicative: they may be carried as a corroborating note on a
factor, but can never supply its authoritative score.
"""
from decimal import Decimal, InvalidOperation

from .errors import Rejected

# PRD default weights (each customer overrides in policy). Must sum to 100.
DEFAULT_WEIGHTS = {
    "capability": 25,        # hardware families and certifications
    "coverage_sla": 20,      # geographic coverage and response SLA
    "past_performance": 20,  # CMMS history of this provider's work for this customer
    "price": 15,             # current written bid
    "stability": 10,         # financial and organisational stability
    "security": 10,          # security and compliance posture
}
FACTORS = tuple(DEFAULT_WEIGHTS)


def _decimal(value, message):
    try:
        number = Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError) as exc:
        raise Rejected(message) from exc
    if not number.is_finite():
        raise Rejected(message)
    return number


def resolve_weights(overrides=None):
    weights = {k: Decimal(str(v)) for k, v in DEFAULT_WEIGHTS.items()}
    if overrides:
        if not isinstance(overrides, dict):
            raise Rejected("weights must be an object")
        for key, value in overrides.items():
            if key not in DEFAULT_WEIGHTS:
                raise Rejected("unknown scoring factor: " + str(key))
            number = _decimal(value, "weight must be a finite number")
            if number < 0:
                raise Rejected("weight must not be negative")
            weights[key] = number
    if sum(weights.values()) != Decimal("100"):
        raise Rejected("weights must sum to 100")
    return weights


def score_candidate(candidate, *, weights):
    provider = candidate.get("provider")
    if not isinstance(provider, str) or not provider:
        raise Rejected("candidate provider must be present")
    factors = candidate.get("factors")
    if not isinstance(factors, dict):
        raise Rejected("candidate factors must be an object")
    breakdown, unverified = {}, []
    total = Decimal("0")
    for name in FACTORS:
        entry = factors.get(name, {})
        if not isinstance(entry, dict):
            raise Rejected("factor entry must be an object: " + name)
        verified = entry.get("verified") is True
        if verified:
            strength = _decimal(entry.get("strength", 0), "factor strength must be a finite number: " + name)
            if not 0 <= strength <= 1:
                raise Rejected("factor strength must be between 0 and 1: " + name)
        else:
            strength = Decimal("0")
            unverified.append(name)
        points = (weights[name] * strength).quantize(Decimal("0.01"))
        total += points
        breakdown[name] = {"weight": str(weights[name]), "verified": verified,
                           "strength": str(strength), "points": str(points),
                           "corroborating": entry.get("corroborating")}
    return {"provider": provider, "score": str(total.quantize(Decimal("0.01"))),
            "max_score": "100.00", "breakdown": breakdown,
            "unverified_factors": unverified, "fully_authoritative": not unverified}


def rank_candidates(candidates, *, weights=None):
    if not isinstance(candidates, list) or not candidates:
        raise Rejected("candidates must be a non-empty list")
    resolved = resolve_weights(weights)
    ranked, excluded, seen = [], [], set()
    for candidate in candidates:
        if not isinstance(candidate, dict):
            raise Rejected("each candidate must be an object")
        provider = candidate.get("provider")
        if not isinstance(provider, str) or not provider or provider in seen:
            raise Rejected("candidate provider must be present and unique")
        seen.add(provider)
        if candidate.get("qualification_verified") is not True:
            excluded.append({"provider": provider, "reason": "pre-qualification not verified at source"})
            continue
        ranked.append(score_candidate(candidate, weights=resolved))
    ranked.sort(key=lambda c: (-Decimal(c["score"]), c["provider"]))
    return {"ranked": ranked, "excluded": excluded, "status": "DRAFT_ONLY",
            "recommendation": ranked[0]["provider"] if ranked else None,
            "weights": {k: str(v) for k, v in resolved.items()},
            "methodology": ("Weighted rubric; verified pre-qualification required to rank; "
                            "authoritative evidence only, each unverified factor scores zero")}

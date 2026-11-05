"""Minimal OAuth 2.1 authorization server for the MCP web gateway.

Demo-grade, for the hackathon: OAuth discovery, dynamic client registration (RFC
7591), PKCE (S256) authorize/token, and audience-bound, short-lived HMAC bearer
tokens. The /authorize step auto-approves a configured demo role instead of a real
login; production replaces only that step with the customer's SSO/OIDC IdP and
terminates TLS at the ingress. Discovery, PKCE, token-audience validation and the
read-only tool surface carry over unchanged.

Standard library only; no dependency. Paired with the onboarding Phase 6 "AI clients"
gate, which certifies that this gateway (metadata, PKCE, audience, read/propose scopes)
is in place.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import secrets
import threading
import time

ROLES = {"operator", "viewer", "procurement", "policy_admin",
         "iam_lead", "security_lead", "risk_lead", "ai_owner"}
DEFAULT_SCOPE = "mcp:read"


def _b64url(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def _b64url_decode(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def mint_token(claims: dict, key: bytes) -> str:
    payload = _b64url(json.dumps(claims, sort_keys=True, separators=(",", ":")).encode())
    signature = hmac.new(key, payload.encode(), hashlib.sha256).hexdigest()
    return payload + "." + signature


def read_token(token: str, key: bytes, *, now: float, audience: str) -> dict:
    if not isinstance(token, str) or token.count(".") != 1:
        raise ValueError("malformed token")
    payload, signature = token.split(".", 1)
    if not hmac.compare_digest(signature, hmac.new(key, payload.encode(), hashlib.sha256).hexdigest()):
        raise ValueError("invalid token signature")
    claims = json.loads(_b64url_decode(payload))
    if not isinstance(claims, dict) or claims.get("exp", 0) <= now:
        raise ValueError("token expired")
    if claims.get("aud") != audience:
        raise ValueError("token audience mismatch")
    return claims


def pkce_matches(verifier: str, challenge: str) -> bool:
    if not isinstance(verifier, str) or not isinstance(challenge, str):
        return False
    return hmac.compare_digest(_b64url(hashlib.sha256(verifier.encode()).digest()), challenge)


class OAuthProvider:
    def __init__(self, key: bytes, public_url: str, *, demo_role: str = "operator",
                 token_ttl: int = 3600, scope: str = DEFAULT_SCOPE, clock=time.time):
        if len(key) < 32:
            raise ValueError("signing key must contain at least 32 bytes")
        if demo_role not in ROLES:
            raise ValueError("demo role must be one of " + ", ".join(sorted(ROLES)))
        self.key, self.public_url, self.clock = key, public_url.rstrip("/"), clock
        self.demo_role, self.token_ttl, self.scope = demo_role, token_ttl, scope
        self._codes: dict[str, dict] = {}
        self._clients: set[str] = set()
        self._lock = threading.Lock()

    @property
    def resource(self) -> str:
        return self.public_url

    def protected_resource(self) -> dict:
        return {"resource": self.resource, "authorization_servers": [self.public_url]}

    def metadata(self) -> dict:
        base = self.public_url
        return {"issuer": base, "authorization_endpoint": base + "/authorize",
                "token_endpoint": base + "/token", "registration_endpoint": base + "/register",
                "response_types_supported": ["code"], "grant_types_supported": ["authorization_code"],
                "code_challenge_methods_supported": ["S256"],
                "token_endpoint_auth_methods_supported": ["none"], "scopes_supported": [self.scope]}

    def register(self) -> dict:
        client_id = "client_" + secrets.token_hex(8)
        with self._lock:
            self._clients.add(client_id)
        return {"client_id": client_id, "token_endpoint_auth_method": "none",
                "grant_types": ["authorization_code"], "response_types": ["code"]}

    def authorize(self, params: dict) -> str:
        if params.get("response_type") != "code":
            raise ValueError("unsupported response_type")
        redirect_uri, challenge = params.get("redirect_uri"), params.get("code_challenge")
        if not redirect_uri or not challenge or params.get("code_challenge_method") != "S256":
            raise ValueError("PKCE S256 and redirect_uri are required")
        role = params.get("role") if params.get("role") in ROLES else self.demo_role
        code = secrets.token_urlsafe(24)
        with self._lock:
            self._codes[code] = {"redirect_uri": redirect_uri, "challenge": challenge,
                                 "role": role, "exp": self.clock() + 300, "used": False}
        location = redirect_uri + ("&" if "?" in redirect_uri else "?") + "code=" + code
        if params.get("state"):
            location += "&state=" + params["state"]
        return location

    def token(self, form: dict) -> dict:
        if form.get("grant_type") != "authorization_code":
            raise ValueError("unsupported_grant_type")
        with self._lock:
            record = self._codes.get(form.get("code"))
            if not record or record["used"] or record["exp"] <= self.clock():
                raise ValueError("invalid or expired authorization code")
            if record["redirect_uri"] != form.get("redirect_uri"):
                raise ValueError("redirect_uri mismatch")
            if not pkce_matches(form.get("code_verifier"), record["challenge"]):
                raise ValueError("PKCE verification failed")
            record["used"] = True
            role = record["role"]
        claims = {"sub": "demo-" + role, "role": role, "scope": self.scope, "iss": self.public_url,
                  "aud": self.resource, "iat": self.clock(), "exp": self.clock() + self.token_ttl,
                  "jti": secrets.token_hex(8)}
        return {"access_token": mint_token(claims, self.key), "token_type": "Bearer",
                "expires_in": self.token_ttl, "scope": self.scope}

    def validate(self, authorization_header: str) -> dict:
        if not authorization_header.startswith("Bearer "):
            raise ValueError("bearer token required")
        return read_token(authorization_header[7:], self.key, now=self.clock(), audience=self.resource)

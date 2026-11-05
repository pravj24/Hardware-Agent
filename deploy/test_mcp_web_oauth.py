"""OAuth 2.1 + onboarding whoami tool tests for the MCP web gateway."""
import hashlib
import http.client
import json
import tempfile
import unittest
from pathlib import Path
from urllib.parse import parse_qs, urlsplit
from urllib.request import Request, urlopen
from urllib.error import HTTPError

from deploy.mcp_web_server import MCPWebServer
from deploy.oauth import OAuthProvider, _b64url
from overwatch.store import Store


def challenge_for(verifier):
    return _b64url(hashlib.sha256(verifier.encode()).digest())


class GatewayOAuthTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        store = Store(Path(self.temp.name) / "overwatch.db")
        provider = OAuthProvider(b"k" * 32, "http://placeholder", demo_role="operator")
        self.server = MCPWebServer(store, host="127.0.0.1", port=0, oauth=provider).start()
        self.addCleanup(self.server.close)
        provider.public_url = self.server.url  # bind audience/issuer to the real URL
        self.port = self.server._httpd.server_port

    def post(self, path, body, *, form=False, token=None, session=None):
        headers = {}
        if form:
            data = "&".join(f"{k}={v}" for k, v in body.items()).encode()
            headers["Content-Type"] = "application/x-www-form-urlencoded"
        else:
            data = json.dumps(body).encode()
            headers["Content-Type"] = "application/json"
        if token:
            headers["Authorization"] = "Bearer " + token
        if session:
            headers["Mcp-Session-Id"] = session
        req = Request(self.server.url + path, data=data, headers=headers)
        try:
            with urlopen(req, timeout=5) as r:
                raw = r.read()
                return r.status, (json.loads(raw) if raw else None), dict(r.headers)
        except HTTPError as e:
            with e:
                raw = e.read()
                return e.code, (json.loads(raw) if raw else None), dict(e.headers)

    def get_no_redirect(self, path):
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=5)
        conn.request("GET", path)
        resp = conn.getresponse()
        status, location = resp.status, resp.getheader("Location")
        resp.read()
        conn.close()
        return status, location

    def oauth_token(self):
        _, client, _ = self.post("/register", {})
        verifier = "verifier-" + "x" * 40
        status, location = self.get_no_redirect(
            f"/authorize?response_type=code&client_id={client['client_id']}"
            f"&redirect_uri=http://localhost/cb&code_challenge={challenge_for(verifier)}"
            "&code_challenge_method=S256&state=st")
        self.assertEqual(status, 302)
        code = parse_qs(urlsplit(location).query)["code"][0]
        status, body, _ = self.post("/token", {"grant_type": "authorization_code", "code": code,
            "redirect_uri": "http://localhost/cb", "code_verifier": verifier}, form=True)
        self.assertEqual(status, 200)
        return body["access_token"]

    def test_metadata_discovery_is_public(self):
        status, body, _ = self.post("/mcp/fleet", {"jsonrpc": "2.0", "id": 1, "method": "initialize"})
        self.assertEqual(status, 401)
        with urlopen(self.server.url + "/.well-known/oauth-authorization-server", timeout=5) as r:
            meta = json.load(r)
        self.assertEqual(meta["code_challenge_methods_supported"], ["S256"])

    def test_health_stays_public(self):
        with urlopen(self.server.url + "/health", timeout=5) as r:
            self.assertEqual(json.load(r)["status"], "ok")

    def test_full_oauth_flow_initialize_and_whoami_tool(self):
        token = self.oauth_token()
        status, init, headers = self.post("/mcp/fleet", {"jsonrpc": "2.0", "id": 1, "method": "initialize"}, token=token)
        self.assertEqual(status, 200)
        session = headers["Mcp-Session-Id"]
        self.post("/mcp/fleet", {"jsonrpc": "2.0", "method": "notifications/initialized"}, token=token, session=session)
        _, tools, _ = self.post("/mcp/fleet", {"jsonrpc": "2.0", "id": 2, "method": "tools/list"}, token=token, session=session)
        names = {t["name"] for t in tools["result"]["tools"]}
        self.assertIn("fleet.get_observations", names)
        self.assertIn("overwatch.whoami", names)  # onboarding tool surfaced over MCP
        _, who, _ = self.post("/mcp/fleet", {"jsonrpc": "2.0", "id": 3, "method": "tools/call",
            "params": {"name": "overwatch.whoami", "arguments": {}}}, token=token, session=session)
        payload = json.loads(who["result"]["content"][0]["text"])
        self.assertEqual(payload["identity"]["role"], "operator")
        self.assertFalse(payload["onboarding"]["started"])
        self.assertTrue(payload["onboarding"]["dashboard_url"])

    def test_tampered_token_is_rejected_with_challenge(self):
        token = self.oauth_token()
        status, _, headers = self.post("/mcp/fleet", {"jsonrpc": "2.0", "id": 1, "method": "initialize"}, token=token + "zz")
        self.assertEqual(status, 401)
        self.assertIn("resource_metadata", headers.get("WWW-Authenticate", ""))


if __name__ == "__main__":
    unittest.main()

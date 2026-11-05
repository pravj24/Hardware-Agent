"""Populate the running loopback hackathon app with explicitly fictional data."""
import argparse
import json
from pathlib import Path
from urllib.request import Request, urlopen

parser = argparse.ArgumentParser()
parser.add_argument("--port", type=int, default=8080)
parser.add_argument("--report", type=Path, default=Path("/tmp/overwatch-hackathon-live-report.json"))
args = parser.parse_args()
base = f"http://127.0.0.1:{args.port}"
token = ""


def request(route, body=None):
    req = Request(base + route, data=json.dumps(body).encode() if body is not None else None,
                  headers={"Content-Type": "application/json", **({"Authorization": "Bearer " + token} if token else {})})
    with urlopen(req, timeout=30) as response:
        return json.load(response)


assert request("/api/demo/config")["enabled"], "start with --hackathon first"
token = request("/api/demo/login", {})["token"]
request("/api/demo/prepare", {"name": "Aster Cloud", "region": "India"})
for step in ["identity", "inventory", "telemetry", "connectors", "policy", "ai", "shadow"]:
    request("/api/demo/step", {"step": step})
request("/api/demo/refresh", {})
request("/api/demo/tender", {})
state = request("/api/state")
open_cases = [c for c in state["cases"] if c["state"] != "CLOSED"]
if open_cases:
    request("/api/demo/draft", {"case_id": open_cases[0]["id"]})
status, state = request("/api/demo/status"), request("/api/state")
assert status["demo_ready"] and status["completed"] == 7
assert state["ledger_valid"] and not state["operations"]
report = {"url": base, "health": request("/healthz"), "demo_ready": status["demo_ready"],
          "completed_phases": status["completed"], "redfish_nodes": 3, "cases": len(state["cases"]),
          "plans": len(state["plans"]), "tenders": len(state["tenders"]), "work_orders": len(status["work_orders"]),
          "ledger_valid": state["ledger_valid"], "broker_executions": len(state["operations"]),
          "data": "fictional enterprise data and local Redfish simulation"}
args.report.write_text(json.dumps(report, indent=2) + "\n")
print(json.dumps(report))

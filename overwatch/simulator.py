"""Stateful loopback test lab, complementary to the upstream DMTF snapshot server.

This is deliberately a small scenario simulator, not a Redfish conformance emulator.
Action acceptance never implies recovery: the scenario controller supplies readings.
"""
from datetime import datetime, timezone
import copy
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import hmac
import json
import threading
import time


class LabServer:
    def __init__(self, host="127.0.0.1", port=0, token=None, fleet_size=1):
        if fleet_size not in {1, 3}:
            raise ValueError("supported lab fleet sizes are one or three")
        self.fleet_size = fleet_size
        self.scenario = "healthy"
        self.token = token
        self.lock = threading.RLock()
        self.requests, self.operations = [], {}
        lab = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def send(self, code, body):
                payload = json.dumps(body).encode()
                self.send_response(code)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)

            def do_GET(self):
                with lab.lock:
                    lab.requests.append(("GET", self.path))
                    if self.path == "/lab/meta":
                        return self.send(200, dict(environment="simulation", fixture="watchermcp-lab-v1"))
                    if self.path.startswith("/lab/operations/"):
                        return self.send(200, {"status": lab.operations.get(self.path.rsplit("/", 1)[-1], "unknown")})
                    if lab.scenario == "dark":
                        return self.send(503, {"error": "simulated telemetry outage"})
                    resources = lab.resources()
                    body = resources.get(self.path.rstrip("/"))
                    self.send(200 if body is not None else 404, body if body is not None else {"error": "not found"})

            def do_POST(self):
                if lab.token and not hmac.compare_digest(self.headers.get("X-Lab-Token", ""), lab.token):
                    return self.send(403, {"error": "lab authorization required"})
                try:
                    size = int(self.headers.get("Content-Length", "0"))
                    if not 0 < size <= 100_000:
                        raise ValueError()
                    body = json.loads(self.rfile.read(size))
                    if not isinstance(body, dict):
                        raise ValueError()
                except (ValueError, TypeError):
                    return self.send(400, {"error": "invalid body"})
                with lab.lock:
                    lab.requests.append(("POST", self.path, body))
                    if self.path == "/lab/scenario":
                        try:
                            lab.set_scenario(body.get("scenario"))
                        except ValueError:
                            return self.send(400, {"error": "unknown scenario"})
                        return self.send(200, {"scenario": lab.scenario})
                    if self.path not in {"/redfish/v1/Systems/node-a/Actions/Oem.OverwatchRepair", "/lab/requisitions"}:
                        return self.send(404, {"error": "unsupported action"})
                    key = body.get("operation_id")
                    if not isinstance(key, str) or not key:
                        return self.send(400, {"error": "operation_id required"})
                    # Replay returns the recorded result without applying another effect.
                    if key in lab.operations:
                        return self.send(200, {"status": lab.operations[key], "replayed": True})
                    lab.operations[key] = "succeeded"
                    if lab.scenario == "lost_response":
                        self.close_connection = True
                        return
                    return self.send(200, {"status": "succeeded", "health_changed": False})

        self.server = ThreadingHTTPServer((host, port), Handler)
        self.server.daemon_threads = True
        self.url = "http://" + host + ":" + str(self.server.server_port)
        self.thread = None

    def set_scenario(self, scenario):
        if scenario not in {"healthy", "degraded", "recovered", "dark", "stale", "lost_response"}:
            raise ValueError("unknown scenario")
        with self.lock:
            self.scenario = scenario

    def resources(self):
        def ref(path):
            return {"@odata.id": path}
        ts = datetime.fromtimestamp(time.time() - (900 if self.scenario == "stale" else 0), timezone.utc).isoformat()
        temp = 45 if self.scenario in {"healthy", "recovered"} else 91
        resources = {
            "/redfish/v1/AccountService/Accounts/overwatch-reader": {"Id": "overwatch-reader", "RoleId": "ReadOnly"},
            "/redfish/v1/AccountService/Roles/ReadOnly": {"Id": "ReadOnly", "AssignedPrivileges": ["Login", "ConfigureSelf"], "OemPrivileges": []},
            "/redfish/v1": {"@odata.type": "#ServiceRoot.v1_15_0.ServiceRoot", "Id": "RootService",
                            "RedfishVersion": "1.15.0", "Systems": ref("/redfish/v1/Systems"),
                            "Chassis": ref("/redfish/v1/Chassis")},
            "/redfish/v1/Systems": {"Members": [ref("/redfish/v1/Systems/node-a")], "Members@odata.count": 1},
            "/redfish/v1/Systems/node-a": {"Id": "node-a", "SerialNumber": "LAB-SERVER-001", "Manufacturer": "WatcherMCP lab",
                "PowerState": "On", "Links": {"Chassis": [ref("/redfish/v1/Chassis/chassis-a")]},
                "Actions": {"Oem": {"#Overwatch.SimulateRepair": {"target": "/redfish/v1/Systems/node-a/Actions/Oem.OverwatchRepair"}}}},
            "/redfish/v1/Chassis": {"Members": [ref("/redfish/v1/Chassis/chassis-a")]},
            "/redfish/v1/Chassis/chassis-a": {"Id": "chassis-a", "Thermal": ref("/redfish/v1/Chassis/chassis-a/Thermal")},
            "/redfish/v1/Chassis/chassis-a/Thermal": {"Timestamp": ts, "Temperatures": [
                {"MemberId": "inlet", "Name": "Inlet temperature", "ReadingCelsius": temp,
                 "ReadingTime": ts, "Status": {"Health": "OK" if temp < 80 else "Critical"}}]}
        }
        for i, letter in enumerate("bc", 2):
            if self.fleet_size == 1:
                break
            system_path, chassis_path = "/redfish/v1/Systems/node-" + letter, "/redfish/v1/Chassis/chassis-" + letter
            resources["/redfish/v1/Systems"]["Members"].append(ref(system_path))
            resources["/redfish/v1/Systems"]["Members@odata.count"] = self.fleet_size
            resources["/redfish/v1/Chassis"]["Members"].append(ref(chassis_path))
            resources[system_path] = {"Id": "node-" + letter, "SerialNumber": f"LAB-SERVER-{i:03d}",
                                      "Manufacturer": "WatcherMCP lab", "PowerState": "On", "Links": {"Chassis": [ref(chassis_path)]}}
            resources[chassis_path] = {"Id": "chassis-" + letter, "Thermal": ref(chassis_path + "/Thermal")}
            resources[chassis_path + "/Thermal"] = copy.deepcopy(resources["/redfish/v1/Chassis/chassis-a/Thermal"])
        return resources

    def start(self):
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        return self

    def close(self):
        if self.thread:
            self.server.shutdown()
            self.thread.join(timeout=3)
        self.server.server_close()

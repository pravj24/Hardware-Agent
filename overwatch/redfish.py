"""Link-following Redfish collector and a separate simulation-only action adapter."""
from datetime import datetime, timezone
import json
import ssl
import time
from urllib.error import HTTPError, URLError
from urllib.parse import urljoin, urlsplit, quote
from urllib.request import (Request, build_opener, HTTPRedirectHandler, HTTPSHandler,
                            ProxyHandler)


class RedfishError(RuntimeError):
    pass


class NoRedirects(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise RedfishError("Redfish redirects are disabled")


def timestamp(value):
    if isinstance(value, (int, float)):
        return float(value)
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            raise ValueError("timestamp has no timezone")
        return dt.timestamp()
    except (AttributeError, TypeError, ValueError) as exc:
        raise RedfishError("invalid measurement timestamp") from exc


class RedfishClient:
    def __init__(self, base_url, *, environment="simulation", timeout=3, authorization=None,
                 lab_token=None, ca_file=None):
        parsed = urlsplit(base_url)
        if parsed.scheme not in {"http", "https"} or not parsed.hostname or parsed.username or parsed.password:
            raise RedfishError("invalid endpoint URL")
        if parsed.query or parsed.fragment or parsed.path not in {"", "/"}:
            raise RedfishError("endpoint must be an origin without a resource path")
        if environment not in {"simulation", "production"}:
            raise RedfishError("invalid environment")
        if environment == "production" and parsed.scheme != "https":
            raise RedfishError("production Redfish requires verified TLS")
        self.base_url, self.origin = base_url.rstrip("/"), (parsed.scheme, parsed.netloc)
        self.environment, self.timeout = environment, timeout
        self.headers = {"Accept": "application/json"}
        if authorization:
            self.headers["Authorization"] = authorization
        if lab_token:
            self.headers["X-Lab-Token"] = lab_token
        self.opener = build_opener(ProxyHandler({}), NoRedirects(),
                                   HTTPSHandler(context=ssl.create_default_context(cafile=ca_file)))

    def request(self, method, path, body=None):
        url = urljoin(self.base_url + "/", path)
        target = urlsplit(url)
        if (target.scheme, target.netloc) != self.origin or target.username or target.fragment:
            raise RedfishError("cross-origin Redfish link rejected")
        data = json.dumps(body, allow_nan=False).encode() if body is not None else None
        request = Request(url, data=data, method=method,
                          headers={**self.headers, **({"Content-Type": "application/json"} if data else {})})
        try:
            with self.opener.open(request, timeout=self.timeout) as response:
                raw = response.read(2_000_001)
                if len(raw) > 2_000_000:
                    raise RedfishError("resource exceeds size limit")
                if not raw:
                    return {}
                result = json.loads(raw)
                if not isinstance(result, dict):
                    raise RedfishError("resource must be a JSON object")
                return result
        except (HTTPError, URLError, OSError, ValueError) as exc:
            # An HTTPError (e.g. the simulated 503 outage) wraps an open socket; close it so
            # Windows does not later leak it and emit a ResourceWarning that CI's
            # `python -W error::ResourceWarning` turns into a fatal failure.
            if isinstance(exc, HTTPError):
                exc.close()
            # Do not include headers or credentials in diagnostics.
            raise RedfishError(f"Redfish {method} failed: {type(exc).__name__}") from exc

    def get(self, path):
        return self.request("GET", path)

    @staticmethod
    def link(value):
        if not isinstance(value, dict) or not isinstance(value.get("@odata.id"), str):
            raise RedfishError("missing resource link")
        return value["@odata.id"]

    def members(self, link):
        visited, result = set(), []
        path = self.link(link)
        while path:
            if path in visited or len(visited) >= 100:
                raise RedfishError("cyclic or excessive collection pagination")
            visited.add(path)
            collection = self.get(path)
            for member in collection.get("Members", []):
                result.append(self.get(self.link(member)))
            path = collection.get("Members@odata.nextLink")
        return result

    def collect(self):
        root = self.get("/redfish/v1/")
        if "Systems" not in root:
            raise RedfishError("Systems collection unavailable")
        readings = []
        for system in self.members(root["Systems"]):
            asset_id = system.get("SerialNumber") or system.get("UUID")
            if not asset_id:
                raise RedfishError("system has no stable serial/UUID")
            chassis_links = system.get("Links", {}).get("Chassis", [])
            if not chassis_links:
                raise RedfishError("system has no chassis link")
            temperatures = []
            for chassis_link in chassis_links:
                chassis = self.get(self.link(chassis_link))
                if "Thermal" in chassis:
                    thermal = self.get(self.link(chassis["Thermal"]))
                    temperatures.extend((sensor, thermal) for sensor in thermal.get("Temperatures", [])
                                        if sensor.get("ReadingCelsius") is not None)
                elif "Sensors" in chassis:
                    for sensor in self.members(chassis["Sensors"]):
                        if sensor.get("ReadingType") == "Temperature" and sensor.get("Reading") is not None:
                            temperatures.append(({**sensor, "ReadingCelsius": sensor["Reading"]}, sensor))
            if not temperatures:
                raise RedfishError("no supported temperature resource; adapter extension required")
            sensor, raw = max(temperatures, key=lambda item: float(item[0]["ReadingCelsius"]))
            observed = sensor.get("ReadingTime") or raw.get("Timestamp")
            # Without a device timestamp a GET proves a read occurred, not when a cached sample was measured.
            received = time.time()
            readings.append(dict(asset_id=str(asset_id), temperature=sensor["ReadingCelsius"],
                                 observed_at=timestamp(observed) if observed else received,
                                 timestamp_basis="device" if observed else "retrieval_only",
                                 source=self.base_url, event_id=str(observed or received) + ":" + str(sensor.get("MemberId", "temperature")),
                                 raw={"system": system, "thermal": raw}))
        return readings


class SimulationAdapter:
    environment = "simulation"

    def __init__(self, client):
        if client.environment != "simulation":
            raise RedfishError("simulation adapter requires a simulation endpoint")
        self.client = client

    def execute(self, plan, key):
        meta = self.client.get("/lab/meta")
        if meta.get("environment") != "simulation" or meta.get("fixture") != "watchermcp-lab-v1":
            raise RedfishError("endpoint is not the WatcherMCP test lab")
        if plan["action"] == "draft_requisition":
            self.client.request("POST", "/lab/requisitions", {"operation_id": key, "plan": plan})
        elif plan["action"] == "simulate_repair":
            systems = self.client.members(self.client.get("/redfish/v1/")["Systems"])
            system = next((s for s in systems if s.get("SerialNumber") == plan["target"]), None)
            if not system:
                raise RedfishError("target is not present in the lab")
            action = system["Actions"]["Oem"]["#Overwatch.SimulateRepair"]["target"]
            self.client.request("POST", action, {"operation_id": key, "recover": False})
        else:
            raise RedfishError("action not allow-listed")
        return "succeeded"

    def status(self, key):
        return self.client.get("/lab/operations/" + quote(key, safe="")).get("status", "unknown")

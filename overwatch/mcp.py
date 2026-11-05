"""Minimal read-only MCP stdio servers, with a fixed tool surface per trust zone.

No approval, permit, write credential, arbitrary URL or action tool is exposed.
Protocol implemented: 2025-06-18 initialize, ping, tools/list, tools/call.
"""
import json

from .store import canonical


TOOL_SETS = {
    "fleet": {"fleet.get_observations": ("Read normalized telemetry receipts", "receipts", "asset_id")},
    "records": {"records.get_cases": ("Read durable case summaries", "cases", "asset_id"),
                "records.get_plans": ("Read proposed maintenance plans", "plans", "case_id")},
    "market": {"market.get_tenders": ("Read simulation tender records: discovery long-lists, scored shortlists and bid comparisons. Crustdata leads are indicative, not supplier qualification", "tenders", None)},
}


class MCPServer:
    def __init__(self, store, zone):
        if zone not in TOOL_SETS:
            raise ValueError("zone must be fleet, records or market")
        self.store, self.zone = store, zone
        self.initialized, self.ready = False, False

    def handle(self, request):
        identifier = request.get("id") if isinstance(request, dict) else None

        def error(code, message):
            return {"jsonrpc": "2.0", "id": identifier, "error": {"code": code, "message": message}}

        def result(body):
            return {"jsonrpc": "2.0", "id": identifier, "result": body}

        if not isinstance(request, dict) or request.get("jsonrpc") != "2.0" or not isinstance(request.get("method"), str):
            return error(-32600, "Invalid request")
        method = request["method"]
        if "id" not in request:
            if method == "notifications/initialized" and self.initialized:
                self.ready = True
            return None
        if method == "initialize":
            if self.initialized:
                return error(-32600, "Already initialized")
            self.initialized = True
            return result({"protocolVersion": "2025-06-18", "capabilities": {"tools": {"listChanged": False}},
                           "serverInfo": {"name": "watchermcp-" + self.zone, "version": "0.1.0"},
                           "instructions": "Read-only evidence. Simulation records cannot authorize production actions."})
        if method == "ping":
            return result({})
        if not self.ready:
            return error(-32002, "Initialize and send notifications/initialized first")
        if method == "tools/list":
            tools = []
            for name, (description, _, field) in TOOL_SETS[self.zone].items():
                properties = {field: {"type": "string"}} if field else {}
                tools.append(dict(name=name, description=description,
                                  inputSchema={"type": "object", "properties": properties, "additionalProperties": False},
                                  annotations={"readOnlyHint": True, "destructiveHint": False,
                                               "idempotentHint": True, "openWorldHint": False}))
            return result({"tools": tools})
        if method == "tools/call":
            params = request.get("params", {})
            if not isinstance(params, dict) or params.get("name") not in TOOL_SETS[self.zone]:
                return error(-32602, "Unknown tool in this trust zone")
            _, kind, field = TOOL_SETS[self.zone][params["name"]]
            args = params.get("arguments", {})
            if not isinstance(args, dict) or set(args) - ({field} if field else set()):
                return error(-32602, "Unknown arguments")
            if field and field in args and not isinstance(args[field], str):
                return error(-32602, "Filter must be a string")
            records = self.store.list(kind)
            if field and field in args:
                records = [r for r in records if r.get("body", r).get(field) == args[field]]
            if kind == "receipts":
                # Raw external text stays outside the tool-using agent's context.
                records = [{**{k: r[k] for k in ("id", "sha256", "environment", "authority", "retrieved_at", "max_age")},
                            "body": {k: r["body"].get(k) for k in ("asset_id", "temperature", "observed_at", "timestamp_basis")}}
                           for r in records]
            return result({"content": [{"type": "text", "text": canonical(records[-100:])}], "isError": False})
        return error(-32601, "Method not found")

    def run(self, source, target):
        for line in source:
            try:
                if len(line) > 1_000_000:
                    raise ValueError("message too large")
                response = self.handle(json.loads(line))
            except ValueError:
                response = {"jsonrpc": "2.0", "id": None, "error": {"code": -32700, "message": "Parse error"}}
            if response is not None:
                target.write(canonical(response) + "\n")
                target.flush()

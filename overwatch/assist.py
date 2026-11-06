"""Server-side LLM assistant for the dashboard: draft maintenance messages, approval
summaries and tender searches, with tool calling wired to the deterministic actions.

This is the dashboard-native LLM path, distinct from the MCP servers (which expose
tools to Claude Code as an external client). The safety boundary is unchanged:

  * every tool here is READ or DRAFT only - discovery, case evidence, recent tenders;
    there is no tool that approves, signs, sends or executes. Those stay on the
    authenticated human path through the policy service and broker.
  * the model drafts prose for human review; it does not invent numbers. Figures come
    back from the tools (receipts, case facts) and are returned to the caller alongside
    the draft so the dashboard can show the evidence the text is built on.

Gemini is the server-side provider, using its native REST function-calling API.
Without GEMINI_API_KEY the endpoint returns an explicitly offline stub. Gemini
output is unverified draft prose, never a permit or authoritative evidence.
The default app/tests need no model SDK and make no paid calls.
"""
import json
import os

from .discovery import discover_providers
from .errors import Rejected
from .gemini import GeminiClient

MAX_STEPS = 4
MAX_TOKENS = 4096

SYSTEM = (
    "You are the Hardware Overwatch operations assistant inside the operator dashboard. "
    "You draft maintenance messages, approval-request summaries and provider/tender "
    "searches for a human to review and act on. Rules you must follow:\n"
    "- You cannot approve, sign, send, purchase or execute anything. You only draft and "
    "gather evidence; a human acts in the dashboard through the policy service and broker.\n"
    "- Never invent numbers, prices, temperatures, dates or quantities. Use only values "
    "returned by the tools, and refer to them with their case id or receipt id.\n"
    "- Crustdata discovery results are indicative leads, never qualified or available "
    "suppliers; say so when you use them.\n"
    "- Present every output explicitly as a draft for human review."
)

TOOLS = [
    {"name": "discover_providers",
     "description": "Find candidate maintenance/service providers via Crustdata plus the approved vendor list. Returns indicative leads only (never qualification); sanctioned/blocked suppliers are screened out.",
     "strict": True,
     "input_schema": {"type": "object", "additionalProperties": False,
                      "properties": {"need_query": {"type": "string"},
                                     "approved_vendors": {"type": "array", "items": {"type": "string"}},
                                     "sanctioned": {"type": "array", "items": {"type": "string"}},
                                     "blocked": {"type": "array", "items": {"type": "string"}}},
                      "required": ["need_query"]}},
    {"name": "get_case_evidence",
     "description": "Read a case and its telemetry receipts (asset, state, temperature, measurement time, receipt hashes). Use these exact figures; do not invent any.",
     "strict": True,
     "input_schema": {"type": "object", "additionalProperties": False,
                      "properties": {"case_id": {"type": "string"}}, "required": ["case_id"]}},
    {"name": "get_recent_tenders",
     "description": "Read recent tender records (discovery long-lists, scored shortlists, bid comparisons).",
     "strict": True,
     "input_schema": {"type": "object", "additionalProperties": False,
                      "properties": {"limit": {"type": "integer"}}, "required": []}},
]
TOOL_NAMES = {tool["name"] for tool in TOOLS}


class StubClient:
    """Offline client used when no GEMINI_API_KEY is configured. No tool calls."""

    def complete(self, *, system, messages, tools):
        asked = messages[0]["content"] if messages and isinstance(messages[0]["content"], str) else ""
        text = ("AI drafting is in offline stub mode: set GEMINI_API_KEY to enable live "
                "drafting with tool calls. Requested: " + asked[:280])
        block = {"type": "text", "text": text}
        return {"stop_reason": "end_turn", "blocks": [block], "assistant_content": [block]}


def choose_assist_client():
    """Use Gemini when configured, otherwise an explicit offline stub."""
    key = os.environ.get("GEMINI_API_KEY")
    return GeminiClient(key) if key else StubClient()


class Assistant:
    def __init__(self, app, *, client, discovery=None, max_steps=MAX_STEPS):
        self.app, self.client, self.discovery, self.max_steps = app, client, discovery, max_steps

    def _run_tool(self, name, arguments, identity):
        if not isinstance(arguments, dict):
            raise Rejected("tool arguments must be an object")
        if name == "discover_providers":
            vendors = [{"name": v} for v in arguments.get("approved_vendors", [])]
            return discover_providers({"query": arguments["need_query"]}, approved_vendors=vendors,
                                      client=self.discovery, now=self.app.clock(),
                                      blocked=arguments.get("blocked"), sanctioned=arguments.get("sanctioned"))
        if name == "get_case_evidence":
            case = self.app.store.read("cases", arguments["case_id"])
            if not case:
                return {"error": "case not found"}
            receipts = []
            for rid in case.get("receipt_ids", []):
                receipt = self.app.store.read("receipts", rid)
                if receipt:
                    receipts.append({"id": receipt["id"], "sha256": receipt["sha256"],
                                     "authority": receipt["authority"], "max_age": receipt["max_age"],
                                     "temperature": receipt["body"].get("temperature"),
                                     "observed_at": receipt["body"].get("observed_at")})
            return {"case": {"id": case["id"], "asset_id": case["asset_id"], "state": case["state"],
                             "title": case.get("title")}, "receipts": receipts}
        if name == "get_recent_tenders":
            limit = arguments.get("limit", 5)
            if not isinstance(limit, int) or limit <= 0:
                limit = 5
            records = self.app.store.list("tenders")[-min(limit, 100):]
            return {"tenders": [{"id": t["id"], "kind": t.get("kind"),
                                 "recommendation": t.get("recommendation"),
                                 "lead_count": len(t.get("leads", [])),
                                 "ranked_count": len(t.get("ranked", []))} for t in records]}
        raise Rejected("tool not available to the assistant: " + str(name))

    def run(self, *, prompt, task=None, identity):
        if not isinstance(prompt, str) or not prompt.strip() or len(prompt) > 16000:
            raise Rejected("prompt must contain between 1 and 16000 characters")
        if task is not None and (not isinstance(task, str) or len(task) > 200):
            raise Rejected("task must be a short string")
        system = SYSTEM + (("\n\nCurrent task: " + task) if task else "")
        messages = [{"role": "user", "content": prompt}]
        tools_used, evidence, reply = [], [], ""
        for _ in range(self.max_steps):
            step = self.client.complete(system=system, messages=messages, tools=TOOLS)
            messages.append({"role": "assistant", "content": step["assistant_content"]})
            reply = "".join(b["text"] for b in step["blocks"] if b["type"] == "text") or reply
            calls = [b for b in step["blocks"] if b["type"] == "tool_use"]
            if step["stop_reason"] != "tool_use" or not calls:
                break
            if len(calls) > 8:
                raise Rejected("too many tool calls in one assistant step")
            results = []
            for call in calls:
                tools_used.append(call["name"])
                try:
                    output = self._run_tool(call["name"], call["input"], identity)
                except (Rejected, KeyError, TypeError, ValueError):
                    output = {"error": "tool unavailable or invalid arguments"}
                evidence.append({"tool": call["name"], "output": output})
                results.append({"type": "tool_result", "tool_use_id": call["id"],
                                "content": json.dumps(output)})
            messages.append({"role": "user", "content": results})
        with self.app.store.transaction() as tx:
            tx.event("assist_drafted", {"by": identity.get("actor"), "task": task, "tools_used": tools_used},
                     self.app.clock())
        return {"reply": reply, "tools_used": tools_used, "evidence": evidence,
                "model": getattr(self.client, "model", "stub"), "status": "DRAFT_ONLY", "verification": "UNVERIFIED_MODEL_DRAFT"}

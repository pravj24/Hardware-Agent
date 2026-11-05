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

The loop is provider-neutral: a client only implements ``complete(system, messages,
tools)`` over the internal block format. Two live providers ship - Claude (official
Anthropic SDK, lazily imported) and Zhipu AI / GLM (OpenAI-compatible REST over stdlib
urllib, no SDK). ``choose_assist_client`` selects one from the environment; without any
key the endpoint runs a stub. The app, demo and default tests keep the repo's
stdlib-only, no-API-key invariant, and the real tool-calling loop is exercised by the
tests' scripted and fake-transport clients.
"""
import json
import os
import urllib.error
import urllib.request

from .discovery import discover_providers
from .errors import Rejected

MODEL = "claude-opus-5-5"
ZHIPU_MODEL = "glm-4.6"
ZHIPU_URL = "https://api.z.ai/api/paas/v4/chat/completions"
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
    """Offline client used when no ANTHROPIC_API_KEY is configured. No tool calls."""

    def complete(self, *, system, messages, tools):
        asked = messages[0]["content"] if messages and isinstance(messages[0]["content"], str) else ""
        text = ("AI drafting is in offline stub mode: set ANTHROPIC_API_KEY to enable live "
                "drafting with tool calls. Requested: " + asked[:280])
        block = {"type": "text", "text": text}
        return {"stop_reason": "end_turn", "blocks": [block], "assistant_content": [block]}


class ClaudeClient:
    """Live client. The Anthropic SDK is imported lazily to preserve the no-dependency
    default; a real call needs `pip install anthropic` and ANTHROPIC_API_KEY."""

    def __init__(self, *, model=MODEL, max_tokens=MAX_TOKENS):
        self.model, self.max_tokens = model, max_tokens

    def complete(self, *, system, messages, tools):
        try:
            import anthropic
        except ImportError as exc:
            raise Rejected("anthropic SDK not installed; run 'pip install anthropic' for live assist") from exc
        client = anthropic.Anthropic()  # resolves ANTHROPIC_API_KEY / ant profile
        response = client.messages.create(model=self.model, max_tokens=self.max_tokens,
                                          system=system, tools=tools, messages=messages)
        blocks = []
        for block in response.content:
            if block.type == "text":
                blocks.append({"type": "text", "text": block.text})
            elif block.type == "tool_use":
                blocks.append({"type": "tool_use", "id": block.id, "name": block.name, "input": block.input})
        # Append the SDK blocks unchanged (append-only; preserves any thinking blocks).
        return {"stop_reason": response.stop_reason, "blocks": blocks, "assistant_content": response.content}


class ZhipuClient:
    """Zhipu AI / GLM via its OpenAI-compatible chat-completions API, over stdlib urllib.

    Needs no SDK. Reads ZHIPU_API_KEY; ZHIPU_BASE_URL and ZHIPU_MODEL override the
    international defaults (set ZHIPU_BASE_URL to the open.bigmodel.cn endpoint for the
    China platform). Translates the internal block format to and from OpenAI tool calls.
    """

    def __init__(self, token, *, url=None, model=None, max_tokens=MAX_TOKENS, timeout=60, opener=None):
        if not token:
            raise Rejected("Zhipu API key required")
        self.token = token
        self.url = url or os.environ.get("ZHIPU_BASE_URL") or ZHIPU_URL
        self.model = model or os.environ.get("ZHIPU_MODEL") or ZHIPU_MODEL
        self.max_tokens, self.timeout = max_tokens, timeout
        self._opener = opener or urllib.request.urlopen

    @staticmethod
    def _to_openai(system, messages, tools):
        out = [{"role": "system", "content": system}]
        for message in messages:
            content = message["content"]
            if message["role"] == "user":
                if isinstance(content, str):
                    out.append({"role": "user", "content": content})
                else:  # tool_result blocks become separate role:"tool" messages
                    for block in content:
                        out.append({"role": "tool", "tool_call_id": block["tool_use_id"],
                                    "content": block["content"]})
            else:  # assistant: text + tool_use blocks
                text = "".join(b["text"] for b in content if b.get("type") == "text")
                calls = [{"id": b["id"], "type": "function",
                          "function": {"name": b["name"], "arguments": json.dumps(b["input"])}}
                         for b in content if b.get("type") == "tool_use"]
                entry = {"role": "assistant", "content": text or None}
                if calls:
                    entry["tool_calls"] = calls
                out.append(entry)
        functions = [{"type": "function", "function": {"name": t["name"], "description": t["description"],
                      "parameters": t["input_schema"]}} for t in tools]
        return out, functions

    def complete(self, *, system, messages, tools):
        openai_messages, functions = self._to_openai(system, messages, tools)
        body = json.dumps({"model": self.model, "messages": openai_messages, "tools": functions,
                           "tool_choice": "auto", "max_tokens": self.max_tokens}).encode()
        request = urllib.request.Request(self.url, data=body, method="POST", headers={
            "Content-Type": "application/json", "Authorization": "Bearer " + self.token})
        try:
            with self._opener(request, timeout=self.timeout) as response:
                payload = json.loads(response.read())
        except urllib.error.HTTPError as exc:
            detail = ""
            try:
                error = json.loads(exc.read()).get("error") or {}
                detail = " (" + str(error.get("code")) + ": " + str(error.get("message")) + ")"
            except (ValueError, TypeError, AttributeError):
                pass
            exc.close()  # release the wrapped socket (avoids a Windows ResourceWarning)
            raise Rejected("Zhipu request failed: HTTP " + str(exc.code) + detail) from exc
        except (urllib.error.URLError, TimeoutError, ValueError, OSError) as exc:
            raise Rejected("Zhipu request failed: " + str(exc)) from exc
        choices = payload.get("choices")
        if not choices:
            raise Rejected("Zhipu returned no choices")
        msg = choices[0].get("message", {})
        blocks = []
        if msg.get("content"):
            blocks.append({"type": "text", "text": msg["content"]})
        for call in msg.get("tool_calls") or []:
            arguments = call.get("function", {}).get("arguments", "{}")
            try:
                parsed = json.loads(arguments) if isinstance(arguments, str) else (arguments or {})
            except ValueError:
                parsed = {}
            blocks.append({"type": "tool_use", "id": call.get("id", "call_0"),
                           "name": call.get("function", {}).get("name"), "input": parsed})
        stop_reason = "tool_use" if any(b["type"] == "tool_use" for b in blocks) else "end_turn"
        return {"stop_reason": stop_reason, "blocks": blocks, "assistant_content": blocks}


def choose_assist_client():
    """Pick a provider from the environment: Zhipu, then Claude, else the offline stub."""
    if os.environ.get("ZHIPU_API_KEY"):
        return ZhipuClient(os.environ["ZHIPU_API_KEY"])
    if os.environ.get("ANTHROPIC_API_KEY"):
        return ClaudeClient()
    return StubClient()


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
            records = self.app.store.list("tenders")[-limit:]
            return {"tenders": [{"id": t["id"], "kind": t.get("kind"),
                                 "recommendation": t.get("recommendation"),
                                 "lead_count": len(t.get("leads", [])),
                                 "ranked_count": len(t.get("ranked", []))} for t in records]}
        raise Rejected("tool not available to the assistant: " + str(name))

    def run(self, *, prompt, task=None, identity):
        if not isinstance(prompt, str) or not prompt.strip():
            raise Rejected("prompt is required")
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
            results = []
            for call in calls:
                tools_used.append(call["name"])
                try:
                    output = self._run_tool(call["name"], call["input"], identity)
                except Rejected as exc:
                    output = {"error": str(exc)}
                evidence.append({"tool": call["name"], "output": output})
                results.append({"type": "tool_result", "tool_use_id": call["id"],
                                "content": json.dumps(output)})
            messages.append({"role": "user", "content": results})
        with self.app.store.transaction() as tx:
            tx.event("assist_drafted", {"by": identity.get("actor"), "task": task, "tools_used": tools_used},
                     self.app.clock())
        return {"reply": reply, "tools_used": tools_used, "evidence": evidence,
                "model": getattr(self.client, "model", "stub"), "status": "DRAFT_ONLY"}

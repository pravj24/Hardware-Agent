"""Gemini Developer API adapter. Credentials remain on the backend.

REST generateContent with bounded responses and native function-call translation.
The exact model content, including thought signatures, is preserved across turns.
"""
import copy
import json
import os
import re
import urllib.error
import urllib.request
import uuid

from .errors import Rejected

DEFAULT_MODEL = "gemini-3.8-flash"
API_ROOT = "https://generativelanguage.googleapis.com/v1beta/models/"


class NoRedirects(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise Rejected("Gemini redirect refused")


class GeminiClient:
    def __init__(self, token, *, model=None, max_tokens=4096, timeout=30, opener=None):
        if not token or not isinstance(token, str) or any(ord(c) < 33 or ord(c) > 126 for c in token):
            raise Rejected("valid Gemini API key required")
        self.token = token
        self.model = model or os.environ.get("GEMINI_MODEL") or DEFAULT_MODEL
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}", self.model):
            raise Rejected("invalid Gemini model identifier")
        self.url = API_ROOT + self.model + ":generateContent"
        self.max_tokens, self.timeout = max_tokens, timeout
        self._opener = opener or urllib.request.build_opener(NoRedirects()).open

    @staticmethod
    def _contents(messages):
        contents, bindings = [], {}
        for message in messages:
            content = message["content"]
            if message["role"] == "assistant":
                if not isinstance(content, dict) or "gemini_content" not in content:
                    raise Rejected("Gemini history is missing original model content")
                contents.append(copy.deepcopy(content["gemini_content"]))
                bindings.update(content.get("tool_bindings", {}))
            elif isinstance(content, str):
                contents.append({"role": "user", "parts": [{"text": content}]})
            else:
                parts = []
                for block in content:
                    binding = bindings.get(block["tool_use_id"])
                    if not binding:
                        raise Rejected("tool result has no matching Gemini call")
                    output = json.loads(block["content"])
                    if not isinstance(output, dict):
                        output = {"result": output}
                    function = {"name": binding["name"], "response": output}
                    if binding.get("google_id"):
                        function["id"] = binding["google_id"]
                    parts.append({"functionResponse": function})
                contents.append({"role": "user", "parts": parts})
        return contents

    def complete(self, *, system, messages, tools):
        payload = {"systemInstruction": {"parts": [{"text": system}]},
                   "contents": self._contents(messages),
                   "generationConfig": {"maxOutputTokens": self.max_tokens}}
        if tools:
            payload["tools"] = [{"functionDeclarations": [
                {"name": tool["name"], "description": tool["description"],
                 "parametersJsonSchema": tool["input_schema"]} for tool in tools]}]
        request = urllib.request.Request(self.url, method="POST", data=json.dumps(payload).encode(),
                                         headers={"Content-Type": "application/json", "x-goog-api-key": self.token})
        try:
            with self._opener(request, timeout=self.timeout) as response:
                raw = response.read(2_000_001)
            if len(raw) > 2_000_000:
                raise Rejected("Gemini response exceeded size limit")
            result = json.loads(raw)
        except urllib.error.HTTPError as exc:
            status = exc.code
            exc.close()
            raise Rejected(f"Gemini request failed: HTTP {status}; check backend key, model access or quota") from None
        except (urllib.error.URLError, TimeoutError, OSError, ValueError):
            raise Rejected("Gemini request failed or returned invalid JSON") from None
        if not isinstance(result, dict) or not isinstance(result.get("candidates"), list) or not result["candidates"]:
            raise Rejected("Gemini returned no usable candidate (possibly blocked)")
        candidate = result["candidates"][0]
        if not isinstance(candidate, dict) or candidate.get("finishReason") not in {"STOP", None}:
            raise Rejected("Gemini response was blocked, truncated or incomplete")
        content = candidate.get("content", {})
        if not isinstance(content, dict) or not isinstance(content.get("parts"), list):
            raise Rejected("Gemini returned malformed content")
        blocks, bindings = [], {}
        for part in content["parts"]:
            if not isinstance(part, dict):
                raise Rejected("Gemini returned malformed content part")
            if "functionCall" in part:
                function = part["functionCall"]
                if not isinstance(function, dict) or not isinstance(function.get("name"), str) or not isinstance(function.get("args", {}), dict):
                    raise Rejected("Gemini returned malformed tool arguments")
                if "id" in function and not isinstance(function["id"], str):
                    raise Rejected("Gemini returned malformed tool identifier")
                local_id = "gemini_" + uuid.uuid4().hex
                bindings[local_id] = {"name": function["name"], "google_id": function.get("id")}
                blocks.append({"type": "tool_use", "id": local_id, "name": function["name"], "input": function.get("args", {})})
            elif isinstance(part.get("text"), str) and not part.get("thought"):
                blocks.append({"type": "text", "text": part["text"]})
        if not blocks or len(bindings) > 8:
            raise Rejected("Gemini returned no usable draft or too many tool calls")
        return {"stop_reason": "tool_use" if bindings else "end_turn", "blocks": blocks,
                "assistant_content": {"gemini_content": copy.deepcopy(content), "tool_bindings": bindings}}

"""Tiny OpenAI-compatible mock server used to exercise real Hermes hooks.

Test infrastructure only; never part of the published plugin. It records every
request to JSONL and returns deterministic replies so a real Hermes agent turn
can be driven without an external model or network access.

Scenario ``agent_hook``:
  1. write_file src/new.py
  2. final answer -> real pre_verify hook may nudge
  3. proof_verify tool call (if the nudge mentions the proof tool)
  4. final answer
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

LOG = Path(
    os.environ.get("MOCK_LLM_LOG")
    or (Path(tempfile.gettempdir()) / "mock_llm_requests.jsonl")
)
SCENARIO = os.environ.get("MOCK_SCENARIO", "simple")
STATE = {"calls": 0, "wrote": False, "proof_sent": False, "final": False, "nudged": False}
LOCK = threading.Lock()


def _completion(content="", tool_call=None, model="mock"):
    message = {"role": "assistant", "content": content}
    finish = "stop"
    if tool_call:
        message["content"] = None
        message["tool_calls"] = [
            {
                "id": "call_1",
                "type": "function",
                "function": {
                    "name": tool_call["name"],
                    "arguments": json.dumps(tool_call["arguments"]),
                },
            }
        ]
        finish = "tool_calls"
    return {
        "id": "chatcmpl-mock",
        "object": "chat.completion",
        "created": int(time.time()),
        "model": model,
        "choices": [{"index": 0, "message": message, "finish_reason": finish}],
        "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
    }


def _reply_for(body, call_number):
    messages = body.get("messages") or []
    text = "\n".join(str(m.get("content") or "") for m in messages if isinstance(m, dict))
    nudged = "[Completion Proof]" in text
    if nudged:
        STATE["nudged"] = True
    if SCENARIO == "simple":
        return _completion("pong")
    if SCENARIO == "agent_hook":
        if nudged and not STATE["proof_sent"]:
            STATE["proof_sent"] = True
            return _completion(tool_call={"name": "proof_verify", "arguments": {"check_ids": ["unit"]}})
        if not nudged and not STATE["wrote"]:
            STATE["wrote"] = True
            return _completion(
                tool_call={
                    "name": "write_file",
                    "arguments": {"path": "src/new.py", "content": "VALUE = 2\n"},
                }
            )
        return _completion("Verified: agent hook scenario complete.")
    return _completion("pong")


class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        pass

    def _send_json(self, payload, status=200):
        body = json.dumps(payload).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.path.rstrip("/").endswith("/models"):
            self._send_json({"object": "list", "data": [{"id": "mock", "object": "model"}]})
        else:
            self._send_json({"error": "not found"}, 404)

    def do_POST(self):
        length = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(length) if length else b"{}"
        try:
            body = json.loads(raw)
        except ValueError:
            body = {}
        with LOCK:
            STATE["calls"] += 1
            call_number = STATE["calls"]
            with LOG.open("a") as handle:
                handle.write(json.dumps({"n": call_number, "path": self.path, "body": body}) + "\n")
            if not self.path.rstrip("/").endswith("/chat/completions"):
                reply = {"model": "mock", "object": "model", "data": []}
            else:
                reply = _reply_for(body, call_number)
            nudged = STATE["nudged"]
            proof_sent = STATE["proof_sent"]
        if not self.path.rstrip("/").endswith("/chat/completions"):
            self._send_json(reply)
            return
        if body.get("stream"):
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Cache-Control", "no-cache")
            self.end_headers()
            message = reply["choices"][0]["message"]
            finish = reply["choices"][0]["finish_reason"]
            base = {
                "id": "chatcmpl-mock",
                "object": "chat.completion.chunk",
                "created": int(time.time()),
                "model": "mock",
            }
            if message.get("tool_calls"):
                tool_call = dict(message["tool_calls"][0])
                tool_call["index"] = 0
                first = dict(base)
                first["choices"] = [{
                    "index": 0,
                    "delta": {"role": "assistant", "content": None, "tool_calls": [tool_call]},
                    "finish_reason": None,
                }]
                self.wfile.write(b"data: " + json.dumps(first).encode() + b"\n\n")
                last = dict(base)
                last["choices"] = [{"index": 0, "delta": {}, "finish_reason": finish}]
                self.wfile.write(b"data: " + json.dumps(last).encode() + b"\n\n")
            else:
                first = dict(base)
                first["choices"] = [{
                    "index": 0,
                    "delta": {"role": "assistant", "content": message.get("content") or ""},
                    "finish_reason": None,
                }]
                self.wfile.write(b"data: " + json.dumps(first).encode() + b"\n\n")
                last = dict(base)
                last["choices"] = [{"index": 0, "delta": {}, "finish_reason": finish}]
                self.wfile.write(b"data: " + json.dumps(last).encode() + b"\n\n")
            self.wfile.write(b"data: [DONE]\n\n")
            self.wfile.flush()
            return
        reply["_mock"] = {"nudged": nudged, "proof_sent": proof_sent, "call": call_number}
        self._send_json(reply)


def main():
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 0
    server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    print(server.server_address[1], flush=True)
    server.serve_forever()


if __name__ == "__main__":
    main()

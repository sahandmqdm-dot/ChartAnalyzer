"""Minimal, dependency-free MCP stdio client for iran-crypto-mcp.

The adapter is deliberately read-only. It never exposes account or trading tools.
"""
from __future__ import annotations

import json
import os
import queue
from collections import deque
import shutil
import subprocess
import threading
import time
from typing import Any, Dict, Optional


class MCPError(RuntimeError):
    pass


def _child_environment(extra=None):
    """Build a minimal environment for the MCP subprocess; never inherit API secrets."""
    keep = ("PATH", "HOME", "USERPROFILE", "APPDATA", "LOCALAPPDATA", "SYSTEMROOT",
            "WINDIR", "TEMP", "TMP", "SSL_CERT_FILE", "SSL_CERT_DIR", "UV_CACHE_DIR")
    env = {key: os.environ[key] for key in keep if key in os.environ}
    env.update({str(k): str(v) for k, v in (extra or {}).items()
                if str(k) in ("IRAN_CRYPTO_MCP_PROXY",)})
    return env


class StdioMCPClient:
    def __init__(self, command: str = "uvx", args=None, timeout: float = 25.0, env=None):
        self.command = command
        self.args = list(args or ["iran-crypto-mcp"])
        self.timeout = min(120.0, max(3.0, float(timeout)))
        self.env = dict(env or {})
        if not isinstance(self.args, list) or len(self.args) > 32 or not all(isinstance(x, str) and chr(0) not in x for x in self.args):
            raise MCPError("MCP args must be a list of at most 32 plain strings")
        self.proc = None
        self._reader = None
        self._stderr_reader = None
        self._responses: Dict[int, queue.Queue] = {}
        self._lock = threading.RLock()
        self._init_lock = threading.RLock()
        self._write_lock = threading.Lock()
        self._next_id = 1
        self._tools = None
        self._stderr_tail = deque(maxlen=20)

    def _start(self):
        if self.proc is not None and self.proc.poll() is None:
            return
        if self.proc is not None:
            self._tools = None
        executable = shutil.which(self.command) or self.command
        # Do not pass provider/API credentials or the LAN access token to this child.
        env = _child_environment(self.env)
        try:
            self.proc = subprocess.Popen(
                [executable, *self.args], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                stderr=subprocess.PIPE, text=True, encoding="utf-8", errors="replace",
                bufsize=1, env=env, close_fds=True,
            )
        except FileNotFoundError as exc:
            raise MCPError(f"MCP command not found: {self.command}. Install uv/uvx or configure command path.") from exc
        self._reader = threading.Thread(target=self._read_stdout, daemon=True, name="iran-mcp-stdout")
        self._reader.start()
        self._stderr_reader = threading.Thread(target=self._drain_stderr, daemon=True, name="iran-mcp-stderr")
        self._stderr_reader.start()

    def _drain_stderr(self):
        try:
            for line in self.proc.stderr:
                # Keep a short diagnostic tail; never include market payloads.
                self._stderr_tail.append(line.rstrip()[:500])
        except Exception:
            pass

    def _read_stdout(self):
        try:
            for line in self.proc.stdout:
                if len(line) > 1_000_000:
                    self._stderr_tail.append("MCP response line exceeded 1 MB; connection closed")
                    break
                line = line.strip()
                if not line:
                    continue
                try:
                    msg = json.loads(line)
                except json.JSONDecodeError:
                    continue
                rid = msg.get("id")
                if rid is not None:
                    waiter = self._responses.get(rid)
                    if waiter is not None:
                        waiter.put(msg)
        finally:
            for waiter in list(self._responses.values()):
                waiter.put({"_transport_closed": True})

    def _request(self, method: str, params=None):
        self._start()
        with self._lock:
            rid = self._next_id
            self._next_id += 1
            waiter = queue.Queue(maxsize=1)
            self._responses[rid] = waiter
        message = {"jsonrpc": "2.0", "id": rid, "method": method}
        if params is not None:
            message["params"] = params
        try:
            with self._write_lock:
                self.proc.stdin.write(json.dumps(message, separators=(",", ":")) + "\n")
                self.proc.stdin.flush()
            try:
                response = waiter.get(timeout=self.timeout)
            except queue.Empty as exc:
                detail = " | ".join(list(self._stderr_tail)[-5:])
                raise MCPError(f"MCP request timed out: {method}" + (f": {detail}" if detail else "")) from exc
            if response.get("_transport_closed"):
                detail = " | ".join(list(self._stderr_tail)[-5:])
                code = self.proc.poll() if self.proc is not None else None
                raise MCPError(f"MCP server closed its stdio connection (exit={code})" + (f": {detail}" if detail else ""))
            if "error" in response:
                err = response["error"] or {}
                raise MCPError(str(err.get("message") or err))
            return response.get("result", {})
        finally:
            self._responses.pop(rid, None)

    def notify(self, method: str, params=None):
        self._start()
        msg = {"jsonrpc": "2.0", "method": method}
        if params is not None:
            msg["params"] = params
        with self._write_lock:
            self.proc.stdin.write(json.dumps(msg, separators=(",", ":")) + "\n")
            self.proc.stdin.flush()

    def initialize(self):
        with self._init_lock:
            if self._tools is not None:
                return
            self._request("initialize", {
                "protocolVersion": "2024-11-05",
                "capabilities": {},
                "clientInfo": {"name": "ChartAnalyzer", "version": "7.2.0"},
            })
            self.notify("notifications/initialized")
            listed = self._request("tools/list", {})
            self._tools = {x["name"]: x for x in listed.get("tools", []) if isinstance(x, dict) and x.get("name")}

    @property
    def tools(self):
        if self.proc is None or self.proc.poll() is not None:
            self._tools = None
        self.initialize()
        return self._tools

    def call_tool(self, name: str, arguments: Dict[str, Any]):
        tools = self.tools
        if name not in tools:
            raise MCPError(f"Tool not available from iran-crypto-mcp: {name}")
        schema = tools[name].get("inputSchema") or {}
        props = schema.get("properties") or {}
        required = schema.get("required") or []
        args = dict(arguments or {})
        unknown = set(args) - set(props)
        if unknown:
            raise MCPError(f"Unsupported arguments for {name}: {', '.join(sorted(unknown))}")
        missing = [k for k in required if k not in args]
        if missing:
            raise MCPError(f"Missing required arguments for {name}: {', '.join(missing)}")
        result = self._request("tools/call", {"name": name, "arguments": args})
        if result.get("isError"):
            content = result.get("content") or []
            raise MCPError("; ".join(str(x.get("text", x)) for x in content))
        content = result.get("content") or []
        texts = [x.get("text", "") for x in content if isinstance(x, dict) and x.get("type") == "text"]
        if len(texts) == 1:
            try:
                return json.loads(texts[0])
            except (TypeError, json.JSONDecodeError):
                return {"text": texts[0]}
        if texts:
            return {"text": "\n".join(texts)}
        return result.get("structuredContent", result)

    def close(self):
        proc, self.proc = self.proc, None
        self._tools = None
        if proc is not None and proc.poll() is None:
            try:
                proc.terminate()
                proc.wait(timeout=2)
            except Exception:
                try:
                    proc.kill()
                except Exception:
                    pass


__all__ = ["MCPError", "StdioMCPClient"]

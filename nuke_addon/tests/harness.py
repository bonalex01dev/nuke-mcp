"""NukeMCP test harness — two backends behind one small vocabulary.

The same scenario runs either:
  * against the **addon socket** (`--backend addon`, default): raw JSON over 127.0.0.1:54321 —
    no MCP server, no LLM, fastest loop, and it is exactly the payload the MCP tools send;
  * against the **MCP server** (`--backend mcp`): spawns `uv run nuke-mcp` over stdio and calls
    the real MCP tools (validates the tool layer: names, arguments, version gating).

Both expose the same calls: create_node / modify_node / get_node_info / get_script_info /
delete_node / connect_nodes / exec_python. `exec_python` returns the `result` variable of the
code, as documented for the addon's `execute_python`.
"""

from __future__ import annotations

import asyncio
import json
import socket
import time
from pathlib import Path

HOST = "127.0.0.1"
PORT = 54321
ROOT = Path(__file__).resolve().parents[2]  # repo root (nuke_addon/tests/harness.py)


class BackendError(RuntimeError):
    pass


def unwrap(payload):
    """Addon/MCP responses are {"status": ..., "result": ...}; return the inner result.

    Raises on an explicit error status so a scenario step fails loudly instead of
    silently working on a None.
    """
    if isinstance(payload, dict):
        if payload.get("status") == "error":
            raise BackendError(payload.get("error") or payload)
        if "result" in payload and len(payload) <= 3:
            return payload["result"]
    return payload


# --------------------------------------------------------------------------- addon socket

class AddonBackend:
    name = "addon"
    needs_nuke_dialog = False

    def __init__(self, host: str = HOST, port: int = PORT, timeout: float = 60.0):
        self.host, self.port, self.timeout = host, port, timeout
        self.handshake: dict = {}

    def connect(self) -> dict:
        """Handshake (also proves Nuke + the addon are up)."""
        s = socket.socket()
        s.settimeout(min(10.0, self.timeout))
        s.connect((self.host, self.port))
        f = s.makefile("r")
        self.handshake = json.loads(f.readline())
        s.close()
        return self.handshake

    def call(self, command: str, timeout: float | None = None, **params):
        """One addon command over a fresh connection (the addon serves one client at a time)."""
        s = socket.socket()
        s.settimeout(timeout or self.timeout)
        try:
            s.connect((self.host, self.port))
            f = s.makefile("r")
            self.handshake = json.loads(f.readline())
            s.sendall((json.dumps({"type": command, "params": params}) + "\n").encode())
            return unwrap(json.loads(f.readline()))
        except socket.timeout as e:
            raise BackendError(
                "no answer from the addon within %.0fs — Nuke's main thread is probably busy "
                "(modal dialog?) or the command is stuck" % (timeout or self.timeout)) from e
        finally:
            try:
                s.close()
            except OSError:
                pass

    # --- vocabulary shared with the MCP tools ---
    async def acall(self, command: str, **params):
        return await asyncio.to_thread(self.call, command, **params)

    async def create_node(self, node_class, name=None, knobs=None, position=None):
        p = {"node_class": node_class}
        if name is not None:
            p["name"] = name
        if knobs:
            p["knobs"] = knobs
        if position is not None:
            p["position"] = position
        return await self.acall("create_node", **p)

    async def modify_node(self, node_name, knobs):
        return await self.acall("modify_node", node_name=node_name, knobs=knobs)

    async def get_node_info(self, node_name):
        return await self.acall("get_node_info", node_name=node_name)

    async def get_script_info(self):
        return await self.acall("get_script_info")

    async def delete_node(self, node_name, confirm=True):
        return await self.acall("delete_node", node_name=node_name, confirm=confirm)

    async def connect_nodes(self, output_node, input_node, input_index=0):
        return await self.acall("connect_nodes", output_node=output_node,
                                input_node=input_node, input_index=input_index)

    async def exec_python(self, code: str):
        return await self.acall("execute_python", code=code, confirm=True)


# --------------------------------------------------------------------------- MCP server

class MCPBackend:
    """Drives the real MCP server (`uv run nuke-mcp`, stdio) with fastmcp's client."""

    name = "mcp"

    def __init__(self, cwd: Path = ROOT, timeout: float = 120.0):
        self.cwd, self.timeout = cwd, timeout
        self.handshake = {"transport": "uv run nuke-mcp (stdio)"}
        self._client = None

    async def __aenter__(self):
        try:
            from fastmcp import Client
            from fastmcp.client.transports import StdioTransport
        except ImportError as e:  # pragma: no cover - depends on the venv
            raise BackendError("fastmcp not available in this interpreter: %r" % (e,))
        transport = StdioTransport(command="uv", args=["--directory", str(self.cwd), "run",
                                                       "nuke-mcp"], cwd=str(self.cwd))
        self._client = Client(transport)
        await self._client.__aenter__()
        return self

    async def __aexit__(self, *exc):
        if self._client is not None:
            await self._client.__aexit__(*exc)

    async def connect(self) -> dict:
        """List the tools the server exposes (also proves the server started)."""
        tools = await self._client.list_tools()
        self.handshake["tools"] = len(tools)
        return self.handshake

    async def call(self, command: str, **params):
        res = await self._client.call_tool(command, params)
        if getattr(res, "is_error", False) or getattr(res, "isError", False):
            raise BackendError("MCP tool %s failed: %s" % (command, res))
        data = getattr(res, "data", None)
        if isinstance(data, dict):
            return unwrap(data)
        for item in getattr(res, "content", None) or []:
            txt = getattr(item, "text", None)
            if txt:
                try:
                    return unwrap(json.loads(txt))
                except json.JSONDecodeError:
                    return {"raw": txt}
        return {"raw": str(res)}

    # --- same vocabulary as AddonBackend ---
    async def acall(self, command: str, **params):
        return await self.call(command, **params)

    async def create_node(self, node_class, name=None, knobs=None, position=None):
        return await self.call("create_node", node_class=node_class, name=name, knobs=knobs,
                               position=position)

    async def modify_node(self, node_name, knobs):
        return await self.call("modify_node", node_name=node_name, knobs=knobs)

    async def get_node_info(self, node_name):
        return await self.call("get_node_info", node_name=node_name)

    async def get_script_info(self):
        return await self.call("get_script_info")

    async def delete_node(self, node_name, confirm=True):
        return await self.call("delete_node", node_name=node_name, confirm=confirm)

    async def connect_nodes(self, output_node, input_node, input_index=0):
        return await self.call("connect_nodes", output_node=output_node,
                               input_node=input_node, input_index=input_index)

    async def exec_python(self, code: str):
        return await self.call("execute_python", code=code, confirm=True)


# --------------------------------------------------------------------------- session/step API

class Session:
    """Records one PASS/FAIL line per step, with evidence, and never hides a failure."""

    def __init__(self, api, verbose: bool = True):
        self.api = api
        self.verbose = verbose
        self.results: list[dict] = []

    async def step(self, label: str, call, verify=None, expect: str | None = None):
        """Run one step: `call` is a coroutine, `verify(result)` may raise on a bad result."""
        t0 = time.time()
        try:
            result = await call
            if verify is not None:
                verify(result)
            rec = {"step": label, "status": "PASS", "seconds": round(time.time() - t0, 2),
                   "evidence": _short(result)}
            if expect:
                rec["note"] = expect
        except Exception as e:  # noqa: BLE001 - a test runner reports, it does not crash
            rec = {"step": label, "status": "FAIL", "seconds": round(time.time() - t0, 2),
                   "error": "%s: %s" % (type(e).__name__, e)}
        self.results.append(rec)
        if self.verbose:
            line = "%-4s %-46s %6.2fs" % (rec["status"], label, rec["seconds"])
            print(line + ("" if rec["status"] == "PASS" else "\n      -> " + rec["error"]),
                  flush=True)
        return result if rec["status"] == "PASS" else None

    @property
    def failed(self) -> int:
        return sum(1 for r in self.results if r["status"] == "FAIL")

    def summary(self) -> dict:
        return {"steps": len(self.results), "failed": self.failed,
                "results": self.results}


def _short(obj, limit: int = 220) -> str:
    try:
        txt = json.dumps(obj, ensure_ascii=False, default=str)
    except Exception:
        txt = str(obj)
    return txt if len(txt) <= limit else txt[:limit] + "…"


def find_knob(info: dict, knob: str):
    """Node info shapes differ slightly between addon and server: look the knob up anywhere."""
    knobs = (info or {}).get("knobs") or {}
    if knob in knobs:
        return knobs[knob]
    for key, val in knobs.items():
        if key.lower() == knob.lower():
            return val
    return None

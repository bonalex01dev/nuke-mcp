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


def dismiss_nuke_dialogs(verbose: bool = False) -> int:
    """Fermer les popups de Nuke par WM_CLOSE (Win32) — sans focus, sans coordonnees.

    POURQUOI PAS EN PYTHON DANS NUKE : une compilation BlinkScript ratee ouvre une modale qui
    bloque le main thread ; tant qu'elle est ouverte, AUCUNE commande de l'addon n'aboutit (meme le
    handshake) et aucun garde-fou in-process ne peut la fermer — un QTimer ne tire pas, car Nuke
    n'itere pas la boucle d'evenements pendant ce blocage (mesure : 0 tick, journal par fichier).

    Le bouton OK n'est pas cliquable depuis Win32 (Qt dessine ses propres widgets, pas de fenetre
    enfant native), mais WM_CLOSE sur la modale la referme ET debloque Nuke. Les modales portent
    exactement le titre 'Nuke' ; la fenetre principale porte '<script> - Nuke'.
    """
    try:
        import ctypes
        from ctypes import wintypes
    except ImportError:  # pragma: no cover - Windows only
        return 0
    user32 = ctypes.WinDLL("user32", use_last_error=True)
    WM_CLOSE = 0x0010
    proc = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    found = []

    def _text(hwnd):
        n = user32.GetWindowTextLengthW(hwnd)
        buf = ctypes.create_unicode_buffer(n + 1)
        user32.GetWindowTextW(hwnd, buf, n + 1)
        return buf.value

    def _cb(hwnd, _):
        if user32.IsWindowVisible(hwnd) and _text(hwnd) == "Nuke":
            found.append(hwnd)
        return True

    user32.EnumWindows(proc(_cb), 0)
    for hwnd in found:
        user32.PostMessageW(hwnd, WM_CLOSE, 0, 0)
    if verbose and found:
        print("      (popup Nuke fermee par WM_CLOSE: %d)" % len(found), flush=True)
    return len(found)


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

    def send_only(self, command: str, **params):
        """Envoyer une commande SANS attendre la reponse, puis fermer la connexion.

        Pour les commandes qui peuvent ne jamais rendre la main : compiler un kernel BlinkScript en
        erreur ouvre une modale qui bloque Nuke. On envoie sans attendre, l'etape suivante ferme la
        popup de l'exterieur, puis on relit le rapport.
        """
        s = socket.socket()
        s.settimeout(10)
        try:
            s.connect((self.host, self.port))
            f = s.makefile("r")
            json.loads(f.readline())                       # handshake
            s.sendall((json.dumps({"type": command, "params": params}) + "\n").encode())
        finally:
            try:
                s.close()
            except OSError:
                pass
        return {"sent": command}

    async def run_and_heal(self, coro_factory, attempts: int = 2):
        """Executer un appel et, si Nuke ne repond plus, fermer la popup puis reessayer UNE fois.

        Cas vise : une compilation BlinkScript ratee laisse une modale ouverte qui bloque le main
        thread. On la ferme de l'exterieur (WM_CLOSE) au lieu de rester bloque jusqu'au timeout.
        """
        last = None
        for i in range(attempts):
            try:
                return await coro_factory()
            except BackendError as e:
                last = e
                if "no answer from the addon" not in str(e) or i == attempts - 1:
                    raise
                if not dismiss_nuke_dialogs(verbose=True):
                    raise
        raise last

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

    async def exec_python(self, code: str, timeout: float | None = None):
        """`timeout` (secondes) pour les etapes lentes — ex. une compilation BlinkScript qui ouvre
        une popup : la commande ne rend la main qu'une fois la popup fermee."""
        return await self.acall("execute_python", code=code, confirm=True, timeout=timeout)

    async def send_only_python(self, code: str):
        """`execute_python` non bloquant : pour les commandes qui peuvent ouvrir une modale.

        Coroutine comme les autres appels (Session.step l'attend), mais l'envoi lui-meme ne se
        bloque pas sur la reponse : la compilation peut ne jamais rendre la main.
        """
        return await asyncio.to_thread(self.send_only, "execute_python",
                                       code=code, confirm=True)


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

    async def exec_python(self, code: str, timeout: float | None = None):
        # le serveur MCP gere son propre timeout : on l'accepte pour garder la meme signature
        return await self.call("execute_python", code=code, confirm=True)

    async def send_only_python(self, code: str):
        raise BackendError("send_only_python n'existe pas en backend MCP : pas d'envoi non "
                           "bloquant sur stdio (utiliser --backend addon pour ce scenario)")


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

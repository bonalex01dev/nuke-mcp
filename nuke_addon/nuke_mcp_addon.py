"""NukeMCP Addon — Socket server running inside Nuke.

This file runs inside Nuke's Python environment. It creates a TCP socket server
that listens for JSON commands from the NukeMCP MCP server, executes them in
Nuke's main thread, and returns structured JSON responses.

No external dependencies beyond what Nuke ships with.

Usage:
    In Nuke's Script Editor or init.py/menu.py:
        import nuke_mcp_addon
        nuke_mcp_addon.start()
"""

import json
import logging
import os
import queue
import socket
import threading
import time

log = logging.getLogger("NukeMCP")

DEFAULT_PORT = 54321
ADDON_VERSION = "0.2.3-hermes"  # bump at each edit; shown in panel title, start() log, and handshake

# ---------------------------------------------------------------------------
# PySide import (PySide6 for Nuke 16+, PySide2 fallback)
# ---------------------------------------------------------------------------
try:
    from PySide6.QtWidgets import (
        QWidget,
        QVBoxLayout,
        QHBoxLayout,
        QLabel,
        QPushButton,
        QTextEdit,
    )
    from PySide6.QtCore import Qt, Signal, QObject
    from PySide6.QtGui import QTextCursor
except ImportError:
    from PySide2.QtWidgets import (
        QWidget,
        QVBoxLayout,
        QHBoxLayout,
        QLabel,
        QPushButton,
        QTextEdit,
    )
    from PySide2.QtCore import Qt, Signal, QObject
    from PySide2.QtGui import QTextCursor


# ---------------------------------------------------------------------------
# Command handlers
# ---------------------------------------------------------------------------

def _get_nuke():
    """Import nuke lazily so this module can be parsed outside Nuke for testing."""
    import nuke
    return nuke


def _handshake_data() -> dict:
    nuke = _get_nuke()
    version = nuke.NUKE_VERSION_STRING
    if nuke.env.get("studio"):
        variant = "NukeStudio"
    elif nuke.env.get("nukex"):
        variant = "NukeX"
    else:
        variant = "Nuke"

    return {
        "type": "handshake",
        "nuke_version": version,
        "variant": variant,
        "addon_version": ADDON_VERSION,
        "pid": __import__("os").getpid(),
    }


def _handle_ping(params: dict) -> dict:
    return {"status": "ok", "result": "pong"}


def _jsonable(value):
    """Valeur serialisable en JSON ; les objets Nuke passent par un attribut lisible.

    Trouve en test (Test01, BlinkScript) : le knob `format` renvoie un objet `nuke.Format` ->
    json.dumps leve TypeError dans _send -> le client ne recevait AUCUNE reponse (connexion
    fermee, 0 octet) et le log du panneau s'arretait sur le `<- get_node_info`.
    On tente un attribut parlant (`name`, puis `value`) avant de retomber sur str().
    """
    try:
        json.dumps(value)
        return value
    except (TypeError, ValueError):
        pass
    for attr in ("name", "value"):
        try:
            got = getattr(value, attr)
            got = got() if callable(got) else got
            json.dumps(got)
            return got
        except Exception:
            continue
    return str(value)


def _format_info(root):
    """Format du script en dict serialisable.

    Avant : str(root.format()) renvoyait "<_nuke.Format object at 0x...>" — inexploitable
    pour le client MCP (c'est cette valeur qui se retrouvait dans les snapshots memoire).
    """
    try:
        f = root.format()
        return {"name": f.name(), "width": f.width(), "height": f.height()}
    except Exception:
        return None


def _handle_get_script_info(params: dict) -> dict:
    """Info du script courant. Robuste quand AUCUN script n'est ouvert (root partiel)."""
    nuke = _get_nuke()
    root = nuke.root()
    out = {"name": "", "frame_range": None, "fps": None, "format": None,
           "colorspace": None, "node_count": 0}
    try:
        out["name"] = root.name() if root is not None else ""
        out["frame_range"] = [root["first_frame"].value(), root["last_frame"].value()]
        out["fps"] = root["fps"].value()
        out["format"] = _format_info(root)
        try:
            out["colorspace"] = root["colorManagement"].value()
        except Exception:
            out["colorspace"] = None
        out["node_count"] = len(nuke.allNodes())
    except Exception as e:
        return {"status": "error", "error": "get_script_info: %r" % (e,)}
    return {"status": "ok", "result": out}


def _handle_get_node_info(params: dict) -> dict:
    nuke = _get_nuke()
    name = params["node_name"]
    node = nuke.toNode(name)
    if not node:
        return {"status": "error", "error": f"Node '{name}' not found"}

    knobs = {}
    for knob_name in node.knobs():
        try:
            knobs[knob_name] = _jsonable(node[knob_name].value())
        except Exception:
            knobs[knob_name] = str(node[knob_name])

    inputs = []
    for i in range(node.inputs()):
        inp = node.input(i)
        inputs.append(inp.name() if inp else None)

    return {
        "status": "ok",
        "result": {
            "name": node.name(),
            "class": node.Class(),
            "xpos": node.xpos(),
            "ypos": node.ypos(),
            "inputs": inputs,
            "knobs": knobs,
        },
    }


def _handle_create_node(params: dict) -> dict:
    nuke = _get_nuke()
    node_class = params["node_class"]
    name = params.get("name")
    knobs = params.get("knobs", {})
    position = params.get("position")

    node = nuke.createNode(node_class, inpanel=False)
    if name:
        node.setName(name)
    for k, v in knobs.items():
        if k in node.knobs():
            node[k].setValue(v)
    if position:
        node.setXpos(int(position[0]))
        node.setYpos(int(position[1]))

    return {
        "status": "ok",
        "result": {
            "name": node.name(),
            "class": node.Class(),
            "xpos": node.xpos(),
            "ypos": node.ypos(),
        },
    }


def _handle_modify_node(params: dict) -> dict:
    nuke = _get_nuke()
    name = params["node_name"]
    knobs = params["knobs"]
    node = nuke.toNode(name)
    if not node:
        return {"status": "error", "error": f"Node '{name}' not found"}

    # Validate all knob names before modifying any
    node_knobs = node.knobs()
    for k in knobs:
        if k not in node_knobs:
            return {"status": "error", "error": f"Knob '{k}' not found on node '{name}'"}

    for k, v in knobs.items():
        node[k].setValue(v)

    return {"status": "ok", "result": {"name": node.name(), "modified_knobs": list(knobs.keys())}}


def _handle_delete_node(params: dict) -> dict:
    nuke = _get_nuke()
    name = params["node_name"]
    node = nuke.toNode(name)
    if not node:
        return {"status": "error", "error": f"Node '{name}' not found"}

    node_class = node.Class()
    nuke.delete(node)
    return {"status": "ok", "result": {"deleted": name, "class": node_class}}


def _handle_connect_nodes(params: dict) -> dict:
    nuke = _get_nuke()
    output_name = params["output_node"]
    input_name = params["input_node"]
    input_index = params.get("input_index", 0)

    output_node = nuke.toNode(output_name)
    input_node = nuke.toNode(input_name)
    if not output_node:
        return {"status": "error", "error": f"Node '{output_name}' not found"}
    if not input_node:
        return {"status": "error", "error": f"Node '{input_name}' not found"}

    input_node.setInput(input_index, output_node)
    return {
        "status": "ok",
        "result": {
            "output": output_node.name(),
            "input": input_node.name(),
            "input_index": input_index,
        },
    }


def _handle_position_node(params: dict) -> dict:
    nuke = _get_nuke()
    name = params["node_name"]
    node = nuke.toNode(name)
    if not node:
        return {"status": "error", "error": f"Node '{name}' not found"}

    node.setXpos(int(params["x"]))
    node.setYpos(int(params["y"]))
    return {"status": "ok", "result": {"name": node.name(), "xpos": node.xpos(), "ypos": node.ypos()}}


def _handle_auto_layout(params: dict) -> dict:
    nuke = _get_nuke()
    node_names = params.get("node_names")
    if node_names:
        nodes = [nuke.toNode(n) for n in node_names if nuke.toNode(n)]
    else:
        nodes = nuke.allNodes()

    if not nodes:
        return {"status": "error", "error": "No nodes to layout"}

    for n in nodes:
        nuke.autoplace(n)

    return {"status": "ok", "result": {"laid_out": len(nodes)}}


def _handle_execute_python(params: dict) -> dict:
    code = params["code"]
    local_vars = {}
    try:
        exec(code, {"nuke": _get_nuke(), "__builtins__": __builtins__}, local_vars)  # noqa: S102
    except Exception as e:
        return {"status": "error", "error": f"{type(e).__name__}: {e}"}
    # Return whatever was assigned to 'result' in the executed code, or None
    result = local_vars.get("result")
    # Try to serialize; fall back to str
    try:
        json.dumps(result)
    except (TypeError, ValueError):
        result = str(result)
    return {"status": "ok", "result": result}


def _handle_load_script(params: dict) -> dict:
    nuke = _get_nuke()
    path = params["path"]
    nuke.scriptOpen(path)
    return {"status": "ok", "result": {"loaded": path, "node_count": len(nuke.allNodes())}}


def _handle_save_script(params: dict) -> dict:
    nuke = _get_nuke()
    path = params.get("path")
    if path:
        nuke.scriptSaveAs(path)
    else:
        nuke.scriptSave()
        path = nuke.root().name()
    return {"status": "ok", "result": {"saved": path}}


def _handle_set_project_settings(params: dict) -> dict:
    nuke = _get_nuke()
    root = nuke.root()
    modified = []
    if "fps" in params:
        root["fps"].setValue(float(params["fps"]))
        modified.append("fps")
    if "colorspace" in params:
        root["colorManagement"].setValue(params["colorspace"])
        modified.append("colorspace")
    if "resolution" in params:
        w, h = params["resolution"]
        fmt = nuke.addFormat(f"{w} {h} custom")
        root["format"].setValue(fmt)
        modified.append("resolution")
    return {"status": "ok", "result": {"modified": modified}}


def _handle_set_frame_range(params: dict) -> dict:
    nuke = _get_nuke()
    first = int(params["first"])
    last = int(params["last"])
    if first > last:
        return {"status": "error", "error": f"first ({first}) must be <= last ({last})"}
    root = nuke.root()
    root["first_frame"].setValue(first)
    root["last_frame"].setValue(last)
    return {
        "status": "ok",
        "result": {"first_frame": first, "last_frame": last},
    }



def _handle_render_frames(params: dict) -> dict:
    nuke = _get_nuke()
    write_node = params["write_node"]
    node = nuke.toNode(write_node)
    if not node:
        return {"status": "error", "error": f"Node '{write_node}' not found"}
    first = params.get("first_frame", nuke.root()["first_frame"].value())
    last = params.get("last_frame", nuke.root()["last_frame"].value())
    nuke.execute(node, int(first), int(last))
    return {"status": "ok", "result": {"rendered": write_node, "first_frame": first, "last_frame": last}}


def _handle_set_proxy_mode(params: dict) -> dict:
    nuke = _get_nuke()
    enabled = params["enabled"]
    nuke.root()["proxy"].setValue(enabled)
    return {"status": "ok", "result": {"proxy_mode": enabled}}


def _handle_find_nodes_by_type(params: dict) -> dict:
    nuke = _get_nuke()
    node_class = params["node_class"]
    if node_class == "*":
        nodes = nuke.allNodes()
    else:
        nodes = nuke.allNodes(node_class)
    return {
        "status": "ok",
        "result": {
            "nodes": [{"name": n.name(), "class": n.Class()} for n in nodes],
        },
    }


def _handle_find_broken_reads(params: dict) -> dict:
    nuke = _get_nuke()
    broken = []
    for node in nuke.allNodes("Read"):
        file_path = node["file"].value()
        if not file_path or node.hasError():
            broken.append({"name": node.name(), "file": file_path})
    return {"status": "ok", "result": {"broken_reads": broken}}


def _handle_find_error_nodes(params: dict) -> dict:
    nuke = _get_nuke()
    errors = []
    for node in nuke.allNodes():
        if node.hasError():
            errors.append({"name": node.name(), "error": node.error()})
    return {"status": "ok", "result": {"nodes": errors}}


def _handle_batch_set_knob(params: dict) -> dict:
    nuke = _get_nuke()
    node_names = params["node_names"]
    knob_name = params["knob_name"]
    value = params["value"]
    modified = []
    for name in node_names:
        node = nuke.toNode(name)
        if node and knob_name in node.knobs():
            node[knob_name].setValue(value)
            modified.append(name)
    return {"status": "ok", "result": {"modified": modified, "knob": knob_name}}


def _handle_batch_reconnect(params: dict) -> dict:
    nuke = _get_nuke()
    node_names = params["node_names"]
    new_input = params["new_input"]
    input_index = params.get("input_index", 0)
    source = nuke.toNode(new_input)
    if not source:
        return {"status": "error", "error": f"Node '{new_input}' not found"}
    reconnected = []
    for name in node_names:
        node = nuke.toNode(name)
        if node:
            node.setInput(input_index, source)
            reconnected.append(name)
    return {"status": "ok", "result": {"reconnected": reconnected}}


def _handle_list_toolsets(params: dict) -> dict:
    import os
    toolset_dir = os.path.join(os.path.expanduser("~"), ".nuke", "ToolSets")
    toolsets = []
    if os.path.isdir(toolset_dir):
        for f in os.listdir(toolset_dir):
            if f.endswith(".nk"):
                toolsets.append(f[:-3])
    return {"status": "ok", "result": {"toolsets": toolsets}}


def _handle_load_toolset(params: dict) -> dict:
    nuke = _get_nuke()
    name = params["name"]
    nuke.loadToolset(name)
    return {"status": "ok", "result": {"loaded": name}}


def _handle_save_toolset(params: dict) -> dict:
    nuke = _get_nuke()
    name = params["name"]
    node_names = params["node_names"]
    nodes = [nuke.toNode(n) for n in node_names if nuke.toNode(n)]
    if not nodes:
        return {"status": "error", "error": "No valid nodes to save"}
    for n in nuke.allNodes():
        n.setSelected(False)
    for n in nodes:
        n.setSelected(True)
    nuke.saveToolset(name)
    return {"status": "ok", "result": {"saved": name, "nodes": node_names}}


def _handle_create_live_group(params: dict) -> dict:
    nuke = _get_nuke()
    name = params["name"]
    node_names = params["node_names"]
    nodes = [nuke.toNode(n) for n in node_names if nuke.toNode(n)]
    if not nodes:
        return {"status": "error", "error": "No valid nodes for LiveGroup"}
    for n in nuke.allNodes():
        n.setSelected(False)
    for n in nodes:
        n.setSelected(True)
    lg = nuke.createNode("LiveGroup", inpanel=False)
    lg.setName(name)
    if params.get("file_path"):
        lg["file"].setValue(params["file_path"])
    return {"status": "ok", "result": {"name": lg.name(), "nodes": node_names}}



def _handle_create_tracker(params: dict) -> dict:
    nuke = _get_nuke()
    source = params["source_node"]
    name = params.get("name", "tracker")
    source_node = nuke.toNode(source)
    if not source_node:
        return {"status": "error", "error": f"Node '{source}' not found"}
    tracker = nuke.createNode("Tracker4", inpanel=False)
    tracker.setName(name)
    tracker.setInput(0, source_node)
    return {"status": "ok", "result": {"name": tracker.name(), "class": "Tracker4", "source": source}}


def _handle_solve_tracker(params: dict) -> dict:
    nuke = _get_nuke()
    tracker_name = params["tracker_node"]
    node = nuke.toNode(tracker_name)
    if not node:
        return {"status": "error", "error": f"Node '{tracker_name}' not found"}
    first = params.get("first_frame", nuke.root()["first_frame"].value())
    last = params.get("last_frame", nuke.root()["last_frame"].value())
    # Execute tracking via nuke.execute on the Tracker node
    nuke.execute(node, int(first), int(last))
    return {"status": "ok", "result": {"solved": tracker_name, "frames": [first, last]}}


def _handle_setup_stabilize(params: dict) -> dict:
    nuke = _get_nuke()
    source = params["source_node"]
    tracker = params["tracker_node"]
    name = params.get("name", "stabilize")
    source_node = nuke.toNode(source)
    tracker_node = nuke.toNode(tracker)
    if not source_node:
        return {"status": "error", "error": f"Node '{source}' not found"}
    if not tracker_node:
        return {"status": "error", "error": f"Node '{tracker}' not found"}
    # Use the Tracker4 node itself in stabilize mode rather than a separate node
    tracker_node["transform"].setValue("stabilize")
    tracker_node.setInput(0, source_node)
    return {"status": "ok", "result": {"name": tracker_node.name(), "source": source, "tracker": tracker}}


def _handle_create_camera_tracker(params: dict) -> dict:
    nuke = _get_nuke()
    source = params["source_node"]
    name = params.get("name", "camera_tracker")
    source_node = nuke.toNode(source)
    if not source_node:
        return {"status": "error", "error": f"Node '{source}' not found"}
    ct = nuke.createNode("CameraTracker", inpanel=False)
    ct.setName(name)
    ct.setInput(0, source_node)
    return {"status": "ok", "result": {"name": ct.name(), "class": "CameraTracker", "source": source}}


def _handle_train_copycat(params: dict) -> dict:
    nuke = _get_nuke()
    node_name = params["copycat_node"]
    epochs = params.get("epochs", 1000)
    node = nuke.toNode(node_name)
    if not node:
        return {"status": "error", "error": f"Node '{node_name}' not found"}
    node["numIterations"].setValue(epochs)
    node["trainButton"].execute()
    return {"status": "ok", "result": {"trained": node_name, "epochs": epochs}}


def _handle_create_annotation(params: dict) -> dict:
    nuke = _get_nuke()
    text = params["text"]
    name = params.get("name", "annotation")
    node = nuke.createNode("StickyNote", inpanel=False)
    node.setName(name)
    node["label"].setValue(text)
    if params.get("position"):
        node.setXpos(int(params["position"][0]))
        node.setYpos(int(params["position"][1]))
    if params.get("color"):
        r, g, b = params["color"]
        # Nuke tile_color is hex int
        color_int = int(r * 255) << 24 | int(g * 255) << 16 | int(b * 255) << 8 | 255
        node["tile_color"].setValue(color_int)
    return {"status": "ok", "result": {"name": node.name(), "text": text}}


def _handle_list_annotations(params: dict) -> dict:
    nuke = _get_nuke()
    annotations = []
    for node in nuke.allNodes("StickyNote"):
        annotations.append({
            "name": node.name(),
            "text": node["label"].value(),
        })
    return {"status": "ok", "result": {"annotations": annotations}}



_event_client_socket = None
_event_clients = set()  # TOUS les clients connectes (plusieurs en parallele)
_direct_dispatch_lock = threading.Lock()  # headless : un appel Nuke a la fois
_event_socket_lock = threading.Lock()
_registered_callbacks = set()


def _push_event(event_type: str, data: dict):
    """Push an event to EVERY connected client (several can be attached at once)."""
    event = {"type": "event", "event_type": event_type, "data": data}
    msg = json.dumps(event) + "\n"
    with _event_socket_lock:
        clients = list(_event_clients)
        if _event_client_socket is not None and _event_client_socket not in clients:
            clients.append(_event_client_socket)  # compat: socket historique
        for sock in clients:
            try:
                sock.sendall(msg.encode("utf-8"))
            except OSError:
                _event_clients.discard(sock)


def _on_node_created():
    nuke = _get_nuke()
    node = nuke.thisNode()
    _push_event("node_created", {"name": node.name(), "class": node.Class()})


def _on_node_destroyed():
    nuke = _get_nuke()
    node = nuke.thisNode()
    _push_event("node_deleted", {"name": node.name(), "class": node.Class()})


def _on_knob_changed():
    nuke = _get_nuke()
    node = nuke.thisNode()
    knob = nuke.thisKnob()
    if knob:
        _push_event("knob_changed", {
            "node": node.name(),
            "knob": knob.name(),
        })


def _on_script_loaded():
    _push_event("script_loaded", {})


def _on_script_saved():
    _push_event("script_saved", {})


def _handle_subscribe_events(params: dict) -> dict:
    global _registered_callbacks
    event_types = params.get("event_types", [])

    try:
        nuke = _get_nuke()

        for et in event_types:
            if et in _registered_callbacks:
                continue
            if et == "node_created":
                nuke.addOnCreate(_on_node_created)
            elif et == "node_deleted":
                nuke.addOnDestroy(_on_node_destroyed)
            elif et == "knob_changed":
                nuke.addKnobChanged(_on_knob_changed)
            elif et == "script_loaded":
                nuke.addOnScriptLoad(_on_script_loaded)
            elif et == "script_saved":
                nuke.addOnScriptSave(_on_script_saved)
            else:
                continue
            _registered_callbacks.add(et)
    except Exception:
        # Nuke not available (headless without callbacks) — acknowledge anyway
        pass

    return {"status": "ok", "result": {"subscribed": event_types}}


# Command dispatch table
COMMANDS = {
    "ping": _handle_ping,
    "get_script_info": _handle_get_script_info,
    "get_node_info": _handle_get_node_info,
    "create_node": _handle_create_node,
    "modify_node": _handle_modify_node,
    "delete_node": _handle_delete_node,
    "connect_nodes": _handle_connect_nodes,
    "position_node": _handle_position_node,
    "auto_layout": _handle_auto_layout,
    "execute_python": _handle_execute_python,
    "load_script": _handle_load_script,
    "save_script": _handle_save_script,
    "set_project_settings": _handle_set_project_settings,
    "set_frame_range": _handle_set_frame_range,
    "render_frames": _handle_render_frames,
    "set_proxy_mode": _handle_set_proxy_mode,
    "find_nodes_by_type": _handle_find_nodes_by_type,
    "find_broken_reads": _handle_find_broken_reads,
    "find_error_nodes": _handle_find_error_nodes,
    "batch_set_knob": _handle_batch_set_knob,
    "batch_reconnect": _handle_batch_reconnect,
    "list_toolsets": _handle_list_toolsets,
    "load_toolset": _handle_load_toolset,
    "save_toolset": _handle_save_toolset,
    "create_live_group": _handle_create_live_group,
    "create_tracker": _handle_create_tracker,
    "solve_tracker": _handle_solve_tracker,
    "setup_stabilize": _handle_setup_stabilize,
    "create_camera_tracker": _handle_create_camera_tracker,
    "train_copycat": _handle_train_copycat,
    "create_annotation": _handle_create_annotation,
    "list_annotations": _handle_list_annotations,
    "subscribe_events": _handle_subscribe_events,
}


# ---------------------------------------------------------------------------
# Socket server
# ---------------------------------------------------------------------------

class _Signaller(QObject):
    """Bridge for emitting Qt signals from the socket thread."""
    log_signal = Signal(str)


class NukeMCPServer:
    """TCP socket server that listens for JSON commands from the MCP server."""

    def __init__(self, port: int = DEFAULT_PORT):
        self.port = port
        self._server_sock: socket.socket | None = None
        self._running = False
        self._thread: threading.Thread | None = None
        self._main_queue: queue.Queue | None = None
        try:
            self._signaller = _Signaller()
        except RuntimeError:
            self._signaller = None

    @property
    def on_log(self):
        if self._signaller:
            return self._signaller.log_signal
        return None

    def start(self):
        if self._running:
            return
        self._running = True
        self._thread = threading.Thread(target=self._serve, daemon=True)
        self._thread.start()
        log.info("NukeMCP server started on port %d", self.port)

    def serve_forever(self):
        """Run the server with main-thread command dispatch (for headless mode).

        Starts the TCP listener in a background thread and processes nuke
        commands on the calling (main) thread. Blocks until stop() is called.
        """
        self._main_queue = queue.Queue()
        self.start()
        try:
            while self._running:
                try:
                    func, args, result_q = self._main_queue.get(timeout=0.1)
                except queue.Empty:
                    continue
                try:
                    result = func(*args)
                except Exception as e:
                    result = e
                result_q.put(result)
        except KeyboardInterrupt:
            pass
        finally:
            self._main_queue = None
            self.stop()

    def stop(self):
        self._running = False
        if self._server_sock:
            try:
                self._server_sock.close()
            except OSError:
                pass
            self._server_sock = None
        log.info("NukeMCP server stopped")

    def _serve(self):
        self._server_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._server_sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._server_sock.settimeout(1.0)
        self._server_sock.bind(("127.0.0.1", self.port))
        self._server_sock.listen(8)
        self._emit_log(f"Listening on port {self.port}")

        while self._running:
            try:
                client, addr = self._server_sock.accept()
            except socket.timeout:
                continue
            except OSError:
                break

            self._emit_log(f"Client connected from {addr[0]}:{addr[1]}")
            # UN THREAD PAR CLIENT. Sans ca, _handle_client boucle sur son client jusqu'a sa
            # deconnexion : un client persistant (le serveur MCP en tient un en permanence)
            # monopolisait la boucle accept et AUCUN autre client n'etait jamais servi
            # (requetes acceptees par TCP, jamais traitees -> tout time-out).
            threading.Thread(target=self._handle_client, args=(client,),
                             daemon=True).start()

    def _run_in_nuke(self, func, *args):
        """Call func in Nuke's main thread (GUI) and wait for its result."""
        nuke = _get_nuke()
        if nuke.GUI:
            # executeInMainThread is fire-and-forget (returns None);
            # WithResult blocks until the main thread returns the value.
            return nuke.executeInMainThreadWithResult(func, args=args)
        if self._main_queue is not None:
            result_q = queue.Queue()
            self._main_queue.put((func, args, result_q))
            result = result_q.get()
            if isinstance(result, Exception):
                raise result
            return result
        # Headless sans serve_forever() : plusieurs threads clients peuvent appeler
        # Nuke en meme temps -> on serialise (le GUI passe par la main thread).
        with _direct_dispatch_lock:
            return func(*args)

    def _handle_client(self, client: socket.socket):
        """Sert UN client, dans son propre thread. Boucle jusqu'a sa deconnexion."""
        global _event_client_socket
        client.settimeout(None)
        _event_client_socket = client
        _event_clients.add(client)
        try:
            self._serve_client(client)
        finally:
            _event_clients.discard(client)
            if _event_client_socket is client:
                _event_client_socket = None
            self._emit_log("Client disconnected")
            try:
                client.close()
            except OSError:
                pass

    def _serve_client(self, client: socket.socket):
        # Send handshake via the main thread (WithResult blocks for the dict)
        try:
            handshake = self._run_in_nuke(_handshake_data)
        except Exception as _e2:
            import traceback
            self._emit_log("HANDSHAKE ERROR: %s" % traceback.format_exc())
            handshake = None
        if not isinstance(handshake, dict):
            return
        self._send(client, handshake)

        buffer = b""
        while self._running:
            try:
                chunk = client.recv(1024 * 1024)
            except OSError:
                break
            if not chunk:
                break

            buffer += chunk
            while b"\n" in buffer:
                line, buffer = buffer.split(b"\n", 1)
                try:
                    command = json.loads(line.decode("utf-8"))
                except json.JSONDecodeError as e:
                    self._send(client, {"status": "error", "error": f"Invalid JSON: {e}"})
                    continue

                cmd_type = command.get("type", "")
                params = command.get("params", {})
                self._emit_log(f"<- {cmd_type}")

                handler = COMMANDS.get(cmd_type)
                if not handler:
                    response = {"status": "error", "error": f"Unknown command: {cmd_type}"}
                else:
                    try:
                        response = self._run_in_nuke(handler, params)
                    except Exception as e:
                        response = {"status": "error", "error": f"{type(e).__name__}: {e}"}
                    if not isinstance(response, dict):
                        response = {"status": "error",
                                    "error": "handler returned no response (main-thread call returned None)"}

                try:
                    self._send(client, response)
                except Exception as e:
                    # Une valeur non serialisable ne doit PAS couper la connexion en silence :
                    # le client recoit une erreur explicite et la raison est dans le log.
                    self._emit_log(f"!! reponse non serialisable ({type(e).__name__}: {e})")
                    self._send(client, {"status": "error",
                                        "error": f"response not serialisable: {type(e).__name__}: {e}"})
                self._emit_log(f"-> {response.get('status', '?')}")


    def _send(self, client: socket.socket, data: dict):
        msg = json.dumps(data) + "\n"
        try:
            client.sendall(msg.encode("utf-8"))
        except OSError:
            pass

    def _emit_log(self, msg: str):
        log.info(msg)
        if self._signaller:
            self._signaller.log_signal.emit(msg)


# ---------------------------------------------------------------------------
# Panel UI
# ---------------------------------------------------------------------------

class NukeMCPPanel(QWidget):
    """Panneau NukeMCP : bouton Start/Stop + log.

    AUCUN affichage d'etat n'est maintenu en continu : l'ancien label "Stopped" etait faux
    par construction (initialise en dur, rafraichi seulement au toggle) pour toute pane
    creee par Nuke apres le demarrage, et le log n'y etait branche que dans ce meme cas.
    Desormais l'etat n'est jamais *suppose* : il est MESURE au clic et ECRIT dans le log,
    qui est le seul canal de verite (et il est branche des la construction).
    """

    def __init__(self, port: int = DEFAULT_PORT, parent=None):
        super().__init__(parent)
        self.setWindowTitle("NukeMCP v%s" % ADDON_VERSION)
        self.setMinimumWidth(300)
        self.setMinimumHeight(200)

        _track_instance(self)
        self._port = port

        # Ligne unique : port + auto-start + bouton neutre (jamais de faux etat affiche)
        self._port_label = QLabel("Port: %d" % self._port)
        self._auto_btn = QPushButton("Start with Nuke: off")
        self._auto_btn.setCheckable(True)
        self._auto_btn.setChecked(_read_pref())
        self._auto_btn.setText(
            "Start with Nuke: " + ("on" if self._auto_btn.isChecked() else "off"))
        self._auto_btn.clicked.connect(self._toggle_autostart_pref)
        self._toggle_btn = QPushButton("Start/Stop")
        self._toggle_btn.clicked.connect(self.toggle_server)
        row = QHBoxLayout()
        row.addWidget(self._port_label)
        row.addStretch()
        row.addWidget(self._auto_btn)
        row.addWidget(self._toggle_btn)

        self._log = QTextEdit()
        self._log.setReadOnly(True)

        layout = QVBoxLayout()
        layout.addLayout(row)
        layout.addWidget(self._log)
        self.setLayout(layout)

        # Log branche DES la construction (une pane ouverte en cours de route doit
        # recevoir les evenements du serveur deja en marche), puis banniere d'etat MESURE.
        self._log_connected = False
        self._connect_log()
        self._append_log("NukeMCP v%s - port %d - etat mesure: %s"
                         % (ADDON_VERSION, self._port,
                            "running" if server_running() else "stopped"))
        self._append_log("Start/Stop = demarrer ou arreter ; 'Start with Nuke: on' = "
                         "demarrage automatique au lancement de Nuke")

    def _connect_log(self):
        """Branche le signal de log du serveur (une seule fois)."""
        if self._log_connected:
            return
        if _server_standalone is not None and _server_standalone.on_log is not None:
            try:
                _server_standalone.on_log.connect(self._append_log)
                self._log_connected = True
            except Exception as e:
                self._append_log("log serveur non branche: %r" % (e,))

    def toggle_server(self):
        """Start/stop puis ecrit l'etat REELLEMENT mesure (jamais un etat suppose)."""
        if server_running():
            stop()
        else:
            _start_standalone(self._port)
            self._connect_log()
        self.refresh()
        self._append_log("-> etat mesure: %s (port %d)"
                         % ("running" if server_running() else "stopped", self._port))
        return server_running()

    def _toggle_autostart_pref(self):
        val = self._auto_btn.isChecked()
        _write_pref(val)
        self._auto_btn.setText("Start with Nuke: " + ("on" if val else "off"))
        self._append_log("auto-start %s" % ("enabled" if val else "disabled"))

    def is_running(self):
        return server_running()

    def refresh(self):
        """Rebranche le log si le serveur a (re)demarre. Rien d'autre a synchroniser :
        aucun label d'etat ne peut se desynchroniser."""
        running = server_running()
        if running:
            self._connect_log()
        return running

    def _append_log(self, msg: str):
        self._log.append(msg)
        # Keep last 200 lines
        doc = self._log.document()
        if doc.blockCount() > 200:
            cursor = self._log.textCursor()
            # Use QTextCursor enum names compatible with both PySide2 and PySide6
            cursor.movePosition(QTextCursor.Start)
            cursor.movePosition(
                QTextCursor.Down, QTextCursor.KeepAnchor, doc.blockCount() - 200
            )
            cursor.removeSelectedText()


# ---------------------------------------------------------------------------
# Module-level convenience
# ---------------------------------------------------------------------------

_panel = None
_server_standalone = None
_server_registry = []  # TOUS les serveurs crees : jamais de serveur orphelin
_instances = []  # live NukeMCPPanel widgets, incl. pane-docked ones created by Nuke


def _track_instance(w):
    """Remember live panel widgets so start/stop can reach pane-docked ones."""
    global _instances
    _instances = [x for x in _instances if _qobj_alive(x)]
    if not any(_qobj_alive(x) and x is w for x in _instances):
        _instances.append(w)


def _qobj_alive(w):
    try:
        import shiboken6 as _sb
    except ImportError:
        try:
            import shiboken2 as _sb
        except ImportError:
            return True  # cannot check; assume alive
    try:
        return _sb.isValid(w)
    except Exception:
        return False

PANEL_NAME = "NukeMCP"
PANEL_ID = "uk.co.thefoundry.NukeMCP"
PREF_FILE = os.path.join(os.path.expanduser("~"), ".nuke", "nukemcp_prefs.json")


def _read_pref() -> bool:
    """Read 'start with MCP server active' from the external JSON prefs file."""
    try:
        with open(PREF_FILE, "r") as f:
            return bool(json.load(f).get("start_active", False))
    except Exception:
        return False


def _write_pref(value: bool):
    try:
        with open(PREF_FILE, "w") as f:
            json.dump({"start_active": bool(value)}, f)
    except Exception as e:
        _log_to_panels("prefs write error: %s" % e)


def _register_panel():
    """Register the widget as a dockable pane panel (window 'custom')."""
    nuke = _get_nuke()
    import nukescripts
    nukescripts.panels.registerWidgetAsPanel(
        # The widget string is embedded in a PyCustom_Knob expression evaluated
        # by Nuke where the module is not imported as a global name — use
        # __import__ so the knob never raises NameError.
        "__import__('nuke_mcp_addon').NukeMCPPanel", PANEL_NAME, PANEL_ID
    )
    try:
        nuke.menu("Pane").addCommand("%s/%s" % ("NukeMCP", PANEL_NAME), show_panel)
    except Exception:
        pass


def _live_panels():
    """Return live panel instances (pane-docked or standalone)."""
    global _instances
    _instances = [x for x in _instances if _qobj_alive(x)]
    return _instances


def _log_to_panels(msg: str):
    """Ecrit un message dans le log de TOUTES les panes vivantes (+ stdout + log fichier).

    Canal unique pour ce qui partait avant dans le vide : erreurs de demarrage, echec
    d'enregistrement du panel, erreur d'ecriture des prefs. Sans ca, un echec est invisible.
    """
    print("[NukeMCP] %s" % msg)
    try:
        log.info(msg)
    except Exception:
        pass
    for panel in _live_panels():
        try:
            panel._append_log(msg)
        except Exception:
            pass


def _port_in_use(port: int, timeout: float = 0.5, tries: int = 3) -> bool:
    """True si quelqu'un ecoute sur le port.

    PLUSIEURS tentatives : la boucle accept du serveur traite UN client a la fois, donc une
    connexion de controle peut etre refusee pendant qu'une requete est en cours d'execution.
    Un seul essai produisait de faux "port libre" (et un second serveur cree pour rien).
    """
    for i in range(max(1, tries)):
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=timeout):
                return True
        except OSError:
            if i < tries - 1:
                time.sleep(0.25)
    return False


def show_panel():
    """Show/reopen the dockable NukeMCP pane panel (via Pane menu command)."""
    global _panel
    panels = _live_panels()
    if panels:
        # Chaque appel est protege separement : un raise_() qui echoue ne doit pas faire
        # tomber dans le chemin "creation", qui ouvrait une SECONDE pane pour rien.
        for meth in ("show", "raise_", "setFocus"):
            try:
                getattr(panels[0], meth)()
            except Exception:
                pass
        return panels[0]
    # No live instance: reopen through the registered Pane menu command
    nuke = _get_nuke()
    try:
        menu = nuke.menu("Pane")
        for it in menu.items():
            if it.name() == PANEL_NAME:
                it.invoke()
                return True
    except Exception:
        pass
    # Fallback: plain widget window
    if _panel is None:
        _panel = NukeMCPPanel(port=DEFAULT_PORT)
    _panel.show()
    _panel.raise_()
    return _panel


def stop_server_object(srv) -> bool:
    """Arrete un serveur precis et libere son socket (utilise par stop() et par le menage)."""
    try:
        srv.stop()
        return True
    except Exception as e:
        _log_to_panels("stop error: %s" % e)
        return False


def _start_standalone(port: int = DEFAULT_PORT):
    """Demarre le serveur au niveau module, IDEMPOTENT et sans jamais perdre un serveur vivant.

    Ordre volontaire (chaque branche journalisee) :
      1. notre serveur tourne et le port repond           -> rien a faire
      2. notre serveur croit tourner mais rien n'ecoute   -> on l'arrete proprement (socket libere)
      3. le port est tenu par QUELQU'UN D'AUTRE (autre Nuke) -> on ne cree pas de second serveur
      4. sinon                                            -> creation + verification du bind
    L'ancien code ecrasait _server_standalone avec un nouvel objet quand le port etait occupe :
    le serveur vivant devenait orphelin (plus arretable) et l'etat affiche faux.
    """
    global _server_standalone
    busy = _port_in_use(port)
    ours = _server_standalone is not None and _server_standalone._running

    if ours and busy:
        return True
    if ours and not busy:
        _log_to_panels("serveur local vivant mais plus d'ecoute sur %d -> arret propre" % port)
        stop_server_object(_server_standalone)
        _server_standalone = None
    elif not ours and busy:
        _log_to_panels("port %d deja tenu par une autre instance de Nuke - serveur local NON demarre"
                       % port)
        return False

    try:
        srv = NukeMCPServer(port)
        srv.start()
        _server_standalone = srv
        _server_registry.append(srv)
    except Exception as e:
        _log_to_panels("start error: %s" % e)
        return False

    # Le bind se fait DANS le thread : on verifie l'ecoute reelle avant d'annoncer "running".
    for _ in range(25):
        if _port_in_use(port):
            _log_to_panels("server running on port %d" % port)
            return True
        time.sleep(0.1)
    _log_to_panels("start failed: rien n'ecoute sur le port %d" % port)
    stop_server_object(srv)
    if _server_standalone is srv:
        _server_standalone = None
    return False


def server_running() -> bool:
    """True si AU MOINS UN serveur cree par ce module est vivant (registre, pas la seule
    derniere reference : un serveur ne doit jamais pouvoir etre perdu de vue)."""
    return any(getattr(srv, "_running", False) for srv in _server_registry)


def start(port: int = DEFAULT_PORT):
    """Demarre le serveur NukeMCP ; en GUI seulement, ouvre la fenetre.

    GUI      : enregistre la pane (une fois), ouvre la fenetre, demarre le serveur.
    Headless : AUCUN Qt, aucun menu, aucun panel — juste le socket (nuke -t, render
               workers). Si le port est deja tenu par une autre instance de Nuke, on ne
               fait rien : une seule instance est pilotable a la fois, et c'est celle qui
               ecoute deja. Aucun bruit d'erreur dans les render workers.

    Retourne l'etat MESURE (True si le serveur tourne apres l'appel).
    """
    nuke = _get_nuke()
    if not getattr(nuke, "GUI", False):
        if _port_in_use(port):
            print("[NukeMCP] headless: port %d deja tenu par une autre instance - autostart ignore" % port)
            return False
        ok = _start_standalone(port)
        print("[NukeMCP] headless: serveur %s sur le port %d (dispatch direct dans le thread socket)"
              % ("demarre" if ok else "ECHEC au demarrage", port))
        return server_running()
    import os as _os
    _src = _os.path.abspath(__file__)
    _msg = "nuke_mcp_addon v%s from %s (module mtime: %s)" % (
        ADDON_VERSION, _src, _os.path.getmtime(_src))
    log.info(_msg)
    print("[NukeMCP]", _msg)
    _register_panel_once()
    show_panel()
    _start_standalone(port)
    _refresh_panels()
    return server_running()


def _refresh_panels():
    """Sync every live panel's UI with the module-level server state."""
    for panel in _live_panels():
        try:
            panel.refresh()
        except Exception:
            pass


def stop():
    """Arrete TOUS les serveurs crees par ce module. Sûr si deja arrete."""
    global _server_standalone
    stopped = False
    for srv in list(_server_registry):
        if getattr(srv, "_running", False):
            stopped = stop_server_object(srv) or stopped
    _server_standalone = None
    _refresh_panels()
    if stopped:
        _log_to_panels("server stopped")
    else:
        print("[NukeMCP] server was not running")
    return stopped


_panel_registered = False


def _register_panel_once():
    global _panel_registered
    if not _panel_registered:
        try:
            _register_panel()
            _panel_registered = True
        except Exception as e:
            _log_to_panels("panel registration failed: %s" % e)
            _panel_registered = True  # don't retry every call

# NukeMCP Hermes patch — addon files

## What goes where (user's machine)
| File in `nuke_addon/` | Destination |
|---|---|
| `nuke_mcp_addon.py` | `C:\Users\alexandre\.nuke\nuke_mcp_addon.py` (replaces upstream) |
| `menu.py` / `init.py` | **snippets only** — merge the marked block into the user's existing `~/.nuke/menu.py` and `init.py` (do NOT overwrite: they contain unrelated user plugins) |
| `tests/probe_headless.py` | test only — verifies the addon works with no GUI (`--nc -t`) |

Preference storage: external JSON `~/.nuke/nukemcp_prefs.json` (key `start_active`).
The Nuke preferences node does NOT persist dynamically-added knobs reliably in Nuke 17.1,
so the addon stores its "start with Nuke" flag in this file instead (same approach as Stamps' config file).
Toggle: panel button "Start with Nuke" (the addon never creates or edits Nuke preferences).

## Version history
- 0.0.4: fix `executeInMainThread` returning None in Nuke 17.1 GUI (handshake + command fallback).
- 0.0.5: pane-dockable panel via `nukescripts.panels.registerWidgetAsPanel`; robust module-level start/stop.
- 0.0.6: external JSON prefs; auto-start button in panel; `show_panel()` via live widget tracking
  (`shiboken6.isValid`) and Pane-menu invoke (Nuke 17.1 has no `nuke.showPanel`); `_start_standalone`
  fallback.
- 0.1.0: `executeInMainThreadWithResult` for handshake AND commands (adopts upstream PRs #1/#2/#4);
  the previous fire-and-forget workaround caused duplicate nodes and stale UI.
- 0.2.1:
  - **panel is now log-only**: the status label is gone (it was initialised to "Stopped" and only
    refreshed on toggle, so any pane Nuke created while the server ran displayed a false state);
    one neutral `Start/Stop` button; the log is connected at construction (not only in `refresh()`);
    every action writes the **measured** state (`-> etat mesure: running|stopped`), and all errors
    (start, panel registration, prefs write, stop) are routed into that log instead of stdout.
  - **headless works without touching Qt**: `start()` skips panel/menu registration when
    `nuke.GUI` is false, runs the socket server only, and does nothing if the port is already held
    by another instance (no error noise in render workers).
  - **no orphan server**: a `_server_registry` tracks every server created, `stop()` stops them all,
    and `_start_standalone` never overwrites the reference of a live server (that bug lost the
    handle on a running server and made `server_running()` report false while the port was served).
  - `listen(1)` → `listen(8)` and `_port_in_use()` retries: the single-client accept loop could
    refuse a probe connection, which looked like a free port and spawned a second server.
  - `show_panel()` no longer creates a duplicate pane when a live instance exists.
  - `get_script_info`: guarded when no script is open, and `format` is now a dict
    (`{name,width,height}`) instead of `str(root.format())`, which serialised a Python repr with a
    memory address.
  - `menu.py`: the "Show NukeMCP panel" entry was removed (it never worked — the pane is opened
    from the Pane menu) and the whole block is skipped when there is no GUI.
- 0.2.3:
  - **`get_node_info` ne coupe plus la connexion.** BlinkScript expose un knob `format` dont
    `.value()` est un objet `nuke.Format` : `json.dumps` levait `TypeError` dans `_send`, donc le
    client ne recevait AUCUNE reponse (0 octet, connexion fermee), le traceback partait sur la
    console du Script Editor et le log du panneau s'arretait sur `<- get_node_info` sans `-> ok`.
    Deux correctifs : `_jsonable()` (essaie `name`, puis `value`, avant `str`) sur chaque valeur de
    knob, et un filet dans `_serve_client` qui renvoie une erreur explicite + ecrit une ligne `!!`
    dans le log si une reponse reste non serialisable — plus jamais de client muet.
  - Tests : `test01` passe **10/10** (`nuke_addon/tests/run_tests.py --scenario test01`).
  - Notes relevees par les tests : `nuke.createNode` branche le nouveau node sur la **selection
    courante** (le blur s'accroche donc au checkerboard sans qu'aucune connexion ne soit demandee) ;
    sur ce build `nuke.activeViewer()` n'expose ni `setInput` ni `getInput` — on connecte par
    `nuke.connectViewer(1, node)` PUIS `Viewer.activateInput(1)` (l'ordre inverse leve
    `ValueError('Input is not connected.')`), et on relit la connexion sur
    `nuke.activeViewer().node().input(1)` (l'index 0 du node viewer reste vide).

- 0.2.2:
  - **Plusieurs clients en parallele.** `_serve` lance un thread par client accepte
    (`_handle_client` -> `_serve_client`). Avant, `_handle_client` bouclait sur SON client jusqu'a
    sa deconnexion : le serveur MCP tenant une connexion permanente, **tout autre client etait
    accepte par TCP mais jamais servi** (time-out partout, rien dans le log du panneau au-dela de
    la paire connected/disconnected). Le dispatch direct en headless est desormais serialise
    (`_direct_dispatch_lock`), sinon deux threads clients appelleraient Nuke en meme temps.
  - Les evenements (`_push_event`) sont diffuses a **tous** les clients connectes, plus seulement
    au dernier socket connecte.
- (0.2.0 was an intermediate revision during the 2026-09-16 session, superseded by 0.2.1.)

## TODO
Open work, decisions taken and ideas for this fork: see **`TODO.md`** at the repo root
(kept on this branch only).

## Gotcha: `execute_python` and comprehensions
`_handle_execute_python` runs `exec(code, {"nuke": ...}, local_vars)` with **separate** globals and
locals, so a module-level list comprehension cannot see the exec'd locals
(`NameError: name 'm' is not defined`). Wrap the payload in a function (`def _run(): ...`) — inside a
function body everything is a local and comprehensions work normally.

## Testing
- GUI, live instance: drive the addon socket on 127.0.0.1:54321 (handshake first, then
  `{"type":"execute_python","params":{"code":"..."}}` with a function-wrapped payload assigning
  `result`). `importlib.reload(nuke_mcp_addon)` picks up addon edits without restarting Nuke;
  after a reload the previously docked pane is orphaned, so reopen the pane (Pane > NukeMCP).
- Headless: `"C:/Program Files/Nuke17.1v1/Nuke17.1.exe" --nc -t nuke_addon/tests/probe_headless.py`
  (prints a `PROBE_JSON` line). NOTE: on this seat plain `-t` is refused by the licence
  ("render only" product not licensed) — `--nc` is what makes a headless run possible here, and the
  addon code path exercised is identical.

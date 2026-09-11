# NukeMCP Hermes patch — addon files

## What goes where (user's machine)
| File in `nuke_addon/` | Destination |
|---|---|
| `nuke_mcp_addon.py` | `C:\Users\alexandre\.nuke\nuke_mcp_addon.py` (replaces upstream) |
| `menu.py` / `init.py` | **snippets only** — merge the marked block into the user's existing `~/.nuke/menu.py` and `init.py` (do NOT overwrite: they contain unrelated user plugins) |
| `install.ps1` | helper that copies `nuke_mcp_addon.py` into `~/.nuke` and prints the merge instructions |

Preference storage: external JSON `~/.nuke/nukemcp_prefs.json` (key `start_active`).
The Nuke preferences node does NOT persist dynamically-added knobs reliably in Nuke 17.1,
so the addon stores its "start with Nuke" flag in this file instead (same approach as Stamps' config file).
Toggle: panel button "Start with Nuke" or menu Scripts > MCP Server > Toggle auto-start.

## Version history
- 0.0.4: fix `executeInMainThread` returning None in Nuke 17.1 GUI (handshake + command fallback).
- 0.0.5: pane-dockable panel via `nukescripts.panels.registerWidgetAsPanel`; robust module-level start/stop.
- 0.0.6: external JSON prefs; auto-start button in panel; `show_panel()` via live widget tracking
  (`shiboken6.isValid`) and Pane-menu invoke (Nuke 17.1 has no `nuke.showPanel`); `_start_standalone`
  fallback; verified end-to-end in Nuke 17.1v1 GUI `--nc` mode (autostart handshake + menu stop).

## Testing without GUI interaction
Run headless-ish GUI probes: `"C:/Program Files/Nuke17.1v1/Nuke17.1.exe" --nc <script>.py`
with a QTimer+QApplication quit at the end, writing results to a temp file. Menus are NOT
available in `-t` (terminal) mode (`RuntimeError: not in GUI mode`), but `--nc` full GUI mode
with auto-quit works and matches the user's license (non-commercial).
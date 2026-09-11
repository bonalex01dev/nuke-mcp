# NukeMCP — snippet for the user's ~/.nuke/init.py (Hermes integration).
# Merge the marked block into your existing init.py.
# --- BEGIN NukeMCP block ---
# Auto-start the MCP server if the preference (external JSON file) is active.

def _nukemcp_autostart():
    try:
        import nuke
        if not nuke.GUI:
            return  # render workers / terminal mode: no UI, no autostart
        import nuke_mcp_addon
        if nuke_mcp_addon._read_pref():
            nuke_mcp_addon.start()
    except Exception as e:
        print("[NukeMCP] autostart failed: %s" % e)


try:
    _nukemcp_autostart()
except Exception as e:
    print("[NukeMCP] init error: %s" % e)
# --- END NukeMCP block ---
# NukeMCP — snippet for the user's ~/.nuke/menu.py (Hermes integration).
# Merge the marked block into your existing menu.py.
# --- BEGIN NukeMCP block ---
nukemcp_mcp_menu = nuke.menu("Nuke").menu("Scripts").addMenu("MCP Server")
nukemcp_mcp_menu.addCommand(
    "Start MCP server",
    "import nuke_mcp_addon; nuke_mcp_addon.start()",
)
nukemcp_mcp_menu.addCommand(
    "Stop MCP server",
    "import nuke_mcp_addon; nuke_mcp_addon.stop()",
)
nukemcp_mcp_menu.addCommand(
    "Toggle auto-start on Nuke launch",
    "import nuke_mcp_addon; nuke_mcp_addon._write_pref(not nuke_mcp_addon._read_pref()); "
    "print('[NukeMCP] auto-start', 'enabled' if nuke_mcp_addon._read_pref() else 'disabled')",
)
nukemcp_mcp_menu.addCommand(
    "Show NukeMCP panel",
    "import nuke_mcp_addon; nuke_mcp_addon.show_panel()",
)

nukemcp_toolbar = nuke.menu("Nodes")
nukemcp_menu = nukemcp_toolbar.addMenu("NukeMCP")
nukemcp_menu.addCommand("Start Server", "import nuke_mcp_addon; nuke_mcp_addon.start()")
nukemcp_menu.addCommand("Stop Server", "import nuke_mcp_addon; nuke_mcp_addon.stop()")
# --- END NukeMCP block ---
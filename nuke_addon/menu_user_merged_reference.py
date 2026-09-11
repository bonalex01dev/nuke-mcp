# import stamps
# import W_hotbox, W_hotboxManager

nukeMenu=nuke.menu("Nuke")
menuBar=nukeMenu.addMenu("Scripts")
#menuBar = nuke.toolbar("Scripts")
# menuBar = nuke.menu("Scripts")

import Scripts.pasteToSelected as PTS
menuBar.addCommand('Edit/Paste To Selected', 'PTS.pasteToSelected()')

#nuke.load('C:/Users/alexandre/.nuke/Scripts/LockViewer.py')

# --- NukeMCP (ajout Hermes) ---
# Menu Scripts > MCP Server. Les callables importent le module a la volee:
# les chaines de commande de Nuke s'executent dans un contexte ou un simple
# "import x; x.y()" peut lever NameError.

def _nukemcp_start():
    import nuke_mcp_addon
    nuke_mcp_addon.start()

def _nukemcp_stop():
    import nuke_mcp_addon
    nuke_mcp_addon.stop()

def _nukemcp_show():
    import nuke_mcp_addon
    nuke_mcp_addon.show_panel()

nukemcp_mcp_menu = menuBar.addMenu("MCP Server")
nukemcp_mcp_menu.addCommand("Start MCP server", _nukemcp_start)
nukemcp_mcp_menu.addCommand("Stop MCP server", _nukemcp_stop)
nukemcp_mcp_menu.addCommand("Show NukeMCP panel", _nukemcp_show)

# Raccourci aussi dans le menu Nodes (conservé)
nukemcp_toolbar = nuke.menu("Nodes")
nukemcp_menu = nukemcp_toolbar.addMenu("NukeMCP")
nukemcp_menu.addCommand("Start Server", _nukemcp_start)
nukemcp_menu.addCommand("Stop Server", _nukemcp_stop)
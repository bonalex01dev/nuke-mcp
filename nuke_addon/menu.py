# --- NukeMCP (ajout Hermes) -------------------------------------------
# Menu Scripts > MCP Server : Start / Stop. Les callables importent le module a la volee :
# les chaines de commande de Nuke s'executent dans un contexte ou un simple
# "import x; x.y()" peut lever NameError.
# AUCUN menu n'est cree sans GUI (mode -t / render workers) : l'entree "Show panel" a ete
# retiree (elle ne fonctionnait pas) — la fenetre s'ouvre par le menu Pane > NukeMCP.

def _nukemcp_start():
    import nuke_mcp_addon
    nuke_mcp_addon.start()


def _nukemcp_stop():
    import nuke_mcp_addon
    nuke_mcp_addon.stop()


def _nukemcp_add_menus():
    scripts = nuke.menu("Nuke").addMenu("Scripts")  # sous-menu Scripts existant (idempotent)
    m = scripts.addMenu("MCP Server")
    m.addCommand("Start MCP server", _nukemcp_start)
    m.addCommand("Stop MCP server", _nukemcp_stop)
    n = nuke.menu("Nodes").addMenu("NukeMCP")
    n.addCommand("Start Server", _nukemcp_start)
    n.addCommand("Stop Server", _nukemcp_stop)


try:
    if getattr(nuke, "GUI", False):
        _nukemcp_add_menus()
    else:
        print("[NukeMCP] pas de GUI: aucun menu cree (serveur socket uniquement)")
except Exception as e:
    print("[NukeMCP] menu non cree: %s" % e)

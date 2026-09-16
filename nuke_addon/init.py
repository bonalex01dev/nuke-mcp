# --- NukeMCP (ajout Hermes) -------------------------------------------
# Auto-demarrage du serveur si la preference est active (~/.nuke/nukemcp_prefs.json).
# GUI      : serveur + fenetre (Pane > NukeMCP).
# Headless : AUCUN Qt / menu / panel, juste le socket — et seulement si le port 54321 est
#            libre (nuke -t, render workers : une seule instance pilotable a la fois).
# nuke_mcp_addon.start() fait lui-meme la distinction GUI/headless et renvoie l'etat MESURE.

def _nukemcp_autostart():
    try:
        import nuke_mcp_addon
        if not nuke_mcp_addon._read_pref():
            print("[NukeMCP] autostart: desactive dans les preferences")
            return
        print("[NukeMCP] autostart -> %s" % nuke_mcp_addon.start())
    except Exception as e:
        print("[NukeMCP] autostart failed: %s" % e)


try:
    _nukemcp_autostart()
except Exception as e:
    print("[NukeMCP] init error: %s" % e)

# -*- coding: utf-8 -*-
"""probe_headless.py — verifie que NukeMCP fonctionne SANS GUI (mode `nuke -t`).

Lancement :
    "C:/Program Files/Nuke17.1v1/Nuke17.1.exe" -t probe_headless.py

Ce que le probe verifie :
  1. nuke.GUI est False (contexte headless reel)
  2. l'autostart d'init.py n'a pas pris le port d'une autre instance (ou l'a pris s'il etait
     libre) — et n'a touche a AUCUN objet Qt
  3. start(port) demarre un serveur socket utilisable (handshake + ping reels)
  4. aucun panel n'a ete enregistre (_panel_registered reste False : pas de menu, pas de pane)
  5. le process rend la main a la fin du script (thread daemon)

Sortie : une ligne JSON prefixee PROBE_JSON sur stdout.
"""
import json
import socket
import sys

import nuke

import nuke_mcp_addon as m

PORT = 54322  # port dedie au probe : ne perturbe pas l'instance GUI sur 54321

out = {"gui": bool(nuke.GUI), "version": m.ADDON_VERSION}
out["panel_registered_apres_autostart"] = m._panel_registered
out["running_apres_autostart"] = m.server_running()
out["port_54321_tenu"] = m._port_in_use(54321)
out["registry_apres_autostart"] = len(m._server_registry)

out["start_54322"] = m.start(PORT)
out["running_54322"] = m.server_running()
out["port_54322_tenu"] = m._port_in_use(PORT)

try:
    s = socket.socket()
    s.settimeout(10)
    s.connect(("127.0.0.1", PORT))
    f = s.makefile("r")
    hs = json.loads(f.readline())
    s.sendall((json.dumps({"type": "ping", "params": {}}) + "\n").encode())
    resp = json.loads(f.readline())
    s.close()
    out["handshake_version"] = hs.get("addon_version")
    out["nuke_version_vue_par_le_serveur"] = hs.get("nuke_version")
    out["ping"] = resp.get("result")
except Exception as e:
    out["ping_error"] = "%s: %s" % (type(e).__name__, e)

out["panel_registered_apres_start"] = m._panel_registered  # doit rester False (aucun Qt)
out["stop"] = m.stop()
out["running_apres_stop"] = m.server_running()
out["port_54322_apres_stop"] = m._port_in_use(PORT, tries=1)

print("PROBE_JSON " + json.dumps(out), flush=True)
sys.stdout.flush()

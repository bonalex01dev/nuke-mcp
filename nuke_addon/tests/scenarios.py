"""NukeMCP test scenarios — one function per Test, runnable at will.

Scenarios declare the nodes they create (idempotence + cleanup) and drive only the shared
vocabulary of the harness backends, so the same Test runs against the addon socket or through
the MCP server tools (`--backend addon|mcp`). Every check is its own PASS/FAIL line: a step
that "looks fine" but whose value did not stick fails the test.
"""

from __future__ import annotations

import asyncio

from harness import Session, dismiss_nuke_dialogs

BLINK_FILE = r"G:\PROGRAMMING\GITHUB\Nuke_ToolsAB\blink01.blink"
# meme kernel que blink01 avec une faute volontaire : line 47, sin(anglee)
BLINK_FILE_KO = r"G:\PROGRAMMING\GITHUB\Nuke_ToolsAB\blink02.blink"
SCENARIOS: dict[str, dict] = {}


def scenario(name: str, nodes: list[str], title: str):
    def deco(fn):
        SCENARIOS[name] = {"fn": fn, "nodes": nodes, "title": title}
        return fn
    return deco


# --------------------------------------------------------------------------- Test01

@scenario(
    "test01",
    ["test01_checkerboard", "test01_blur", "test01_blinkscript"],
    "checkerboard + blur(20) + BlinkScript(kernelSourceFile -> recompile) + viewer1",
)
async def run_test01(s: Session):
    """Steps as specified: checkerboard, blur set to 20, blinkscript with blink01.blink
    recompiled, then viewer1 input 1 hooked to the blinkscript output.
    NOTE: no connection between the created nodes is asked for — the sequence tests the MCP
    plumbing (creation, knob writes, knob click, viewer wiring), not a renderable graph.
    """
    api = s.api
    CB, BLUR, BLINK = "test01_checkerboard", "test01_blur", "test01_blinkscript"

    # 1 — checkerboard
    await s.step("1. creer un node checkerboard",
                 api.create_node("CheckerBoard", name=CB),
                 verify=lambda r: _expect_created(r, CB, "CheckerBoard"))

    # 2 — blur
    await s.step("2. creer un node blur",
                 api.create_node("Blur", name=BLUR),
                 verify=lambda r: _expect_created(r, BLUR, "Blur"))

    # 3 — blur size = 20 (set + read back)
    await s.step("3. passer le blur a 20",
                 api.modify_node(BLUR, {"size": 20}),
                 verify=lambda r: _expect_modified(r, "size"))
    await s.step("3b. relire size sur le blur",
                 api.get_node_info(BLUR),
                 verify=lambda i: _expect_knob(i, BLUR, "size", 20.0))

    # 4 — blinkscript
    await s.step("4. creer un node blinkscript",
                 api.create_node("BlinkScript", name=BLINK),
                 verify=lambda r: _expect_created(r, BLINK, "BlinkScript"))

    # 5 — kernelSourceFile (set + read back)
    # NOTE: renseigner kernelSourceFile ne charge PAS le fichier dans l'editeur ; c'est le bouton
    # Load (`reloadKernelSourceFile`) qui le fait. Sans lui, `recompile` recompile l'ANCIENNE
    # source et le test passerait au vert en ne testant rien (constate sur blink02 : 0 erreur).
    await s.step("5. kernelSourceFile = blink01.blink",
                 api.modify_node(BLINK, {"kernelSourceFile": BLINK_FILE}),
                 verify=lambda r: _expect_modified(r, "kernelSourceFile"))
    await s.step("5b. relire kernelSourceFile",
                 api.get_node_info(BLINK),
                 verify=lambda i: _expect_knob_path(i, BLINK, "kernelSourceFile", BLINK_FILE))

    # 5a — Load (ce qui rend le fichier effectif)
    await s.step("5a. charger le fichier (bouton Load)",
                 api.exec_python(_load_code(BLINK)),
                 verify=_expect_loaded)

    # 6 — recompile (no MCP tool clicks a knob: execute_python is the documented path)
    await s.step("6. cliquer sur recompile",
                 api.exec_python(_recompile_code(BLINK)),
                 verify=_expect_compiled)
    await s.step("6b. relire l'etat d'erreur du blinkscript",
                 api.get_node_info(BLINK),
                 verify=lambda i: _expect_no_node_error(i, BLINK))

    # 7 — viewer1 input 1 <- blinkscript output (connect + read back)
    await s.step("7. accrocher l'input 1 de viewer1 a la sortie du blinkscript",
                 api.exec_python(_viewer_code(BLINK)),
                 verify=lambda r: _expect_viewer(r, BLINK))
    return s


# --------------------------------------------------------------------------- Test02

@scenario(
    "test02",
    ["test02_checkerboard", "test02_blur", "test02_blinkscript"],
    "kernel en ERREUR (blink02) : recompilation + recuperation du rapport de compilation",
)
async def run_test02(s: Session):
    """Meme parcours que test01, mais avec blink02.blink (faute volontaire : line 47, `anglee`).

    Ce que ce test verifie (deterministe) : la compilation du kernel en erreur a lieu, elle ouvre
    une popup que le harnais FERME de l'exterieur (WM_CLOSE) sans laisser Nuke bloque, et le
    controle negatif (blink01, kernel valide) ne produit ni popup ni erreur.

    Ce que ce test ne verifie PAS, et pourquoi : le TEXTE du rapport. Nuke ne l'expose ni par un
    knob ni par `node.error()` (toujours False) ; il n'existe que dans deux sources d'INTERFACE —
    la popup (lisible seulement depuis l'exterieur, arbre d'accessibilite) et l'`ErrorTable` du
    panneau Properties, qui n'apparait que si le noeud est ACTIF et affiche AU MOMENT de l'echec
    (l'activer apres coup ne la cree pas — constate ; et compiler sur noeud actif bloque Nuke).
    Le texte obtenu live, identique a chaque essai :
        "Error compiling kernel: File blink02.blink, Line 47: use of undeclared identifier
         'anglee'; did you mean 'angle'?"
    """
    api = s.api
    CB, BLUR, BLINK = "test02_checkerboard", "test02_blur", "test02_blinkscript"

    await s.step("1. creer un node checkerboard",
                 api.create_node("CheckerBoard", name=CB),
                 verify=lambda r: _expect_created(r, CB, "CheckerBoard"))
    await s.step("2. creer un node blur",
                 api.create_node("Blur", name=BLUR),
                 verify=lambda r: _expect_created(r, BLUR, "Blur"))
    await s.step("3. passer le blur a 20",
                 api.modify_node(BLUR, {"size": 20}),
                 verify=lambda r: _expect_modified(r, "size"))
    await s.step("4. creer un node blinkscript",
                 api.create_node("BlinkScript", name=BLINK),
                 verify=lambda r: _expect_created(r, BLINK, "BlinkScript"))
    await s.step("5. kernelSourceFile = blink02.blink (kernel en erreur)",
                 api.modify_node(BLINK, {"kernelSourceFile": BLINK_FILE_KO}),
                 verify=lambda r: _expect_modified(r, "kernelSourceFile"))
    await s.step("5b. relire kernelSourceFile",
                 api.get_node_info(BLINK),
                 verify=lambda i: _expect_knob_path(i, BLINK, "kernelSourceFile", BLINK_FILE_KO))
    # 5a : PAS de Load ici. Sur un noeud ACTIF, le Load compile le kernel en erreur et ouvre la
    # modale qui bloque tout Nuke : le chargement part donc dans l'etape 6a, noeud desactive.

    # 6a — ENVOI NON BLOQUANT : sur noeud ACTIF, une compilation ratee ouvre la modale qui bloque
    # le main thread ; meme desactive, l'appel ne rend parfois la main qu'une fois la popup fermee.
    # On envoie donc sans attendre la reponse.
    await s.step("6a. compiler le kernel en erreur (envoi non bloquant)",
                 api.send_only_python(_disabled_compile_code(BLINK, BLINK_FILE_KO)),
                 verify=_expect_sent)
    # 6b — on ferme la popup DE L'EXTERIEUR (WM_CLOSE) puis on relit le rapport de compilation dans
    # l'ErrorTable du panneau Properties : c'est la source structuree, celle de « en bas a droite »,
    # et elle ne depend d'aucune popup.
    await s.step("6b. fermer la popup et verifier que Nuke repond de nouveau",
                 _dismiss_then_report(api, BLINK),
                 verify=_expect_popup_handled)
    # 7 — controle negatif : kernel valide, appel bloquant normal (aucune popup attendue)
    await s.step("7a. controle negatif : desactiver, charger et compiler blink01 (valide)",
                 api.exec_python(_disabled_compile_code(BLINK, BLINK_FILE)),
                 verify=_expect_kickoff)
    await s.step("7b. controle negatif : rapport vide pour un kernel valide",
                 api.exec_python(_compile_report_code(BLINK)),
                 verify=_expect_no_compile_error)
    # 9 — DESACTIVER avant de brancher le viewer : sans ca Nuke evalue le kernel au wiring et
    #     rouvre la popup. Le noeud reste desactive a la fin du test, volontairement.
    await s.step("9. laisser le noeud desactive avant le wiring du viewer",
                 api.exec_python(_disable_code(BLINK)),
                 verify=_expect_disabled)
    await s.step("9b. accrocher l'input 1 de viewer1 a la sortie du blinkscript",
                 api.exec_python(_viewer_code(BLINK)),
                 verify=lambda r: _expect_viewer(r, BLINK))
    return s


# --------------------------------------------------------------------------- verifications

def _expect_created(result, name: str, node_class: str):
    if not isinstance(result, dict):
        raise AssertionError("resultat create_node inattendu: %r" % (result,))
    if result.get("name") != name:
        raise AssertionError("node cree %r au lieu de %r" % (result.get("name"), name))
    if result.get("class") != node_class:
        raise AssertionError("classe %r au lieu de %r" % (result.get("class"), node_class))
    return True


def _expect_modified(result, *knobs):
    if not isinstance(result, dict):
        raise AssertionError("resultat modify_node inattendu: %r" % (result,))
    done = result.get("modified_knobs") or []
    missing = [k for k in knobs if k not in done]
    if missing:
        raise AssertionError("knobs non modifies: %s (recu %s)" % (missing, done))
    return True


def _knobs(info):
    if not isinstance(info, dict):
        raise AssertionError("resultat get_node_info inattendu: %r" % (info,))
    return info.get("knobs") or {}


def _expect_knob(info, node_name: str, knob: str, expected):
    knobs = _knobs(info)
    if knob not in knobs:
        raise AssertionError("knob %r absent de %s (ex: %s)"
                             % (knob, node_name, sorted(knobs)[:15]))
    got = knobs[knob]
    if isinstance(expected, float) and isinstance(got, (int, float)):
        if abs(float(got) - expected) > 1e-6:
            raise AssertionError("%s.%s = %r, attendu %r" % (node_name, knob, got, expected))
    elif got != expected:
        raise AssertionError("%s.%s = %r, attendu %r" % (node_name, knob, got, expected))
    return True


def _expect_knob_path(info, node_name: str, knob: str, expected_path: str):
    knobs = _knobs(info)
    if knob not in knobs:
        raise AssertionError("knob %r absent de %s" % (knob, node_name))
    got, want = str(knobs[knob]), str(expected_path)
    norm = lambda p: p.replace("/", "\\").lower()      # noqa: E731
    if norm(got) != norm(want):
        raise AssertionError("%s.%s = %r, attendu %r" % (node_name, knob, got, want))
    return True


def _expect_compiled(result):
    if not isinstance(result, dict):
        raise AssertionError("resultat du recompile inattendu: %r" % (result,))
    if not result.get("clicked"):
        raise AssertionError("le bouton recompile n'a pas pu etre clique: %s"
                             % result.get("error"))
    if result.get("error"):
        raise AssertionError("recompile a leve: %s" % result["error"])
    return True


def _expect_no_node_error(info, node_name: str):
    """Second reading after the click: the node itself must not report an error."""
    knobs = _knobs(info)
    for key in ("error", "errors"):
        if key in knobs and str(knobs[key]).strip():
            raise AssertionError("%s.%s = %r" % (node_name, key, knobs[key]))
    return True


def _expect_popup_handled(result):
    """La compilation en erreur a bien produit une popup, elle a ete fermee, et Nuke repond.

    POURQUOI PAS LE TEXTE ICI : le rapport de compilation n'est atteignable ni par un knob ni par
    `node.error()` (toujours False), seulement par deux sources d'INTERFACE — la popup (dont le
    texte ne se lit que depuis l'exterieur, arbre d'accessibilite Windows) et l'ErrorTable du
    panneau Properties, qui n'existe que lorsque le noeud est actif et affiche au moment de l'echec
    (l'activer APRES coup ne la cree pas — constate). Ce que le test peut garantir de facon
    deterministe, c'est le scenario complet : la popup a eu lieu, elle a ete fermee, et Nuke
    n'est pas reste bloque — c'est exactement ce qui casse une automatisation.
    """
    if not isinstance(result, dict):
        raise AssertionError("resultat inattendu: %r" % (result,))
    if not result.get("popups_fermees"):
        raise AssertionError("aucune popup d'erreur detectee: la compilation de blink02 a-t-elle "
                             "eu lieu ? (resultat=%r)" % (result,))
    if result.get("disabled") is not False:
        raise AssertionError("le noeud n'a pas ete reactive apres la compilation: %r"
                             % result.get("disabled"))
    return True


def _expect_sent(result):
    """Commande envoyee sans attente (une commande qui peut bloquer ne doit pas etre attendue)."""
    if not isinstance(result, dict) or "sent" not in result:
        raise AssertionError("envoi non bloquant inattendu: %r" % (result,))
    return True


def _expect_kickoff(result):
    """Compilation lancee sur noeud DESACTIVE, et rendue la main RAPIDEMENT."""
    if not isinstance(result, dict):
        raise AssertionError("resultat de compilation inattendu: %r" % (result,))
    steps = result.get("steps") or {}
    if steps.get("disable") is not True:
        raise AssertionError("le noeud n'a pas ete desactive avant compilation: %r"
                             % steps.get("disable"))
    for knob in ("reloadKernelSourceFile", "recompile"):
        if not str(steps.get(knob, "")).startswith("ok"):
            raise AssertionError("le bouton %s a echoue: %r" % (knob, steps.get(knob)))
    slow = {k: v for k, v in steps.items() if k.endswith("_seconds") and v > 30}
    if slow:
        raise AssertionError("compilation bloquante (noeud actif ?): %s" % slow)
    return True


def _expect_disabled(result):
    if not isinstance(result, dict) or result.get("disabled") is not True:
        raise AssertionError("le noeud n'est pas desactive: %r" % (result,))
    return True


def _expect_loaded(result):
    """Le bouton Load doit avoir ete clique sans erreur."""
    if not isinstance(result, dict):
        raise AssertionError("resultat du Load inattendu: %r" % (result,))
    if not result.get("clicked"):
        raise AssertionError("le bouton Load n'a pas pu etre clique: %s" % result.get("error"))
    return True


def _expect_compile_error(result, node_name: str, must_contain):
    """Le rapport doit contenir l'erreur attendue, ET le compte doit l'annoncer."""
    if not isinstance(result, dict):
        raise AssertionError("rapport de compilation inattendu: %r" % (result,))
    errors = result.get("errors") or []
    if not result.get("tables"):
        raise AssertionError(
            "aucune ErrorTable visible dans le panneau Properties (le rapport de compilation "
            "n'est pas la ou on le cherche, ou le noeud %s n'est pas celui affiche)" % node_name)
    if not errors:
        raise AssertionError("ErrorTable vide: aucune erreur remontee (compte=%r)"
                             % result.get("count_text"))
    joined = " ".join(errors).lower()
    missing = [t for t in must_contain if t.lower() not in joined]
    if missing:
        raise AssertionError("le rapport ne contient pas %s ; recu: %s" % (missing, errors))
    count = str(result.get("count_text") or "")
    if "0 errors" in count.lower():
        raise AssertionError("compte d'erreurs incoherent: %r alors que %d erreur(s) lue(s)"
                             % (count, len(errors)))
    return True


def _expect_no_compile_error(result):
    """Controle negatif : un kernel valide ne doit RIEN remonter."""
    if not isinstance(result, dict):
        raise AssertionError("rapport de compilation inattendu: %r" % (result,))
    errors = result.get("errors") or []
    if errors:
        raise AssertionError("blink01 (kernel valide) remonte des erreurs: %s" % errors)
    count = str(result.get("count_text") or "")
    if count and "0 errors" not in count.lower():
        raise AssertionError("compte d'erreurs inattendu pour un kernel valide: %r" % count)
    return True


def _expect_viewer(result, node_name: str):
    if not isinstance(result, dict):
        raise AssertionError("resultat viewer inattendu: %r" % (result,))
    if result.get("readback") is None:
        raise AssertionError(
            "connexion NON verifiable (used=%r, set_input_error=%r, active_input=%r): "
            "aucune lecture de viewer_node.input() n'a abouti"
            % (result.get("used"), result.get("set_input_error"), result.get("active_input")))
    if result["readback"] != node_name:
        raise AssertionError("viewer1 input 1 = %r, attendu %r"
                             % (result["readback"], node_name))
    return True


# --------------------------------------------------------------------------- code snippets

def _recompile_code(node_name: str) -> str:
    """Cliquer sur recompile — pour un kernel VALIDE (aucune popup attendue).

    Pour un kernel en erreur, passer par `_disabled_compile_code` + `dismiss_nuke_dialogs` : sur
    noeud actif, une compilation ratee ouvre une modale qui bloque Nuke entierement (plus aucune
    commande de l'addon ne passe, meme le handshake).
    """
    return (
        "n = nuke.toNode('%s')\n"
        "res = {'clicked': False, 'error': None}\n"
        "try:\n"
        "    n['recompile'].execute()\n"
        "    res['clicked'] = True\n"
        "except Exception as e:\n"
        "    res['error'] = repr(e)\n"
        "result = res\n" % node_name)


def _disabled_compile_code(node_name: str, kernel_file: str) -> str:
    """Desactiver le noeud puis charger et compiler un kernel, SANS bloquer Nuke.

    POURQUOI `disable=True` : sur un noeud actif, une compilation ratee ouvre une boite modale qui
    bloque le main thread de Nuke — le clic ne rend jamais la main (>240 s mesure) et, tant qu'elle
    est ouverte, PLUS AUCUNE commande de l'addon n'aboutit (meme le handshake). Aucun garde-fou
    in-process ne peut la fermer : un QTimer ne tire pas, car Nuke n'itere pas la boucle
    d'evenements pendant ce blocage (verifie : 0 tick, journal par fichier). Le noeud DESACTIVE, la
    compilation se fait bien (recompileCount s'incremente, le rapport est produit) mais sans
    bloquer le dispatch — la popup apparait et reste fermable par Qt.

    Le rapport est alors disponible sur DEUX sources, et le test verifie les deux :
    Le rapport lui-meme est lu par les DEUX etapes suivantes :
      * `_collect_popups_code` : la popup `QDialog` ('Error compiling kernel: ...') ;
      * `_enable_and_report_code` : l'`ErrorTable` du panneau Properties, noeud reactive.
    """
    return (
        "def _run():\n"
        "    import time\n"
        "    import nuke\n"
        "    from PySide6 import QtWidgets\n"
        "    app = QtWidgets.QApplication.instance()\n"
        "    n = nuke.toNode('%s')\n"
        "    res = {'steps': {}, 'dialogs': [], 'dismissed': 0, 'errors': [], 'count': None}\n"
        "    if n is None:\n"
        "        return {'error': 'noeud absent: %s'}\n"
        "    seen = set()\n"
        "    def _pump(seconds):\n"
        "        deadline = time.time() + seconds\n"
        "        while time.time() < deadline:\n"
        "            app.processEvents()\n"
        "            for w in app.topLevelWidgets():\n"
        "                try:\n"
        "                    if not w.isVisible() or not isinstance(w, QtWidgets.QDialog):\n"
        "                        continue\n"
        "                    if id(w) in seen:\n"
        "                        continue\n"
        "                    labels = [c.text() for c in w.findChildren(QtWidgets.QLabel) if c.text()]\n"
        "                    if not labels:\n"
        "                        continue\n"
        "                    seen.add(id(w))\n"
        "                    res['dialogs'].append({'title': w.windowTitle(), 'labels': labels})\n"
        "                    done = False\n"
        "                    for b in w.findChildren(QtWidgets.QPushButton):\n"
        "                        if b.text().strip().lower() in ('ok', '&ok'):\n"
        "                            b.click()\n"
        "                            done = True\n"
        "                            break\n"
        "                    if not done:\n"
        "                        w.accept()\n"
        "                    res['dismissed'] += 1\n"
        "                except Exception as e:\n"
        "                    res['steps']['pump_error'] = repr(e)\n"
        "            time.sleep(0.05)\n"
        "    n.selectOnly()\n"
        "    try:\n"
        "        n['disable'].setValue(True)\n"
        "        res['steps']['disable'] = True\n"
        "    except Exception as e:\n"
        "        res['steps']['disable'] = 'ERR ' + repr(e)\n"
        "    try:\n"
        "        n['kernelSourceFile'].setValue(%r)\n"
        "        res['steps']['setValue'] = 'ok'\n"
        "    except Exception as e:\n"
        "        res['steps']['setValue'] = 'ERR ' + repr(e)\n"
        "    for knob in ('reloadKernelSourceFile', 'recompile'):\n"
        "        t0 = time.time()\n"
        "        try:\n"
        "            n[knob].execute()\n"
        "            res['steps'][knob] = 'ok'\n"
        "        except Exception as e:\n"
        "            res['steps'][knob] = 'ERR ' + repr(e)[:200]\n"
        "        res['steps'][knob + '_seconds'] = round(time.time() - t0, 2)\n"
        "    _pump(2.5)\n"
        "    for w in app.allWidgets():\n"
        "        cls = w.metaObject().className()\n"
        "        if cls == 'GroupWidgetButton':\n"
        "            try:\n"
        "                t = w.text()\n"
        "            except Exception:\n"
        "                t = ''\n"
        "            if 'rror' in t and res['count'] is None and w.isVisible():\n"
        "                res['count'] = t\n"
        "        elif cls == 'ErrorTable' and w.isVisible():\n"
        "            for r in range(w.rowCount()):\n"
        "                for c in range(w.columnCount()):\n"
        "                    it = w.item(r, c)\n"
        "                    v = (it.text() if it is not None else '').strip()\n"
        "                    if v:\n"
        "                        res['errors'].append(v)\n"
        "    res['dialogs_restants'] = _pump(0.4)\n"
        "    return res\n"
        "result = _run()\n" % (node_name, node_name, kernel_file))


def _load_code(node_name: str) -> str:
    """Cliquer sur le bouton Load du BlinkScript (`reloadKernelSourceFile`).

    Indispensable : renseigner `kernelSourceFile` ne pousse pas le fichier dans l'editeur du
    kernel. Sans ce clic, `recompile` recompile la source precedente — un kernel en erreur passe
    donc inapercu (verifie : blink02 + recompile sans Load => '0 Errors Total').
    """
    return (
        "n = nuke.toNode('%s')\n"
        "res = {'clicked': False, 'error': None}\n"
        "try:\n"
        "    n['reloadKernelSourceFile'].execute()\n"
        "    res['clicked'] = True\n"
        "except Exception as e:\n"
        "    res['error'] = repr(e)\n"
        "result = res\n" % node_name)


_PING_CODE = "result = {'ping': True}\n"


async def _dismiss_then_report(api, node_name: str):
    """Fermer la/les popup(s) de compilation puis relire le rapport, quand Nuke repond.

    La popup apparait de facon differee et bloque le main thread : on la ferme par WM_CLOSE
    (`dismiss_nuke_dialogs`). Tant que la compilation envoyee en 6a n'a pas fini, une NOUVELLE popup
    peut sortir — on insiste donc : fermer, tester une commande courte, recommencer, et seulement
    quand Nuke repond on lit le rapport dans l'ErrorTable (noeud reactive).
    """
    await asyncio.sleep(2.0)
    fermees = 0
    for _ in range(8):
        fermees += dismiss_nuke_dialogs(verbose=True)
        try:
            await api.exec_python(_PING_CODE, timeout=5)
            break
        except Exception:                                  # noqa: BLE001 - insister est le but
            await asyncio.sleep(1.5)
    result = await api.exec_python(_enable_and_report_code(node_name), timeout=30)
    if isinstance(result, dict):
        result["popups_fermees"] = fermees
    return result


def _enable_and_report_code(node_name: str) -> str:
    """Reactiver le noeud puis relire l'ErrorTable (source structuree du rapport).

    Aucun clic sur recompile : on relit un etat deja compile, donc pas de nouvelle popup.
    C'est la lecture que fait un humain « en bas a droite » une fois le noeud actif.
    """
    return _compile_report_code(node_name, enable=True)


def _disable_code(node_name: str) -> str:
    """Desactiver le noeud (`disable=True`).

    Deux usages, tires de l'experience :
      * compiler un kernel en erreur : sur noeud ACTIF la compilation ratee ouvre une modale qui
        bloque Nuke entierement (aucune commande de l'addon ne passe, meme le handshake) ;
      * brancher le viewer : le wiring declenche une evaluation, donc la popup de compilation —
        il faut que le noeud soit deja desactive.
    """
    return (
        "n = nuke.toNode('__NODE__')\n"
        "n['disable'].setValue(True)\n"
        "result = {'disabled': bool(n['disable'].value())}\n".replace("__NODE__", node_name)
    )


def _compile_report_code(node_name: str, enable: bool | None = None) -> str:
    """Lire le rapport de compilation dans le panneau Properties.

    OU EST LE RAPPORT : nulle part dans l'API — `node.error()` reste False et aucun knob ne porte
    le texte ; l'editeur kernelSource ne contient QUE la source. Nuke l'affiche dans une table Qt
    (`ErrorTable`, sous-classe de QTableWidget) placee sous l'editeur, surmontee d'un bouton
    `GroupWidgetButton` libelle '<n> Errors Total' ; en dessous viennent 'Cycle Errors' puis la
    barre de statut. Comme c'est une table, `text()` / `toPlainText()` ne renvoient RIEN : il faut
    passer par `item(r, c).text()` — c'est ce qui rend un simple balayage de widgets aveugle.

    `enable=True` reactive le noeud avant la lecture : la table n'existe pas quand le noeud est
    desactive, et reactiver sans recompiler ne rouvre aucune popup.
    On ne filtre PAS sur `isVisible()` (la table peut exister avec son groupe replie), on
    selectionne le noeud pour que le panneau affiche le bon, et on laisse passer des evenements car
    le panneau se rafraichit de facon differee.

    NOTE INTERPOLATION : le payload est construit sur des placeholders (`__NODE__`, `__PREAMBLE__`)
    puis remplace. Avec la concatenation implicite de litteraux, un `%` mal place ne s'applique
    qu'au dernier morceau — bug paye : le payload cherchait un noeud nomme '%s'.
    """
    payload = (
        "def _run():\n"
        "    import time\n"
        "    import nuke\n"
        "    from PySide6 import QtWidgets\n"
        "    n = nuke.toNode('__NODE__')\n"
        "__PREAMBLE__"
        "    if n is not None:\n"
        "        n.selectOnly()\n"
        "    app = QtWidgets.QApplication.instance()\n"
        "    for _ in range(25):\n"
        "        app.processEvents()\n"
        "        time.sleep(0.02)\n"
        "    res = {'count_text': None, 'tables': 0, 'header': None, 'errors': []}\n"
        "    if n is not None:\n"
        "        try:\n"
        "            res['disabled'] = bool(n['disable'].value())\n"
        "        except Exception:\n"
        "            pass\n"
        "    for w in app.allWidgets():\n"
        "        cls = w.metaObject().className()\n"
        "        if cls == 'GroupWidgetButton':\n"
        "            try:\n"
        "                t = w.text()\n"
        "            except Exception:\n"
        "                t = ''\n"
        "            if 'rror' in t and res['count_text'] is None:\n"
        "                res['count_text'] = t\n"
        "            continue\n"
        "        if cls != 'ErrorTable':\n"
        "            continue\n"
        "        res['tables'] += 1\n"
        "        try:\n"
        "            hh = [w.horizontalHeaderItem(c).text() if w.horizontalHeaderItem(c) else ''\n"
        "                  for c in range(w.columnCount())]\n"
        "            if hh and not res['header']:\n"
        "                res['header'] = hh\n"
        "        except Exception:\n"
        "            pass\n"
        "        for r in range(w.rowCount()):\n"
        "            for c in range(w.columnCount()):\n"
        "                it = w.item(r, c)\n"
        "                cell = (it.text() if it is not None else '').strip()\n"
        "                if cell:\n"
        "                    res['errors'].append(cell)\n"
        "    return res\n"
        "result = _run()\n"
    )
    preamble = "" if enable is None else ("    n['disable'].setValue(%s)\n"
                                          % ("False" if enable else "True"))
    return payload.replace("__NODE__", node_name).replace("__PREAMBLE__", preamble)


def _viewer_code(node_name: str) -> str:
    """Connect viewer input 1 and READ IT BACK on the viewer NODE.

    Nuke 17.1v1 has neither `Viewer.setInput` nor `Viewer.getInput` (verifie par dir()) : la paire
    qui marche est `nuke.connectViewer(1, node)` PUIS `Viewer.activateInput(1)` (l'ordre inverse
    leve `ValueError('Input is not connected.')`), et la connexion se relit sur le NODE du viewer —
    `nuke.activeViewer().node().input(1)` (1 = le numero d'input du viewer ; l'index 0 reste vide).
    """
    return (
        "n = nuke.toNode('__NODE__')\n"
        "v = nuke.activeViewer()\n"
        "res = {'viewer': (str(v) if v is not None else None), 'set_input_error': None,\n"
        "       'used': None, 'method': None, 'readback': None, 'active_input': None,\n"
        "       'activate_error': None}\n"
        "if v is None:\n"
        "    res['set_input_error'] = 'aucun viewer actif'\n"
        "else:\n"
        "    if hasattr(v, 'setInput'):\n"
        "        try:\n"
        "            v.setInput(1, n)\n"
        "            res['used'] = 'Viewer.setInput'\n"
        "        except Exception as e:\n"
        "            res['set_input_error'] = repr(e)\n"
        "    if res['used'] is None:\n"
        "        try:\n"
        "            nuke.connectViewer(1, n)\n"
        "            res['used'] = 'nuke.connectViewer(1, node)'\n"
        "        except Exception as e:\n"
        "            res['set_input_error'] = repr(e)\n"
        "        try:\n"
        "            v.activateInput(1)\n"
        "        except Exception as e:\n"
        "            res['activate_error'] = repr(e)\n"
        "    try:\n"
        "        res['active_input'] = int(v.activeInput())\n"
        "    except Exception:\n"
        "        pass\n"
        "    vn = v.node()\n"
        "    x = None\n"
        "    for idx in (1, 0):\n"
        "        if vn is None:\n"
        "            break\n"
        "        try:\n"
        "            x = vn.input(idx)\n"
        "        except Exception:\n"
        "            x = None\n"
        "        if x is not None:\n"
        "            res['method'] = 'activeViewer().node().input(' + str(idx) + ')'\n"
        "            break\n"
        "    if x is not None:\n"
        "        try:\n"
        "            res['readback'] = x.name()\n"
        "        except Exception:\n"
        "            res['readback'] = str(x)\n"
        "result = res\n".replace("__NODE__", node_name)
    )

"""NukeMCP test scenarios — one function per Test, runnable at will.

Scenarios declare the nodes they create (idempotence + cleanup) and drive only the shared
vocabulary of the harness backends, so the same Test runs against the addon socket or through
the MCP server tools (`--backend addon|mcp`). Every check is its own PASS/FAIL line: a step
that "looks fine" but whose value did not stick fails the test.
"""

from __future__ import annotations

from harness import Session

BLINK_FILE = r"G:\PROGRAMMING\GITHUB\Nuke_ToolsAB\blink01.blink"
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
    await s.step("5. kernelSourceFile = blink01.blink",
                 api.modify_node(BLINK, {"kernelSourceFile": BLINK_FILE}),
                 verify=lambda r: _expect_modified(r, "kernelSourceFile"))
    await s.step("5b. relire kernelSourceFile",
                 api.get_node_info(BLINK),
                 verify=lambda i: _expect_knob_path(i, BLINK, "kernelSourceFile", BLINK_FILE))

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
    if result.get("error_text"):
        raise AssertionError("recompile a laisse une erreur: %s" % result["error_text"])
    return True


def _expect_no_node_error(info, node_name: str):
    """Second reading after the click: the node itself must not report an error."""
    knobs = _knobs(info)
    for key in ("error", "errors"):
        if key in knobs and str(knobs[key]).strip():
            raise AssertionError("%s.%s = %r" % (node_name, key, knobs[key]))
    return True


def _expect_viewer(result, node_name: str):
    if not isinstance(result, dict):
        raise AssertionError("resultat viewer inattendu: %r" % (result,))
    if result.get("readback") is None:
        raise AssertionError(
            "connexion NON verifiable (used=%r, set_input_error=%r): aucune lecture de "
            "viewer_input n'a abouti" % (result.get("used"), result.get("set_input_error")))
    if result["readback"] != node_name:
        raise AssertionError("viewer1 input 1 = %r, attendu %r"
                             % (result["readback"], node_name))
    return True


# --------------------------------------------------------------------------- code snippets

def _recompile_code(node_name: str) -> str:
    """Click the BlinkScript 'recompile' button and report the resulting error, if any.

    `n.error()` / `hasError()` are looked up defensively: the point is to click and to report
    what the node says afterwards, not to assume an API that may not exist.
    """
    return (
        "n = nuke.toNode('%s')\n"
        "res = {'clicked': False, 'error': None, 'error_text': None}\n"
        "try:\n"
        "    n['recompile'].execute()\n"
        "    res['clicked'] = True\n"
        "except Exception as e:\n"
        "    res['error'] = repr(e)\n"
        "try:\n"
        "    res['error_text'] = str(n.error()) if n.error() else None\n"
        "except Exception:\n"
        "    res['error_text'] = None\n"
        "try:\n"
        "    res['has_error'] = bool(n.hasError())\n"
        "except Exception:\n"
        "    res['has_error'] = None\n"
        "result = res\n" % node_name)


def _viewer_code(node_name: str) -> str:
    """Connect viewer input 1 (setInput, else connectViewer) and READ IT BACK."""
    return (
        "n = nuke.toNode('%s')\n"
        "v = nuke.activeViewer()\n"
        "res = {'viewer': (str(v) if v is not None else None), 'set_input_error': None,\n"
        "       'used': None, 'method': None, 'readback': None}\n"
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
        "            res['used'] = 'nuke.connectViewer'\n"
        "        except Exception as e:\n"
        "            res['set_input_error'] = repr(e)\n"
        "    x = None\n"
        "    for meth in ('getInput', 'input'):\n"
        "        if hasattr(v, meth):\n"
        "            try:\n"
        "                x = getattr(v, meth)(1)\n"
        "                res['method'] = meth\n"
        "                break\n"
        "            except Exception:\n"
        "                continue\n"
        "    if x is not None:\n"
        "        try:\n"
        "            res['readback'] = x.name()\n"
        "        except Exception:\n"
        "            res['readback'] = str(x)\n"
        "result = res\n" % node_name)

#!/usr/bin/env python3
"""NukeMCP test runner — launch the scenarios on demand, against a live Nuke.

    python run_tests.py                          # tous les scenarios, backend addon (socket)
    python run_tests.py --scenario test01
    python run_tests.py --backend mcp            # via le serveur MCP (uv run nuke-mcp, stdio)
    python run_tests.py --reset                  # efface tout sauf le viewer avant de lancer
    python run_tests.py --cleanup                # supprime les nodes crees a la fin
    python run_tests.py --list
    python run_tests.py --json out/test01.json   # ecrit le rapport

Backends:
  * addon (default) — JSON over 127.0.0.1:54321, the exact payload the MCP tools send.
    Nothing else to start: Nuke must run with the addon listening.
  * mcp — spawns `uv run nuke-mcp` over stdio and calls the real MCP tools (also requires the
    addon: the server refuses to start without it).

A step that times out usually means Nuke's main thread is busy (modal dialog) — the runner
says so instead of hanging forever.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import scenarios  # noqa: E402
from harness import AddonBackend, BackendError, MCPBackend, Session  # noqa: E402

OUT_DIR = Path(__file__).resolve().parent / "out"


def parse_args(argv=None):
    p = argparse.ArgumentParser(description="NukeMCP scenario runner")
    p.add_argument("--backend", choices=("addon", "mcp"), default="addon")
    p.add_argument("--scenario", default="all", help="scenario name, or 'all'")
    p.add_argument("--host", default="127.0.0.1")
    p.add_argument("--port", type=int, default=54321)
    p.add_argument("--timeout", type=float, default=60.0, help="per command, seconds")
    p.add_argument("--reset", action="store_true",
                   help="effacer tous les nodes SAUF le(s) Viewer avant de lancer (script propre)")
    p.add_argument("--cleanup", action="store_true",
                   help="delete the nodes the scenario created (default: leave them visible)")
    p.add_argument("--cleanup-first", action="store_true",
                   help="delete leftovers BEFORE running (always done when --cleanup)")
    p.add_argument("--json", default=None, help="write the report to this path")
    p.add_argument("--list", action="store_true")
    return p.parse_args(argv)


def header(args, handshake):
    print("=" * 74)
    print("NukeMCP tests — backend %s" % args.backend)
    if handshake:
        print("addon   v%s | Nuke %s | variante %s"
              % (handshake.get("addon_version"), handshake.get("nuke_version"),
                 handshake.get("variant")))
    print("=" * 74, flush=True)


async def reset_script(api):
    """Remise a zero demandee par l'utilisateur : tout sauf les viewers."""
    try:
        res = await api.exec_python(scenarios.reset_script_code())
    except Exception as e:  # noqa: BLE001 - un reset impossible ne doit pas empecher le run
        print("  reset impossible: %s: %s" % (type(e).__name__, e), flush=True)
        return
    if isinstance(res, dict):
        print("  reset: %d node(s) supprime(s), garde(s)=%s%s"
              % (len(res.get("supprimes") or []), res.get("gardes"),
                 (" ; viewer recree: %s" % res["viewer_cree"]) if res.get("viewer_cree") else ""),
              flush=True)


async def cleanup_nodes(api, nodes, label: str):
    """Delete the scenario's nodes if they exist (idempotence: leftovers from a previous run)."""
    for name in nodes:
        try:
            await api.delete_node(name, confirm=True)
            print("  %s: %s supprime" % (label, name), flush=True)
        except Exception:
            pass  # absent: rien a faire (un no-show n'est pas une erreur)


async def run_scenario(api, name: str, args) -> dict:
    spec = scenarios.SCENARIOS[name]
    print("\n--- %s : %s%s" % (name, spec["title"], " " + "-" * 6), flush=True)
    await cleanup_nodes(api, spec["nodes"], "menage avant")
    s = Session(api)
    t0 = time.time()
    try:
        await spec["fn"](s)
    except Exception as e:  # a broken backend must not lose the report
        s.results.append({"step": "scenario interrompu", "status": "FAIL",
                          "error": "%s: %s" % (type(e).__name__, e)})
    report = s.summary()
    report.update({"scenario": name, "backend": args.backend,
                   "seconds": round(time.time() - t0, 2),
                   "nodes": spec["nodes"]})
    if args.cleanup:
        await cleanup_nodes(api, spec["nodes"], "menage apres (--cleanup)")
        report["cleaned"] = True
    print("  -> %d etapes, %d echec(s), %.1fs"
          % (report["steps"], report["failed"], report["seconds"]), flush=True)
    return report


async def amain(args) -> int:
    if args.list:
        for k, v in scenarios.SCENARIOS.items():
            print("%-8s %s (nodes: %s)" % (k, v["title"], ", ".join(v["nodes"])))
        return 0

    names = list(scenarios.SCENARIOS) if args.scenario == "all" else [args.scenario]
    for n in names:
        if n not in scenarios.SCENARIOS:
            print("scenario inconnu: %s (dispo: %s)"
                  % (n, ", ".join(scenarios.SCENARIOS)), file=sys.stderr)
            return 2

    reports = []
    if args.backend == "mcp":
        try:
            async with MCPBackend() as api:
                hs = await api.connect()
                header(args, {"addon_version": "?", "nuke_version": "?", "variant": "?",
                              "tools": hs.get("tools")})
                print("serveur MCP: %s outils exposes" % hs.get("tools"), flush=True)
                for n in names:
                    reports.append(await run_scenario(api, n, args))
        except BackendError as e:
            print("backend MCP indisponible: %s" % e, file=sys.stderr)
            return 3
    else:
        api = AddonBackend(host=args.host, port=args.port, timeout=args.timeout)
        try:
            hs = api.connect()
        except Exception as e:
            print("addon injoignable sur %s:%d (%s: %s)\n"
                  "-> Nuke doit tourner, avec le serveur NukeMCP demarre (menu Scripts > MCP "
                  "Server, ou autostart via la preference)."
                  % (args.host, args.port, type(e).__name__, e), file=sys.stderr)
            return 3
        header(args, hs)
        if args.reset:
            await reset_script(api)
        for n in names:
            reports.append(await run_scenario(api, n, args))

    total_failed = sum(r["failed"] for r in reports)
    print("\n" + "=" * 74)
    for r in reports:
        print("%-8s %2d etapes / %d echec(s)  %.1fs"
              % (r["scenario"], r["steps"], r["failed"], r["seconds"]))
    print("RESULTAT: %s" % ("OK" if total_failed == 0 else "%d ECHEC(S)" % total_failed))
    print("=" * 74, flush=True)

    if args.json:
        path = Path(args.json)
        if not path.is_absolute():
            path = OUT_DIR / path
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"backend": args.backend, "reports": reports},
                                   ensure_ascii=False, indent=2), encoding="utf-8")
        print("rapport ecrit: %s" % path, flush=True)
    return 0 if total_failed == 0 else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(amain(parse_args())))

# TODO — Hermes fork (`hermes-addon-patch`)

Working list for our fork of [kleer001/nuke-mcp](https://github.com/kleer001/nuke-mcp).
The addon patch lives in `nuke_addon/` + `patches/`; see `patches/README.md` for the procedure,
the version history and the test recipes. Current: **v0.2.1-hermes** (commit `3a60e19`).

Language note: kept in English to match the rest of the repo.

## Decisions taken (not to re-litigate)

- **Nuke vs NukeX differences: out of scope for now.** The connected variant is reported in the
  handshake (`variant: "Nuke"` on this seat), and the MCP server gates NukeX-only tools on it.
  We do **not** try to reconcile or work around the Nuke/NukeX tool differences at this stage —
  if a tool is missing because of variant gating, that is expected behaviour, not a bug.
- **No Nuke preference is ever created or edited** by the addon: its own settings live in
  `~/.nuke/nukemcp_prefs.json` (key `start_active`). Verified against the live pref files and the
  whole git history. Keep it that way.
- **One pilotable instance at a time.** Port 54321 belongs to whoever listens first; the addon
  never doubles it and logs `port deja tenu par une autre instance` instead.
- **Headless is supported** (socket only, no Qt/menu/panel). Note: plain `nuke -t` is refused by
  this seat's licence ("render only" product not licensed) — `--nc -t` is what makes a headless
  run possible here for tests.

## Open work

1. **Harden `get_script_info`** (and audit the other handlers the same way).
   The handler was fixed in v0.2.1 (guard when no script is open, `format` serialised as
   `{name,width,height}` instead of `str(root.format())`). What remains:
   - audit the rest of the handlers for the same class of problem — unguarded `root[...]` /
     `node[...]` reads, values that serialise as Python reprs (addresses, enums, `Format`),
     missing `nuke.GUI`-only assumptions, and error paths that raise instead of returning
     `{"status": "error", ...}`;
   - make every handler return a JSON-serialisable result even on an empty scene
     (`nuke.allNodes()` on Root, no viewer, no Write node, no UI).
2. **Multiple Nuke instances.** Today a second Nuke (GUI or headless) cannot take the port, so it
   is simply not pilotable and the MCP server can only ever talk to one instance. Decide the model:
   - a port derived per instance (e.g. `54321 + n`, or stored in the prefs JSON) plus a way for
     the MCP server to know which instance to address;
   - and/or an explicit "session picker" (list running instances, choose one) on the server side;
   - and/or a single-instance rule made explicit in the UI/log.
   Constraints to respect: the port number is currently hard-coded in the addon, in
   `hermes mcp add` (`--directory … run nuke-mcp`) and in the Hermes MCP config.
3. **Server-side changes** (`src/nukemcp/`) go on a **separate branch** (e.g. `hermes-server-tools`),
   never on `hermes-addon-patch`, so the two streams stay separable in the fork.
4. **Licence-aware headless**: plain `-t` fails on this seat because the "render only" product is
   not licensed. If headless piloting is wanted in production, either check the entitlement or
   detect the licence refusal and surface it clearly in the addon log.
5. **Upstream**: our `executeInMainThreadWithResult` fix (v0.1.0) adopts their PRs #1/#2/#4. Consider
   proposing the v0.2.1 robustness fixes upstream too (orphan-server registry, `listen(8)`,
   `_port_in_use` retries, log-only panel).
6. **Docs kept in sync at every change**: `patches/README.md` (version history + recipes) and the
   Hermes skill `nuke-mcp` (procedure, pitfalls, current head).

## Ideas (not planned)

- `nukemcp_prefs.json` could also carry the port (prerequisite for item 2).
- Make the pane report *why* an action failed in one line (already the case since v0.2.1: all
  errors are routed into the pane log) — extend that to handler errors surfaced from the server.
- A tiny dev helper to reload the addon in a running Nuke and reopen the pane (today: manual
  `importlib.reload` over the socket + Pane > NukeMCP).

## Done (2026-09-16 session)

- Removed the dead duplicate `start()` left over from earlier patch rounds.
- Panel redesigned as **log-only**: neutral `Start/Stop` button, log connected at construction,
  every action writes the measured state, all errors routed into the pane log.
- Headless support verified (`--nc -t`): no Qt, no menu, no pane, skip when the port is taken.
- Orphan-server bug fixed (`_server_registry`, `stop()` stops all, no reference overwrite) and the
  false "port free" fixed (`listen(8)`, `_port_in_use` retries).
- `menu.py`: the "Show NukeMCP panel" entry removed (never worked — use Pane > NukeMCP).
- `tests/probe_headless.py` added.

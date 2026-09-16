# NukeMCP tests (live Nuke)

Scenario tests run **on demand** against a running Nuke. They are not part of the mocked
`tests/` suite at the repo root used by CI (that one runs without Nuke).

```bash
python run_tests.py --list                    # scenarios disponibles
python run_tests.py                           # tous, backend addon (socket 54321)
python run_tests.py --scenario test01          # un seul
python run_tests.py --backend mcp              # via le serveur MCP (uv run nuke-mcp, stdio)
python run_tests.py --cleanup                  # supprime les nodes crees (sinon laisses visibles)
python run_tests.py --json test01.json         # rapport JSON dans tests/out/
```

## Prerequis

- Nuke lance, avec l'addon NukeMCP demarre (autostart via la preference, ou menu
  Scripts > MCP Server). Le runner verifie le handshake et affiche version addon / version Nuke /
  variante.
- Backend `mcp` : `fastmcp` dans l'environnement courant et le venv du repo synchronise.

## Deux backends, un seul vocabulaire

| Backend | Ce qu'il teste | Quand l'utiliser |
|---|---|---|
| `addon` (defaut) | le protocole socket : exactement la payload que les outils MCP envoient. Aucun LLM, aucune dependance. | boucle de dev rapide |
| `mcp` | la couche outils du serveur MCP (noms, arguments, gating) via `uv run nuke-mcp` en stdio. | valider l'integration Hermes/MCP |

Les scenarios n'appellent que `create_node` / `modify_node` / `get_node_info` / `get_script_info` /
`delete_node` / `connect_nodes` / `exec_python` : les deux backends les exposent, donc le meme Test
tourne des deux cotes.

## Fichiers

| Fichier | Role |
|---|---|
| `harness.py` | backends (addon socket, serveur MCP) + `Session` (une ligne PASS/FAIL par etape, avec preuve) |
| `scenarios.py` | les Tests ; chacun declare les nodes qu'il cree (idempotence + `--cleanup`) |
| `run_tests.py` | le runner (CLI, rapport, menage, messages d'erreur explicites) |
| `probe_headless.py` | verifie que l'addon fonctionne sans GUI (`nuke --nc -t`) |

## Ce que les etapes verifient vraiment

Chaque etape a sa propre ligne et sa propre verification — une valeur qui ne "prend pas" fait
echouer le test au lieu de passer inapercue :

- creation : nom ET classe du node retournes par l'addon ;
- knobs : ecriture via `modify_node`, puis **relecture** via `get_node_info` ;
- `kernelSourceFile` : comparaison normalisee (`\` / `/`, casse) ;
- `recompile` : clic via `execute_python` (aucun outil MCP ne clique un knob), puis etat d'erreur du
  node relu — le clic qui laisse une erreur echoue ;
- viewer : connexion puis **relecture** de l'entree 1 ; si aucune lecture n'aboutit, l'etape echoue
  en le disant (pas de "probablement connecte").

## Pieges connus

- **Un client persistant ne bloque plus rien.** L'addon sert desormais chaque client dans son propre
  thread (`_serve_client`). Avant la v0.2.2, `_handle_client` bouclait sur son client jusqu'a sa
  deconnexion : le serveur MCP en tenant une connexion permanente, **aucun autre client n'etait
  jamais servi** (requetes acceptees par TCP, jamais traitees — tout time-out sans rien dans le log).
- Une etape qui time-out alors que Nuke va bien indique que la boucle principale est occupee
  (dialogue modal, calcul Blink...) : le runner le dit au lieu de rester bloque.
- `execute_python` dans l'addon => `exec(code, globals, locals)` : **pas de comprehension** sur des
  variables locales, englober le payload dans une fonction.
- Apres un `importlib.reload(nuke_mcp_addon)`, la pane deja dockee est orpheline : rouvrir la pane
  (Pane > NukeMCP).

## Test01 (etat actuel)

`checkerboard` + `blur` (size 20) + `blinkScript` (`kernelSourceFile` =
`G:\PROGRAMMING\GITHUB\Nuke_ToolsAB\blink01.blink`, puis clic **recompile**) + **viewer1 input 1**
accroche a la sortie du blinkscript.

Les nodes crees s'appellent `test01_*` et sont supprimes en debut de run (idempotence) ; laisses en
place a la fin pour inspection sauf `--cleanup`.

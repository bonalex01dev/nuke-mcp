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

## Scenarios

- `test01` — checkerboard + blur(20) + BlinkScript(blink01) + viewer1 (11/11 en 0,7 s)
- `test02` — kernel en ERREUR (blink02) : compilation non bloquante, popup fermee, controle negatif
  (12/12 en ~14 s)

## test02 — kernel en erreur (blink02) : ce que la compilation ratee apprend

`blink02.blink` = le kernel de test01 avec une faute volontaire : line 47, `sin(anglee)`.

Charge puis compile ce kernel demande trois precautions, toutes decouvertes en le faisant :

1. **Un kernel qui ne compile pas ouvre une modale qui bloque TOUT Nuke.** Le clic `recompile` ne
   rend alors jamais la main (>240 s mesure) et, tant que la popup est ouverte, PLUS AUCUNE
   commande de l'addon n'aboutit — pas meme le handshake. Une compilation se lance donc en **envoi
   non bloquant** (`AddonBackend.send_only_python`).
2. **Aucun garde-fou in-process ne peut fermer cette popup.** Un `QTimer` arme avant le clic ne
   tire pas (verifie : 0 tick, journal par fichier) : Nuke n'itere pas la boucle d'evenements
   pendant le blocage. La fermeture se fait **de l'exterieur**, par `WM_CLOSE` sur la fenetre
   intitulee exactement `Nuke` (`harness.dismiss_nuke_dialogs`) : le bouton OK n'est pas cliquable
   depuis Win32 (Qt dessine ses widgets, pas de fenetre enfant native) mais WM_CLOSE referme la
   modale et debloque Nuke.
3. **`disable=True` avant de charger, compiler ou brancher le viewer.** Le wiring du viewer
   declenche une evaluation, donc la popup. Le noeud reste desactive a la fin du test.

Le **texte** du rapport n'est exposé nulle part dans l'API : `node.error()` reste `False`, aucun
knob ne le porte, l'editeur kernelSource ne contient que la source. Il n'existe que dans deux
sources d'interface :
- la **popup** — lisible depuis l'exterieur (arbre d'accessibilite Windows) tant qu'elle est
  ouverte ; texte constant a chaque essai :
  `Error compiling kernel: File blink02.blink, Line 47: use of undeclared identifier 'anglee'; did you mean 'angle'?`
- l'**`ErrorTable`** du panneau Properties (sous l'editeur du kernel, surmontee du bouton
  `n Errors Total`) — elle n'existe que si le noeud est ACTIF et affiche **au moment** de l'echec :
  la reactiver apres coup ne la cree pas.

test02 verifie donc ce qui est deterministe : la compilation ratee a bien lieu, la popup est
detectee et fermee, Nuke n'est pas reste bloque, et le controle negatif (blink01, kernel valide)
ne produit ni popup ni erreur. Le texte du rapport se lit par l'arbre d'accessibilite.

## Remise a zero : `--reset`

```bash
python run_tests.py --scenario all --reset
```

Efface **tous** les nodes du script **sauf le(s) Viewer** (et les `ViewerProcess`, internes au
viewer), en recreant un `Viewer` s'il n'y en a plus. C'est la facon propre de partir d'un script
vide sans perdre le viewer : `File > New comp` / `nuke.scriptClear()` les detruisent tous et n'en
recree pas un identique — or les deux scenarios s'accrochent a `viewer1 input 1`.

Sortie attendue : `reset: N node(s) supprime(s), garde(s)=['Viewer1']`.

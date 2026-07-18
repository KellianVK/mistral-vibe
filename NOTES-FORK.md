# NOTES-FORK.md — Reconnaissance de `mistral-vibe` (fork KellianVK)

> Réponses aux questions de l'Étape 0, basées sur la lecture du code source (pas de la doc publique générique) + tests empiriques exécutés le 2026-07-18 sur cette machine. Toutes les citations `file:line` pointent dans ce repo (`vibe/...`, `docs/...`, etc.) — écrites à l'origine quand le fork était cloné dans un sous-dossier `mistral-vibe/` séparé ; `dev_evan_mistral` l'a depuis fusionné à la racine du repo, les chemins relatifs restent donc valides.

## TL;DR — décisions que ça conditionne

- **Pas besoin de pty.** Le fork a un vrai mode headless de première classe (`vibe -p`), avec un bloc "Headless Mode" injecté explicitement dans le system prompt. C'est un citoyen de première classe de ce fork, pas un hack.
- `vibe_runner.py` pilotera Vibe par **subprocess** (`vibe -p ...`), pas par import Python direct (`run_programmatic` force `asyncio.run()` en interne et ne peut pas tourner dans une event loop déjà active — bloquant si `blackboard/api.py` tourne en async).
- Chaque rôle = un `--agent NAME` qui résout vers `.vibe/agents/NAME.toml` **dans le projet cible**. Confirmé empiriquement fonctionnel.
- **`--trust` est obligatoire** sur chaque invocation headless, sinon `.vibe/agents/`, `.vibe/hooks.toml` et `.vibe/config.toml` du projet ne sont **jamais chargés** (pas d'erreur, échec silencieux).
- **`--auto-approve` (ou `bypass_tool_permissions = true` dans le profil) est obligatoire**, sinon les tool calls en mode `ASK` sont silencieusement skippés (pas d'erreur, pas de blocage — juste un feedback `"Tool execution not permitted."` renvoyé au modèle).
- `--output json` donne un dump structuré de tous les messages (system/user/assistant/tool) — c'est ce qu'on parse dans `vibe_runner.py`, pas du texte libre.
- Auth déjà configurée sur cette machine (`~/.vibe/config.toml`, `active_model = mistral-medium-3.5`), testé et fonctionnel de bout en bout — la démo peut tourner en vrai.

---

## Q1 — Invocation non-interactive / scriptable

Trois chemins programmatiques existent ; on retient **A** pour `vibe_runner.py`.

### A. CLI headless : `vibe -p/--prompt` (retenu)

Flags pertinents (parsing argparse dans `vibe/cli/entrypoint.py:20-170`) :

| Flag | Rôle |
|---|---|
| `-p/--prompt [TEXT]` | Active le mode headless dès que `args.prompt is not None` (`cli.py:500`) |
| `--agent NAME` | Résout vers un profil builtin ou `.vibe/agents/NAME.toml` (projet) ou `~/.vibe/agents/NAME.toml` |
| `--auto-approve` / `--yolo` | Auto-approuve tous les tool calls |
| `--trust` | Trust *session-only* du cwd — **nécessaire en headless**, pas de TTY pour prompter |
| `--workdir DIR` | Répertoire du projet cible (là où vivent `.vibe/agents/`, le code, etc.) |
| `--output {text,json,streaming}` | `json` = dump structuré de tous les messages |
| `--max-turns`, `--max-price`, `--max-tokens` | Garde-fous, utiles pour éviter qu'un rôle parte en boucle et flambe le budget démo |
| `-c/--continue`, `--resume [ID]` | Reprise de session (pas utilisé en MVP) |

Pas de flag `--print` séparé : `-p` **est** le mode print/exit-on-completion.

**Piège critique #1 — permissions silencieuses.** En headless, `AgentLoop.approval_callback` reste `None` par défaut. Un tool en `ToolPermission.ASK` n'est **pas bloqué** : il est skippé silencieusement (`vibe/core/agent_loop/_loop.py:1954-1959`), avec juste un `feedback="Tool execution not permitted."` renvoyé au modèle. Aucune erreur visible. → toujours passer `--auto-approve`, ou mettre `bypass_tool_permissions = true` dans chaque profil de rôle.

**Piège critique #2 — trust silencieux.** `.vibe/config.toml`, `.vibe/hooks.toml`, `.vibe/agents/` du projet ne sont chargés que si le cwd est *trusted* (`HarnessFilesManager._trusted_workdir`, `vibe/core/config/harness_files/_harness_manager.py:37-45`). Sans TTY, pas de prompt — juste un warning stderr, puis exécution silencieuse **sans** les extensions projet (`cli.py:115-132`). → toujours passer `--trust`.

**Confirmé empiriquement** (voir section "Tests empiriques" plus bas) : `vibe -p "..." --agent backend --trust --auto-approve --output json` fonctionne de bout en bout, résout bien un agent profile custom dans `.vibe/agents/`, exécute des tool calls, et produit un JSON exploitable.

### B. API Python : `vibe.core.run_programmatic` (écarté pour le MVP)

Importable : `from vibe.core import run_programmatic` (`vibe/core/__init__.py:9-15`). Signature complète dans `vibe/core/programmatic.py:28-43`. **Écarté** car `asyncio.run()` est appelé en interne (`programmatic.py:107`) — incompatible avec un orchestrateur/serveur déjà async (notre `blackboard/api.py` FastAPI). Utilisable seulement depuis un contexte 100% synchrone ou dans un thread séparé.

### C. API bas niveau : `AgentLoop.act()`

Générateur async (`vibe/core/agent_loop/_loop.py:898-906`) — primitive utilisée en interne par B et par le tool `task`. Overkill pour le MVP (nécessite de construire soi-même `ConfigOrchestratorPort`, gérer le cycle de vie). Piste pour une V2 si on veut du streaming token-par-token au lieu d'attendre la fin du subprocess.

### D. `vibe-acp` (Agent Client Protocol, écarté)

Bridge JSON-RPC/stdio pour éditeurs (Zed, JetBrains, Neovim) via le SDK tiers `agent-client-protocol`. Protocole riche (`initialize`/`new_session`/`prompt`/`session_update` streaming/`cancel`...) mais pensé pour un client éditeur, pas pour un script. Écarté pour le MVP — trop de protocole à implémenter pour le temps disponible ; **le point positif noté** : contrairement au mode `-p`, l'approbation des tools y fait un vrai aller-retour (`set_approval_callback` est branché, `acp_agent_loop.py:755-756`). Piste bonus si on veut un jour un vrai contrôle interactif humain-dans-la-boucle par rôle.

---

## Q2 — Définition des profils d'agents

**Emplacements** (ordre de découverte, premier trouvé gagne) :
1. `config.agent_paths` (dossiers custom listés en config)
2. `<projet>/.vibe/agents/*.toml` — **seulement si le cwd est trusted**
3. `~/.vibe/agents/*.toml` (ou `$VIBE_HOME/agents/*.toml`)
4. Profils builtin (`BUILTIN_AGENTS`)

Le **nom du fichier** (sans `.toml`) est le nom de l'agent — pas un champ dans le fichier.

**Ce n'est pas un schéma Pydantic strict** — `AgentProfile` est un `dataclass` (`vibe/core/agents/models.py:51-58`) :
```python
@dataclass(frozen=True)
class AgentProfile:
    name: str
    display_name: str
    description: str
    safety: AgentSafety
    agent_type: AgentType = AgentType.AGENT   # AGENT | SUBAGENT
    overrides: dict[str, Any] = field(default_factory=dict)
    install_required: bool = False
```
Champs reconnus dans le TOML : `display_name` (optionnel), `description` (optionnel), `safety` (optionnel), `agent_type` (optionnel, `"agent"` par défaut). **Tout le reste tombe dans `overrides`** et est mergé sur la config globale — n'importe quel champ `VibeConfig` est overridable depuis un profil d'agent (`enabled_tools`, `disabled_tools`, `bypass_tool_permissions`, `active_model`, `system_prompt_id`, `tools.<nom>.permission`, etc.), validé seulement à la fusion finale.

**Exemple réel et minimal, confirmé fonctionnel sur cette machine** (`.vibe/agents/backend.toml`) :
```toml
display_name = "Backend Engineer"
description = "Implements backend features"
bypass_tool_permissions = true
```

**Builtins disponibles** (`models.py:100-198`) : `default`, `plan`, `accept-edits`, `auto-approve`, `explore` (seul `SUBAGENT` builtin), `lean`. Un profil custom peut réutiliser un nom builtin pour l'overrider (log à `manager.py:119-122`).

**Implication pour `templates/roles/*.toml`** : chaque rôle (`planner`, `backend`, `qa`, `reviewer`) sera un fichier `.vibe/agents/<role>.toml` avec `agent_type` implicite (`agent`, valeur par défaut), `bypass_tool_permissions = true`, et un `description`/prompt-guidance spécifique au rôle. Pas besoin de toucher `agent_type = "subagent"` — nos rôles sont des agents de premier niveau pilotés depuis l'extérieur par `vibe_runner.py`, pas des subagents spawnés via le tool `task` à l'intérieur d'une session.

---

## Q3 — Task tool / délégation à des subagents

Fichier : `vibe/core/tools/builtins/task.py`. **Non utilisé dans l'architecture MVP** (notre orchestration se fait *depuis l'extérieur* des sessions Vibe, pas via le tool `task` *à l'intérieur* d'une session) — documenté ici pour Phase 4 si on veut une variante où le Planner délègue nativement via `task(agent=...)` plutôt que via notre `loop_engine.py` externe.

Résumé : `TaskArgs.agent` est un `str` libre (pas d'enum), résolu dynamiquement via `AgentManager.get_agent()` ; la seule contrainte dure est `agent_type == SUBAGENT` sur le profil résolu (`task.py:100-118`). Un profil custom `agent_type = "subagent"` dans `.vibe/agents/` est donc utilisable par le tool `task`. Permission : allowlist par défaut = `["explore"]` seulement (`task.py:51-53`) ; tout autre subagent custom nécessite `--auto-approve` ou un ajout explicite à `[tools.task] allowlist = [...]`.

**Point notable** : `agent_type = "subagent"` n'impose **aucun sandbox read-only au niveau moteur** — le caractère "lecture seule" de `explore` vient uniquement de son `enabled_tools=["grep","read_file"]`, pas d'une garantie du framework. À ne pas utiliser comme frontière de sécurité si on explore cette piste en Phase 4.

---

## Q4 — Config MCP et Hooks

### MCP (`config.toml`, `[[mcp_servers]]`)

Union discriminée sur `transport` (`vibe/core/config/models.py:307-309`) : `stdio` | `http` | `streamable-http`. Champs communs : `name`, `prompt`, `startup_timeout_sec=10.0`, `tool_timeout_sec=60.0`, `disabled=false`, `disabled_tools=[]`.

Exemple `stdio` (celui qu'on utilisera pour `blackboard/mcp_server.py` en Phase 3/4) :
```toml
[[mcp_servers]]
name = "blackboard"
transport = "stdio"
command = "uv"
args = ["run", "--project", "/Users/evanmse/Desktop/Mistral/workflow", "python", "-m", "mistral_workflow.blackboard.mcp_server"]
```
Nommage des tools exposés : `{server_name}_{tool_name}` (ex. `blackboard_publish_decision`), permissions configurables via `[tools.blackboard_publish_decision] permission = "always"`.

### Hooks (`hooks.toml`, `[[hooks]]`)

Trois types seulement (`HookType`, `vibe/core/hooks/models.py:20-24`) : `pre_tool`, `post_tool`, `post_agent` — **pas** de `session_start`. Champs : `name`, `type`, `command` (interprété par le shell), `match` (glob/`re:` sur le nom du tool, pre/post_tool seulement), `timeout` (def. 60s), `strict` (interdit sur `post_agent`), `description`.

Contrat subprocess : stdin = JSON de l'invocation, stdout JSON = `HookStructuredResponse{decision: allow|deny, reason, hook_specific_output}`, exit 0 + stdout vide = passthrough, tout le reste (exit≠0, timeout, JSON malformé) = échec → warning (ou deny si `strict=true` sur un hook `pre_tool`).

Exemple pertinent pour le bonus "bloquer `git push` sans validation Reviewer" (Phase 4) :
```toml
[[hooks]]
name = "require-reviewer-approval"
type = "pre_tool"
match = "bash"
command = "uv run --project /Users/evanmse/Desktop/Mistral/workflow python -m mistral_workflow.hooks.guard_push"
strict = true
timeout = 10.0
```
Le script `guard_push` lira le Blackboard (fichier JSON ou appel HTTP à `blackboard/api.py`) et renverra `decision="deny"` si le tool_input contient `git push` et qu'aucune décision `reviewer` n'est encore publiée.

---

## Tests empiriques réalisés sur cette machine (2026-07-18)

1. `vibe -p "Reply with exactly the word: OK" --output text --max-turns 1` → répond `OK`. Auth/modèle déjà configurés et fonctionnels (`~/.vibe/config.toml`, `active_model = mistral-medium-3.5`, providers `mistral` + un `llamacpp` local ; modèles nommés `mistral-vibe-cli-latest`, `devstral-small-latest`, `devstral`). **Aucune configuration d'auth à faire pour la démo.**
2. `vibe -p "List the files..." --output json --auto-approve --max-turns 3` → JSON = liste plate de messages `{role, content, tool_calls, ...}` ; tool call `bash` exécuté et auto-approuvé sans blocage ; `role="assistant"` final contient la réponse texte. C'est le format que `vibe_runner.py` parsera (dernier message `role=="assistant"` sans `tool_calls` = résultat du tour).
3. `.vibe/agents/backend.toml` custom (`bypass_tool_permissions = true`) + `vibe -p ... --agent backend --trust` → [voir mise à jour ci-dessous / à confirmer par le test en cours].

**Note sur les noms de modèles** : le template `workflow.toml` du brief cite `model = "medium-3.5"` et `model = "codestral"` — ces noms exacts ne correspondent pas forcément aux `[[models]] name = ...` réellement configurés ici (`mistral-vibe-cli-latest`, `devstral-small-latest`, `devstral`). **Décision** : le champ `model` dans `workflow.toml` sera **optionnel** ; s'il est omis, le rôle hérite du `default_agent`/`active_model` déjà configuré (confirmé fonctionnel). S'il est fourni, on le mappe vers `overrides.active_model` dans le TOML du rôle généré, au risque que ça échoue si le nom ne correspond à rien — documenté comme limitation connue plutôt que deviné.

## Convention de commit injectée par le fork

Le system prompt headless injecte une convention de commit fixe :
```
git commit -m <message>

Generated by Mistral Vibe.
Co-Authored-By: Mistral Vibe <vibe@mistral.ai>
```
Les rôles Backend/QA/Reviewer qui committent du code produiront donc des commits marqués ainsi — à mentionner dans la démo, ne pas essayer de l'overrider (ça vient du system prompt "Headless Mode", pas de notre contrôle).

## Simplifications assumées pour le MVP (à documenter aussi dans README.md racine)

- Orchestration externe (subprocess `vibe -p`), pas de contrôle interne via le tool `task`/subagents.
- Blackboard en JSON fichier + accès direct en Python par l'orchestrateur pour le MVP ; MCP stdio et appels HTTP depuis l'intérieur d'une session Vibe sont repoussés en Phase 3/4 bonus.
- Pas de gestion fine par rôle du champ `model` — hérite de la config globale sauf override explicite documenté comme "à vos risques".

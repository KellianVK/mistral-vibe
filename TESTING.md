# Tester MiaouFlow (fork final)

> ⚠️ Le `vibe` de votre PATH est la CLI officielle, **sans** MiaouFlow.
> Toujours tester avec `uv run vibe ...` depuis la racine du repo
> (branche `miaouflow-merged`).

## 0. Smoke test automatique (gratuit, ~1 min, aucun appel LLM)

```sh
./scripts/test_miaouflow.sh
```

Couvre : les 70 tests offline, la sous-commande native, les restrictions
d'outils dans les profils générés, la porte de push (NO-GO refusé / GO
autorisé, via le vrai hook), les 8 prompts dédiés, le board embarqué.
Attendu : `19 PASS, 0 FAIL`.

## 1a. /workflow dans la session interactive (le mode "comme Kellian")

> Par défaut, un run utilise **toute l'équipe de 8 agents** (~10-15 min,
> jusqu'à ~16 $). Pour un test rapide/pas cher, utilisez
> `--roles Planner,Backend,Frontend` (scénario 1b) — le script de
> répétition le fait déjà.

```sh
cd demo-project && uv --project .. run vibe
# puis, dans la session :
#   /workflow Build a small Todo API with JWT auth, Flask in server/app.py, minimal web/index.html client
#   /workflow status        (état + un agent par ligne)
#   /workflow stop          (annulation)
```

L'assistant appelle `start_workflow` (mêmes vagues, même boucle qualité que
`vibe workflow run`), répond avec l'URL du board (http://127.0.0.1:8787) et
la session reste utilisable pendant que l'équipe tourne en arrière-plan.
Les trois outils sont invisibles dans les workers (pas d'équipes récursives).

## 1b. Démo rapide — trio seulement (~2-4 min, ~2-4 $)

```sh
uv run vibe workflow run \
  --roles Planner,Backend,Frontend \
  --goal "Build a small Todo API with JWT auth: Flask app in server/app.py with POST /auth/register and POST /auth/login returning {token, expires_in}, and /todos CRUD guarded by the JWT. Frontend: a minimal web/index.html client. Keep everything small and runnable." \
  --workdir demo-project
```

Ouvrir <http://127.0.0.1:8787> immédiatement. À observer :
- **toute l'équipe apparaît dès la première seconde** : les rôles en file
  d'attente sont `idle` avec « Waiting for Planner » etc. (fix
  `fix_agent_number_interface`), puis passent `working` vague par vague ;
- Planner `working` (pulsation) → `done`, décision `plan` dans le feed ;
- Backend ∥ Frontend en parallèle (vague 2), broadcasts dans Messages ;
- éventuellement le money shot : Frontend `blocked — waiting on Backend`
  puis débloqué à la publication du `auth-contract` ;
- chips de timing sur chaque nœud, claims en bas, onglet **Changes** avec
  les diffs cliquables du code réellement écrit ;
- clic sur un nœud → tiroir de détails ; le board reste servi après le run.

Relancer la même commande avec le board ouvert = le board se remet à zéro
(reset par run). Nettoyage après: `git clean -fd demo-project`.

## 2. Provocation déterministe de la boucle qualité (~3-5 min, ~3-5 $)

Le bug est planté d'avance ; QA doit le trouver → `FAIL:` → l'orchestrateur
relance Backend avec l'échec en contexte → QA repasse → `PASS:`.

```sh
D=/tmp/miaou-loop && rm -rf $D && mkdir -p $D
cat > $D/calculator.py <<'EOF'
def add(a, b):
    return a - b  # bug volontaire
EOF
printf 'pytest\n' > $D/requirements.txt

uv run vibe workflow run \
  --goal "In calculator.py, add a multiply(a, b) function. QA: write tests for BOTH add and multiply based on what their names promise mathematically (add(2,3)==5), run them, and fail the verdict if any function is wrong. Backend: fix any failure QA reports." \
  --roles Planner,Backend,QA --workdir $D
```

Un Ctrl-C en cours de run marque proprement les rôles jamais démarrés en
`blocked — Orchestrator cancelled before starting` (visible sur le board).

À observer sur le board : verdict `FAIL:` de QA dans le feed → broadcast
`Orchestrator: QA failed — retry 1/3` dans Messages → Backend repasse
`working` → nouveau verdict `PASS:`. En terminal : tous les rôles `done`.
Vérifier le correctif : `grep "a + b" $D/calculator.py`.

## 3. Vaisseau amiral — init conversationnel, équipe 6-8 (~10-15 min, ~8-15 $)

Dans un dossier vide :

```sh
mkdir /tmp/miaou-final && cd /tmp/miaou-final
uv --project <racine-du-repo> run vibe workflow init
```

Répondre aux questions (objectif → UI ? → stack → qualité stricte ? →
CI ?). Attendu :
- équipe de 6-8 provisionnée (Planner, Reviewer, Backend, QA, Security,
  Docs ± Frontend, DevOps), navigateur ouvert automatiquement sur le board ;
- vagues : Planner → Backend ∥ Frontend → QA ∥ Security ∥ Docs ∥ DevOps →
  **Reviewer en dernier** ;
- messages de Security/QA vers Backend dans le panneau Messages ;
- verdict `GO:`/`NO-GO:` du Reviewer dans le feed ;
- `.vibe/hooks.toml` créé (porte de push armée) — un agent qui tenterait
  `git push` sans GO serait refusé ;
- onglet System : 11 outils blackboard + profils avec `disabled_tools`.

Sur projet existant : lancer `init` dans un repo non vide → le scan propose
l'équipe (Frontend si code front détecté, DevOps si CI), 2-3 confirmations.

## 4. Vérifications ponctuelles après un run

```sh
uv run vibe workflow status --workdir <dir>          # snapshot terminal
sqlite3 <dir>/.vibe/workflow.db "select role,topic,substr(summary,1,60) from decisions"
sqlite3 <dir>/.vibe/workflow.db "select from_role||' -> '||to_role||': '||substr(content,1,50) from messages"
tail -3 <dir>/logs/backend.jsonl                     # transcripts bruts NDJSON
```

## Points connus (assumés, voir AMELIORATIONS.md)

- L'attribution des changements est heuristique quand deux agents touchent
  un même fichier non réclamé dans la même vague.
- La boucle qualité partage le budget temps global du run (600 s par
  défaut) — sur un échec tardif, augmenter `--timeout`.
- Les vieux runs (avant la capture de diffs) affichent « No text diff
  available » dans Changes ; les nouveaux runs ont les vrais diffs.

# MiaouFlow — Démo 5 minutes (fin de hackathon)

## Préparation (15 min avant, obligatoire)

```sh
# 1. Ports et processus propres
pkill -f "vibe workflow" ; pkill -f "python -m vibe"

# 2. Pré-calculer le board "fin de partie" (8 agents complets + boucle qualité)
#    Scénario : un jeu de memory des chats, très visuel — avec un bug planté
#    dans le scoring (chaque paire trouvée fait PERDRE des points).
D=/tmp/demo-full && rm -rf $D && mkdir -p $D
printf 'def score_for_match(current_score):\n    return current_score - 10  # bug volontaire: une paire doit AJOUTER 10 points\n' > $D/game_logic.py
printf 'flask\npytest\n' > $D/requirements.txt
#    (sans Security : son audit, volontairement strict, est non-déterministe
#     sur une app de démo — il vit dans le run LIVE et le board de backup)
vibe workflow run --roles Planner,Backend,Frontend,QA,Docs,Reviewer --goal "Build a cat-themed memory card game with a clear split: BACKEND owns server/app.py ONLY — a small Flask app that serves the static web/ directory and exposes POST /match which MUST use game_logic.score_for_match to return the new score; bind to 127.0.0.1, debug OFF; publish the /match contract early. IMPORTANT: game_logic.py is under QA authority — Backend must NOT modify game_logic.py during initial implementation, only if QA's verdict reports a failure in it. FRONTEND owns web/index.html ONLY — a single self-contained page: animated 4x4 grid of cat-emoji cards with CSS flip animations, bold orange-gradient design, live score display updated from POST /match. QA: unit-test game_logic.score_for_match against its mathematical promise (score_for_match(0)==10, score_for_match(10)==20) plus one Flask test_client check of /match, and FAIL the verdict if the scoring is wrong. On a QA failure report, Backend fixes exactly what QA reports, nothing else. Keep everything small." --workdir $D --no-board --timeout 1200
# (~10-15 min ; vérifier à la fin : QA FAIL -> retry -> PASS, verdict GO du Reviewer)

# 3. Servir ce board pré-calculé sur un port séparé (onglet 2 du navigateur)
(vibe workflow board --workdir $D --port 8790 &)

# 4. Préparer le projet du run LIVE (onglet 1 : http://127.0.0.1:8787)
L=/tmp/demo-live && rm -rf $L && mkdir -p $L && cd $L

# 5. LANCER L'APP CONSTRUITE PAR LES AGENTS (onglet 3 du navigateur)
#    Regarder ce que le pré-run a produit, puis la démarrer — PAS le port
#    5000 (réservé macOS) :
ls /tmp/demo-full
(cd /tmp/demo-full && python3 -m flask --app server/app.py run --port 5001 &)
#    Vérifier dans le navigateur : http://127.0.0.1:5001 — le jeu doit
#    s'afficher, retourner une paire doit faire +10. Noter la commande qui
#    marche. Repli si Flask accroche : ouvrir web/index.html directement
#    (le visuel reste), et garder un `curl -X POST .../match` pour le score.

# 6. Navigateur : onglet 1 = 8787 (vide), onglet 2 = 8790 (board complet),
#    onglet 3 = l'app qui tourne. Enregistrement de secours prêt.
#    Terminal en police large.
```

Si le pré-run échoue en boucle qualité : le relancer ; sinon utiliser
n'importe quel run complet — les points 3-4 de la démo s'adaptent.

---

## Script minute par minute

### 0:00 – 0:40 — Le hook : une phrase, une commande

Terminal, dans `/tmp/demo-live`, session `vibe` déjà ouverte :

> « Claude Code et Codex savent paralléliser des agents. Ce que personne ne
> livre, c'est des agents qui *se coordonnent*. On a forké Mistral Vibe pour
> lui donner une couche d'orchestration native : MiaouFlow. »

Taper :
```
/workflow Build a small Todo API with JWT auth: Flask app in server/app.py, minimal web/index.html client. Keep it small.
```

Basculer sur l'onglet 1 (8787) **immédiatement**.

### 0:40 – 1:40 — Le board : l'équipe entière, en file

> « Une commande : huit agents. Planner, Backend, Frontend, QA, Security,
> DevOps, Docs, Reviewer — chacun son modèle Mistral, chacun ses permissions :
> le Planner et le Reviewer ne peuvent techniquement pas écrire de fichier. »

Montrer : les 8 nœuds visibles dès la première seconde (`idle — Waiting for
Planner`), le Planner qui pulse, puis sa décision `plan` dans le feed et son
broadcast dans Messages. Les chips de timing apparaissent (~3 s de premier
geste : le warm-start évite la re-exploration du repo).

### 1:40 – 3:00 — La coordination live (le money shot)

La vague 2 démarre : Backend ∥ Frontend en parallèle.

> « Ils négocient en temps réel sur un blackboard partagé : contrats,
> questions, messages, verrous de fichiers. »

Montrer, dans l'ordre où ça arrive :
- le panneau **Messages** (broadcast « contracts published ») ;
- si Frontend passe `blocked — waiting on Backend` : *« Pas de conflit de
  merge, pas d'API hallucinée — ils négocient. »* puis son déblocage ;
- **File claims** en bas ; **clic sur le nœud Backend** → tiroir de détails
  (modèle, timings, activité live) ;
- onglet **Changes** → cliquer un fichier → **le diff du code réellement
  écrit, ligne par ligne**.

### 3:00 – 4:00 — La fin de partie (onglet 2, board pré-calculé)

> « Voici le même système au bout de sa course, sur un projet piégé : on
> avait planté un bug dans le scoring du code de départ. »

Montrer sur 8790 :
1. Feed : verdict QA **`FAIL:`** → Messages : broadcast **« Orchestrator: QA
   failed — retry 1/3 »** → Backend re-passé `working` → verdict **`PASS:`**.
   *« La boucle qualité est automatique et bornée. »*
2. Les messages inter-agents dans **Messages** (kickoff du Planner, contrat
   de Backend). Pour l'histoire Security : elle se joue en live sur l'onglet
   1 (l'audit y tourne), et le board de backup 8789 montre un vrai
   « Security FAIL → Reviewer NO-GO » si un juré demande.
3. Le verdict **`GO:`** du Reviewer dans le feed. Puis dans un second
   terminal (la variable HOOK une fois pour toutes) :
   ```sh
   HOOK=~/.local/share/uv/tools/mistral-vibe/bin/python
   # sans verdict Reviewer (le run live) -> REFUSÉ :
   echo '{"tool_name":"bash","tool_input":{"command":"git push"},"cwd":"/tmp/demo-live"}' | $HOOK -m vibe.workflow.hooks.guard_push
   # avec le GO du Reviewer (le run complet) -> silence = autorisé :
   echo '{"tool_name":"bash","tool_input":{"command":"git push"},"cwd":"/tmp/demo-full"}' | $HOOK -m vibe.workflow.hooks.guard_push
   ```
   *« Ce verdict n'est pas décoratif : un hook natif refuse `git push` tant
   que le Reviewer n'a pas dit GO. »*

### 4:00 – 4:40 — L'app qui tourne (onglet 3)

> « Et le produit de tout ça, ce n'est pas un rapport — c'est une app. »

1. Onglet **Changes** du board 8790 : cliquer `game_logic.py` → **le diff du
   fix de Backend** (`- current_score - 10` / `+ current_score + 10`) :
   *« Voilà la correction que la boucle qualité a exigée, ligne par ligne. »*
2. Onglet 3 : **le jeu construit par les agents, en marche**. Retourner deux
   cartes, matcher une paire de chats : **le score fait +10**.
   *« Dans le code de départ, chaque paire trouvée vous faisait PERDRE dix
   points. QA l'a attrapé, Backend l'a corrigé, le Reviewer a validé — sans
   intervention humaine. Et accessoirement : huit agents viennent de vous
   livrer un jeu. »*

### 4:40 – 5:00 — La chute

Retour terminal, dans la session vibe :
```
/workflow status
```

> « Tout ça sans quitter la CLI — et tout est natif : sous-commande
> `vibe workflow`, outils natifs, hooks natifs, skill natif. Le feed de
> décisions, c'est le journal de conception écrit par les agents eux-mêmes.
> Vibe aujourd'hui, c'est un agent. MiaouFlow en fait une équipe. »

---

## Règles de survie

- **Un truc traîne en longueur → basculer sur l'onglet 8790 sans s'excuser**
  et dérouler la fin de partie ; le live n'a pas besoin de finir.
- L'app de l'onglet 3 tourne depuis AVANT la démo — on ne lance rien en
  live, on ne fait que s'en servir.
- Le run live n'ira que jusqu'à la vague 2 en 5 min : c'est prévu, la fin
  est sur l'onglet 2.
- Board figé → F5 ; le pill passe `Reconnecting` → `Live` tout seul (repli
  polling automatique).
- Catastrophe totale → enregistrement de secours, même narration.
- Ne jamais montrer le JSON brut : le board raconte mieux.

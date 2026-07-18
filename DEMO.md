# MiaouFlow — Démo 5 minutes (fin de hackathon)

## Préparation (15 min avant, obligatoire)

```sh
# 1. Ports et processus propres
pkill -f "vibe workflow" ; pkill -f "python -m vibe"

# 2. Pré-calculer le board "fin de partie" (8 agents complets + boucle qualité)
D=/tmp/demo-full && rm -rf $D && mkdir -p $D
printf 'def add(a, b):\n    return a - b  # bug volontaire\n' > $D/calculator.py
printf 'pytest\n' > $D/requirements.txt
vibe workflow run --goal "Extend calculator.py with multiply and a tiny web calculator UI. QA: test add and multiply against their mathematical promises and fail the verdict if wrong. Backend: fix what QA reports." --workdir $D --no-board --timeout 900
# (~10-15 min ; vérifier à la fin : QA FAIL -> retry -> PASS, verdict GO du Reviewer)

# 3. Servir ce board pré-calculé sur un port séparé (onglet 2 du navigateur)
(vibe workflow board --workdir $D --port 8790 &)

# 4. Préparer le projet du run LIVE (onglet 1 : http://127.0.0.1:8787)
L=/tmp/demo-live && rm -rf $L && mkdir -p $L && cd $L

# 5. Navigateur : onglet 1 = 8787 (vide pour l'instant), onglet 2 = 8790 (complet)
# 6. Enregistrement de secours prêt. Terminal en police large.
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

### 3:00 – 4:15 — La fin de partie (onglet 2, board pré-calculé)

> « Voici le même système au bout de sa course, sur un projet piégé : on
> avait planté un bug dans le code de départ. »

Montrer sur 8790 :
1. Feed : verdict QA **`FAIL:`** → Messages : broadcast **« Orchestrator: QA
   failed — retry 1/3 »** → Backend re-passé `working` → verdict **`PASS:`**.
   *« La boucle qualité est automatique et bornée. »*
2. Les messages de **Security** vers Backend (audit sans droit d'écriture).
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

### 4:15 – 5:00 — La chute

Retour terminal, dans la session vibe :
```
/workflow status
```

> « Tout ça sans quitter la CLI — et tout est natif : sous-commande
> `vibe workflow`, outils natifs, hooks natifs, skill natif. Le feed de
> décisions que vous voyez, c'est le journal de conception du projet, écrit
> par les agents eux-mêmes. Vibe aujourd'hui, c'est un agent. MiaouFlow en
> fait une équipe. »

(Si le temps le permet : `vibe workflow init` sur un dossier vide pour
montrer les questions de composition d'équipe — 15 s, sans lancer le run :
Ctrl-C après l'affichage « Team of 8 ».)

---

## Règles de survie

- **Un truc traîne en longueur → basculer sur l'onglet 8790 sans s'excuser**
  et dérouler la fin de partie ; le live n'a pas besoin de finir.
- Le run live n'ira que jusqu'à la vague 2 en 5 min : c'est prévu, la fin
  est sur l'onglet 2.
- Board figé → F5 ; le pill passe `Reconnecting` → `Live` tout seul (repli
  polling automatique).
- Catastrophe totale → enregistrement de secours, même narration.
- Ne jamais montrer le JSON brut : le board raconte mieux.

# MiaouFlow demo project

Seed project for the `vibe workflow` golden-path demo: a small **Todo API with
JWT auth**.

Conventions for the agent team:

- Backend owns `server/` — a Flask app in `server/app.py` with register/login
  (returning a JWT) and `/todos` CRUD guarded by it.
- Frontend owns `web/` — a minimal `web/index.html` client that talks to the
  published API contract.
- QA owns `tests/` — acceptance tests derived from the blackboard contracts.
- Python dependencies go in `requirements.txt`.

Run the demo from the repo root:

```sh
vibe workflow run --goal "Build a small Todo API with JWT auth" --workdir demo-project
```

Then watch the team coordinate live at <http://127.0.0.1:8787>.

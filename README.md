# Party Pulse

Self-hosted, anonymous party-questionnaire app with live big-screen stats and a guessing game.
Built for a wedding reception. See [`PLAN.md`](PLAN.md) for the full design and what's built vs.
backlog.

## Local development

```sh
uv sync
DJANGO_DEBUG=1 uv run python manage.py migrate
DJANGO_DEBUG=1 uv run python manage.py seed_questions   # 28 curated questions, all draft
DJANGO_DEBUG=1 uv run python manage.py createsuperuser  # the one shared host/admin login
DJANGO_DEBUG=1 uv run python manage.py runserver
```

- Guest app: <http://localhost:8000/>
- Host console: <http://localhost:8000/host/> (flip questions from draft → live in
  `/admin/pulse/question/` first)
- Big screen: <http://localhost:8000/screen/>

Run tests with `uv run pytest`.

## Containerized deployment

```sh
podman build -t party-pulse .
podman run -d -p 8000:8000 -v party-pulse-data:/data party-pulse
```

The image defaults `DJANGO_DB_PATH` to `/data/db.sqlite3` and runs migrations automatically on
start (see `docker-entrypoint.sh`) — just mount a volume at `/data`. For a real deployment, also
set `DJANGO_DEBUG=0`, `DJANGO_SECRET_KEY`, and `DJANGO_ALLOWED_HOSTS` (see `.env.example`).

CI (`.github/workflows/docker-build.yml`) builds and tests every push/PR, and publishes to
`ghcr.io/jswetzen/party-pulse` on push to `main` — mirroring this household's other small
self-hosted apps (see the `mikro-iac` repo's `docs/poc-lyrics.md` for the conventions this
follows). Wiring an actual deployment CT (Terraform, Traefik route, secrets) is a separate,
not-yet-done step in that repo.

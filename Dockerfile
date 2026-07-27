# See README.md "Containerized deployment" / PLAN.md "Hosting".

FROM docker.io/library/python:3.12-alpine AS builder

RUN apk add --no-cache gcc musl-dev
COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/

WORKDIR /app
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-install-project --no-dev
COPY manage.py ./
COPY partypulse/ partypulse/
COPY pulse/ pulse/
RUN uv sync --frozen --no-dev

# collectstatic only needs settings to import, not a real secret/DB.
RUN DJANGO_DEBUG=1 uv run python manage.py collectstatic --noinput

FROM docker.io/library/python:3.12-alpine

RUN adduser -D -u 1000 partypulse
WORKDIR /app
COPY --from=builder /app /app
ENV PATH="/app/.venv/bin:$PATH"

COPY docker-entrypoint.sh /docker-entrypoint.sh
RUN chmod +x /docker-entrypoint.sh

# /data holds db.sqlite3 — mount a volume here so guest responses survive a
# container restart/redeploy. Baked in as the default (not just documented
# in .env.example) so a deployer who forgets to set DJANGO_DB_PATH gets a
# working, if unpersisted, container instead of a silent "unable to open
# database file" crash from trying to write into the root-owned /app.
VOLUME /data
RUN mkdir -p /data && chown partypulse:partypulse /data
ENV DJANGO_DB_PATH=/data/db.sqlite3
USER partypulse

EXPOSE 8000
ENTRYPOINT ["/docker-entrypoint.sh"]
CMD ["gunicorn", "partypulse.wsgi:application", "--bind", "0.0.0.0:8000"]

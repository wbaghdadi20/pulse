# Pulse

Pulse is an uptime monitor and status page. It checks HTTP services on a schedule,
records every result, opens and resolves incidents, sends signed alerts to a webhook,
and serves a public status page.

Built with Django, PostgreSQL and Docker Compose.

## Prerequisites

- [Git](https://git-scm.com/)
- [Docker](https://docs.docker.com/get-docker/) with Docker Compose v2 (`docker compose version`)
- Python 3 (only to generate a secret key; the app itself runs in Docker)

## Quick start

1. Clone the repo:

   ```bash
   git clone https://github.com/wbaghdadi20/pulse.git
   cd pulse
   ```

2. Create your local environment file:

   ```bash
   cp .env.example .env
   ```

3. Generate a secret key and paste it into `.env` as `DJANGO_SECRET_KEY`:

   ```bash
   python3 -c "import secrets; print(secrets.token_urlsafe(50))"
   ```

   Also change `POSTGRES_PASSWORD` to a password of your choice.

4. Build and start the stack:

   ```bash
   docker compose up --build -d --wait
   ```

5. Create the database tables:

   ```bash
   docker compose exec api python manage.py migrate
   ```

6. Check it works:

   ```bash
   curl -i http://localhost:8100/health
   ```

   Expected: `HTTP/1.1 200 OK` with `{"status": "ok"}`.

## Services and ports

| Service    | Description           | URL / port on your machine                 |
| ---------- | --------------------- | ------------------------------------------ |
| `api`      | Django app (gunicorn) | http://localhost:8100                      |
| `postgres` | PostgreSQL 16         | `localhost:5433` (bound to 127.0.0.1 only) |

Host ports 8100 and 5433 are used instead of 8000 and 5432 so Pulse can run
alongside the EasyeSuite stack.

## Everyday commands

| Command                                              | What it does                                     |
| ---------------------------------------------------- | ------------------------------------------------ |
| `docker compose up --build -d --wait`                | Build and start everything in the background     |
| `docker compose ps`                                  | Show running services and their health           |
| `docker compose logs -f api`                         | Follow the app's logs (Ctrl+C stops following)   |
| `docker compose exec api python manage.py <command>` | Run a Django management command                  |
| `docker compose down`                                | Stop everything (database data is kept)          |
| `docker compose down -v`                             | Stop everything **and delete the database data** |

## Configuration

All settings come from environment variables in `.env` (never committed).
`.env.example` lists every variable with placeholder values.

| Variable               | Description                                                           |
| ---------------------- | --------------------------------------------------------------------- |
| `DJANGO_SECRET_KEY`    | Secret used by Django for signing. Required.                          |
| `DJANGO_DEBUG`         | `true` for local development, `false` otherwise. Defaults to `false`. |
| `DJANGO_ALLOWED_HOSTS` | Comma-separated hostnames Django will serve.                          |
| `POSTGRES_DB`          | Database name.                                                        |
| `POSTGRES_USER`        | Database user.                                                        |
| `POSTGRES_PASSWORD`    | Database password.                                                    |
| `POSTGRES_HOST`        | Database host. `postgres` (the compose service name) inside Docker.   |
| `POSTGRES_PORT`        | Database port inside the Docker network. Defaults to `5432`.          |

## Troubleshooting

- **`port is already allocated`**: another program (often the EasyeSuite stack) is using
  8100 or 5433. Stop it, or change the host port in `compose.yaml`.
- **The app container keeps restarting or exits**: check `docker compose logs api`.
  A `KeyError` means a variable is missing from `.env`.

## Development

Install the git hooks once after cloning (requires [pre-commit](https://pre-commit.com/)):

    pre-commit install

Run all checks manually with `pre-commit run --all-files`.

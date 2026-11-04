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
| `api`      | Django app            | http://localhost:8100                      |
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

## API

Built with Django REST framework. Open any endpoint in a browser for DRF's
browsable API.

| Method + URL                   | Does                         | Success | Errors   |
| ------------------------------ | ---------------------------- | ------- | -------- |
| `GET /health`                  | liveness check               | 200     |          |
| `POST /monitors/`              | create a monitor             | 201     | 400, 409 |
| `GET /monitors/?limit&offset`  | list monitors (paginated)    | 200     |          |
| `GET /monitors/<id>/`          | get one monitor              | 200     | 404      |
| `PATCH /monitors/<id>/`        | change some fields           | 200     | 400, 404, 409 |
| `DELETE /monitors/<id>/`       | delete a monitor             | 204     | 404      |
| `POST /monitors/<id>/check/`   | call the monitor's URL once  | 200     | 404      |

Example:

```bash
curl -X POST http://localhost:8100/monitors/ -H 'Content-Type: application/json' \
  -d '{"name": "httpbin", "url": "https://httpbin.org/get", "timeout_s": 5, "interval_s": 30}'
```

Conventions:

- **Invalid input returns 400** with one message per field. That is DRF's
  convention (FastAPI uses 422).
- **URLs end with a slash** (`/monitors/`), the Django convention. A `POST` without
  the slash fails, because Django can't redirect a `POST` and keep its body.
- **Pagination** uses `?limit=` (default 20, max 100) and `?offset=`. The response is
  `{"count": <total>, "next": <url>, "previous": <url>, "results": [...]}`.
- **"Check now" always returns 200.** A target that is down is a result, not an
  error in Pulse. `error_type` is one of `timeout`, `connection_refused`, `dns`,
  `wrong_status`, or `error` (anything else, e.g. a TLS failure).

### Which endpoints are idempotent

Idempotent means calling it twice leaves the system in the same state as calling it once.

| Endpoint             | Idempotent? | Why |
| -------------------- | ----------- | --- |
| `GET` (all)          | yes         | reads only, changes nothing |
| `PATCH /monitors/<id>/` | yes      | it sets fields to values; setting `timeout_s` to 10 twice still leaves 10 |
| `DELETE /monitors/<id>/` | yes     | after the first call the monitor is gone, and a second call changes nothing. The *response* differs (204, then 404), but idempotency is about the effect, not the response |
| `POST /monitors/`    | no          | each call tries to create a new monitor (the second one gets 409 only because names are unique) |
| `POST /monitors/<id>/check/` | no  | each call makes a new request to the target and records a new result |

## Networking

Inside a container, `localhost` means **that container**, not your laptop. So from
inside Pulse, `http://localhost:8000` is Pulse itself, not the EasyeSuite backend
that your laptop reaches at `localhost:8000`.

Docker Compose gives each project its own network, and containers on the same
network reach each other by **service name** through Docker's built-in DNS. That is
why the `api` container reaches its database at `postgres:5432`. To monitor
EasyeSuite, the `api` service joins EasyeSuite's network as an external network.
Then `http://django:8000/ht/` and `http://frontend:3000/` resolve to those
containers:

```yaml
services:
  api:
    networks: [default, easyesuite]
networks:
  easyesuite:
    external: true
    name: <name from `docker network ls`>
```

Monitor URLs are validated with a custom validator because Django's `URLValidator`
rejects hostnames without a dot (such as `django` or `frontend`).

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
- **`password authentication failed for user`**: Postgres only applies `POSTGRES_PASSWORD`
  when its data volume is first created. If you changed the password in `.env` later,
  reset the database with `docker compose down -v` (deletes all data), then
  `docker compose up -d --wait` and run `migrate` again.

## Development

Install the git hooks once after cloning (requires [pre-commit](https://pre-commit.com/)):

    pre-commit install

Run all checks manually with `pre-commit run --all-files`.

`compose.override.yaml` is merged automatically by Docker Compose. It mounts the
code into the `api` container and runs Django's `runserver`, which reloads on save.
To run the production-style gunicorn setup instead:

    docker compose -f compose.yaml up --build -d --wait

Passing -f compose.yaml tells Compose to use only that file, so the override isn't merged in.

FROM python:3.14-slim AS base

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PATH="/venv/bin:$PATH"

RUN useradd --create-home --uid 1000 app

WORKDIR /app


# Installs what the app needs to run into /venv, the one directory the final stage takes.
FROM base AS builder

RUN python -m venv /venv

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt


# Local development and tests (compose.override.yaml): the tools on top, and the whole repo.
FROM builder AS dev

COPY requirements-dev.txt .
RUN pip install --no-cache-dir -r requirements-dev.txt

COPY . .

USER app

EXPOSE 8000


# What is published. The last stage, so a plain `docker build .` produces it.
FROM base AS runtime

COPY --from=builder /venv /venv

COPY manage.py gunicorn.conf.py ./
COPY accounts/ accounts/
COPY config/ config/
COPY core/ core/
COPY monitors/ monitors/

USER app

EXPOSE 8000

CMD ["gunicorn", "config.wsgi:application"]

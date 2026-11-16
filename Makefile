# Shortcuts for the commands typed most. `make` alone lists them.
#
# Each target prints the commands it runs, and every one of them can be typed by
# hand instead: README.md and LOADTEST.md say what each part means. A value is
# given as NAME=value after the target: make logs SERVICE=worker

DEV      := docker compose
SHOP     := docker compose -f shop/compose.yaml
# Naming the files leaves compose.override.yaml out: api is then the runtime image
# under gunicorn, on its own database.
LOADTEST := docker compose -f compose.yaml -f loadtest/compose.loadtest.yaml
LOCUST   := loadtest/.venv/bin/locust

SERVICE  ?= api
DB       ?=
ARGS     ?=
WORKERS  ?= 4
USERS    ?= 50
RATE     ?= 5
DURATION ?= 2m

# migrate and the seed run in whichever api container is up. If that is the
# development one they would land in the development database, so the load-test
# targets refuse unless api is the load-test one.
define require_loadtest_api
@test "$$($(DEV) exec -T api printenv POSTGRES_DB 2>/dev/null)" = pulse_loadtest \
	|| { echo "api is not running as it is load-tested. First: make loadtest-up"; exit 1; }
endef

.DEFAULT_GOAL := help
.PHONY: help up down migrate test logs psql status rollup demo-break demo-fix \
	loadtest-up loadtest-seed loadtest loadtest-down

help: ## List the targets
	@awk -F ':.*## ' '/^[a-z-]+:.*## / {printf "  make %-14s %s\n", $$1, $$2}' $(MAKEFILE_LIST)

up: ## Start Pulse and shop for development (also the way back from a load test)
	@test -f .env || { echo "No .env yet: README, Quick start, steps 2 and 3"; exit 1; }
	docker network inspect pulse-targets > /dev/null 2>&1 || docker network create pulse-targets
	$(DEV) run --rm api python manage.py migrate
	$(DEV) up -d --build --wait
	$(SHOP) up -d --build --wait

down: ## Stop Pulse and shop (database data is kept)
	$(SHOP) down
	$(DEV) down

migrate: ## Apply database migrations
	$(DEV) exec api python manage.py migrate

test: ## Run the tests. One file: make test ARGS=tests/monitors/test_retention.py
	$(DEV) exec api pytest -q -n auto $(ARGS)

logs: ## Follow one service's log: make logs SERVICE=worker
	$(DEV) logs -f $(SERVICE)

psql: ## Open psql on Pulse's database, or another: make psql DB=pulse_loadtest
	$(DEV) exec postgres sh -c 'psql -U "$$POSTGRES_USER" -d "$(if $(DB),$(DB),$$POSTGRES_DB)"'

status: ## Print the state of each monitor on the status page
	@curl -s localhost:8100/status | python3 -c 'import json, sys; [print(m["state"], m["name"], sep="\t") for m in json.load(sys.stdin)["monitors"]]'

rollup: ## Run the hourly roll-up of checks now
	$(DEV) exec worker celery -A config call monitors.tasks.roll_up_checks

demo-break: ## Stop shop's Redis: the deep monitor fails, the shallow one does not
	$(SHOP) stop shop-redis

demo-fix: ## Start shop's Redis again
	$(SHOP) start shop-redis

loadtest-up: ## Run api as it is load-tested: make loadtest-up WORKERS=8
	$(DEV) stop worker beat
	WEB_CONCURRENCY=$(WORKERS) $(LOADTEST) up -d --build --wait api

loadtest-seed: ## Create the load-test database, its tables and its data (needed once)
	$(require_loadtest_api)
	$(DEV) exec postgres sh -c 'psql -U "$$POSTGRES_USER" -d "$$POSTGRES_DB" -lqt | cut -d "|" -f 1 | grep -qw pulse_loadtest || createdb -U "$$POSTGRES_USER" pulse_loadtest'
	$(LOADTEST) exec api python manage.py migrate
	$(LOADTEST) exec -T api python manage.py shell < loadtest/seed.py

# A file target: Locust is installed the first time, and again when its pins change.
$(LOCUST): loadtest/requirements.txt
	python3 -m venv loadtest/.venv
	loadtest/.venv/bin/pip install --quiet -r loadtest/requirements.txt

loadtest: $(LOCUST) ## Run the load test: make loadtest USERS=50 RATE=5 DURATION=2m
	$(require_loadtest_api)
	$(LOCUST) -f loadtest/locustfile.py --headless -u $(USERS) -r $(RATE) -t $(DURATION) --host http://localhost:8100

loadtest-down: up ## Back to the development stack

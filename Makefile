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
MONITOR  ?= 1
DAYS     ?= 30
# The organization `status`, `explain` and the demo targets look at. Its schema is
# org_ and the slug with "_" for "-".
ORG      ?= acme
SCHEMA   := org_$(subst -,_,$(ORG))

# migrate and the seed run in whichever api container is up. If that is the
# development one they would land in the development database, so the load-test
# targets refuse unless api is the load-test one.
define require_loadtest_api
@test "$$($(DEV) exec -T api printenv POSTGRES_DB 2>/dev/null)" = pulse_loadtest \
	|| { echo "api is not running as it is load-tested. First: make loadtest-up"; exit 1; }
endef

.DEFAULT_GOAL := help
.PHONY: help up down migrate seed test logs psql explain status rollup \
	demo-ready demo-watch demo-break demo-fix demo-alerts \
	loadtest-up loadtest-seed loadtest loadtest-down

help: ## List the targets
	@awk -F ':.*## ' '/^[a-z-]+:.*## / {printf "  make %-14s %s\n", $$1, $$2}' $(MAKEFILE_LIST)

up: ## Start Pulse and shop for development (also the way back from a load test)
	@test -f .env || { echo "No .env yet: README, Quick start, steps 2 and 3"; exit 1; }
	docker network inspect pulse-targets > /dev/null 2>&1 || docker network create pulse-targets
	$(DEV) run --rm api python manage.py migrate_all
	$(DEV) up -d --build --wait
	$(SHOP) up -d --build --wait

down: ## Stop Pulse and shop (database data is kept)
	$(SHOP) down
	$(DEV) down

migrate: ## Apply migrations to public, the template schema and every organization
	$(DEV) exec api python manage.py migrate_all

seed: ## Create the demo organizations acme and globex, and shop's monitors in acme
	$(DEV) exec api python manage.py seed_demo

test: ## Run the tests. One file: make test ARGS=tests/monitors/test_retention.py
	$(DEV) exec api pytest -q -n auto $(ARGS)

logs: ## Follow one service's log: make logs SERVICE=worker
	$(DEV) logs -f $(SERVICE)

psql: ## Open psql on Pulse's database, or another: make psql DB=pulse_loadtest
	$(DEV) exec postgres sh -c 'psql -U "$$POSTGRES_USER" -d "$(if $(DB),$(DB),$$POSTGRES_DB)"'

explain: ## How Postgres runs the uptime query in an organization: make explain ORG=acme MONITOR=1 DAYS=30
	$(DEV) exec postgres sh -c 'psql -U "$$POSTGRES_USER" -d "$(if $(DB),$(DB),$$POSTGRES_DB)" -c "SET search_path TO $(SCHEMA)" -c "EXPLAIN (ANALYZE, BUFFERS) SELECT count(*), count(ok) FILTER (WHERE NOT ok), percentile_cont(0.95) WITHIN GROUP (ORDER BY latency_ms) FROM checks WHERE monitor_id = $(MONITOR) AND checked_at > now() - make_interval(days => $(DAYS))"'

status: ## Print the state of each monitor on an organization's status page: make status ORG=acme
	@curl -s localhost:8100/t/$(ORG)/status | python3 -c 'import json, sys; [print(m["state"], m["name"], sep="\t") for m in json.load(sys.stdin)["monitors"]]'

rollup: ## Run the hourly roll-up of checks now
	$(DEV) exec worker celery -A config call monitors.tasks.roll_up_checks

demo-ready: ## Before the demo: is everything it needs running? Each line says ok, or what to run
	@curl -fs -o /dev/null localhost:8100/ready \
		&& echo "ok    Pulse answers" || echo "FAIL  Pulse does not answer.        Run: make up"
	@test "$$($(DEV) exec -T api printenv POSTGRES_DB 2>/dev/null)" != pulse_loadtest \
		&& echo "ok    api is the development one" || echo "FAIL  api is in load-test mode.       Run: make loadtest-down"
	@test "$$($(DEV) ps --status running --services 2>/dev/null | grep -cE '^(worker|beat)$$')" = 2 \
		&& echo "ok    worker and beat are running" || echo "FAIL  worker or beat is stopped.      Run: make up"
	@test "$$($(SHOP) ps --status running --services 2>/dev/null | grep -c .)" = 3 \
		&& echo "ok    shop, shop-db and shop-redis are running" || echo "FAIL  part of shop is stopped.        Run: make demo-fix, or make up"
	@curl -fs -o /dev/null localhost:8400/ \
		&& echo "ok    catcher answers" || echo "FAIL  catcher does not answer.      Run: make up"
	@curl -s localhost:8100/t/$(ORG)/status | python3 -c 'import json, sys; m = json.load(sys.stdin)["monitors"]; d = [x["name"] for x in m if x["state"] == "down"]; print("ok    %d monitors, all up" % len(m) if m and not d else "FAIL  down: %s. Run: make demo-fix, wait 30 s, make demo-ready" % (", ".join(d) or "no public monitor at all"))' 2>/dev/null \
		|| echo "FAIL  no status page for $(ORG).      Run: make up, then make seed"

demo-watch: ## Follow the worker: failed checks and alerts sent, as they happen (Ctrl+C stops)
	$(DEV) logs -f --since 1m worker | grep --line-buffered -oE '"message": "(monitor [0-9]+ ok=False[^"]*|alert [0-9]+ sent[^"]*)"'

demo-break: ## Stop shop's Redis: the deep monitor fails, the shallow one does not. Undo: make demo-fix
	$(SHOP) stop shop-redis

demo-fix: ## Start shop's Redis again
	$(SHOP) start shop-redis

demo-alerts: ## The alerts the catcher received, newest first, with their signatures
	@curl -s localhost:8400/ | python3 -c 'import json, sys; [print(a["received_at"][11:19], a["body"]["event"], a["body"]["monitor"], "downtime_seconds=%s" % a["body"]["downtime_seconds"], "signature=%s..." % a["headers"]["x-pulse-signature"][:16], sep="  ") for a in json.load(sys.stdin)]'

loadtest-up: ## Run api as it is load-tested: make loadtest-up WORKERS=8. Undo: make loadtest-down
	$(DEV) stop worker beat
	WEB_CONCURRENCY=$(WORKERS) $(LOADTEST) up -d --build --wait api

loadtest-seed: ## Create the load-test database, its tables and its data (needed once)
	$(require_loadtest_api)
	$(DEV) exec postgres sh -c 'psql -U "$$POSTGRES_USER" -d "$$POSTGRES_DB" -lqt | cut -d "|" -f 1 | grep -qw pulse_loadtest || createdb -U "$$POSTGRES_USER" pulse_loadtest'
	$(LOADTEST) exec api python manage.py migrate_all
	$(LOADTEST) exec -T api python manage.py shell < loadtest/seed.py

# A file target: Locust is installed the first time, and again when its pins change.
$(LOCUST): loadtest/requirements.txt
	python3 -m venv loadtest/.venv
	loadtest/.venv/bin/pip install --quiet -r loadtest/requirements.txt

loadtest: $(LOCUST) ## Run the load test: make loadtest USERS=50 RATE=5 DURATION=2m
	$(require_loadtest_api)
	$(LOCUST) -f loadtest/locustfile.py --headless -u $(USERS) -r $(RATE) -t $(DURATION) --host http://localhost:8100

loadtest-down: up ## Back to the development stack

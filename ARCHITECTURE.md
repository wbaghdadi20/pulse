# Architecture

Pulse described with the [C4 model](https://c4model.com/): context, containers,
components. Level 4 (code) is the code itself; in its place are the data model, the
three flows worth tracing end to end, and the two state machines.

The diagrams are Mermaid, so they live in Git, are reviewed in the same PR as the
code they describe, and render on GitHub without a build step. Decisions and their
alternatives are in [`docs/adr/`](docs/adr/).

Box notation: **name**, [type: technology], what it does. Arrow labels: what
travels, [protocol].

## Level 1: system context

Who uses Pulse and what it talks to.

```mermaid
flowchart TB
    operator["<b>Operator</b><br/>[Person]<br/>Creates monitors, reads uptime,<br/>sets maintenance windows"]
    oncall["<b>On-call engineer</b><br/>[Person]<br/>Reads alerts"]
    pulse["<b>Pulse</b><br/>[Software system]<br/>Checks HTTP services on a schedule,<br/>records every result, opens and resolves<br/>incidents, sends signed alerts"]
    targets["<b>Monitored services</b><br/>[External systems]<br/>flaky and shop today"]
    receiver["<b>Alert receiver</b><br/>[External system]<br/>catcher, standing in for<br/>Slack or PagerDuty"]

    operator -- "manages monitors<br/>[JSON over HTTP]" --> pulse
    pulse -- "checks<br/>[HTTP GET or HEAD]" --> targets
    pulse -- "incident opened or resolved<br/>[signed webhook, HTTP POST]" --> receiver
    oncall -- "reads alerts<br/>[HTTP]" --> receiver

    classDef person fill:#08427b,stroke:#052e56,color:#ffffff
    classDef system fill:#1168bd,stroke:#0b4884,color:#ffffff
    classDef external fill:#6b6b6b,stroke:#4a4a4a,color:#ffffff
    class operator,oncall person
    class pulse system
    class targets,receiver external
```

Pulse only reaches services over HTTP. It never sees a target's database or cache:
it learns about them through the target's own deep health check.

## Level 2: containers

What runs. `api`, `worker` and `beat` are the same image and codebase started with
three different commands.

```mermaid
flowchart TB
    operator["<b>Operator</b><br/>[Person]"]

    subgraph pulse ["Pulse"]
        api["<b>api</b><br/>[Container: Django + DRF on gunicorn]<br/>REST API: monitors, check now,<br/>uptime, incidents"]
        beat["<b>beat</b><br/>[Container: Celery beat]<br/>The clock. Queues one<br/>enqueue_due_checks every 10 s"]
        worker["<b>worker</b><br/>[Container: Celery worker]<br/>Runs checks, evaluates incidents,<br/>delivers alerts"]
        redis[("<b>redis</b><br/>[Container: Redis 7]<br/>Celery broker: the task queue")]
        postgres[("<b>postgres</b><br/>[Container: PostgreSQL 16]<br/>monitors, checks,<br/>incidents, alerts")]
    end

    targets["<b>flaky, shop</b><br/>[External systems: FastAPI, Django]<br/>Monitored services"]
    catcher["<b>catcher</b><br/>[External system: FastAPI]<br/>Alert inbox, verifies signatures"]

    operator -- "REST calls<br/>[JSON over HTTP, host port 8100]" --> api
    api -- "reads and writes<br/>[SQL, psycopg]" --> postgres
    api -- "queues send_alert<br/>[Redis protocol]" --> redis
    api -- "check now<br/>[HTTP]" --> targets
    beat -- "queues enqueue_due_checks<br/>[Redis protocol]" --> redis
    worker -- "takes tasks, queues run_check<br/>and send_alert [Redis protocol]" --> redis
    worker -- "reads and writes<br/>[SQL, psycopg]" --> postgres
    worker -- "scheduled checks<br/>[HTTP]" --> targets
    worker -- "signed alert<br/>[HTTP POST, HMAC-SHA256]" --> catcher

    classDef person fill:#08427b,stroke:#052e56,color:#ffffff
    classDef container fill:#438dd5,stroke:#2e6295,color:#ffffff
    classDef store fill:#438dd5,stroke:#2e6295,color:#ffffff
    classDef external fill:#6b6b6b,stroke:#4a4a4a,color:#ffffff
    class operator person
    class api,beat,worker container
    class redis,postgres store
    class targets,catcher external
```

| Container  | Scales how                                   | State                         |
| ---------- | -------------------------------------------- | ----------------------------- |
| `api`      | more replicas behind a load balancer         | none                          |
| `worker`   | `--scale worker=N`                           | none                          |
| `beat`     | exactly one, two would queue everything twice | schedule file in `/tmp`       |
| `redis`    | single instance                              | queued tasks, no named volume |
| `postgres` | single instance                              | the system of record (volume `pgdata`) |

`api` can queue a task as well as `worker`: "check now" runs the same service code,
and if that check opens an incident, the alert is queued from the request.

### Deployment: Compose projects and networks

`flaky` and `catcher` are services of the `pulse` Compose project for convenience,
but Pulse treats them as outsiders. `shop` is a separate project, like a service
another team operates.

```mermaid
flowchart LR
    host["<b>Laptop</b><br/>localhost"]

    subgraph pulseproj ["Compose project: pulse, network pulse_default"]
        api["api<br/>:8000"]
        worker["worker"]
        beat["beat"]
        redis[("redis<br/>:6379")]
        postgres[("postgres<br/>:5432")]
        flaky["flaky<br/>:8000"]
        catcher["catcher<br/>:8000"]
    end

    subgraph shopproj ["Compose project: shop, network shop_default"]
        shop["shop<br/>:8000"]
        shopdb[("shop-db")]
        shopredis[("shop-redis")]
    end

    host -- "8100" --> api
    host -- "8200" --> flaky
    host -- "8400" --> catcher
    host -- "127.0.0.1:5433" --> postgres
    host -- "8300" --> shop
    api -. "pulse-net" .-> shop
    worker -. "pulse-net" .-> shop
    shop --> shopdb
    shop --> shopredis
```

| Network         | Members                                                   | Purpose                                 |
| --------------- | --------------------------------------------------------- | --------------------------------------- |
| `pulse_default` | api, worker, beat, redis, postgres, flaky, catcher        | Pulse's own traffic                     |
| `pulse-net`     | api, worker, shop                                         | the only path from Pulse to shop        |
| `shop_default`  | shop, shop-db, shop-redis                                 | shop's private dependencies             |

`shop-db` and `shop-redis` are unreachable from Pulse by design.

## Level 3: components

Inside the Django codebase that `api` and `worker` both run. Arrows point from
caller to callee. An HTTP request enters at the top left, a Celery task at the top
right, and both meet in `services`.

```mermaid
flowchart TB
    http(["HTTP request"])
    task(["Celery task message"])

    subgraph entry ["Entry points: no business rules"]
        views["<b>monitors/views.py</b><br/>[DRF APIView]<br/>Parse, call a service, shape the response"]
        serializers["<b>monitors/serializers.py</b><br/>[DRF serializers]<br/>Validate input, shape output"]
        handler["<b>core/exception_handler.py</b><br/>Domain errors to 404 and 409"]
        tasks["<b>monitors/tasks.py</b><br/>[Celery tasks]<br/>enqueue_due_checks, run_check, send_alert"]
    end

    subgraph domain ["Domain: no HTTP in, no Celery in"]
        services["<b>monitors/services.py</b><br/>Business rules: CRUD, check_monitor,<br/>evaluate_incidents, uptime, open and resolve"]
        incidents["<b>monitors/incidents.py</b><br/>[Pure function]<br/>decide: open, resolve or nothing"]
        alerting["<b>monitors/alerting.py</b><br/>Record an alert, sign it, deliver it,<br/>retry policy"]
        checks["<b>monitors/checks.py</b><br/>probe: one HTTP call,<br/>classified into a CheckResult"]
    end

    subgraph infra ["Infrastructure"]
        models["<b>monitors/models.py</b><br/>[Django ORM]<br/>Monitor, Check, Incident, Alert<br/>and their constraints"]
        clients["<b>monitors/clients.py</b><br/>[httpx]<br/>probe_client, webhook_client"]
        db["<b>core/db.py</b><br/>PercentileCont, violated_constraint"]
    end

    http --> views
    views --> serializers
    views --> services
    services -. "raises MonitorNotFound,<br/>MonitorNameTaken, IncidentAlreadyOpen" .-> handler
    task --> tasks
    tasks --> services
    tasks --> alerting
    services --> checks
    services --> incidents
    services --> alerting
    services --> models
    services --> db
    alerting --> models
    alerting -. "queues send_alert<br/>after commit" .-> tasks
    checks --> clients
    alerting --> clients
```

| Rule                                                        | Kept by                                              |
| ----------------------------------------------------------- | ---------------------------------------------------- |
| Views and tasks hold no business rules                      | both call `services`; "check now" and the scheduler share `check_monitor` |
| `services` knows nothing about HTTP status codes            | it raises domain errors; `core/exception_handler.py` maps them |
| `incidents.decide` has no I/O                               | it takes a list of booleans and returns an enum      |
| Rules that two requests could break at once live in Postgres | named constraints; `violated_constraint` reads which one fired |

`core/` holds what any app could use (errors, pagination, the exception handler,
database helpers, startup checks of settings). `config/` is the project: settings,
URLs, the Celery app, typed environment readers.

## Data model

```mermaid
erDiagram
    MONITOR ||--o{ CHECK : "is checked by"
    MONITOR ||--o{ INCIDENT : "has"
    INCIDENT ||--o{ ALERT : "produces"

    MONITOR {
        bigint id PK
        varchar name UK "uq_monitor_name"
        varchar url
        varchar method "GET or HEAD"
        smallint expected_status "default 200"
        smallint timeout_s "1 to 30"
        int interval_s "at least 10"
        jsonb assertions "nullable"
        timestamptz created_at
        timestamptz last_checked_at "nullable"
        timestamptz maintenance_until "nullable"
    }
    CHECK {
        bigint id PK
        bigint monitor_id FK
        timestamptz checked_at
        boolean ok
        smallint status_code "nullable"
        int latency_ms
        varchar error_type "nullable"
    }
    INCIDENT {
        bigint id PK
        bigint monitor_id FK
        varchar kind "availability"
        timestamptz opened_at
        timestamptz resolved_at "null while open"
        varchar cause
    }
    ALERT {
        bigint id PK
        bigint incident_id FK
        varchar kind "opened or resolved"
        varchar status "pending, sent or failed"
        smallint attempts
        timestamptz created_at
        timestamptz sent_at "nullable"
    }
```

| Table       | Index or constraint                                                          | Serves                                         |
| ----------- | ---------------------------------------------------------------------------- | ---------------------------------------------- |
| `monitors`  | `uq_monitor_name` unique `(name)`                                            | duplicate name is a 409, decided by Postgres   |
| `checks`    | `checks_monitor_checked_idx` on `(monitor_id, checked_at)`                   | the uptime query and "last N checks"           |
| `incidents` | `uq_one_open_incident_per_kind` unique `(monitor_id, kind)` where `resolved_at IS NULL` | at most one open incident per monitor and kind |
| `alerts`    | `uq_alert_incident_kind` unique `(incident_id, kind)`                        | one alert per incident event, however often it is queued |

Deleting a monitor cascades to its checks, incidents and alerts. `checks` is
append-only and is the only table that grows without bound.

## Flows

### Check now: `POST /monitors/<id>/check/`

```mermaid
sequenceDiagram
    actor Operator
    participant V as views.MonitorCheckView
    participant S as services
    participant P as checks.probe
    participant T as Target
    participant DB as Postgres
    participant R as Redis

    Operator->>V: POST /monitors/5/check/
    V->>S: get_monitor(5)
    S->>DB: SELECT monitor
    Note over V,S: unknown id raises MonitorNotFound, mapped to 404
    V->>S: check_monitor(monitor)
    S->>P: probe(monitor)
    P->>T: GET url with timeout_s
    T-->>P: response, or timeout, refused, DNS failure
    P-->>S: CheckResult, never an exception
    S->>DB: INSERT check
    S->>DB: UPDATE monitor SET last_checked_at
    S->>S: evaluate_incidents(monitor)
    S->>DB: SELECT last max(N, M) checks
    S->>DB: SELECT open incident
    Note over S: incidents.decide returns OPEN, RESOLVE or NOTHING
    opt decide returned OPEN
        S->>DB: BEGIN, INSERT incident, INSERT alert, COMMIT
        S->>R: after commit, queue send_alert(alert_id)
    end
    S-->>V: CheckResult
    V-->>Operator: 200 with ok, status_code, latency_ms, error_type
```

The answer is 200 whatever the target did: a target that is down is the result,
not an error in Pulse.

### Scheduled check

```mermaid
sequenceDiagram
    participant B as beat
    participant R as Redis
    participant W as worker
    participant DB as Postgres
    participant T as Target

    loop every 10 s
        B->>R: enqueue_due_checks
    end
    R->>W: enqueue_due_checks
    W->>DB: SELECT id FROM monitors that are due
    Note over W,DB: due means never checked, or last_checked_at + interval_s has passed
    loop each due monitor
        W->>R: run_check(monitor_id)
    end
    R->>W: run_check(monitor_id)
    W->>DB: SELECT monitor
    Note over W: a monitor deleted in the meantime is logged and skipped
    W->>T: probe
    T-->>W: result
    W->>DB: INSERT check, UPDATE last_checked_at
    W->>W: evaluate_incidents, as in check now
```

From `services.check_monitor` onward this is the same code path as "check now".
Task arguments are ids, never objects: they cross Redis as JSON and the row is
loaded fresh when the task runs.

### Alert delivery

```mermaid
sequenceDiagram
    participant S as services
    participant A as alerting
    participant DB as Postgres
    participant R as Redis
    participant W as worker running send_alert
    participant C as catcher

    S->>DB: BEGIN
    S->>DB: INSERT or UPDATE incident
    S->>A: queue_alert(incident, kind)
    A->>DB: INSERT alert, status pending
    Note over A,DB: a duplicate hits uq_alert_incident_kind and nothing is queued
    S->>DB: COMMIT
    A->>R: on_commit, queue send_alert(alert_id)
    R->>W: send_alert(alert_id)
    W->>DB: SELECT alert with incident and monitor
    alt status is not pending
        Note over W: already sent or failed, stop
    else status is pending
        W->>DB: UPDATE attempts = attempts + 1
        Note over W: serialise once, sign those bytes with HMAC-SHA256
        W->>C: POST /webhook with X-Pulse-Signature
        alt 2xx
            W->>DB: UPDATE status = sent, sent_at
        else error and attempts below the limit
            W->>R: retry in 2^n s plus jitter
        else error on the last attempt
            W->>DB: UPDATE status = failed
        end
    end
```

Delivery is at-least-once. The receiver can deduplicate on `incident_id` plus
`event`, both in the payload.

## State machines

### Incident, per monitor and kind

```mermaid
stateDiagram-v2
    [*] --> NoIncident
    NoIncident --> Open: last N checks all failed, or opened by hand
    Open --> NoIncident: last M checks all passed
    NoIncident --> NoIncident: anything else
    Open --> Open: anything else
```

N is `PULSE_FAIL_THRESHOLD` (3), M is `PULSE_OK_THRESHOLD` (2). The state is not a
column: a monitor is in `Open` when it has an incident row with `resolved_at IS
NULL`. During a maintenance window no transition happens.

### Alert

```mermaid
stateDiagram-v2
    [*] --> pending: incident opened or resolved
    pending --> sent: receiver answered 2xx
    pending --> pending: attempt failed, retry scheduled
    pending --> failed: attempt failed and it was the last
    sent --> [*]
    failed --> [*]
```

## Decisions

| ADR | Decision |
| --- | -------- |
| [0001](docs/adr/0001-shared-http-clients.md) | One shared HTTP client per process, keep-alive off for checks |
| [0002](docs/adr/0002-layering.md) | Thin views and tasks over a service layer, the ORM as the repository |
| [0003](docs/adr/0003-database-enforced-rules.md) | Rules that can race are enforced by Postgres constraints |
| [0004](docs/adr/0004-incident-detection.md) | Incident detection is a pure function over the last checks |
| [0005](docs/adr/0005-scheduling.md) | Celery with Redis, one beat tick that queues what is due |
| [0006](docs/adr/0006-alert-delivery.md) | Alerts: row first, queue on commit, at-least-once delivery |
| [0007](docs/adr/0007-targets-as-separate-services.md) | Targets and the receiver are separate services |

## Known limits

| Limit | Effect | Where it is addressed |
| ----- | ------ | --------------------- |
| A monitor is "due" until its check finishes | a check slower than its interval, or a backlog, is queued again and runs twice | D7 |
| `timeout_s` bounds each network phase, not the whole request | a target that trickles bytes can hold a worker longer than `timeout_s` | D7, task time limit |
| `checks` is never pruned | the 90-day uptime query slows as the table grows | not scheduled: retention or daily roll-ups |
| An alert whose `send_alert` message is lost stays `pending` | broker down at commit, or a worker killed mid-task | not scheduled: a periodic sweep of old pending alerts |
| No authentication | anyone who reaches port 8100 can change monitors | D8 |
| Monitor URLs may point anywhere the containers can reach | with untrusted users this is server-side request forgery | D8, with authentication |
| One `requirements.txt` | test and lint tools ship in the production image | D9 |

# Deployment

This page covers running the Leos HTTP service (`leos serve`) in production
form: Docker Compose quick start, configuration, TLS, backups, and upgrades.
The service is a thin transport over the kernel — every write still requires a
signed, unexpired, consume-once approval decision on the existing gate path
(see `docs/SERVICE.md`).

## Quick start (Docker Compose)

```bash
cp leos-server.env.example leos-server.env
# edit leos-server.env: set LEOS_SERVER_API_KEY and LEOS_APPROVAL_HMAC_SECRET
docker compose up --build -d
curl -fsS http://127.0.0.1:8080/healthz
```

The container fails closed: without `LEOS_SERVER_API_KEY` it prints the
configuration summary, names the missing variable, and exits nonzero. All
endpoints except `/healthz` and `/readyz` require the `X-Leos-Api-Key` header.

## Running without Docker

```bash
pip install "leos-agent[server]"
LEOS_SERVER_API_KEY=... leos serve --host 127.0.0.1 --port 8080 --data-dir ./leos-data
leos serve --check   # validate configuration and exit
```

## Configuration

Precedence: defaults < `leos-server.toml` < `LEOS_SERVER_*` environment
variables < CLI flags. The TOML path defaults to `./leos-server.toml` and can
be set with `--config` or `LEOS_SERVER_CONFIG`.

```toml
# leos-server.toml — non-secret settings only
host = "127.0.0.1"
port = 8080
workers = 1
data_dir = "/data"
inbox_dir = "/inbox"
```

**Secrets never go in the TOML file.** Any secret-shaped key (`api_key`,
`github_token`, `approval_hmac_secret`, …) in `leos-server.toml` aborts
startup, so plaintext credentials cannot land on disk via configuration. The
startup summary prints secret *presence* only, never values.

| Variable | Required | Purpose |
| --- | --- | --- |
| `LEOS_SERVER_API_KEY` | yes | Boundary auth for every non-health endpoint (`X-Leos-Api-Key`, constant-time compare). Comma-separate multiple keys (each 32+ chars) for zero-downtime rotation: add the new key, migrate clients, remove the old |
| `LEOS_APPROVAL_HMAC_SECRET` | for decisions | Signs approval decisions (`/approvals/decide`, `/apply`, inbox decide) |
| `LEOS_GITHUB_TOKEN` | for `/apply` | Fine-grained PAT used by the bounded GitHub operator |
| `LEOS_ENABLE_REAL_GITHUB_WRITES` | for `/apply` | Explicit opt-in gate for real writes |
| `LEOS_SERVER_HOST` / `LEOS_SERVER_PORT` / `LEOS_SERVER_WORKERS` | no | Bind address, port, worker count |
| `LEOS_SERVER_DATA_DIR` / `LEOS_SERVER_INBOX_DIR` | no | Audits/receipts directory; approval inbox directory |
| `LEOS_SERVER_CONFIG` | no | Path to `leos-server.toml` |
| `LEOS_SERVER_RATE_LIMIT_PER_MINUTE` | no | Write-endpoint token bucket (default 60; 0 disables; over budget → 429) |
| `LEOS_SERVER_MAX_BODY_BYTES` | no | Request body cap (default 1000000; 0 disables; oversized → 413) |

Generate strong secrets:

```bash
python -c "import secrets; print(secrets.token_hex(32))"
```

## TLS: terminate at a reverse proxy

The service does not do TLS itself; run it behind a reverse proxy and bind it
to localhost or an internal network only (the compose file publishes
`127.0.0.1:8080`). Caddy example:

```caddyfile
leos.example.internal {
    reverse_proxy 127.0.0.1:8080
}
```

nginx example:

```nginx
server {
    listen 443 ssl;
    server_name leos.example.internal;
    ssl_certificate     /etc/ssl/leos.crt;
    ssl_certificate_key /etc/ssl/leos.key;
    location / {
        proxy_pass http://127.0.0.1:8080;
        proxy_set_header Host $host;
    }
}
```

## What to back up

| Data | Location (compose volume) | Why |
| --- | --- | --- |
| Audit chains | `/data/audits` (`leos-data`) | Append-only evidence of every apply |
| Approval receipts | `/data/receipts` (`leos-data`) | Consume-once markers; loss allows decision replay within expiry |
| Approval inbox | `/inbox` (`leos-inbox`) | Pending packets and signed decisions |
| Postgres | `leos-pgdata` volume | Runtime store / task queue state (`pg_dump`) |

## Upgrades

1. `docker compose pull` / rebuild with the new version.
2. `docker compose up -d` — the entrypoint revalidates configuration on start.
3. Verify `curl -fsS localhost:8080/healthz` and `/readyz`.
4. Audit and receipt files are plain JSONL/JSON on the volumes; no migration
   steps are required for them. Postgres schema changes, if any, are called
   out in `CHANGELOG.md`.

Key rotation and operational alarms are covered by the runbook
(`docs/RUNBOOK.md`, forthcoming); rotating `LEOS_APPROVAL_HMAC_SECRET`
invalidates approval decisions that are still in flight — drain pending
approvals first.

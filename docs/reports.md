# Private operations reports

AutoLog remains one application. The public article feed and its draft policy are unchanged.
Reports use `/reports` and the private `/api/reports` API, backed by two additive tables.

## Architecture and access

- Next.js 16 App Router / React 19, existing cookie-forwarding API proxy and Markdown renderer.
- FastAPI / SQLAlchemy 2 / PostgreSQL / Alembic. Migration `0003` follows `0002`.
- Existing authentication has one site owner, not database-backed user accounts. All authorized
  owner OAuth identities share one report repository, regardless of ChatGPT/Codex client.
  This is **not multi-tenant isolation**. Do not grant an unrelated user's subject access.
- The existing owner API token or a valid admin session can read/write reports. Optional
  `REPORT_READ_TOKEN` and `REPORT_WRITE_TOKEN` (distinct, at least 32 characters) restrict direct
  credentials to Reports. A write token can also read Reports. Neither grants article access.
  Keep credentials in the existing secret-management flow; never put them in browser code.
- MCP enforces new `autolog:reports:read` and `autolog:reports:write` scopes in addition to its
  existing issuer/audience/expiry/subject validation. Existing article scopes do not grant
  report access. Both the IdP grant and the subject scope ceiling must explicitly allow Reports.
- `AUTOLOG_MCP_REPORT_BACKEND_TOKEN` optionally selects a report-only backend credential;
  otherwise the existing server-side backend owner token is reused. OAuth tokens are never
  forwarded to the backend. Existing article draft integration opt-in remains unchanged.
- Cookie writes retain origin checks. Explicit invalid bearer credentials cannot fall back to
  a valid cookie. Private responses, including errors, use `private, no-store`.

No live IdP consent or installed ChatGPT/Codex connector was changed during implementation.
Clients must refresh tool discovery/consent after an approved MCP rollout. If a client still uses
`get_article` on a draft, its existing restriction intentionally remains in force; use `get_report`
on the explicitly copied operational report instead.

## Contract

Stable example ID: `OPENSTACK-RABBITMQ-0020`. Use a new ID for each execution; updates preserve
ID, project/component and creation timestamp. `source` is Codex/ChatGPT/Manual; execution status
is SUCCESS/PARTIAL/BLOCKED/FAILED, independent of blog publication status.

Fields include author, summary, environment/production health, findings/investigated facts,
changes/actually applied work, remaining_issues, next_actions/proposals, raw_markdown,
related_report_ids and related_article_ids. Related IDs must exist; self-links are rejected.
No historical articles are automatically classified or copied.

Each metric has `name`, numeric-or-null `value`, `unit`, optional `observed_at`, paired optional
`window_start`/`window_end`, and `note`. Times require a timezone. A missing measurement is null;
the UI only computes differences for matching name/unit pairs. It never assumes which direction
is better, substitutes zero, or compares MB/s with MiB/s.

| Method | Route | Purpose |
|---|---|---|
| POST | `/api/reports` | Stable-ID create/upsert |
| GET | `/api/reports` | Filtered list/search |
| GET | `/api/reports/{report_id}` | Full private report |
| PATCH | `/api/reports/{report_id}` | Complete replacement, expected_revision required |
| GET | `/api/reports/{report_id}/history` | Append-only version snapshots, limit/offset |
| GET | `/api/reports/latest?project=...&component=...` | Latest creation date, deterministic ID tie-break |
| GET | `/api/reports/unresolved` | Latest execution per project/component with remaining issues |
| GET | `/api/reports/projects` | Dashboard project/component index |

List/search/unresolved support project, component, status, report_id, q, inclusive date_from/date_to
on immutable **creation** timestamps, sort=newest/oldest, limit=1..100 and offset.
Creation date + ID provide deterministic ordering. Offset pages are not a frozen snapshot across
concurrent inserts; supply date_to for a fixed execution cutoff. A newer component execution
supersedes older issue snapshots in the unresolved view; it does not modify historical reports.

MCP adds create_report, get_report, search_reports, list_reports, update_report,
get_latest_project_report, get_unresolved_issues and get_report_history to the existing server.

An identical POST replay is a no-op. Different content under an existing ID returns 409 unless
expected_revision is supplied and matches. Project/component reassignment is always rejected.
PATCH is a complete replacement: read first, send all fields and expected_revision. Conflicts
must be reviewed, not retried blindly. A PostgreSQL transaction advisory lock serializes first
creates and updates across backend replicas. Revision snapshots are written in the same transaction.
Database triggers reject snapshot UPDATE/DELETE and ID/created_at edits. Authenticated credential
class is the audit actor; author/source are supplied attribution, not verified OAuth identity claims.
There is no Reports delete endpoint.

## Development verification

Use a dedicated local PostgreSQL database ending in `_test`; never production DATABASE_URL.

```bash
cd backend
TEST_DATABASE_URL='<LOCAL_POSTGRES_TEST_URL>' .venv/bin/python -m pytest -q
cd ../frontend
npm ci
npm run typecheck
node --test tests/*.test.cjs
npm run build
```

`scripts/check-reports-migration.py` accepts TEST_DATABASE_URL and RESTORE_TEST_DATABASE_URL
(two fresh local `_test` databases). It seeds a pre-migration draft at 0002, backs up, upgrades,
checks exact preservation, races conflicting creates, verifies guarded downgrade, backs up again
and restores into the separate target. Artifacts are private/ignored under `.deploy`.

`scripts/check-reports-ui.py` accepts a migrated local TEST_DATABASE_URL, requires Python
Playwright/Chromium, and launches only local API/frontend processes. It checks login protection,
filters, metrics, Markdown safety, history, create/edit, mobile and public-feed separation.
Optional ARTICLE_COPY_JSON explicitly seeds/copies one exported draft into the **development** DB.
Use a fresh database per complete UI run. CHROMIUM_PATH can select the installed browser.

Local verification used PostgreSQL 16. The existing GitHub backend CI targets PostgreSQL 17;
that remote CI must pass before production approval. Browser test dependencies are optional,
not application runtime dependencies.

## Production targets and pending rollout

Read-only inventory during this task confirmed namespace `lifelog`:

- `deployment/lifelog-backend`, container `backend`, 2 replicas.
- `deployment/lifelog-frontend`, container `frontend`, 2 replicas.
- `statefulset/lifelog-postgres`, pod `lifelog-postgres-0`, PostgreSQL 17, 1 replica.
- Existing app images use release `bcbd8febe2edcb4cabf5acdd654efaac0bd9efad`.
- `autolog-mcp` and the repository's auth candidate were **not running in this namespace**.
  Their packaged source exists, but applying it is a separate approved rollout with IdP prerequisites.

No production rollout, schema migration, credential change or draft copy has been executed.
Do not run the existing generic deploy script alone: it excludes MCP and does not run migrations.

After explicit operator approval:

1. Recheck context, workload images, Alembic revision and existing MCP/IdP endpoint ownership.
   Run CI on the implementation commit. Record old image digests and render manifests for review.
2. **Back up before touching the schema**, using the existing Kubernetes procedure:

   ```bash
   umask 077
   kubectl -n lifelog exec lifelog-postgres-0 -- sh -c \
     'pg_dump -U "$POSTGRES_USER" -d "$POSTGRES_DB" -Fc' > /secure/backup/autolog-before-reports.dump
   ```

   Check dump exit status, checksum and `pg_restore --list` with PostgreSQL 17 tools. Restore into
   a separate PostgreSQL 17 database and compare article/category/entry counts and content hashes.
   Protect the backup: it includes private drafts. This production backup/restore gate remains pending.
3. Review `0003`: CREATE TABLE/INDEX/FUNCTION/TRIGGER only, no DML on existing content.
   Run the existing migration Job with the reviewed new backend image exactly once; wait for success.
   Do not modify DB/PVC resources or run concurrent migration jobs.
4. Roll backend and frontend to reviewed immutable images. Existing application rollback remains
   schema-compatible. Configure optional report-only secrets only if desired.
5. Separately review/reconcile the existing packaged MCP/IdP release. Register Reports scopes,
   verify owner subject ceilings and client consent, use the same backend image for MCP, and check
   real issuer/audience/access-token validation. Do not overwrite an external connected integration
   based solely on its display name. Confirm exact endpoints before changing any live integration.
6. Smoke-test anonymous denials, owner/private reads, report read-only denial of writes,
   OAuth report-scope denials, stable-ID replay/conflict, history, and ordinary public/draft articles.
   Recheck historical article hashes. Then perform only an explicitly approved Report 20 copy.

### Rollback

Roll back frontend/backend/MCP application images and any explicitly changed IdP grants to the
recorded versions. **Keep the additive Reports tables and revision data.** Existing blog code
ignores them. Do not restore a full database over newer articles as a routine application rollback.
Migration downgrade refuses when report data exists; deleting report history requires separate
explicit authorization and an exported, verified backup. An empty development schema can be
returned to 0002 with `alembic downgrade 0002`.

### Report 20 safe copy

The production article draft was exported read-only and remains unchanged. An explicitly selected
copy was verified only in development as `OPENSTACK-RABBITMQ-0020`, including exact Markdown
SHA-256 equality and preservation of the source draft. No old report or consolidated blog series
was migrated. The copy helper never contacts a database:

```bash
python scripts/prepare-report-copy.py /secure/report20-article.json \
  --report-id OPENSTACK-RABBITMQ-0020 --project OpenStack --component RabbitMQ \
  --author Codex --source Codex --status PARTIAL --output /secure/report20-copy.json
```

Review that payload, extract structured facts/metrics only from actual evidence if desired, and
POST to the private Reports endpoint **after deployment/copy approval**. The helper preserves
exact raw_markdown and source article reference/creation timestamps in provenance, leaves
unextracted fields empty rather than inventing facts, and exclusively creates its output file.
Verify the saved report with authorized GET/MCP, content hash, links and history; retain the old
draft. No removal is proposed or authorized by this implementation.

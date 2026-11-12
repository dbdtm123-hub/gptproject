# Issue #12 — private Reports read integration

## Cause and implementation

The inspected remote `work` head was `3a44d69e8dfcaf340fc40b3b71f4b95941def9c0`. It had no Reports model/API, although the separate production release `43a317f` did. The connected integration is a Sites Worker at `https://autolog-personal.dbdtmtm1.chatgpt.site/mcp`, not the packaged Python MCP process. Sites version 3 already registers the eight Reports tools, while this chat advertises the older ten-tool article catalog.

This change brings the existing production Reports model, API, contracts and MCP registration into work without frontend changes. The original migration 0003 is included byte-for-byte for clean installations and CI, not a new production migration. The existing ten Sites tools and the Python upload_image tool remain intact. Reports remain separate from articles.

There is no general-user login or row owner column in the inspected application. Instead of inventing identities or trusting display author names, `REPORT_READERS` assigns each independently authenticated reader token an operator-verified list of owned stable report IDs. No ID may be assigned to two readers; tokens must be distinct from owner/read/write credentials. An empty assignment reads nothing. This is explicit operator-managed ownership, not automatic OAuth signup or automatic ownership inference. A reader receives no write/delete or blog-write rights. Existing owner credentials and owner admin access retain their existing behavior.

Every Reports read path applies the assignment: list/search and counts, direct get, history, project/component options, latest, and unresolved issues. Latest/unresolved ranking is performed inside the reader's visible set. Unassigned and nonexistent IDs both return 404 to prevent existence disclosure; missing/invalid credentials return 401, and reader writes return 403. No client query/header can select an owner.

Python MCP uses introspected subjects and `autolog:reports:read` / `autolog:reports:write`. Reports calls require an explicit per-subject backend credential or a verified subject in `AUTOLOG_MCP_REPORT_OWNER_SUBJECTS`; they never implicitly use the shared article owner token. The existing Sites Worker already encrypts credentials separately per platform user and forwards that user's token. Its captured production source and regression tests are now versioned in `integrations/sites-autolog/`; this is not a second hosted MCP server. No Sites deployment is needed just to add tools already present in version 3.

## Configure after approval

1. Operator verifies which report IDs belong to each reader. Never infer this from `author`, report prefix, supplied headers, or a user's claimed ID. Unassigned legacy rows remain owner-only.
2. Generate a distinct 32+ character random reader token through the existing secure secret workflow. In Kubernetes secret `lifelog-report-readers`, set `REPORT_READERS` to JSON shaped like `{"alice":{"token":"<secret>","report_ids":["OPENSTACK-RABBITMQ-0020"]}}`. Do not commit real tokens or put them in chat. The backend manifest has an optional reference to this key.
3. In AutoLog's private Site connection settings, each reader stores only their own reader token. Never share the owner API_TOKEN with ordinary users. The current Site audience is custom/owner-only; adding a specific viewer requires an explicit audience/access decision. Do not make it public.
4. Only if using the separate Python MCP: provision the matching `AUTOLOG_MCP_REPORT_SUBJECT_TOKENS` secret JSON (`{"<verified OAuth sub>":"<reader token>"}`), allow that verified subject, and grant only `autolog:reports:read`. Keep report_owner_subjects empty for ordinary users. Do not activate the currently separate Python deployment to repair Sites discovery.

The assignment is configurable without database changes. Restart backend replicas after changing secret-backed environment variables. Reader assignments can be revoked by removing the reader or ID and restarting replicas.

## Validation and limitations

- Local MCP/auth/adapter and reader unit tests: 56 passed, 2 PostgreSQL integration tests deliberately excluded.
- Explicit in-memory SQLite read-query fixtures: 3 passed. These exercise real ORM visibility queries for Alice/Bob, own/other/missing IDs, history, totals, latest/unresolved, options and empty grants. They are a portable read supplement, not validation of PostgreSQL migration/CAS/JSONB constraints.
- Captured Sites Worker tests: 16 passed, covering all eight Reports registrations, original article behavior, identity, per-user encrypted credentials and token forwarding, validation and conflicts.
- PostgreSQL suite and new OAuth-to-real-Reports non-admin integration are included in CI. Local execution is blocked because this executor refuses TCP and Unix socket creation. TestClient also stalls at its thread portal here; no passing HTTP/DB result is inferred from query tests.
- OPENSTACK-RABBITMQ-0020 is used by the synthetic fixture and retrieval/isolation tests. These do not prove live report content. Prior production evidence recorded real report 20 retrieval, but fresh production API/MCP requests fail DNS resolution in this executor. Report 21 production existence is unknown.
- Connected AutoLog check_connection currently succeeds. Sites metadata confirms active version 3 and a succeeded MCP deployment, but neither that metadata nor local tools/list is an authenticated production tools/list response.

## Approved production procedure (not executed)

The existing `.github/workflows/autolog-deploy.yml` is manual but rolls backend, frontend and image gateway together. Do not use it for this backend-only authorization change. CI image publication does not deploy workloads.

After explicit user approval, successful full PostgreSQL CI, and access to the authorized management executor:

1. Read the live Kubernetes context and namespace lifelog, capture the current backend image and Deployment JSON for rollback, and confirm running images/configuration against this release. Inspect existing report/API authentication without exposing secrets.
2. Read `alembic_version` and confirm existing revision 0003 and both report tables. **Do not run migrations against the deployed Reports database.** If actual schema differs, stop and investigate. Do not downgrade/delete tables or data.
3. Securely provision the reviewed reader assignments. Apply only the backend Deployment env reference for REPORT_READERS (or the equivalent narrow Deployment patch), preserving all existing env, secret references, probes and rollout settings. Do not apply the entire old manifest over the live deployment without reconciling it.
4. Roll only `deployment/lifelog-backend`, container backend, to the verified immutable backend image for the approved work commit. Use maxUnavailable=0 and wait for readiness. Do not restart database, frontend, image gateway, OAuth or tunnel services.
5. With separate owner, Alice and Bob credentials held in memory, verify own report read/search, cross-reader 404 and empty search, anonymous/invalid 401, reader write 403, private/no-store, original blog search and draft/write behavior. Verify report 20 only with its authorized owner token; log status/ID/hash, not contents. Do not create test reports/articles.
6. Refresh the existing AutoLog plugin connection and start a new chat. Confirm all original ten tools plus search_reports/get_report (version 3 actually has 18). Authenticate to the existing Sites MCP URL; the backend API token is not the Sites OAuth bearer. Retrieve report 20 using get_report. If the snapshot stays stale, reconnect the existing plugin or have its owner/admin refresh/approve its tool snapshot. Reuse plugin `plugin_asdk_app_sites_fa8ded4d05388191865780cbd12bac1d`; do not create a duplicate.
7. Capture authenticated production tools/list names and read-only get_report results. Completion requires this live evidence, not a local dispatcher response.

Rollback: restore the captured backend image and only the changed reader env/secret reference as appropriate, preserving the existing Reports tables, immutable history, report content and blog data. Do not roll back a live Sites version unless a separately approved Sites change was actually published.

## Sites source

`integrations/sites-autolog/worker/index.js` is copied unchanged from production version 3 source commit `2708b57341babc7b7dd7e7cc566bc089038a4cec`. Its tests have one additional cross-user credential-forwarding regression. No hosted source or plugin definition was modified during preparation.

## Delivery blocker

The GitHub connector rejected create_tree with `MCP tool call requires approval, but approval policy is never`. No remote tree/commit/ref was created, no push succeeded, and no CI run or production deployment was triggered. The local implementation is committed. A patch against the exact fetched remote work tree is exported under `/home/ubuntu/issue12-delivery/issue-12.patch` for an authorized write-enabled executor. The current executor also cannot resolve github.com for ordinary Git transport. Do not bypass either restriction or report the local commit as remotely pushed.

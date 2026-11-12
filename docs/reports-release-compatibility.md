# Production-compatible Operations Reports release

Release branch: `release/operations-reports-production`, based on verified Reports commit `8ebcde2`.
The production branch is not replaced or force-pushed.

The reviewed production-to-candidate prerequisites add the packaged MCP/OAuth process, opt-in
private article integration reads, upload/editor integration and their tests/documentation.
`backend/app/auth.py`, backend dependency lockfiles, frontend package/lockfiles and migrations
0001/0002 are byte-identical between live bcbd8fe and the candidate. The only database revision
added is 0003 (Reports tables, indexes and immutable audit triggers).

Production retains its existing owner credentials, session-signing key derivation, API token,
public article/draft authorization, Secret references, configuration and routing. Private article
integration reads remain disabled by default. Reports are separately authenticated and private.
No new authentication provider, callback, consent, OAuth subject or signing key is guessed or enabled.
The already connected managed AutoLog integration remains the production article integration.
The packaged standalone OAuth/MCP candidate remains inactive; activating it later requires real
IdP/callback/consent validation. Its regression tests run as part of this release.

The existing CI workflow supports an explicit dispatch of this reviewed release branch, runs
PostgreSQL 17 backend/MCP, OAuth, frontend and manifest checks, then publishes only immutable
backend/frontend SHA images. The production work branch's existing push behavior is preserved.
No image-gateway update or infrastructure rollout is part of this release.

Before production migration, restore a consistent production snapshot into isolated PostgreSQL 17,
compare every existing public table, apply 0003, and verify the unchanged production credential and
an old-release admin cookie against the candidate. Keep the captured production secrets private.
Roll back only application images on failed health gates; keep all report data and additive tables.

PostgreSQL 17 regression found locale-dependent filter-index ordering. The release explicitly uses
PostgreSQL C collation for the new Reports project/component index; the test remains unchanged.

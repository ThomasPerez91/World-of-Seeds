# World of Seeds V2 — Release candidate runbook

This runbook is the V2-34 boundary. It validates a release candidate on the isolated Rise2 V2 stack; it does not authorize a stable release, DNS switch, V1 import, or automatic deployment.

## Functional freeze

- Candidate version: `2.0.0-rc.1`.
- New product functionality is frozen. Only release-blocking correctness, security, operability, compatibility, documentation, and test fixes belong in V2-34.
- `master` and `develop` remain V1-only.
- The V2-33 pilot evidence is immutable and must not be rewritten to describe V2-34.
- V2-32D remains blocked and non-blocking unless a separate decision changes that status.

## Immutable pilot baseline

The approved V2-33 baseline is `v2-33-go-20260907`. Its final ledger SHA-256 is `38c94b41aed849a754053470e4a1eba8834157c64c57c6fb2e7d79dcca19d70b` and its tested runtime revision is `adcf67d5ea92b72c2a2210f8cdafb29669a940d8`.

The previous WOS image retained for application rollback is:

`ghcr.io/thomasperez91/world-of-seeds-v2@sha256:d0e817283ad95ba1792b4e16e7241bddabbce2190272382e2c339b4c291a947e`

This digest is a rollback input, never a mutable tag.

## Candidate artifact

1. Merge V2-34 only after its pull-request CI and review are green and a separate merge authorization is given.
2. Wait for the normal `develop_V2` image publication workflow to publish the merged revision by immutable SHA. V2-34 does not add a deployment path to that workflow.
3. Resolve the published candidate to an immutable `ghcr.io/thomasperez91/world-of-seeds-v2@sha256:...` reference and record both the merge revision and digest in the operational evidence before touching Rise2.
4. Reject a candidate if its OCI revision label does not match the merged `develop_V2` revision.
5. Do not substitute a rebuilt image after validation; the digest recorded for validation is the digest eligible for V2-35 consideration.

## Database compatibility

The V2-33 pilot schema and the V2-34 candidate schema are both Alembic revision `20260831_22`. V2-34 is therefore deliberately schema-neutral: no expand/contract migration is required inside the RC itself, and adding any later migration requires reopening this compatibility decision and updating the manifest, tests, and runbook.

Before candidate validation:

1. verify Alembic reports a single head at `20260831_22`;
2. execute the normal CI migration round-trip and require it to pass;
3. take the encrypted V2 backup and complete the isolated restore drill before a host mutation;
4. preserve the V2 PostgreSQL volume during an application-image rollback.

Because the schema is unchanged from the pilot, rollback to the previous WOS digest must not require a database downgrade. Any future RC migration must be expand/contract compatible with the previous approved application digest or supply and prove a bounded rollback migration before it is accepted.

## Rise2 validation

Rise2 validation is explicit host work, never a side effect of merging V2-34.

1. Checkout the exact merged `develop_V2` revision under `/opt/world-of-seeds-v2` and require a clean tree.
2. Keep `/etc/world-of-seeds-v2/environment` mode-restricted and use only immutable WOS, qBittorrent, and NewGreedy image references.
3. Run `scripts/rise2_v2_preflight.sh` before starting or recreating the V2 application services.
4. Require the NewGreedy policy validator to pass, including `proxy.flow_detail = 0`, `persist_stats = true`, and `auto_purge_stopped = false`.
5. Require the reproducible qB authentication/bootstrap contract and CA/tracker-only proxy checks to pass.
6. Re-run the complete application health/authentication, worker/scheduler, qB/NewGreedy, bounded-load, WebSocket recovery, dependency-failure, resource-pressure, security/observability, backup/restore, and monitoring checks that are relevant to the candidate.
7. Record secret-free evidence only. Complete tracker URLs, passkeys, credentials, host secrets, infohash/storage identifiers, and business identifiers must not enter logs or release artifacts.
8. Do not switch public DNS and do not import real V1 data during V2-34 validation.

A regression in health, authentication, durable jobs, scheduler recovery, storage integrity, secret handling, observability, backup/restore, or rollback is release-blocking.

## Rollback

Rollback remains an admission-and-application boundary, not a destructive V2 cleanup.

1. Suspend new V2 admission before application rollback.
2. Preserve V2 PostgreSQL, Redis, qBittorrent, NewGreedy state/CA, and shared-storage volumes unless a separately approved recovery procedure requires otherwise.
3. Restore the exact previous WOS image:
   `ghcr.io/thomasperez91/world-of-seeds-v2@sha256:d0e817283ad95ba1792b4e16e7241bddabbce2190272382e2c339b4c291a947e`.
4. Recreate only the WOS application services needed to return to the previous application digest; do not wipe qB/NewGreedy or database state as part of ordinary image rollback.
5. Verify API health and authentication, worker/scheduler recovery, zero unintended V1 writes, V1 availability, and preservation of V2 volumes.
6. Record elapsed rollback time and require it to remain inside the approved V2-33 RTO boundary.

If the previous digest cannot run safely against the preserved schema and configuration, the candidate is rejected and V2-35 is blocked.

## V2-35 boundary

Passing V2-34 does not publish `2.0.0`, modify `master`, switch DNS, import V1, or retire V1. V2-35 requires a separate branch/PR, complete stable-release validation, and explicit approval for the progressive Rise2 cutover. V1 remains available throughout the approved rollback window.

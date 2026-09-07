# World of Seeds V2 — Stable 2.0.0 release and Rise2 cutover

This runbook is the V2-35 boundary. It promotes the already validated V2 release-candidate line to
stable `2.0.0` without introducing product functionality. Merging V2-35 does not switch DNS, import
V1 data, publish a mutable `latest` tag, or retire V1.

## Provenance

The stable line is anchored to the V2-34 release candidate that passed the real Rise2 validation:

- RC version: `2.0.0-rc.1`;
- RC revision: `896323ac1858a804ce5a6d33185f3b30b7db3847`;
- RC image: `ghcr.io/thomasperez91/world-of-seeds-v2@sha256:47f0e7ba7ca2f5700e94def18bac91748cefcaac6b61ed4225573b4aa6d1afcd`;
- real application rollback measured on Rise2: 15 seconds;
- database revision: `20260831_22`;
- migration tree: `12c3a629ecf105797afd787d06f6c1e9d2b24d5b`.

`scripts/validate_v2_stable_release.py` fails closed if V2-35 changes functional application code,
migrations, runtime topology, or another file outside the closed release allowlist. The release
branch may contain only version mirrors, release policy/workflow changes, release tests and release
documentation.

## Stable artifact

1. Merge V2-35 into `develop_V2` only after CI/review is green and separate merge approval is given.
2. Wait for the normal V2 integration-image workflow to build the merged revision once and publish
   `ghcr.io/thomasperez91/world-of-seeds-v2:sha-<merged-sha>`.
3. Resolve that tag to its immutable digest and verify the OCI labels:
   - revision equals the exact merged `develop_V2` SHA;
   - version equals `2.0.0`;
   - ref name remains `develop_V2`.
4. Validate that exact digest on Rise2. Do not rebuild it after validation. Because the V2-35
   validator proves there is no functional delta from the validated RC, do not repeat the long
   V2-33 scheduler/WebSocket/manifest load campaign. The stable validation is limited to artifact
   identity, preflight, migrations at the same head, API readiness/authentication, workers and
   scheduler, qBittorrent/NewGreedy integration, secret-safe logging, monitoring, and preservation
   of persistent state.
5. Only after the exact merged digest has passed this validation may the operator, with explicit
   approval, add the immutable release tag `2.0.0` to the same image. Promotion must be a registry
   retag/copy of the tested digest, never a new Docker build.
6. Verify that resolving `ghcr.io/thomasperez91/world-of-seeds-v2:2.0.0` returns the same manifest
   digest as the validated SHA image before creating the Git tag/release `v2.0.0`.

No merge-time workflow publishes `2.0.0`, `latest`, changes DNS, or deploys to Rise2 automatically.

## Progressive Rise2 cutover

The cutover is an explicit operator sequence and each phase has a stop/rollback point.

1. **Stable runtime phase** — deploy the exact validated stable digest only to the WOS application
   services on Rise2. Preserve PostgreSQL, Redis, qBittorrent, NewGreedy, their CA/state and shared
   storage. Keep V1 serving production traffic.
2. **Isolated verification phase** — verify V2 health, authentication, one normal application path,
   worker/scheduler operation, qBittorrent/NewGreedy health, HTTPS on the V2 ingress and monitoring.
   No long pilot campaign is repeated when the closed V2-35 delta validator is green.
3. **Traffic cutover phase** — changing the production DNS/ingress target requires explicit approval
   after the stable runtime phase is green. Record the pre-change DNS value and TTL immediately
   before changing it.
4. **Observation phase** — keep V1 intact and operational for the approved rollback window. Observe
   API errors, authentication, queue/jobs, scheduler, qB/NewGreedy, PostgreSQL/Redis, disk/I/O and
   ingress metrics. Do not delete V1 data, containers, images or configuration during this window.

Any health, authentication, durable-job, scheduler, storage, secret-handling or observability
regression stops the progression and triggers rollback rather than an in-place destructive fix.

## Rollback window

Application rollback on Rise2 is non-destructive:

1. suspend new V2 admission;
2. preserve PostgreSQL, Redis, qBittorrent, NewGreedy, CA and storage state;
3. first restore the validated RC image
   `ghcr.io/thomasperez91/world-of-seeds-v2@sha256:47f0e7ba7ca2f5700e94def18bac91748cefcaac6b61ed4225573b4aa6d1afcd`;
4. if a deeper application rollback is required, the previously proven pilot image remains
   `ghcr.io/thomasperez91/world-of-seeds-v2@sha256:d0e817283ad95ba1792b4e16e7241bddabbce2190272382e2c339b4c291a947e`;
5. recreate only the WOS application services and verify API health/auth plus worker/scheduler;
6. never run `docker compose down --volumes` as part of ordinary rollback.

The V2-34 drill already proved the previous application digest can run against the preserved schema
and state in 15 seconds. V2-35 remains schema-neutral and may not weaken that boundary.

If production traffic has already moved to V2, traffic/DNS is returned to the recorded V1 target as
part of the operator rollback. V1 remains available throughout the rollback window.

## DNS and V1 boundary

- V2-35 merge: no DNS change.
- Stable image publication by SHA: no DNS change.
- Stable digest validation on Rise2: no DNS change.
- Stable tag/GitHub release: no DNS change by itself.
- Production traffic switch: separate explicit approval.
- V1 import: separate explicit approval and procedure; it is not implied by the stable release.
- V1 retirement: out of scope until the rollback window is explicitly closed.

Stable `2.0.0` therefore means the software artifact is approved for production; it does not mean
that V1 is automatically destroyed or that traffic is automatically redirected.

# Rise2 NewGreedy log policy

This targeted post-pilot hardening makes the security setting discovered during V2-33 reproducible without changing the runtime SHA or evidence ledger of the completed pilot.

## Required policy

The Rise2 NewGreedy configuration must contain:

```ini
[proxy]
flow_detail = 0

[stats]
persist_stats = true
auto_purge_stopped = false
```

`flow_detail = 0` prevents full intercepted tracker flows from being persisted by mitmdump, because complete announce URLs can contain tracker passkeys. The stats requirements preserve the previously validated READY continuity behavior.

## Enforcement

The policy is checked twice from the same secret-safe validator:

- `scripts/rise2_v2_preflight.sh` rejects an unsafe configuration before the normal Rise2 preflight proceeds;
- `newgreedy-init` in `deploy/compose.rise2.v2.yaml` mounts the configuration read-only and refuses to complete unless the policy passes. Since `newgreedy` depends on successful completion of `newgreedy-init`, a direct Compose startup also fails closed if preflight is bypassed.

The validator reports only fixed key names and never prints configuration values or file contents.

## Regression

`scripts/rise2_v2_newgreedy_smoke.sh` creates disposable fresh state with `flow_detail = 0`, proves NewGreedy remains healthy across restart/recreate, then temporarily changes the disposable configuration to `flow_detail = 1` and verifies that `newgreedy-init` fails.

This change does not alter the finalized V2-33 pilot ledger, its tested runtime SHA, DNS, V1 data, or a stable release.

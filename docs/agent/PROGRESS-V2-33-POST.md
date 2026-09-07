# V2-33 post-pilot hardening

- V2-33 limited Rise2 pilot remains finalized independently; its runtime SHA and evidence ledger are unchanged.
- Targeted follow-up: make the NewGreedy `proxy.flow_detail = 0` security setting discovered during Gate 9 reproducible from versioned deployment configuration before V2-34.
- A single validator now enforces `flow_detail=0`, `persist_stats=true`, and `auto_purge_stopped=false`.
- Rise2 preflight invokes that validator.
- `newgreedy-init` invokes the same validator and `newgreedy` still depends on its successful completion, so direct Compose startup fails closed when verbose flow logging is configured.
- The disposable Rise2 NewGreedy smoke covers fresh state, restart/recreate continuity, and a negative `flow_detail=1` startup test.
- Scope excludes application runtime behavior, frontend, database migrations, dependencies, DNS, V1 import, and release publication.

# External API v1

World of Seeds 2.2.3 exposes a deliberately small, stable server-to-server API under
`/api/external/v1`. It is separate from the browser APIs under `/api/v1` and `/api/v2`.

## Authentication and scopes

An administrator creates an API client in **Administration > Services > External APIs**.
The raw `wos_live_…` key is displayed once; only its SHA-256 hash and a display prefix are
stored. Send the key in `Authorization: Bearer <key>`.

User-scoped calls additionally require `X-WOS-User-Seed`. A seed is a stable, unique,
25-character base62 credential owned by one active WOS account. It grants no scope by itself.

Available scopes:

- `users:create`: create a normal WOS account;
- `downloads:read`: read downloads belonging to the resolved account.

Never put either credential in a URL. Production clients must use the normal HTTPS ingress.

WOS has no existing at-rest encryption primitive that can safely support later seed disclosure.
The seed is therefore persisted as the user credential itself, protected by database access control,
never included in generic user DTOs, and exposed to its owner only through the authenticated,
non-cacheable account endpoint. This release deliberately does not introduce custom cryptography.

## Endpoints

### `GET /health`

No authentication is required. Returns `{"status":"ok","api_version":"1"}`.

### `POST /users`

Requires `users:create` and an `Idempotency-Key` header (8–200 characters). The optional JSON
body is `{"username":"example-user"}`; omit `username` to let WOS generate it. A normal,
non-admin account is created through the same quota-protected provisioning service as the admin UI.

```sh
curl -X POST https://wos.example/api/external/v1/users \
  -H 'Authorization: Bearer wos_live_FAKE_EXAMPLE_KEY' \
  -H 'Idempotency-Key: fake-command-12345' \
  -H 'Content-Type: application/json' \
  -d '{"username":"example-user"}'
```

The first response is `201` and contains the temporary password and authentication seed. A retry
with the same client, key and payload does not create another user; it returns `200`, the same user
and seed, `temporary_password: null`, and `idempotent_replay: true` because temporary passwords are
not stored in recoverable form. Credential responses use `Cache-Control: no-store`.

### `GET /me/downloads?offset=0&limit=50`

Requires `downloads:read` plus `X-WOS-User-Seed`. `limit` is bounded to 1–100. Only the resolved
user's requests are returned. States are normalized as `waiting`, `downloading`, `stalled`,
`cooldown`, `ready`, `error`, `cancelled`, or `expired`.

`eta_seconds` is `null` when no trustworthy runtime ETA exists. `access_remaining_seconds` is
calculated in UTC from the READY request's unsubscribe time and never drops below zero.

```sh
curl https://wos.example/api/external/v1/me/downloads?limit=25 \
  -H 'Authorization: Bearer wos_live_FAKE_EXAMPLE_KEY' \
  -H 'X-WOS-User-Seed: AAAAAAAAAAAAAAAAAAAAAAAAA'
```

## Errors, request IDs and limits

Errors use `{"error":{"code":"…","message":"…","request_id":"…"}}`. Every external response
includes `X-Request-ID`; a safe caller-provided value may be reused. Stable codes include
`invalid_api_key`, `api_client_disabled`, `insufficient_scope`, `invalid_user_seed`,
`user_disabled`, `account_quota_reached`, `username_conflict`, `validation_error`,
`idempotency_conflict`, and `rate_limit_exceeded`.

Requests are limited per client and source IP. A limited request returns `429` and `Retry-After`.
API keys, seeds, temporary passwords and authorization headers must never be logged. Mutation audit
records contain only IDs, timestamps, request IDs and a short hash fingerprint of idempotency keys.

Future capabilities will be added under this versioned namespace or a new major API version without
aliasing internal frontend routes.

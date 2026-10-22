# External API v1

World of Seeds 2.3.26 exposes a deliberately small, stable server-to-server API under
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
- `users:read`: inspect account capacity and account status by Discord ID (no credentials).
- `users:recover`: replace undelivered initial credentials for accounts provisioned by this API client.

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
body is `{"username":"example-user","discord_user_id":"123456789012345678"}`; omit `username` to let WOS generate it. Send `{}` for legacy non-Discord creation.
Discord IDs are decimal strings of 17–20 digits, never JSON numbers. A normal,
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

Before account creation, WOS rechecks the client's stored key fingerprint, active/revoked
state and `users:create` scope after password preparation and after acquiring the quota
mutex. It holds the client row lock until the creation transaction commits. A revocation
that commits first prevents creation; if creation has already acquired that lock, it
commits before the revocation can complete. After the revocation completes, new attempts
are denied. Idempotent replays also recheck current authorization, without password work.
Authorization failures roll back the transaction and return the existing 401/403 error
codes without account, credential, idempotency or provisioning-audit creation.

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


## Discord onboarding (2.3.26)

### `GET /capacity`

Requires `users:read`. Returns `used`, `maximum`, `available` (at least zero), and
`reached`. Accounts suspended but not deleted still occupy a place, and administrator
accounts count too, matching the administration panel. The response is advisory:
`POST /users` always checks the quota again while holding the creation mutex.
All capacity and account status responses use `Cache-Control: no-store`.

### Discord identity during `POST /users`

Supply `discord_user_id` from the authenticated Discord interaction's author ID,
not an ID entered by another member. WOS trusts the bot to authenticate Discord
members; the API key alone does not prove which human initiated a request.

The association is created in the same transaction as the account, idempotency
record and audit. A globally unique Discord ID and a unique WOS user ID prevent
duplicate bindings. Creation is serialized across clients. A different command or
API client targeting an already linked identity receives `409 discord_account_exists`.
Replaying the original command with its original payload and idempotency key retains
its existing `200` behavior, without returning a password.

Bindings survive suspension and soft deletion. A deleted account cannot be replaced
by silently creating another account for the same Discord ID. Existing accounts are
not automatically linked or matched by username; this release creates bindings only
during new account provisioning, and provides no unauthenticated rebind endpoint.
Legacy clients omitting Discord ID keep their previous request hash/replay behavior.

### `GET /users/discord/{discord_user_id}`

Requires `users:read`. Returns only `id`, `username`, `is_active`, `deleted` and
`must_change_credentials`. A deleted account reports `is_active: false`.
Unknown identities return `404 discord_account_not_found`. Passwords and authentication
seeds are never returned by this route. Use the stable WOS UUID for local references:
the member can subsequently change their WOS username.

### `POST /users/discord/{discord_user_id}/initial-credentials`

Requires `users:recover`. This endpoint handles a lost creation response or a failed
Discord private-message delivery. It only works for an active, non-admin account
created with a Discord binding by **this same API client**, before its first successful
login and while `must_change_credentials` remains true. Another API client cannot
recover it, even with the same scope. Creating a replacement API client does not
transfer recovery ownership; that case requires operator assistance.

Returns the same shape as the creation response, with a **new** temporary password
and the existing authentication seed. The previous temporary password is invalidated;
existing sessions are revoked. Successful recoveries are audited without credentials.
This is deliberately not a general password-reset API. A claimed, suspended, deleted
or admin account returns `409 initial_credentials_unavailable`; a missing or
foreign-client binding returns `404 discord_account_not_found`.

Each successful call replaces the password again. If its response is lost, call it
again and deliver only the latest result. Serialize onboarding/recovery commands per
Discord member in the bot to avoid delivering concurrent responses out of order.
Key revocation and scope changes are rechecked after hashing; account status and first
login are rechecked while holding the user lock, which is also used by website login.

### Recommended bot flow

1. Acknowledge the interaction privately; obtain the invoking Discord member ID.
2. Check account status. If already linked, do not create another account or display
   secrets publicly. A suspended/deleted account must not regain access through onboarding.
3. Check `/capacity` to show availability, then call `/users` with the Discord ID,
   an empty/generated username choice and a stable `Idempotency-Key` for this operation.
4. Deliver initial credentials by private message or an ephemeral interaction only.
   Keep API keys, seeds and passwords out of logs and public channels. Use persistent
   bot storage for the WOS UUID and any user seed needed for `/me/downloads`.
5. After a timeout, retry the same creation request. If the reply has no password,
   use initial recovery. Handle `409 initial_credentials_unavailable` without an
   endless retry loop: the account may already have been used.
6. Use status checks to reflect site-admin suspensions/deletions. Download notifications
   use bounded polling of `/me/downloads` with `downloads:read` and that user's seed;
   there is no webhook in this version. A user rotating their seed must update the bot.

The bot token needs the new scopes explicitly. Existing tokens gain no permissions
automatically. Configure a dedicated Discord client with the required scopes in
Administration > Services > External APIs before starting onboarding.


## General password reset — 2.3.28

`users:reset-password` is a new, explicit privileged scope. Existing keys are unchanged.
It allows the trusted Discord bot to issue reset grants for any eligible Discord-linked
account, including those created by an earlier API client. It does not attach old accounts
by username. `users:recover` and its creator/first-login restrictions remain unchanged.

`POST /users/discord/{discord_user_id}/password-reset` returns
`{"token":"<secret>","expires_in":900}` with `Cache-Control: no-store`. The bot must
use the actual interaction author's identity, never an identity supplied by another user.
The bot delivers `https://world-of-seeds.fr/#reset-password=<token>` by DM or ephemeral
reply only, disables embeds and never logs/persists the token. Do not retry an ambiguous
issuance automatically: wait for the 60-second per-account cooldown before a new grant.
That new grant invalidates the previous one. No account/password/session changes at issuance.

Public `POST /api/v1/auth/password-reset/confirm` accepts token and `new_password`
(12–256 characters) in the JSON body. Success is 204, no-store, clears browser auth cookies,
revokes all target sessions and consumes the grant atomically. No automatic login, username
or seed change. Mandatory first-access personalization is retained. The site strips the
fragment immediately, never saves it to browser storage and uses a no-referrer policy.
Invalid/expired/used links and revoked issuer permissions return the same public
`password_reset_unavailable` error. Validation responses never echo tokens or passwords.

Only token hashes are stored, one grant per user. TTL 15 minutes, per-account issuance
cooldown 60 seconds plus API and public IP budgets. Locks client → User → grant; bounded
Argon2 work outside SQL transaction. Password fingerprint, expiry, current token, active
account and issuer key/scope are revalidated after locks. A password change, superseding
grant, suspension, revoked issuer or consumed/expired grant denies redemption. Admins,
suspended/deleted users and accounts without a Discord association require operator help.

The standalone bot sources and operator upgrade steps are in `discord-bot/README.md`.
A new key with users:create, users:read and users:reset-password is sufficient for the new
panel; downloads:read is not required. The bot is not redeployed by the application workflow.

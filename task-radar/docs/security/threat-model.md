# Mela Task Radar — Threat Model

Last updated: 2026-05.

## 1. System overview

Mela Task Radar is a multi-tenant SaaS that scans a user's Microsoft 365 mailbox / Teams chats, extracts actionable tasks via Azure OpenAI, and optionally syncs them to Excel and Planner. Mela (a separate AI assistant) and an MCP HTTP server consume the same backend on behalf of the signed-in user.

Trust boundaries:

```
[ Browser ] --HTTPS--> [ FastAPI API ] --HTTPS--> [ Microsoft Graph ]
                              |--> [ Azure OpenAI ]
                              |--> [ Azure SQL / SQLite ]
                              |--> [ Azure Key Vault ]      (secrets)
                              |--> [ Azure Service Bus ]    (scan queue)
[ Mela / MCP client ] --HTTPS--> [ FastAPI API ]            (per-user agent token)
```

## 2. Assets

| Asset | Sensitivity | Owner |
| --- | --- | --- |
| Microsoft Graph access / refresh tokens | **Critical** — full mailbox access | User |
| JWT signing key (`JWT_SECRET`) | **Critical** — forges any session | Ops |
| Token-encryption key (`TOKEN_ENCRYPTION_KEY`) | **Critical** — decrypts every cached Graph token | Ops |
| Per-tenant Azure client secret | High | Tenant admin |
| Audit log | High — incident-response evidence | Ops |
| User task content (subjects, excerpts) | High — may contain confidential data | User |
| Source-message metadata | Medium | User |
| Agent tokens | High — full per-user API access | User |
| Session cookies | High — full per-user API access | User |

## 3. STRIDE per asset

### 3.1 Graph tokens
- **S** Spoofing → mitigated by MSAL `id_token` `nonce` + `iss`/`aud` verification on callback.
- **T** Tampering → tokens encrypted at rest with Fernet (`f1:` scheme) + MultiFernet rotation.
- **R** Repudiation → audit chain records every issuance / refresh / revocation with `request_id`.
- **I** Info disclosure → no token, no `token_reference`, no `Authorization` header is ever logged (real redactor + JsonFormatter strip patterns).
- **D** DoS → rate limiting on `/api/auth/microsoft/login` (10/min) and on token refresh paths.
- **E** Elevation of privilege → no header-based impersonation; agent tokens scoped to one user; admin role gates `require_admin`.

### 3.2 JWT / session
- **S** Spoofing → HS256 only; `iss`, `aud`, `nbf`, `exp`, `jti` enforced.
- **T** Tampering → signature check rejects altered tokens.
- **R** Repudiation → server-side `sessions` row keyed by `jti`, revocable; "sign out everywhere" sets `revoked_at`.
- **I** Info disclosure → cookies are HttpOnly, Secure (prod), SameSite=Lax + `__Host-` prefix optional.
- **D** DoS → rate limit per principal id + IP; CSRF blocks cross-origin form submission.
- **E** Elevation → admin role re-checked on every admin endpoint.

### 3.3 Audit log
- **T** Tampering → SHA-256 hash chain (`prev_hash`/`entry_hash`/`seq`); admin verify endpoint replays end-to-end. Mirrored to `audit` logger so a SIEM can co-store the chain — silent DB edits are detectable but not preventable; for full WORM ship to immutable Azure Storage with legal hold (planned).
- **I** Info disclosure → audit details exclude raw secret material; only metadata booleans are persisted.

### 3.4 Per-tenant Azure client secret
- **I** Info disclosure → never returned through any API; stored in Key Vault, only the opaque `kv://…` reference touches the DB.
- **T** Tampering → admin-only `PUT /api/admin/tenant-config`; every change audited with the actor user id.

## 4. Top residual risks

1. **Insider with DB write access** can re-compute the hash chain after tampering, defeating the chain. Mitigation: ship audit rows to immutable Azure Storage / Log Analytics on a schedule (planned, Phase 7.x).
2. **Shared OpenAI keys** — `AZURE_OPENAI_API_KEY` is global. Mitigation: rotate quarterly; move to per-tenant deployment (planned).
3. **Backup exfiltration** — DB backups contain encrypted token references; an attacker who also exfiltrates the Fernet key recovers all Graph tokens. Mitigation: Key Vault separation; Managed Identity-only access in prod; bi-weekly key rotation.
4. **Compromised admin** — can invoke `/admin/security/lockdown` (a recovery primitive) but also `PUT tenant-config`. Mitigation: 4-eyes on admin role grants; alert on `tenant_config.update` audit events.

## 5. Out of scope

Hardware key management, side-channel attacks against Azure Key Vault, social engineering of Microsoft Entra admins.

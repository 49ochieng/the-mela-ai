# Mela Task Radar — Incident Response Runbook

Last updated: 2026-05.

## 1. Severity definitions

| Sev | Definition | Initial response time | Examples |
| --- | --- | --- | --- |
| 1 | Confirmed exfiltration of customer data, credential leak, or active intrusion | 15 min | JWT secret found on GitHub; Graph token in pastebin |
| 2 | Unconfirmed but credible breach signal; significant degradation | 1 hr | Audit chain divergence; spike in 401/403 |
| 3 | Hardening regression, suspicious user report | 4 hr | Missing security header in prod; CSP report flood |
| 4 | Routine — no customer impact | 1 business day | Dependabot alert; expired cert renewal pending |

## 2. On-call

Primary: rotation listed in PagerDuty `mtr-secops`. Secondary: Engineering lead. Comms lead (Sev 1/2): Founder.

## 3. Sev-1 playbook

1. **Lock down.** From an admin browser session: `POST /api/admin/security/lockdown`. This revokes every session, every agent token, and disconnects every Graph connection. Customers must re-authenticate.
2. **Snapshot.** Capture DB snapshot, Key Vault secret versions in use, last 24 h of API + audit + worker logs.
3. **Verify audit chain.** `GET /api/admin/audit/verify`. Note the lowest broken `seq` if any; that anchors the tampering window.
4. **Rotate keys.** In Key Vault, generate new versions of `JWT_SECRET`, `TOKEN_ENCRYPTION_KEY`, and the Microsoft client secret. Set the new value as primary; keep the old one as `*_SECONDARY` for the grace window (≤ 8 h for JWT, until the re-encrypt job finishes for Fernet).
5. **Revoke at the IdP.** In Microsoft Entra: revoke active refresh tokens for the application, rotate the per-tenant client secret if applicable.
6. **Comms.** Internal Sev-1 channel within 15 min. Customer notification within 24 h. Regulator notification within 72 h (GDPR Art. 33). Use templates in `docs/security/comms-templates/`.
7. **Eradicate.** Patch the root-cause vulnerability, deploy, then unset the lockdown by allowing customers to re-login.
8. **Post-mortem.** Blameless write-up within 5 business days; track action items in `docs/security/post-mortems/`.

## 4. Sev-2 playbook

Same sequence but the lockdown step is replaced with targeted revocation:

- Suspicious user: revoke their sessions (`UPDATE sessions SET revoked_at=now() WHERE user_id=…`) and agent tokens.
- Suspicious tenant: revoke all sessions/tokens for that `tenant_id`.

## 5. Common diagnostic queries

```sql
-- Active sessions per user
SELECT user_id, count(*) FROM sessions WHERE revoked_at IS NULL AND expires_at > now() GROUP BY user_id;

-- Audit events for a window
SELECT created_at, action, user_id, ip, request_id FROM audit_logs WHERE created_at > now() - interval '1 hour' ORDER BY seq;

-- Hash chain status
GET /api/admin/audit/verify
```

## 6. Customer comms templates

Stored in `docs/security/comms-templates/` (placeholder; populate per regulator guidance).

## 7. Recovery validation

Before declaring "all clear":

- `GET /api/admin/audit/verify` returns `ok: true`.
- All known production-boot-guard env values still present.
- A canary user can complete OAuth flow and run a scan end-to-end.
- No 5xx in last 30 min.

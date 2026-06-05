# Security Policy

## Reporting a Vulnerability

If you discover a security vulnerability in Mela AI, please do **not** open
a public GitHub issue. Instead, email the maintainers with:

- A clear description of the issue and its impact.
- Steps to reproduce, including any proof-of-concept payloads.
- The commit hash or release version where you observed the behavior.
- Whether you would like public credit once a fix is released.

You should expect an acknowledgement within **two business days** and a
remediation plan within **ten business days** for confirmed issues.

## Supported Versions

Only the `main` branch and the most recent tagged release receive security
patches. Older releases must be upgraded.

## Operational Controls

The following controls are enforced by code and CI today:

- **Server-side session lifecycle.** Every authenticated request is bound
  to a `user_sessions` row; the row tracks issued-at, expires-at, and
  last-activity-at. Sessions auto-expire after **30 minutes of inactivity**
  or **12 hours absolute**, whichever comes first. Admins can revoke any
  user's sessions immediately via `POST /api/v1/admin/users/{id}/revoke-sessions`.
- **Account disable revokes sessions.** Toggling `is_active=False` on a
  user revokes every active session for that user atomically.
- **Profile + tenant isolation.** All project/chat reads pass through
  `authorization.check_*_access`, which fails closed when the profile mode
  or tenant on the record does not match the caller's `ProfileContext`.
- **Document isolation.** Document list/get/status endpoints return only
  the caller's own uploads (or all when the caller is Admin); search in
  work mode returns 503 instead of falling through to a non-ACL backend.
- **Secret discipline.** No secret value may live in the tracked tree.
  CI runs `gitleaks` on every push and PR, and a pre-commit hook blocks
  staged secrets locally. Production startup refuses to boot when
  `JWT_SECRET_KEY`, `AZURE_TENANT_ID`, or `AZURE_CLIENT_ID` is missing,
  and rejects placeholder JWT keys.
- **Log redaction.** `app.core.logging._SecretRedactFilter` scrubs
  JWT-shaped tokens, bearer headers, and `secret=…` query strings out
  of every log record before it leaves the process.
- **Tenant isolation in JWT.** `AzureADAuth.validate_token` rejects any
  Entra access token whose `tid` claim does not match the configured
  tenant (single-tenant deployments).

## Reviewers

Security-relevant changes (anything under `backend/app/core/security.py`,
`authorization.py`, `sessions.py`, `middleware.py`, or
`profile_context.py`, plus changes to `.github/workflows/security.yml`,
`.pre-commit-config.yaml`, or `.gitleaks.toml`) require an explicit review
by a security-named codeowner.

# Security baseline

## Implemented in this scaffold
- Secret configuration requires `APP_SECRET` with minimum length 32.
- Local bootstrap admin credentials via environment configuration.
- Signed session cookies with expiration.
- HttpOnly session cookie; SameSite=Strict.
- CSRF double-submit token on logout.
- Session invalidation on logout in the current process.
- Basic login attempt throttling.
- Localhost-bound development frontend and Docker port mapping.

## Required before production
- Bind locally by default; explicit LAN exposure.
- Authentication and authorization on all non-public endpoints.
- Secure cookies, CSRF on every state-changing route, Host/Origin validation.
- Persistent per-user rate limits and session revocation.
- Database-backed users, sessions, roles, and permissions.
- Bound file size, rows, numeric ranges, JSON depth, and execution time.
- No shell execution with untrusted values.
- Secrets from secret manager; never log them.
- Parameterized database access, least privilege, migrations, backups.
- Dependency pinning/lockfiles, CI security scans, and code review.
- Audit events for auth, access changes, and research artifacts.
- Network/process/compute operations have timeouts and cancellation.

## Known limitations
Current auth is a single bootstrap admin account. Throttling and session revocation are process-memory only and reset on restart; running multiple workers can bypass them. Password is supplied as environment configuration, not a database hash. This scaffold is not production-ready and must not be internet-exposed or used for live trading.

Release gate: production requires persistent identity/session stores, complete authorization, CSRF/Origin protections, rate limits, persistence/backup, security tests, and review.

# Security baseline

Local bind by default; explicit LAN exposure. Auth and authorization on all non-public endpoints. Secure HttpOnly SameSite cookies, CSRF, Host/Origin validation. Rate/concurrency limits. Bound input sizes and numeric ranges. No shell with untrusted input. Secrets out of source and logs. Parameterized DB, least privilege, migrations and backups. Pinned dependencies, CI scans, audit logs, timeouts and cancellation.

Release gate: authentication, CSRF, permissions, rate limits, persistence, backups, dependency review, and security tests are required before production.

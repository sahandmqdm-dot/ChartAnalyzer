# Security notes

## Current protections

- LAN access uses a random per-run token when no configured token is supplied.
- Token comparisons use constant-time comparison; successful query-token access is exchanged for an HttpOnly, SameSite=Strict cookie.
- Non-loopback requests are checked against an explicit Host allowlist; POST requests require JSON and are size-limited.
- Responses include no-store, nosniff, no-referrer, frame denial and a restrictive Content-Security-Policy.
- User-supplied UDF URLs must match an allowlisted scheme, hostname, port and path boundary; redirects are disabled for upstream HTTP requests.
- The optional MCP integration is read-only, launches without a shell, validates its launch configuration and receives a minimal child-process environment without the application's LLM keys or LAN token.
- Local configuration, environment files, database files and runtime artifacts are excluded by .gitignore.

## Deployment guidance

1. Prefer the default loopback-only mode: `python app.py`.
2. Use `python app.py --lan` only on a trusted private network. The built-in server uses plain HTTP; the LAN token is visible to anyone able to sniff that network.
3. Never expose the built-in HTTP server directly to the public Internet. If remote access is required, put it behind a maintained HTTPS reverse proxy with authentication and network access controls.
4. Keep `config.json` private. On POSIX systems use `chmod 600 config.json`; do not commit it or paste credentials into issues/logs.
5. Treat `udf_allowlist`, `iran_crypto_mcp.command`, and `iran_crypto_mcp.args` as administrator-controlled configuration. Do not let untrusted users edit them.
6. Rotate configured `access_token` and provider keys if they may have been exposed.

## Remaining limitations

- The built-in server does not terminate TLS. Its session cookie therefore cannot be marked Secure in the current direct-LAN mode.
- Loopback clients intentionally bypass the LAN token. This assumes local processes on the same machine are trusted.
- The token is shared by all LAN clients; there are no per-user identities, roles, or server-side session revocation.
- Rate limiting is in-memory and IP-based, not a distributed abuse-control system.
- CSP still permits inline scripts/styles because the dashboard is currently a single embedded HTML page. Do not insert untrusted content into HTML; use text-only DOM APIs for dynamic values.
- A configured MCP command is executable by design. Only administrators should be able to change this setting.
- Automated dependency auditing detects known vulnerable packages; it does not prove the application is free of vulnerabilities.

## Regression tests

Run:

```bash
python -m pip install -r requirements.txt
python -m pytest -q
python -m compileall -q .
python -m pip install pip-audit
python -m pip_audit -r requirements.txt
```

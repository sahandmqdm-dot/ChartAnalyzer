# Evidence Integrity and Production Gate

## What changed

The production decision path always requires fresh, matching OOS strategy evidence.
The legacy `gate.require_oos_validation` setting is ignored by `app.make_gate`.
Research configuration may tighten policy thresholds, but cannot relax the
mandatory floors or disable cost-stress, positive-confidence-interval, or
gap-free-data requirements.

Persisted deployment evidence is authenticated with HMAC-SHA256. Any edit to
a signed record invalidates its signature and causes the live deployment gate
to fail closed.

## Signing-key configuration

Set `CHARTANALYZER_EVIDENCE_SIGNING_KEY` in the process environment before
starting the application and before generating OOS evidence. The value must
contain at least 32 UTF-8 bytes. Use a cryptographically random secret and
inject it through the deployment secret manager. Never commit it, place it in
`config.json`, log it, or share it in support reports.

Example for a one-session PowerShell process (replace the value using a secure
secret-injection mechanism; do not use a hard-coded production value):

```powershell
$env:CHARTANALYZER_EVIDENCE_SIGNING_KEY = (python -c "import secrets; print(secrets.token_hex(32))")
python app.py
```

The example generates an ephemeral key. It is suitable only for a disposable
local test session: restarting with a different key invalidates all evidence
signed by the previous key. Production deployments must load a stable secret
from a protected secret store and preserve it across restarts.

## Failure behavior

- If the key is missing or shorter than 32 bytes, evidence may be written for
  research diagnostics, but it is unsigned and cannot authorize deployment.
- If a signature is absent, malformed, or invalid, evidence is rejected.
- If the key changes, previously signed evidence is rejected. Re-run the
  complete OOS validation under the new key; do not re-sign old evidence.
- The HMAC protects integrity only while the signing key remains secret. It
  does not provide non-repudiation, immutable storage, or protection against
  an attacker who can read the key or compromise the running process.
- HMAC is an interim local integrity control. A future hardened deployment
  should support OS-backed key storage (Windows DPAPI/Credential Manager) and
  cloud KMS/Secret Manager without exposing key material to application logs.

## Operational warning

After upgrading, existing unsigned evidence files are intentionally not
deployable. The application will return `NO_TRADE` until fresh evidence is
generated with the configured signing key and passes every mandatory gate.
This change does not validate the statistical correctness of the underlying
backtest, prove holdout independence, or establish a profitable trading edge.

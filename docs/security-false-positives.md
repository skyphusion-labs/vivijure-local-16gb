# Security audit false positives

Documented dismissals for adversarial-audit (K2.7/K3) findings under the homelab GPU stack threat model.

## Homelab operator trust

The local-16gb stack runs on operator-controlled hardware with a single bearer token, cloudflared tunnel, and shared `/shared` volume. R2 credentials in `.env` for cloudflared/ready services are intentional for homelab tunnel auth.

## Record

| Date | Audit | Finding | Rationale |
| --- | --- | --- | --- |
| 2026-07-23 | K3 verify ~18:04 | R2 secret in cloudflared/ready env_file | Homelab operator stack; single-tenant tunnel |
| 2026-07-23 | K3 verify ~18:04 | Backend token on 1777 shared volume | Homelab tunnel auth; operator-controlled box |
| 2026-07-23 | K3 verify ~18:04 | Single token cross-project bucket access | Architecture: one operator token + key prefix guards |
| 2026-07-23 | K3 verify ~18:04 | Fork PRs build runtime.Dockerfile | Fork-safe CI; no secrets in docker-build-smoke job |
| 2026-07-23 | K3 verify ~18:04 | Bundle tar no size cap | Operator-supplied bundles; _safe_extract blocks traversal |

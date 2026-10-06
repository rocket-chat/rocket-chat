# Security & Compliance

Rocket Chat is built with defense-in-depth principles for enterprise compliance and secure autonomous code execution.

---

## 1. Security Architecture Summary

```
+-----------------------------------------------------------------------------+
| LAYER 1: NETWORK & INGRESS SECURITY                                         |
| - Slack: 100% outbound Socket Mode (No inbound ports or public IP needed)    |
| - GitHub Webhooks: HMAC SHA-256 signature verification                      |
| - OIDC SSO: RS256 JWT signature verification via corporate JWKS             |
+-----------------------------------------------------------------------------+
                                      |
+-----------------------------------------------------------------------------+
| LAYER 2: TENANT ISOLATION (DATABASE)                                        |
| - PostgreSQL 16 Row-Level Security (RLS) enforced at the database engine    |
| - Non-superuser application connections (rocket_app) cannot bypass RLS      |
| - Transaction-local tenant context injection (SET LOCAL app.current_org_id) |
+-----------------------------------------------------------------------------+
                                      |
+-----------------------------------------------------------------------------+
| LAYER 3: CREDENTIAL SECURITY (AT REST)                                      |
| - BYOK API keys encrypted with hardware-grade AES-256-GCM                   |
| - PBKDF2 HMAC-SHA256 (100,000 iterations) with random 16-byte salt per key  |
| - 96-bit unique IVs and 128-bit authentication tags prevent tampering        |
+-----------------------------------------------------------------------------+
                                      |
+-----------------------------------------------------------------------------+
| LAYER 4: RUNTIME SANDBOX ISOLATION                                          |
| - Sandboxes isolated in dedicated agent-sandboxes namespace                 |
| - Kubernetes NetworkPolicy blocks cloud metadata (169.254.169.254/32 SSRF)  |
| - ResourceQuotas prevent runaway CPU/memory or container sprawl             |
+-----------------------------------------------------------------------------+
                                      |
+-----------------------------------------------------------------------------+
| LAYER 5: SUPPLY CHAIN & COMMIT ATTESTATION                                  |
| - Mandatory Co-authored-by git trailers attribute human and AI contributors |
| - Cryptographic signing via GitHub App Installation Tokens or GPG           |
+-----------------------------------------------------------------------------+
```

---

## 2. Cloud Instance Metadata Mitigation (SSRF)

When an autonomous agent runs arbitrary code (such as a developer test suite), malicious dependencies could attempt to exfiltrate IAM role credentials by querying cloud instance metadata endpoints:
- AWS IMDSv1/v2: `http://169.254.169.254/latest/meta-data/`
- GCP Metadata Server: `http://169.254.169.254/computeMetadata/v1/`
- Azure Instance Metadata: `http://169.254.169.254/metadata/instance`

Rocket Chat mitigates this attack vector at the Kubernetes networking layer via an automated `NetworkPolicy`:

```yaml
apiVersion: networking.k8s.io/v1
kind: NetworkPolicy
metadata:
  name: sandbox-network-policy
  namespace: agent-sandboxes
spec:
  podSelector: {}
  policyTypes:
    - Egress
  egress:
    - to:
        - ipBlock:
            cidr: 0.0.0.0/0
            except:
              - 169.254.169.254/32
```
All agent sandboxes can access the public internet (to clone repositories, install packages, and query APIs), but cannot communicate with the cloud instance metadata IP.

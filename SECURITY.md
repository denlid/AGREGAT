# AGREGAT Security Policy

## Reporting a Vulnerability

**Please do NOT report security vulnerabilities through public GitHub issues.**

Instead, send an email to: **alephxau@gmail.com** with the subject line:
`[SECURITY] AGREGAT Vulnerability Report`

### What to Include

- Description of the vulnerability
- Steps to reproduce (proof of concept)
- Potential impact
- Suggested fix (if any)

### Response Timeline

| Stage | Timeline |
|-------|----------|
| Acknowledgment | 48 hours |
| Initial assessment | 5 business days |
| Fix development | 14 business days |
| Public disclosure | After fix is deployed |

## Security Architecture

### Server-Side Security

| Component | Protection |
|-----------|-----------|
| **Authentication** | bcrypt (cost 14) + TOTP MFA (SHA-256) |
| **Session Management** | PostgreSQL-backed, 1-hour expiry, IP/UA binding |
| **Rate Limiting** | Per-endpoint sliding window (Redis-backed) |
| **CORS** | Explicit origin whitelist (no wildcards in prod) |
| **CSP** | Strict Content-Security-Policy headers |
| **HSTS** | max-age=31536000; includeSubDomains; preload |
| **CSRF** | Double-submit cookie pattern |
| **TLS** | HTTPS enforced, certifi CA bundle |

### Client-Side Security

| Component | Protection |
|-----------|-----------|
| **License Verification** | Ed25519 server signatures (fail-closed) |
| **Device Fingerprint** | CPU + Disk + MAC + BIOS + Motherboard serial |
| **Anti-Debug** | IsDebuggerPresent, hardware breakpoints, timing checks |
| **Anti-Tamper** | IntegrityGuard (bytecode hash monitoring) |
| **Memory Protection** | XOR-encrypted tokens, VirtualProtect (Windows) |
| **Time Manipulation** | Server time tracking, clock rollback detection |
| **Audit Logging** | Local append-only security event log |

### Data Protection

| Data | Protection |
|------|-----------|
| **Passwords** | bcrypt with cost factor 14 |
| **TOTP Secrets** | Fernet encryption at rest |
| **Backup Codes** | Fernet encryption, single-use, consumed on use |
| **Activation Keys** | SHA-256 HMAC with server-side pepper |
| **Device Fingerprints** | HMAC with server-side pepper |
| **Refresh Tokens** | SHA-256 hash stored (never plaintext) |
| **Session Tokens** | SHA-256 hash stored (never plaintext) |

## Docker Security

- **Multi-stage builds** — build tools excluded from runtime image
- **Non-root user** — `agregat` user with `/usr/sbin/nologin` shell
- **No exposed DB/Redis ports** — Docker internal network only
- **Redis hardening** — dangerous commands disabled (FLUSHDB, DEBUG, CONFIG)
- **Health checks** — curl-based (no password in process list)
- **Read-only root filesystem** — recommended for production

## Build Security

### Nuitka Compilation (Desktop Client)

```bash
nuitka --standalone --onefile \
  --enable-plugin=tk-inter \
  --include-package=engine \
  --include-package=gui \
  --output-filename=AGREGAT.exe \
  --windows-icon-from-ico=assets/icon.ico \
  --windows-company-name="AGREGAT" \
  --windows-product-name="AGREGAT" \
  --windows-file-version="7.78.0.0" \
  --windows-product-version="7.78.0" \
  --windows-file-description="AGREGAT Trading Platform" \
  app.py
```

### Pre-Build Checklist

- [ ] Ed25519 public key embedded in `license_client.py`
- [ ] TLS certificate pin configured (`AGREGAT_CERT_PIN`)
- [ ] `APP_ENV=production` (no dev mode bypass)
- [ ] `AGREGAT_DEV_MODE` NOT set
- [ ] All `.env` files excluded from build
- [ ] `SECURITY_AUDIT.md` reviewed and all P0/P1 items resolved

## Known Security Trade-offs

### Accepted Risks (Beta)

1. **XOR memory encryption** — Not cryptographically secure. Defeats casual memory scraping but not a determined reverse engineer. Acceptable for beta; will upgrade to `CryptProtectMemory` for production.

2. **No code signing** — Desktop binary is not signed. Users may see SmartScreen warnings. Will add EV code signing certificate for production release.

3. **No WAF** — No Web Application Firewall in front of the server. Rate limiting and input validation provide basic protection. Will add Cloudflare or similar for production.

4. **No bug bounty program** — Will establish after production launch.

### Future Improvements (Post-Beta)

- [ ] EV Code Signing certificate for Windows binaries
- [ ] Notarization for macOS binaries
- [ ] Cloudflare WAF / DDoS protection
- [ ] Hardware Security Module (HSM) for key management
- [ ] Automated dependency scanning (Dependabot / Snyk)
- [ ] Penetration testing by third party
- [ ] Bug bounty program
- [ ] SOC 2 compliance audit

## Security Updates

Security patches are released as priority updates. Users should update immediately when a security release is announced.

### Version History

| Version | Date | Security Changes |
|---------|------|-----------------|
| 7.78 | 2026-06-07 | Comprehensive security audit: Ed25519 signatures, TOTP SHA-256, memory protection, anti-debug enhancements, Docker hardening |

## Contact

- **Security Issues**: alephxau@gmail.com
- **General Support**: alephxau@gmail.com
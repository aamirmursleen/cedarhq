# CedarHQ

CedarHQ is an original SaaS platform for international founders who need a clear, evidence-backed workflow for US company formation and early back-office compliance.

This MVP intentionally avoids local package builds. It runs on Python 3.12 standard library, SQLite, and vanilla HTML/CSS/JS. Real-world providers are represented by swappable sandbox adapters until production credentials are configured.

## Run Locally

```bash
python3 app.py --migrate --seed-demo --init-only
python3 app.py --host 127.0.0.1 --port 8088
```

Open `http://127.0.0.1:8088`.

For access from another device on the same network, bind to all interfaces and open the host IP:

```bash
python3 app.py --host 0.0.0.0 --port 8088
```

Demo users created only by `--seed-demo`:

- Founder: `founder@cedarhq.local` / `ChangeMe123!`
- Staff: `ops@cedarhq.local` / `ChangeMe123!`
- Admin: `admin@cedarhq.local` / `ChangeMe123!`

## Validation

```bash
python3 -m unittest
python3 -m py_compile app.py cedarhq/*.py
```

Use `vm115-build` for manual validation on VM115 when available. Do not run local bundlers, Docker builds, or full local browser suites on this host.

## Production Notes

Before production use, configure real credentials for Google OAuth, Stripe, email delivery, object storage/KMS, e-signature, legal filing, registered agent, mailroom, banking, accounting, tax, Shopify, Amazon, and AI providers. Sandbox workflows are clearly labeled and must not be represented as legal filings.

# Hardware Overwatch: complete hackathon journey

The seven onboarding phases are connected to the operations desk using fictional enterprise data and a running three-node Redfish simulator. No external account, API key, cloud deployment, language-model subscription or hardware is needed.

## Run and open

From `WatcherMCP`:

```bash
bash scripts/run-hackathon.sh
```

Open **http://127.0.0.1:8080** and click **Enter hackathon demo**. No token copying is needed. This shortcut is available only when the server is explicitly launched with `--hackathon` and still obeys the loopback host/origin boundary. Standard mode retains its normal token login.

Runtime data is stored under `/tmp/overwatch-hackathon-runtime`. The earlier standard workspace at `/tmp/hardware-overwatch-runtime` is preserved. `/tmp` may be cleared at reboot; set `WATCHER_DEMO_DATA` to another writable directory if desired. Stop the server with Ctrl+C.

Equivalent command:

```bash
python3 -m overwatch demo --hackathon --data /tmp/overwatch-hackathon-runtime
```

## Present the journey

1. Enter an organization name and region, then **Create demo workspace**.
2. Click **Complete all seven phases**, or run each phase separately. Each phase generates evidence, invokes the existing gate checks, and records explicitly fictional owner reviews. A real failure of the local Redfish probe blocks progress.
3. Review the three thermal cases created when onboarding reaches shadow mode. **Inspect case** opens the existing case/evidence workflow.
4. **Draft maintenance request** creates a linked mock CMMS work order and local notice. Nothing is sent externally.
5. **Compare service bids** opens the existing tender comparison: two eligible bids, one excluded lead, exact INR arithmetic. The lowest eligible sample bid is INR 13,400 for three nodes.
6. Use the **Enterprise assistant** to summarize current cases, inspect temperatures, or create a proposal. Responses use current records and deterministic templates; no LLM is invoked, and the assistant cannot approve or execute.
7. **Simulate technician repair + verify recovery** records a fictional external technician action, switches the lab to recovered, and closes cases only after three distinct fresh HTTP observations. The hackathon mode uses a zero-second soak so this demonstration takes seconds. The broker never executes a hardware repair.
8. **Download evidence pack** exports onboarding, receipts, case history, artifacts, work orders, bids and the hash-chained audit ledger as JSON.
9. Optionally **Export and simulate offboarding**: export first, revoke mock gateway tokens/certificates/enrollment tokens, and record account-removal/deletion simulation. Actual local history is retained for judging.

**Restart onboarding walkthrough** clears the current phase approvals and revokes mock access while retaining cases, evidence and audit history. It allows repeated presentations without deleting earlier work.

## What each phase demonstrates

| Phase | Working demo behavior | Fake data / simulated boundary |
|---|---|---|
| Identity | Tenant configuration, six role mappings, authority validation, nine fictional owners and one deactivated user; separate owner identities sign their assigned gates | SSO, SCIM and MFA results are generated local identity fixtures, not real Entra/Okta sessions |
| Sites and hardware | Three declared serials reconcile against three actual HTTP resources in the bundled Redfish lab; rack, site, owner and service links shown | CMDB/DCIM data and the organization are fictional; hardware is a software simulator |
| Collectors | Site-bound 24-hour single-use enrollment tokens; wrong-site/reused/expired tokens rejected; primary and standby certificate records rotate; read-only vault resolver rejects broker paths; local AccountService privilege check | Certificates represent simulated outbound mTLS, not cryptographic TLS sessions; coverage is seven days of synthetic history |
| Systems of record | Nine local connector datasets are checked and hashed: seven required categories plus procurement/OEM entitlement; linked drafts feed operations | External applications are mock adapters; no credential or network access to those applications |
| Policy | Thirty synthetic historical cases, T0 replay report, distinct fictional risk/security reviews, versioned settings, invalidation on changes | Historical outcomes and MFA evidence are fictional; the existing backend actually prevents permits/execution during onboarding/shadow |
| Enterprise AI | Two mock clients, allowed-scope manifest, audience/scope/expiry rejection tests, eight fake pilot users and one week of fake audit history; read/propose assistant uses current local records | A mock gateway contract and local assistant, not a hosted OAuth deployment or LLM integration |
| Shadow and operations | All six preceding gates checked; 56 synthetic daily outcomes produce 48 recommendations, 40 confirmations and 83.3% synthetic precision; real local readings create and close cases | Eight weeks are represented by fake historical rows, not eight elapsed weeks. No autonomy promotion or production-performance claim |

The UI distinguishes **demo readiness** from production readiness. Fake people and history are allowed for the hackathon and are labeled throughout the evidence pack. The full mock journey can complete; enterprise credentials are not required for it.

## Verification

The suite exercises the complete journey plus failure cases: rejected enrollment reuse, vault isolation, forbidden gateway scopes, wrong audience/expiry, incorrect phase order, failed owner sign-off, real simulator outage, retained T0, exports without credential tokens, restart and offboarding.

```bash
python3 -m unittest discover -s tests -v
PYTHONPYCACHEPREFIX=/tmp/overwatch-pycache python3 -m compileall -q overwatch
node --check overwatch/web/hackathon.js
```

The browser runner uses an isolated temporary database and checks login, all seven phases, three nodes, nine connectors, drafts, the assistant, bids, export, recovery, page reload, mobile width, repeated incidents and offboarding:

```bash
PLAYWRIGHT_MODULE=/tmp/overwatch-browser/node_modules/playwright \
PLAYWRIGHT_EXECUTABLE=/opt/google/chrome/chrome \
node scripts/hackathon-smoke.cjs
```

The application needs only Python 3.11+. Playwright and Chrome are optional verification tools, installed/located at those paths in this environment. The runner prints a temporary artifact directory containing screenshots, the JSON export and its report.

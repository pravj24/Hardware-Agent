# Hardware Overwatch: complete hackathon journey

The seven onboarding phases are connected to the operations desk using fictional enterprise data and a running three-node Redfish simulator. No external account, API key, cloud deployment, language-model subscription or hardware is needed.

## Run the API rehearsal

The current frontend is a separate NOC application. It has live onboarding and Gemini drafting, but the old guided hackathon buttons were retired with `overwatch/web/`. Port 8080 serves JSON, not a dashboard. Use the API sequence below for the complete fictional journey, or `npm ci` / `npm run dev` for the dashboard at port 5173.

Start an isolated backend from `WatcherMCP`:

```powershell
python -m overwatch demo --hackathon --data data/hackathon
```

In another PowerShell terminal:

```powershell
$base = 'http://127.0.0.1:8080'
$login = Invoke-RestMethod -Method Post -Uri "$base/api/demo/login" -ContentType 'application/json' -Body '{}'
$headers = @{ Authorization = "Bearer $($login.token)" }
Invoke-RestMethod -Method Post -Uri "$base/api/demo/prepare" -Headers $headers -ContentType 'application/json' -Body '{"name":"Aster Cloud","region":"India"}'
foreach ($step in @('identity','inventory','telemetry','connectors','policy','ai','shadow')) {
    $body = @{ step = $step } | ConvertTo-Json
    Invoke-RestMethod -Method Post -Uri "$base/api/demo/step" -Headers $headers -ContentType 'application/json' -Body $body
}
Invoke-RestMethod -Uri "$base/api/demo/status" -Headers $headers
```

The explicit `--hackathon` flag enables unauthenticated local presenter login. Use only for a local fictional demo. Standard mode requires the generated role tokens. Do not expose this mode to a public network.

## Present the journey

After the seven phases, the three-node HTTP Redfish lab produces cases and verified recommendations at T0. Use the authenticated routes below; POST bodies are JSON and GET routes have no body.

| Action | Route and body |
|---|---|
| View cases and identity | GET `/api/state` |
| Draft linked mock work order and notice | POST `/api/demo/draft` with `{"case_id":"<case ID>"}` |
| Compare fictional service bids | POST `/api/demo/tender` with `{}` |
| Read/propose template assistant | POST `/api/demo/assistant` with `{"question":"Summarize current cases"}` |
| Simulated technician repair and fresh recovery checks | POST `/api/demo/recover` with `{}` |
| Export evidence pack | GET `/api/demo/export` |
| Simulate offboarding after export | POST `/api/demo/offboard` with `{}` |
| Restart phase walkthrough, retain evidence history | POST `/api/demo/restart` with `{}` |

The hackathon assistant uses deterministic templates and does not call an LLM. The separate `/api/assist` endpoint uses Gemini when configured. No route sends real maintenance messages, orders parts or repairs physical hardware. Recovery uses three distinct fresh samples and a zero-second demo soak. Export before simulated offboarding; local history is retained.

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

The API distinguishes **demo readiness** from production readiness. Fake people and history are allowed for the hackathon and are labeled throughout the evidence pack. The full mock journey can complete; enterprise credentials are not required for it.

## Verification

The suite exercises the complete journey plus failure cases: rejected enrollment reuse, vault isolation, forbidden gateway scopes, wrong audience/expiry, incorrect phase order, failed owner sign-off, real simulator outage, retained T0, exports without credential tokens, restart and offboarding.

```powershell
python -m unittest tests.test_hackathon -v
python -m compileall -q overwatch
```

For the current dashboard, run `npm run build` then `node scripts/frontend-smoke.cjs` with Playwright installed. `PLAYWRIGHT_MODULE` can point to an existing package; `PLAYWRIGHT_CHANNEL=msedge` selects installed Edge. This browser check covers live onboarding connection and offline Gemini drafting; the full fictional hackathon journey is verified by the Python tests.

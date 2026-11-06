# Parallel development handoff

Start from the latest `main` at https://github.com/pravj24/WatcherMCP. Each worker should use an isolated worktree and topic branch. The initial safety implementation has already been integrated with onboarding, discovery/scoring, the separate NOC frontend and the MCP deployment gateway; preserve those modules.

| Workstream | Owned files | Deliverable and verification |
|---|---|---|
| Gemini assistant | `overwatch/gemini.py`, `overwatch/assist.py`, `tests/test_gemini.py`, `tests/test_assist.py` | Extend drafts/evidence handling; fake-transport tests first; no approval or execution tools |
| Frontend/deployment | `frontend/`, root npm files, `netlify.toml`, `vercel.json` | Static build and UI; `npm ci && npm test && npm run build`; browser smoke on built output |
| Collection/OEM adapters | `overwatch/redfish.py`, `overwatch/simulator.py`, `integrations/dmtf/`, matching tests | Additional schema/timestamp/event coverage; pinned upstream and authorized physical-lab tests |
| Permission and durability | `overwatch/policy.py`, `overwatch/store.py`, `overwatch/service.py`, workflow/architecture tests | Transactional permission boundaries, durable reconciliation and ledger hardening |
| Sourcing | `overwatch/discovery.py`, `overwatch/scoring.py`, `overwatch/tender.py`, matching tests | Source-backed discovery and qualification; preserve indicative/authoritative separation |
| Onboarding/demo | `overwatch/onboarding.py`, `overwatch/hackathon.py`, matching tests | Honest readiness gates and clearly labeled synthetic scenarios |
| MCP gateway | `overwatch/mcp.py`, `deploy/`, `plugin/`, `.claude-plugin/` | Read-only contracts, OAuth/transport and deployment tests |

`overwatch/api.py`, `frontend/index.html`, `.github/workflows/tests.yml`, `README.md` and shared lockfiles need one designated integration owner during parallel changes. Propose API additions with request/response shapes and authentication requirements before editing those files. Share commit IDs, test results and remaining limitations, then integrate serially without force-pushing.

The frontend model panel calls `/api/assist`; it never calls Google directly. Configure `GEMINI_API_KEY` only in the backend environment. The default no-key stub is explicit and cannot be mistaken for a live model response.

Do not restore the superseded `overwatch/web/` desk over the current `frontend/` application. Current `main` is the integration baseline.

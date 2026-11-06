# Working in WatcherMCP

Canonical remote: `https://github.com/pravj24/WatcherMCP.git`.

## Coordination

- Fetch the latest remote before starting. Other agents may be updating this repository concurrently.
- Use an isolated worktree/branch (`codex/<topic>`) per concurrent task. Do not run branch switches in a checkout another agent is editing.
- Claim a workstream from `docs/parallel-work.md` and agree ownership before changing shared files.
- Preserve other agents' work. Do not force-push, reset away changes or replace the frontend with an older copy.
- Push only after the task's required verification passes. State any live credentials or physical-hardware checks that remain unavailable.

## Boundaries

- Gemini is the backend model provider (`GEMINI_API_KEY`, optional `GEMINI_MODEL`). Do not reintroduce Zhipu/GLM or expose provider keys to frontend code.
- Keep model tools read/draft-only. Approval, policy, signing, money and execution remain deterministic and authenticated.
- Never let model text become a shell command, approval, permit or supplier qualification.
- Preserve original Gemini response content/thought signatures when returning function results.
- Production hardware writes remain disabled in this reference implementation. Simulation evidence cannot authorize production actions.
- Keep `data/`, `.env`, keys, tokens, databases and browser artifacts out of commits.

## Tests and build

- Write the failing behavioral test before changing an invariant or integration contract.
- Python: `python -m unittest discover -s tests -v`.
- Deployment gateway: `python -m unittest discover -s deploy -p "test_*.py" -v`.
- Frontend: `npm ci`, `npm test`, `npm run build` from the repo root; output is `frontend/dist/`.
- The actual DMTF tests require `WATCHER_DMTF_REPO` and optionally `WATCHER_DMTF_PYTHON`. Skipped hardware/credential checks are not evidence of live compatibility.
- Update relevant docs with behavior changes. The backend API and frontend are separate processes; do not claim the API root serves the dashboard.

## Shared files

Coordinate changes to `overwatch/api.py`, `overwatch/service.py`, `frontend/index.html`, package lockfiles, CI and root documentation. Keep provider transport in `overwatch/gemini.py`, app/tool orchestration in `overwatch/assist.py`, and deterministic permission code independent of both.

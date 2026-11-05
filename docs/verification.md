# Verification protocol

## Gate 1: local behavioral contracts

Run `python -m unittest discover -s tests -v`. Every release must preserve:

- No action without current verified evidence, the required approvals and a valid permit.
- Exactly one local execution claim per permit under concurrent requests.
- No blind retry after a timeout/exception; unknown outcomes remain reconciliations.
- Budget cannot be reserved twice beyond the configured ceiling.
- No case closure from HTTP success alone, old healthy samples or broken recovery continuity.
- No simulation evidence crossing into a production database or action scope.
- No role escalation via client-submitted actor or role fields.
- No decision permission or write credential accessible through MCP tools.

Assertions use independent scenarios and expected outcomes rather than matching generated prose. Tests intentionally do not claim mathematical exactly-once effects on arbitrary downstream systems.

## Gate 2: upstream connector contract

Set `WATCHER_DMTF_REPO` and optionally `WATCHER_DMTF_PYTHON`. Run `python -m unittest tests.test_dmtf -v` against the revision in `integrations/dmtf/manifest.json`.

Pass conditions: the published resource layout is traversed, stable serial identity is recovered, measurement provenance is explicit, and reset acknowledgment is shown not to establish a changed device state. The test owns and terminates its server process tree.

The built-in WatcherMCP simulator is a narrow scenario tool. It is not claimed to pass DMTF schema validation. Run DMTF Service Validator on captured OEM fixtures and any fixtures claimed to be standards-conformant; separate deliberately malformed negative fixtures.

## Gate 3: local HTTP and UI

`tests/test_api.py` runs real HTTP requests against the lab and application, including authorization, origin checks, approvals, permit issuance, execution, reconciliation and case closure. The dashboard static assets are served through the same app. Run `node --check overwatch/web/app.js` for syntax validation.

Manual/browser smoke: connect as operator, run degradation through POST_CHECK, confirm unhealthy readings prevent closure, then recover and collect across the soak. Inspect evidence and audit tabs. Switch to procurement and compare bids; unverified candidates must be excluded. Switch to policy_admin and confirm the kill switch prevents execution of a previously issued permit.

## Gate 4: physical hardware (not satisfied by this repository)

For each supported OEM/model/firmware combination, record actual authentication/roles, TLS behavior, subscriptions, telemetry timestamps, asynchronous task semantics, workload drain, firmware persistence and recovery results. A mock does not validate physical safety or vendor compatibility.

## Gate 5: customer shadow and canary (not satisfied by this repository)

Measure calibrated predictive performance against actual failure labels, on time-based holdouts, per hardware family and failure mode. Agree recovery windows and workload impact with the customer. Promote individual action classes only after named approval and tested recovery paths.

## Evidence package

Retain source commit, fixture revision/hash, policy version, test report, request/response trace where enabled, plan hashes, approvals, permit identifiers, execution results and recovery observations. Keep generated credentials out of reports. `python -m overwatch audit --db data/overwatch.db` checks the local hash chain; it does not prove source truth or resist an attacker rewriting the entire database without an external anchor.

## Failure behavior

| Condition | Result |
|---|---|
| Missing authority, stale timestamp or changed plan | Rejected; refusal event appended |
| Expired/replayed/revoked permit | No adapter call |
| Kill switch or freeze | No new execution |
| Adapter exception | Unknown outcome and retained budget reservation |
| Unknown status after restart | Hold and reconcile; no re-execution |
| Health remains abnormal | Case stays open |
| Requisition draft succeeds | Await fulfillment, not repair completion |
| Case recovers across soak | Close with independently collected receipts |

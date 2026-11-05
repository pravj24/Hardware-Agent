# Implementation status and deployment decisions

## Implemented and testable

| Area | Current behavior |
|---|---|
| Collection | Redfish service discovery, Systems/Chassis links, legacy Thermal and temperature Sensors, bounded pagination, timestamp provenance, same-origin link checks, verified production TLS |
| Cases | One active case per asset, event deduplication, persistent workflow state, stale/dark holds, operator-driven plan preparation |
| Evidence | Original parsed resource data, canonical JSON hashes, source/event identity, measurement/retrieval times, environment identity and deterministic freshness checks |
| Planning | Deterministic thermal-threshold trigger and two template options; no estimated failure probabilities or causal certainty |
| Verification | Receipt integrity, authority, target/environment consistency, plan hash and freshness checks at approval and execution boundaries |
| Authorization | Trusted role mapping, independent procurement/finance approvals at USD 1,000, plan-bound approvals, 120-second signed permits, freeze and kill switch |
| Broker | Atomic durable claim, single-use permit, exact approved plan, budget reservation/settlement, no blind retries, status reconciliation |
| Outcomes | At least three post-action healthy samples over the configured soak; stale data, regression and long gaps reset recovery; procurement drafts wait for fulfillment |
| Tender discovery | Crustdata company-search client (live, token-gated) plus a keyless fixture of real captured maintainer records; approved-vendor list loaded first; leads normalised to typed fields only (free-text descriptions dropped), marked indicative and unqualified; deterministic compliance screen removes supplied sanctioned/blocked suppliers with a reason; Crustdata outage degrades to approved vendors only without guessing |
| Tender scoring | Six-factor weighted qualification rubric (customer-overridable, must sum to 100); pre-qualification must be verified at source to rank; each unverified factor scores zero, not partial; Crustdata signals carried as corroboration only, never authoritative; exact Decimal arithmetic |
| Tender comparison | Reviewed simulation inputs, qualification/screening/availability flags, quote reference and expiry, exact Decimal arithmetic, scope/quantity consistency, no implicit currency conversion |
| Interfaces | Authenticated local API, operations dashboard, three read-only MCP stdio zones |
| Dashboard assistant | Server-side LLM (Claude via the official SDK, lazily imported; `claude-opus-5-5`) that drafts maintenance messages, approval summaries and tender searches via a bounded, append-only tool-use loop. Tools are read/draft-only (case evidence, Crustdata discovery, recent tenders); it cannot approve, send or execute, and is instructed to use only tool-returned figures. Runs an offline stub without `ANTHROPIC_API_KEY`; each invocation is audited to the ledger |
| Verification lab | Stateful in-process fixture server plus a separate actual DMTF integration pinned to a commit |
| CI | Python 3.11/3.13 on Windows/Linux; separate upstream DMTF job |

## Explicitly not implemented

- Production deployment, SSO/OIDC, distributed signing keys, hardware-backed key custody, customer vault integration or separately deployed trust zones.
- Distributed durable workflow engine, replicated stores, transactional outbox, multi-tenant authorization, retention management and immutable external ledger anchoring.
- Fleet-scale monitoring, DCGM/SNMP collectors, EventService subscriptions, full OEM schemas, topology-aware cross-node case merging and facility-cause diagnosis.
- Trained survival/failure models, validated RUL estimates, drift monitoring or benchmark claims. A temperature threshold is only a test trigger.
- Hosted LLM orchestration or autonomous specialist agents. MCP permits model-assisted read-only analysis; it cannot approve or execute.
- Crustdata *watches* (discovery/entity monitoring), live sanctions/entity-list *data* (the screen matches only a caller-supplied blocked/sanctioned list, not a subscribed list), source verification of qualification documents (certs with the issuer, insurance with the broker, reference checks), RFI/RFQ delivery, contract entitlement APIs or financial commitments. Discovery produces indicative leads and scoring consumes reviewed verification inputs; neither performs real supplier outreach, document verification or award.
- CMMS/ERP/ITSM integration or scheduler drain. Requisition execution goes to an explicitly simulated endpoint.
- Real reset, fan controls, firmware updates, rollback or hardware recovery. No such action is allow-listed.
- Provider performance scoring and verified business savings reporting.

These boundaries are necessary because the repository began empty and no customer endpoints, integration credentials or training labels were supplied. The code intentionally does not substitute invented evidence for those dependencies.

## Decisions

1. **Executable vertical slice first.** Start with Python's standard library so the complete safety workflow can run without account setup or dependency installation. Replace the loopback server with a production ingress/service stack when deploying.
2. **Durable facts, bounded tools.** SQLite transactions own state; model context never owns approval state. MCP is a tool contract, not a substitute for process/network isolation.
3. **Simulation-only writes.** A special WatcherMCP lab identity is checked before simulation actions. The production collector is read-only and production action-plan creation is rejected.
4. **Single-use means admission to a durable operation.** The permit is consumed before the network call. A crash or ambiguous exception leads to status queries, never a second command. If both the app and simulator restart, remote outcomes can remain unknown; manual reconciliation is required.
5. **Budget reservations serialize permission grants.** Outstanding/unknown operations retain their reservation; confirmed failure releases it; confirmed success settles it. The amounts are illustrative USD policy inputs, not actual purchases.
6. **An HTTP acknowledgment is not an outcome.** Device health must recover independently. Requisition draft creation does not claim repair completion.
7. **Freshness uses measurement time.** Retrieval-only snapshots can inform a case but cannot pass an action gate. Real OEM adapters must explicitly map reliable measurement semantics.
8. **No secrets in tool results or Git.** MCP has no signing key and returns normalized telemetry fields without raw external text. Dashboard responses expose permit IDs rather than signed envelopes. Generated state and credentials are ignored.

## Production extraction order

1. Move policy, approvals, budget reservations and signing into a separately authenticated service with an asymmetric signing key. Give brokers only the public verification key and scoped vault identities.
2. Move cases/jobs into a durable workflow engine; use PostgreSQL transactions and an outbox. Preserve the behavioral contracts when changing infrastructure.
3. Export evidence and event-chain checkpoints to independently retained WORM storage. Store raw response bytes as well as canonical normalized documents where exact transport preservation is required.
4. Add one partner's asset/CMMS/ERP mappings and supplier review workflow. Keep ERP ownership of POs explicit.
5. Validate one OEM's read and write behavior in a physical lab. Enable individual reversible action classes only after preconditions, task semantics and recovery are proven.
6. Run the PRD's shadow and recommendation periods using real labels before any predictive-performance or outcome claims.

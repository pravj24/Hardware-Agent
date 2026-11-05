# Test-driven architecture record

The repository started empty. Tests were added before the corresponding implementation, then run again after changes.

1. **Domain/collector/tender red baseline:** `test_workflow.py`, `test_redfish.py` and `test_tender.py` initially failed to import the unimplemented modules.
2. **Domain green:** 32 tests passed after implementing persistence, receipts, verification, approvals, permits, budget reservation, reconciliation, recovery, Redfish transport and bid comparison. The first run also caught unclosed SQLite connections on Windows, which were corrected with explicit connection closing.
3. **Interface red baseline:** `test_api.py` and `test_mcp.py` failed to import the missing API and MCP modules.
4. **Interface green:** 45 tests passed with authenticated local HTTP routes, role enforcement, end-to-end collection/execution, read-only MCP lifecycle and tool boundaries.
5. **Additional regression red:** three new tests exposed acceptance of pre-action recovery samples, incorrect treatment of a requisition draft as a repair, and comparison of unequal quantities.
6. **Regression green:** completion-time boundaries, an `AWAITING_FULFILLMENT` state and quantity/scope checks corrected those failures. Added budget-release/unknown-reservation and interrupted-soak tests also passed.
7. **Upstream integration:** collector and action-semantics tests ran against the actual DMTF server at a pinned commit. A Windows subprocess cleanup issue was addressed in the harness.
8. **Architectural fitness:** dependency/tool-boundary tests protect deterministic policy evaluation and prevent the read-only MCP surface from gaining approval/execution capabilities accidentally.
9. **Audit-integrity red/green:** additional negative tests demonstrated that a recomputed receipt hash must be compared with its original ledger anchor, and that ledger corruption must block execution even for an already issued permit. Both checks now fail closed.

This records the observed development sequence, not a certification. Current CI results and the reproducible test suite are the release evidence. Physical hardware behavior and real-world model quality remain separate acceptance gates.

# Security and deployment scope

This release is a loopback development/reference application. Do not expose its standard-library HTTP server directly to a network or connect its simulation action adapter to real hardware.

Generated signing keys, role tokens and databases live under `data/`, which is ignored by Git. Use the operating system's account permissions to restrict this directory. Do not paste these tokens into issue reports or screenshots. The application does not print them in logs.

Approver identity is derived from a server-configured token, not from request data. The demo has separate operator, procurement, finance, viewer and policy-admin tokens. Production must replace this mechanism with authenticated identities, segregation of duties and audited credential rotation.

HMAC permits are bound to exact plans and recorded single-use status. In this reference application, policy and broker code share a process. Production separation requires asymmetric verification, separate deployment identities, customer vaults, restricted networks and an independent revocation/control path.

The ledger detects changes relative to its current chain; an attacker able to rewrite the entire database can reconstruct the chain. External immutable checkpoints and retained original evidence are required for stronger audit guarantees.

Redfish links are restricted to the configured origin and redirects are disabled. HTTPS certificate verification is mandatory in production collection mode. The collector supports only a bounded subset of resource layouts; unsupported layouts fail explicitly. Endpoint allow-lists and customer network isolation remain deployment responsibilities.

To report an issue, use the repository's private security reporting mechanism if enabled, or contact the repository owner privately. Do not publish credentials, customer telemetry or exploit details in a public issue.

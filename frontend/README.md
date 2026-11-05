# WatcherMCP — Hardware Overwatch NOC & Agent Operations Frontend

This folder provides a live-deployable frontend dashboard for [`WatcherMCP`](https://github.com/pravj24/WatcherMCP), combining:
1. **NOC 256-Monitor Matrix & Real-Time Telemetry Charts** (`Current Status of All Monitors`, Down/Critical/Trouble/Soak counters, IST clock, and live thermal/packet charts).
2. **Fleet Node Status Cards (`watchermcp.app/dashboard`)** with per-node `CPU`, `MEM`, and `TEMP` bars and instant node selection.
3. **Server & Redfish BMC Drilldown + 8-Stage Hardware Overwatch Lifecycle** (`Observe -> Gather Receipts -> Plan -> Verify -> Approve -> Permit -> Execute -> Verify Recovery`), 6-Factor Vendor Tender scoring, Read-Only MCP Trust Zone inspector (`fleet`, `records`, `market`), and SHA-256 Audit Ledger viewer.
4. **Start-of-Day Operator Login & Shift Handover Popup** and **Critical Anomaly / Fault Incident Popup**.
5. **Live Enterprise Onboarding** (the `🚀 Onboard` rail / `Enterprise Onboarding · live` tab) — a **functional** 7-phase gated onboarding driven by the real backend API, with an in-browser simulation fallback when no backend is reachable.

This is the **single** dashboard UI for WatcherMCP; the old `overwatch/web/` desk has been removed and the Python backend is now API-only.

## Deploy the webapp

The `frontend/` directory is a self-contained static webapp (Tailwind + inline JS, no build step). Deploy it directly:

```bash
# Vercel
vercel --prod            # uses frontend/vercel.json

# Netlify
netlify deploy --prod --dir=frontend   # uses frontend/netlify.toml

# Or GitHub Pages / any static host — just serve frontend/ as the web root.
```

All the NOC matrix, fleet, lifecycle, tender, MCP-zone and audit views run from the deterministic in-browser simulation, so the deployed site works with **zero backend**.

## Live onboarding (connect to the backend)

The **Onboarding** view is functional against a running Overwatch backend:

1. Start the backend (API-only): `python -m overwatch demo --port 8080` (prints the API URL and the role-token credentials file).
2. Open the dashboard → **Onboard** → set the **API base URL** (default `http://127.0.0.1:8080`) and paste a **role token** from `data/demo-credentials.json`, then **Connect**.
3. The view loads live gate status and shows the actions your role may take — `policy_admin` starts onboarding and loads config, gate owners sign their gates, `operator`/`security_lead` run the Redfish probe, `risk_lead` starts T0 shadow mode.

The API base URL and token are stored in the browser's `localStorage`. The backend sends permissive CORS, so the deployed static site can point at any reachable Overwatch API. If the backend is unreachable, the view falls back to the in-browser onboarding simulation.

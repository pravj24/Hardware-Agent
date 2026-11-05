# WatcherMCP — Hardware Overwatch NOC & Agent Operations Frontend

This folder provides a live-deployable frontend dashboard for [`WatcherMCP`](https://github.com/pravj24/WatcherMCP), combining:
1. **NOC 256-Monitor Matrix & Real-Time Telemetry Charts** (`Current Status of All Monitors`, Down/Critical/Trouble/Soak counters, IST clock, and live thermal/packet charts).
2. **Fleet Node Status Cards (`watchermcp.app/dashboard`)** with per-node `CPU`, `MEM`, and `TEMP` bars and instant node selection.
3. **Server & Redfish BMC Drilldown + 8-Stage Hardware Overwatch Lifecycle** (`Observe -> Gather Receipts -> Plan -> Verify -> Approve -> Permit -> Execute -> Verify Recovery`), 6-Factor Vendor Tender scoring, Read-Only MCP Trust Zone inspector (`fleet`, `records`, `market`), and SHA-256 Audit Ledger viewer.
4. **Start-of-Day Operator Login & Shift Handover Popup** and **Critical Anomaly / Fault Incident Popup**.

## Live Deployment Options

### Option 1: GitHub Pages / Vercel / Netlify (Static Live Deployment)
Deploy the `frontend/` directory directly to GitHub Pages, Vercel (`vercel --prod`), or Netlify. It includes a complete deterministic in-browser simulation engine so all 256 monitors, popups, MCP zone tools, and safety gates work out of the box with zero backend setup required.

### Option 2: Built-in Python `watchermcp` Demo Server (`overwatch/web/`)
Copy `overwatch/web/index.html` into your repo's `overwatch/web/` folder and run:
```bash
python -m overwatch demo --port 8080
```

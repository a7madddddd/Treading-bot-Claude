"""Live visual dashboard for the SPX 0DTE module -- Controller's own
request (2026-11-19): a page showing the "vital signs" of each open
position (current price vs. target/stop, armed state, time
remaining), not just a technical text log.

Hosted on THIS server (the Oracle VM), not on claude.ai -- a
claude.ai-hosted page cannot reach this server's private data live;
it would only update when manually refreshed through a conversation.
This script serves the page directly from here, so the Controller's
own browser talks to the real, live data with no middleman.

Data flow: `live_loop.run_one_tick(..., status_file_path=...)` writes
a JSON snapshot (`build_status_snapshot`'s output) to a file after
every tick. This server does nothing but read that file and serve it
-- it never touches the broker, the SQLite file, or any trading
logic itself, so running it carries zero trading risk.

Stdlib only (http.server), per this project's existing dependency
policy. No external JS/CSS libraries either -- the page works even
if this machine has no outbound internet access.

Usage (on the Oracle VM, while run_spx0dte_session.py is also
running with --status-file pointing at the same path):

    PYTHONPATH=src python3.11 scripts/spx0dte_dashboard_server.py \\
        --status-file spx0dte_status.json --port 8787

Then open http://<this-server's-address>:8787/ in a browser.
Opening the port to the outside world is a network-configuration
step on the cloud provider's side (e.g. Oracle Cloud's VCN security
list), not something this script can do.
"""
from __future__ import annotations

import argparse
import json
import os
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

_PAGE = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>SPX 0DTE -- Live Vital Signs</title>
<style>
  :root { --bg:#12120f; --card:#1d1c18; --fg:#eae6da; --dim:#8a857a; --border:#332f28;
          --good:#3f9b5c; --bad:#c1473f; --accent:#d98f3c; }
  * { box-sizing: border-box; }
  body { margin:0; background:var(--bg); color:var(--fg); font-family: ui-monospace, Menlo, Consolas, monospace;
         padding: 20px; }
  h1 { font-size: 18px; margin: 0 0 4px; }
  .sub { color: var(--dim); font-size: 12px; margin-bottom: 18px; }
  .header-grid { display:flex; gap:14px; flex-wrap:wrap; margin-bottom: 20px; }
  .stat { background:var(--card); border:1px solid var(--border); border-radius:8px; padding:10px 14px; min-width:150px; }
  .stat .label { color:var(--dim); font-size:11px; text-transform:uppercase; letter-spacing:.04em; }
  .stat .value { font-size:17px; font-weight:600; margin-top:3px; }
  .loss-bar-track { height:8px; background:#332f28; border-radius:4px; margin-top:6px; overflow:hidden; }
  .loss-bar-fill { height:100%; background:var(--accent); }
  .loss-bar-fill.danger { background: var(--bad); }
  .positions { display:flex; flex-direction:column; gap:14px; }
  .card { background:var(--card); border:1px solid var(--border); border-radius:10px; padding:16px; }
  .card-top { display:flex; justify-content:space-between; align-items:baseline; margin-bottom:10px; flex-wrap:wrap; gap:8px; }
  .dir-badge { font-weight:700; padding:2px 10px; border-radius:5px; font-size:13px; }
  .dir-badge.call { background: rgba(63,155,92,.2); color: var(--good); }
  .dir-badge.put { background: rgba(193,71,63,.2); color: var(--bad); }
  .armed-badge { font-size:11px; padding:2px 8px; border-radius:999px; border:1px solid var(--border); color:var(--dim); }
  .armed-badge.armed { color: var(--good); border-color: var(--good); }
  .sym { color: var(--dim); font-size: 12px; }
  .gauge-wrap { margin: 12px 0 6px; }
  .chart-wrap { margin: 14px 0 6px; background: #15140f; border-radius: 6px; padding: 6px; }
  .chart-wrap canvas { width: 100%; height: 130px; display: block; }
  .gauge { position: relative; height: 26px; background: linear-gradient(to right, var(--bad), #444, var(--good));
           border-radius: 6px; opacity: .35; }
  .gauge-marker { position:absolute; top:-6px; width:3px; height:38px; background:#fff; border-radius:2px; }
  .gauge-marker.armline { background: var(--accent); width:2px; opacity:.8; }
  .gauge-labels { display:flex; justify-content:space-between; font-size:11px; color:var(--dim); margin-top:4px; }
  .pnl { font-size: 20px; font-weight:700; }
  .pnl.pos { color: var(--good); } .pnl.neg { color: var(--bad); }
  .meta-row { display:flex; gap:18px; font-size:12px; color:var(--dim); margin-top:10px; flex-wrap:wrap; }
  .time-bar-track { height:6px; background:#332f28; border-radius:3px; margin-top:4px; width:160px; overflow:hidden; }
  .time-bar-fill { height:100%; background: var(--dim); }
  .empty { color: var(--dim); font-style: italic; padding: 20px; text-align:center; border:1px dashed var(--border); border-radius:10px; }
  .stale { color: var(--bad); font-size: 11px; margin-top: 10px; }
  .sell-row { margin-top: 12px; }
  .sell-btn { background: var(--bad); color: #fff; border: none; border-radius: 6px; padding: 7px 14px;
              font-family: inherit; font-size: 12px; font-weight: 700; cursor: pointer; }
  .sell-btn:hover { opacity: .85; }
  .sell-confirm { margin-top: 8px; padding: 10px; background: rgba(193,71,63,.12); border: 1px solid var(--bad);
                  border-radius: 6px; font-size: 12px; }
  .sell-confirm p { margin: 0 0 8px; color: var(--fg); }
  .sell-confirm-actions { display: flex; gap: 8px; }
  .sell-confirm-yes { background: var(--bad); color:#fff; border:none; border-radius:5px; padding:6px 12px;
                       font-family: inherit; font-size:12px; font-weight:700; cursor:pointer; }
  .sell-confirm-no { background: transparent; color: var(--dim); border:1px solid var(--border); border-radius:5px;
                      padding:6px 12px; font-family: inherit; font-size:12px; cursor:pointer; }
  .sell-pending { font-size: 12px; color: var(--accent); margin-top: 12px; }
</style>
</head>
<body>
  <h1>SPX 0DTE &mdash; Live Vital Signs</h1>
  <div class="sub" id="asof">loading...</div>
  <div class="header-grid" id="header"></div>
  <div class="positions" id="positions"></div>
  <div class="stale" id="stale" style="display:none;"></div>

<script>
function fmtMoney(n) {
  if (n === null || n === undefined) return "--";
  const sign = n >= 0 ? "+" : "-";
  return sign + "$" + Math.abs(n).toFixed(2);
}
function fmtPrice(n) { return (n === null || n === undefined) ? "--" : n.toFixed(2); }

function clampPct(v, lo, hi) {
  if (v < lo) return 0;
  if (v > hi) return 100;
  return ((v - lo) / (hi - lo)) * 100;
}

function drawChart(canvasId, history, stopPrice, targetPrice, armedPrice, entryPrice) {
  const canvas = document.getElementById(canvasId);
  if (!canvas) return;
  const dpr = window.devicePixelRatio || 1;
  const cssW = canvas.clientWidth || canvas.parentElement.clientWidth;
  const cssH = 130;
  canvas.width = cssW * dpr;
  canvas.height = cssH * dpr;
  const ctx = canvas.getContext("2d");
  ctx.scale(dpr, dpr);
  ctx.clearRect(0, 0, cssW, cssH);

  if (!history || history.length < 2) {
    ctx.fillStyle = "#8a857a";
    ctx.font = "11px ui-monospace, monospace";
    ctx.textAlign = "center";
    ctx.fillText("not enough price history yet", cssW / 2, cssH / 2);
    return;
  }

  const prices = history.map(h => h[1]);
  // Y-range covers stop/target/armed AND the actual price path, so a
  // move that overshoots the gauge is still fully visible, not clipped.
  let lo = Math.min(stopPrice, targetPrice, ...prices);
  let hi = Math.max(stopPrice, targetPrice, ...prices);
  if (hi === lo) { hi += 1; lo -= 1; }
  const pad = (hi - lo) * 0.08;
  lo -= pad; hi += pad;

  const marginL = 44, marginR = 8, marginT = 6, marginB = 6;
  const plotW = cssW - marginL - marginR;
  const plotH = cssH - marginT - marginB;
  const xAt = i => marginL + (i / (history.length - 1)) * plotW;
  const yAt = v => marginT + plotH - ((v - lo) / (hi - lo)) * plotH;

  function hline(value, color, dash) {
    const y = yAt(value);
    ctx.strokeStyle = color;
    ctx.setLineDash(dash || []);
    ctx.lineWidth = 1;
    ctx.beginPath();
    ctx.moveTo(marginL, y);
    ctx.lineTo(cssW - marginR, y);
    ctx.stroke();
    ctx.setLineDash([]);
    ctx.fillStyle = color;
    ctx.font = "9px ui-monospace, monospace";
    ctx.textAlign = "left";
    ctx.fillText(value.toFixed(2), 2, y + 3);
  }
  hline(targetPrice, "#3f9b5c", [3, 3]);
  hline(stopPrice, "#c1473f", [3, 3]);
  hline(armedPrice, "#d98f3c", [2, 2]);
  hline(entryPrice, "#8a857a", [1, 3]);

  ctx.strokeStyle = "#eae6da";
  ctx.lineWidth = 1.5;
  ctx.beginPath();
  history.forEach((h, i) => {
    const x = xAt(i), y = yAt(h[1]);
    if (i === 0) ctx.moveTo(x, y); else ctx.lineTo(x, y);
  });
  ctx.stroke();

  const lastY = yAt(prices[prices.length - 1]);
  ctx.fillStyle = prices[prices.length - 1] >= entryPrice ? "#3f9b5c" : "#c1473f";
  ctx.beginPath();
  ctx.arc(xAt(history.length - 1), lastY, 3, 0, Math.PI * 2);
  ctx.fill();
}

function askSell(proposalId) {
  const row = document.getElementById(`sell-row-${proposalId}`);
  if (!row) return;
  row.innerHTML = `
    <div class="sell-confirm">
      <p>This will sell this position at the NEXT cycle (it does not execute instantly) --
         are you sure?</p>
      <div class="sell-confirm-actions">
        <button class="sell-confirm-yes" onclick="confirmSell('${proposalId}')">Yes, sell at next cycle</button>
        <button class="sell-confirm-no" onclick="cancelSell('${proposalId}')">Cancel</button>
      </div>
    </div>
  `;
}

function cancelSell(proposalId) {
  const row = document.getElementById(`sell-row-${proposalId}`);
  if (!row) return;
  row.innerHTML = `<button class="sell-btn" onclick="askSell('${proposalId}')">Sell Now</button>`;
}

async function confirmSell(proposalId) {
  const row = document.getElementById(`sell-row-${proposalId}`);
  try {
    const res = await fetch("/close/" + encodeURIComponent(proposalId), { method: "POST" });
    if (!res.ok) throw new Error("server returned " + res.status);
    if (row) row.innerHTML = '<div class="sell-pending">Sell requested &mdash; will close on the next cycle.</div>';
  } catch (e) {
    if (row) row.innerHTML = `<div class="sell-confirm"><p>Could not send the sell request: ${e}.
      Try again.</p><div class="sell-confirm-actions">
      <button class="sell-confirm-no" onclick="cancelSell('${proposalId}')">Close</button></div></div>`;
  }
}

function render(data) {
  document.getElementById("asof").textContent =
    "Date: " + (data.date || "--") + "  |  Day-pace cap: " + (data.dynamic_cap_today ?? "--") + " min" +
    "  |  Snapshot generated: " + data.generated_at;

  const ll = data.loss_limit || {};
  const limitDollars = (ll.locked_equity && ll.limit_pct) ? ll.locked_equity * ll.limit_pct : null;
  const usedPct = (limitDollars && ll.realized_pnl_today !== null)
    ? clampPct(-ll.realized_pnl_today, 0, limitDollars) : 0;

  document.getElementById("header").innerHTML = `
    <div class="stat"><div class="label">Account equity (locked today)</div>
      <div class="value">${ll.locked_equity ? "$" + ll.locked_equity.toLocaleString(undefined,{maximumFractionDigits:0}) : "--"}</div></div>
    <div class="stat"><div class="label">Daily loss limit</div>
      <div class="value">${limitDollars ? "$" + limitDollars.toFixed(0) : "--"} (${ll.limit_pct ? (ll.limit_pct*100).toFixed(1) : "--"}%)</div>
      <div class="loss-bar-track"><div class="loss-bar-fill ${ll.limit_reached ? "danger" : ""}" style="width:${usedPct}%"></div></div>
    </div>
    <div class="stat"><div class="label">Realized P&amp;L today</div>
      <div class="value" style="color:${(ll.realized_pnl_today||0) >= 0 ? "var(--good)" : "var(--bad)"}">${fmtMoney(ll.realized_pnl_today)}</div></div>
    <div class="stat"><div class="label">Proposals today</div>
      <div class="value">${data.proposals_created_today ?? "--"}</div></div>
  `;

  const container = document.getElementById("positions");
  const positions = data.open_positions || [];
  if (positions.length === 0) {
    container.innerHTML = '<div class="empty">No open positions right now.</div>';
    return;
  }

  container.innerHTML = positions.map(p => {
    const lo = p.stop_price, hi = p.target_price;
    const curPct = p.current_price !== null ? clampPct(p.current_price, lo, hi) : null;
    const armPct = clampPct(p.armed_price, lo, hi);
    const pnlClass = (p.unrealized_pnl_dollars || 0) >= 0 ? "pos" : "neg";
    const timePct = p.hold_cap_minutes > 0 ? Math.min(100, (p.elapsed_minutes / p.hold_cap_minutes) * 100) : 0;
    return `
      <div class="card">
        <div class="card-top">
          <div>
            <span class="dir-badge ${p.direction}">${p.direction.toUpperCase()}</span>
            <span class="sym">${p.occ_symbol} &middot; strike ${p.strike}</span>
          </div>
          <span class="armed-badge ${p.armed ? "armed" : ""}">${p.armed ? "ARMED" : "not armed"}</span>
        </div>
        <div class="pnl ${pnlClass}">${fmtMoney(p.unrealized_pnl_dollars)}</div>
        <div class="gauge-wrap">
          <div class="gauge">
            ${curPct !== null ? `<div class="gauge-marker" style="left:calc(${curPct}% - 1px)"></div>` : ""}
            <div class="gauge-marker armline" style="left:calc(${armPct}% - 1px)"></div>
          </div>
          <div class="gauge-labels">
            <span>stop ${fmtPrice(lo)}</span>
            <span>current ${fmtPrice(p.current_price)}</span>
            <span>target ${fmtPrice(hi)}</span>
          </div>
        </div>
        <div class="chart-wrap"><canvas id="chart-${p.proposal_id}" height="130"></canvas></div>
        <div class="meta-row">
          <span>entry ${fmtPrice(p.entry_price)} @ ${p.entry_time.slice(11,16)}Z</span>
          <span>elapsed ${p.elapsed_minutes}m / remaining ${p.remaining_minutes}m
            <div class="time-bar-track"><div class="time-bar-fill" style="width:${timePct}%"></div></div>
          </span>
        </div>
        ${p.manual_close_requested
          ? '<div class="sell-pending">Sell requested &mdash; will close on the next cycle.</div>'
          : `<div class="sell-row" id="sell-row-${p.proposal_id}">
               <button class="sell-btn" onclick="askSell('${p.proposal_id}')">Sell Now</button>
             </div>`}
      </div>
    `;
  }).join("");

  // Canvas elements must exist in the DOM before drawing -- draw after innerHTML is set.
  positions.forEach(p => {
    drawChart(`chart-${p.proposal_id}`, p.price_history || [], p.stop_price, p.target_price, p.armed_price, p.entry_price);
  });
}

async function tick() {
  try {
    const res = await fetch("/status.json", { cache: "no-store" });
    const data = await res.json();
    document.getElementById("stale").style.display = "none";
    render(data);
  } catch (e) {
    document.getElementById("stale").style.display = "block";
    document.getElementById("stale").textContent = "Could not reach the status feed: " + e;
  }
}
tick();
setInterval(tick, 5000);
</script>
</body>
</html>
"""


def make_handler(status_file_path: str, state=None):
    """`state`, when given, is the live `spx0dte.live_loop.LiveSessionState`
    object the trading session itself is using. Only `POST /close/<id>`
    touches it, and only by adding to `state.manual_close_requests` --
    this HTTP handler runs on its OWN thread and must NEVER call the
    broker or any trading logic directly; it only enqueues the request
    for `live_loop.monitor_open_positions` to act on at its next tick
    (the same "enqueue, let the next tick process it" pattern already
    used for Telegram decisions). `state=None` (e.g. the standalone
    `main()` CLI entry point, run as a separate process from the
    trading session) disables the sell button server-side: the request
    is acknowledged as queued but nothing holds it, since there is no
    live state to add it to from this process."""

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, format, *args):  # noqa: A002 -- stdlib signature
            pass  # quiet by default; the dashboard's own page is the point, not server access logs

        def do_GET(self):  # noqa: N802 -- stdlib method name
            if self.path in ("/", "/index.html"):
                body = _PAGE.encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
            elif self.path == "/status.json":
                if os.path.exists(status_file_path):
                    with open(status_file_path, "rb") as f:
                        body = f.read()
                else:
                    body = b'{"open_positions": [], "loss_limit": {}, "note": "no snapshot written yet"}'
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
            else:
                self.send_response(404)
                self.end_headers()

        def do_POST(self):  # noqa: N802 -- stdlib method name
            if self.path.startswith("/close/"):
                proposal_id = self.path[len("/close/"):]
                if not proposal_id:
                    self.send_response(400)
                    self.end_headers()
                    return
                if state is not None:
                    state.manual_close_requests.add(proposal_id)
                    payload = {"status": "queued", "proposal_id": proposal_id}
                else:
                    # No live state in this process (standalone server) -- nothing can
                    # act on the request, so say so rather than falsely confirming it.
                    payload = {"status": "no_live_session", "proposal_id": proposal_id}
                body = json.dumps(payload).encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
            else:
                self.send_response(404)
                self.end_headers()

    return Handler


def start_server_in_background(
    *, status_file_path: str, port: int = 8787, host: str = "0.0.0.0", state=None,
) -> ThreadingHTTPServer:
    """Starts the dashboard server on a daemon thread and returns the
    live `ThreadingHTTPServer` instance (call `.shutdown()` on it to
    stop, though in practice the caller's own process exiting is
    enough, since the thread is a daemon). Exists so
    `run_spx0dte_session.py` can run the dashboard INSIDE the same
    process as the trading session itself (Controller's own request,
    2026-11-19: "too hard to run two commands each time") instead of
    a second separate script/terminal. `state` (the session's own
    `LiveSessionState`) is passed through so the dashboard's "Sell Now"
    button can reach it -- see `make_handler`."""
    server = ThreadingHTTPServer((host, port), make_handler(status_file_path, state=state))
    thread = threading.Thread(target=server.serve_forever, daemon=True, name="spx0dte-dashboard")
    thread.start()
    return server


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--status-file", default="spx0dte_status.json")
    parser.add_argument("--port", type=int, default=8787)
    parser.add_argument("--host", default="0.0.0.0")
    args = parser.parse_args()

    server = ThreadingHTTPServer((args.host, args.port), make_handler(args.status_file))
    print(f"SPX 0DTE dashboard serving on http://{args.host}:{args.port}/ (reading {args.status_file})")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()

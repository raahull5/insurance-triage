"""Enterprise Dashboard Server using Python standard HTTP server."""

import http.server
import json
import logging
import socketserver
import sqlite3
import threading
import urllib.parse
from typing import Any, Dict, Optional

from src.db.database import Database
from src.dashboard.data_service import DashboardDataService

HTML_DASHBOARD = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Apex Shield — Insurance Support &amp; Claims Triage</title>
<link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&display=swap" rel="stylesheet">
<style>
:root{
  --bg:#F8FAFC; --surface:#FFFFFF; --border:#E2E8F0;
  --text-primary:#0F172A; --text-secondary:#64748B; --navy:#1E293B;
  --blue:#2563EB; --blue-light:#EFF6FF; --red:#DC2626; --red-light:#FEF2F2;
  --amber:#D97706; --amber-light:#FFFBEB; --green:#16A34A; --green-light:#F0FDF4;
}
*{box-sizing:border-box;margin:0;padding:0;font-family:'Inter',sans-serif}
body{background:var(--bg);color:var(--text-primary);padding:24px}
.header{display:flex;justify-content:space-between;align-items:center;margin-bottom:20px;flex-wrap:wrap;gap:12px}
.header h1{font-size:20px;font-weight:700;color:var(--navy)}
.badge-live{background:#DCFCE7;color:#166534;font-size:12px;font-weight:600;padding:3px 8px;border-radius:4px;margin-left:6px}
.status{font-size:12px;color:var(--text-secondary);margin-right:10px}
.kpis{display:grid;grid-template-columns:repeat(auto-fit,minmax(160px,1fr));gap:14px;margin-bottom:18px}
.kpi-card{background:var(--surface);border:1px solid var(--border);border-radius:6px;padding:14px}
.kpi-label{font-size:12px;color:var(--text-secondary);margin-bottom:6px}
.kpi-val{font-size:24px;font-weight:700}
.charts{display:grid;grid-template-columns:repeat(auto-fit,minmax(260px,1fr));gap:14px;margin-bottom:18px}
.chart-card{background:var(--surface);border:1px solid var(--border);border-radius:6px;padding:14px}
.chart-title{font-size:13px;font-weight:600;margin-bottom:10px}
.bar-row{display:flex;align-items:center;gap:8px;margin-bottom:6px;font-size:12px}
.bar-label{width:118px;color:var(--text-secondary);white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.bar-track{flex:1;background:#F1F5F9;border-radius:3px;height:14px;overflow:hidden}
.bar-fill{height:100%;background:var(--blue);border-radius:3px}
.bar-count{width:32px;text-align:right;font-weight:600}
.controls{display:flex;gap:10px;margin-bottom:14px;flex-wrap:wrap}
.input-search{flex:1;min-width:220px;padding:8px 12px;border:1px solid var(--border);border-radius:4px;font-size:13px;background:#FFF;outline:none}
.select-filter{padding:8px 10px;border:1px solid var(--border);border-radius:4px;font-size:13px;background:#FFF}
.btn-refresh{padding:8px 14px;background:var(--navy);color:#FFF;border:none;border-radius:4px;font-size:13px;font-weight:600;cursor:pointer}
.table-container{background:var(--surface);border:1px solid var(--border);border-radius:6px;overflow:auto;max-height:60vh}
table{width:100%;border-collapse:collapse;text-align:left;font-size:13px}
th{background:#F1F5F9;color:var(--text-secondary);padding:10px 14px;font-weight:600;position:sticky;top:0;z-index:2}
td{padding:10px 14px;border-bottom:1px solid var(--border);vertical-align:middle}
tr:hover td{background:#F8FAFC}
.tag{display:inline-block;padding:2px 6px;border-radius:4px;font-size:11px;font-weight:600}
.tag-critical{background:var(--red-light);color:var(--red)}
.tag-high{background:var(--amber-light);color:var(--amber)}
.tag-medium{background:var(--blue-light);color:var(--blue)}
.tag-low{background:#F1F5F9;color:#475569}
.tag-sent{background:var(--green-light);color:var(--green)}
.tag-escalated{background:#FEF3C7;color:#92400E}
.drawer{position:fixed;right:-460px;top:0;width:460px;height:100vh;background:var(--surface);border-left:1px solid var(--border);box-shadow:-4px 0 16px rgba(0,0,0,.06);padding:24px;transition:right .25s ease;overflow-y:auto;z-index:100}
.drawer.open{right:0}
.drawer-header{display:flex;justify-content:space-between;align-items:center;margin-bottom:18px;padding-bottom:12px;border-bottom:1px solid var(--border)}
.btn-close{background:none;border:none;font-size:18px;cursor:pointer;color:var(--text-secondary)}
.detail-row{margin-bottom:14px;font-size:13px}
.detail-row label{display:block;font-size:11px;font-weight:600;color:var(--text-secondary);text-transform:uppercase;margin-bottom:4px}
.reply-box{background:#F8FAFC;border:1px solid var(--border);border-radius:4px;padding:12px;font-family:ui-monospace,monospace;font-size:12px;white-space:pre-wrap;margin-top:6px}
.empty{text-align:center;color:var(--text-secondary);padding:20px}
</style>
</head>
<body>
<div class="header">
  <h1>Apex Shield Operations &middot; Support &amp; Claims Triage <span class="badge-live">LIVE</span></h1>
  <div>
    <span class="status" id="last-updated">Loading&hellip;</span>
    <button class="btn-refresh" onclick="fetchData()">Refresh</button>
  </div>
</div>

<div class="kpis" id="kpi-grid"></div>
<div class="charts" id="charts"></div>

<div class="controls">
  <input type="text" id="search-input" class="input-search" placeholder="Search sender, subject, intent, summary...">
  <select id="priority-filter" class="select-filter">
    <option value="">All Priorities</option><option>Critical</option><option>High</option><option>Medium</option><option>Low</option>
  </select>
  <select id="escalation-filter" class="select-filter">
    <option value="">All Tickets</option><option value="yes">Escalated</option><option value="no">Not Escalated</option>
  </select>
</div>

<div class="table-container">
  <table>
    <thead><tr><th>ID</th><th>Priority</th><th>Score</th><th>Intent</th><th>Sender</th><th>Subject</th><th>Escalation</th><th>Routing Desk</th><th>Status</th></tr></thead>
    <tbody id="queue-body"><tr><td colspan="9" class="empty">Loading triage records&hellip;</td></tr></tbody>
  </table>
</div>

<div class="drawer" id="detail-drawer">
  <div class="drawer-header"><h3 id="d-title">Record Details</h3><button class="btn-close" onclick="closeDrawer()">&times;</button></div>
  <div class="detail-row"><label>Customer / Sender</label><div id="d-sender"></div></div>
  <div class="detail-row"><label>Subject</label><div id="d-subject"></div></div>
  <div class="detail-row"><label>Intent &amp; Classification</label><div id="d-intent"></div></div>
  <div class="detail-row"><label>Policy &amp; Claim Link</label><div id="d-pol-claim"></div></div>
  <div class="detail-row"><label>Summary</label><div id="d-summary"></div></div>
  <div class="detail-row"><label>Generated Reply</label><div class="reply-box" id="d-reply"></div></div>
</div>

<script>
function esc(s){return String(s==null?'':s).replace(/[&<>"']/g,function(c){
  return {'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c];})}

async function fetchData(){
  try{
    var q = new URLSearchParams({
      search: document.getElementById('search-input').value,
      priority: document.getElementById('priority-filter').value,
      escalation: document.getElementById('escalation-filter').value
    });
    var res = await fetch('/api/data?' + q.toString());
    var data = await res.json();
    renderKPIs(data.kpis);
    renderCharts(data.distributions);
    renderQueue(data.queue);
    document.getElementById('last-updated').textContent = 'Updated ' + new Date().toLocaleTimeString();
  }catch(e){
    document.getElementById('last-updated').textContent = 'Connection error';
    console.error(e);
  }
}

var KPI_LABEL = {
  total_inquiries:'Total Inquiries', critical_emergencies:'Critical &amp; Emergencies',
  human_escalations:'Human Escalations', auto_replied:'Auto-Replied', suppressed:'Suppressed', replies_held:'Replies Held', reply_disabled:'Reply Disabled',
  unique_senders:'Unique Senders', avg_urgency_score:'Avg Urgency Score', open_tickets:'Open Tickets',
  automation_rate:'Automation Rate %'
};
var KPI_COLOR = {
  critical_emergencies:'var(--red)', human_escalations:'var(--amber)', auto_replied:'var(--green)',
  suppressed:'var(--text-secondary)', automation_rate:'var(--green)'
};

function renderKPIs(kpis){
  var html = Object.keys(KPI_LABEL).map(function(k){
    var v = kpis[k];
    if (v === null || v === undefined) v = 0;
    if (k === 'automation_rate') v = v + '%';
    return '<div class="kpi-card"><div class="kpi-label">' + KPI_LABEL[k] +
           '</div><div class="kpi-val" style="color:' + (KPI_COLOR[k] || 'var(--text-primary)') + '">' +
           esc(v) + '</div></div>';
  }).join('');
  document.getElementById('kpi-grid').innerHTML = html;
}

var BAR_COLORS = {Critical:'var(--red)',High:'var(--amber)',Medium:'var(--blue)',Low:'#94A3B8',
  Positive:'var(--green)',Neutral:'#94A3B8',Frustrated:'var(--amber)',Angry:'var(--red)'};
var CHART_TITLES = {category:'Intents by Category', priority:'Priority Mix', sentiment:'Customer Sentiment'};

function renderCharts(dist){
  document.getElementById('charts').innerHTML = Object.keys(CHART_TITLES).map(function(key){
    var rows = (dist && dist[key]) || [];
    if (!rows.length) return '<div class="chart-card"><div class="chart-title">' + CHART_TITLES[key] + '</div><div class="empty">No data yet</div></div>';
    var max = rows.reduce(function(a,r){return Math.max(a,r.count);},0) || 1;
    var body = rows.map(function(r){
      return '<div class="bar-row"><div class="bar-label" title="' + esc(r.label) + '">' + esc(r.label) +
        '</div><div class="bar-track"><div class="bar-fill" style="width:' + (100*r.count/max).toFixed(1) + '%;background:' +
        (BAR_COLORS[r.label] || 'var(--blue)') + '"></div></div><div class="bar-count">' + r.count + '</div></div>';
    }).join('');
    return '<div class="chart-card"><div class="chart-title">' + CHART_TITLES[key] + '</div>' + body + '</div>';
  }).join('');
}

function renderQueue(records){
  var tbody = document.getElementById('queue-body');
  if (!records.length){ tbody.innerHTML = '<tr><td colspan="9" class="empty">No matching triage records found.</td></tr>'; return; }
  tbody.innerHTML = records.map(function(r){
    var sender = r.sender_name
      ? esc(r.sender_name) + '<br><small style="color:var(--text-secondary)">' + esc(r.sender_email) + '</small>'
      : esc(r.sender_email);
    return '<tr onclick="openDetail(' + r.id + ')">' +
      '<td>#' + r.id + '</td>' +
      '<td><span class="tag tag-' + String(r.priority || 'low').toLowerCase() + '">' + esc(r.priority) + '</span></td>' +
      '<td><b>' + esc(r.urgency_score) + '/10</b></td>' +
      '<td><b>' + esc(r.intent) + '</b></td>' +
      '<td>' + sender + '</td>' +
      '<td>' + esc(r.subject) + '</td>' +
      '<td>' + (r.escalation_needed ? '<span class="tag tag-escalated">Escalated</span>' : '<span style="color:var(--text-secondary)">Standard</span>') + '</td>' +
      '<td>' + esc(r.routing_desk) + '</td>' +
      '<td><span class="tag ' + (String(r.reply_status || '').toUpperCase() === 'SENT' ? 'tag-sent' : 'tag-low') + '">' + esc(r.reply_status || '—') + '</span></td>' +
      '</tr>';
  }).join('');
}

async function openDetail(id){
  try{
    var res = await fetch('/api/record/' + id);
    if (!res.ok) return;
    var rec = await res.json();
    document.getElementById('d-title').innerText = 'Record #' + rec.id + ' — ' + rec.intent;
    document.getElementById('d-sender').innerHTML = esc(rec.sender_name || 'Unidentified') + ' &lt;' + esc(rec.sender_email) + '&gt;';
    document.getElementById('d-subject').innerText = rec.subject || '(No Subject)';
    document.getElementById('d-intent').innerHTML = '<b>' + esc(rec.category) + '</b> → ' + esc(rec.intent) +
      ' (Priority: ' + esc(rec.priority) + ', Sentiment: ' + esc(rec.sentiment) + ')';
    document.getElementById('d-pol-claim').innerText = 'Policy: ' + (rec.policy_number || 'N/A') + ' | Claim: ' + (rec.claim_number || 'N/A');
    document.getElementById('d-summary').innerText = rec.summary || 'N/A';
    document.getElementById('d-reply').innerText = rec.suggested_reply || '(No reply generated)';
    document.getElementById('detail-drawer').classList.add('open');
  }catch(e){ console.error(e); }
}

function closeDrawer(){ document.getElementById('detail-drawer').classList.remove('open'); }

['search-input','priority-filter','escalation-filter'].forEach(function(id){
  document.getElementById(id).addEventListener('input', fetchData);
});

fetchData();
setInterval(fetchData, 10000);
</script>
</body>
</html>"""


class DashboardRequestHandler(http.server.SimpleHTTPRequestHandler):
    database: Optional[Database] = None

    def log_message(self, fmt, *args):  # noqa: A002 - stdlib signature
        """Silence default stderr access logging."""
        return

    def _send_json(self, payload: Dict[str, Any], status: int = 200) -> None:
        body = json.dumps(payload, default=str).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)
        params = urllib.parse.parse_qs(parsed.query)
        path = parsed.path

        if path in ("/", "/index.html"):
            body = HTML_DASHBOARD.encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return

        if not self.database:
            self._send_json({"error": "Database not configured"}, 500)
            return

        def first(key: str, default: str = "") -> str:
            return (params.get(key) or [default])[0]

        try:
            db = self.database
            raw = db.get_connection()
            # Accept either a raw sqlite3.Connection or a context manager yielding one.
            if hasattr(raw, "__enter__") and not isinstance(raw, sqlite3.Connection):
                raw = raw.__enter__()
            conn = raw
            try:
                service = DashboardDataService(conn)

                if path == "/api/data":
                    self._send_json(
                        service.get_dashboard_payload(
                            search=first("search"),
                            priority=first("priority"),
                            limit=int(first("limit", "200") or 200),
                        )
                    )
                    return

                if path.startswith("/api/record/"):
                    try:
                        record_id = int(path.rsplit("/", 1)[-1])
                    except ValueError:
                        self._send_json({"error": "Invalid record id"}, 400)
                        return
                    record = service.get_record(record_id)
                    if not record:
                        self._send_json({"error": "Record not found"}, 404)
                        return
                    self._send_json(record)
                    return

                if path == "/api/kpis":
                    self._send_json({"kpis": service.get_kpi_metrics()})
                    return

                if path == "/api/distribution":
                    field = first("field", "category")
                    try:
                        self._send_json({"field": field, "items": service.get_distribution(field)})
                    except ValueError as exc:
                        self._send_json({"error": str(exc)}, 400)
                    return

                if path == "/api/tickets":
                    self._send_json({"tickets": service.get_ticket_summary(int(first("limit", "25") or 25))})
                    return

                if path == "/api/health":
                    self._send_json({"status": "ok", "database": "connected"})
                    return
            finally:
                try:
                    conn.close()
                except Exception:  # pragma: no cover - best effort
                    pass
        except Exception as exc:  # pragma: no cover - defensive
            self._send_json({"error": str(exc)}, 500)
            return

        self.send_error(404, "Not Found")


def start_dashboard_server(db: Database, host: str = "127.0.0.1", port: int = 8080) -> socketserver.TCPServer:
    """Start the dashboard HTTP server in a background daemon thread."""
    DashboardRequestHandler.database = db
    socketserver.TCPServer.allow_reuse_address = True
    server = socketserver.TCPServer((host, port), DashboardRequestHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    logging.getLogger(__name__).info("Dashboard running at http://%s:%s", host, port)
    return server

/* Small dependency-free page logic. All numbers come from assets/data.js, which
   scripts/render_site.py generates from evidence/benchmark/raw.json. */
(function () {
  "use strict";
  var data = window.CP_BENCHMARK;
  var host = document.getElementById("stats");
  if (!host) return;
  if (!data || !data.conditions) {
    host.innerHTML =
      '<p class="small">Benchmark data has not been generated. Run ' +
      "<code>python3 scripts/benchmark.py</code> then <code>python3 scripts/render_site.py</code>.</p>";
    return;
  }
  function esc(value) {
    return String(value).replace(/[&<>"']/g, function (ch) {
      return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[ch];
    });
  }
  var labels = {
    direct_command_baseline: "Direct command (baseline)",
    engine_verify_observe_no_store: "Observe (engine, no ledger write)",
    engine_verify_enabled: "Engine verify (hashing + receipt)",
    engine_status_fresh: "Status read (fresh receipt)",
    engine_pre_verify_stale_gate: "pre_verify gate (stale)"
  };
  var html = "";
  data.conditions.forEach(function (row) {
    html +=
      '<div class="stat"><div class="label">' +
      esc(labels[row.condition] || row.condition) +
      '</div><div class="value">' +
      esc(row.wall_p50_ms) +
      ' ms p50</div><div class="detail">p95 ' +
      esc(row.wall_p95_ms) +
      " ms · n=" +
      esc(row.count) +
      (row.python_peak_p50_kb != null ? " · py alloc " + esc(row.python_peak_p50_kb) + " KiB" : "") +
      (row.rss_peak_p50_kb != null ? " · RSS " + esc(row.rss_peak_p50_kb) + " KiB" : "") +
      (row.total_cpu_p50_ms != null ? " · CPU " + esc(row.total_cpu_p50_ms) + " ms" : "") +
      "</div></div>";
  });
  var env = data.environment || {};
  html +=
    '<div class="stat"><div class="label">Measurement scope</div><div class="value">0 LLM</div>' +
    '<div class="detail">' +
    esc(env.measurement_scope || "deterministic engine only") +
    " · network attempts observed: " +
    esc(env.network_attempts_observed != null ? env.network_attempts_observed : "n/a") +
    "</div></div>";
  host.innerHTML = html;
})();

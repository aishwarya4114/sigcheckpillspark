async function loadScorecard() {
  const el = document.querySelector("#scorecard");
  try {
    const s = await api("/api/scorecard");
    if (s.error) throw Error(s.error);
    const pct = (a, b) => (b ? Math.round((100 * a) / b) + "%" : "n/a");
    const metrics = [
      [
        "Planted errors caught",
        `${s.traps_caught.caught} / ${s.traps_caught.of}`,
        "Known challenges detected",
      ],
      [
        "Precision of passed fields",
        pct(s.verified_precision.right, s.verified_precision.of),
        `${s.verified_precision.right} correct of ${s.verified_precision.of} passed fields`,
      ],
      [
        "Invented absent values",
        s.invented_values_on_absent_fields,
        "Values absent from the source",
      ],
      [
        "Exact field accuracy",
        pct(s.field_accuracy.exact, s.field_accuracy.of),
        `${s.field_accuracy.exact} correct of ${s.field_accuracy.of} fields`,
      ],
    ];
    el.innerHTML = `<div class="metric-grid">${metrics.map(([l, v, f]) => `<div class="card metric"><div class="metric-label">${esc(l)}</div><div class="metric-value">${esc(v)}</div><div class="metric-foot">${esc(f)}</div></div>`).join("")}</div><div class="run-meta"><span><strong>Reader:</strong> ${esc(s.reader)}</span><span><strong>Run:</strong> ${esc(new Date(s.run_at).toLocaleString())}</span><span><strong>Documents:</strong> ${esc(s.faxes)}</span></div><section class="card results-table"><div class="table-title"><h2>Case-by-case results</h2><span class="muted small">${s.rows.length} prescriptions</span></div><table><thead><tr><th scope="col">Test prescription</th><th scope="col">Challenge</th><th scope="col">Workflow status</th><th scope="col">Review flag</th></tr></thead><tbody>${s.rows
      .map(
        (r, i) =>
          `<tr><td><span class="mono muted">${String(i + 1).padStart(2, "0")}</span> &nbsp; ${esc(
            r.fax
              .replace(/^fax_\d+_/, "")
              .replace(/\.png$/, "")
              .replaceAll("_", " "),
          )}</td><td>${esc(r.trap)}</td><td>${badge(r.status)}</td><td><span class="badge ${r.flagged ? "amber" : ""}">${r.flagged ? "Flagged" : "No flag"}</span></td></tr>`,
      )
      .join(
        "",
      )}</tbody></table></section><p class="small muted" style="margin-top:18px">A review flag describes routing to a person. It does not mean the prescription is incorrect.</p>`;
  } catch (e) {
    el.innerHTML = `<div class="card empty" style="margin-top:24px">${icon("chart")}<h2>Results unavailable</h2><p>${esc(e.message)}</p><button id="retry-score">Try again</button></div>`;
    document.querySelector("#retry-score").onclick = loadScorecard;
  }
}
loadScorecard();

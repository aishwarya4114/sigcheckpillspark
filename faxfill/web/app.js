// SigCheck review UI. Reads verdicts from the server; never runs models.
const $ = (s, el = document) => el.querySelector(s);
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
let CFG = { pass_label: "Matches fax, checks passed" };

const FIELD_ORDER = ["patient_name", "patient_dob", "drug_name", "strength", "dosage_form", "directions", "quantity",
  "refills", "prescriber_name", "prescriber_npi", "prescriber_dea", "date_written", "substitution", "notes"];
const FIELD_LABEL = { patient_name: "Patient", patient_dob: "Date of birth", drug_name: "Drug", strength: "Strength",
  dosage_form: "Form", directions: "Directions (Sig)", quantity: "Quantity", refills: "Refills",
  prescriber_name: "Prescriber", prescriber_npi: "NPI", prescriber_dea: "DEA", date_written: "Date written",
  substitution: "Substitution", notes: "Notes" };
const CHECK_LABEL = { ocr_text_agrees: "OCR text agrees", patient_in_profiles: "patient profile found",
  dob_matches_profile: "DOB matches profile", dob_valid_date: "valid date", drug_in_rxnorm: "drug in RxNorm",
  strength_in_rxnorm: "strength in RxNorm", form_in_rxnorm: "form in RxNorm", directions_parsed: "directions understood",
  "within_usual_max (manual, unverified)": "within usual max dose (manual, unverified)",
  numeral_matches_words: "number matches words", quantity_matches_duration: "quantity matches duration",
  refills_whole_number: "whole number", prescriber_matches_npi: "name matches NPI in directory",
  prescriber_in_directory: "in prescriber directory", npi_luhn: "NPI check digit valid", npi_in_directory: "NPI in directory",
  dea_checksum: "DEA check digit valid", date_valid_not_future: "valid, not in the future", substitution_known_value: "box reading recognised" };

function statusPill(kind, text) {
  const map = {
    READY: ["✓", "Ready"], NEEDS_PHARMACIST: ["⚠", "Needs pharmacist"], BLOCKED: ["⛔", "Blocked"],
    verified: ["✓", CFG.pass_label], needs_pharmacist: ["⚠", "Needs pharmacist"], not_present: ["–", "Not on fax"],
    info: ["ℹ", "Note"], resolved: ["✓", "Resolved by pharmacist"],
  };
  const [ic, label] = map[kind] || ["", kind];
  return `<span class="pill s-${kind}"><span class="ic" aria-hidden="true">${ic}</span>${esc(text || label)}</span>`;
}

async function api(path, opts) {
  const r = await fetch(path, opts);
  const body = await r.json().catch(() => ({}));
  if (!r.ok) throw new Error(body.detail || r.statusText);
  return body;
}

// ---------------------------------------------------------------- queue
async function showQueue() {
  const rows = await api("/api/queue");
  $("#app").innerHTML = `
    <div class="qhead"><div><h1>Incoming faxes</h1>
      <div class="muted">Sorted by danger score, most dangerous first. Arrival time does not change the order.</div></div>
      <div class="muted">${rows.length} faxes</div></div>
    <table class="queue"><thead><tr><th>Status</th><th>Fax</th><th>Patient</th><th>Drug</th><th>Top reason</th><th>Flags</th><th>Danger</th></tr></thead>
    <tbody>${rows.map((r) => `<tr tabindex="0" data-id="${r.fax_id}">
      <td>${statusPill(r.status)}</td><td>${esc(r.fax_id)}${r.pages > 1 ? ` <span class="muted">(${r.pages} pages)</span>` : ""}</td>
      <td>${esc(r.patient || "—")}</td><td>${esc(r.drug || "—")}</td>
      <td class="reason">${esc(r.top_reason || "All fields match the fax and passed their checks.")}</td>
      <td class="num">${r.flags}</td><td class="num">${r.danger}</td></tr>`).join("")}</tbody></table>`;
  document.querySelectorAll(".queue tbody tr").forEach((tr) => {
    const go = () => (location.hash = `#/fax/${tr.dataset.id}`);
    tr.addEventListener("click", go);
    tr.addEventListener("keydown", (e) => e.key === "Enter" && go());
  });
}

// ---------------------------------------------------------------- review
const S = { data: null, page: 1, selected: null, drafts: {} };

async function showFax(id) {
  S.data = await api(`/api/fax/${id}`);
  S.page = 1; S.selected = null; S.drafts = {};
  renderFax();
}

function blockBanner(v) {
  const blocks = v.order_flags.filter((f) => f.status === "blocked");
  if (!blocks.length) return "";
  return blocks.map((f) => {
    let detail = esc(f.reason);
    if (f.code === "INJECTION" && f.evidence && f.evidence.length) {
      detail = `Text on the fax addressed to an AI: <q>${esc(f.evidence[0].text)}</q> (page ${f.evidence[0].page}). Instructions on a fax are never followed.`;
    }
    return `<div class="banner blocked" role="alert"><strong>⛔ Blocked — ${f.code === "INJECTION" ? "instructions found on the fax" : f.code === "PAGE_COUNT_MISMATCH" ? "pages missing" : esc(f.code)}</strong>${detail}
      <div>This order cannot be submitted from this screen. There is no override.</div></div>`;
  }).join("");
}

function itemsState() {
  const items = S.data.items, res = S.data.resolutions;
  return { total: items.length, done: items.filter((i) => res[i.key]).length };
}

function renderFax() {
  const { verdict: v, pages } = S.data;
  const blocked = v.order_status === "BLOCKED";
  const st = itemsState();
  const canSubmit = !blocked && st.done === st.total;
  const orderWarn = v.order_flags.filter((f) => f.status !== "blocked");
  $("#app").innerHTML = `
    <div class="rhead">
      <div><a href="#/queue">← Queue</a><h1>${esc(v.fax_id)} ${statusPill(v.order_status)}</h1>
        <div class="muted">Danger score ${v.danger_score} · ${v.pages_received} page(s) received${v.pages_declared_in_header ? ` · header says ${v.pages_declared_in_header}` : ""}</div></div>
      <div class="actions">
        <span class="muted">${blocked ? "Submit disabled: order blocked" : `${st.done} of ${st.total} flags resolved`}</span>
        <button id="clar">Clarification fax</button>
        <button id="submit" class="primary" ${canSubmit ? "" : "disabled"} aria-disabled="${!canSubmit}">Submit order</button>
      </div>
    </div>
    ${blockBanner(v)}
    ${orderWarn.map((f) => orderFlagCard(f, blocked)).join("")}
    ${prismPanel(v)}
    <div id="submit-msg"></div>
    <div class="split">
      <section class="faxpane" aria-label="Fax image">
        <div class="tabs" role="tablist">${pages.map((p) => `<button role="tab" data-page="${p.page}" aria-selected="${p.page === S.page}">Page ${p.page}</button>`).join("")}
          <span class="muted" style="margin-left:auto">Click a field to see where it was read</span></div>
        <div class="faxwrap" id="faxwrap"><img id="faximg" alt="Fax page ${S.page}" src="${pages[S.page - 1].url}"><div id="evbox" class="evbox hidden"></div></div>
        <div class="locnote" id="locnote"></div>
      </section>
      <section class="fields" aria-label="Fields">
        ${v.prescriptions.map((rx) => `${v.prescriptions.length > 1 ? `<div class="page-label">Page ${rx.page} prescription</div>` : ""}
          ${FIELD_ORDER.map((name) => fieldCard(rx, name, blocked)).join("")}`).join("")}
      </section>
    </div>`;
  document.querySelectorAll(".tabs button").forEach((b) => b.addEventListener("click", () => { S.page = +b.dataset.page; S.selected = null; renderFax(); }));
  document.querySelectorAll(".field").forEach((el) => el.addEventListener("click", (e) => {
    if (e.target.closest(".resolve")) return;
    selectField(el.dataset.key);
  }));
  wireResolvers();
  $("#clar").addEventListener("click", openClarification);
  $("#submit").addEventListener("click", submitOrder);
  if (S.selected) drawBox(S.selected);
}

function prismPanel(v) {
  const p = v.prism || {};
  if (!p.traces_sent) return `<details class="card prism"><summary><b>PRISM</b> <span class="muted">not configured</span></summary></details>`;
  const g = p.guardrail || {};
  const ls = v.lookup_sources || {};
  const row = (k, val) => `<tr><th>${k}</th><td>${val}</td></tr>`;
  const code = (x) => `<code>${esc(x)}</code>`;
  return `<details class="card prism"><summary><b>PRISM (Block Convey)</b>
      <span class="muted">· ${p.traces_sent} traces sent for this fax${p.traces_failed && p.traces_failed.length ? `, ${p.traces_failed.length} failed` : ", 0 failed"}
      · guardrail: local ${g.local_injection_check ? "⛔ injection found" : "✓ clean"}, PRISM ${g.prism === null || g.prism === undefined ? "flag in dashboard only" : g.prism ? "⛔ flagged" : "✓ clean"}</span></summary>
    <table class="kv">
      ${row("Input guardrail", `Local check: <b>${g.local_injection_check ? "injection found → order blocked" : "no injected instructions"}</b>.<br>PRISM: OCR text sent as ${(p.intake_trace_ids || []).map(code).join(", ")} (agent <code>sigcheck-intake</code>). ${esc(g.prism_status || "")}`)}
      ${row("Reader traces", (p.reader_trace_ids || []).map(code).join("<br>") + ` <span class="muted">(agent sigcheck-reader)</span>`)}
      ${row("Verifier trace", `${code(p.verifier_trace_id || "—")} <span class="muted">(agent sigcheck-verifier; scored by the Evaluators Hub rubric)</span>`)}
      ${row("Knowledge Base", `${esc(p.knowledge_base || "")}<br><span class="muted">Drug, prescriber and patient lookups answered by: ${esc(ls.drug || "?")}</span>`)}
    </table></details>`;
}

function orderFlagCard(f, blocked) {
  const key = `order:${f.code}`;
  const r = S.data.resolutions[key];
  return `<div class="banner warn"><strong>⚠ ${esc(f.code.replaceAll("_", " ").toLowerCase())}</strong>${esc(f.reason)}
    ${r ? `<div class="resolved-note">Resolved: ${esc(r.action === "call_prescriber" ? "call prescriber" : "confirmed by pharmacist")} — “${esc(r.reason)}”</div>`
      : blocked ? "" : resolverHTML(key, [], true)}</div>`;
}

function fieldCard(rx, name, blocked) {
  const f = rx.fields[name];
  const key = `p${rx.page}:${name}`;
  const res = S.data.resolutions[key];
  const flagged = f.codes.length > 0;
  const lasaNote = f.notes.find((n) => n.startsWith("Look-alike partner"));
  const lasaPartners = lasaNote ? lasaNote.replace(/^Look-alike partner\(s\): /, "").split(".")[0]
    : f.codes.includes("LOOKALIKE_RISK") ? f.alternatives.join(", ") : null;
  const ocrNotes = f.notes.filter((n) => !n.startsWith("Look-alike partner"));
  const status = res ? "resolved" : f.status;
  const checks = f.checks_passed.map((c) => CHECK_LABEL[c.check] || c.check.replaceAll("_", " "));
  return `<article class="field ${flagged ? (res ? "done" : "flagged") : ""} ${S.selected === key ? "selected" : ""}" data-key="${key}" data-page="${rx.page}" tabindex="0">
    <div class="frow"><div><div class="fname">${FIELD_LABEL[name] || name}</div>
      <div class="fval ${f.value == null ? "empty" : ""}">${f.value == null ? (flagged ? "Not taken — see reason" : "Not on fax") : esc(f.value)}</div></div>
      ${statusPill(status)}</div>
    ${lasaPartners ? `<div class="badge">Look-alike: ${esc(lasaPartners)}</div>` : ""}
    ${flagged ? `<ul class="reason">${f.reasons.map((r) => `<li>${esc(r)}</li>`).join("")}</ul>` : ""}
    ${checks.length ? `<div class="checks">✓ ${checks.map(esc).join(" · ")}</div>` : ""}
    ${ocrNotes.map((n) => `<div class="ocrnote">${esc(n)}</div>`).join("")}
    ${flagged ? `<div class="reads">Reader A: ${esc(JSON.stringify(f.reads.A))} · Reader B: ${esc(JSON.stringify(f.reads.B))} · OCR: ${esc(JSON.stringify(f.reads.C))}</div>` : ""}
    ${flagged && res ? `<div class="resolved-note">Resolved: <b>${esc(res.action === "call_prescriber" ? "Call prescriber" : res.chosen_value)}</b> — “${esc(res.reason)}” <span class="muted">(${esc(res.time)})</span></div>` : ""}
    ${flagged && !res && !blocked ? resolverHTML(key, f.alternatives, false) : ""}
  </article>`;
}

function resolverHTML(key, alts, orderLevel) {
  const d = S.drafts[key] || {};
  return `<div class="resolve" data-key="${key}">
    <div class="alts">${orderLevel ? `<button data-act="confirm" class="${d.action === "confirm_order_flag" ? "chosen" : ""}">Confirmed by pharmacist</button>`
      : alts.map((a) => `<button data-alt="${esc(a)}" class="${d.action === "alternative" && d.chosen_value === a ? "chosen" : ""}">${esc(a)}</button>`).join("")}
      <button data-act="call" class="${d.action === "call_prescriber" ? "chosen" : ""}">☎ Call prescriber</button></div>
    ${orderLevel ? "" : `<input type="text" data-typed placeholder="Or type the correct value" value="${esc(d.action === "typed" ? d.chosen_value : "")}">`}
    <div class="row"><input type="text" data-reason placeholder="Reason (required), e.g. confirmed with prescriber's office" value="${esc(d.reason || "")}">
      <button class="primary" data-save>Save</button></div>
    <div class="err" data-err></div></div>`;
}

function wireResolvers() {
  document.querySelectorAll(".resolve").forEach((box) => {
    const key = box.dataset.key;
    const d = (S.drafts[key] = S.drafts[key] || {});
    box.querySelectorAll("[data-alt]").forEach((b) => b.addEventListener("click", () => { Object.assign(d, { action: "alternative", chosen_value: b.dataset.alt }); keepAndRender(box, d); }));
    const call = box.querySelector("[data-act=call]");
    call && call.addEventListener("click", () => { Object.assign(d, { action: "call_prescriber", chosen_value: null }); keepAndRender(box, d); });
    const conf = box.querySelector("[data-act=confirm]");
    conf && conf.addEventListener("click", () => { Object.assign(d, { action: "confirm_order_flag", chosen_value: null }); keepAndRender(box, d); });
    const typed = box.querySelector("[data-typed]");
    typed && typed.addEventListener("input", () => { if (typed.value.trim()) Object.assign(d, { action: "typed", chosen_value: typed.value }); });
    box.querySelector("[data-reason]").addEventListener("input", (e) => (d.reason = e.target.value));
    box.querySelector("[data-save]").addEventListener("click", async () => {
      d.reason = box.querySelector("[data-reason]").value;
      try {
        if (!d.action) throw new Error("Pick an option, type a value, or choose Call prescriber.");
        const rec = await api(`/api/resolve/${S.data.verdict.fax_id}`, { method: "POST", headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ key, action: d.action, chosen_value: d.chosen_value, reason: d.reason }) });
        S.data.resolutions[key] = rec;
        delete S.drafts[key];
        renderFax();
      } catch (e) { box.querySelector("[data-err]").textContent = e.message; }
    });
  });
}

function keepAndRender(box, d) {
  d.reason = box.querySelector("[data-reason]").value;
  renderFax();
}

function selectField(key) {
  S.selected = key;
  const [p] = key.split(":");
  const page = +p.slice(1);
  if (page !== S.page) { S.page = page; renderFax(); return; }
  document.querySelectorAll(".field").forEach((el) => el.classList.toggle("selected", el.dataset.key === key));
  drawBox(key);
}

function drawBox(key) {
  const [p, name] = key.split(":");
  const rx = S.data.verdict.prescriptions.find((r) => `p${r.page}` === p);
  const f = rx.fields[name];
  const pg = S.data.pages[rx.page - 1];
  const box = $("#evbox"), note = $("#locnote");
  box.classList.remove("region");
  let rect = f.evidence_box;
  if (!rect) {
    // never guess the field's location; but if a noise check measured a real region, show THAT, labelled as such
    const noise = f.checks_failed.find((c) => c.check === "NOISE_NEAR_CRITICAL" && c.evidence && c.evidence.region);
    if (!noise) {
      box.classList.add("hidden");
      note.textContent = `${FIELD_LABEL[name]}: location not found on the fax (no matching OCR text, so no box is drawn).`;
      return;
    }
    rect = noise.evidence.region;
    box.classList.add("region");
    note.textContent = `${FIELD_LABEL[name]}: location not found (the noise hides it from OCR). Dashed outline = the region where noise was measured, not the field's position.`;
  } else {
    note.textContent = `${FIELD_LABEL[name]}: box from Tesseract word positions on page ${rx.page}.`;
  }
  const [x0, y0, x1, y1] = rect;
  Object.assign(box.style, { left: `${(x0 / pg.width) * 100}%`, top: `${(y0 / pg.height) * 100}%`,
    width: `${((x1 - x0) / pg.width) * 100}%`, height: `${((y1 - y0) / pg.height) * 100}%` });
  box.classList.remove("hidden");
  const wrap = $("#faxwrap"), img = $("#faximg");
  const scale = img.clientWidth / pg.width;
  wrap.scrollTo({ top: Math.max(0, y0 * scale - wrap.clientHeight / 3), behavior: "smooth" });
}

function openClarification() {
  const v = S.data.verdict;
  const f = v.prescriptions[0].fields;
  const val = (k) => f[k].value || f[k].alternatives[0] || "(not legible)";
  const qs = v.clarification_questions;
  const text = qs.length ? [
    `TO: ${val("prescriber_name")}`,
    `FROM: ${CFG.pharmacy_name} — phone ${CFG.pharmacy_phone}, fax ${CFG.pharmacy_fax}`,
    `RE: ${val("patient_name")} (DOB ${val("patient_dob")}) — ${val("drug_name")}`,
    "",
    "We received this prescription by fax. Before we can fill it, please clarify:",
    "",
    ...qs.map((q, i) => `${i + 1}. ${q.question}${q.page && v.prescriptions.length > 1 ? ` (page ${q.page})` : ""}`),
    "",
    `Please reply by fax to ${CFG.pharmacy_fax} or call ${CFG.pharmacy_phone}. Thank you.`,
  ].join("\n") : "No clarification questions for this fax: every flag is internal to the pharmacy (or there are no flags).";
  $("#clar-text").value = text;
  $("#copy-msg").textContent = "";
  $("#modal").classList.remove("hidden");
}

async function submitOrder() {
  try {
    const r = await api(`/api/submit/${S.data.verdict.fax_id}`, { method: "POST" });
    $("#submit-msg").innerHTML = `<div class="banner warn" style="background:var(--ok-bg);border-color:#bfe0cc;color:var(--ok)"><strong>✓ Submitted</strong>Outcome: ${esc(r.outcome.replaceAll("_", " "))} at ${esc(r.time)}. Recorded in submissions.jsonl.</div>`;
  } catch (e) {
    $("#submit-msg").innerHTML = `<div class="banner blocked"><strong>Not submitted</strong>${esc(e.message)}</div>`;
  }
}

// ---------------------------------------------------------------- scorecard
async function showScorecard() {
  const s = await api("/api/scorecard");
  if (!s.dev) { $("#app").innerHTML = "<h1>Scorecard</h1><p>No evaluation report yet. Run <code>python -m faxfill.eval</code>.</p>"; return; }
  const d = s.dev.data, a = d.accuracy;
  const caught = d.traps.filter((t) => t.outcome.startsWith("caught")).length;
  const right = d.traps.filter((t) => t.outcome === "caught (right reason)").length;
  const cAll = d.c_only_flags.clean.concat(d.c_only_flags.trap);
  const c = cAll.length, cRight = cAll.filter((x) => x.includes("regex was RIGHT")).length;
  const tile = (v, k) => `<div class="tile"><div class="v">${v}</div><div class="k">${k}</div></div>`;
  $("#app").innerHTML = `
    <h1>Scorecard</h1>
    <div class="card"><span class="label-dev">Development set (rules tuned on these faxes)</span>
      <p class="muted">31 synthetic faxes. Thresholds were chosen while looking at these, so these numbers are optimistic.</p>
      <div class="tiles">
        ${tile(`${a.verified_right}/${a.verified}`, "Verified and actually right")}
        ${tile(`${caught}/${d.traps.length}`, `planted traps caught (${right} for the right reason)`)}
        ${tile(`${d.clean_flags.length}`, "flags on the 5 clean faxes")}
        ${tile(`${a.correct}/${a.correct + a.wrong}`, "field values correct")}
        ${tile(`${c}`, `flags from the regex reader alone (regex right in ${cRight}, wrong in ${c - cRight})`)}
      </div>
      <details><summary>Full report</summary><pre class="report">${esc(s.dev.text)}</pre></details></div>
    <div class="card"><h2>Held-out set</h2><p class="muted">Not run yet. It will be generated later with a new seed, after the rules are frozen, and reported here separately.</p></div>`;
}

// ---------------------------------------------------------------- router
async function route() {
  const h = location.hash || "#/queue";
  document.querySelectorAll("nav a").forEach((a) => a.classList.toggle("active", h.startsWith(`#/${a.dataset.nav}`) || (a.dataset.nav === "queue" && h.startsWith("#/fax"))));
  try {
    if (h.startsWith("#/fax/")) await showFax(h.slice(6));
    else if (h.startsWith("#/scorecard")) await showScorecard();
    else await showQueue();
  } catch (e) { $("#app").innerHTML = `<div class="banner blocked"><strong>Error</strong>${esc(e.message)}</div>`; }
  window.scrollTo(0, 0);
}

$("#modal-close").addEventListener("click", () => $("#modal").classList.add("hidden"));
$("#modal").addEventListener("click", (e) => e.target.id === "modal" && $("#modal").classList.add("hidden"));
$("#copy-btn").addEventListener("click", async () => {
  try { await navigator.clipboard.writeText($("#clar-text").value); $("#copy-msg").textContent = "Copied."; }
  catch { $("#clar-text").select(); document.execCommand("copy"); $("#copy-msg").textContent = "Copied."; }
});
window.addEventListener("hashchange", route);
api("/api/config").then((c) => (CFG = c)).finally(route);

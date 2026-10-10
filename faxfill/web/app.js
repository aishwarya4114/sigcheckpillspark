// SigCheck review UI for a first-time pharmacist user. Reads verdicts from the server; never runs models.
const $ = (s, el = document) => el.querySelector(s);
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
let CFG = { pass_label: "Matches fax, checks passed", demo_label: "Demo: saved locally" };
let QUEUE = [];

const FIELD_ORDER = ["patient_name", "patient_dob", "drug_name", "strength", "dosage_form", "directions", "quantity",
  "refills", "prescriber_name", "prescriber_npi", "prescriber_dea", "date_written", "substitution", "notes"];
const FIELD_LABEL = { patient_name: "Patient", patient_dob: "Date of birth", drug_name: "Drug", strength: "Strength",
  dosage_form: "Form", directions: "Directions (Sig)", quantity: "Quantity", refills: "Refills",
  prescriber_name: "Prescriber", prescriber_npi: "NPI", prescriber_dea: "DEA", date_written: "Date written",
  substitution: "Substitution", notes: "Notes", _order: "Whole fax" };
const CHECK_LABEL = { ocr_text_agrees: "OCR text agrees", patient_in_profiles: "patient profile found",
  dob_matches_profile: "DOB matches profile", dob_valid_date: "valid date", drug_in_rxnorm: "drug in RxNorm",
  strength_in_rxnorm: "strength in RxNorm", form_in_rxnorm: "form in RxNorm", directions_parsed: "directions understood",
  "within_usual_max (manual, unverified)": "within usual max dose", numeral_matches_words: "number matches words",
  quantity_matches_duration: "matches duration", refills_whole_number: "whole number",
  prescriber_matches_npi: "matches NPI in directory", prescriber_in_directory: "in directory", npi_luhn: "check digit valid",
  npi_in_directory: "in directory", dea_checksum: "check digit valid", date_valid_not_future: "valid, not in future",
  substitution_known_value: "box reading recognised" };
const STATUS_TXT = { BLOCKED: ["⛔", "Blocked"], NEEDS_PHARMACIST: ["⚠", "Needs review"], READY: ["✓", "Ready"] };

const pill = (status, text) => {
  const [ic, label] = STATUS_TXT[status] || ["", status];
  return `<span class="pill s-${status}"><span aria-hidden="true">${ic}</span>${esc(text || label)}</span>`;
};
async function api(path, opts) {
  const r = await fetch(path, opts);
  const body = await r.json().catch(() => ({}));
  if (!r.ok) throw new Error(body.detail || r.statusText);
  return body;
}

// ================================================================ QUEUE
async function showQueue() {
  QUEUE = await api("/api/queue");
  const groups = [["BLOCKED", "Blocked", "Cannot be processed. Contact the sender."],
                  ["NEEDS_PHARMACIST", "Needs review", "A person must decide before these go on."],
                  ["READY", "Ready", "Every field matches the fax and passed its checks."]];
  const n = (st) => QUEUE.filter((r) => r.status === st).length;
  $("#app").innerHTML = `
    <div class="qhead"><h1>Incoming faxes</h1>
      <div class="summarybar" role="status">
        <span class="sb s-BLOCKED">⛔ ${n("BLOCKED")} blocked</span><span class="dot">·</span>
        <span class="sb s-NEEDS_PHARMACIST">⚠ ${n("NEEDS_PHARMACIST")} need review</span><span class="dot">·</span>
        <span class="sb s-READY">✓ ${n("READY")} ready</span></div></div>
    <p class="muted">Within each group, the most dangerous fax is first. Arrival time does not change the order.</p>
    ${groups.map(([st, title, hint]) => {
      const rows = QUEUE.filter((r) => r.status === st);
      if (!rows.length) return "";
      return `<section class="qgroup"><h2>${pill(st, title)} <span class="muted small">${rows.length} · ${hint}</span></h2>
        <table class="queue"><thead><tr><th>Fax</th><th>Patient</th><th>Drug</th><th>Why</th><th>Priority</th></tr></thead><tbody>
        ${rows.map((r) => `<tr tabindex="0" data-id="${r.fax_id}">
          <td><div>Fax from ${esc(r.clinic)} · ${esc(r.received)}</div><div class="faxid">${esc(r.fax_id)}${r.pages > 1 ? ` · ${r.pages} pages` : ""}</div></td>
          <td>${esc(r.patient || "—")}</td><td>${esc(r.drug || "—")}</td>
          <td>${r.top_reason ? esc(r.top_reason) : '<span class="muted">All checks passed</span>'}</td>
          <td><span class="prio p-${r.priority}" title="Danger score ${r.danger}. ${esc(CFG.priority?.about || "")}">${r.priority}</span></td>
        </tr>`).join("")}</tbody></table></section>`;
    }).join("")}`;
  document.querySelectorAll(".queue tbody tr").forEach((tr) => {
    const go = () => (location.hash = `#/fax/${tr.dataset.id}`);
    tr.addEventListener("click", go);
    tr.addEventListener("keydown", (e) => e.key === "Enter" && go());
  });
}

// ================================================================ DETAIL
const S = { data: null, page: 1, selected: null, drafts: {}, details: {} };

async function showFax(id) {
  S.data = await api(`/api/fax/${id}`);
  S.page = 1; S.selected = null; S.drafts = {}; S.details = {};
  if (!QUEUE.length) QUEUE = await api("/api/queue");
  renderFax();
}

const isBlocked = () => S.data.verdict.order_status === "BLOCKED";
const openItems = () => S.data.items.filter((i) => !S.data.resolutions[i.key]);

function nextStep() {
  const v = S.data.verdict;
  if (isBlocked()) {
    const b = v.order_flags.find((f) => f.status === "blocked");
    if (b.code === "INJECTION") return ["blocked", "Blocked: hidden instructions found. Contact the sender; this fax can't be processed."];
    const it = S.data.summary.top_reason || "pages missing";
    return ["blocked", `Blocked: ${it.charAt(0).toLowerCase() + it.slice(1)}. Ask the sender to resend; this fax can't be processed yet.`];
  }
  const n = openItems().length;
  if (n) return ["todo", `${n} decision${n > 1 ? "s" : ""} needed before this goes to the pharmacist.`];
  if (S.data.items.length) return ["done", "All decisions made. Send it to pharmacist verification."];
  return ["done", "Every field matches the fax and passed its checks. Send it to pharmacist verification."];
}

function renderFax() {
  const { verdict: v, pages, summary } = S.data;
  const blocked = isBlocked();
  const [kind, sentence] = nextStep();
  const canSend = !blocked && openItems().length === 0;
  $("#app").innerHTML = `
    <div class="rhead">
      <div><a href="#/queue">← Queue</a>
        <h1>${esc(summary.patient || "Patient unclear")} · ${esc(summary.drug || "drug unclear")} ${pill(v.order_status)}</h1>
        <div class="muted">Fax from ${esc(summary.clinic)} · received ${esc(summary.received)} · ${v.pages_received} page(s)
          · <span title="Danger score ${v.danger_score}">${summary.priority} priority</span> · <span class="faxid">${esc(v.fax_id)}</span></div>
        <p class="nextstep ns-${kind}" role="status">${esc(sentence)}</p></div>
      <div class="actions">
        ${blocked ? `<button id="sender" class="primary">Draft message to sender</button>`
          : `<button id="clar">Clarification fax</button>
             <button id="send" class="primary" ${canSend ? "" : "disabled"} title="${canSend ? "" : "Make every decision first"}">Send to pharmacist verification</button>`}
      </div>
    </div>
    ${blocked ? blockBanner(v) : ""}
    <div class="split">
      <section class="faxpane" aria-label="Fax image">
        <div class="tabs" role="tablist">${pages.map((p) => `<button role="tab" data-page="${p.page}" aria-selected="${p.page === S.page}">Page ${p.page}</button>`).join("")}
          <span class="muted small" style="margin-left:auto">Click a field to see where it was read</span></div>
        <div class="faxwrap" id="faxwrap"><div class="faxstage" id="faxstage">
          <img id="faximg" alt="Fax page ${S.page}" src="${pages[S.page - 1].url}"><div id="evbox" class="evbox hidden"></div></div></div>
        <div class="locnote" id="locnote"></div>
      </section>
      <section class="fields" aria-label="Fields">${blocked ? blockedFields(v) : decisionsAndChecklist(v)}
        ${prismPanel(v)}</section>
    </div>`;
  document.querySelectorAll(".tabs button").forEach((b) => b.addEventListener("click", () => { S.page = +b.dataset.page; S.selected = null; renderFax(); }));
  document.querySelectorAll(".selectable").forEach((el) => {
    el.addEventListener("click", (e) => { if (!e.target.closest(".resolve, .detailtoggle, details")) selectField(el.dataset.key); });
  });
  document.querySelectorAll(".detailtoggle").forEach((b) => b.addEventListener("click", () => { S.details[b.dataset.k] = !S.details[b.dataset.k]; renderFax(); }));
  wireResolvers();
  $("#sender") && $("#sender").addEventListener("click", openSenderMessage);
  $("#clar") && $("#clar").addEventListener("click", openClarification);
  $("#send") && $("#send").addEventListener("click", openSendSummary);
  $("#faximg").addEventListener("load", () => S.selected && drawBox(S.selected));
  if (S.selected) drawBox(S.selected);
}

function blockBanner(v) {
  return v.order_flags.filter((f) => f.status === "blocked").map((f) => {
    let detail = esc(f.reason);
    if (f.code === "INJECTION" && f.evidence && f.evidence.length)
      detail = `Text on the fax addressed to an AI: <q>${esc(f.evidence[0].text)}</q> (page ${f.evidence[0].page}). Instructions printed on a fax are never followed.`;
    return `<div class="banner blocked" role="alert"><strong>⛔ ${esc(S.data.summary.top_reason)}</strong>${detail}
      <div>There is no override. The fields below were not reviewed.</div></div>`;
  }).join("");
}

function blockedFields(v) {
  return v.prescriptions.map((rx) => `
    ${v.prescriptions.length > 1 ? `<div class="page-label">Page ${rx.page}</div>` : ""}
    <div class="checklist greyed" aria-label="Not reviewed: order blocked">
      <div class="cl-head">Not reviewed: order blocked</div>
      ${FIELD_ORDER.map((name) => {
        const f = rx.fields[name];
        return `<div class="cl-row selectable" data-key="p${rx.page}:${name}" tabindex="0"><span class="cl-name">${FIELD_LABEL[name]}</span>
          <span class="cl-val">${f.value == null ? "—" : esc(f.value)}</span><span class="cl-st">not reviewed</span></div>`;
      }).join("")}</div>`).join("");
}

function decisionsAndChecklist(v) {
  const items = S.data.items;
  const orderItems = items.filter((i) => i.field === "_order");
  let html = "";
  const flagged = [];
  v.prescriptions.forEach((rx) => FIELD_ORDER.forEach((name) => { if (rx.fields[name].codes.length) flagged.push([rx, name]); }));
  if (flagged.length || orderItems.length) {
    html += `<h2 class="sect">Needs your decision <span class="muted small">${openItems().length} of ${items.length} open</span></h2>`;
    html += orderItems.map((it) => orderCard(it)).join("");
    html += flagged.map(([rx, name]) => decisionCard(rx, name)).join("");
  }
  v.prescriptions.forEach((rx) => {
    const ok = FIELD_ORDER.filter((n) => !rx.fields[n].codes.length);
    const matching = ok.filter((n) => rx.fields[n].status === "verified").length;
    html += `<h2 class="sect">${v.prescriptions.length > 1 ? `Page ${rx.page}: ` : ""}${matching} field${matching === 1 ? "" : "s"} match the fax</h2>
      <div class="checklist">${ok.map((name) => {
        const f = rx.fields[name];
        const lasa = (f.notes.find((n) => n.startsWith("Look-alike partner")) || "").replace(/^Look-alike partner\(s\): /, "").split(".")[0];
        const okRow = f.status === "verified";
        const checks = f.checks_passed.map((c) => CHECK_LABEL[c.check] || c.check).join(" · ");
        return `<div class="cl-row selectable ${S.selected === `p${rx.page}:${name}` ? "selected" : ""}" data-key="p${rx.page}:${name}" tabindex="0" title="${esc(checks)}">
          <span class="cl-name">${FIELD_LABEL[name]}</span>
          <span class="cl-val">${f.value == null ? '<span class="muted">not on fax</span>' : esc(f.value)}${lasa ? ` <span class="badge">Look-alike: ${esc(lasa)}</span>` : ""}</span>
          <span class="cl-st ${okRow ? "ok" : ""}">${okRow ? `✓ ${esc(CFG.pass_label)}` : f.status === "info" ? "note" : "not on fax"}</span></div>`;
      }).join("")}</div>`;
  });
  return html;
}

function readsLine(f) {
  return `Reader A (gpt-5.5): ${esc(JSON.stringify(f.reads.A))} · Reader B (mistral): ${esc(JSON.stringify(f.reads.B))} · OCR: ${esc(JSON.stringify(f.reads.C))}`;
}

function decisionCard(rx, name) {
  const f = rx.fields[name];
  const key = `p${rx.page}:${name}`;
  const item = S.data.items.find((i) => i.key === key);
  const res = S.data.resolutions[key];
  const reading = item.kind === "reading";
  const lasa = f.codes.includes("LOOKALIKE_RISK") ? f.alternatives.filter((a) => a.toLowerCase() !== String(f.value).toLowerCase()) : [];
  const lasaNote = (f.notes.find((n) => n.startsWith("Look-alike partner")) || "").replace(/^Look-alike partner\(s\): /, "").split(".")[0];
  const ocrNotes = f.notes.filter((n) => !n.startsWith("Look-alike partner"));
  return `<article class="dcard ${res ? "done" : ""} selectable ${S.selected === key ? "selected" : ""}" data-key="${key}" tabindex="0">
    <div class="frow"><div><div class="fname">${FIELD_LABEL[name]}${S.data.verdict.prescriptions.length > 1 ? ` · page ${rx.page}` : ""}</div>
      <div class="ftitle">${esc(item.short)}</div></div>
      <span class="kind k-${item.kind}">${reading ? "Reading problem" : "Safety problem"}</span></div>
    <div class="fval ${f.value == null ? "empty" : ""}">${f.value == null ? "Not taken from the fax" : `Fax reads: ${esc(f.value)}`}</div>
    ${lasa.length || lasaNote ? `<div class="badge">Look-alike: ${esc(lasa.join(", ") || lasaNote)}</div>` : ""}
    <ul class="reason">${f.reasons.map((r) => `<li>${esc(r)}</li>`).join("")}</ul>
    ${ocrNotes.map((n) => `<div class="ocrnote">${esc(n)}</div>`).join("")}
    <button class="detailtoggle linkish" data-k="${key}">${S.details[key] ? "Hide details" : "Show details"}</button>
    ${S.details[key] ? `<div class="details"><div class="reads">${readsLine(f)}</div><div class="reads">Codes: ${f.codes.map(esc).join(", ")}</div>
      ${f.checks_passed.length ? `<div class="reads">Checks passed: ${f.checks_passed.map((c) => esc(CHECK_LABEL[c.check] || c.check)).join(", ")}</div>` : ""}</div>` : ""}
    ${res ? resolvedNote(res) : resolverHTML(key, reading ? f.alternatives : null, reading ? "field" : "safety")}
  </article>`;
}

function orderCard(it) {
  const fl = S.data.verdict.order_flags.find((f) => `order:${f.code}` === it.key);
  const res = S.data.resolutions[it.key];
  return `<article class="dcard ${res ? "done" : ""}" data-key="${it.key}">
    <div class="frow"><div><div class="fname">Whole fax</div><div class="ftitle">${esc(it.short)}</div></div>
      <span class="kind k-reading">Reading problem</span></div>
    <ul class="reason"><li>${esc(fl.reason)}</li></ul>
    ${res ? resolvedNote(res) : resolverHTML(it.key, null, "order")}</article>`;
}

const ACTION_TXT = { alternative: "Fax says", typed: "Fax says", call_prescriber: "Call prescriber", confirm_order_flag: "Confirmed: all pages received",
  confirmed_proceed: "Confirmed with prescriber, proceed", hold_call: "Hold and call prescriber", reject: "Rejected" };
function resolvedNote(r) {
  const what = ["alternative", "typed"].includes(r.action) ? `${ACTION_TXT[r.action]} <b>${esc(r.chosen_value)}</b>` : `<b>${ACTION_TXT[r.action]}</b>`;
  return `<div class="resolved-note">✓ Decided: ${what} — “${esc(r.reason)}” <span class="muted small">${esc(r.time)}</span></div>`;
}

function resolverHTML(key, alts, mode) {
  const d = S.drafts[key] || {};
  const btn = (act, label) => `<button data-act="${act}" class="${d.action === act ? "chosen" : ""}">${label}</button>`;
  let q, choices;
  if (mode === "safety") {
    q = "The fax was read correctly, but there is a safety problem. What should happen?";
    choices = btn("confirmed_proceed", "Confirmed with prescriber, proceed") + btn("hold_call", "☎ Hold and call prescriber") + btn("reject", "✕ Reject");
  } else if (mode === "order") {
    q = "Did every page arrive?";
    choices = btn("confirm_order_flag", "Yes, all pages received") + btn("call_prescriber", "☎ Call sender");
  } else {
    q = "What does the fax say?";
    choices = (alts || []).map((a) => `<button data-alt="${esc(a)}" class="${d.action === "alternative" && d.chosen_value === a ? "chosen" : ""}">${esc(a)}</button>`).join("")
      + btn("call_prescriber", "☎ Call prescriber");
  }
  return `<div class="resolve" data-key="${key}"><div class="q">${q}</div>
    <div class="alts">${choices}</div>
    ${mode === "field" ? `<input type="text" data-typed placeholder="Or type what the fax says" value="${esc(d.action === "typed" ? d.chosen_value : "")}">` : ""}
    <div class="row"><input type="text" data-reason placeholder="Reason (required), e.g. verified against the original" value="${esc(d.reason || "")}">
      <button class="primary" data-save>Save decision</button></div>
    <div class="err" data-err></div></div>`;
}

function wireResolvers() {
  document.querySelectorAll(".resolve").forEach((box) => {
    const key = box.dataset.key;
    const d = (S.drafts[key] = S.drafts[key] || {});
    const keep = () => { d.reason = box.querySelector("[data-reason]").value; renderFax(); };
    box.querySelectorAll("[data-alt]").forEach((b) => b.addEventListener("click", () => { Object.assign(d, { action: "alternative", chosen_value: b.dataset.alt }); keep(); }));
    box.querySelectorAll("[data-act]").forEach((b) => b.addEventListener("click", () => { Object.assign(d, { action: b.dataset.act, chosen_value: null }); keep(); }));
    const typed = box.querySelector("[data-typed]");
    typed && typed.addEventListener("input", () => { if (typed.value.trim()) Object.assign(d, { action: "typed", chosen_value: typed.value }); });
    box.querySelector("[data-reason]").addEventListener("input", (e) => (d.reason = e.target.value));
    box.querySelector("[data-save]").addEventListener("click", async () => {
      d.reason = box.querySelector("[data-reason]").value;
      try {
        if (!d.action) throw new Error("Choose an option first.");
        const rec = await api(`/api/resolve/${S.data.verdict.fax_id}`, { method: "POST", headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ key, action: d.action, chosen_value: d.chosen_value, reason: d.reason }) });
        S.data.resolutions[key] = rec; delete S.drafts[key]; renderFax();
      } catch (e) { box.querySelector("[data-err]").textContent = e.message; }
    });
  });
}

// ---------------------------------------------------------------- evidence boxes
function selectField(key) {
  S.selected = key;
  const page = +key.split(":")[0].slice(1);
  if (page !== S.page) { S.page = page; renderFax(); return; }
  document.querySelectorAll(".selectable").forEach((el) => el.classList.toggle("selected", el.dataset.key === key));
  drawBox(key);
}

function drawBox(key) {
  const [p, name] = key.split(":");
  const rx = S.data.verdict.prescriptions.find((r) => `p${r.page}` === p);
  if (!rx || !rx.fields[name]) return;
  const f = rx.fields[name];
  const pg = S.data.pages[rx.page - 1];
  const box = $("#evbox"), note = $("#locnote");
  box.classList.remove("region");
  let rect = f.evidence_box;
  if (!rect) {
    const noise = f.checks_failed.find((c) => c.check === "NOISE_NEAR_CRITICAL" && c.evidence && c.evidence.region);
    if (!noise) { box.classList.add("hidden"); note.textContent = `${FIELD_LABEL[name]}: location not found on the fax.`; return; }
    rect = noise.evidence.region;
    box.classList.add("region");
    note.textContent = `${FIELD_LABEL[name]}: location not found (noise hides it). Dashed outline = where the noise was measured, not the field.`;
  } else {
    note.textContent = `${FIELD_LABEL[name]}: where it was read (Tesseract word positions), page ${rx.page}.`;
  }
  // percentages of the IMAGE: the stage is exactly the image's size, so the box scales and scrolls with it
  const [x0, y0, x1, y1] = rect;
  Object.assign(box.style, { left: `${(x0 / pg.width) * 100}%`, top: `${(y0 / pg.height) * 100}%`,
    width: `${((x1 - x0) / pg.width) * 100}%`, height: `${((y1 - y0) / pg.height) * 100}%` });
  box.classList.remove("hidden");
  const wrap = $("#faxwrap"), img = $("#faximg");
  if (img.clientHeight) wrap.scrollTo({ top: Math.max(0, (y0 / pg.height) * img.clientHeight - wrap.clientHeight / 3), behavior: "smooth" });
}

// ---------------------------------------------------------------- messages and sending
function modal(title, bodyHTML) {
  $("#modal-title").textContent = title;
  $("#modal-body").innerHTML = bodyHTML;
  $("#modal").classList.remove("hidden");
}
function closeModal() { $("#modal").classList.add("hidden"); }

function draftBox(text) {
  return `<p class="muted">Review before sending. Nothing is sent from this tool.</p>
    <textarea id="clar-text" rows="14" spellcheck="false">${esc(text)}</textarea>
    <div class="modal-actions"><span id="copy-msg" class="muted"></span><button id="copy-btn" class="primary">Copy text</button></div>`;
}
function wireCopy() {
  $("#copy-btn").addEventListener("click", async () => {
    try { await navigator.clipboard.writeText($("#clar-text").value); } catch { $("#clar-text").select(); document.execCommand("copy"); }
    $("#copy-msg").textContent = "Copied.";
  });
}
const fv = (k) => { const f = S.data.verdict.prescriptions[0].fields[k]; return f.value || "(not legible)"; };

function openClarification() {
  const v = S.data.verdict, qs = v.clarification_questions;
  const text = qs.length ? [`TO: ${fv("prescriber_name")}`, `FROM: ${CFG.pharmacy_name} — phone ${CFG.pharmacy_phone}, fax ${CFG.pharmacy_fax}`,
    `RE: ${fv("patient_name")} (DOB ${fv("patient_dob")}) — ${fv("drug_name")}`, "",
    "We received this prescription by fax. Before we can fill it, please clarify:", "",
    ...qs.map((q, i) => `${i + 1}. ${q.question}${q.page && v.prescriptions.length > 1 ? ` (page ${q.page})` : ""}`), "",
    `Please reply by fax to ${CFG.pharmacy_fax} or call ${CFG.pharmacy_phone}. Thank you.`].join("\n")
    : "No questions for the prescriber: the open items are internal pharmacy decisions.";
  modal("Clarification fax (draft)", draftBox(text)); wireCopy();
}

function openSenderMessage() {
  const v = S.data.verdict, b = v.order_flags.find((f) => f.status === "blocked");
  const body = b.code === "INJECTION"
    ? ["We received a fax from your office that we cannot process: it contains printed text addressed to automated software, which we do not act on.",
       "", "Please call us to confirm the prescription, or resend it from your system without additional notes."]
    : [`We received only part of a fax from your office: ${S.data.summary.top_reason.toLowerCase()}.`, "",
       "Please resend the complete fax. We have not processed any part of it."];
  const text = [`TO: ${S.data.summary.clinic}`, `FROM: ${CFG.pharmacy_name} — phone ${CFG.pharmacy_phone}, fax ${CFG.pharmacy_fax}`,
    `RE: fax received ${S.data.summary.received}`, "", ...body, "", "Thank you."].join("\n");
  modal("Message to sender (draft)", draftBox(text)); wireCopy();
}

function openSendSummary() {
  const v = S.data.verdict, res = S.data.resolutions;
  const rows = v.prescriptions.map((rx) => FIELD_ORDER.filter((n) => rx.fields[n].value != null || res[`p${rx.page}:${n}`]).map((n) => {
    const r = res[`p${rx.page}:${n}`];
    const val = r && ["alternative", "typed"].includes(r.action) ? r.chosen_value : rx.fields[n].value;
    return `<tr><th>${FIELD_LABEL[n]}${v.prescriptions.length > 1 ? ` (p${rx.page})` : ""}</th><td>${esc(val ?? "—")}${r ? ' <span class="badge">decided</span>' : ""}</td></tr>`;
  }).join("")).join("");
  const decisions = Object.values(res);
  modal("Send to pharmacist verification", `
    <p><span class="label-dev">${esc(CFG.demo_label)}</span> Nothing leaves this computer.</p>
    <h3>What is being sent</h3><table class="kv">${rows}</table>
    <h3>Decisions made (${decisions.length})</h3>
    ${decisions.length ? `<ul class="reason">${decisions.map((r) => `<li><b>${esc(FIELD_LABEL[r.field] || r.field)}</b>: ${esc(ACTION_TXT[r.action])}${r.chosen_value ? ` “${esc(r.chosen_value)}”` : ""} — ${esc(r.reason)}</li>`).join("")}</ul>` : '<p class="muted">None needed: every field passed.</p>'}
    <div class="modal-actions"><button id="cancel-send">Cancel</button><button id="confirm-send" class="primary">Confirm and send</button></div>`);
  $("#cancel-send").addEventListener("click", closeModal);
  $("#confirm-send").addEventListener("click", async () => {
    try {
      const r = await api(`/api/submit/${v.fax_id}`, { method: "POST" });
      const idx = QUEUE.findIndex((q) => q.fax_id === v.fax_id);
      const next = QUEUE.slice(idx + 1).find((q) => q.status !== "BLOCKED") || QUEUE.find((q) => q.status !== "BLOCKED" && q.fax_id !== v.fax_id);
      const outcome = { sent_to_pharmacist_verification: "Sent to pharmacist verification", held_for_prescriber_call: "Held: waiting on a call to the prescriber", rejected: "Rejected" }[r.outcome];
      modal("Done", `<p class="nextstep ns-done">✓ ${esc(outcome)}.</p><p class="muted">${esc(CFG.demo_label)} (submissions.jsonl, ${esc(r.time)})</p>
        <div class="modal-actions"><a href="#/queue">Back to queue</a>${next ? `<a class="button primary" href="#/fax/${next.fax_id}" id="next-fax">Next fax →</a>` : ""}</div>`);
    } catch (e) { modal("Not sent", `<div class="banner blocked">${esc(e.message)}</div>`); }
  });
}

function prismPanel(v) {
  const p = v.prism || {};
  if (!p.traces_sent) return "";
  const g = p.guardrail || {}, ls = v.lookup_sources || {};
  const code = (x) => `<code>${esc(x)}</code>`;
  return `<details class="card prism"><summary><b>PRISM (Block Convey)</b> <span class="muted small">· ${p.traces_sent} traces sent
      · local guardrail ${g.local_injection_check ? "⛔ injection found" : "✓ clean"} · PRISM guardrail: dashboard only</span></summary>
    <table class="kv">
      <tr><th>Input guardrail</th><td>Local check: <b>${g.local_injection_check ? "injection found → blocked" : "no injected instructions"}</b>.<br>
        PRISM: OCR text sent as ${(p.intake_trace_ids || []).map(code).join(", ")}. ${esc(g.prism_status || "")}</td></tr>
      <tr><th>Reader traces</th><td>${(p.reader_trace_ids || []).map(code).join("<br>")}</td></tr>
      <tr><th>Verifier trace</th><td>${code(p.verifier_trace_id || "—")} <span class="muted">(scored by the Evaluators Hub rubric)</span></td></tr>
      <tr><th>Knowledge Base</th><td>${esc(p.knowledge_base || "")}<br><span class="muted">Lookups answered by: ${esc(ls.drug || "?")}</span></td></tr>
    </table></details>`;
}

// ================================================================ SCORECARD
async function showScorecard() {
  const s = await api("/api/scorecard");
  if (!s.dev) { $("#app").innerHTML = "<h1>Scorecard</h1><p>No evaluation report yet.</p>"; return; }
  const d = s.dev.data, a = d.accuracy;
  const caught = d.traps.filter((t) => t.outcome.startsWith("caught")).length;
  const right = d.traps.filter((t) => t.outcome === "caught (right reason)").length;
  const cAll = d.c_only_flags.clean.concat(d.c_only_flags.trap), cRight = cAll.filter((x) => x.includes("regex was RIGHT")).length;
  const tile = (v, k) => `<div class="tile"><div class="v">${v}</div><div class="k">${k}</div></div>`;
  $("#app").innerHTML = `<h1>Scorecard</h1>
    <div class="card"><span class="label-dev">Development set (rules tuned on these faxes)</span>
      <p class="muted">31 synthetic faxes. Thresholds were chosen while looking at these, so these numbers are optimistic.</p>
      <div class="tiles">${tile(`${a.verified_right}/${a.verified}`, "marked passing and actually right")}
        ${tile(`${caught}/${d.traps.length}`, `planted traps caught (${right} for the right reason)`)}
        ${tile(`${d.clean_flags.length}`, "flags on the 5 clean faxes")}
        ${tile(`${a.correct}/${a.correct + a.wrong}`, "field values correct")}
        ${tile(`${cAll.length}`, `flags from the regex reader alone (right in ${cRight}, wrong in ${cAll.length - cRight})`)}</div>
      <details><summary>Full report</summary><pre class="report">${esc(s.dev.text)}</pre></details></div>
    <div class="card"><h2>Held-out set</h2><p class="muted">Not run yet. It will be generated later with a new seed, after the rules are frozen.</p></div>`;
}

// ================================================================ ROUTER
async function route() {
  const h = location.hash || "#/queue";
  closeModal();
  document.querySelectorAll("nav a").forEach((a) => a.classList.toggle("active", h.startsWith(`#/${a.dataset.nav}`) || (a.dataset.nav === "queue" && h.startsWith("#/fax"))));
  try {
    if (h.startsWith("#/fax/")) await showFax(h.slice(6));
    else if (h.startsWith("#/scorecard")) await showScorecard();
    else await showQueue();
  } catch (e) { $("#app").innerHTML = `<div class="banner blocked"><strong>Error</strong>${esc(e.message)}</div>`; }
  window.scrollTo(0, 0);
}
$("#modal-close").addEventListener("click", closeModal);
$("#modal").addEventListener("click", (e) => e.target.id === "modal" && closeModal());
window.addEventListener("hashchange", route);
window.addEventListener("resize", () => S.selected && location.hash.startsWith("#/fax") && drawBox(S.selected));
api("/api/config").then((c) => (CFG = { ...CFG, ...c })).finally(route);

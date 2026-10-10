const $ = (s) => document.querySelector(s);
const labels = {
  patient_name: "Patient name",
  patient_dob: "Date of birth",
  drug_name: "Medication",
  strength: "Strength",
  dosage_form: "Dosage form",
  directions: "Directions",
  quantity: "Quantity",
  refills: "Refills",
  prescriber_name: "Prescriber",
  prescriber_npi: "NPI number",
  prescriber_dea: "DEA number",
  date_written: "Date written",
};
const groups = [
  ["Patient", ["patient_name", "patient_dob"]],
  [
    "Prescription",
    [
      "drug_name",
      "strength",
      "dosage_form",
      "directions",
      "quantity",
      "refills",
    ],
  ],
  [
    "Prescriber",
    ["prescriber_name", "prescriber_npi", "prescriber_dea", "date_written"],
  ],
];
let cur = null,
  selected = null,
  queue = [],
  filter = "all",
  busy = false,
  zoom = 100,
  openSequence = 0,
  uploadKey = null,
  failedUpload = null;
const state = (k, f) => (cur.review?.[k] ? "reviewed" : f.status);
const OPS = { new: ["New", ""], needs_verification: ["Needs verification", "amber"], needs_clarification: ["Prescriber clarification required", "amber"],
  waiting_for_prescriber: ["Waiting for prescriber", "blue"], ready_for_approval: ["Ready to approve", "green"], approved: ["Approved draft", "green"], blocked: ["Blocked", "red"] };
const srcKind = (s) => { s = (s || "").toLowerCase(); return s.includes("rescan") || s.includes("corrected") ? "Rescan" : s.includes("phone") ? "Phone capture" : "Fax"; };
const PLAIN_SOURCE = { "Apple Vision": "how clearly the scanner could read this part of the fax", "check-digit formula": "the built-in check digit every valid number has",
  "demonstration catalogue": "the pharmacy's drug catalogue", "intake policy + catalogue": "the intake policy and the drug catalogue", "intake policy": "the pharmacy's intake policy",
  "format check": "a basic format check", "local guardrail + PRISM trace": "the safety screen that runs before any automated reading", "reader": "the automated reading step" };
const ago = (t) => { const m = Math.max(0, Math.round((Date.now() - new Date(t)) / 60000)); return m < 1 ? "just now" : m < 60 ? m + " min ago" : Math.round(m / 60) + " h ago"; };
function renderQueue() {
  const GROUP = { needs_action: ["new", "needs_verification", "needs_clarification", "blocked"], waiting: ["waiting_for_prescriber"], ready: ["ready_for_approval"], completed: ["approved"] };
  const inGroup = (d, g) => g === "all" || (GROUP[g] || [g]).includes(d.workflow_status);
  const visible = queue.filter((d) => inGroup(d, filter));
  $("#queue-count").textContent = queue.length;
  document.querySelectorAll("[data-filter]").forEach((b) => {
    const n = queue.filter((d) => inGroup(d, b.dataset.filter)).length;
    b.dataset.count = n;
  });
  $("#queue").innerHTML = visible.length
    ? visible.map((d) => {
        const [lab, tone] = OPS[d.workflow_status] || [d.workflow_label || d.status, ""];
        const acts = d.workflow_status === "approved" ? "" : d.actions ? `${d.actions} action${d.actions === 1 ? "" : "s"}` : "";
        const person = d.patient || (d.workflow_status === "blocked" ? "Blocked document" : "Prescription document");
        const drug = d.workflow_status === "blocked" ? (d.headline || "Blocked") : (d.drug || "Awaiting extraction");
        return `<button class="queue-item ${tone} ${cur?.id === d.id ? "active" : ""}" data-doc="${esc(d.id)}" aria-pressed="${cur?.id === d.id}">
          <span class="queue-priority" aria-hidden="true"></span>
          <span class="queue-identity"><span class="queue-person">${esc(person)}</span><span class="queue-drug">${esc(drug)}</span></span>
          <span class="queue-op ${tone}"><span class="queue-state"><b>${esc(lab)}</b>${acts ? `<em>${esc(acts)}</em>` : ""}</span><small>${esc(d.next_action || "Open case")}</small></span>
          <span class="queue-src muted small"><b>${esc(ago(d.received_at))}</b><small>${esc(srcKind(d.source))}<span class="tech-only"> · r${esc(d.revision)}</span></small></span>
          <span class="queue-open" aria-hidden="true">${icon("chevron")}</span>
        </button>`;
      }).join("")
    : '<div class="queue-empty">' + (filter === "all" ? "Your queue is clear. Upload a fax or explore a sample." : "Nothing in this step right now.") + "</div>";
}
function showQueueView() {
  cur = null;
  selected = null;
  history.replaceState(null, "", location.pathname);
  document.body.classList.add("queue-mode");
  renderQueue();
}
async function refresh() {
  queue = await api("/api/documents");
  renderQueue();
  const latest = queue.find((d) => d.id === cur?.id);
  if (
    latest &&
    (latest.revision !== cur.revision || latest.status !== cur.status) &&
    !busy
  ) {
    const bar = $("#approval-bar");
    if (bar && !bar.querySelector("[data-reload]"))
      bar.insertAdjacentHTML(
        "afterbegin",
        '<button data-reload style="width:100%;margin-bottom:9px">New revision available · reload</button>',
      );
  }
}
async function openDoc(id) {
  const seq = ++openSequence;
  try {
    const doc = await api("/api/documents/" + encodeURIComponent(id) + "/open", { method: "POST" });
    if (seq !== openSequence) return;
    cur = doc;
    document.body.classList.remove("queue-mode");
    selected = null;
    zoom = 100;
    history.replaceState(null, "", "#" + encodeURIComponent(id));
    renderDocument();
    renderQueue();
  } catch (e) {
    toast(e.message, true);
  }
}
function renderDocument() {
  $("#document-title").textContent =
    cur.fields?.patient_name?.value || "Prescription document";
  $("#document-subtitle").innerHTML =
    `Received ${esc(ago(cur.received_at))} by ${esc(srcKind(cur.source).toLowerCase())}<span class="tech-only tech-note"> · ${esc(cur.source)} · revision ${esc(cur.revision)} · ${esc(cur.id)}</span>`;
  $("#workspace").innerHTML =
    `<section class="card source-card" aria-label="Original prescription"><div class="panel-bar"><strong>${icon("file")}Original fax</strong><div class="toolbar"><button data-zoom="-25" aria-label="Zoom out">${icon("minus")}</button><button data-zoom="reset" id="zoom-value" aria-label="Fit document to width">100%</button><button data-zoom="25" aria-label="Zoom in">${icon("plus")}</button></div></div><div class="document-stage"><div class="paper" id="paper"><img id="fax-image" src="/api/documents/${encodeURIComponent(cur.id)}/image?r=${cur.revision}" alt="Original prescription, revision ${cur.revision}"><div class="highlight" id="highlight" hidden></div></div></div><div class="source-bottom">${icon("info")}Select a field to locate its source on the fax.</div></section><section class="card review-card" aria-label="Review extracted details"><div class="panel-bar"><strong>${icon("shield")}Review details</strong><span class="queue-op ${(OPS[cur.workflow?.status] || [])[1] || ""}">${esc(cur.workflow?.label || "")}</span></div><div class="review-body" id="review-body"></div><div class="approval-bar" id="approval-bar"></div></section>`;
  renderPanel();
  $("#fax-image").addEventListener("error", () =>
    toast(
      "The source image could not be loaded. Please refresh the document.",
      true,
    ),
  );
}
function renderPanel() {
  const body = $("#review-body"),
    scroll = body.scrollTop;
  const entries = Object.entries(cur.fields || {}),
    required = entries.filter(([, f]) => f.required),
    done = required.filter(
      ([k, f]) => cur.review?.[k] && String(f.value ?? "").trim(),
    ).length;
  const flagged = entries.filter(([k, f]) =>
    ["missing", "needs_review"].includes(state(k, f)),
  ).length;
  const locked = ["blocked", "processing_failed", "processing"].includes(
      cur.status,
    ),
    approved = cur.status === "approved";
  const globalFindings = (cur.findings || []).filter((f) => !f.field);
  const canApprove = cur.workflow?.status === "ready_for_approval";
  let message =
    cur.status === "blocked"
      ? [
          "red",
          "Document blocked",
          "Instructions aimed at the software were detected. Review the source; this order cannot be approved.",
        ]
      : cur.status === "processing_failed"
        ? [
            "red",
            "Extraction needs another try",
            cur.sponsors?.reader?.error ||
              "We could not extract a reliable draft. Upload a clearer image.",
          ]
        : approved
          ? [
              "",
              "Draft approved",
              "The current revision is ready to export for pharmacy entry.",
            ]
          : cur.clarification?.open
            ? ["amber", "Clarification requested", cur.clarification.message]
            : flagged
              ? [
                  "amber",
                  `${flagged} field${flagged === 1 ? "" : "s"} need attention`,
                  "Resolve the highlighted details, then confirm each required field against the fax.",
                ]
              : [
                  "",
                  "Check the source, then confirm",
                  "Automatic checks are complete. Your review is required before approval.",
                ];
  body.innerHTML = `${caseHeaderHTML()}${workHTML()}
 ${
   cur.status === "blocked"
     ? ""
     : groups
         .map(
           ([title, keys]) =>
             `<div class="field-group"><div class="field-group-title eyebrow">${title}</div>${keys
               .filter((k) => cur.fields[k])
               .map((k) => fieldHTML(k, cur.fields[k], locked || approved))
               .join("")}</div>`,
         )
         .join("")
 }
 ${detailsHTML()}`;
  $("#approval-bar").innerHTML = footerHTML();
  updateStepper();
  body.scrollTop = scroll;
  highlight(cur.fields?.[selected]?.box);
}
function fieldHTML(k, f, locked) {
  const s = state(k, f),
    review = cur.review?.[k],
    attention = ["missing", "needs_review"].includes(s);
  return `<article class="field-row ${attention ? "attention" : ""} ${selected === k ? "selected" : ""}"><button class="field-toggle" data-field="${k}" aria-expanded="${selected === k}" aria-controls="editor-${k}"><span class="field-top"><span>${labels[k]}${f.required ? " *" : ""}</span><span class="chips">${machineChip(f)}${humanChip(k, f)}</span></span><div class="field-value ${f.value == null ? "missing" : ""}">${esc(f.value ?? (k === "refills" ? "Not stated" : f.status === "not_applicable" ? "Not required" : "Not found on fax"))}</div>${attention ? `<div class="field-hint">${icon("alert")}${esc(f.reasons?.[0] || "Review against the original document")}</div>` : ""}</button>${selected === k ? `<div class="field-editor" id="editor-${k}"><div class="evidence"><span>What the scanner read on the fax${f.ocr_score != null ? `<span class="tech-only tech-note"> · recognizer score ${esc(f.ocr_score)}</span>` : ""}</span>${f.source_text ? "“" + esc(f.source_text) + "”" : "No readable source text located."}</div>${checksHTML(f)}${review ? `<p class="reviewed-note">${icon("check")} ${esc(review.action === "edit" ? "Corrected" : "Confirmed")} by ${esc(review.by)}${review.reason ? " · " + esc(review.reason) : ""}</p>` : ""}${!locked ? `<div class="editor-actions">${f.value != null ? `<button data-confirm="${k}" ${busy ? "disabled" : ""}>${icon("check")}Confirm against fax</button>` : ""}</div><details><summary class="small muted">Correct this field</summary><form class="edit-form" data-edit="${k}"><label>Corrected value<input class="input" name="value" value="${esc(f.value ?? "")}" ${f.required ? "required" : ""}></label><label>Evidence or reason<input class="input" name="reason" required placeholder="What supports this correction?"></label><button type="submit" ${busy ? "disabled" : ""}>Save correction</button></form></details><div class="request-actions"><button data-clarify="rescan" data-key="${k}">Request clearer image</button><button data-clarify="prescriber" data-key="${k}">Draft prescriber clarification</button></div>` : ""}</div>` : ""}</article>`;
}
const CAT = { verify_transcription: ["Verify transcription", "The software is unsure what the fax says. Check it against the image."],
  prescriber_clarification: ["Clarify with the prescriber", "The fax itself is missing, ambiguous or unsafe. Re-reading it can't resolve this."],
  blocked: ["Blocked document", "This document can't continue through normal intake."] };
const NOTE_OK = new Set(["LOOK_ALIKE_DRUG", "DRUG_NOT_IN_CATALOGUE", "STRENGTH_NOT_IN_CATALOGUE", "PRESCRIPTION_TOO_OLD", "DEA_CHECK_DIGIT_FAILED", "DEA_PRESCRIBER_MISMATCH", "IMPLAUSIBLE_DOB", "DATE_IN_FUTURE", "TOO_MANY_REFILLS"]);
function machineChip(f) {
  const m = { checks_passed: ["No automated issue", "neutral", "info"], needs_review: ["Automated concern", "amber", "alert"], missing: ["Missing on fax", "red", "x"], not_applicable: ["Not required", "neutral", "dash"] }[f.status] || [f.status, "neutral", "info"];
  return `<span class="chip ${m[1]}">${icon(m[2])}${m[0]}</span>`;
}
function humanChip(k, f) {
  if (f.status === "not_applicable") return "";
  const viaPrescriber = (cur.workflow?.issues || []).some((i) => i.field === k && i.category === "prescriber_clarification" && i.state === "resolved");
  const r = cur.review?.[k];
  if (viaPrescriber) return `<span class="chip green">${icon("phone")}Resolved with prescriber</span>`;
  if (r) return `<span class="chip green">${icon("check")}${r.action === "edit" ? "Corrected from source" : "Verified by you"}</span>`;
  return f.required ? `<span class="chip outline">Not reviewed</span>` : "";
}
const FIELD_ORDER = ["patient_name", "patient_dob", "drug_name", "strength", "dosage_form", "directions", "quantity", "refills", "prescriber_name", "prescriber_npi", "prescriber_dea", "date_written"];
function nextField() {
  const f = cur.fields || {}, open = new Set((cur.workflow?.issues || []).filter((i) => i.state === "open" && i.category === "verify_transcription").map((i) => i.field));
  const todo = FIELD_ORDER.filter((k) => f[k]?.required && f[k].value != null && !cur.review?.[k]);
  return todo.find((k) => open.has(k)) || todo[0] || null;
}
function footerHTML() {
  const w = cur.workflow || {}, st = w.status, id = encodeURIComponent(cur.id);
  if (st === "approved") return `<div class="footer-pair"><a class="primary big-action" target="_blank" rel="noopener" href="/api/documents/${id}/order-sheet">${icon("file")}Print or save PDF</a><a class="secondary big-action" href="/api/documents/${id}/export">${icon("download")}Export JSON</a></div><p>Draft order for pharmacy entry · not dispensed</p>`;
  if (st === "blocked") return cur.quarantined
    ? `<button class="primary big-action" data-upload-corrected>${icon("upload")}Upload corrected document</button><p>Quarantined for manual review. Nothing was sent to a model.</p>`
    : `<button class="primary big-action" data-quarantine>${icon("lock")}Quarantine document</button><p>This document can't continue through normal intake.</p>`;
  if (st === "needs_clarification") return `<a class="primary big-action" target="_blank" rel="noopener" href="/api/documents/${id}/clarification-sheet">${icon("file")}Prepare clarification</a><p>Then mark the prescriber as contacted.</p>`;
  if (st === "waiting_for_prescriber") return `<button class="primary big-action" data-focus-response>${icon("phone")}Record prescriber response</button><p>Enter exactly what the prescriber confirmed.</p>`;
  if (st === "ready_for_approval") return `<button class="primary big-action" data-attest>${icon("check")}Review and approve</button><p>${w.counts?.reviewed} of ${w.counts?.required} required fields reviewed · 0 open issues</p>`;
  const k = selected && cur.fields?.[selected]?.required && cur.fields[selected].value != null && !cur.review?.[selected] ? selected : nextField();
  if (k) return `<button class="primary big-action" data-confirm-next="${k}" ${busy ? "disabled" : ""}>${icon("check")}Confirm ${esc(labels[k])} and continue</button><p>Check the highlighted part of the fax first. To change it, use "Correct this field".</p>`;
  return `<p>${esc(w.next_action || "")}</p>`;
}
function attest() {
  let d = $("#attest-dialog");
  if (!d) { d = document.createElement("dialog"); d.id = "attest-dialog"; d.setAttribute("aria-labelledby", "attest-title"); document.body.appendChild(d); }
  const c = cur.workflow?.counts || {};
  d.innerHTML = `<div class="dialog-head"><h2 id="attest-title">Approve draft order</h2><button class="quiet" data-close="attest-dialog" aria-label="Close">${icon("close")}</button></div>
    <p><b>${esc(cur.fields?.patient_name?.value || "")}</b> · ${esc(cur.fields?.drug_name?.value || "")} ${esc(cur.fields?.strength?.value || "")}</p>
    ${orderTableHTML()}
    <ul class="attest-list"><li>${icon("check")}${c.reviewed} of ${c.required} required fields reviewed against the source</li><li>${icon("check")}No open transcription issues</li><li>${icon("check")}No open prescriber clarifications</li></ul>
    <label class="attest-check"><input type="checkbox" id="attest-ok"> I reviewed every required field against the source and resolved all open issues.</label>
    <button class="primary big-action" data-action="approve" id="attest-approve" disabled>${icon("check")}Approve draft order</button>
    <p class="small muted">This approves a draft for pharmacy entry. It is not dispensing.</p>`;
  d.querySelector("#attest-ok").addEventListener("change", (e) => (d.querySelector("#attest-approve").disabled = !e.target.checked));
  d.showModal();
}
function updateStepper() {
  const st = cur?.workflow?.status, steps = document.querySelectorAll(".steps span");
  if (steps.length !== 3) return;
  const stage = st === "approved" ? 3 : st === "ready_for_approval" ? 2.5 : 2;
  steps.forEach((el, i) => {
    el.classList.toggle("done", i + 1 < Math.floor(stage) || (st === "approved" && i < 3));
    el.classList.toggle("current", st === "approved" ? false : (stage === 2.5 ? i === 2 : i === 1));
  });
}
function caseHeaderHTML() {
  const w = cur.workflow || {}, [lab, tone] = OPS[w.status] || [w.label, ""];
  return `<div class="case-head"><div class="case-meta"><span class="muted small">${esc(cur.fields?.drug_name?.value || "")} · received ${esc(ago(cur.received_at))}<span class="tech-only"> · revision ${esc(cur.revision)}</span></span></div><p class="next-action"><span class="eyebrow">Next action</span>${esc(w.next_action || "")}</p></div>`;
}
function workHTML() {
  const w = cur.workflow || {}, iss = (w.issues || []);
  if (w.status === "approved") {
    const a = cur.approved || {};
    return `<div class="receipt"><h3>${icon("check")} Draft order approved</h3><dl><dt>Approved by</dt><dd>${esc(a.by)}</dd><dt>Time</dt><dd>${esc(new Date(a.at).toLocaleString())}</dd><dt>Revision</dt><dd>${esc(a.revision)}</dd><dt>Attestation</dt><dd>${esc(a.attestation || "")}</dd><dt>Audit trail</dt><dd>${(cur.events || []).length} events (see history below)</dd></dl><p class="small muted">Draft order for pharmacy entry. Not dispensed, not sent to a pharmacy system.</p><button class="quiet" data-back-queue>${icon("arrow")} Return to queue</button></div>`;
  }
  if (w.status === "ready_for_approval") {
    const c = w.counts || {};
    return `<div class="summary-card"><h3>Ready for pharmacist approval</h3><ul><li>${c.reviewed} of ${c.required} required fields reviewed</li><li>0 unresolved transcription issues</li><li>0 open prescriber clarifications</li><li>Current revision: ${esc(cur.revision)}</li></ul>${orderTableHTML()}</div>`;
  }
  const open = iss.filter((i) => i.state !== "resolved");
  if (!open.length) return "";
  const byCat = ["blocked", "prescriber_clarification", "verify_transcription"].map((c) => [c, open.filter((i) => i.category === c)]).filter(([, l]) => l.length);
  return `<section class="actions-panel" aria-label="Required actions"><h3 class="eyebrow">Required actions · ${open.length}</h3>${byCat
    .map(([c, list]) => `<div class="action-group ${c}"><div class="action-group-head"><strong>${CAT[c][0]}</strong><span class="small muted">${CAT[c][1]}</span></div>${list.map(issueHTML).join("")}</div>`)
    .join("")}</section>`;
}
function issueHTML(i) {
  const fl = i.field ? labels[i.field] || i.field : "Document";
  const val = i.field ? cur.fields?.[i.field]?.value : null;
  let acts = "";
  if (i.category === "verify_transcription")
    acts = `<button data-field="${esc(i.field)}">${icon("search")}Check against fax</button><button data-clarify="rescan" data-key="${esc(i.field)}">Request clearer scan</button>`;
  else if (i.category === "prescriber_clarification")
    acts = i.state === "waiting"
      ? `<form class="resolve-form" data-resolve="${esc(i.key)}"><label>Prescriber's answer (who, how, what)<input class="input" name="note" required placeholder="Dr Patel by phone 2:31 PM: 5 mg"></label>${i.field ? `<label>${NOTE_OK.has(i.code) ? "Value to record (only if the prescriber changed it)" : "Unambiguous value the prescriber gave (required)"}<input class="input" name="value" value="" ${NOTE_OK.has(i.code) ? "" : "required"} placeholder="${esc(i.code === "AMBIGUOUS_TRAILING_ZERO" ? "e.g. 5 mg" : "")}"></label>` : ""}<button type="submit">${icon("check")}Record prescriber answer</button></form>`
      : `<a class="button-link" target="_blank" rel="noopener" href="/api/documents/${encodeURIComponent(cur.id)}/clarification-sheet">${icon("file")}Draft clarification fax</a><button data-contacted="${esc(i.key)}">${icon("phone")}Mark prescriber contacted</button>`;
  else
    acts = cur.quarantined ? '<span class="small muted">Quarantined for manual review.</span>' : "";
  return `<article class="issue ${i.state}"><div class="issue-top"><strong>${esc(fl)}${val ? ` · <span class="mono">${esc(val)}</span>` : ""}</strong>${i.state === "waiting" ? `<span class="queue-op blue">${icon("clock")}Waiting</span>` : ""}</div><p>${esc(i.message)}</p>${Array.isArray(i.details) && i.details.length ? `<ul class="block-details">${i.details.map((d) => `<li>${esc(d)}</li>`).join("")}</ul>` : ""}${i.next_step ? `<p class="small"><b>Next:</b> ${esc(i.next_step)}</p>` : ""}<details class="why"><summary>Why was this flagged?</summary><p class="small">Based on ${esc(PLAIN_SOURCE[i.source] || i.source)}.${i.category === "prescriber_clarification" ? " Confirming that the fax says this doesn't resolve it: only the prescriber can." : ""}<span class="tech-only tech-note"> Rule ${esc(i.code)} · ${esc(i.source)} · policy ${esc(cur.policy?.version || "")}</span></p></details><div class="issue-actions">${acts}</div></article>`;
}
function orderTableHTML() {
  const f = cur.fields || {};
  return `<table class="order-table">${Object.entries(f).filter(([, x]) => x.value != null).map(([k, x]) => `<tr><th>${esc(labels[k] || k)}</th><td>${esc(x.value)}</td></tr>`).join("")}</table>`;
}
function checksHTML(f) {
  if (!Array.isArray(f.checks) || !f.checks.length)
    return f.reasons?.length ? `<ul class="reasons">${f.reasons.map((x) => "<li>" + esc(x) + "</li>").join("")}</ul>` : "";
  const failed = f.checks.filter((c) => !c.passed);
  const next = failed.find((c) => c.next_step);
  return `${next ? `<div class="next-step"><span class="eyebrow">Suggested next step</span>${esc(next.next_step)}</div>` : ""}<div class="checklist" aria-label="Checks run on this field"><span class="eyebrow">Checks run · ${f.checks.length - failed.length} of ${f.checks.length} passed</span>${f.checks
    .map((c) => `<div class="check ${c.passed ? "ok" : "fail"}"><span class="check-mark" aria-hidden="true">${icon(c.passed ? "check" : "x")}</span><span><span class="sr-only">${c.passed ? "Passed: " : "Failed: "}</span>${esc(c.label)}${!c.passed && c.detail ? `<small>${esc(c.detail)}</small>` : ""}</span></div>`)
    .join("")}</div>`;
}
function pipelineHTML() {
  const t = cur.timings_ms || {}, g = cur.sponsors?.guardrail || {}, r = cur.sponsors?.reader || {}, kb = cur.sponsors?.kb || {};
  const flagged = Object.values(cur.fields || {}).filter((f) => ["missing", "needs_review"].includes(f.status)).length;
  const steps = [
    ["Fax read", (cur.ocr?.lines || []).length ? "ok" : "fail", (cur.ocr?.lines || []).length ? "done" : "unreadable", t.ocr, "Apple Vision"],
    ["Safety screen", g.blocked ? "fail" : "ok", g.blocked ? "stopped" : "passed", t.guardrail, "guardrail + PRISM"],
    ["Details read", cur.status === "blocked" ? "skip" : r.ok === false ? "fail" : "ok", cur.status === "blocked" ? "skipped" : r.ok === false ? "failed" : "done", t.extract, (r.engine || "").startsWith("Nicrron") ? "Nicrron" : "demo reader"],
    ["Checks", cur.status === "blocked" ? "skip" : flagged ? "warn" : "ok", cur.status === "blocked" ? "skipped" : flagged ? `${flagged} need you` : "all clear", t.checks, "rules"],
    ["Catalogue", cur.status === "blocked" ? "skip" : kb.status === "supported" ? "ok" : "skip", cur.status === "blocked" ? "skipped" : kb.status === "supported" ? "found" : kb.status === "no_match" ? "not found" : "not checked", t.kb, "PRISM KB"],
  ];
  return `<ol class="pipeline" aria-label="Processing steps">${steps
    .map(([n, st, d, ms, tech]) => `<li class="${st}"><span class="dot" aria-hidden="true">${icon(st === "ok" ? "check" : st === "fail" ? (n === "Guard" ? "ban" : "x") : st === "warn" ? "bang" : "dash")}</span><b>${n}</b><small>${esc(d)}</small><small class="tech-only tech-note">${esc(tech || "")}${Number.isFinite(ms) ? " · " + ms + " ms" : ""}</small></li>`)
    .join("")}</ol>`;
}
async function openRules() {
  const p = await api("/api/policy"), pol = cur?.policy || p.policy, lim = p.limits;
  const can = cur && !["blocked", "processing_failed", "processing"].includes(cur.status);
  $("#rules-body").innerHTML = `<p class="small muted">Policy version <b>${esc(p.policy.version)}</b>${cur ? ` · this document was checked with threshold ${esc(pol.recognizer_threshold)} and a ${esc(pol.max_rx_age_days)}-day validity window` : ""}.</p>
  <h3 class="rules-h">Adjustable (stricter or looser review, never off)</h3>
  <label class="rules-row">Recognizer review threshold <output id="thr-out">${Number(p.policy.recognizer_threshold).toFixed(2)}</output>
    <input id="thr" type="range" min="${lim.recognizer_threshold[0]}" max="${lim.recognizer_threshold[1]}" step="0.05" value="${p.policy.recognizer_threshold}" oninput="document.getElementById('thr-out').textContent=Number(this.value).toFixed(2)"></label>
  <p class="small muted">Fields read below this score go to a pharmacist. Apple Vision reports coarse scores (often 1.0, 0.5 or 0.3), so this may not change results on clean images.</p>
  <label class="rules-row">Prescription validity window (days) <output id="age-out">${p.policy.max_rx_age_days}</output>
    <input id="age" type="range" min="${lim.max_rx_age_days[0]}" max="${lim.max_rx_age_days[1]}" step="1" value="${p.policy.max_rx_age_days}" oninput="document.getElementById('age-out').textContent=this.value"></label>
  <h3 class="rules-h">Safety rules (always on, not configurable)</h3>
  <ul class="locked-rules">${p.locked_rules.map((r) => `<li>${icon("lock")}${esc(r)}</li>`).join("")}</ul>
  <h3 class="rules-h">Data sources</h3>
  <ul class="small">${p.data_sources.map((d) => `<li>${esc(d)}</li>`).join("")}</ul>
  <button class="primary" id="rerun-button" ${can ? "" : "disabled"}>${icon("check")}Re-run checks on this extraction</button>
  <p class="small muted">${can ? "Keeps the image and extracted values. Runs only the deterministic checks (no new OCR or model call). Clears confirmations, invalidates any approval, and records the change in the history." : "Open a reviewable document to re-run its checks. Blocked documents can't be re-run."}</p>`;
  $("#rules-dialog").showModal();
}
async function rerunChecks(b) {
  if (!cur) return;
  b.disabled = true;
  try {
    const r = await api(`/api/documents/${encodeURIComponent(cur.id)}/rerun-checks`, {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ recognizer_threshold: Number($("#thr").value), max_rx_age_days: Number($("#age").value), revision: cur.revision }),
    });
    cur = r.document;
    $("#rules-dialog").close();
    renderDocument();
    await refresh();
    toast(r.message);
  } catch (err) {
    toast(err.message, true);
    b.disabled = false;
  }
}
function howChecksHTML() {
  const f = cur.fields || {}, s = cur.sponsors || {}, kb = s.kb || {}, g = s.guardrail || {};
  const has = (k) => f[k] && f[k].value != null && String(f[k].value).trim() !== "";
  const blocked = cur.status === "blocked";
  const lab = (x) => `<span class="res res-${x.toLowerCase().replace(/ /g, "-")}">${x}</span>`;
  const na = "Not applicable", ok = "Checked", un = "Unavailable";
  const rows = [
    ["Legibility", "Parts of the fax the scanner struggled to read go to you; a clear read doesn't prove the value is right", "Scanner", (cur.ocr?.lines || []).length ? ok : un],
    ["Trailing / leading zero", "5.0 mg could be misread as 50 mg; .5 mg as 5 mg", "Intake policy", blocked ? na : has("strength") ? ok : na],
    ["Drug / strength coverage", "Whether the combination appears in the demonstration catalogue (coverage, not safety)", "Drug catalogue", blocked || !has("drug_name") ? na : ok, blocked || !has("drug_name") ? null : kb.prism?.accepted ? ok : un],
    ["Look-alike name", "Warns about known confusing drug names", "Catalogue", blocked ? na : has("drug_name") ? ok : na],
    ["NPI check digit", "Detects likely mistyped or misread identifiers", "Check-digit formula", blocked ? na : has("prescriber_npi") ? ok : na],
    ["DEA validation", "Check digit and prescriber initial, for controlled substances", "Check-digit formula", blocked ? na : f.prescriber_dea?.required ? (has("prescriber_dea") ? ok : ok) : na],
    ["Never-guess rule", "Missing values remain blank", "Intake policy", blocked ? na : ok],
    ["Safety screen", "Stops a fax that contains instructions aimed at the software", "Runs before any automated reading", ok, g.prism?.accepted ? ok : un],
    ["Approval gate", "A pharmacist must review required fields before export", "SigCheck workflow", ok],
  ];
  return `<details class="detail-section"><summary>How SigCheck checks a prescription</summary><div class="detail-content"><table class="how-table"><thead><tr><th>Check</th><th>What it means</th><th>Source</th><th>This fax</th></tr></thead><tbody>${rows
    .map(([n, m, src, r1, r2]) => `<tr><td><b>${esc(n)}</b></td><td>${esc(m)}</td><td>${esc(src)}</td><td>${lab(r1)}${r2 ? `<span class="tech-only"><br><small>PRISM: </small>${lab(r2)}</span>` : ""}</td></tr>`)
    .join("")}</tbody></table><p class="small muted">"Checked" means the check ran on this fax. "Unavailable" means it couldn't run, and is never shown as a pass.</p></div></details>`;
}
function detailsHTML() {
  const s = cur.sponsors || {},
    kb = s.kb || {},
    g = s.guardrail || {},
    r = s.reader || {},
    e = s.evaluator || {};
  return `<details class="detail-section"><summary>Processing evidence</summary><div class="detail-content">${pipelineHTML()}</div></details>${howChecksHTML()}<details class="detail-section"><summary>Catalogue check</summary><div class="detail-content"><p>${esc(kb.status === "supported" ? `${cur.fields?.drug_name?.value || "This drug"} ${cur.fields?.strength?.value || ""} ${cur.fields?.dosage_form?.value || ""} is in the pharmacy's drug catalogue.` : kb.status === "no_match" ? "This drug and strength weren't found in the pharmacy's drug catalogue. That's a coverage result, not a safety judgement." : "The catalogue check didn't run for this fax.")}</p>${kb.citation ? `<p class="tech-only tech-note">${esc(kb.document)} · ${esc(kb.citation)} · ${esc(kb.source || "")}</p>` : ""}</div></details><details class="detail-section"><summary>Review history · ${(cur.events || []).length}</summary><div class="detail-content">${(
    cur.events || []
  )
    .slice()
    .reverse()
    .map(
      (x) =>
        `<div class="history-event"><strong>${esc(x.action)}${x.field ? " · " + esc(labels[x.field] || x.field) : ""}</strong><p>${esc(x.actor)} · ${esc(new Date(x.time).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" }))}</p>${x.reason ? `<p>${esc(x.reason)}</p>` : ""}</div>`,
    )
    .join(
      "",
    )}</div></details><details class="detail-section tech-only"><summary>Processing & sponsor details</summary><div class="detail-content"><p><strong>Privacy:</strong> ${esc(s.telemetry || "Synthetic data only")}. The fax image is sent to the model provider for extraction; real prescriptions would require a BAA or a self-hosted model.</p><p><strong>Reader:</strong> ${esc(r.engine || "Not run")}</p><p><strong>Reader trace:</strong> ${r.trace?.accepted ? "Accepted" : "Not recorded"}</p><p><strong>Input guardrail:</strong> ${g.blocked ? "Blocked" : "No local pattern detected"}; PRISM trace ${g.prism?.accepted ? "accepted" : "not recorded"}</p><p><strong>Knowledge base:</strong> ${esc(kb.status || "Unavailable")}</p><p><strong>Evaluator:</strong> ${e.trajectory_id ? "Submitted; verdict must be verified" : "Not submitted"}</p><p class="mono">${esc(e.trajectory_id || "")}</p><p>Trace delivery is not a passing evaluation.</p></div></details>`;
}
function highlight(box) {
  const el = $("#highlight");
  if (!el) return;
  el.hidden = !Array.isArray(box) || box.length !== 4;
  if (el.hidden) return;
  const [x, y, w, h] = box;
  if (![x, y, w, h].every(Number.isFinite)) {
    el.hidden = true;
    return;
  }
  Object.assign(el.style, {
    left: Math.max(0, x - 0.004) * 100 + "%",
    top: Math.max(0, y - 0.004) * 100 + "%",
    width: Math.min(1, w + 0.008) * 100 + "%",
    height: Math.min(1, h + 0.008) * 100 + "%",
  });
}
async function mutate(fn) {
  if (busy || !cur) return;
  busy = true;
  const id = cur.id;
  try {
    const doc = await fn();
    if (cur?.id === id) {
      cur = doc;
      renderPanel();
    }
    await refresh();
  } catch (e) {
    toast(e.message, true);
  } finally {
    busy = false;
    if (cur?.id === id) renderPanel();
  }
}
document.addEventListener("click", async (e) => {
  const b = e.target.closest("button");
  if (!b) return;
  if (b.dataset.close) {
    $("#" + b.dataset.close).close();
    return;
  }
  if (b.hasAttribute("data-reload")) {
    await openDoc(cur.id);
    return;
  }
  if (b.dataset.confirmNext) {
    const k = b.dataset.confirmNext;
    await mutate(async () => {
      const doc = await api(`/api/documents/${cur.id}/fields/${k}`, jsonPost({ action: "confirm", revision: cur.revision, reviewer: "pharmacist", reason: "Verified against the original fax" }));
      toast(labels[k] + " verified");
      return doc;
    });
    selected = nextField();
    renderPanel();
    if (selected) document.querySelector(`[data-field="${selected}"]`)?.focus();
    return;
  }
  if (b.hasAttribute("data-focus-response")) {
    const inp = document.querySelector(".resolve-form input");
    inp?.scrollIntoView({ block: "center" }); inp?.focus();
    return;
  }
  if (b.hasAttribute("data-attest")) { attest(); return; }
  if (b.dataset.contacted) {
    const key = b.dataset.contacted;
    await mutate(async () => {
      const d = await api(`/api/documents/${cur.id}/issues/${encodeURIComponent(key)}/contacted`, jsonPost({ note: "Prescriber contacted", revision: cur.revision, reviewer: "pharmacist" }));
      toast("Marked as contacted. The case is waiting for the prescriber.");
      return d;
    });
    return;
  }
  if (b.hasAttribute("data-quarantine")) {
    await mutate(async () => {
      const d = await api(`/api/documents/${cur.id}/quarantine`, { method: "POST" });
      toast("Document quarantined for manual review.");
      return d;
    });
    return;
  }
  if (b.hasAttribute("data-upload-corrected")) {
    const inp = document.createElement("input");
    inp.type = "file"; inp.accept = "image/*";
    inp.onchange = () => {
      const f = inp.files[0]; if (!f) return;
      const fd = new FormData(); fd.append("file", f); fd.append("source", "corrected document");
      mutate(async () => {
        await api(`/api/documents/${cur.id}/rescan`, { method: "POST", body: fd, headers: { "Idempotency-Key": crypto.randomUUID() } });
        const d = await api(`/api/documents/${cur.id}`);
        toast(d.changed_fields?.length ? `New revision ${d.revision}: ${d.changed_fields.length} field(s) changed and need review.` : `New revision ${d.revision} attached.`);
        renderDocument();
        return d;
      });
    };
    inp.click();
    return;
  }
  if (b.hasAttribute("data-back-queue")) {
    showQueueView();
    return;
  }
  if (b.dataset.filter) {
    filter = b.dataset.filter;
    document.querySelectorAll("[data-filter]").forEach((x) => {
      x.classList.toggle("active", x === b);
      x.setAttribute("aria-pressed", x === b);
    });
    renderQueue();
    return;
  }
  if (b.dataset.doc) {
    await openDoc(b.dataset.doc);
    return;
  }
  if (b.dataset.field) {
    selected = selected === b.dataset.field ? null : b.dataset.field;
    renderPanel();
    $(`[data-field="${b.dataset.field}"]`)?.focus({ preventScroll: true });
    return;
  }
  if (b.dataset.zoom) {
    zoom =
      b.dataset.zoom === "reset"
        ? 100
        : Math.max(50, Math.min(250, zoom + Number(b.dataset.zoom)));
    $("#paper").style.width = zoom + "%";
    $("#zoom-value").textContent = zoom + "%";
    return;
  }
  if (b.dataset.finding !== undefined) {
    highlight(
      (cur.findings || []).filter((f) => !f.field)[Number(b.dataset.finding)]
        ?.box,
    );
    return;
  }
  if (b.dataset.confirm) {
    const k = b.dataset.confirm;
    await mutate(async () => {
      const doc = await api(
        `/api/documents/${cur.id}/fields/${k}`,
        jsonPost({
          action: "confirm",
          revision: cur.revision,
          reviewer: "pharmacist",
          reason: "Reviewed against original fax",
        }),
      );
      toast(labels[k] + " confirmed");
      return doc;
    });
    return;
  }
  if (b.dataset.clarify) {
    const kind = b.dataset.clarify;
    await mutate(async () => {
      const doc = await api(
        `/api/documents/${cur.id}/clarify`,
        jsonPost({ kind, fields: [b.dataset.key] }),
      );
      toast(
        kind === "rescan"
          ? "Clearer image requested."
          : "Clarification saved as a draft. No message was sent.",
      );
      return doc;
    });
    return;
  }
  if (b.dataset.action === "approve") {
    await mutate(async () => {
      const o = jsonPost({ revision: cur.revision, reviewer: "pharmacist" });
      o.headers["X-Role"] = "pharmacist";
      const d = await api(`/api/documents/${cur.id}/approve`, o);
      toast("Draft approved. Your export is ready.");
      return d;
    });
    return;
  }
  if (b.dataset.sample) {
    if (busy) return;
    busy = true;
    b.disabled = true;
    b.innerHTML = '<span class="spinner"></span>Reading prescription…';
    try {
      const d = await api(
        "/api/samples/" + encodeURIComponent(b.dataset.sample),
        { method: "POST" },
      );
      $("#samples-dialog").close();
      await refresh();
      await openDoc(d.id);
    } catch (err) {
      toast(err.message, true);
    } finally {
      busy = false;
      b.disabled = false;
      b.textContent =
        "Open " +
        b.dataset.sample.replace(/^fax_\d+_/, "").replaceAll("_", " ");
    }
    return;
  }
  if (["samples-button", "welcome-samples"].includes(b.id))
    $("#samples-dialog").showModal();
  if (b.id === "system-button") $("#system-dialog").showModal();
  if (b.id === "rules-button") openRules();
  if (b.id === "rerun-button") rerunChecks(b);
  if (b.id === "upload-button") {
    if (failedUpload) await uploadFile(failedUpload);
    else $("#upload").click();
  }
});
document.addEventListener("submit", (e) => {
  if (e.target.matches("[data-resolve]")) {
    e.preventDefault();
    const key = e.target.dataset.resolve, fd = new FormData(e.target);
    const body = { note: String(fd.get("note") || "").trim(), revision: cur.revision, reviewer: "pharmacist" };
    if (fd.has("value")) body.value = String(fd.get("value"));
    mutate(async () => {
      const d = await api(`/api/documents/${cur.id}/issues/${encodeURIComponent(key)}/resolve`, jsonPost(body));
      toast("Prescriber's answer recorded and re-checked.");
      return d;
    });
    return;
  }
  if (!e.target.matches("[data-edit]")) return;
  e.preventDefault();
  const k = e.target.dataset.edit,
    fd = new FormData(e.target);
  mutate(async () => {
    const d = await api(
      `/api/documents/${cur.id}/fields/${k}`,
      jsonPost({
        action: "edit",
        value: String(fd.get("value")).trim() || null,
        reason: String(fd.get("reason")).trim(),
        revision: cur.revision,
        reviewer: "pharmacist",
      }),
    );
    toast("Correction saved with your reason.");
    return d;
  });
});
$("#upload").addEventListener("change", async (e) => {
  const file = e.target.files[0];
  if (!file || busy) return;
  uploadKey = uid();
  await uploadFile(file);
});
async function uploadFile(file) {
  busy = true;
  const b = $("#upload-button");
  b.disabled = true;
  b.innerHTML = '<span class="spinner"></span>Reading fax…';
  const fd = new FormData();
  fd.append("file", file);
  fd.append("source", "web upload");
  try {
    const d = await api("/api/documents", {
      method: "POST",
      body: fd,
      headers: { "Idempotency-Key": uploadKey },
    });
    failedUpload = null;
    $("#upload").value = "";
    await refresh();
    await openDoc(d.id);
  } catch (e) {
    failedUpload = file;
    toast(e.message + " Tap Retry upload to send this same scan again.", true);
  } finally {
    busy = false;
    b.disabled = false;
    b.innerHTML =
      icon(failedUpload ? "refresh" : "plus") +
      (failedUpload ? "Retry upload" : "New prescription");
  }
}
async function boot() {
  try {
    const [samples, h] = await Promise.all([
      api("/api/samples"),
      api("/api/health"),
    ]);
    $("#samples").innerHTML = samples
      .map(
        (s) =>
          `<button data-sample="${esc(s.name)}"><span>${esc(s.name.replace(/^fax_\d+_/, "").replaceAll("_", " "))}<small>${esc(s.trap)}</small></span>${icon("arrow")}</button>`,
      )
      .join("");
    $("#mode").textContent =
      h.nicrron === "live" ? "Reader configured" : "Demo workspace";
    $("#health").innerHTML =
      `<p><strong>OCR:</strong> ${esc(h.ocr)}</p><p><strong>Reader:</strong> ${esc(h.nicrron)}</p><p><strong>PRISM:</strong> ${esc(h.prism)}</p><p>Configuration does not establish a successful live call.</p>`;
    await refresh();
    if (location.hash.length > 1)
    {
      const [docId, field] = decodeURIComponent(location.hash.slice(1)).split("?field=");
      await openDoc(docId);
      if (field && cur?.fields?.[field]) { selected = field; renderPanel(); }
    }
    else showQueueView();
    setInterval(() => refresh().catch(() => {}), 5000);
  } catch (e) {
    $("#queue").innerHTML =
      '<div class="queue-empty">Could not load the workspace. Reload to retry.</div>';
    toast(e.message, true);
  }
}
boot();

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
function renderQueue() {
  const visible = queue.filter(
    (d) =>
      filter === "all" ||
      (filter === "approved"
        ? d.status === "approved"
        : d.status !== "approved"),
  );
  $("#queue-count").textContent = queue.length;
  $("#queue").innerHTML = visible.length
    ? visible
        .map(
          (d) =>
            `<button class="queue-item ${cur?.id === d.id ? "active" : ""}" data-doc="${esc(d.id)}" aria-pressed="${cur?.id === d.id}"><span class="queue-person">${esc(d.patient || "Prescription document")}</span><span class="queue-drug">${esc(d.drug || "Awaiting extraction")}</span><span class="queue-meta">${badge(d.status)}<span class="mono muted">${d.unresolved ? esc(d.unresolved) + " flagged" : "r" + esc(d.revision)}</span></span></button>`,
        )
        .join("")
    : '<div class="queue-empty">' +
      (filter === "all"
        ? "Your queue is clear. Upload a fax or explore a sample."
        : "No prescriptions in this view.") +
      "</div>";
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
    const doc = await api("/api/documents/" + encodeURIComponent(id));
    if (seq !== openSequence) return;
    cur = doc;
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
  $("#document-subtitle").textContent =
    `${cur.source} · Revision ${cur.revision} · ${cur.id}`;
  $("#workspace").innerHTML =
    `<section class="card source-card" aria-label="Original prescription"><div class="panel-bar"><strong>${icon("file")}Original fax</strong><div class="toolbar"><button data-zoom="-25" aria-label="Zoom out">${icon("minus")}</button><button data-zoom="reset" id="zoom-value" aria-label="Fit document to width">100%</button><button data-zoom="25" aria-label="Zoom in">${icon("plus")}</button></div></div><div class="document-stage"><div class="paper" id="paper"><img id="fax-image" src="/api/documents/${encodeURIComponent(cur.id)}/image?r=${cur.revision}" alt="Original prescription, revision ${cur.revision}"><div class="highlight" id="highlight" hidden></div></div></div><div class="source-bottom">${icon("info")}Select a field to locate its source on the fax.</div></section><section class="card review-card" aria-label="Review extracted details"><div class="panel-bar"><strong>${icon("shield")}Review details</strong>${badge(cur.status)}</div><div class="review-body" id="review-body"></div><div class="approval-bar" id="approval-bar"></div></section>`;
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
  const canApprove =
    !locked &&
    !approved &&
    required.length > 0 &&
    done === required.length &&
    !globalFindings.length;
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
  body.innerHTML = `<div class="review-intro"><div class="review-progress"><strong>${done} of ${required.length} required fields reviewed</strong><span class="muted">${required.length ? Math.round((done / required.length) * 100) : 0}%</span></div><div class="progress-track"><span style="width:${required.length ? (done / required.length) * 100 : 0}%"></span></div></div><div class="notice ${message[0]}">${icon(message[0] === "red" ? "shield" : message[0] === "amber" ? "alert" : "info")}<div><strong>${esc(message[1])}</strong>${esc(message[2])}</div></div>
 ${globalFindings.map((f, i) => `<button class="notice red" style="width:100%;text-align:left;margin-top:10px" data-finding="${i}">${icon("alert")}<span>${esc(f.reason)}</span></button>`).join("")}
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
  $("#approval-bar").innerHTML = approved
    ? `<a class="primary" style="display:flex;align-items:center;justify-content:center;gap:8px;border-radius:8px;padding:11px" href="/api/documents/${encodeURIComponent(cur.id)}/export">${icon("download")}Export approved draft</a><p>JSON export · revision ${cur.revision} · not dispensed</p>`
    : `<button class="primary" data-action="approve" ${canApprove && !busy ? "" : "disabled"}>${icon("check")}Approve draft</button><p>${locked ? "Resolve the document issue before approval." : globalFindings.length ? "Document-level findings require clarification." : done < required.length ? `${required.length - done} required fields left to review.` : "Ready for your final approval."}</p>`;
  body.scrollTop = scroll;
  highlight(cur.fields?.[selected]?.box);
}
function fieldHTML(k, f, locked) {
  const s = state(k, f),
    review = cur.review?.[k],
    attention = ["missing", "needs_review"].includes(s);
  return `<article class="field-row ${attention ? "attention" : ""} ${selected === k ? "selected" : ""}"><button class="field-toggle" data-field="${k}" aria-expanded="${selected === k}" aria-controls="editor-${k}"><span class="field-top"><span>${labels[k]}${f.required ? " *" : ""}</span>${badge(s)}</span><div class="field-value ${f.value == null ? "missing" : ""}">${esc(f.value ?? (k === "refills" ? "Not stated" : f.status === "not_applicable" ? "Not required" : "Not found on fax"))}</div>${attention ? `<div class="field-hint">${icon("alert")}${esc(f.reasons?.[0] || "Review against the original document")}</div>` : ""}</button>${selected === k ? `<div class="field-editor" id="editor-${k}"><div class="evidence"><span>Source evidence · machine transcription</span>${f.source_text ? "“" + esc(f.source_text) + "”" : "No readable source text located."}</div>${f.reasons?.length ? `<ul class="reasons">${f.reasons.map((x) => "<li>" + esc(x) + "</li>").join("")}</ul>` : ""}${review ? `<p class="reviewed-note">${icon("check")} ${esc(review.action === "edit" ? "Corrected" : "Confirmed")} by ${esc(review.by)}${review.reason ? " · " + esc(review.reason) : ""}</p>` : ""}${!locked ? `<div class="editor-actions">${f.value != null ? `<button data-confirm="${k}" ${busy ? "disabled" : ""}>${icon("check")}Confirm against fax</button>` : ""}</div><details><summary class="small muted">Correct this field</summary><form class="edit-form" data-edit="${k}"><label>Corrected value<input class="input" name="value" value="${esc(f.value ?? "")}" ${f.required ? "required" : ""}></label><label>Evidence or reason<input class="input" name="reason" required placeholder="What supports this correction?"></label><button type="submit" ${busy ? "disabled" : ""}>Save correction</button></form></details><div class="request-actions"><button data-clarify="rescan" data-key="${k}">Request clearer image</button><button data-clarify="prescriber" data-key="${k}">Draft prescriber clarification</button></div>` : ""}</div>` : ""}</article>`;
}
function detailsHTML() {
  const s = cur.sponsors || {},
    kb = s.kb || {},
    g = s.guardrail || {},
    r = s.reader || {},
    e = s.evaluator || {};
  return `<details class="detail-section"><summary>Reference evidence</summary><div class="detail-content"><p>${esc(kb.claim || "No catalogue evidence available.")}</p>${kb.citation ? `<p><strong>${esc(kb.document)}</strong><br>${esc(kb.citation)}</p>` : ""}</div></details><details class="detail-section"><summary>Review history · ${(cur.events || []).length}</summary><div class="detail-content">${(
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
    )}</div></details><details class="detail-section"><summary>Processing & sponsor details</summary><div class="detail-content"><p><strong>Reader:</strong> ${esc(r.engine || "Not run")}</p><p><strong>Reader trace:</strong> ${r.trace?.accepted ? "Accepted" : "Not recorded"}</p><p><strong>Input guardrail:</strong> ${g.blocked ? "Blocked" : "No local pattern detected"}; PRISM trace ${g.prism?.accepted ? "accepted" : "not recorded"}</p><p><strong>Knowledge base:</strong> ${esc(kb.status || "Unavailable")}</p><p><strong>Evaluator:</strong> ${e.trajectory_id ? "Submitted; verdict must be verified" : "Not submitted"}</p><p class="mono">${esc(e.trajectory_id || "")}</p><p>Trace delivery is not a passing evaluation.</p></div></details>`;
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
  if (b.id === "upload-button") {
    if (failedUpload) await uploadFile(failedUpload);
    else $("#upload").click();
  }
});
document.addEventListener("submit", (e) => {
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
      await openDoc(decodeURIComponent(location.hash.slice(1)));
    setInterval(() => refresh().catch(() => {}), 5000);
  } catch (e) {
    $("#queue").innerHTML =
      '<div class="queue-empty">Could not load the workspace. Reload to retry.</div>';
    toast(e.message, true);
  }
}
boot();

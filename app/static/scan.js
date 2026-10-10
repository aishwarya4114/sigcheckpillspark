const $ = (s) => document.querySelector(s);
let file = null,
  previewURL = null,
  rescanFor = null,
  key = null,
  sending = false;
const mine = () => {
  try {
    const v = JSON.parse(localStorage.getItem("sigcheck_uploads") || "[]");
    return Array.isArray(v) ? v : [];
  } catch {
    return [];
  }
};
function selectFile(e) {
  const next = e.target.files[0];
  if (!next) return;
  file = next;
  key = uid();
  if (previewURL) URL.revokeObjectURL(previewURL);
  previewURL = URL.createObjectURL(file);
  $("#preview-image").src = previewURL;
  $("#preview").hidden = false;
  $("#preview-title").textContent = rescanFor
    ? "Attach a clearer scan"
    : "Check your scan";
  $("#send").textContent = rescanFor
    ? "Attach clearer scan"
    : "Send for review";
  $("#preview").scrollIntoView({ behavior: "smooth", block: "start" });
  e.target.value = "";
}
$("#camera").onchange = selectFile;
$("#file").onchange = selectFile;
$("#camera-button").onclick = () => {
  rescanFor = null;
  $("#camera").click();
};
$("#file-button").onclick = () => {
  rescanFor = null;
  $("#file").click();
};
$("#retake").onclick = () => $("#camera").click();
function cancel() {
  file = null;
  rescanFor = null;
  key = null;
  $("#preview").hidden = true;
  if (previewURL) URL.revokeObjectURL(previewURL);
  previewURL = null;
}
$("#cancel").onclick = cancel;
function showMessage(text, kind) {
  $("#message").className = "notice " + kind;
  $("#message").textContent = text;
}
$("#send").onclick = async () => {
  if (!file || sending) return;
  sending = true;
  document.querySelectorAll("button").forEach((b) => (b.disabled = true));
  showMessage("Uploading and reading your scan. Keep this page open.", "");
  const fd = new FormData();
  fd.append("file", file);
  fd.append("source", rescanFor ? "phone rescan" : "phone capture");
  try {
    const d = await api(
      rescanFor
        ? `/api/documents/${encodeURIComponent(rescanFor)}/rescan`
        : "/api/documents",
      { method: "POST", body: fd, headers: { "Idempotency-Key": key } },
    );
    let saved = true;
    try {
      localStorage.setItem(
        "sigcheck_uploads",
        JSON.stringify([...new Set([d.id, ...mine()])].slice(0, 30)),
      );
    } catch {
      saved = false;
    }
    showMessage(
      `Received · ${d.id}. ${saved ? "You can follow its status below." : "Device history is unavailable; keep this document ID."}`,
      "",
    );
    cancel();
    await load();
  } catch (e) {
    showMessage(
      e.message + " Your scan is kept here; tap Send to retry.",
      "red",
    );
  } finally {
    sending = false;
    document.querySelectorAll("button").forEach((b) => (b.disabled = false));
  }
};
async function load() {
  try {
    const ids = mine();
    if (!ids.length) {
      $("#uploads").innerHTML =
        '<p class="muted small">No uploads from this device yet.</p>';
      return;
    }
    const all = await api("/api/documents");
    const docs = ids.map((id) => all.find((d) => d.id === id)).filter(Boolean);
    $("#uploads").innerHTML = docs.length
      ? docs
          .map(
            (d) =>
              `<div class="upload-row"><div><strong>${esc(d.patient || "Prescription")}</strong><small>${esc(d.drug || "Extraction pending")} · r${esc(d.revision)}</small><span class="mono muted">${esc(d.id)}</span>${d.clarification?.open ? `<small>${esc(d.clarification.message)}</small>` : ""}</div><div>${badge(d.status)}${d.clarification?.open && d.clarification.kind === "rescan" ? `<br><button data-rescan="${esc(d.id)}">Capture again</button>` : ""}</div></div>`,
          )
          .join("")
      : '<p class="muted small">Previous uploads are not available on this server.</p>';
  } catch (e) {
    toast(e.message, true);
  }
}
$("#uploads").onclick = (e) => {
  const b = e.target.closest("[data-rescan]");
  if (b) {
    rescanFor = b.dataset.rescan;
    $("#camera").click();
  }
};
$("#refresh").onclick = load;
load();
setInterval(() => {
  if (!sending) load();
}, 5000);

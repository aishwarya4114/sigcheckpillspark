const paths = {
  check: "M5 12l4 4L19 6",
  shield: "M12 3l8 3v6c0 5-8 9-8 9s-8-4-8-9V6l8-3 M8 12l3 3 5-6",
  file: "M14 3H5v18h14V8z M14 3v6h5 M8 13h8 M8 17h5",
  upload: "M12 16V4 M7 9l5-5 5 5 M4 16v5h16v-5",
  camera: "M3 7h4l2-3h6l2 3h4v14H3z M16 13a4 4 0 1 1-8 0 4 4 0 0 1 8 0",
  chart: "M4 3v18h17 M8 16v-5 M13 16V7 M18 16v-8",
  arrow: "M5 12h14 M13 6l6 6-6 6",
  plus: "M12 5v14 M5 12h14",
  minus: "M5 12h14",
  close: "M6 6l12 12 M6 18L18 6",
  info: "M12 10v7 M12 7h.01 M21 12a9 9 0 1 1-18 0 9 9 0 0 1 18 0",
  alert: "M12 3L2 21h20L12 3 M12 9v5 M12 17h.01",
  chevron: "M9 5l7 7-7 7",
  download: "M12 3v12 M7 10l5 5 5-5 M4 17v4h16v-4",
  refresh:
    "M20 7v5h-5 M4 17v-5h5 M5 8a8 8 0 0 1 13-3l2 3 M4 16l2 3a8 8 0 0 0 13-3",
  grid: "M3 3h7v7H3z M14 3h7v7h-7z M3 14h7v7H3z M14 14h7v7h-7z",
};
window.icon = (name) =>
  `<svg class="icon" aria-hidden="true" viewBox="0 0 24 24"><path d="${paths[name] || paths.file}"/></svg>`;
window.esc = (s) =>
  String(s ?? "").replace(
    /[&<>"']/g,
    (c) =>
      ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[
        c
      ],
  );
window.uid = () =>
  globalThis.crypto?.randomUUID?.() ||
  "request-" +
    Date.now().toString(36) +
    "-" +
    Math.random().toString(36).slice(2);
window.api = async (u, o = {}) => {
  let r;
  try {
    r = await fetch(u, o);
  } catch {
    throw Error("Connection interrupted. Please retry.");
  }
  const j = await r.json().catch(() => ({}));
  if (!r.ok) {
    const d = j.detail;
    throw Error(
      typeof d === "string"
        ? d
        : d?.error ||
            (Array.isArray(d)
              ? d.map((x) => x.msg).join("; ")
              : "Request failed. Please try again."),
    );
  }
  return j;
};
window.toast = (text, error = false) => {
  const t = document.querySelector("#toast");
  t.textContent = text;
  t.classList.toggle("error", error);
  clearTimeout(t._timer);
  t._timer = setTimeout(() => (t.textContent = ""), 6500);
};
window.jsonPost = (body) => ({
  method: "POST",
  headers: { "Content-Type": "application/json" },
  body: JSON.stringify(body),
});
window.statusInfo = (s) =>
  ({
    needs_review: ["In review", "amber"],
    needs_clarification: ["Clarification", "amber"],
    approved: ["Approved", "green"],
    blocked: ["Blocked", "red"],
    processing_failed: ["Failed", "red"],
    processing: ["Processing", ""],
    checks_passed: ["Check passed", "green"],
    reviewed: ["Reviewed", "green"],
    missing: ["Missing", "amber"],
    not_applicable: ["Optional", ""],
  })[s] || [s || "Pending", ""];
window.badge = (s) => {
  const [t, c] = statusInfo(s);
  return `<span class="badge ${c}">${esc(t)}</span>`;
};
document
  .querySelectorAll("[data-icon]")
  .forEach((el) => (el.innerHTML = icon(el.dataset.icon)));

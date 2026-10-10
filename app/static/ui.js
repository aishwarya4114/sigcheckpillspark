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
  lock: "M6 11h12v10H6z M8 11V8a4 4 0 0 1 8 0v3 M12 15v2",
  search: "M11 18a7 7 0 1 1 0-14 7 7 0 0 1 0 14z M16 16l5 5",
  x: "M7 7l10 10 M17 7L7 17",
  dash: "M7 12h10",
  bang: "M12 6v8 M12 18h.01",
  external: "M14 4h6v6 M20 4l-9 9 M18 14v6H4V6h6",
  clock: "M12 7v5l3 2 M21 12a9 9 0 1 1-18 0 9 9 0 0 1 18 0",
  phone: "M5 4h4l2 5-3 2a11 11 0 0 0 5 5l2-3 5 2v4a2 2 0 0 1-2 2A16 16 0 0 1 3 6a2 2 0 0 1 2-2",
  ban: "M5.6 5.6l12.8 12.8 M21 12a9 9 0 1 1-18 0 9 9 0 0 1 18 0",
};
// Pharmacist view by default; "Show technical details" reveals traces, codes, scores and admin tools.
window.techMode = () => { try { return localStorage.getItem("sigcheck_tech") === "1"; } catch { return false; } };
const applyTech = () => document.documentElement.classList.toggle("tech", window.techMode());
applyTech();
document.addEventListener("change", (e) => {
  if (e.target?.id !== "tech-toggle") return;
  try { localStorage.setItem("sigcheck_tech", e.target.checked ? "1" : "0"); } catch {}
  applyTech();
  document.dispatchEvent(new Event("sigcheck:tech"));
});
document.addEventListener("DOMContentLoaded", () => {
  const t = document.getElementById("tech-toggle");
  if (t) t.checked = window.techMode();
  document.querySelectorAll(".brand-mark").forEach((el) => {
    el.removeAttribute("data-icon");
    el.innerHTML = '<img src="/static/logo.svg" alt="" width="34" height="34">';
  });
});
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
  t.setAttribute("role", error ? "alert" : "status");
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

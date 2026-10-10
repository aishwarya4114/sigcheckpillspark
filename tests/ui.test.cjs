const { test } = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const { JSDOM } = require("jsdom");
const tick = () => new Promise((resolve) => setTimeout(resolve, 30));
const base = {
  id: "doc1",
  source: "synthetic sample",
  revision: 1,
  status: "needs_review",
  fields: {
    patient_name: {
      value: "<img src=x onerror=alert(1)>",
      source_text: "Fictional patient",
      required: true,
      status: "checks_passed",
      reasons: [],
      box: [0.1, 0.1, 0.2, 0.05],
    },
  },
  review: {},
  findings: [],
  events: [],
  sponsors: {},
  sha256: "abc",
  // the server derives readiness; this fake mirrors it
  workflow: { status: "needs_verification", label: "Needs verification", next_action: "Confirm 1 required field against the fax.", issues: [], counts: { required: 1, reviewed: 0 } },
};
function setup(page, script, fetcher) {
  const dom = new JSDOM(fs.readFileSync("app/static/" + page, "utf8"), {
    url: "http://localhost/",
    runScripts: "outside-only",
    pretendToBeVisual: true,
  });
  const w = dom.window;
  w.fetch = fetcher;
  w.setInterval = () => 0;
  w.HTMLElement.prototype.scrollIntoView = () => {};
  if (!w.HTMLDialogElement.prototype.showModal) {
    w.HTMLDialogElement.prototype.showModal = function () { this.open = true; };
    w.HTMLDialogElement.prototype.close = function () { this.open = false; };
  }
  w.eval(fs.readFileSync("app/static/ui.js", "utf8"));
  w.eval(fs.readFileSync("app/static/" + script, "utf8"));
  return dom;
}
const response = (data) => ({
  ok: true,
  json: async () => structuredClone(data),
});
test("review fields preserve typing, escape extracted values and unlock only after review", async () => {
  let doc = structuredClone(base);
  const fetcher = async (url, o = {}) => {
    if (url === "/api/samples") return response([]);
    if (url === "/api/health")
      return response({
        nicrron: "demo",
        prism: "not configured",
        ocr: "test",
      });
    if (url === "/api/documents")
      return response([
        { ...doc, patient: "Fictional patient", drug: "Metformin" },
      ]);
    if (url.includes("/fields/")) {
      doc.review.patient_name = { action: "confirm", by: "pharmacist" };
      doc.revision++;
      doc.workflow = { status: "ready_for_approval", label: "Ready to approve", next_action: "Approve the draft order.", issues: [], counts: { required: 1, reviewed: 1 } };
      return response(doc);
    }
    if (url.endsWith("/approve")) {
      doc.status = "approved";
      doc.approved = { revision: doc.revision, by: "pharmacist", at: new Date().toISOString() };
      doc.workflow = { status: "approved", label: "Approved", next_action: "Export the draft.", issues: [], counts: { required: 1, reviewed: 1 } };
      return response(doc);
    }
    return response(doc);
  };
  const dom = setup("index.html", "review.js", fetcher);
  const w = dom.window;
  await tick();
  assert(w.document.body.classList.contains("queue-mode"));
  w.document.querySelector("[data-doc]").click();
  await tick();
  assert.equal(w.document.body.classList.contains("queue-mode"), false);
  // no approve button is offered before the case is ready (state-specific footer)
  assert.equal(w.document.querySelector("[data-action=approve]"), null);
  assert(w.document.querySelector("[data-confirm-next]"));
  w.document.querySelector("[data-field]").click();
  assert.equal(w.document.querySelectorAll(".field-row img").length, 0);
  const input = w.document.querySelector("[name=reason]");
  input.focus();
  input.value = "Read from original";
  input.dispatchEvent(
    new w.KeyboardEvent("keydown", { key: "Enter", bubbles: true }),
  );
  assert.equal(w.document.activeElement, input);
  assert.equal(input.value, "Read from original");
  w.document.querySelector("[data-confirm]").click();
  await tick();
  // ready: the footer opens an attestation step; approval needs the explicit attestation
  w.document.querySelector("[data-attest]").click();
  await tick();
  const approve = w.document.querySelector("#attest-approve");
  assert.equal(approve.disabled, true);
  const ok = w.document.querySelector("#attest-ok");
  ok.checked = true;
  ok.dispatchEvent(new w.Event("change", { bubbles: true }));
  assert.equal(approve.disabled, false);
  approve.click();
  await tick();
  assert(w.document.querySelector('a[href$="/export"]'));
  dom.window.close();
});
test("scorecard escapes case text and handles failed requests", async () => {
  const s = {
    reader: "demo",
    run_at: "2026-10-10T12:00:00Z",
    faxes: 1,
    traps_caught: { caught: 1, of: 1 },
    verified_precision: { right: 1, of: 1 },
    field_accuracy: { exact: 1, of: 1 },
    invented_values_on_absent_fields: 0,
    rows: [
      {
        fax: "sample.png",
        trap: "<img src=x>",
        status: "blocked",
        flagged: true,
      },
    ],
  };
  const dom = setup("scorecard.html", "scorecard.js", async () => response(s));
  await tick();
  assert.equal(dom.window.document.querySelectorAll("td img").length, 0);
  assert.match(
    dom.window.document.querySelector("table").textContent,
    /<img src=x>/,
  );
  dom.window.close();
  const failed = setup("scorecard.html", "scorecard.js", async () => {
    throw Error("offline");
  });
  await tick();
  assert(failed.window.document.querySelector("#retry-score"));
  failed.window.close();
});
test("phone retry reuses upload key and renders server strings as text", async () => {
  let attempts = [];
  const dom = setup("scan.html", "scan.js", async (url, o = {}) => {
    if (url === "/api/documents" && o.method === "POST") {
      attempts.push(o.headers["Idempotency-Key"]);
      if (attempts.length === 1) throw Error("network");
      return response({ id: "doc1" });
    }
    return response([
      {
        id: "doc1",
        patient: "<script>bad</script>",
        drug: "Synthetic drug",
        revision: 1,
        status: "needs_review",
      },
    ]);
  });
  const w = dom.window;
  w.URL.createObjectURL = () => "blob:test";
  w.URL.revokeObjectURL = () => {};
  const file = new w.File(["test"], "rx.png", { type: "image/png" });
  const picker = w.document.querySelector("#file");
  Object.defineProperty(picker, "files", { value: [file] });
  picker.dispatchEvent(new w.Event("change"));
  w.document.querySelector("#send").click();
  await tick();
  assert.match(w.document.querySelector("#message").textContent, /retry/);
  w.document.querySelector("#send").click();
  await tick();
  assert.equal(attempts.length, 2);
  assert.equal(attempts[0], attempts[1]);
  assert.equal(w.document.querySelectorAll("#uploads script").length, 0);
  assert.equal(w.document.querySelector("#preview").hidden, true);
  dom.window.close();
});

# Fax checks the verifier must implement

These five checks are about the **fax itself**, not the drug. They catch problems that every reader can agree on and still be wrong about, so **reader agreement never clears them**.

Every flag the verifier emits carries: `check` (ID below), `code` (answer-key reason code), `field` (or `_order`), `page`, a plain-English `reason`, and `evidence` (a box on the original page image, from Tesseract word boxes or the measured region). Statuses combine as **blocked > needs pharmacist > verified**: one blocked flag blocks the order.

| Check ID | Answer-key code | Status | Test faxes |
|---|---|---|---|
| NOISE_NEAR_CRITICAL | `noise_near_critical` (+ `ambiguous_digit`) | needs pharmacist | rx_026, rx_027 |
| TRUNCATED_EDGE | `truncated_edge` | needs pharmacist | rx_028 |
| PAGE_COUNT_MISMATCH | `page_count_mismatch` | **order blocked** | rx_029 |
| MISSING_PATIENT_ID | `missing_patient_id` | needs pharmacist | rx_030 (page 2) |
| CONFLICTING_DIRECTIONS | `conflicting_directions` | needs pharmacist | rx_031 |

The 5 clean faxes (rx_001–rx_005) and the other traps must **not** trigger these checks. The one exception is a heavy clean fax (e.g. rx_005) where a streak genuinely crosses a critical field: flagging that is correct, and the scorecard reports it as a flag on a clean fax rather than hiding it.

---

## 1. NOISE_NEAR_CRITICAL

**Why.** ISMP reported a specialty-pharmacy error in which fax noise made "0.6 mg" of somatropin look like "0.8 mg"; the wrong dose was dispensed. ISMP's recommendation: review faxes for noise and call the prescriber when noise sits near the medication or the directions. Source: ISMP column in *America's Pharmacist*, October 2022 — https://digitaleditions.sheridan.com/article/Medication+Safety/4346835/761802/article.html

**What to measure** (on the binarized page, before deskew so boxes match the original):
1. Critical regions = OCR/reader evidence boxes for `drug_name`, `strength`, `directions`, each expanded by 30 px.
2. Noise density in a region = black pixels that belong to tiny connected components (area < 4 px, i.e. specks rather than strokes) ÷ region area.
3. Page baseline = the same density over the page with all text boxes masked out.
4. Streaks = rows where ≥ 80% of the page width is ink.

**Rule.** Flag a field when its region's noise density is ≥ 3× the page baseline **and** ≥ 0.01, **or** a streak crosses its region. Thresholds must be tuned on rx_001–rx_031 and written into the scorecard, not hard-coded silently.

**Outcome.** Needs pharmacist on that field, **even if all three readers agree**. Reason names the field and shows the noisy region. If the reading is a digit whose plausible alternatives are also real strengths of that drug (rx_027: warfarin 5 mg vs 6 mg, both in RxNorm), add `ambiguous_digit` and list both strengths; the reference data cannot settle it, so never pick one.

## 2. TRUNCATED_EDGE

**Why.** Patient Safety Solutions (citing an ISMP Canada 2012 bulletin) describes a faxed order whose left edge was cut off, so "dalteparin" lost its first letters and read as "heparin". The same article also covers sheets lost in transmission (see check 3). Source: https://www.patientsafetysolutions.com/docs/June_19_2012_More_Problems_with_Faxed_Orders.htm

**What to measure.**
1. Ink touching a page edge: any OCR word box with x0 ≤ 5 px (or x1 ≥ width − 5, y0 ≤ 5, y1 ≥ height − 5) inside a text row.
2. Missing blocks: the transmit header line, or the prescriber block (name with credential + NPI) not found in the top 20% of page 1.
3. The drug name's evidence box starts at the edge.

**Outcome.** Needs pharmacist on every critical field that touches the edge (rx_028: `drug_name`, `prescriber_name`, `prescriber_npi`). If the visible fragment matches a *different* drug in our reference data (rx_028: "citalopram" is visible, the truth is escitalopram), say so in the reason. **Never resolve a truncated name by matching it to the closest drug.** The visible name is exactly the dangerous one.

## 3. PAGE_COUNT_MISMATCH

**Why.** Same source as check 2: two sheets stuck together and only one was transmitted, so part of the order never arrived. https://www.patientsafetysolutions.com/docs/June_19_2012_More_Problems_with_Faxed_Orders.htm

**What to measure.** Parse every page's header for `P.00x/00N` or `PAGE x/N` (tolerate OCR noise: `P(?:AGE)?[.\s]*0*(\d+)\s*/\s*0*(\d+)`). N = the largest total seen.

**Outcome.**
- Pages received < N, or a page number missing or duplicated → **order blocked**: "waiting for page k of N". A missing page can hold another prescription or a change to this one.
- Header unreadable on any page → needs pharmacist ("page count unknown"), never a pass.
- rx_029: page 1 is complete and otherwise clean, so this is its only flag.

## 4. MISSING_PATIENT_ID

**Why.** The Ontario College of Pharmacists describes two prescriptions faxed together for different patients. Staff assumed both were for the same patient, partly because the pages had a name but no second identifier such as a date of birth. Source: OCP, *Focus on Error Prevention*, Summer 2021 — https://ocpinfo.com/ocp-resources/focus-on-error-prevention-summer-2021/ (an OCP case, not an ISMP publication).

**What to measure.** For every page that contains a prescription (an Rx/drug line was read), check that the patient's name **and** DOB were read *on that page*.

**Outcome.**
- Missing name or DOB on a page → needs pharmacist on that page's `patient_name` / `patient_dob` (rx_030 page 2).
- **Never carry the patient over from another page**, even when the prescriber and fax header match.
- Two pages with different patients → needs pharmacist on both: they are separate orders.

## 5. CONFLICTING_DIRECTIONS

**Why.** ISMP Safety Briefs (reported by NABP) describe prescriptions where the Sig said one thing and the prescriber's note field said another (for example "1 tablet PO TID" in the Sig, but BID for 7 days and then TID in the notes). Staff may miss the second set. The source cases are electronic prescriptions; the same split happens on faxes between Sig and Notes. Source: https://nabp.pharmacy/news/blog/regulatory_news/ismp-safety-briefs-electronic-prescription-prescribers-note-field-error/

**What to measure.** Parse dose, frequency, route and duration from the Sig, then look for the same in Notes / comments / any free text. A note that states its own dose or frequency counts as a second set of directions.

**Outcome.** If the note's dose, frequency, route or duration differs from the Sig → needs pharmacist on `directions`, showing **both** texts with their boxes. Never choose one, and never merge them into a titration schedule. rx_031: Sig "1 tab PO BID" vs Notes "Take 1 tab PO TID after 7 days".

---

### Test contract

`python -m faxfill.check_answers` verifies the answer keys: pages exist, each fax carries only its declared trap, a missing page is expected to block, and the ambiguous misread is a real strength. The verifier's own tests must show, for each check, that it fires on its test fax and does not fire on rx_001–rx_004.

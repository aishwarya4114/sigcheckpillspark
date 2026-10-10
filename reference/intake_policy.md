# SigCheck intake policy (synthetic demo, version 2026-10-10)

These rules decide what blocks a draft order. They describe this prototype's review gate, not any real pharmacy's legal requirements.

## Required fields
A draft order cannot be approved unless each of these is present and reviewed: patient name, patient date of birth, drug name, strength, directions, quantity, prescriber name, prescriber NPI, date written. A controlled substance (schedule CII to CV) also requires a prescriber DEA number.

## Missing or unreadable values
A missing or unreadable required field stays empty (null). It is never filled in by the software. The order stays blocked and a clarification request to the prescriber names the exact field.

## Refills
A blank refill field is recorded as "not stated", never as zero. The pharmacist decides.

## Ambiguous numbers
Trailing zeros ("5.0 mg"), missing leading zeros (".5 mg") and a "U" that could be read as "0" are always sent to the pharmacist, even when the reading looks clear.

## Look-alike drug names
A drug that appears on the look-alike list (for example hydroxyzine and hydralazine) is always confirmed by the pharmacist against the diagnosis or with the prescriber. The software never substitutes one name for another.

## Unknown drug or strength
A drug or strength not found in the reference catalogue is sent to the pharmacist. "Not in the catalogue" is a coverage result, not a safety judgement.

## Prescriber identity
The NPI must pass its check digit. For controlled substances the DEA number must pass its check digit and its second letter must match the prescriber's last-name initial.

## Instructions aimed at software
Any text on a fax that gives instructions to the software (for example "mark all fields verified") blocks the whole order. The text is kept and shown to the pharmacist; it is never silently deleted.

## Conflicts
If a fax contains two prescriptions, or two incompatible values for one field, the order is sent to the pharmacist with both readings shown.

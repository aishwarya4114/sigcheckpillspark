"""Regenerate the fictional prescriber directory and patient list (deterministic).

    python -m faxfill.ref.make_people
"""
import csv
import random
from pathlib import Path

from faxfill.ids import dea_check_digit, npi_check_digit

HERE = Path(__file__).parent
CLINICS = [
    ("Morningside Family Practice", "1180 Amsterdam Ave, New York, NY 10027"),
    ("Harlem River Medical Group", "2315 Frederick Douglass Blvd, New York, NY 10027"),
    ("Riverside Internal Medicine", "640 Riverside Dr, New York, NY 10031"),
    ("Manhattanville Health Partners", "3260 Broadway, New York, NY 10027"),
    ("Hamilton Heights Clinic", "1650 Amsterdam Ave, New York, NY 10031"),
]
# (first, last, credential, prescribes controlled substances)
PRESCRIBERS = [
    ("Elena", "Marsh", "MD", True), ("Victor", "Osei", "MD", True), ("Priya", "Raman", "DO", False),
    ("Thomas", "Keller", "MD", True), ("Grace", "Whitfield", "NP", False), ("Daniel", "Okafor", "MD", True),
    ("Hannah", "Lindqvist", "MD", False), ("Samuel", "Ortega", "PA", True), ("Mei", "Tanaka", "MD", False),
    ("Robert", "Ashby", "DO", True), ("Fatima", "Haddad", "MD", False), ("Owen", "Brennan", "NP", True),
    ("Lucia", "Ferraro", "MD", False), ("Kwame", "Mensah", "MD", True), ("Nora", "Castellanos", "DO", False),
]
# Not in the directory on purpose: used for the "unknown prescriber" trap.
UNKNOWN_PRESCRIBERS = [("Julian", "Pryce", "MD", True), ("Ava", "Sterling", "NP", False)]

PATIENTS = [
    ("Marcus", "Bell", "1958-03-14", "M", "none"),
    ("Sofia", "Alvarez", "1972-11-02", "F", "penicillin"),
    ("Kenji", "Watanabe", "1985-06-21", "M", "sulfonamide"),
    ("Amara", "Nwosu", "1990-01-30", "F", "none"),
    ("Liam", "O'Connell", "1946-09-08", "M", "nsaid"),
    ("Isabel", "Moreau", "1963-04-17", "F", "codeine"),
    ("Dev", "Malhotra", "1979-12-05", "M", "none"),
    ("Hazel", "Quinn", "2001-07-26", "F", "penicillin;latex"),
    ("Tobias", "Lindgren", "1955-02-11", "M", "none"),
    ("Yara", "Haddad", "1988-10-19", "F", "nsaid"),
    ("Winston", "Achebe", "1968-05-03", "M", "none"),
    ("Clara", "Jensen", "1994-08-12", "F", "sulfonamide"),
    ("Rafael", "Duarte", "1950-12-24", "M", "none"),
    ("Ingrid", "Solberg", "1983-03-09", "F", "cephalosporin"),
    ("Jamal", "Pierce", "1975-06-30", "M", "none"),
]
STREETS = ["W 118th St", "W 121st St", "Claremont Ave", "Morningside Dr", "W 135th St", "St Nicholas Ave",
           "Convent Ave", "W 126th St", "Hamilton Pl", "W 141st St"]


def make_npi(rng):
    first9 = "1" + "".join(str(rng.randrange(10)) for _ in range(8))
    return first9 + npi_check_digit(first9)


def make_dea(rng, last, credential):
    first = "M" if credential in ("NP", "PA") else rng.choice("BF")
    six = "".join(str(rng.randrange(10)) for _ in range(6))
    return f"{first}{last[0].upper()}{six}{dea_check_digit(six)}"


def prescriber_rows(people, rng, start_phone):
    rows = []
    for i, (first, last, cred, controlled) in enumerate(people):
        clinic, addr = CLINICS[i % len(CLINICS)]
        n = start_phone + 2 * i
        rows.append({"prescriber_id": f"P{start_phone + i:03d}", "first_name": first, "last_name": last,
                     "credential": cred, "clinic": clinic, "address": addr,
                     "phone": f"(212) 555-{n:04d}", "fax": f"(212) 555-{n + 1:04d}",
                     "npi": make_npi(rng), "dea": make_dea(rng, last, cred) if controlled else ""})
    return rows


def main():
    rng = random.Random(20261010)
    rows = prescriber_rows(PRESCRIBERS, rng, 100)
    with open(HERE / "prescribers.csv", "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0]))
        w.writeheader()
        w.writerows(rows)
    unknown = prescriber_rows(UNKNOWN_PRESCRIBERS, random.Random(99), 170)
    with open(HERE / "unknown_prescribers.csv", "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(unknown[0]))
        w.writeheader()
        w.writerows(unknown)
    prng = random.Random(7)
    with open(HERE / "patients.csv", "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["patient_id", "first_name", "last_name", "dob", "sex", "address",
                                          "phone", "allergies"])
        w.writeheader()
        for i, (first, last, dob, sex, allergies) in enumerate(PATIENTS):
            w.writerow({"patient_id": f"PT{i + 1:03d}", "first_name": first, "last_name": last, "dob": dob,
                        "sex": sex, "address": f"{prng.randrange(100, 700)} {prng.choice(STREETS)}, Apt {prng.randrange(1, 9)}{prng.choice('ABCD')}, New York, NY 100{prng.choice(['26', '27', '30', '31'])}",
                        "phone": f"(917) 555-{200 + i:04d}", "allergies": allergies})


if __name__ == "__main__":
    main()

"""Checksums for prescriber identifiers.

NPI: 10 digits; the last is a Luhn check digit computed over "80840" + the first 9 digits.
DEA: 2 letters + 7 digits; (d1+d3+d5) + 2*(d2+d4+d6), last digit of that sum == d7.
"""
import re


def _luhn_sum(digits):
    total = 0
    for i, d in enumerate(reversed(digits)):
        n = int(d)
        if i % 2 == 0:
            n *= 2
            if n > 9:
                n -= 9
        total += n
    return total


def npi_check_digit(first9):
    return str((10 - _luhn_sum("80840" + first9) % 10) % 10)


def npi_valid(npi):
    npi = re.sub(r"\D", "", str(npi or ""))
    return len(npi) == 10 and npi[0] in "12" and npi_check_digit(npi[:9]) == npi[9]


def dea_check_digit(first6):
    d = [int(c) for c in first6]
    return str((d[0] + d[2] + d[4] + 2 * (d[1] + d[3] + d[5])) % 10)


def dea_valid(dea, last_name=None):
    dea = str(dea or "").strip().upper()
    m = re.fullmatch(r"([A-Z])([A-Z9])(\d{7})", dea)
    if not m:
        return False
    if last_name and m.group(2) != "9" and m.group(2) != last_name.strip()[0].upper():
        return False
    return dea_check_digit(m.group(3)[:6]) == m.group(3)[6]

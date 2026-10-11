# -*- coding: utf-8 -*-
"""Generate a folder of FICTITIOUS sample data so the app can be tried out.

    python tools/make_sample_data.py              # -> ./sample_data, current month
    python tools/make_sample_data.py --month 09 --year 2026 --out demo

All names and account numbers are randomly generated.
"""
import argparse
import datetime
import os
import random
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from dbf_merger.dbf import create_dbf  # noqa: E402

FIRST = ["Ana", "Mihai", "Elena", "Radu", "Ioana", "Şerban", "Zoë", "Lukas", "Marta", "Tomáš",
         "Irina", "Victor", "Sofia", "Ştefan", "Laura", "Paul"]
LAST = ["Popa", "Müller", "Novák", "Dumitrescu", "Rossi", "Ţurcanu", "Kovács", "Martin",
        "Georgescu", "Weiß", "Petrov", "Lungu"]

# Main structure (the base file of every group)
BASE_FIELDS = [("ACCOUNT", "C", 24, 0), ("NAME", "C", 40, 0), ("AMOUNT", "N", 12, 2),
               ("AMT_BASE", "N", 12, 2), ("AMT_BONUS", "N", 10, 2), ("AMT_OTHER", "N", 10, 2),
               ("PAYDATE", "D", 8, 0)]
# Supplement files come from another system: wider fields, no AMT_OTHER,
# amount stored as text – the merge has to convert them.
SUPP_FIELDS = [("ACCOUNT", "C", 30, 0), ("NAME", "C", 50, 0), ("AMOUNT", "C", 15, 0),
               ("AMT_BASE", "N", 14, 2), ("AMT_BONUS", "N", 12, 2), ("PAYDATE", "D", 8, 0)]
EXCL_FIELDS = [("ACCOUNT", "C", 24, 0), ("REASON", "C", 30, 0)]

GROUPS = [("group_a", "ga"), ("group_b", "gb"), ("group_c", "gc")]


def person(rnd, n, paydate, text_amount=False, other=True):
    base = round(rnd.uniform(800, 4500), 2)
    bonus = round(rnd.choice([0, 0, 0, rnd.uniform(50, 400)]), 2)
    oth = round(rnd.choice([0, 0, rnd.uniform(10, 150)]), 2) if other else 0.0
    total = round(base + bonus + oth, 2)
    row = {"ACCOUNT": "ACC%010d" % n,
           "NAME": "%s %s" % (rnd.choice(FIRST), rnd.choice(LAST)),
           "AMOUNT": ("%.2f" % total) if text_amount else total,
           "AMT_BASE": base, "AMT_BONUS": bonus, "PAYDATE": paydate}
    if other:
        row["AMT_OTHER"] = oth
    return row


def make(out, mm, yyyy, seed=42):
    rnd = random.Random(seed)
    yy = yyyy[2:]
    paydate = datetime.date(int(yyyy), int(mm), 15)
    counter = iter(range(1000, 10 ** 9))
    files = []
    for unit, sub in (("U1", ""), ("U2", "unit2/")):
        excluded = []
        for folder, code in GROUPS:
            base_rows = [person(rnd, next(counter), paydate) for _ in range(rnd.randint(25, 40))]
            sup_rows = [person(rnd, next(counter), paydate, text_amount=True, other=False)
                        for _ in range(rnd.randint(3, 8))]
            add_rows = [person(rnd, next(counter), paydate) for _ in range(rnd.randint(1, 4))]
            excluded += rnd.sample(base_rows, 3) + rnd.sample(sup_rows, 1)

            p = os.path.join(out, folder, "base_%s_%s_%s%s_1.dbf" % (code, unit, mm, yy))
            os.makedirs(os.path.dirname(p), exist_ok=True)
            create_dbf(p, BASE_FIELDS, base_rows, deleted={len(base_rows) - 1})
            files.append(p)

            p = os.path.join(out, "supplements", mm + yy, sub.rstrip("/"),
                             "sup_%s_%s%s.dbf" % (code, mm, yy))
            os.makedirs(os.path.dirname(p), exist_ok=True)
            create_dbf(p, SUPP_FIELDS, sup_rows)
            files.append(p)

            p = os.path.join(out, "additions", "add_%s_%s.dbf" % (code, unit))
            os.makedirs(os.path.dirname(p), exist_ok=True)
            create_dbf(p, BASE_FIELDS, add_rows)
            files.append(p)

        excl_rows = [{"ACCOUNT": r["ACCOUNT"], "REASON": "closed"} for r in excluded]
        excl_rows.append({"ACCOUNT": "ACC9999999999", "REASON": "not in any file"})
        excl_rows.append({"ACCOUNT": "", "REASON": "empty key"})
        p = os.path.join(out, "exclusions_%s.dbf" % unit)
        create_dbf(p, EXCL_FIELDS, excl_rows)
        files.append(p)
    return files


def main():
    today = datetime.date.today()
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--out", default="sample_data")
    ap.add_argument("--month", default="%02d" % today.month)
    ap.add_argument("--year", default=str(today.year))
    a = ap.parse_args()
    files = make(a.out, "%02d" % int(a.month), a.year)
    print("Created %d sample files in %s" % (len(files), os.path.abspath(a.out)))


if __name__ == "__main__":
    main()

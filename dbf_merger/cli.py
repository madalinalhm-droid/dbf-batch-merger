# -*- coding: utf-8 -*-
"""Headless batch run – processes every group of one unit and period.

    python -m dbf_merger.cli --unit U1 --month 09 --year 2026
    python -m dbf_merger.cli --config my_config.json --work-folder D:/data ...

Exit code 0 = all groups processed and every cross-check matched,
1 = at least one error or cross-check difference.
"""
import argparse
import os
import sys

from . import core


def _log(text, tag=None):
    if tag == "sep":
        print("-" * 72)
    else:
        print(text)


def run(cfg, unit, mm, yyyy, work_folder):
    if unit not in cfg["units"]:
        _log("Unknown unit %s (configured: %s)" % (unit, ", ".join(cfg["units"])))
        return 1
    amount_field = cfg.get("amount_field")
    cross = cfg.get("cross_check_fields") or []
    key_field = cfg.get("record_key_field", "ACCOUNT")

    excl, _ = core.find_file(work_folder,
                             core.expand(cfg["units"][unit]["exclusions"], cfg, unit, mm, yyyy))
    if not excl:
        _log("Exclusion list not found for unit %s." % unit)
        return 1
    keys, empty, deleted = core.load_keys(excl, cfg["exclusion_key_field"])
    _log("Exclusion list %s: %d distinct keys (%d empty, %d deleted rows ignored)"
         % (os.path.basename(excl), len(keys), empty, deleted))

    out_dir = core.output_dir(work_folder, unit, mm, yyyy)
    report = [core.report_header(cfg, unit, mm, yyyy, excl)]
    problems = 0
    for g in cfg["groups"]:
        paths = {k: core.find_file(work_folder, core.expand(g.get(k, ""), cfg, unit, mm, yyyy))[0]
                 for k, _ in core.SOURCES}
        if not paths["base"]:
            _log("%s: no base file for this period – skipped" % g["name"])
            continue
        heading = "%s   (%s %s, %s.%s)" % (g["name"].upper(), cfg["units"][unit]["name"],
                                            unit, mm, yyyy)
        _log("", "sep")
        _log(heading)
        extra = [("supplement", paths["supplement"]), ("additions", paths["additions"])]
        if paths["extra"]:
            extra.append(("extra", paths["extra"]))
        out = os.path.join(out_dir, os.path.basename(paths["base"]))
        try:
            ctx = core.merge_group(paths["base"], extra, _log, amount_field, cross)
            lines = core.stage1_lines(ctx["st"], amount_field, cross)
            st, _removed = core.remove_excluded(ctx, keys, key_field, out)
            lines += core.stage2_lines(st, amount_field, cross, key_field,
                                       os.path.basename(out))
        except Exception as ex:  # report and continue with the next group
            _log("   ERROR: %s" % ex)
            report += ["", heading, "   ERROR: %s" % ex]
            problems += 1
            continue
        problems += sum(1 for _, tag in lines if tag == "err")
        for text, _tag in lines:
            _log(text)
        report += ["", heading] + [t for t, _ in lines]

    report_path = os.path.join(out_dir, "report.txt")
    with open(report_path, "w", encoding="utf-8") as fh:
        fh.write("\n".join(report) + "\n")
    _log("", "sep")
    _log("Output and report: %s" % out_dir)
    return 1 if problems else 0


def main(argv=None):
    p = argparse.ArgumentParser(description="Merge monthly DBF files and remove exclusions.")
    p.add_argument("--config", default="config.json",
                   help="JSON settings file (created with defaults if missing)")
    p.add_argument("--work-folder", help="overrides work_folder from the config")
    p.add_argument("--unit", required=True)
    p.add_argument("--month", required=True, help="two digits, e.g. 09")
    p.add_argument("--year", required=True, help="four digits, e.g. 2026")
    a = p.parse_args(argv)
    cfg = core.load_config(a.config)
    work = a.work_folder or cfg.get("work_folder", ".")
    if not os.path.isabs(work):
        work = os.path.join(os.path.dirname(os.path.abspath(a.config)), work)
    return run(cfg, a.unit, "%02d" % int(a.month), a.year, os.path.normpath(work))


if __name__ == "__main__":
    sys.exit(main())

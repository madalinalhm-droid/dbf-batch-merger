# -*- coding: utf-8 -*-
"""Batch merge logic, independent of any user interface.

For every *group* (one data provider, e.g. one partner that sends a monthly
file) the process runs in two stages:

  Stage 1 – merge:  base file + supplement file + additions file (+ optional
                    extra file). Nothing is removed and nothing is written yet,
                    so the operator can check record counts and totals first.
  Stage 2 – filter: drop every record whose key appears in the exclusion
                    list, then write the result to a separate output folder.
                    Source files are never modified.

Every amount is totalled twice – from the main amount field and from the sum
of several component fields – as a cross-check.
"""
import datetime
import glob
import json
import os
import shutil

from .dbf import Dbf, DbfError, make_mapper

__all__ = [
    "DEFAULT_CONFIG", "load_config", "save_config", "expand", "find_file",
    "norm_key", "fmt_amount", "load_keys", "merge_group", "remove_excluded",
    "process_group", "fetch_files", "stage1_lines", "stage2_lines", "compare_amounts",
    "SOURCES",
]

# Source slots of a group, in processing order: (config key, label)
SOURCES = (("base", "Base file"), ("supplement", "Supplement"),
           ("additions", "Additions"), ("extra", "Extra"))


def _group(name, folder, code):
    """Default group definition following the sample naming convention."""
    return {
        "name": name,
        "folder": folder,
        "file_name": "base_" + code + "_{unit}_{mm}{yy}*.dbf",
        "base": folder + "/base_" + code + "_{unit}_{mm}{yy}*.dbf",
        "supplement": "supplements/{mm}{yy}/{sub}sup_" + code + "_{mm}{yy}.dbf",
        "additions": "additions/add_" + code + "_{unit}*.dbf",
    }


DEFAULT_CONFIG = {
    "_help": [
        "Paths are relative to work_folder (or absolute).",
        "{unit} = unit code, {mm} = month (09), {yy} = short year (26), {yyyy} = year (2026).",
        "{sub} = the unit's 'sub' value (e.g. an extra subfolder for that unit).",
        "* matches any text (add_ga_U1*.dbf also finds add_ga_U1_0926_1.dbf).",
        "monthly_source = folder with the raw monthly files ('Fetch files' copies them here).",
        "amount_field = numeric field totalled in the report.",
        "cross_check_fields = fields summed separately to cross-check amount_field.",
    ],
    "work_folder": "./sample_data",
    "monthly_source": "",
    "amount_field": "AMOUNT",
    "cross_check_fields": ["AMT_BASE", "AMT_BONUS", "AMT_OTHER"],
    "record_key_field": "ACCOUNT",
    "exclusion_key_field": "ACCOUNT",
    "units": {
        "U1": {"name": "Unit 1", "exclusions": "exclusions_U1.dbf", "sub": ""},
        "U2": {"name": "Unit 2", "exclusions": "exclusions_U2.dbf", "sub": "unit2/"},
    },
    "groups": [
        _group("Group A", "group_a", "ga"),
        _group("Group B", "group_b", "gb"),
        _group("Group C", "group_c", "gc"),
    ],
}


def _copy(obj):
    return json.loads(json.dumps(obj))


def complete_config(cfg):
    """Add any key or group present in DEFAULT_CONFIG but missing from `cfg`,
    without touching what the user customised. Returns True if changed."""
    changed = False
    for key, val in DEFAULT_CONFIG.items():
        if key == "_help":
            continue
        if key not in cfg:
            cfg[key] = _copy(val)
            changed = True
    by_name = {g.get("name"): g for g in cfg.get("groups", [])}
    for g_new in DEFAULT_CONFIG["groups"]:
        existing = by_name.get(g_new["name"])
        if existing is None:
            continue  # the user may have removed or renamed default groups
        for key, val in g_new.items():
            if key not in existing:
                existing[key] = val
                changed = True
    return changed


def load_config(path):
    """Load the JSON config, creating it with defaults on first run."""
    if not os.path.exists(path):
        save_config(path, DEFAULT_CONFIG)
        return _copy(DEFAULT_CONFIG)
    with open(path, "r", encoding="utf-8") as f:
        cfg = json.load(f)
    if complete_config(cfg):
        try:
            save_config(path, cfg)
        except OSError:
            pass
    return cfg


def save_config(path, cfg):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(cfg, f, ensure_ascii=False, indent=2)


def expand(pattern, cfg, unit, mm, yyyy):
    """Fill the placeholders of a path pattern."""
    sub = cfg.get("units", {}).get(unit, {}).get("sub", "")
    return pattern.format(unit=unit, mm=mm, yy=yyyy[2:], yyyy=yyyy, sub=sub)


def find_file(root, pattern):
    """Return (path, number_of_matches). With several matches, the newest wins."""
    if not pattern:
        return None, 0
    pattern = pattern.replace("\\", os.sep).replace("/", os.sep)
    full = pattern if os.path.isabs(pattern) else os.path.join(root, pattern)
    matches = [m for m in glob.glob(full) if os.path.isfile(m)]
    if not matches:
        return None, 0
    matches.sort(key=os.path.getmtime, reverse=True)
    return os.path.normpath(matches[0]), len(matches)


def norm_key(raw):
    """Normalise a key value: trimmed, upper case, '00123' == '123.00'."""
    s = raw.decode("latin-1").strip()
    try:
        f = float(s)
        if f.is_integer():
            return str(int(f))
    except ValueError:
        pass
    return s.upper()


def fmt_amount(v):
    """1234567.5 -> '1,234,567.50'."""
    return "{:,.2f}".format(v)


def parse_amount(raw):
    s = raw.decode("latin-1", "replace").strip()
    if not s:
        return 0.0
    try:
        return float(s.replace(",", "."))
    except ValueError:
        return 0.0


def _amount(rec, fl):
    return parse_amount(rec[fl["off"]: fl["off"] + fl["len"]]) if fl else 0.0


def _amount_alt(rec, fls):
    return sum(parse_amount(rec[fl["off"]: fl["off"] + fl["len"]]) for fl in fls)


def compare_amounts(main, alt):
    """(equal?, absolute difference)."""
    diff = abs(alt - main)
    return diff < 0.005, diff


def load_keys(path, field):
    """Distinct keys of the exclusion list -> (keys, empty_rows, deleted_rows)."""
    t = Dbf(path)
    fl = t.field(field)
    if fl is None:
        raise DbfError("Field %s does not exist in %s" % (field, path))
    keys, empty, deleted = set(), 0, 0
    for r in t.records:
        if r[:1] == b"*":
            deleted += 1
            continue
        k = norm_key(r[fl["off"]: fl["off"] + fl["len"]])
        if k:
            keys.add(k)
        else:
            empty += 1
    return keys, empty, deleted


def merge_group(base_path, extra_sources, log, amount_field=None, cross_fields=None):
    """STAGE 1: read the base file and append the extra sources onto it.

    extra_sources -- list of (label, path or None)
    Removes nothing and writes nothing. Returns a context used by
    remove_excluded()."""
    base = Dbf(base_path)
    af = base.field(amount_field) if amount_field else None
    if amount_field and af is None:
        log("   warning: amount field «%s» not found in %s – no totals for this group"
            % (amount_field, os.path.basename(base_path)), "warn")
    af_alt = []
    for name in (cross_fields or []):
        fl = base.field(name)
        if fl is None:
            log("   warning: cross-check field «%s» not found in %s"
                % (name, os.path.basename(base_path)), "warn")
        else:
            af_alt.append(fl)

    records = [r for r in base.records if r[:1] != b"*"]
    st = {"base": len(records)}
    if af:
        st["amount_base"] = sum(_amount(r, af) for r in records)
    if af_alt:
        st["amount_alt_base"] = sum(_amount_alt(r, af_alt) for r in records)
    for label, path in extra_sources:
        if not path:
            st[label] = None
            continue
        src = Dbf(path)
        mapper, missing = make_mapper(base, src)
        if missing:
            log("   warning: %s has no field(s) %s (left empty)"
                % (os.path.basename(path), ", ".join(missing)), "warn")
        new = [mapper(r) for r in src.records if r[:1] != b"*"]
        records.extend(new)
        st[label] = len(new)
        if af:
            st["amount_" + label] = sum(_amount(r, af) for r in new)
        if af_alt:
            st["amount_alt_" + label] = sum(_amount_alt(r, af_alt) for r in new)
    st["total_merged"] = len(records)
    if af:
        st["amount_merged"] = sum(_amount(r, af) for r in records)
    if af_alt:
        st["amount_alt_merged"] = sum(_amount_alt(r, af_alt) for r in records)
    return {"base": base, "base_path": base_path, "records": records, "st": st,
            "af": af, "af_alt": af_alt}


def remove_excluded(ctx, keys, key_field, out_path):
    """STAGE 2: drop records whose key is in `keys` and write `out_path`.
    Does not modify ctx, so it can be repeated. Returns (stats, removed_keys)."""
    base, records, af, af_alt = ctx["base"], ctx["records"], ctx["af"], ctx["af_alt"]
    kf = base.field(key_field)
    if kf is None:
        raise DbfError("Field %s does not exist in %s" % (key_field, ctx["base_path"]))
    if os.path.normcase(os.path.abspath(out_path)) == \
            os.path.normcase(os.path.abspath(ctx["base_path"])):
        raise DbfError("The output file would overwrite the source file.")
    kept, removed = [], []
    amount_removed = amount_alt_removed = 0.0
    for r in records:
        k = norm_key(r[kf["off"]: kf["off"] + kf["len"]])
        if k and k in keys:
            removed.append(k)
            amount_removed += _amount(r, af)
            amount_alt_removed += _amount_alt(r, af_alt)
        else:
            kept.append(r)
    base.save(out_path, kept)
    st = dict(ctx["st"])
    st["removed"], st["final"] = len(removed), len(kept)
    if af:
        st["amount_removed"] = amount_removed
        st["amount_final"] = st["amount_merged"] - amount_removed
    if af_alt:
        st["amount_alt_removed"] = amount_alt_removed
        st["amount_alt_final"] = st["amount_alt_merged"] - amount_alt_removed
    return st, removed


def process_group(base_path, extra_sources, keys, key_field, out_path, log,
                  amount_field=None, cross_fields=None):
    """Both stages in one call (used by the CLI and the tests)."""
    ctx = merge_group(base_path, extra_sources, log, amount_field, cross_fields)
    return remove_excluded(ctx, keys, key_field, out_path)


def fetch_files(source_root, groups, cfg, unit, mm, yyyy, dest_root, log):
    """Copy each group's base file from the monthly source folder into the
    group's folder under dest_root. Returns (copied, unknown_dbf_files)."""
    copied = 0
    matched = set()
    for g in groups:
        pattern = g.get("file_name")
        if not pattern:
            continue
        path, n = find_file(source_root, expand(pattern, cfg, unit, mm, yyyy))
        if not path:
            continue
        matched.add(os.path.basename(path).lower())
        if n > 1:
            log("%s: %d matching files in source, using the newest: %s"
                % (g["name"], n, os.path.basename(path)), "warn")
        dest_dir = os.path.join(dest_root, g.get("folder") or "")
        os.makedirs(dest_dir, exist_ok=True)
        dest = os.path.join(dest_dir, os.path.basename(path))
        if os.path.normcase(os.path.abspath(path)) == os.path.normcase(os.path.abspath(dest)):
            continue
        shutil.copy2(path, dest)
        log("%s: fetched %s" % (g["name"], os.path.basename(path)), "ok")
        copied += 1
    unknown = []
    try:
        for f in os.listdir(source_root):
            if f.lower().endswith(".dbf") and f.lower() not in matched:
                unknown.append(f)
    except OSError:
        pass
    return copied, unknown


# ---------------------------------------------------------------------------
# Report lines – (text, style tag) pairs shared by the GUI log and the report
# ---------------------------------------------------------------------------

def _n(v):
    return "-" if v is None else str(v)


def _source_amounts(st, prefix):
    f = lambda v: "-" if v is None else fmt_amount(v)
    parts = ["base %s" % f(st.get(prefix + "_base")),
             "supplement %s" % f(st.get(prefix + "_supplement")),
             "additions %s" % f(st.get(prefix + "_additions"))]
    if (prefix + "_extra") in st:
        parts.append("extra %s" % f(st.get(prefix + "_extra")))
    return " + ".join(parts)


def _cross_check(amount_field, cross_fields, main, alt):
    name = "+".join(cross_fields)
    ok, diff = compare_amounts(main, alt)
    if ok:
        return ("     Cross-check %s: %s   ✓ equal to %s"
                % (name, fmt_amount(alt), amount_field), "ok")
    return ("     Cross-check %s: %s   ✗ DIFFERS from %s by %s"
            % (name, fmt_amount(alt), amount_field, fmt_amount(diff)), "err")


def stage1_lines(st, amount_field, cross_fields):
    counts = "base %s + supplement %s + additions %s" % (
        _n(st.get("base")), _n(st.get("supplement")), _n(st.get("additions")))
    if "extra" in st:
        counts += " + extra %s" % _n(st.get("extra"))
    lines = [("  1) AFTER MERGE (exclusions not removed yet)", "stage"),
             ("     Records: %s   (%s)" % (st["total_merged"], counts), "info")]
    if "amount_merged" in st:
        lines.append(("     GROUP TOTAL: %s" % fmt_amount(st["amount_merged"]), "total"))
        lines.append(("     (%s)" % _source_amounts(st, "amount"), "info"))
        if "amount_alt_merged" in st:
            lines.append(_cross_check(amount_field, cross_fields, st["amount_merged"],
                                      st["amount_alt_merged"]))
    return lines


def stage2_lines(st, amount_field, cross_fields, key_field, out_name):
    lines = [("  2) AFTER REMOVING EXCLUSIONS (matched by %s)" % key_field, "stage")]
    removed = "     Removed: %s records" % st["removed"]
    if "amount_removed" in st:
        removed += ", %s" % fmt_amount(st["amount_removed"])
    lines.append((removed, "info"))
    lines.append(("     Records left: %s" % st["final"], "info"))
    if "amount_final" in st:
        lines.append(("     GROUP TOTAL: %s" % fmt_amount(st["amount_final"]), "total_final"))
        if "amount_alt_final" in st:
            lines.append(_cross_check(amount_field, cross_fields, st["amount_final"],
                                      st["amount_alt_final"]))
    lines.append(("  → output file: %s" % out_name, "ok"))
    return lines


def output_dir(work_folder, unit, mm, yyyy):
    d = os.path.join(work_folder, "OUTPUT_%s_%s%s" % (unit, mm, yyyy[2:]))
    os.makedirs(d, exist_ok=True)
    return d


def report_header(cfg, unit, mm, yyyy, exclusions_path):
    return "%s (%s), period %s.%s, exclusion list %s, run %s" % (
        cfg["units"][unit]["name"], unit, mm, yyyy, exclusions_path,
        datetime.datetime.now().strftime("%Y-%m-%d %H:%M"))

# -*- coding: utf-8 -*-
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from dbf_merger import core, cli  # noqa: E402
from dbf_merger.dbf import Dbf, DbfError, create_dbf  # noqa: E402
from tools.make_sample_data import make  # noqa: E402

BASE = [("ACCOUNT", "C", 10, 0), ("NAME", "C", 20, 0), ("AMOUNT", "N", 10, 2),
        ("P1", "N", 10, 2), ("P2", "N", 10, 2)]


def quiet(*_a, **_k):
    pass


def rows(*items):
    return [{"ACCOUNT": a, "NAME": "Şerban Weiß", "AMOUNT": t, "P1": p1, "P2": t - p1}
            for a, t, p1 in items]


@pytest.fixture
def files(tmp_path):
    base = tmp_path / "base.dbf"
    create_dbf(str(base), BASE, rows(("A1", 100, 60), ("A2", 200, 150), ("A3", 50, 50),
                                     ("DEL", 999, 999)), deleted={3})
    # different structure: wider key, amount as text, no P2
    sup = tmp_path / "sup.dbf"
    create_dbf(str(sup), [("ACCOUNT", "C", 16, 0), ("AMOUNT", "C", 12, 0), ("P1", "N", 12, 2)],
               [{"ACCOUNT": "B1", "AMOUNT": "30.50", "P1": 30.5}])
    add = tmp_path / "add.dbf"
    create_dbf(str(add), BASE, rows(("C1", 10, 4)))
    excl = tmp_path / "excl.dbf"
    create_dbf(str(excl), [("ACCOUNT", "C", 10, 0)],
               [{"ACCOUNT": "A2"}, {"ACCOUNT": "B1"}, {"ACCOUNT": "ZZ"}, {"ACCOUNT": ""}])
    return tmp_path, str(base), str(sup), str(add), str(excl)


def test_merge_counts_and_totals(files):
    _, base, sup, add, _ = files
    ctx = core.merge_group(base, [("supplement", sup), ("additions", add)], quiet,
                           "AMOUNT", ["P1", "P2"])
    st = ctx["st"]
    assert st["base"] == 3            # deleted record skipped
    assert st["supplement"] == 1 and st["additions"] == 1
    assert st["total_merged"] == 5
    assert st["amount_merged"] == pytest.approx(390.5)
    # supplement has no P2, so the cross-check is short by nothing (P1 == AMOUNT there)
    assert st["amount_alt_merged"] == pytest.approx(390.5)


def test_remove_exclusions_writes_new_file(files):
    tmp, base, sup, add, excl = files
    before = open(base, "rb").read()
    keys, empty, deleted = core.load_keys(excl, "ACCOUNT")
    assert keys == {"A2", "B1", "ZZ"} and empty == 1 and deleted == 0
    out = str(tmp / "out" / "result.dbf")
    os.makedirs(os.path.dirname(out))
    st, removed = core.process_group(base, [("supplement", sup), ("additions", add)], keys,
                                     "ACCOUNT", out, quiet, "AMOUNT", ["P1", "P2"])
    assert sorted(removed) == ["A2", "B1"]
    assert st["final"] == 3
    assert st["amount_final"] == pytest.approx(160.0)
    result = Dbf(out)
    accounts = [r[1:11].decode().strip() for r in result.records]
    assert accounts == ["A1", "A3", "C1"]
    assert open(base, "rb").read() == before          # source untouched


def test_text_is_copied_byte_for_byte(files):
    tmp, base, *_ = files
    ctx = core.merge_group(base, [], quiet)
    out = str(tmp / "copy.dbf")
    core.remove_excluded(ctx, set(), "ACCOUNT", out)
    assert Dbf(out).records[0] == Dbf(base).records[0]
    assert "Şerban Weiß".encode("cp1250") in open(out, "rb").read()


def test_refuses_to_overwrite_source(files):
    _, base, *_ = files
    ctx = core.merge_group(base, [], quiet)
    with pytest.raises(DbfError):
        core.remove_excluded(ctx, set(), "ACCOUNT", base)


def test_cross_check_detects_difference(tmp_path):
    p = tmp_path / "bad.dbf"
    create_dbf(str(p), BASE, [{"ACCOUNT": "X", "AMOUNT": 100, "P1": 60, "P2": 30}])
    st = core.merge_group(str(p), [], quiet, "AMOUNT", ["P1", "P2"])["st"]
    lines = core.stage1_lines(st, "AMOUNT", ["P1", "P2"])
    assert any(tag == "err" and "DIFFERS" in text for text, tag in lines)


def test_norm_key():
    assert core.norm_key(b"  00123 ") == "123"
    assert core.norm_key(b"123.00") == "123"
    assert core.norm_key(b" ab12 ") == "AB12"


def test_expand_and_find(tmp_path):
    cfg = core.DEFAULT_CONFIG
    assert core.expand("x_{unit}_{mm}{yy}_{sub}", cfg, "U2", "09", "2026") == "x_U2_0926_unit2/"
    (tmp_path / "f_1.dbf").write_bytes(b"x")
    path, n = core.find_file(str(tmp_path), "f_*.dbf")
    assert n == 1 and path.endswith("f_1.dbf")


def test_cli_end_to_end_on_sample_data(tmp_path, capsys):
    data = tmp_path / "data"
    make(str(data), "09", "2026")
    cfg_path = str(tmp_path / "config.json")
    code = cli.main(["--config", cfg_path, "--work-folder", str(data),
                     "--unit", "U2", "--month", "9", "--year", "2026"])
    out = capsys.readouterr().out
    assert code == 0, out
    assert out.count("✓ equal") == 6            # 3 groups x 2 stages
    result_dir = data / "OUTPUT_U2_0926"
    assert len(list(result_dir.glob("*.dbf"))) == 3
    assert (result_dir / "report.txt").exists()

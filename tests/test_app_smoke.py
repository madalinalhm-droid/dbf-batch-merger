# -*- coding: utf-8 -*-
"""Drives the Flet GUI logic without a window (skipped if Flet is not installed)."""
import asyncio
import os
import sys

import pytest

ft = pytest.importorskip("flet")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from dbf_merger import app  # noqa: E402
from tools.make_sample_data import make  # noqa: E402


class _Window:
    pass


class FakePage:
    def __init__(self):
        self.window, self.services, self.controls, self.title = _Window(), [], [], ""

    def update(self):
        pass

    def add(self, *controls):
        self.controls.extend(controls)


def _walk(c, out):
    out.append(c)
    for attr in ("content", "controls"):
        v = getattr(c, attr, None)
        if isinstance(v, list):
            for x in v:
                _walk(x, out)
        elif v is not None and not isinstance(v, str) and hasattr(v, "__dict__"):
            _walk(v, out)
    return out


def test_gui_merge_and_remove(tmp_path, monkeypatch):
    import datetime
    today = datetime.date.today()
    data = tmp_path / "data"
    make(str(data), "%02d" % today.month, str(today.year))
    cfg = tmp_path / "config.json"
    cfg.write_text('{"work_folder": "%s"}' % str(data).replace("\\", "\\\\"), encoding="utf-8")
    monkeypatch.setattr(app, "CONFIG_PATH", str(cfg))

    page = FakePage()
    asyncio.run(app.main(page))
    controls = _walk(page.controls[0], [])

    def button(text):
        return next(c for c in controls if isinstance(c, (ft.Button, ft.OutlinedButton))
                    and c.content == text)

    log = next(c for c in controls if isinstance(c, ft.ListView))
    button("1 · Merge all (checked)").on_click(None)
    button("2 · Remove exclusions for all (checked)").on_click(None)
    lines = [getattr(c, "value", "") for c in log.controls]
    assert any("exclusions removed for 3 groups" in t for t in lines), lines
    assert not any("DIFFERS" in t or "ERROR" in t for t in lines)
    out = data / ("OUTPUT_U1_%02d%s" % (today.month, str(today.year)[2:]))
    assert len(list(out.glob("*.dbf"))) == 3

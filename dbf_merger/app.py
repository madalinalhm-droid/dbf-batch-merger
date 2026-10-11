# -*- coding: utf-8 -*-
"""Flet desktop interface for the batch merge (see core.py for the logic)."""
import datetime
import json
import os
import subprocess
import sys

import flet as ft

from . import core
from .core import (SOURCES, expand, find_file, fmt_amount, compare_amounts, load_keys,
                   merge_group, remove_excluded, fetch_files, stage1_lines, stage2_lines)

if getattr(sys, "frozen", False):
    APP_DIR = os.path.dirname(sys.executable)
else:
    APP_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CONFIG_PATH = os.path.join(APP_DIR, "config.json")

MONTHS = ["%02d" % m for m in range(1, 13)]

# Log styles: dark text on a light background for readability.
TAG_STYLE = {
    "warn": dict(color=ft.Colors.ORANGE_900, size=13),
    "err": dict(color=ft.Colors.RED_700, size=14, weight=ft.FontWeight.BOLD),
    "ok": dict(color=ft.Colors.GREEN_900, size=13),
    "info": dict(color=ft.Colors.GREY_800, size=13),
    "title": dict(color=ft.Colors.BLUE_900, size=14, weight=ft.FontWeight.BOLD),
    "group": dict(color=ft.Colors.BLUE_900, size=18, weight=ft.FontWeight.BOLD),
    "stage": dict(color=ft.Colors.GREY_900, size=14, weight=ft.FontWeight.BOLD),
    "total": dict(color=ft.Colors.BLACK, size=17, weight=ft.FontWeight.BOLD),
    "total_final": dict(color=ft.Colors.GREEN_900, size=17, weight=ft.FontWeight.BOLD),
}


def open_path(path):
    """Open a folder or file with the operating system's default program."""
    if sys.platform.startswith("win"):
        os.startfile(path)
    elif sys.platform == "darwin":
        subprocess.Popen(["open", path])
    else:
        subprocess.Popen(["xdg-open", path])


async def main(page: ft.Page):
    page.title = "DBF Batch Merger"
    page.window.width = 1360
    page.window.height = 940
    page.window.min_width = 900
    page.window.min_height = 600

    cfg_error = None
    try:
        cfg = core.load_config(CONFIG_PATH)
    except Exception as e:
        cfg = json.loads(json.dumps(core.DEFAULT_CONFIG))
        cfg_error = str(e)

    work_default = cfg.get("work_folder", "./sample_data")
    if not os.path.isabs(work_default):
        work_default = os.path.normpath(os.path.join(APP_DIR, work_default))

    today = datetime.date.today()
    units = list(cfg["units"].keys())
    state = {"unit": units[0], "last_output": None, "report": {}}
    rows = {}  # row id -> row data

    AMOUNT_FIELD = cfg.get("amount_field")
    CROSS_FIELDS = cfg.get("cross_check_fields") or []
    KEY_FIELD = cfg.get("record_key_field", "ACCOUNT")

    # --- top controls -------------------------------------------------------
    folder_field = ft.TextField(value=work_default, read_only=True, expand=True,
                                border_radius=8, content_padding=10,
                                border_color=ft.Colors.GREY_400)
    excl_field = ft.TextField(value="", read_only=True, expand=True, border_radius=8,
                              content_padding=10, border_color=ft.Colors.GREY_400)
    source_field = ft.TextField(value="", expand=True, border_radius=8, content_padding=10,
                                border_color=ft.Colors.GREY_400)
    month_dd = ft.Dropdown(value="%02d" % today.month, width=90,
                           options=[ft.dropdown.Option(m) for m in MONTHS], border_radius=8)
    year_field = ft.TextField(value=str(today.year), width=90, border_radius=8,
                              content_padding=10, text_align=ft.TextAlign.CENTER)

    def change_year(delta):
        def handler(e):
            try:
                v = int(year_field.value.strip())
            except ValueError:
                v = today.year
            year_field.value = str(max(2000, v + delta))
            page.update()
        return handler

    year_minus = ft.IconButton(icon=ft.Icons.REMOVE, icon_size=16, width=30, height=30,
                               tooltip="Previous year", on_click=change_year(-1))
    year_plus = ft.IconButton(icon=ft.Icons.ADD, icon_size=16, width=30, height=30,
                              tooltip="Next year", on_click=change_year(1))

    rows_column = ft.Column(spacing=6, scroll=ft.ScrollMode.AUTO, expand=True)
    log_view = ft.ListView(spacing=2, auto_scroll=True, expand=True)

    file_picker = ft.FilePicker()
    page.services.append(file_picker)

    state["period"] = (state["unit"], month_dd.value, year_field.value.strip())

    # --- helpers --------------------------------------------------------------
    def log(text, tag=None):
        if tag == "sep":
            log_view.controls.append(ft.Divider(height=10, color=ft.Colors.GREY_400))
        else:
            style = TAG_STYLE.get(tag) or ({"color": tag, "size": 13} if tag else {"size": 13})
            log_view.controls.append(ft.Text(text, font_family="Consolas", selectable=True,
                                             **{"color": ft.Colors.BLACK_87, **style}))
        page.update()

    def shown(path):
        if not path:
            return "— missing"
        try:
            rel = os.path.relpath(path, folder_field.value)
            return path if rel.startswith("..") else rel
        except ValueError:
            return path

    # --- group rows ---------------------------------------------------------
    def toggle_active(rid):
        def handler(e):
            r = rows[rid]
            r["active"] = bool(e.control.value) and bool(r["base"])
            e.control.value = r["active"]
            page.update()
        return handler

    def set_btn2(r, enabled):
        b = r["btn2"]
        b.disabled = not enabled
        b.bgcolor = ft.Colors.GREEN_600 if enabled else ft.Colors.GREY_300
        b.color = ft.Colors.WHITE if enabled else ft.Colors.GREY_600

    def reset_stages(rid):
        """When a file changes, results already computed are no longer valid."""
        r = rows[rid]
        r["ctx"] = None
        r["res1"].controls, r["res1"].visible = [], False
        r["res2"].controls, r["res2"].visible = [], False
        set_btn2(r, False)
        state["report"].pop(rid, None)
        r["steps_row"].visible = bool(r["base"])

    def pick_file(rid, key):
        async def handler(e):
            r = rows[rid]
            start = os.path.dirname(r[key]) if r.get(key) else folder_field.value
            files = await file_picker.pick_files(
                dialog_title="%s – %s" % (r["name"], key),
                initial_directory=start, allowed_extensions=["dbf"])
            if not files:
                return
            r[key] = os.path.normpath(files[0].path)
            if key == "base":
                r["active"] = True
                r["checkbox"].value = True
            r["labels"][key].value = shown(r[key])
            r["labels"][key].color = ft.Colors.BLACK
            r["clear_buttons"][key].visible = True
            reset_stages(rid)
            page.update()
        return handler

    def clear_file(rid, key):
        def handler(e):
            r = rows[rid]
            r[key] = None
            if key == "base":
                r["active"] = False
                r["checkbox"].value = False
            r["labels"][key].value = "— missing"
            r["labels"][key].color = ft.Colors.GREY_500
            r["clear_buttons"][key].visible = False
            reset_stages(rid)
            page.update()
        return handler

    # --- results shown inside a group row ------------------------------------
    def cross_check_text(main_v, alt_v):
        ok, diff = compare_amounts(main_v, alt_v)
        if ok:
            return ft.Text("✓ cross-check %s equal" % "+".join(CROSS_FIELDS), size=12,
                           color=ft.Colors.GREEN_800)
        return ft.Text("✗ cross-check %s DIFFERS by %s" % ("+".join(CROSS_FIELDS),
                                                            fmt_amount(diff)),
                       size=13, weight=ft.FontWeight.BOLD, color=ft.Colors.RED_700)

    def stage1_controls(st):
        c = [ft.Text("1) After merge:", weight=ft.FontWeight.BOLD, size=13,
                     color=ft.Colors.GREY_900),
             ft.Text("%s records" % st["total_merged"], size=13, color=ft.Colors.BLACK_87)]
        if "amount_merged" in st:
            c.append(ft.Text("TOTAL: %s" % fmt_amount(st["amount_merged"]),
                             weight=ft.FontWeight.BOLD, size=16, color=ft.Colors.BLACK))
            if "amount_alt_merged" in st:
                c.append(cross_check_text(st["amount_merged"], st["amount_alt_merged"]))
        return c

    def stage2_controls(st, out_name):
        removed = "removed %s" % st["removed"]
        if "amount_removed" in st:
            removed += " (%s)" % fmt_amount(st["amount_removed"])
        c = [ft.Text("2) After exclusions:", weight=ft.FontWeight.BOLD, size=13,
                     color=ft.Colors.GREY_900),
             ft.Text(removed, size=13, color=ft.Colors.BLACK_87),
             ft.Text("left %s" % st["final"], size=13, color=ft.Colors.BLACK_87)]
        if "amount_final" in st:
            c.append(ft.Text("TOTAL: %s" % fmt_amount(st["amount_final"]),
                             weight=ft.FontWeight.BOLD, size=16, color=ft.Colors.GREEN_900))
            if "amount_alt_final" in st:
                c.append(cross_check_text(st["amount_final"], st["amount_alt_final"]))
        c.append(ft.Text("→ %s" % out_name, size=12, color=ft.Colors.GREEN_800))
        return c

    def error_text(msg):
        return [ft.Text("ERROR: %s" % msg, size=13, weight=ft.FontWeight.BOLD,
                        color=ft.Colors.RED_700)]

    def build_row(rid, r, index):
        checkbox = ft.Checkbox(value=r["active"], on_change=toggle_active(rid))
        r["checkbox"] = checkbox
        r["labels"], r["clear_buttons"] = {}, {}
        r["ctx"] = None
        cells = []
        for key, _title in SOURCES:
            found = bool(r.get(key))
            text = shown(r.get(key))
            label = ft.Text(text, size=12, no_wrap=True, max_lines=1,
                            overflow=ft.TextOverflow.ELLIPSIS,
                            color=ft.Colors.BLACK if found else ft.Colors.GREY_500, width=175)
            clear_btn = ft.IconButton(icon=ft.Icons.CLOSE, icon_size=13, visible=found,
                                      tooltip="Remove file", on_click=clear_file(rid, key),
                                      width=26, height=26)
            r["labels"][key] = label
            r["clear_buttons"][key] = clear_btn
            cells.append(ft.Container(
                content=ft.Row([label, clear_btn], spacing=0, tight=True,
                               vertical_alignment=ft.CrossAxisAlignment.CENTER),
                padding=ft.Padding.symmetric(horizontal=8, vertical=4),
                border_radius=6, bgcolor=ft.Colors.GREY_200, ink=True,
                clip_behavior=ft.ClipBehavior.HARD_EDGE, tooltip=text,
                on_click=pick_file(rid, key), width=210))
        top = ft.Row(
            [checkbox, ft.Container(
                ft.Text(r["name"], weight=ft.FontWeight.W_600, size=13, no_wrap=True,
                        max_lines=1, overflow=ft.TextOverflow.ELLIPSIS),
                width=170, tooltip=r["name"])] + cells,
            vertical_alignment=ft.CrossAxisAlignment.CENTER, wrap=True)
        r["btn1"] = ft.Button(
            "1 · Merge", bgcolor=ft.Colors.BLUE_600, color=ft.Colors.WHITE, height=36,
            tooltip="Merge base + supplement + additions and show the totals, removing nothing",
            on_click=lambda e, i=rid: stage1_row(i))
        r["btn2"] = ft.Button(
            "2 · Remove exclusions", height=36, disabled=True,
            tooltip="Remove excluded records (by key) and write the output file",
            on_click=lambda e, i=rid: stage2_row(i))
        set_btn2(r, False)
        r["res1"] = ft.Row([], wrap=True, spacing=14, visible=False)
        r["res2"] = ft.Row([], wrap=True, spacing=14, visible=False)
        r["steps_row"] = ft.Row(
            [r["btn1"], r["btn2"], ft.Column([r["res1"], r["res2"]], spacing=2, expand=True)],
            vertical_alignment=ft.CrossAxisAlignment.START, spacing=10, visible=bool(r["base"]))
        return ft.Container(bgcolor=ft.Colors.WHITE if index % 2 == 0 else ft.Colors.GREY_50,
                            border_radius=8, padding=8,
                            content=ft.Column([top, r["steps_row"]], spacing=6))

    # --- locating the files ---------------------------------------------------
    def search(e):
        unit, mm, yyyy = state["unit"], month_dd.value, year_field.value.strip()
        if len(yyyy) != 4 or not yyyy.isdigit():
            log("The year must have 4 digits.", "warn")
            return
        root = folder_field.value
        rows.clear()
        rows_column.controls.clear()
        log_view.controls.clear()
        state["report"] = {}
        state["period"] = (unit, mm, yyyy)
        log("%s (%s), period %s.%s" % (cfg["units"][unit]["name"], unit, mm, yyyy), "title")
        if cfg_error:
            log("config.json could not be read (%s) – using defaults." % cfg_error, "warn")
        excl, _ = find_file(root, expand(cfg["units"][unit]["exclusions"], cfg, unit, mm, yyyy))
        excl_field.value = excl or ""
        if not excl:
            log("Exclusion list not found – pick it with «Browse…».", "warn")
        if cfg.get("monthly_source"):
            source_field.value = expand(cfg["monthly_source"], cfg, unit, mm, yyyy)
        found = 0
        for i, g in enumerate(cfg["groups"]):
            r = {"name": g["name"]}
            for key, _ in SOURCES:
                path, n = find_file(root, expand(g.get(key, ""), cfg, unit, mm, yyyy))
                r[key] = path
                if n > 1:
                    log("%s: %d files match «%s», using the newest: %s"
                        % (g["name"], n, key, os.path.basename(path)), "warn")
            r["active"] = bool(r["base"])
            found += r["active"]
            rid = str(i)
            rows[rid] = r
            rows_column.controls.append(build_row(rid, r, i))
        log("Base files found: %d of %d groups." % (found, len(cfg["groups"])))
        log("Per group: «1 · Merge» → check the total → «2 · Remove exclusions».", "info")
        page.update()

    # --- processing stages ------------------------------------------------------
    def group_heading(r):
        unit, mm, yyyy = state["period"]
        return "%s   (%s %s, %s.%s)" % (r["name"].upper(), cfg["units"][unit]["name"],
                                        unit, mm, yyyy)

    def out_dir():
        unit, mm, yyyy = state["period"]
        d = core.output_dir(folder_field.value, unit, mm, yyyy)
        state["last_output"] = d
        return d

    def write_report():
        unit, mm, yyyy = state["period"]
        lines = [core.report_header(cfg, unit, mm, yyyy, excl_field.value)]
        for rid in sorted(state["report"], key=int):
            b = state["report"][rid]
            lines += ["", b["heading"]] + b.get("s1", []) + b.get("s2", [])
        with open(os.path.join(out_dir(), "report.txt"), "w", encoding="utf-8") as fh:
            fh.write("\n".join(lines) + "\n")

    def stage1(rid):
        """STAGE 1 for one group: merge and show totals. Writes nothing."""
        r = rows[rid]
        if not r.get("base"):
            return False
        log("", "sep")
        log(group_heading(r), "group")
        extra = [("supplement", r["supplement"]), ("additions", r["additions"])]
        if r.get("extra"):
            extra.append(("extra", r["extra"]))
        try:
            ctx = merge_group(r["base"], extra, log, AMOUNT_FIELD, CROSS_FIELDS)
        except Exception as ex:
            r["ctx"] = None
            log("   ERROR: %s" % ex, "err")
            r["res1"].controls, r["res1"].visible = error_text(ex), True
            r["res2"].controls, r["res2"].visible = [], False
            set_btn2(r, False)
            page.update()
            return False
        r["ctx"] = ctx
        lines = stage1_lines(ctx["st"], AMOUNT_FIELD, CROSS_FIELDS)
        for text, tag in lines:
            log(text, tag)
        state["report"][rid] = {"heading": group_heading(r), "s1": [t for t, _ in lines]}
        r["res1"].controls, r["res1"].visible = stage1_controls(ctx["st"]), True
        r["res2"].controls, r["res2"].visible = [], False
        set_btn2(r, True)
        page.update()
        return True

    def load_exclusions():
        path = excl_field.value
        if not path or not os.path.isfile(path):
            log("Pick the exclusion list first.", "err")
            return None
        try:
            keys, empty, deleted = load_keys(path, cfg["exclusion_key_field"])
        except Exception as ex:
            log("Error reading the exclusion list: %s" % ex, "err")
            return None
        log("Exclusion list: %s — %d distinct values in field %s"
            % (os.path.basename(path), len(keys), cfg["exclusion_key_field"]), "title")
        if empty:
            log("   %d rows with empty %s – ignored" % (empty, cfg["exclusion_key_field"]), "warn")
        if deleted:
            log("   %d rows marked as deleted – ignored" % deleted, "warn")
        return keys

    def stage2(rid, keys):
        """STAGE 2 for one group: remove exclusions and write the output file."""
        r = rows[rid]
        ctx = r.get("ctx")
        if not ctx:
            log("%s: run «1 · Merge» first." % r["name"], "warn")
            return False
        out = os.path.join(out_dir(), os.path.basename(r["base"]))
        log("", "sep")
        log(group_heading(r), "group")
        try:
            st, removed = remove_excluded(ctx, keys, KEY_FIELD, out)
        except Exception as ex:
            log("   ERROR: %s" % ex, "err")
            r["res2"].controls, r["res2"].visible = error_text(ex), True
            page.update()
            return False
        lines = stage2_lines(st, AMOUNT_FIELD, CROSS_FIELDS, KEY_FIELD, os.path.basename(out))
        for text, tag in lines:
            log(text, tag)
        s2 = [t for t, _ in lines]
        if removed:
            s2.append("     %s removed: %s" % (KEY_FIELD, ", ".join(sorted(set(removed)))))
        state["report"].setdefault(rid, {"heading": group_heading(r), "s1": []})["s2"] = s2
        r["res2"].controls, r["res2"].visible = stage2_controls(st, os.path.basename(out)), True
        write_report()
        page.update()
        return True

    def stage1_row(rid):
        stage1(rid)

    def stage2_row(rid):
        keys = load_exclusions()
        if keys is not None:
            stage2(rid, keys)

    def checked():
        return [(rid, r) for rid, r in rows.items() if r["active"] and r["base"]]

    def merge_all(e):
        active = checked()
        if not active:
            log("No group selected.", "err")
            return
        ok = sum(1 for rid, _ in active if stage1(rid))
        log("", "sep")
        log("Stage 1 done for %d of %d groups. Check the totals, then press "
            "«2 · Remove exclusions» (per group or for all)." % (ok, len(active)), "title")

    def remove_all(e):
        active = checked()
        if not active:
            log("No group selected.", "err")
            return
        keys = load_exclusions()
        if keys is None:
            return
        skipped = [r["name"] for _, r in active if not r.get("ctx")]
        if skipped:
            log("Skipped (stage 1 not done yet): %s" % ", ".join(skipped), "warn")
        ok = sum(1 for rid, r in active if r.get("ctx") and stage2(rid, keys))
        log("", "sep")
        log("Done: exclusions removed for %d groups. Output in %s"
            % (ok, state.get("last_output") or folder_field.value), "title")

    async def pick_folder(e):
        result = await file_picker.get_directory_path(dialog_title="Choose the work folder",
                                                       initial_directory=folder_field.value)
        if result:
            folder_field.value = os.path.normpath(result)
            cfg["work_folder"] = folder_field.value
            try:
                core.save_config(CONFIG_PATH, cfg)
            except OSError:
                pass
            page.update()
            search(None)

    async def pick_exclusions(e):
        files = await file_picker.pick_files(dialog_title="Choose the exclusion list",
                                             initial_directory=folder_field.value,
                                             allowed_extensions=["dbf"])
        if files:
            excl_field.value = os.path.normpath(files[0].path)
            page.update()

    async def pick_source(e):
        result = await file_picker.get_directory_path(
            dialog_title="Choose the monthly source folder",
            initial_directory=source_field.value or None)
        if result:
            source_field.value = os.path.normpath(result)
            page.update()

    def fetch(e):
        src = source_field.value.strip()
        if not src or not os.path.isdir(src):
            log("The source folder does not exist or is empty.", "err")
            return
        unit, mm, yyyy = state["unit"], month_dd.value, year_field.value.strip()
        log("")
        log("Fetching files from %s ..." % src, "title")
        copied, unknown = fetch_files(src, cfg["groups"], cfg, unit, mm, yyyy,
                                      folder_field.value, log)
        log("Fetched %d files." % copied, "title")
        if unknown:
            log("Source .dbf files not fetched (no matching group in the config): "
                + ", ".join(unknown), "warn")
        search(None)

    def open_output(e):
        d = state["last_output"] or folder_field.value
        if os.path.isdir(d):
            open_path(d)

    def edit_config(e):
        try:
            open_path(CONFIG_PATH)
        except Exception as ex:
            log("Could not open config.json: %s" % ex, "err")
            return
        log("After saving the file, restart the application to load the new settings.", "title")

    # --- unit selector ------------------------------------------------------------
    unit_buttons = {}

    def choose_unit(unit):
        def handler(e):
            state["unit"] = unit
            for u, b in unit_buttons.items():
                b.bgcolor = ft.Colors.GREEN_600 if u == unit else ft.Colors.GREY_300
                b.color = ft.Colors.WHITE if u == unit else ft.Colors.BLACK
            page.update()
            search(None)
        return handler

    for u in units:
        first = u == state["unit"]
        unit_buttons[u] = ft.Button(
            "%s (%s)" % (cfg["units"][u]["name"], u),
            bgcolor=ft.Colors.GREEN_600 if first else ft.Colors.GREY_300,
            color=ft.Colors.WHITE if first else ft.Colors.BLACK,
            on_click=choose_unit(u))

    # --- page layout --------------------------------------------------------------
    content = ft.Column(
        [
            ft.Text("DBF Batch Merger", size=22, weight=ft.FontWeight.BOLD),
            ft.Row(list(unit_buttons.values()) +
                   [ft.Container(width=16), ft.Text("Month:"), month_dd, ft.Text("Year:"),
                    year_minus, year_field, year_plus],
                   vertical_alignment=ft.CrossAxisAlignment.CENTER),
            ft.Row([ft.Container(ft.Text("Work folder:"), width=140), folder_field,
                    ft.Button("Browse…", on_click=pick_folder)]),
            ft.Row([ft.Container(ft.Text("Exclusion list:"), width=140), excl_field,
                    ft.Button("Browse…", on_click=pick_exclusions)]),
            ft.Row([ft.Container(ft.Text("Monthly source:"), width=140), source_field,
                    ft.Button("Browse…", on_click=pick_source),
                    ft.Button("Fetch files", bgcolor=ft.Colors.TEAL_600,
                                      color=ft.Colors.WHITE, on_click=fetch)]),
            ft.Text("Click a file = choose another one  •  ✕ = remove it  •  per group: "
                    "1 · Merge → check the total → 2 · Remove exclusions",
                    size=12, color=ft.Colors.GREY_600),
            ft.Container(content=rows_column, height=270, border_radius=10, padding=8,
                         bgcolor=ft.Colors.GREY_50, border=ft.Border.all(1, ft.Colors.GREY_300)),
            ft.Row([
                ft.OutlinedButton("Search files again", on_click=search),
                ft.Button("1 · Merge all (checked)", bgcolor=ft.Colors.BLUE_600,
                                  color=ft.Colors.WHITE, on_click=merge_all),
                ft.Button("2 · Remove exclusions for all (checked)",
                                  bgcolor=ft.Colors.GREEN_600, color=ft.Colors.WHITE,
                                  on_click=remove_all),
                ft.Button("Open output folder", on_click=open_output),
                ft.OutlinedButton("Edit settings (config)", on_click=edit_config),
            ], wrap=True),
            ft.Container(content=log_view, height=340, bgcolor=ft.Colors.WHITE, border_radius=10,
                         border=ft.Border.all(1, ft.Colors.GREY_400), padding=12),
        ],
        expand=True, spacing=12, scroll=ft.ScrollMode.AUTO,
    )
    page.add(ft.Container(content=content, padding=18, bgcolor=ft.Colors.GREY_100, expand=True))
    search(None)


def run():
    ft.run(main)


if __name__ == "__main__":
    run()

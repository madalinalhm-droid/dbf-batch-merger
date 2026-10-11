# DBF Batch Merger

A desktop tool (Python + [Flet](https://flet.dev)) that automates a monthly
consolidation job on legacy **dBASE / Visual FoxPro `.dbf`** files. It replaces
a chain of manual FoxPro commands (`USE … APPEND FROM … DELETE FOR … COPY TO`)
with a guided, two-step workflow that runs every check before anything is written.

> Built from a real-world payment-file workflow and published here in a generic
> form. All data in this repository is fictitious.

## The problem

Every month, each data provider ("group") sends a base file. Before the data can
be used, it has to be:

1. **merged** with a supplement file and an additions file that come from other
   systems, often with a slightly different table structure;
2. **filtered**: every record whose key appears in an exclusion list must be removed;
3. **checked**: record counts and money totals must reconcile before and after.

Doing this by hand for 20+ groups and two organisational units every month is slow
and error-prone.

## What the tool does

| | |
|---|---|
| **Finds the files** | Locates each group's files for the chosen unit and month from configurable name patterns (`base_ga_{unit}_{mm}{yy}*.dbf`). With several matches, it takes the newest and says so. |
| **Stage 1 – Merge** | Appends the supplement / additions records onto the base structure, matching fields by name and converting types (text ↔ numeric, wider ↔ narrower), exactly like FoxPro `APPEND FROM`. Nothing is written yet – the operator checks the totals first. |
| **Stage 2 – Remove exclusions** | Drops records whose key (e.g. an account number) is in the exclusion list and writes the result to a separate `OUTPUT_<unit>_<mmyy>` folder. **Source files are never modified.** |
| **Reconciliation** | Every total is computed twice – from the main amount field and from the sum of its component fields – and any difference is flagged in red. |
| **Report** | A plain-text `report.txt` with counts, totals, cross-checks and the list of removed keys, for the audit trail. |
| **Legacy encodings** | Records are copied byte-for-byte, so text in cp852 / cp1250 and national characters (ş, ţ, ä, ß…) is preserved exactly. No external DBF library is needed. |
| **Configurable** | Groups, units, file patterns and field names live in `config.json` – adding a new data provider needs no code change. |

The same logic is available as a **command-line tool** for unattended runs.

## Tech stack

Python 3.10+ · Flet 0.85 (desktop GUI) · binary file parsing with `struct` ·
pytest · GitHub Actions (Windows + Linux) · Docker

## Getting started

```bash
git clone https://github.com/<your-user>/dbf-batch-merger.git
cd dbf-batch-merger
pip install -r requirements.txt

# create fictitious sample files for the current month
python tools/make_sample_data.py

# start the desktop app
python main.py
```

On first start the app creates `config.json` next to `main.py` (see
[`config.example.json`](config.example.json)). In the app:

1. pick the unit and the month;
2. press **1 · Merge all** and check the totals and cross-checks;
3. press **2 · Remove exclusions for all** – results and `report.txt` appear in `sample_data/OUTPUT_U1_<mmyy>`.

### Command line

```bash
python -m dbf_merger.cli --unit U1 --month 09 --year 2026
```

Exit code `0` means every group was processed and every cross-check matched.

### Docker (headless demo, no GUI)

```bash
docker build -t dbf-batch-merger .   # runs the test suite during the build
docker run --rm dbf-batch-merger     # generates sample data and runs the batch
```

## Configuration

```jsonc
{
  "work_folder": "./sample_data",
  "amount_field": "AMOUNT",                         // totalled in the report
  "cross_check_fields": ["AMT_BASE", "AMT_BONUS", "AMT_OTHER"],
  "record_key_field": "ACCOUNT",                    // key in the data files
  "exclusion_key_field": "ACCOUNT",                 // key in the exclusion list
  "units": {
    "U1": {"name": "Unit 1", "exclusions": "exclusions_U1.dbf", "sub": ""},
    "U2": {"name": "Unit 2", "exclusions": "exclusions_U2.dbf", "sub": "unit2/"}
  },
  "groups": [
    {
      "name": "Group A",
      "folder": "group_a",
      "base": "group_a/base_ga_{unit}_{mm}{yy}*.dbf",
      "supplement": "supplements/{mm}{yy}/{sub}sup_ga_{mm}{yy}.dbf",
      "additions": "additions/add_ga_{unit}*.dbf"
    }
  ]
}
```

Placeholders: `{unit}`, `{mm}`, `{yy}`, `{yyyy}`, `{sub}`; `*` matches any text.
An optional `"extra"` pattern adds a fourth source to a group.

## Project structure

```
dbf_merger/
  dbf.py    – dependency-free DBF reader/writer and field conversion
  core.py   – merge / exclusion / reconciliation logic (no UI)
  app.py    – Flet desktop interface
  cli.py    – command-line batch run
tools/make_sample_data.py – fictitious test data generator
tests/      – pytest suite (logic, end-to-end CLI, GUI smoke test)
```

## Tests

```bash
pip install -r requirements-dev.txt
python -m pytest -q
```

## Limitations

- Memo fields (`M`, `G`, `W`) are not supported – the tool refuses such tables.
- Output files are written without a `.cdx` index; rebuild it in FoxPro if needed.

## License

MIT

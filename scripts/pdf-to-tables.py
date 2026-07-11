#!/usr/bin/env python3
"""Extract the spec .txt tables from a Roland GT-1000 MIDI Implementation PDF.

Pipeline position: PDF -> specs/*.txt (this script) -> specs/*.json
(options-to-json.py for parameter tables, spec-to-json.py for address maps).
Historically the .txt tables were copy/pasted from the PDF by hand; this
automates the copy so a new spec revision can be re-extracted and diffed.

The PDF's mono-spaced pipe tables survive pypdf text extraction line-for-line,
so extraction is: keep `| ... |` rows, drop page furniture (borders, repeated
Offset/Address/Description headers, ellipsis rows in the address maps), and
collapse cell whitespace to the compact form of the hand-made files.

Table naming: a section header like `* [PatchDelay1, PatchDelay2, ...]`
declares N structurally identical tables; the emitted file drops the varying
digit/letter suffix or infix (PatchDelay.txt, PatchFxComp.txt), matching the
manual convention (spec-to-json.py maps per-instance refs like PatchDelay3
back onto the canonical table name).

--check compares the extraction against the committed specs at two levels:
byte-identical .txt, and (when the text differs, e.g. different wrap points
or kept/dropped furniture) equality of the JSON produced by the downstream
converter, which is what the library actually loads.

Usage:
  poetry run python scripts/pdf-to-tables.py <spec.pdf> --list
  poetry run python scripts/pdf-to-tables.py <spec.pdf> --check
  poetry run python scripts/pdf-to-tables.py <spec.pdf> --out /tmp/stage
  poetry run python scripts/pdf-to-tables.py <spec.pdf> --out pygt1000/specs \
      --only Patch,PatchCommon,PatchStompBox,PatchLed
"""

import argparse
import contextlib
import importlib.util
import io
import re
import sys
from pathlib import Path

SPECS_DIR = Path(__file__).resolve().parent.parent / "pygt1000" / "specs"
SCRIPTS_DIR = Path(__file__).resolve().parent

SECTION_RE = re.compile(r"^\*\s*\[([^\]]+)\]")
CHAPTER_RE = re.compile(r"^\d+\.\s+\S")
HEX_BYTE_RE = re.compile(r"^[0-9A-F]{2}$")
# Furniture header cells repeated after every page break.
HEADER_CELLS = {"Offset", "Address", "Description", "Start"}


def load_module(name, filename):
    """Import a sibling script that has dashes in its file name."""
    spec = importlib.util.spec_from_file_location(name, SCRIPTS_DIR / filename)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def extract_text_lines(pdf_path):
    from pypdf import PdfReader

    reader = PdfReader(str(pdf_path))
    if reader.is_encrypted:
        reader.decrypt("")
    lines = []
    for page in reader.pages:
        lines.extend((page.extract_text() or "").splitlines())
    return lines


def split_cells(line):
    """Split a `| a | b | c |` line into stripped, space-collapsed cells."""
    parts = line.strip().split("|")
    # parts[0] is the text before the first pipe (empty), parts[-1] after the
    # last. Roland rows always close their last cell with a pipe.
    cells = [re.sub(r"\s+", " ", p).strip() for p in parts[1:-1]]
    return cells


def is_hex_bytes(cell, count=None):
    toks = cell.lstrip("#").split()
    if count is not None and len(toks) != count:
        return False
    return bool(toks) and all(HEX_BYTE_RE.match(t) for t in toks)


def is_furniture(cells):
    if not cells:
        return True
    if all(re.fullmatch(r"[-+ ]*", c) for c in cells):  # border rows
        return True
    nonempty = [c for c in cells if c]
    if nonempty and set(nonempty) <= HEADER_CELLS:  # column headers
        return True
    return False


def format_row(cells):
    """Reassemble cells in the compact style of the hand-made spec files:
    `| 00 01 | 0000 0aaa | TYPE (0 - 5) |`, empty cell -> `| |`, and the
    multi-byte marker keeps its bare `|#` prefix."""
    out = "|"
    for cell in cells:
        if not cell:
            out += " |"
        elif cell.startswith("#"):
            out += f"{cell} |"
        else:
            out += f" {cell} |"
    return out


def canonical_name(names):
    """[PatchDelay1..4] -> PatchDelay, [PatchFx1Comp..] -> PatchFxComp,
    [PatchPreampA,B] -> PatchPreamp; single names pass through."""
    if len(names) == 1:
        return names[0]
    prefix = names[0]
    for n in names[1:]:
        while not n.startswith(prefix):
            prefix = prefix[:-1]
    suffix = names[0]
    for n in names[1:]:
        while not n.endswith(suffix):
            suffix = suffix[1:]
    # The varying middle (digit/letter) is dropped; guard against overlap.
    if len(prefix) + len(suffix) > min(len(n) for n in names):
        suffix = ""
    return prefix + suffix


def parse_tables(lines):
    """Return {table_name: [normalized rows]} plus 'base-addresses'."""
    tables = {}
    current = None            # current table name or None
    in_address_map = False    # inside chapter 3, before the first * [section]
    aux_skip = False          # inside a footnote target-list table

    for raw in lines:
        line = raw.rstrip()
        stripped = line.strip()

        m = SECTION_RE.match(stripped)
        if m:
            names = [n.strip() for n in m.group(1).split(",")]
            current = canonical_name(names)
            in_address_map = False
            aux_skip = False
            tables.setdefault(current, [])
            continue

        if CHAPTER_RE.match(stripped):
            # A new numbered chapter ends any table; chapter 3 is the address
            # map ("3. System Exclusive Address Map" / "3. Parameter address
            # map" depending on revision).
            current = None
            in_address_map = "address map" in stripped.lower()
            aux_skip = False
            continue

        if not stripped.startswith("|"):
            continue  # prose, page furniture, page markers

        cells = split_cells(stripped)
        if is_furniture(cells):
            continue
        if any(c == "Total Size" for c in cells):
            continue
        # Footnote target-list tables (KNOB SETTING / ASSIGN TARGET, headed by
        # `| # | Category | Target |`) follow a section without a new `* [`
        # header and use decimal indexes that can look like hex offsets. A bare
        # "#" first cell (the multi-byte marker is "# <hex>", never bare) flags
        # the header; skip rows until the next real section.
        if cells and cells[0] == "#":
            aux_skip = True
            continue
        if aux_skip:
            continue
        # Inside a table, a row's first cell is empty (continuation), ":"
        # (elision), hex bytes, or a "#"-marked hex offset. Anything else is
        # an embedded auxiliary sub-table (e.g. PatchLed's `| BIT | PEDAL |`).
        if current is not None and cells:
            first = cells[0]
            if first and first != ":" and not is_hex_bytes(first):
                continue

        if current is None:
            # Only the top-level start-address map lives outside a section.
            if in_address_map and len(cells) >= 2 and is_hex_bytes(cells[0], 4):
                tables.setdefault("base-addresses", []).append(format_row(cells))
            continue

        # Ellipsis rows: meaningful only as elision. In 2-cell address maps
        # they would crash spec-to-json; drop them there, keep the normalized
        # `| : | | |` in parameter tables (options-to-json skips it).
        if cells and cells[0] == ":":
            if len(cells) >= 3:
                tables[current].append("| : | | |")
            continue

        tables[current].append(format_row(cells))

    return {k: v for k, v in tables.items() if v}


def table_kind(rows):
    """'map' for address maps (2 cells, `name [Table]` refs), else 'params'."""
    for row in rows:
        cells = split_cells(row)
        if len(cells) >= 2 and "[" in cells[-1] and len(cells) == 2:
            return "map"
        if len(cells) >= 3:
            return "params"
    return "params"


def rows_to_json(rows, kind, options_mod, spec_mod):
    """Run the downstream converter; None if it rejects the rows (e.g. a
    `(fixed value)` range the manual files edited away)."""
    try:
        if kind == "map":
            return spec_mod.convert_to_json(rows)
        with contextlib.redirect_stdout(io.StringIO()):
            return options_mod.process_data(rows)
    except Exception:
        return None


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("pdf", type=Path, help="GT-1000 MIDI Implementation PDF")
    ap.add_argument("--out", type=Path, help="directory to write .txt tables")
    ap.add_argument("--only", type=str, help="comma-separated table names")
    ap.add_argument("--list", action="store_true", help="list found tables")
    ap.add_argument(
        "--check",
        action="store_true",
        help="compare extraction against the committed specs (txt + JSON level)",
    )
    args = ap.parse_args()

    tables = parse_tables(extract_text_lines(args.pdf))
    if args.only:
        wanted = {t.strip() for t in args.only.split(",")}
        missing = wanted - tables.keys()
        if missing:
            sys.exit(f"tables not found in PDF: {', '.join(sorted(missing))}")
        tables = {k: v for k, v in tables.items() if k in wanted}

    if args.list or not (args.out or args.check):
        for name in sorted(tables):
            rows = tables[name]
            print(f"{name:32s} {len(rows):4d} rows  ({table_kind(rows)})")
        print(f"{len(tables)} tables")

    if args.check:
        options_mod = load_module("options_to_json", "options-to-json.py")
        spec_mod = load_module("spec_to_json", "spec-to-json.py")
        counts = {"identical": 0, "json-equal": 0, "JSON-DIFFERS": 0, "new": 0}
        for name in sorted(tables):
            rows = tables[name]
            ref = SPECS_DIR / f"{name}.txt"
            if not ref.is_file():
                counts["new"] += 1
                print(f"NEW          {name} ({len(rows)} rows, no committed .txt)")
                continue
            ref_lines = [ln for ln in ref.read_text().splitlines() if ln.strip()]
            if ref_lines == rows:
                counts["identical"] += 1
                print(f"identical    {name}")
                continue
            kind = table_kind(rows)
            ours = rows_to_json(rows, kind, options_mod, spec_mod)
            theirs = rows_to_json(ref_lines, kind, options_mod, spec_mod)
            if ours is None or theirs is None:
                counts["JSON-DIFFERS"] += 1
                which = "extracted" if ours is None else "committed"
                print(f"JSON-DIFFERS {name} (converter rejects the {which} txt)")
                continue
            if ours == theirs:
                counts["json-equal"] += 1
                print(f"json-equal   {name} (txt differs, converter output equal)")
            else:
                counts["JSON-DIFFERS"] += 1
                print(f"JSON-DIFFERS {name}")
        not_in_pdf = sorted(
            p.stem for p in SPECS_DIR.glob("*.txt") if p.stem not in tables
        )
        if not_in_pdf and not args.only:
            print(
                f"\ncommitted specs not in this PDF revision ({len(not_in_pdf)}): "
                + ", ".join(not_in_pdf)
            )
        print(f"\nsummary: {counts}")

    if args.out:
        args.out.mkdir(parents=True, exist_ok=True)
        for name, rows in tables.items():
            (args.out / f"{name}.txt").write_text("\n".join(rows) + "\n")
        print(f"wrote {len(tables)} tables to {args.out}")


if __name__ == "__main__":
    main()

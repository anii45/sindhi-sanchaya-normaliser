"""
Combine all 40 cleaned workbooks into ONE single-sheet Excel file.

  - Parts 1-39 : <project folder>\\Sindhi Sanchaya 40 Parts\\Cleaned\\Part_NN_Cleaned.xlsx
  - Part 40    : the cleaned Unknown-authors workbook
  - Output     : <project folder>\\SINDHI_SANCHAYA_Final_Cleaned.xlsx

Writes ONE sheet. Header is taken from Part_01 once; every row from every
part is appended after it in order (Part 01 -> 02 -> ... -> 39 -> 40).

Usage:
    python merge_parts.py
"""

import os
import sys
import time

from openpyxl import Workbook, load_workbook

import config


CLEANED_DIR = os.path.join(config._PARENT, "Sindhi Sanchaya 40 Parts", "Cleaned")
UNKNOWN_FILE = os.path.join(
    config._HERE, "SINDHI_SANCHAYA_UnknownAuthors_Cleaned.xlsx"
)
OUTPUT_FILE = os.path.join(config._PARENT, "SINDHI_SANCHAYA_Final_Cleaned.xlsx")


def part_path(n: int) -> str:
    return os.path.join(CLEANED_DIR, f"Part_{n:02d}_Cleaned.xlsx")


def iter_data_rows(path: str):
    """Yield rows from a workbook, skipping the header (row 1)."""
    wb = load_workbook(path, read_only=True, data_only=True)
    ws = wb.active
    for i, row in enumerate(ws.iter_rows(values_only=True), start=1):
        if i == 1:
            continue
        yield row
    wb.close()


def main():
    sources = []
    for n in range(1, 40):
        p = part_path(n)
        if not os.path.exists(p):
            print(f"[!] Missing: {p}")
            sys.exit(1)
        sources.append((f"Part_{n:02d}", p))

    if not os.path.exists(UNKNOWN_FILE):
        print(f"[!] Missing Part 40 (unknown authors): {UNKNOWN_FILE}")
        sys.exit(1)
    sources.append(("Part_40_Unknown", UNKNOWN_FILE))

    print("=" * 64)
    print(" MERGE 40 CLEANED WORKBOOKS -> ONE SINGLE-SHEET FILE")
    print("=" * 64)
    print(f"  Sources : {len(sources)}")
    print(f"  Output  : {OUTPUT_FILE}")
    print()

    out_wb = Workbook()
    out_ws = out_wb.active
    out_ws.title = "SINDHI_SANCHAYA"

    # Header from the first source (Part_01).
    wb1 = load_workbook(sources[0][1], read_only=True, data_only=True)
    ws1 = wb1.active
    header = next(ws1.iter_rows(values_only=True))
    wb1.close()
    out_ws.append(list(header))
    print(f"  Header  : {len(header)} columns")
    print()

    t0 = time.time()
    total_rows = 0
    for label, path in sources:
        n = 0
        for row in iter_data_rows(path):
            out_ws.append(list(row))
            n += 1
        total_rows += n
        print(f"  {label:<18}  +{n:>5} rows  (running total {total_rows:,})")

    print()
    print(f"[i] Writing {OUTPUT_FILE} ...")
    out_wb.save(OUTPUT_FILE)
    elapsed = time.time() - t0

    print()
    print("=" * 64)
    print(f" DONE in {elapsed:.1f}s")
    print(f"  Rows merged   : {total_rows:,}  (excluding header)")
    print(f"  Total in file : {total_rows + 1:,}  (header + data)")
    print(f"  Output        : {OUTPUT_FILE}")
    print("=" * 64)


if __name__ == "__main__":
    main()

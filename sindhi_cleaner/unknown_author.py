"""
UNKNOWN-AUTHOR BOOK CLEANER — full concurrent run.

What's special vs run_full.py:
  - Only processes rows where the author is "Unknown Author" / "अज्ञात लेखक"
    / "نامعلوم ليکڪ" or blank.
  - Sends ONLY book-name fields to Gemini (smaller prompt + smaller output
    → ~40% cheaper per call).
  - Author columns are FORCED to the canonical Unknown markers in code;
    never asked from the API.
  - Same script-integrity validator, same concurrency, same checkpoints.

Outputs:
    SINDHI_SANCHAYA_UnknownAuthors_Cleaned.xlsx
    SINDHI_SANCHAYA_UnknownAuthors_ChangeReport.csv
    SINDHI_SANCHAYA_UnknownAuthors_ScriptViolations.csv (if any)

Run it like:
    python unknown_author.py
"""

import asyncio
import json
import os
import re
import sys
import time

import config
import pipeline_core as pc


_HERE = os.path.dirname(os.path.abspath(__file__))
_PARENT = os.path.dirname(_HERE)

# Source file — dedicated Unknown-author-only workbook.
# (Filtering on the fly is no longer needed; this file contains all 2,814 unknowns.)
RAW_INPUT_FILE = os.path.join(_PARENT, "Unknown Authors Only.xlsx")

OUTPUT_FILE     = os.path.join(_HERE, "SINDHI_SANCHAYA_UnknownAuthors_Cleaned.xlsx")
REPORT_FILE     = os.path.join(_HERE, "SINDHI_SANCHAYA_UnknownAuthors_ChangeReport.csv")
VIOLATIONS_FILE = os.path.join(_HERE, "SINDHI_SANCHAYA_UnknownAuthors_ScriptViolations.csv")
CHECKPOINT_FILE = os.path.join(_HERE, "unknown_checkpoint.pkl")

# Canonical Unknown-author markers (forced into every output row)
UNKNOWN_EN = "Unknown Author"
UNKNOWN_DV = "अज्ञात लेखक"
UNKNOWN_PA = "نامعلوم ليکڪ"


# ====================================================================
# Book-only prompt (smaller than the main one — no author fields)
# ====================================================================
BOOK_ONLY_PROMPT = """Fix/transliterate Sindhi book title across 3 scripts. Roman authoritative=Title Case. Strict scripts: english=Roman, devanagari=Devanagari, perso_arabic=Sindhi-Arabic.

TRANSLITERATION (Roman → Devanagari → Perso-Arabic):
Implosives:    b→ॿ→ٻ  |  j→ॼ→ڄ  |  d→ॾ→ڏ  |  g→ॻ→ڳ
Aspirated:     bh→भ→ڀ  |  ph→फ→ڦ  |  th→थ→ٿ  |  dh→ध→ڌ  |  kh→ख→کھ  |  gh→घ→گھ  |  ch→च→چ  |  chh→छ→ڇ  |  jh→झ→جھ
Sibilants:     s→स→س  |  sh→श→ش  |  z→ज़→ز
Retroflexes:   ṭ→ट→ٽ  |  ḍ→ड→ڊ  |  ṛ→ड़→ڙ  |  ṇ→ण→ڻ
Nasals:        n→न→ن  |  ng→ङ→ڱ  |  ny→ञ→ڃ  |  m→म→م
Long vowels:   ā/aa→आ→ا  |  ī/ee→ई→ي  |  ū/oo→ऊ→و  |  e→ए→ي  |  o→ओ→و
Roman style:   ALA-LC Sindhi (loc.gov) — preserve ā ī ū ṭ ḍ ṇ ṛ ñ ṅ ś ḥ marks when present in any source.

RULES:
1. Roman is authoritative AFTER cleaning. If Roman is garbled, fall back to Devanagari > Perso-Arabic, then re-derive clean Roman.
2. Preserve diacritics (ā ī ū ṭ ḍ ṇ ś ṣ etc.) when present.
3. Strip noise: ALL CAPS → Title Case; birth/death year ranges; empty brackets; double spaces; per-letter virama junk (e.g. "किस्ह्ननि" → "किशनानी").
4. If a field already looks correct, return UNCHANGED. Never invent facts.
5. If you recognize the book as a real Sindhi work, prefer the canonical published spelling. If unsure, transliterate literally without inventing.

Respond ONLY with valid JSON, no markdown:
{
  "book_name_english": "...",
  "book_name_devanagari": "...",
  "book_name_perso_arabic": "..."
}

ORIGINAL DATA:
"""


def build_book_prompt(book_fields):
    """book_fields = (book_en, book_dv, book_pa)"""
    return BOOK_ONLY_PROMPT + (
        f"Book English: {book_fields[0]}\n"
        f"Book Devanagari: {book_fields[1]}\n"
        f"Book Perso-Arabic: {book_fields[2]}\n"
    )


# ====================================================================
# Unknown-author detection (lightweight, no config dependency)
# ====================================================================
def is_unknown_author(en, dv, pa) -> bool:
    en_s = (en or "").strip().lower()
    dv_s = (dv or "").strip()
    pa_s = (pa or "").strip()
    en_unk = en_s in ("", "nan", "unknown", "unknown author", "anonymous")
    dv_unk = dv_s in ("", "nan", UNKNOWN_DV)
    pa_unk = pa_s in ("", "nan", UNKNOWN_PA)
    return en_unk and dv_unk and pa_unk


def filter_unknown_rows(all_rows):
    """Return [header] + only the rows whose author is Unknown across all 3 scripts."""
    header = all_rows[0]
    auth_en_i = pc._idx(pc.AUTH_EN_COL)
    auth_dv_i = pc._idx(pc.AUTH_DV_COL)
    auth_pa_i = pc._idx(pc.AUTH_PA_COL)
    out = [header]
    for row in all_rows[1:]:
        if not row:
            continue
        if is_unknown_author(row[auth_en_i], row[auth_dv_i], row[auth_pa_i]):
            out.append(list(row))
    return out


def build_book_unique_pairs(rows):
    """Group rows by (book_en, book_dv, book_pa) — author isn't part of the key."""
    book_en_i = pc._idx(pc.BOOK_EN_COL)
    book_dv_i = pc._idx(pc.BOOK_DV_COL)
    book_pa_i = pc._idx(pc.BOOK_PA_COL)
    pairs = {}
    for i, row in enumerate(rows[1:], start=1):
        if not row:
            continue
        key = (
            (row[book_en_i] or "").strip() if row[book_en_i] is not None else "",
            (row[book_dv_i] or "").strip() if row[book_dv_i] is not None else "",
            (row[book_pa_i] or "").strip() if row[book_pa_i] is not None else "",
        )
        entry = pairs.setdefault(key, {"fields": key, "row_indices": []})
        entry["row_indices"].append(i)
    return pairs


# ====================================================================
# Apply book corrections (with author force-write)
# ====================================================================
_BOOK_FIELD_MAP = [
    ("book_name_english",      pc.BOOK_EN_COL, "english"),
    ("book_name_devanagari",   pc.BOOK_DV_COL, "devanagari"),
    ("book_name_perso_arabic", pc.BOOK_PA_COL, "perso_arabic"),
]


def apply_book_corrections(rows, pair, correction, log):
    """
    Write book corrections (with script validation) AND force author columns
    to canonical Unknown markers for every affected row.
    """
    # 1. Force author columns to canonical strings on every affected row
    for row_i in pair["row_indices"]:
        for col, canonical in [
            (pc.AUTH_EN_COL, UNKNOWN_EN),
            (pc.AUTH_DV_COL, UNKNOWN_DV),
            (pc.AUTH_PA_COL, UNKNOWN_PA),
        ]:
            ci = pc._idx(col)
            old = rows[row_i][ci]
            old_s = "" if old is None else str(old).strip()
            if old_s != canonical:
                rows[row_i][ci] = canonical
                log.add(rows[row_i][pc._idx(pc.COL_ID)], col, old, canonical)

    # 2. Write book fields with script-integrity guard
    for src_key, col_name, target in _BOOK_FIELD_MAP:
        new_val = correction.get(src_key)
        if new_val is None:
            continue
        new_val = str(new_val).strip()
        if not new_val:
            continue

        if pc.has_wrong_script(new_val, target):
            for row_i in pair["row_indices"]:
                log.add_violation(
                    rows[row_i][pc._idx(pc.COL_ID)], col_name, target,
                    new_val, rows[row_i][pc._idx(col_name)],
                )
            continue

        ci = pc._idx(col_name)
        for row_i in pair["row_indices"]:
            old = rows[row_i][ci]
            old_s = "" if old is None else str(old).strip()
            if old_s == new_val:
                continue
            rows[row_i][ci] = new_val
            log.add(rows[row_i][pc._idx(pc.COL_ID)], col_name, old, new_val)


# ====================================================================
# Async pair processing
# ====================================================================
async def process_pair(key, pair, client_cfg, meter, semaphore):
    async with semaphore:
        prompt = build_book_prompt(pair["fields"])
        try:
            corrections = await pc.call_gemini_async(client_cfg, prompt, meter)
            if not isinstance(corrections, list) or not corrections:
                return key, {"_error": "bad response shape"}
            return key, corrections[0]
        except Exception as e:
            return key, {"_error": f"{type(e).__name__}: {str(e)[:100]}"}


def truncate(s, n=42):
    s = (s or "").replace("\n", " ").strip()
    return s if len(s) <= n else s[:n - 1] + "…"


# ====================================================================
# Main
# ====================================================================
async def main() -> None:
    print("=" * 70)
    print(" UNKNOWN-AUTHOR BOOK CLEANER (concurrent)")
    print("=" * 70)
    print(f" Source       : {RAW_INPUT_FILE}")
    print(f" Output       : {OUTPUT_FILE}")
    print(f" Report       : {REPORT_FILE}")
    print(f" Model        : {config.MODEL_NAME}")
    print(f" Concurrency  : {config.CONCURRENCY_LIMIT} parallel calls")
    print()

    # 1. Load Unknown-author-only workbook (no filtering needed)
    rows = pc.load_excel(RAW_INPUT_FILE)
    print(f"[i] Loaded {len(rows)-1:,} Unknown-author rows.")

    # 2. Dedup by book triple
    pairs = build_book_unique_pairs(rows)
    keys = list(pairs.keys())
    total = len(keys)
    print(f"[i] Unique book pairs to process: {total:,}")
    print()

    # 3. Resume from checkpoint if present
    log = pc.ChangeLog()
    processed: set = set()
    ckpt = pc.load_checkpoint(CHECKPOINT_FILE)
    if ckpt:
        rows = ckpt["rows"]
        log.changes = ckpt["changes"]
        processed = set(tuple(k) for k in ckpt["processed_keys"])
        print(f"[i] Resumed from checkpoint: {len(processed):,} pairs done.")
        print()

    client_cfg = pc.setup_gemini()
    meter = pc.TokenMeter()

    todo = [k for k in keys if k not in processed]
    n_todo = len(todo)
    print(f"[i] Pairs remaining: {n_todo:,}")
    print()

    if n_todo == 0:
        print("[i] Nothing to do.")
        return

    # 4. Concurrent processing
    semaphore = asyncio.Semaphore(config.CONCURRENCY_LIMIT)
    tasks = [
        asyncio.create_task(process_pair(k, pairs[k], client_cfg, meter, semaphore))
        for k in todo
    ]

    t_start = time.time()
    successes = 0
    failures = 0
    done = 0

    for coro in asyncio.as_completed(tasks):
        key, result = await coro
        done += 1
        pair = pairs[key]
        first_row_id = rows[pair["row_indices"][0]][pc._idx(pc.COL_ID)]
        label = truncate(pair["fields"][0] or pair["fields"][1] or pair["fields"][2] or "<empty>")

        if "_error" in result:
            failures += 1
            print(f"  [{done:>5}/{n_todo}] ✗ id={first_row_id:<6} FAIL ({result['_error'][:60]})",
                  flush=True)
        else:
            before = len(log.changes)
            apply_book_corrections(rows, pair, result, log)
            delta = len(log.changes) - before
            successes += 1
            processed.add(key)

            elapsed = time.time() - t_start
            rate = done / elapsed if elapsed > 0 else 0
            eta_min = ((n_todo - done) / rate / 60) if rate > 0 else 0
            usd = meter.cost_usd()
            print(f"  [{done:>5}/{n_todo}] ✓ id={first_row_id:<6} +{delta} chg | "
                  f"${usd:.3f} | {rate:.1f}/s | ETA {eta_min:>4.1f}m | {label}",
                  flush=True)

        if done % config.CHECKPOINT_EVERY == 0:
            pc.save_checkpoint(CHECKPOINT_FILE, list(processed), rows, log.changes)
            print(f"  [ckpt] saved at {done:,} completed pairs", flush=True)

    # 5. Save final outputs
    pc.save_excel(rows, OUTPUT_FILE)
    log.save_csv(REPORT_FILE)
    log.save_violations_csv(VIOLATIONS_FILE)
    log.summary()
    meter.report()

    total_min = (time.time() - t_start) / 60
    print()
    print("=" * 70)
    print(f" Done in {total_min:.1f} minutes. Successes: {successes:,}  Failures: {failures:,}")
    print(f" Output     : {OUTPUT_FILE}")
    print(f" Report     : {REPORT_FILE}")
    if log.violations:
        print(f" Violations : {VIOLATIONS_FILE}  ({len(log.violations):,} rejected)")
    print("=" * 70)

    if os.path.exists(CHECKPOINT_FILE) and failures == 0:
        os.remove(CHECKPOINT_FILE)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\n[!] Interrupted. Re-run to resume from last checkpoint.")
        sys.exit(1)

"""
Core machinery for the Sindhi Sanchaya cleaner pipeline.

Public surface:
    load_excel(path)                                 -> rows (list of lists, row 0 = header)
    save_excel(rows, path)                           -> writes xlsx
    build_unique_pairs(rows)                         -> dict[key, {fields, row_indices}]
    setup_gemini()                                   -> (client, gen_config)
    call_gemini(model, prompt, meter)                -> parsed JSON list, with retries
    apply_corrections(rows, pair, correction, log)   -> mutates rows + appends to log
    ChangeLog                                        -> records every cell change
    TokenMeter                                       -> tracks input/output tokens + cost
    IMPROVED_PROMPT                                  -> the cleaning prompt (Claude-designed)
"""

import asyncio
import csv
import json
import random
import re
import time
from typing import Any, Dict, List, Optional, Tuple

from openpyxl import Workbook, load_workbook

import config


# ====================================================================
# Cleaning prompt — compact, cost-effective
# ====================================================================
SYSTEM_PROMPT = """Fix/transliterate Sindhi catalog. Fact-check real names. Strict scripts: english=Roman, devanagari=Devanagari, perso_arabic=Sindhi-Arabic. Roman (Book & Author)=Title Case.

SINDHI TRANSLITERATION (Roman → Devanagari → Perso-Arabic):
Implosives:    b→ॿ→ٻ  |  j→ॼ→ڄ  |  d→ॾ→ڏ  |  g→ॻ→ڳ
Aspirated:     bh→भ→ڀ  |  ph→फ→ڦ  |  th→थ→ٿ  |  dh→ध→ڌ  |  kh→ख→کھ  |  gh→घ→گھ  |  ch→च→چ  |  chh→छ→ڇ  |  jh→झ→جھ
Sibilants:     s→स→س  |  sh→श→ش  |  z→ज़→ز
Retroflexes:   ṭ→ट→ٽ  |  ḍ→ड→ڊ  |  ṛ→ड़→ڙ  |  ṇ→ण→ڻ
Nasals:        n→न→ن  |  ng→ङ→ڱ  |  ny→ञ→ڃ  |  m→म→م
Long vowels:   ā/aa→आ→ا  |  ī/ee→ई→ي  |  ū/oo→ऊ→و  |  e→ए→ي  |  o→ओ→و
Roman style:   Follow ALA-LC Sindhi romanization (loc.gov standard) — preserve ā ī ū ṭ ḍ ṇ ṛ ñ ṅ ś ḥ marks when present in any source.

RULES:
1. English (Roman) is authoritative AFTER cleaning. If Roman is garbled, fall back to Devanagari > Perso-Arabic, then re-derive clean Roman.
2. Diacritics (ā ī ū ṭ ḍ ṇ ś ṣ etc.) are FINE — preserve them when present.
3. Strip noise: ALL CAPS → Title Case; "Lastname, Firstname" → "Firstname Lastname"; author appended to title (e.g. title "Madur peeda Valiram Kishnani" + author "Kishnani, Valiram" → title becomes "Madhur Peeda"); birth/death year ranges; empty brackets; double spaces; per-letter virama junk (e.g. "किस्ह्ननि" → "किशनानी").
4. If a field already looks correct, return UNCHANGED. Never invent facts.
5. If you recognize the book or author as a real Sindhi work/person, prefer the canonical published spelling. If unsure, transliterate literally without inventing.

Respond ONLY with valid JSON, no markdown:
{
  "book_name_english": "...",
  "book_name_devanagari": "...",
  "book_name_perso_arabic": "...",
  "author_name_english": "...",
  "author_name_devanagari": "...",
  "author_name_perso_arabic": "..."
}

ORIGINAL DATA:
"""


# ====================================================================
# Column names — must match the Raw Database headers exactly
# ====================================================================
COL_ID            = "id"
BOOK_EN_COL       = "book_name_english"
BOOK_DV_COL       = "book_name_devanagari"
BOOK_PA_COL       = "book_name_perso_arabic"
AUTH_EN_COL       = "author_name_english"
AUTH_DV_COL       = "author_name_devanagari"
AUTH_PA_COL       = "author_name_perso_arabic"

# These get filled in by load_excel
_HEADER_INDEX: Dict[str, int] = {}


# ====================================================================
# Script-integrity validator
# Rejects any output value containing characters from the wrong script.
# ====================================================================
_LATIN_RE  = re.compile(r"[A-Za-z]")
_DEVA_RE   = re.compile(r"[\u0900-\u097F]")
_ARABIC_RE = re.compile(r"[\u0600-\u06FF\u0750-\u077F\uFB50-\uFDFF\uFE70-\uFEFF]")

_TARGET_SCRIPT = {
    "english":      ("english",      lambda v: bool(_DEVA_RE.search(v) or _ARABIC_RE.search(v))),
    "devanagari":   ("devanagari",   lambda v: bool(_LATIN_RE.search(v) or _ARABIC_RE.search(v))),
    "perso_arabic": ("perso_arabic", lambda v: bool(_LATIN_RE.search(v) or _DEVA_RE.search(v))),
}

# Column → target script
_COL_SCRIPT = {
    BOOK_EN_COL:  "english",
    AUTH_EN_COL:  "english",
    BOOK_DV_COL:  "devanagari",
    AUTH_DV_COL:  "devanagari",
    BOOK_PA_COL:  "perso_arabic",
    AUTH_PA_COL:  "perso_arabic",
}


def has_wrong_script(value: str, target: str) -> bool:
    """Return True if `value` contains characters from a script other than `target`."""
    if not value:
        return False
    _, checker = _TARGET_SCRIPT[target]
    return checker(value)


def _idx(name: str) -> int:
    return _HEADER_INDEX[name]


# ====================================================================
# Excel I/O
# ====================================================================
def load_excel(path: str) -> List[List[Any]]:
    """Load the workbook and return rows (list of lists). Row 0 is the header."""
    wb = load_workbook(path, data_only=True)
    ws = wb.active
    rows = [list(r) for r in ws.iter_rows(values_only=True)]
    if not rows:
        raise ValueError(f"{path} appears to be empty.")
    header = rows[0]
    global _HEADER_INDEX
    _HEADER_INDEX = {str(h): i for i, h in enumerate(header)}
    required = [BOOK_EN_COL, BOOK_DV_COL, BOOK_PA_COL,
                AUTH_EN_COL, AUTH_DV_COL, AUTH_PA_COL]
    missing = [c for c in required if c not in _HEADER_INDEX]
    if missing:
        raise ValueError(f"Missing columns in input: {missing}")
    return rows


def save_excel(rows: List[List[Any]], path: str) -> None:
    """Write rows back to an .xlsx file."""
    wb = Workbook()
    ws = wb.active
    for r in rows:
        ws.append(r)
    wb.save(path)


# ====================================================================
# Deduplication
# ====================================================================
def _norm(v: Any) -> str:
    if v is None:
        return ""
    return str(v).strip()


def build_unique_pairs(rows: List[List[Any]]) -> Dict[Tuple[str, ...], Dict[str, Any]]:
    """
    Group rows by their (book_en, book_dv, book_pa, auth_en, auth_dv, auth_pa) tuple.
    Returns: {key_tuple: {"fields": (en,dv,pa,en,dv,pa), "row_indices": [int,...]}}
    """
    pairs: Dict[Tuple[str, ...], Dict[str, Any]] = {}
    for i, row in enumerate(rows[1:], start=1):
        if not row:
            continue
        fields = (
            _norm(row[_idx(BOOK_EN_COL)]),
            _norm(row[_idx(BOOK_DV_COL)]),
            _norm(row[_idx(BOOK_PA_COL)]),
            _norm(row[_idx(AUTH_EN_COL)]),
            _norm(row[_idx(AUTH_DV_COL)]),
            _norm(row[_idx(AUTH_PA_COL)]),
        )
        entry = pairs.setdefault(fields, {"fields": fields, "row_indices": []})
        entry["row_indices"].append(i)
    return pairs


# ====================================================================
# Gemini client
# ====================================================================
def setup_gemini() -> Tuple[Any, Any]:
    """Returns (client, gen_config). Raises if API key isn't set."""
    from google import genai
    from google.genai import types

    if not config.GEMINI_API_KEY or config.GEMINI_API_KEY.startswith("PASTE_"):
        raise SystemExit("[ERROR] Set the GEMINI_API_KEY environment variable before running (see README).")

    client = genai.Client(api_key=config.GEMINI_API_KEY)
    kwargs: Dict[str, Any] = dict(
        temperature=0.1,
        response_mime_type="application/json",
    )
    if config.DISABLE_THINKING:
        try:
            kwargs["thinking_config"] = types.ThinkingConfig(thinking_budget=0)
        except Exception:
            pass
    gen_config = types.GenerateContentConfig(**kwargs)
    return client, gen_config


class TokenMeter:
    """Tracks input/output tokens and computes cost in USD."""

    def __init__(self) -> None:
        self.input_tokens = 0
        self.output_tokens = 0
        self.calls = 0

    def add(self, in_tok: int, out_tok: int) -> None:
        self.input_tokens += in_tok
        self.output_tokens += out_tok
        self.calls += 1

    def cost_usd(self) -> float:
        c_in = self.input_tokens / 1_000_000 * config.PRICE_INPUT_PER_M
        c_out = self.output_tokens / 1_000_000 * config.PRICE_OUTPUT_PER_M
        return c_in + c_out

    def report(self) -> None:
        print()
        print("=" * 60)
        print(" Token usage report")
        print("=" * 60)
        print(f"  Calls           : {self.calls:,}")
        print(f"  Input tokens    : {self.input_tokens:,}")
        print(f"  Output tokens   : {self.output_tokens:,}")
        print(f"  Cost (USD)      : ${self.cost_usd():.4f}")
        print(f"  Cost (INR ~83)  : ₹{self.cost_usd() * 83:.2f}")
        print("=" * 60)


def call_gemini(client_cfg: Tuple[Any, Any], prompt: str, meter: TokenMeter) -> List[Dict[str, Any]]:
    """Call Gemini once. Retries on transient failures. Returns parsed JSON array."""
    client, gen_config = client_cfg
    last_err: Optional[Exception] = None
    for attempt in range(config.MAX_RETRIES):
        try:
            resp = client.models.generate_content(
                model=config.MODEL_NAME,
                contents=prompt,
                config=gen_config,
            )
            text = (resp.text or "").strip()
            # Track tokens from response.usage_metadata if available
            try:
                meta = resp.usage_metadata
                in_tok = getattr(meta, "prompt_token_count", 0) or 0
                out_tok = getattr(meta, "candidates_token_count", 0) or 0
                meter.add(in_tok, out_tok)
            except Exception:
                pass
            # Strip accidental markdown fences if any
            if text.startswith("```"):
                text = re.sub(r"^```[a-zA-Z]*\n?", "", text)
                text = re.sub(r"\n?```$", "", text)
            data = json.loads(text)
            if isinstance(data, dict):
                data = [data]
            if not isinstance(data, list):
                raise ValueError(f"Expected JSON array, got {type(data).__name__}")
            return data
        except Exception as e:
            last_err = e
            if attempt < config.MAX_RETRIES - 1:
                time.sleep(1.5 * (attempt + 1))
            continue
    raise RuntimeError(f"Gemini call failed after {config.MAX_RETRIES} attempts: {last_err}")


# ====================================================================
# Change log
# ====================================================================
class ChangeLog:
    """Records every per-cell change for the final CSV report."""

    HEADERS = [
        "row_id", "column", "old_value", "new_value",
    ]

    def __init__(self) -> None:
        self.changes: List[Dict[str, Any]] = []
        self.violations: List[Dict[str, Any]] = []

    def add(self, row_id: Any, column: str, old: Any, new: Any) -> None:
        old_s = "" if old is None else str(old)
        new_s = "" if new is None else str(new)
        if old_s == new_s:
            return
        self.changes.append({
            "row_id": row_id,
            "column": column,
            "old_value": old_s,
            "new_value": new_s,
        })

    def save_csv(self, path: str) -> None:
        with open(path, "w", encoding="utf-8-sig", newline="") as f:
            w = csv.DictWriter(f, fieldnames=self.HEADERS)
            w.writeheader()
            for c in self.changes:
                w.writerow(c)

    def add_violation(self, row_id: Any, column: str, target: str,
                      attempted_value: str, kept_value: Any) -> None:
        self.violations.append({
            "row_id": row_id,
            "column": column,
            "target_script": target,
            "rejected_value": attempted_value,
            "kept_value": "" if kept_value is None else str(kept_value),
        })

    def save_violations_csv(self, path: str) -> None:
        if not self.violations:
            return
        headers = ["row_id", "column", "target_script", "rejected_value", "kept_value"]
        with open(path, "w", encoding="utf-8-sig", newline="") as f:
            w = csv.DictWriter(f, fieldnames=headers)
            w.writeheader()
            for v in self.violations:
                w.writerow(v)

    def summary(self) -> None:
        print()
        print("=" * 60)
        print(" Change log summary")
        print("=" * 60)
        if not self.changes:
            print("  No changes recorded.")
            print("=" * 60)
            return
        per_col: Dict[str, int] = {}
        for c in self.changes:
            per_col[c["column"]] = per_col.get(c["column"], 0) + 1
        for col, n in sorted(per_col.items(), key=lambda x: -x[1]):
            print(f"  {col:<28} {n:>6,}")
        print(f"  {'TOTAL':<28} {len(self.changes):>6,}")
        if self.violations:
            print(f"  {'SCRIPT VIOLATIONS REJECTED':<28} {len(self.violations):>6,}")
        print("=" * 60)


# ====================================================================
# Apply API corrections back into the rows
# ====================================================================
_FIELD_MAP = [
    # (source_key_in_correction, target_column_name)
    ("book_name_english",        BOOK_EN_COL),
    ("book_name_devanagari",     BOOK_DV_COL),
    ("book_name_perso_arabic",   BOOK_PA_COL),
    ("author_name_english",      AUTH_EN_COL),
    ("author_name_devanagari",   AUTH_DV_COL),
    ("author_name_perso_arabic", AUTH_PA_COL),
]


def apply_corrections(
    rows: List[List[Any]],
    pair: Dict[str, Any],
    correction: Dict[str, Any],
    log: ChangeLog,
) -> None:
    """
    Push corrected values from `correction` into every row in pair["row_indices"].
    Records each cell change in `log`.
    """
    for src_key, col_name in _FIELD_MAP:
        new_val = correction.get(src_key)
        if new_val is None:
            continue
        new_val = str(new_val).strip()
        if not new_val:
            continue

        # SCRIPT-INTEGRITY GUARD: reject values containing wrong-script chars.
        target = _COL_SCRIPT[col_name]
        if has_wrong_script(new_val, target):
            for row_i in pair["row_indices"]:
                log.add_violation(
                    rows[row_i][_idx(COL_ID)], col_name, target,
                    new_val, rows[row_i][_idx(col_name)],
                )
            continue  # keep old value, do not write

        col_i = _idx(col_name)
        for row_i in pair["row_indices"]:
            row = rows[row_i]
            old_val = row[col_i]
            old_s = "" if old_val is None else str(old_val).strip()
            if old_s == new_val:
                continue
            row[col_i] = new_val
            log.add(rows[row_i][_idx(COL_ID)], col_name, old_val, new_val)


# ====================================================================
# Checkpointing
# ====================================================================
def save_checkpoint(
    path: str,
    processed_keys: List[Tuple[str, ...]],
    rows: List[List[Any]],
    log_changes: List[Dict[str, Any]],
) -> None:
    """Pickle a snapshot of progress so a crash doesn't lose work."""
    import pickle
    with open(path, "wb") as f:
        pickle.dump({
            "processed_keys": processed_keys,
            "rows": rows,
            "changes": log_changes,
        }, f)


def load_checkpoint(path: str) -> Optional[Dict[str, Any]]:
    import os, pickle
    if not os.path.exists(path):
        return None
    with open(path, "rb") as f:
        return pickle.load(f)

async def call_gemini_async(client_cfg, prompt: str, meter: "TokenMeter") -> List[Dict[str, Any]]:
    """Async version of call_gemini. Exponential backoff retries on transient errors."""
    client, gen_config = client_cfg
    last_err: Optional[Exception] = None
    for attempt in range(config.MAX_RETRIES):
        try:
            resp = await client.aio.models.generate_content(
                model=config.MODEL_NAME,
                contents=prompt,
                config=gen_config,
            )
            text = (resp.text or "").strip()
            try:
                meta = resp.usage_metadata
                in_tok = getattr(meta, "prompt_token_count", 0) or 0
                out_tok = getattr(meta, "candidates_token_count", 0) or 0
                meter.add(in_tok, out_tok)
            except Exception:
                pass
            if text.startswith("```"):
                text = re.sub(r"^```[a-zA-Z]*\n?", "", text)
                text = re.sub(r"\n?```$", "", text)
            data = json.loads(text)
            if isinstance(data, dict):
                data = [data]
            if not isinstance(data, list):
                raise ValueError(f"Expected JSON array, got {type(data).__name__}")
            return data
        except Exception as e:
            last_err = e
            if attempt < config.MAX_RETRIES - 1:
                # exponential backoff + random jitter so the concurrent
                # retries do not all wake up inside the same throttled minute
                await asyncio.sleep(2 ** attempt + random.uniform(0, 3))
            continue
    raise RuntimeError(f"Gemini async call failed after {config.MAX_RETRIES} attempts: {last_err}")


def build_prompt(fields):
    """Build the full prompt for a single (book, author) pair across 3 scripts.
    `fields` is the 6-tuple from build_unique_pairs.
    """
    return SYSTEM_PROMPT + (
        f"Book English: {fields[0]}\n"
        f"Book Devanagari: {fields[1]}\n"
        f"Book Perso-Arabic: {fields[2]}\n"
        f"Author English: {fields[3]}\n"
        f"Author Devanagari: {fields[4]}\n"
        f"Author Perso-Arabic: {fields[5]}\n"
    )

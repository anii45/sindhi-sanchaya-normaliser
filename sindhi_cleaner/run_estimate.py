"""
COST PROBE — run before the full pipeline.

What it does:
  1. Loads the workbook.
  2. Picks SAMPLE_SIZE random unique pairs from the dataset.
  3. Calls Gemini once per pair.
  4. Reports actual avg tokens/pair, then extrapolates total $ for all pairs.

Run it like:
    python run_estimate.py
"""

import json
import os
import random
import sys
import time

import config
import pipeline_core as pc


SAMPLE_SIZE = 100
SEED        = 42  # fixed seed → reproducible sample

_HERE = os.path.dirname(os.path.abspath(__file__))
ESTIMATE_OUTPUT_FILE = os.path.join(_HERE, "100_Test_Cleaned.xlsx")
ESTIMATE_REPORT_FILE = os.path.join(_HERE, "100_Test_ChangeReport.csv")
ESTIMATE_VIOLATIONS_FILE = os.path.join(_HERE, "100_Test_ScriptViolations.csv")


def main() -> None:
    print("=" * 64)
    print(f" Cost-probe: {SAMPLE_SIZE} unique pairs, {config.MODEL_NAME}")
    print("=" * 64)

    rows = pc.load_excel(config.INPUT_FILE)
    print(f"[i] Loaded {len(rows)-1:,} rows from {config.INPUT_FILE}")

    pairs = pc.build_unique_pairs(rows)
    total_pairs = len(pairs)
    print(f"[i] Unique pairs to process: {total_pairs:,}")
    print()

    rng = random.Random(SEED)
    sample_keys = rng.sample(list(pairs.keys()), min(SAMPLE_SIZE, total_pairs))

    client_cfg = pc.setup_gemini()
    meter = pc.TokenMeter()
    log = pc.ChangeLog()

    # Remember which rows we touch so we can dump just those at the end.
    touched_row_indices: set = set()

    t_start = time.time()
    successes = 0
    failures = 0

    for idx, key in enumerate(sample_keys, 1):
        pair = pairs[key]
        prompt = pc.build_prompt(pair["fields"])
        first_row_id = rows[pair["row_indices"][0]][pc._idx(pc.COL_ID)]

        t0 = time.time()
        try:
            corrections = pc.call_gemini(client_cfg, prompt, meter)
            pc.apply_corrections(rows, pair, corrections[0], log)
            touched_row_indices.update(pair["row_indices"])
            successes += 1
            elapsed = time.time() - t0
            cost_so_far = meter.cost_usd()
            print(f"  [{idx:>2}/{len(sample_keys)}] {elapsed:>4.1f}s ✓ id={first_row_id:<6} "
                  f"running cost ${cost_so_far:.4f}", flush=True)
        except Exception as e:
            failures += 1
            print(f"  [{idx:>2}/{len(sample_keys)}] ✗ id={first_row_id:<6} "
                  f"FAIL ({type(e).__name__}: {str(e)[:80]})", flush=True)

    total_elapsed = time.time() - t_start

    # Save just the rows we processed (plus header) + the change log
    subset = [rows[0]] + [rows[i] for i in sorted(touched_row_indices)]
    pc.save_excel(subset, ESTIMATE_OUTPUT_FILE)
    log.save_csv(ESTIMATE_REPORT_FILE)
    log.save_violations_csv(ESTIMATE_VIOLATIONS_FILE)

    meter.report()
    log.summary()

    print()
    print("=" * 64)
    print(" TEST OUTPUT FILES")
    print("=" * 64)
    print(f"  Cleaned rows ({len(subset)-1}): {ESTIMATE_OUTPUT_FILE}")
    print(f"  Change report          : {ESTIMATE_REPORT_FILE}")
    if log.violations:
        print(f"  Script violations      : {ESTIMATE_VIOLATIONS_FILE}  ({len(log.violations)} rejected)")

    print()
    print("=" * 64)
    print(" EXTRAPOLATION TO FULL DATASET")
    print("=" * 64)
    if successes == 0:
        print("[!] No successful calls — cannot extrapolate.")
        sys.exit(1)

    avg_in = meter.input_tokens / successes
    avg_out = meter.output_tokens / successes
    avg_sec = total_elapsed / successes

    full_in  = avg_in  * total_pairs
    full_out = avg_out * total_pairs
    full_cost = (full_in / 1_000_000 * config.PRICE_INPUT_PER_M
                 + full_out / 1_000_000 * config.PRICE_OUTPUT_PER_M)
    full_minutes = avg_sec * total_pairs / 60

    print(f"  Avg input tokens / pair : {avg_in:,.0f}")
    print(f"  Avg output tokens / pair: {avg_out:,.0f}")
    print(f"  Avg seconds / pair      : {avg_sec:.2f}")
    print()
    print(f"  Pairs in full dataset   : {total_pairs:,}")
    print(f"  Projected input tokens  : {full_in:,.0f}")
    print(f"  Projected output tokens : {full_out:,.0f}")
    print(f"  Projected COST (USD)    : ${full_cost:.2f}")
    print(f"  Projected COST (INR ~83): ₹{full_cost*83:.0f}")
    print(f"  Projected RUNTIME       : ~{full_minutes:.0f} minutes "
          f"({full_minutes/60:.1f} hours)")
    print("=" * 64)
    print()
    print("If those numbers look OK, run:  python run_full.py")


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\n[!] Interrupted by user.")
     
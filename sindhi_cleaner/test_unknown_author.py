"""
TEST UNKNOWN-AUTHOR CLEANER — 100-row probe.

Same logic as unknown_author.py but:
  - Processes only SAMPLE_SIZE random unique book pairs
  - Sequential (simpler, no need for concurrency on 100 calls)
  - Reports avg tokens/cost then extrapolates to the full unknown set

Run it like:
    python test_unknown_author.py

Outputs (in sindhi_cleaner/):
    100_Test_UnknownAuthors_Cleaned.xlsx
    100_Test_UnknownAuthors_ChangeReport.csv
    100_Test_UnknownAuthors_ScriptViolations.csv (if any)
"""

import os
import random
import sys
import time

import config
import pipeline_core as pc
import unknown_author as ua


SAMPLE_SIZE = 100
SEED        = 42

_HERE = os.path.dirname(os.path.abspath(__file__))
TEST_OUTPUT_FILE     = os.path.join(_HERE, "100_Test_UnknownAuthors_Cleaned.xlsx")
TEST_REPORT_FILE     = os.path.join(_HERE, "100_Test_UnknownAuthors_ChangeReport.csv")
TEST_VIOLATIONS_FILE = os.path.join(_HERE, "100_Test_UnknownAuthors_ScriptViolations.csv")


def main():
    print("=" * 64)
    print(f" UNKNOWN-AUTHOR cost probe: {SAMPLE_SIZE} pairs, {config.MODEL_NAME}")
    print("=" * 64)

    rows = pc.load_excel(ua.RAW_INPUT_FILE)
    print(f"[i] Loaded {len(rows)-1:,} Unknown-author rows.")

    pairs = ua.build_book_unique_pairs(rows)
    total_pairs = len(pairs)
    print(f"[i] Unique book pairs: {total_pairs:,}")
    print()

    rng = random.Random(SEED)
    sample_keys = rng.sample(list(pairs.keys()), min(SAMPLE_SIZE, total_pairs))

    client_cfg = pc.setup_gemini()
    meter = pc.TokenMeter()
    log = pc.ChangeLog()
    touched_row_indices: set = set()

    t_start = time.time()
    successes = 0
    failures = 0

    for idx, key in enumerate(sample_keys, 1):
        pair = pairs[key]
        prompt = ua.build_book_prompt(pair["fields"])
        first_row_id = rows[pair["row_indices"][0]][pc._idx(pc.COL_ID)]

        t0 = time.time()
        try:
            corrections = pc.call_gemini(client_cfg, prompt, meter)
            ua.apply_book_corrections(rows, pair, corrections[0], log)
            touched_row_indices.update(pair["row_indices"])
            successes += 1
            elapsed = time.time() - t0
            cost_so_far = meter.cost_usd()
            label = ua.truncate(pair["fields"][0] or pair["fields"][1] or pair["fields"][2] or "<empty>", 40)
            print(f"  [{idx:>3}/{len(sample_keys)}] {elapsed:>4.1f}s ✓ id={first_row_id:<6} "
                  f"${cost_so_far:.4f} | {label}", flush=True)
        except Exception as e:
            failures += 1
            print(f"  [{idx:>3}/{len(sample_keys)}] ✗ id={first_row_id:<6} "
                  f"FAIL ({type(e).__name__}: {str(e)[:80]})", flush=True)

    total_elapsed = time.time() - t_start

    # Save just the touched rows + the change log + violations
    subset = [rows[0]] + [rows[i] for i in sorted(touched_row_indices)]
    pc.save_excel(subset, TEST_OUTPUT_FILE)
    log.save_csv(TEST_REPORT_FILE)
    log.save_violations_csv(TEST_VIOLATIONS_FILE)

    meter.report()
    log.summary()

    print()
    print("=" * 64)
    print(" TEST OUTPUT FILES")
    print("=" * 64)
    print(f"  Cleaned rows ({len(subset)-1}): {TEST_OUTPUT_FILE}")
    print(f"  Change report          : {TEST_REPORT_FILE}")
    if log.violations:
        print(f"  Script violations      : {TEST_VIOLATIONS_FILE}  ({len(log.violations)} rejected)")

    print()
    print("=" * 64)
    print(" EXTRAPOLATION TO ALL UNKNOWN-AUTHOR ROWS")
    print("=" * 64)
    if successes == 0:
        print("[!] No successful calls — cannot extrapolate.")
        sys.exit(1)

    avg_in  = meter.input_tokens / successes
    avg_out = meter.output_tokens / successes
    avg_sec = total_elapsed / successes

    full_in   = avg_in  * total_pairs
    full_out  = avg_out * total_pairs
    full_cost = (full_in / 1_000_000 * config.PRICE_INPUT_PER_M
                 + full_out / 1_000_000 * config.PRICE_OUTPUT_PER_M)
    full_minutes = avg_sec * total_pairs / 60 / max(config.CONCURRENCY_LIMIT, 1)

    print(f"  Avg input tokens / pair : {avg_in:,.0f}")
    print(f"  Avg output tokens / pair: {avg_out:,.0f}")
    print(f"  Avg seconds / pair      : {avg_sec:.2f}")
    print()
    print(f"  Total unknown pairs     : {total_pairs:,}")
    print(f"  Projected COST (USD)    : ${full_cost:.2f}")
    print(f"  Projected COST (INR ~83): ₹{full_cost*83:.0f}")
    print(f"  Projected RUNTIME (concurrent, {config.CONCURRENCY_LIMIT} workers): ~{full_minutes:.0f} min")
    print("=" * 64)
    print()
    print("If those numbers look OK, run:  python unknown_author.py")


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\n[!] Interrupted by user.")
        sys.exit(1)

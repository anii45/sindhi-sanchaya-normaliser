"""
RUN A BATCH OF PARTS — cleans a contiguous range of parts back to back,
unattended.

Default range: parts 4 through 39 (parts 1-3 assumed already done).

Usage:
    python run_batch.py            # runs parts 4 -> 39
    python run_batch.py 4 39       # same, explicit
    python run_batch.py 10 20      # any custom range (1-39)

Each part is processed by run_part.run_part(), so every part keeps its OWN
checkpoint and its OWN output files. Safety behaviour:
  - If one part crashes, the batch logs it and CONTINUES to the next part.
  - Re-running the batch resumes any half-done part from its checkpoint
    and skips already-finished parts cheaply (they say "Nothing to do").
  - Ctrl-C stops cleanly; just re-run to pick up where you left off.
"""

import asyncio
import sys
import time
import traceback

import run_part as rp


DEFAULT_START = 4
DEFAULT_END   = 39


def parse_range():
    if len(sys.argv) == 1:
        return DEFAULT_START, DEFAULT_END
    if len(sys.argv) == 3:
        try:
            a, b = int(sys.argv[1]), int(sys.argv[2])
        except ValueError:
            raise SystemExit("Usage: python run_batch.py [start end]  (1-39)")
        if not (1 <= a <= 39 and 1 <= b <= 39 and a <= b):
            raise SystemExit("[ERROR] start/end must be 1-39 and start <= end.")
        return a, b
    raise SystemExit("Usage: python run_batch.py [start end]   (defaults to 4 39)")


def main():
    start, end = parse_range()
    parts = list(range(start, end + 1))

    print("#" * 70)
    print(f"#  BATCH RUN  -  parts {start} to {end}   ({len(parts)} parts)")
    print("#" * 70)

    t0 = time.time()
    ok, failed = [], []

    for i, n in enumerate(parts, 1):
        print()
        print("#" * 70)
        print(f"#  [{i}/{len(parts)}]  Starting Part {n:02d}")
        print("#" * 70)
        try:
            asyncio.run(rp.run_part(n))
            ok.append(n)
        except KeyboardInterrupt:
            print(f"\n[!] Interrupted during Part {n:02d}.")
            print("    Re-run the batch to resume - finished parts are skipped")
            print(f"    and Part {n:02d} resumes from its checkpoint.")
            break
        except Exception as e:
            failed.append(n)
            print(f"\n[!] Part {n:02d} FAILED: {type(e).__name__}: {e}")
            traceback.print_exc()
            print("[i] Continuing to the next part...")

    mins = (time.time() - t0) / 60
    print()
    print("#" * 70)
    print(f"#  BATCH DONE in {mins:.1f} min")
    print(f"#  Succeeded : {len(ok)} parts -> {ok}")
    if failed:
        print(f"#  Failed    : {len(failed)} parts -> {failed}")
        print(f"#  Re-run failed ones:  python run_batch.py {min(failed)} {max(failed)}")
    print("#" * 70)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\n[!] Batch interrupted - re-run to resume.")

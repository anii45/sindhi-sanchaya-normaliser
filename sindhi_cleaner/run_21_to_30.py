"""
RUN PARTS 21 -> 30  — unattended batch, fixed range.

Usage:
    python run_21_to_30.py

Each part keeps its own checkpoint + output files (handled by
run_part.run_part). If a part crashes, this logs it and continues to the
next part. Safe to interrupt with Ctrl-C and re-run — finished parts are
skipped, and a half-done part resumes from its checkpoint.
"""

import asyncio
import time
import traceback

import run_part as rp


START = 21
END   = 30


def main():
    parts = list(range(START, END + 1))
    print("#" * 70)
    print(f"#  RUN PARTS {START} -> {END}   ({len(parts)} parts)")
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
            print("    Re-run this script to resume - finished parts are skipped")
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
    print(f"#  DONE in {mins:.1f} min")
    print(f"#  Succeeded : {len(ok)} parts -> {ok}")
    if failed:
        print(f"#  Failed    : {len(failed)} parts -> {failed}")
    print("#" * 70)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\n[!] Interrupted - re-run to resume.")

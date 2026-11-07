"""
RUN ONE PART — concurrent cleaning of a single ~1000-row part of the
SINDHI SANCHAYA database (parts 1-39, books with KNOWN authors only).

Usage:
    python run_part.py 1        # processes SINDHI_SANCHAYA_Part_01.xlsx
    python run_part.py 17       # processes SINDHI_SANCHAYA_Part_17.xlsx
    python run_part.py 39

Each part is fully isolated — outputs land in  "Sindhi Sanchaya 40 Parts/Cleaned/":
    Part_NN_Cleaned.xlsx
    Part_NN_ChangeReport.csv
    Part_NN_ScriptViolations.csv
    Part_NN_checkpoint.pkl     <- re-run the SAME number to resume after a crash

Reuses pipeline_core.py — same prompt, same script validator, same
concurrency (15), same retries. Only the input/output paths change per part.
"""

import asyncio
import os
import sys
import time

import config
import pipeline_core as pc


# The folder holding the 39 split workbooks (sits next to the sindhi_cleaner folder).
PARTS_DIR  = os.path.join(config._PARENT, "Sindhi Sanchaya 40 Parts")
# All per-part outputs land in this subfolder.
OUTPUT_DIR = os.path.join(PARTS_DIR, "Cleaned")

# Checkpoint more often than the full run — a 1000-row part is small,
# so losing at most this many pairs on a crash is cheap.
CHECKPOINT_EVERY = 200

# Circuit breaker: if this many calls fail back-to-back, the daily quota is
# almost certainly exhausted; bail out instead of grinding 60s retries for hours.
CONSECUTIVE_FAIL_LIMIT = 8


def part_paths(n: int) -> dict:
    """Build every input/output path for part number n."""
    tag = f"Part_{n:02d}"
    return {
        "tag":        tag,
        "input":      os.path.join(PARTS_DIR,  f"SINDHI_SANCHAYA_{tag}.xlsx"),
        "output":     os.path.join(OUTPUT_DIR, f"{tag}_Cleaned.xlsx"),
        "report":     os.path.join(OUTPUT_DIR, f"{tag}_ChangeReport.csv"),
        "violations": os.path.join(OUTPUT_DIR, f"{tag}_ScriptViolations.csv"),
        "checkpoint": os.path.join(OUTPUT_DIR, f"{tag}_checkpoint.pkl"),
    }


def truncate(s: str, n: int = 42) -> str:
    s = (s or "").replace("\n", " ").strip()
    return s if len(s) <= n else s[:n - 1] + "..."


async def process_pair(key, pair, client_cfg, meter, semaphore):
    """Call Gemini for one pair, under the semaphore."""
    async with semaphore:
        prompt = pc.build_prompt(pair["fields"])
        try:
            corrections = await pc.call_gemini_async(client_cfg, prompt, meter)
            if not isinstance(corrections, list) or not corrections:
                return key, {"_error": "bad response shape"}
            return key, corrections[0]
        except Exception as e:
            return key, {"_error": f"{type(e).__name__}: {str(e)[:100]}"}


async def run_part(n: int) -> None:
    p = part_paths(n)

    if not os.path.exists(p["input"]):
        raise SystemExit(f"[ERROR] Input not found:\n      {p['input']}")
    os.makedirs(OUTPUT_DIR, exist_ok=True)

    print("=" * 70)
    print(f" SINDHI SANCHAYA - cleaning {p['tag']}")
    print("=" * 70)
    print(f" Input        : {p['input']}")
    print(f" Output       : {p['output']}")
    print(f" Model        : {config.MODEL_NAME}")
    print(f" Concurrency  : {config.CONCURRENCY_LIMIT} parallel calls")
    print()

    rows = pc.load_excel(p["input"])
    print(f"[i] Loaded {len(rows)-1:,} rows.")

    pairs = pc.build_unique_pairs(rows)
    keys = list(pairs.keys())
    total = len(keys)
    print(f"[i] Unique pairs to process: {total:,}")
    print()

    # Resume from THIS part's own checkpoint, if one exists.
    log = pc.ChangeLog()
    processed = set()
    ckpt = pc.load_checkpoint(p["checkpoint"])
    if ckpt:
        rows = ckpt["rows"]
        log.changes = ckpt["changes"]
        processed = set(tuple(k) for k in ckpt["processed_keys"])
        print(f"[i] Resumed from checkpoint: {len(processed):,} pairs already done.")
        print()

    client_cfg = pc.setup_gemini()
    meter = pc.TokenMeter()

    todo = [k for k in keys if k not in processed]
    n_todo = len(todo)
    print(f"[i] Pairs remaining: {n_todo:,}")
    print()

    if n_todo == 0:
        print("[i] Nothing to do - this part is already complete.")
        pc.save_excel(rows, p["output"])
        log.save_csv(p["report"])
        log.save_violations_csv(p["violations"])
        print(f"[i] Re-saved outputs to {OUTPUT_DIR}")
        return

    semaphore = asyncio.Semaphore(config.CONCURRENCY_LIMIT)
    tasks = [
        asyncio.create_task(process_pair(k, pairs[k], client_cfg, meter, semaphore))
        for k in todo
    ]

    t_start = time.time()
    successes = 0
    failures = 0
    done = 0
    consecutive_failures = 0
    aborted = False

    # Wrap the loop so the checkpoint is ALWAYS written on exit -
    # normal completion, Ctrl-C, crash, anything. No work is ever lost.
    try:
     for coro in asyncio.as_completed(tasks):
         key, result = await coro
         done += 1
         pair = pairs[key]
         first_row_id = rows[pair["row_indices"][0]][pc._idx(pc.COL_ID)]
         f = pair["fields"]
         label = truncate(f[0] or f[1] or f[2] or "<empty>")

         if "_error" in result:
             failures += 1
             consecutive_failures += 1
             print(f"  [{done:>4}/{n_todo}] x id={first_row_id:<6} FAIL ({result['_error'][:60]})",
                   flush=True)
         else:
             before = len(log.changes)
             pc.apply_corrections(rows, pair, result, log)
             delta = len(log.changes) - before
             successes += 1
             consecutive_failures = 0
             processed.add(key)

             elapsed = time.time() - t_start
             rate = done / elapsed if elapsed > 0 else 0
             eta_min = ((n_todo - done) / rate / 60) if rate > 0 else 0
             usd = meter.cost_usd()
             print(f"  [{done:>4}/{n_todo}] ok id={first_row_id:<6} +{delta} chg | "
                   f"${usd:.3f} | {rate:.1f}/s | ETA {eta_min:>4.1f}m | {label}",
                   flush=True)

         if done % CHECKPOINT_EVERY == 0:
             pc.save_checkpoint(p["checkpoint"], list(processed), rows, log.changes)
             print(f"  [ckpt] saved at {done:,} completed pairs", flush=True)

         if consecutive_failures >= CONSECUTIVE_FAIL_LIMIT:
             print()
             print("!" * 70)
             print(f"!!  {consecutive_failures} consecutive 429 failures - daily quota almost")
             print("!!  certainly exhausted. Bailing out cleanly.")
             print("!!  Re-run after the quota resets (midnight Pacific Time).")
             print("!!  Progress is saved - re-running will skip what is done.")
             print("!" * 70)
             for t in tasks:
                 if not t.done():
                     t.cancel()
             aborted = True
             break
    finally:
        # Cancel any still-pending tasks so they don't leak warnings,
        # then save the checkpoint with whatever was completed.
        for t in tasks:
            if not t.done():
                t.cancel()
        pc.save_checkpoint(p["checkpoint"], list(processed), rows, log.changes)
        print(f"  [ckpt] final save on exit: {len(processed):,} pairs done", flush=True)

    # Final save (only reaches here on normal completion / circuit break)
    pc.save_excel(rows, p["output"])
    log.save_csv(p["report"])
    log.save_violations_csv(p["violations"])
    log.summary()
    meter.report()

    total_min = (time.time() - t_start) / 60
    print()
    print("=" * 70)
    print(f" {p['tag']} done in {total_min:.1f} min.  "
          f"Successes: {successes:,}  Failures: {failures:,}")
    print(f" Cleaned    : {p['output']}")
    print(f" Report     : {p['report']}")
    if log.violations:
        print(f" Violations : {p['violations']}  ({len(log.violations)} rejected)")
    print("=" * 70)


def parse_part_arg() -> int:
    if len(sys.argv) != 2:
        raise SystemExit("Usage: python run_part.py <part-number 1-39>")
    raw = sys.argv[1].strip().lstrip("0") or "0"
    try:
        n = int(raw)
    except ValueError:
        raise SystemExit(f"[ERROR] '{sys.argv[1]}' is not a valid part number.")
    if not (1 <= n <= 39):
        raise SystemExit(f"[ERROR] Part number must be between 1 and 39 (got {n}).")
    return n


if __name__ == "__main__":
    try:
        part = parse_part_arg()
        asyncio.run(run_part(part))
    except KeyboardInterrupt:
        print("\n[!] Interrupted - re-run the same part number to resume.")

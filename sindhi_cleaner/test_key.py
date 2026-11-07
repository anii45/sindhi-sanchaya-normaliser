"""
Quick verification that the alternate Gemini API key works.

Makes ONE real API call using a small synthetic record. Confirms the key,
billing, model name, and prompt are all wired up correctly BEFORE you commit
to a multi-hour batch run.

Usage:
    python test_key.py
"""

import sys
import os

import config

# Override the key with the one you want to test (the run_31_to_39 key).
config.GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY_2") or config.GEMINI_API_KEY
import pipeline_core as pc


def main():
    print("=" * 64)
    print(" Gemini key smoke test")
    print("=" * 64)
    print(f"  Key (last 6 chars) : ......{config.GEMINI_API_KEY[-6:]}")
    print(f"  Model              : {config.MODEL_NAME}")
    print()

    # A small synthetic record - one real API call.
    fields = (
        "Test Book Name",
        "टेस्ट पुस्तक",
        "ٽيسٽ ڪتاب",
        "Test Author",
        "टेस्ट लेखक",
        "ٽيسٽ ليکڪ",
    )
    prompt = pc.build_prompt(fields)

    try:
        client_cfg = pc.setup_gemini()
    except SystemExit as e:
        print(f"[X] setup failed: {e}")
        sys.exit(1)

    meter = pc.TokenMeter()
    try:
        result = pc.call_gemini(client_cfg, prompt, meter)
    except Exception as e:
        print(f"[X] API CALL FAILED:  {type(e).__name__}: {e}")
        print()
        print("Common causes:")
        print("  - 429 quota / rate limit (check Cloud Console > Quotas)")
        print("  - Invalid API key (check Console > Credentials)")
        print("  - Billing not enabled on the project")
        print("  - Network / firewall blocks generativelanguage.googleapis.com")
        sys.exit(1)

    print("[OK] API call succeeded.")
    print(f"     Input tokens  : {meter.input_tokens}")
    print(f"     Output tokens : {meter.output_tokens}")
    print(f"     Cost          : ${meter.cost_usd():.6f}")
    print()
    print("Sample response:")
    print(result[0] if isinstance(result, list) and result else result)
    print()
    print("=" * 64)
    print(" Key is working - safe to run run_31_to_39.py.")
    print("=" * 64)


if __name__ == "__main__":
    main()

"""
Configuration for the Sindhi Sanchaya cleaner pipeline.
"""
import os as _os

# 1. Gemini API key — REQUIRED
# Never write the key here. Set it once on your computer instead:
#   Windows:  setx GEMINI_API_KEY "your-key"   (then open a new terminal)
#   macOS/Linux:  export GEMINI_API_KEY="your-key"
GEMINI_API_KEY = _os.environ.get("GEMINI_API_KEY", "")
# 2. Model + pricing
MODEL_NAME = "gemini-2.5-flash"
PRICE_INPUT_PER_M = 0.30
PRICE_OUTPUT_PER_M = 2.50

# 3. Files (relative to this file's location)
_HERE = _os.path.dirname(_os.path.abspath(__file__))
_PARENT = _os.path.dirname(_HERE)
INPUT_FILE  = _os.path.join(_PARENT, "SINDHI SANCHAYA Raw Database - without unknown.xlsx")
OUTPUT_FILE = _os.path.join(_HERE, "SINDHI_SANCHAYA_WithoutUnknown_Cleaned.xlsx")
REPORT_FILE = _os.path.join(_HERE, "SINDHI_SANCHAYA_WithoutUnknown_ChangeReport.csv")

# 4. Speed / quality
DISABLE_THINKING  = True
MAX_RETRIES       = 6        # exponential backoff retries on transient errors
CHECKPOINT_EVERY  = 500      # save progress every N completed pairs
CONCURRENCY_LIMIT = 6        # parallel API calls. Tier 1 cap is ~1M tokens/min;
                             # 15 overshot it and caused 429s. 6 stays well under.

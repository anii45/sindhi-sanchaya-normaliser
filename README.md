# Sindhi Sanchaya normaliser

The Python pipeline used to normalise the Sindhi Sanchaya union catalogue (https://sindhisanchaya.online), a catalogue of Sindhi books in three scripts: Roman, Devanagari and Perso-Arabic. It sends each distinct record to Gemini 2.5 Flash with a short, phonologically informed instruction, checks every answer in code, and logs every change.

The method is described in the chapter "Making Do at the Scale of the Catalogue: Large Language Models, and the Limits of Minimal Computing in a Tri-Script Sindhi Archive" (forthcoming).

## The run in numbers

- 41,459 catalogue rows, in 40 parts: 38,645 rows with a known author (Parts 1 to 39) and 2,814 with no known author (Part 40).
- 38,814 distinct records sent to the model after deduplication (36,197 six-field signatures and 2,617 titles).
- 160,469 cell corrections logged; 1,258 model outputs refused by the script validator.
- Roughly US$48 at the Gemini 2.5 Flash prices of the time (US$0.30 and US$2.50 per million input and output tokens). Prices change: run the cost probe before any new job.

## What is here

```
prompts/
  main_prompt.txt            the instruction for records with a known author
  unknown_author_prompt.txt  the title-only instruction for records with no known author
sindhi_cleaner/
  config.py                  model, prices, file paths, concurrency, retries; reads the API key from the environment
  pipeline_core.py           shared code: Excel in/out, deduplication, the instruction, Gemini calls with retries,
                             the script validator, the change log, the cost meter, checkpoints
  run_estimate.py            cost probe: a seeded sample of 100 records, then the full cost and time worked out
  run_part.py                cleans one part:  python run_part.py 17
  run_batch.py               runs a range of parts unattended
  run_11_to_39.py, run_21_to_30.py, run_31_to_39.py   the fixed ranges used in the actual run
  unknown_author.py          the path for records with no known author (title-only; author markers written in code)
  test_unknown_author.py     100-record test of that path
  merge_parts.py             joins the 40 cleaned parts into one workbook, in order
  test_key.py                one real call to check a key, billing and the model before a long run
```

## Folder layout the scripts expect

The scripts look for data in the folder above `sindhi_cleaner` (the repository folder). Data is not part of this repository.

```
sindhi-sanchaya-normaliser/
  SINDHI SANCHAYA Raw Database - without unknown.xlsx
  Unknown Authors Only.xlsx
  Sindhi Sanchaya 40 Parts/
    SINDHI_SANCHAYA_Part_01.xlsx ... SINDHI_SANCHAYA_Part_39.xlsx
    Cleaned/          (outputs: Part_NN_Cleaned.xlsx, _ChangeReport.csv, _ScriptViolations.csv, checkpoints)
  sindhi_cleaner/
```

Each workbook has one sheet with these columns: id, book_name_english, book_name_devanagari, book_name_perso_arabic, author_name_english, author_name_devanagari, author_name_perso_arabic, collection_location, address, other_details, image, available_online.

## How to run

```
pip install -r requirements.txt
setx GEMINI_API_KEY "your-key"        (Windows; open a new terminal afterwards)
cd sindhi_cleaner
python test_key.py
python run_estimate.py                read the cost estimate before going further
python run_part.py 1
python unknown_author.py
python merge_parts.py
```

`run_31_to_39.py` and `test_key.py` use a second key if `GEMINI_API_KEY_2` is set (the actual run used one for Parts 31 to 39, so each key's daily limit applied separately); otherwise they use `GEMINI_API_KEY`.

## Licence

Code: MIT (see LICENSE). The catalogue data is licensed separately; see the Zenodo record linked from the chapter.

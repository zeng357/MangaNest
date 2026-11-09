BallonsTranslator custom translator modules (v1.4.0)
==================================================

Copy these files into <BallonsTranslator>/custom_modules/ (file names must start with "trans_"):

  trans_hybrid.py     "Hybrid" translator - online MyMemory first, offline NLLB fallback.
                      Also: all-caps normalisation, repeat collapse, glossary fixes,
                      watermark / pure-symbol blocks -> empty translation.
  trans_nllb.py       "NLLB" translator - fully offline nllb-200-distilled-600M (en -> zh).
                      Model dir resolution: NLLB_MODEL_DIR env -> C:\bt_models\... -> <BT>/data/models/...
  trans_opus.py       "OpusMT" translator - kept for reference, quality too low for manga.
  trans_mymemory.py   "MyMemory" translator - online only, free quota runs out fast (HTTP 403).

Then set translator = "Hybrid" in <BallonsTranslator>/config/config.json.

Details: Docs/Translation.md

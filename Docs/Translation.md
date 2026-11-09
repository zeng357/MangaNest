# Manga Translation (English → Chinese)

MangaNest's **🈶 Translate** page turns an English manga chapter into a Chinese one. The heavy lifting is done by the open-source engine **BallonsTranslator**; this repository ships only the integration: `Source/bt_slice_translate.py` (slice / run / clean / stitch) and the translator modules in `Extensions/BallonsTranslator-custom-modules/`.

> The engine, its embedded Python and the ~4 GB of models are **not** in this repository — download BallonsTranslator (minimum package) separately and drop it into `tools/Ballonstranslator_win_minium/`, next to `gui.py`.

```
tools/
  Ballonstranslator_win_minium/      <- open-source engine + models (not in repo)
    ballontrans_pylibs_win/python.exe
    ballontranslator/
    config/config.json
    custom_modules/                  <- trans_hybrid.py, trans_nllb.py go here
    data/models/                     <- comictextdetector, lama_large_512px, mit48px, nllb-200-distilled-600M
```

## 1. Why the page is sliced before detection

BallonsTranslator's text detector resizes the whole image to `detect_size` (2048 in our config) before detecting. A long-strip page of 800×8000+ then has a line height of 3–5 px: thin lines (glowing title strokes, small SFX) are missed. Missed lines are neither erased nor translated — this is the number-one cause of "there is still English on the page".

`bt_slice_translate.py` therefore:

1. cuts the page into ~2000 px slices (`--slice`), snapping cut points to blank bands (`_blank_cut`) so no bubble is cut in half,
2. runs BallonsTranslator on the slices (`--exec_dirs`), so detection is nearly 1:1,
3. re-runs any slice that produced no result image (engine often crashes on exit *after* writing results — exit code `0xC0000409` is normal and not a failure),
4. stitches the slice results back at their exact y offsets.

## 2. Why punctuation used to survive

BallonsTranslator masks only the strokes inside its detection box. In `BUT,` the comma can sit just outside the box, and `mask dilate size` cannot reach it (tested; also erodes bubble frames when raised). Our pipeline separates the two layers instead:

- the **base layer** is BallonsTranslator's `inpainted/` output (original art with text erased);
- on that base, `_neutralize_residual_text()` runs a local ink test around every text block and erases leftovers with `cv2.inpaint` (no hard rectangles painted over the art);
- the **Chinese layer** comes from `result/`; `compose_slice()` composites only the pixels that actually differ from the base (`diff`) **minus** the pixels we just cleaned (`keep = diff & ~clean_mask`).

That exclusion mask is essential: without it the compositor copies our freshly cleaned pixels straight back from `result/`, which is why the watermark/"stray letter" cleanup appeared to do nothing in early versions.

For site banners (`LUACOMIC…`, `DSC.GG/…`, "only official domain is …"), the block is recognised as a watermark and given an empty translation, so the original line is erased and nothing is written back. On colour pages an extra channel keeps only *small* bright connected components (area 25–4000, height ≤110) full-width, so faint grey watermark strokes go while the white dress and the artwork logo stay intact.

## 3. Translation channel

| Channel | Module | Status |
|---|---|---|
| `Hybrid` (default) | `trans_hybrid.py` | online first (MyMemory), offline NLLB fallback; per-block watermark/symbol filtering, glossary fixes, repeat collapse |
| `NLLB` | `trans_nllb.py` | fully offline `nllb-200-distilled-600M` (English→Chinese), 1.5 GB VRAM in fp16 |
| `OpusMT` | `trans_opus.py` | kept for reference; quality too poor for manga dialogue |

The hybrid module also normalises **all-caps** source text before translating (the offline model mistranslates shouting as proper nouns), collapses clauses repeated 3+ times, and applies a small glossary (`Ria/RIA → 莉娅`, `Discord`, orphanage…).

## 4. Installing / repairing the engine

1. Download the **BallonsTranslator minimum package** (Windows) and extract to `tools/Ballonstranslator_win_minium/`.
2. Copy `Extensions/BallonsTranslator-custom-modules/trans_*.py` into `tools/Ballonstranslator_win_minium/custom_modules/`.
3. Configure `config/config.json` (engine side):

```json
"textdetector": "ctd",
"ocr": "mit48px",
"inpainter": "lama_large_512px",
"translator": "Hybrid",
"source": "English",
"target": "简体中文"
```

and in `textdetector_params.ctd`: `detect_size 2048`, `font size multiplier 0.7`, `mask dilate size 4`, `low vram mode true`, `font size max 40`.

4. GPU: install `torch==2.5.1+cu121` and `torchvision==0.20.1+cu121` into the embedded interpreter, then check `torch.cuda.is_available()`:

```bat
tools\Ballonstranslator_win_minium\ballontrans_pylibs_win\python.exe -c "import torch;print(torch.cuda.is_available())"
```

5. `transformers` must stay at **4.46.3** (5.x breaks the offline loader); `sentencepiece` + `sacremoses` are required for NLLB.
6. Models: put `comictextdetector.pt`, `lama_large_512px.ckpt`, `mit48pxctc_ocr.ckpt`, `nllb-200-distilled-600M/` under `data/models/`. Downloads from `huggingface.co` are often unreachable in CN — the script sets `HF_ENDPOINT=https://hf-mirror.com` for the engine process.

### Known gotchas

- **Chinese paths + sentencepiece**: `Illegal byte sequence` when the model sits under a path containing Chinese characters. Workaround: create an ASCII junction (`mklink /J C:\bt_models\nllb-200-distilled-600M <...>`) and point `NLLB_MODEL_DIR` at it — the resolver checks env var → ASCII default → engine `data/models`.
- **`detect_size` 3072** on a 4 GB card: hangs (OOM), not "slower". Keep 2048.
- **`mask dilate size` 8**: does not remove outside-box punctuation and erodes bubble frames. Keep 4.
- **`ctbd` detector**: OCR returns empty for every block on this pipeline. Use `ctd`.
- Engine exit code `3221226505` / `-1073740791` / `0xC0000409` after a run: results are already on disk; treat as success (the script retries only truly missing slices).

## 5. CLI reference (`Source/bt_slice_translate.py`)

```
--input      required   chapter folder (all images) or a single image
--output     optional   output dir; default "<source> 汉化版" / "<image dir>/汉化版"
--slice      default 2000   slice height in px
--overlap    default 60     extra overlap between slices (cut already snaps to blank bands)
--text-only  flag       export only 中英对照.txt, skip page compositing
--keep-temp  flag       keep <output>/_bt_work (slices, results, masks, engine logs)
```

Exit code 5 = failure (message printed); engine exit codes are not propagated.

## 6. Output

```
<output>/
  1.png … N.png      Chinese pages, same base file names as the source
  中英对照.txt        every block: source text → translation (blocks with empty translation were watermarks/symbols)
```

## 7. Limits

- Offline-model quality: long sentences read fine; exclamations, name puns and "read on X" ad lines can be literal.
- Inpainted flat-colour bubbles occasionally show a faint rectangle of a slightly different shade (cosmetic, rare).
- Very stylised SFX lettering that OCR returns empty is restored from the original art, so it stays English by design (better than an empty hole).
- Artist-logo graphics that are *drawn*, not typed, are not text blocks and are left as-is.
- Speed: ~1–2 min per 800×9000 page on a GTX 1050 4 GB; CPU-only ≈ 10× slower.
- Use only on material you are allowed to translate; see `DISCLAIMER.txt`.

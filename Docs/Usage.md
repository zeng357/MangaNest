# MangaNest · Usage Guide

MangaNest · Multi-Site Manga Downloader — full user guide (chapter-list edition, v1.1).

## Contents

1. Quick start
2. Home page (download workflow)
3. Format Converter page (merge manga into a book)
4. Settings page
5. Adding any new website (Auto Site Analyzer)
6. Self-check, logs & network repair
7. Add your own crawler (two ways)
8. Translate page (English manga → Chinese)
9. FAQ
10. Disclaimer

---

## 1. Quick start

- **Source version**: install Python 3.10 + dependencies (see README section 4), double-click `start.bat`.
- **Standalone EXE**: download from GitHub Releases, extract (EXE + `sites_data` + `custom-sites` + `config.json` in the same folder), double-click the EXE. No Python needed.

Both versions open the same GUI with three pages in the left sidebar: **🏠 Home · 🔄 Format Converter · ⚙ Settings**.

> Keep the console window open while downloading — progress is shown there. Close it or the main window to quit.

---

## 2. Home page (download workflow)

### 2.1 Step-by-step

1. **Site selector**: choose a manga website.
2. **Comic name**: type the manga title.
3. **Search**: click Search; candidates appear below. Click one to auto-fill and open its detail page.
4. **Need login** (optional): check it, then `Open Login` (a browser opens) → sign in (QR/account) → `Login Done` (Cookie saved). Required for chapters that need login.
5. **Load chapter list**: reads all chapters (number + name) and builds a checkable list.
6. **Select chapters**: check the ones to download (all / partial / range).
7. **Start download**: downloads all images of the selected chapters into `ComicName/ChapterName/N.jpg`.
8. **Retry tools**: "Retry failed images" and "Retry failed chapters" for failures.

### 2.2 Folder structure

```
ComicName/
├── 0/cover.jpg                 # cover (0-prefixed folder = cover directory)
├── 128 Final-Chap/
│   ├── 1.jpg
│   └── ...
└── 127 Side-Story/
    ├── 1.jpg
    └── ...
```

### 2.3 Notes

- **VIP/paid chapters**: guests only see preview pages. After login, free chapters download fully; VIP paid chapters need a matching membership. The program never bypasses paywalls.
- Some sites lazy-load chapter lists/images — the program auto-scrolls and paginates.

---

## 3. Format Converter page (merge manga into a book)

Turn downloaded manga images (JPG/PNG/WebP/GIF/BMP) into e-books: **PDF · CBZ · EPUB · MOBI**.

### 3.1 Add source folders

- **Add folder**: add a manga folder (a comic root, a chapter folder, anything containing images).
- **Scan sub-folders (batch)**: pick a comic root; every sub-folder that contains images is added at once.

### 3.2 Convert

| Control | What it does |
|---|---|
| **⚡ One-click whole-book merge** | Merges **all folders in the list (1 or many) into ONE file**, auto cover. Just click it. |
| **Start convert** | Converts per the current settings (whole book / per chapter, cover on/off). |
| Target format | PDF (universal) / CBZ (comic readers) / EPUB (e-book) / MOBI (requires Calibre installed). |
| Output folder | Where result files are saved. |
| **Per-chapter files** | On = one file per chapter (cover only on the first); off = one merged file. |
| **Add cover to first book** | Default on: uses the original manga cover automatically. |
| **Choose cover image** | Pick any image manually as the cover (overrides auto detection). |
| Convert log | Live progress for every file. |

### 3.3 Cover rules (auto)

1. Detects a **0-prefixed folder** (e.g. `0/cover.jpg`) as the original cover — prefers `cover.*`, else the first image in that folder.
2. No 0-folder → no extra cover; the first image naturally becomes the cover page.
3. Only the **first book** gets a cover.

### 3.4 Examples

- **One folder** (a comic root): add it, click ⚡ → one PDF with cover + all chapters.
- **Many folders** (chapter folders / several comics): add them all, click ⚡ → ONE merged file (name = first folder + " etc.").
- After conversion the source list clears automatically, ready for the next batch.

---

## 4. Settings page

- **Image naming**: padding (001, 002…), naming pattern.
- **Chapter folder naming**: display style of chapter folders.
- **Window / download / browser options**: fonts, timeouts, headless mode, etc.

---

## 5. Adding any new website (Auto Site Analyzer)

Open **Auto Site Analyzer** from the Home page, follow 4 steps:

1. **Open & analyze**: paste the new site homepage; the program analyzes the search box and search entry.
2. **Trial search**: type a keyword; candidates are listed (filter/fix rules if needed).
3. **Analyze chapters**: opens the comic detail page; scrolls/paginates to find all chapters (with names).
4. **Analyze images → save adapter**: opens a chapter page, analyzes image structure, generates rules and **saves `custom-sites/newsite_adapter.json`** — reusable forever.

Refresh the site list afterwards; the new site behaves like built-in ones.

See `Docs/How-to-Add-a-New-Site.md` for the adapter JSON fields.

---

## 6. Self-check, logs & network repair

### 6.1 Self-check (v1.1)

Every launch runs a self-check: Python version, required modules, network socket, core files, writable directories.

- Result log: **`startup_diag.log`** (next to the program).
- Unexpected errors: **`crash.log`**.
- If a blocking error is found, a dialog points to the log file.

### 6.2 Network repair (WinError 10038)

If the program cannot create sockets / reach the internet:

1. Right-click **`fix-network-stack.bat`** → **Run as administrator** (resets Winsock, TCP/IP, DNS).
2. **Restart your PC**.
3. Connect normally afterwards.

> Format conversion is fully local — it works without internet.

---

## 7. Add your own crawler (two ways)

- **No coding** → Auto Site Analyzer wizard / "New Adapter" (JSON). See `Docs/How-to-Add-a-New-Site.md`.
- **Python** → "Crawler Source Manager" → "New from template" → edit → save; sources in `sites_data/` auto-load. See `Docs/How-to-Add-a-Custom-Crawler.md`.

---

## 8. Translate page (English manga → Chinese)

**What it does**: takes a downloaded chapter folder (English artwork) and produces the same pages with Chinese text, plus a block-by-block source/target list.

**Requirements**

1. Open-source engine **BallonsTranslator** installed at `tools/Ballonstranslator_win_minium/` (engine + models, ~4 GB, not shipped in this repo).
2. Our translator modules copied to `<BallonsTranslator>/custom_modules/`: `trans_hybrid.py`, `trans_nllb.py` (`Extensions/BallonsTranslator-custom-modules/`).
3. `<BallonsTranslator>/config/config.json`: `textdetector=ctd`, `ocr=mit48px`, `inpainter=lama_large_512px`, `translator=Hybrid`, `source=English`, `target=简体中文`.
4. Offline model `nllb-200-distilled-600M` (~2.4 GB) under `<BallonsTranslator>/data/models/`, or point `NLLB_MODEL_DIR` at it.

Full install/repair notes: `Docs/Translation.md`.

**Steps**

| Step | What happens |
|---|---|
| 1. Pick source folder | A chapter folder containing `1.jpg … N.jpg` (or single image) |
| 2. Output folder (optional) | Empty = auto `"<chapter folder> 汉化版"` next to the source |
| 3. Text only (optional) | Only exports `中英对照.txt`, skips image compositing (faster, less disk) |
| 4. Start | Runs in background thread; log window shows page/slice progress; Done dialog when finished |

**Output**

```
<output>/
  1.png … N.png         Chinese pages (same base names as the source images)
  中英对照.txt           per block: source text / translated text
```

**CLI equivalent**

```bat
python bt_slice_translate.py --input "<chapter>" --output "<out>"
python bt_slice_translate.py --input "<chapter>" --text-only
python bt_slice_translate.py --input "<chapter>" --slice 2000 --overlap 60 --keep-temp
```

`--keep-temp` keeps `_bt_work/` (slices, per-slice BallonsTranslator output, JSON, logs) — use it when a page still shows English and you want to see which slice failed.

**How it avoids three classic failures**

| Failure | Cause | Handling |
|---|---|---|
| English still visible | Whole page downscaled before detection → thin lines missed; punctuation outside the text box never masked | Slice to ~2000px before detection; residual ink detected on the inpainted base and erased by pixel mask |
| Empty bubbles (bubble frame erased) | Inpainter wipes area, OCR returned nothing | OCR-empty blocks are restored from the original artwork; Chinese is composited only where text was replaced |
| Translation missing on some blocks | A slice produced no result (engine crashed on exit) | Every missing slice is detected and re-run automatically (up to 3 rounds); leftovers listed in the log |

**Timing reference** (GTX 1050 4 GB): ~1–2 min per 800×9000 page; a 21-page chapter ≈ 25–35 min. CPU only: roughly 10× slower.

---

## 9. FAQ

| Problem | Cause & fix |
|---|---|
| Black window flashes and closes | BAT line-ending/encoding issue; use the original BAT, or run `python gui.py` |
| No module named 'xxx' | Missing dependency; install per README section 4 |
| Chapter list numbers only | Old bug; fixed — new version shows "Chap N + name" |
| Chapter downloads only 1–2 images | Paid/login chapter; guests see preview only; check "Need login" and retry |
| Search shows unrelated results | Some sites don't filter search; adapters apply keyword filter + fallback |
| Missing chapters / no next page | Lazy-loaded sites; new version auto-scrolls + paginates |
| Images include UI pictures | Restricted to real images inside the chapter container |
| Converted PDF won't open | Old object-number bug fixed in v1.1 — re-convert |
| Conversion still produces separate files | Use "⚡ One-click whole-book merge" |
| "Self-check found problems" | Open `startup_diag.log`; network → `fix-network-stack.bat` |

---

## 10. Disclaimer

Full disclaimer: `Docs/DISCLAIMER.txt` (English & Chinese). In short:

- For personal learning/research only; no commercial use.
- Downloaded content belongs to the original authors/platforms; delete within 24 hours; support the official releases.
- Obey each site's terms of service; use at your own risk.
- No paywall bypass / membership cracking / piracy distribution.
- Provided "as is" without warranty.

By using this software you agree to the terms above.

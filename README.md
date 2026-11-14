# MangaNest · Multi-Site Manga Downloader

📖 Full usage guide: `Docs/Usage.md` (main UI walkthrough + download flow + adding sites + FAQ)

A manga downloader that runs on Windows and supports multiple manga websites: built-in crawlers + custom site adapters (JSON rules) + an Auto Site Analyzer wizard — paste any new website, the program automatically analyzes its search/chapter/image structure and generates a reusable adapter.

**v1.1 new features**: 🔄 **Format Converter page** (turn downloaded manga images into PDF / CBZ / EPUB / MOBI e-books — one-click whole-book merge, multi-folder merge, automatic original cover), **startup self-check** (writes diagnostic logs), **one-click network-stack repair** script.

**v1.1.1 new features**: 🔐 **Secure Login Vault** — login sessions are now stored in an **AES-256-GCM encrypted container**:
- **Device-bound**: opens automatically on THIS PC (hardware fingerprint) — no password needed
- **Password unlock** for other computers (PBKDF2 200k iterations)
- **Auto-destroy**: 4 wrong passwords → all saved login data is permanently destroyed
- **Password rule**: at least 6 characters, must contain **letters + digits + symbols** (e.g. `Abc@123`)
- **Auto-login**: after logging in once, the program automatically uses the saved container — no more QR scans
- New **🔐 Password Manager** page (fold menu in the sidebar): view container status / set & change password / test password / clear container

**v1.1.2 new features**: ❓ **Security Question verification** - set a **custom security question** when creating the container password (e.g. "What is my hobby?" / "Name of my favorite person", free text, no length limit):
- Changing the password **requires answering the security question correctly** (retry allowed, no auto-destroy)
- Answer stored as a salted hash (only THIS PC can verify; other computers cannot read it)
- Forgot your password on this PC? Answer the security question -> reset a new password

**v1.1.3 new features**: :mag: **Vault Access Verification** & :shield: **Program Integrity Detection**:
- **Vault access verification**: opening the :lock: Password Manager now requires the container password **even on this PC** - anyone trying to view or modify saved passwords must verify first. Browser auto-login/download stays seamless on this PC (device fingerprint); only the password vault itself demands verification
- **Program integrity detection (anti-tamper)**: on first run the program fingerprints its own core files (AES-GCM encrypted, device-bound). If the program files are modified by anyone, startup warns you and the Password Manager **forces you to re-enter the container password** before use; a successful verification resets the integrity baseline (intended for your own program updates)

**v1.2.0 new features**: :framed_picture: **Universal Image Downloader page** (inspired by KoManga-Utils, rebuilt & enhanced) — a new sidebar page between Format Converter and Password Manager:
- **URL template download**: batch-download images from a URL pattern with `${key}` placeholder, e.g. `http://IP:8080/img/${key}.jpg` (start/end pages + thread count)
- **Web page image download**: paste any web URL → auto-extract every `<img>` (incl. lazy `data-src`) and download them all — the fallback for sites without an adapter
- **HTML source image download**: paste raw HTML → extract image URLs (src / data-src / data-original / data-lazy-src) and download with progress bar
- Enhanced vs. the original tool: random User-Agent, automatic retry (x2), per-format saving (jpg/png/webp/gif), live progress, threaded (UI never blocks), full usage guide built into the page

**v1.2.1 fix**: :wrench: **Self-check & stability fixes**:
- **Startup script check fixed**: previously the self-check looked for `start.bat`/`start.vbs` and always reported them missing (the project actually uses `一键启动.bat`) — it now accepts any of `start.bat` / `start.vbs` / `一键启动.bat` / `一键启动.vbs`, so no more false "missing" warnings
- **Summary verdict fixed**: the self-check summary only reports real problems now; optional-module warnings (e.g. `bs4`/`img2pdf`) no longer make the result show "异常"
- **Optional dependencies installed**: `beautifulsoup4` + `img2pdf` added to the requirements list (fully optional, warnings removed on fresh installs)

**v1.3 new features**: :books: **Local Manga Library & One-Click Updates**:
- New **📚 Local Library** sidebar page: pick any folder as your library root, scan it, and browse every downloaded manga with its chapters
- **Check for Updates**: compares your local chapters against the site and lists exactly the missing chapters — **smart episode-number ("Total X") tracking** understands chapter folders named like `总676·双双被擒？` (no numeric prefix) as well as `808 总696·...` folders, so a library that is already up to date reports "up to date" instead of a wall of false misses
- **Latest-episode comparison** in the status bar (local vs. site), plus a hint when your local folder has far fewer chapters than the site (chapters may be scattered in other folders)
- **Download new chapters**: one click downloads every missing chapter straight into your library
- **Check bad images**: verifies every downloaded image (magic bytes + minimum size), lists and deletes bad/unrelated images with automatic re-download (downloader-level too: images are checked right after each download)

**v1.3.1 security upgrade**: :lock: **Argon2id key derivation** (OWASP-recommended, memory-hard, GPU brute-force resistant):
- Vault password derivation upgraded from PBKDF2 to **Argon2id** (64 MiB / 3 iterations / 4 lanes); **AES-256-GCM** authenticated encryption retained for vault data
- Old v1 vaults stay fully compatible and are **auto-upgraded to Argon2id** when you change the password
- Fixed: destroyed vaults no longer count as password-protected for the main-program lock
- Program-integrity lock now only activates **after a password has been set** (no popup on a fresh install); once set, modified program files require the password to unlock
- Self-check Python-version comparison bug fixed (3.10 no longer misreported as old)

**v1.4.0 new**: 🌐 **mgread.io site + slice-based manga translation** (details: `Docs/Translation.md`)
- New **🈶 Translate** page: English manga chapters → Chinese, powered by the open-source **BallonsTranslator** engine, orchestrated by `Source/bt_slice_translate.py`
- **Slice-then-detect**: long-strip pages (800×8000+) are cut into ~2000px slices before text detection, because full-page detection downscales the page and misses thin text lines — the main reason English used to survive on the page
- Output = Chinese PNG per page + `中英对照.txt` (per-block source/target), or `--text-only` for text alone
- **Residual-English cleanup**: punctuation outside the detection box is removed by pixel-level ink detection on the inpainted base layer, then the Chinese text layer is composited back with an exclusion mask (`compose_slice` keeps `diff & ~clean_mask`)
- Site banner / watermark lines (`LUACOMIC…`, `DSC.GG/…`, official-domain notices) are **blanked**, not copied back, and OCR-empty blocks are restored from the original art
- Offline translation channel: **NLLB-200-distilled-600M** (English→Chinese) with uppercase normalization, repeat-collapse and glossary fixes; hybrid channel auto-switches between online and offline
- New built-in site **mgread.io** (`sites_data/mgread_crawler.py`): WordPress REST search + detail pre-check, search drops from 30–40s to ~10s per query
- **Chinese → English search bridging** (`Source/search_translate.py`): CJK keywords are translated into up to 6 shortened variants for English-only sites; all-fail raises a hint instead of silently returning nothing
- Download channel: retries use **aiohttp first** (requests kept failing on WAF/TLS-resetting hosts), default concurrency raised 3/4 → 6

**v1.4.1 new**: 🛡️ **Proxy pool (hide your real IP) + site rate limiting + 漫画客 chapter fix**
- **Proxy settings** (⚙ Settings → 代理设置): route **both image downloads and the browser** through proxy exits, so the site never sees your real IP
  - Sources: local proxy list file (`proxies.txt`, one proxy per line) or a **subscription link** (plain text / base64)
  - Image downloads **rotate exits per request** (`proxy_max_tries`, default 3); an exit that fails 3 times in a row is cooled down for 90 s
  - Browser (search / detail / chapter pages) uses one fixed exit (`proxy_browser`, e.g. `http://127.0.0.1:7890`); left empty → first proxy in the list
  - **Fallback to direct connection** when every exit fails (`proxy_allow_direct`, default on) — turn it off if you never want your real IP to touch the site
  - Accepted formats: `http://host:port`, `http://user:pass@host:port`, `host:port`, `host:port:user:pass`, `socks5://host:port` (SOCKS5 is browser-only; images need an HTTP/HTTPS proxy)
- **Per-site rate limiting**: chapter-page concurrency, image threads and per-image delay are now read from each crawler's `CONFIG` (`chapter_concurrency` / `download_threads` / `download_concurrency` / `download_delay`) — no more 429/403 walls, and 403/429 now switch to another exit instead of aborting the image
- **漫画客 (yumanhua) chapter list fixed**: the detail-page filter regex only matched titles starting with a digit, so chapters named `第95话 …` were dropped — the list stopped at 74 chapters. Now title patterns `第N话/章` and `总N` both match, the latest 20 DOM chapters are merged with the 75 from `/morechapter`, and notice rows (`预告`) are filtered only when the title carries no chapter number → **94 chapters** for the same comic
- **Human-like pacing on every site** (always on, `config.py → HUMAN_MODE`): per-image random delay, chapter-to-chapter random pause, concurrency capped at 3 / threads at 2, rotating browser User-Agent, and the browser pauses 0.6–1.6 s after every page navigation — downloads look like a person clicking through chapters instead of a script hammering the site, which is what triggers bans in the first place. Turn `HUMAN_MODE = False` if you want the old full-speed behaviour

**v1.4.2 new**: 🐢 **Dedicated slow profile for 漫画客 (yumanhua)** — the site escalated from per-IP throttling to whole-segment bans, so this site now runs far below the global human caps:
- **One chapter at a time** (`chapter_concurrency: 1`), **one download thread**, **one image request in flight** (`download_threads: 1` / `download_concurrency: 1`)
- **+2.0 s after every image** on top of the random human jitter → effective ~2.3–2.9 s per image, ~2–3 min per 50-page chapter
- **4–9 s pause between chapters** (`chapter_pause_min` / `chapter_pause_max`)
- Downloader now understands **site-level chapter pauses**: any crawler can declare `chapter_pause_min` / `chapter_pause_max` in its `CONFIG`, the values are applied for that download run only and reset afterwards (so slowing one site never slows the others)
- If you still get 403/429 or timeouts, stop the batch, switch to proxy exits (⚙ Settings → 代理设置), and wait before retrying

**v1.4.3 / v1.4.4 new**: 👤 **漫画客 (yumanhua) now behaves like a person reading** — the 1.4.2 pace was still detected, so this site alone gets a full human-browsing model (other sites are untouched):
- **Page turning instead of a jump**: the old code fired `scrollTo(0, scrollHeight)` 60 times in a row — the most obvious script fingerprint on a long-strip comic page. Now the browser scrolls **one screenful at a time** (90–120 % of the viewport height), pauses **0.35–0.8 s** per screen, scrolls **back up a little** every 10–16 screens (like a person re-checking a panel), takes a **2.5–5 s** break every 12–18 screens, and stops **2–4 s** at the bottom — every scroll event is produced by the real browser, not simulated
- **One image at a time, slowly**: 1 chapter at a time, 1 thread, 1 request in flight, **+3.0 s** after each image on top of human jitter → ~3.3–3.9 s per image
- **Reading breaks**: every **8 images** the downloader pauses **6–14 s** (`download_pause_every` / `download_pause_extra_min` / `download_pause_extra_max`)
- **15–35 s between chapters** (`chapter_pause_min` / `chapter_pause_max`)
- Expected cost for this site: **~6–7 min per 50-page chapter** (≈2.5 min page turning + ≈3.5–4 min downloads), so a 94-chapter comic takes several hours — intentional, and the only reliable way to stay under the site's radar
- The reading-break mechanism is generic: any crawler can declare `download_pause_every` in its `CONFIG`; sites that don't declare it are unaffected
- **v1.6.6 — no-sleep switch on the main page**: a **禁止休眠（下载时不自动睡眠）** checkbox now sits in the main-page button row (between 下载失败图片 and 退出). Ticked, the program calls Windows `SetThreadExecutionState(ES_CONTINUOUS | ES_SYSTEM_REQUIRED | ES_DISPLAY_REQUIRED)`, so the PC will not auto-sleep or blank the screen during a long download; unticked (or when the window is closed / 退出 is pressed) it resets to `ES_CONTINUOUS` and your normal power plan returns. The choice is saved as `prevent_sleep` in `config.json` and restored on startup. Verified locally: ON returns success and blocks sleep, OFF releases it, and the value round-trips through `config.json`
- **v1.6.5 — download-logic switch (new batch gate / legacy per-thread)**: settings → 下载设置 now has a **下载逻辑** option. **新版（整批暂停）** is the v1.6.1 behaviour: every N images *all* download threads wait together, so no thread keeps fetching during the pause. **旧版（逐张停顿）** restores the v1.4.x behaviour: only the thread that hit the pause sleeps, the others keep downloading. Saved as `download_engine` in `config.json`; applies to every site and takes effect immediately. Verified offline: with a 3 s pause the other thread was blocked 2.65 s in new mode vs 0 s in legacy mode
- **v1.6.4 — human-mode scope selector**: next to the 模拟人工下载 checkbox there is now a **选择网站…** button. Pick **全部网站** (default, unchanged behaviour) or **仅指定的网站** and tick any number of sites in a multi-select list; the switch then only applies to those sites, everything else keeps the program's default speed. The choice is saved in `config.json` (`human_scope` + `human_sites`), re-applied on startup, and refreshed when you switch sites — the label next to the button always shows whether the current site is actually covered
- **v1.6.3 — mgread.io overseas-server retries & faster chapter paging**: pages that come back as a reset or stay blank are now reloaded up to **8 times** (blank detected after 8 s instead of waiting a full timeout), which gets past the site's frequent transient drops; chapter pagination pages 2+ are read over HTTP with static HTML parsing instead of rendering each page (seconds instead of minutes), an out-of-range page ends the pagination immediately instead of 8 wasted reloads, and a search query whose slug matches exactly (`solo leveling` → `solo-leveling`) now wins over same-name spin-offs. Verified locally: homepage in on attempt 4–7, search+detail+first chapter images and full 200-chapter list all completed
- **v1.6.2 — mgread.io Cloudflare verification + connection-reset handling**: the site now front-loads a Cloudflare “Just a moment” check on every page. The crawler detects it, tells you to solve it **once** in a headed window, and keeps a persistent browser profile for this site only so the clearance cookie is reused on later runs (headless works afterwards). No CAPTCHA solving is automated
- **v1.6.1 — yumanhua 10-image batches with a real global pause**: 漫画客 now defaults to `SPEED_MODE='batch10'` — 10 images are downloaded, then **every download thread stops for 150–300 s** (a global gate, not one thread sleeping while the others keep fetching), then the next 10 begin; chapters are separated by another 300–600 s. Threads/并发 are 2/2 for this site only. Verified offline: all workers blocked together during the pause window
- **v1.6.0 — human-mode switch in Settings**: the Settings page now has a “模拟人工下载（随机停顿 + 低并发，降低被封风险）” checkbox, **on by default and applying to every site**. It writes `config.HUMAN_MODE` immediately (no restart needed) and is saved to `config.json` as `human_mode`. Turned on: per-image 0.3–0.9 s pause, ≤3 image concurrency, ≤2 download threads, 1.5–4.0 s between chapters. Turned off: downloads run with the fixed thread/concurrency/delay values from Settings (faster, higher block risk)
- **v1.5.6 — back to the program's own defaults**: `SPEED_MODE` defaults to `'default'`, which sets **no** chapter-concurrency cap and **no** `download_threads` / `download_concurrency` / `download_delay` / pause overrides — 漫画客 downloads exactly with the built-in defaults and whatever the GUI is set to, same as every other site. Chapter collection still opens several pages at once (using the GUI's "tabs" value), closes each chapter's tab as soon as that chapter reaches the bottom, and force-closes a chapter that runs past 300 s. Verified: 3 chapters, 52/40/28 images, tabs closed automatically
- **v1.5.5 — each chapter closes its own tab the moment it finishes**: parallel collection now watches per-chapter completion and closes that chapter's tab immediately instead of waiting for the whole batch, prints `章节N 采集完成，已关闭标签页` so the shutdown is visible in the log, and force-closes any chapter still running after 300 s. Measured headed: 3 chapters collected in 53 s with every chapter tab closed automatically; downloads keep the global thread settings (no extra delay)
- **v1.5.4 — multi-threaded chapter collection back on**: `SPEED_MODE` defaults to `'multi'` — 3 chapter pages are opened and scrolled in parallel (worker threads only drive their own tab; opening/closing stays on the main thread), each page is closed the moment it hits the bottom and the next one opens, and downloads use the global thread settings with no extra delay or pause. Measured: 3 chapters collected in ~45 s (was ~87 s sequentially) and the full run took 115 s for 120 images, 21 transient 403s recovered. `'default'` (one page at a time) and `'slow'` remain available if 403/429 appear

---

## 1. UI Guide

After launch (double-click `start.bat`), the main window has four pages in the left sidebar (plus a collapsible **🔐 Password Manager** menu below Universal Image Downloader):

| Page | Purpose |
|---|---|
| 🏠 **Home** | Download comics: site selector, search, load chapter list, start download |
| 📚 **Local Library** | Scan downloaded folders, browse chapters, **check for updates**, download new chapters, verify bad images |
| 🔄 **Format Converter** | Merge downloaded manga images into e-books (PDF/CBZ/EPUB/MOBI) |
| 🖼 **Universal Image Downloader** | URL-template / web-page / HTML-source batch image download (built-in usage guide) |
| 🈶 **Translate** | English manga chapter → Chinese (BallonsTranslator engine; Chinese PNG + `中英对照.txt`) |
| 🔐 **Password Manager** | Click `🔐 密码管理 ▸` to expand → open the vault manager page (encrypted login containers) |
| ⚙ **Settings** | Image naming, window, download and browser options |

### 🏠 Home (download)

| Area | Description |
|---|---|
| **Site selector** | Dropdown to choose a manga website (built-in sites + your custom adapters) |
| **Comic name input** | Type the manga title |
| **Search button** | Searches the selected site; candidates are listed below; click one to auto-fill and open its detail page |
| **Need login** | When checked, a login flow runs before opening chapters: Open Login (browser login page) → scan QR / sign in → Login Done. **Login is saved as an encrypted container** (v1.1.1): next time the site needs login, the program auto-logs-in with the saved container — no QR scan needed. On this PC it unlocks automatically; on another PC you enter the container password; 4 wrong passwords destroy the container |
| **Load chapter list** | Reads all chapters of the comic (number + name), builds a checkable list |
| **Chapter list** | Select chapters to download (all / partial / range) |
| **Start download** | Downloads all images of the selected chapters (auto-creates `comic/chapter/1.jpg` structure) |
| **Retry failed images** | Retry images that failed before |
| **Retry failed chapters** | Batch-retry failed chapters |
| **禁止休眠 (v1.6.6)** | Main-page checkbox: keeps Windows from auto-sleeping or blanking the screen while the program runs (long downloads); saved as `prevent_sleep` |
| **Log window** | Live log of every step (search → chapters → images → download → cover) |

Folder structure after download:

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

> Keep the console window open while downloading; close it or the main window to quit.

### 🔐 Secure Login Vault (v1.1.1)

Login sessions are stored in **encrypted containers** (`cookies/<site>_cookies.json`, AES-256-GCM + **Argon2id** key derivation since v1.3.1; PBKDF2 vaults from older versions remain readable and auto-upgrade):

| Behavior | Description |
|---|---|
| **This PC** | Opens automatically via hardware fingerprint — you are never asked |
| **Another PC** | Requires the **container password** (set when login completes, or in Password Manager) |
| **4 wrong passwords** | The container **destroys itself** — all saved login data is wiped permanently |
| Password rule | ≥ 6 chars, must contain **letters + digits + symbols** |

**Password Manager page** (sidebar → `🔐 密码管理 ▸` → open):
- View each site's container status (encrypted / needs password / destroyed / none)
- Set or change the container password (no old password needed on this PC)
- Test a password (careful: wrong attempts count, 4 = destroy)
- Clear a container (delete that site's saved login, requires re-login)

### 🔄 Format Converter page (merge manga into a book)

| Control | Description |
|---|---|
| **Add folder** | Add a manga folder to convert |
| **Scan sub-folders (batch)** | Pick a comic root folder; every sub-folder containing images is added to the list at once |
| **Remove selected / Clear list** | Manage the list |
| **⚡ One-click whole-book merge** | **Merge all folders in the list (1 or many) into ONE file** (auto cover) |
| **Start convert** | Convert per current settings (whole book / per chapter, cover on/off) |
| Target format | PDF (universal) / CBZ (comic readers) / EPUB (e-book) / MOBI (needs Calibre) |
| Output folder | Where the result files are saved |
| **Per-chapter files** | Checked = one file per chapter (cover only on the first); unchecked = merge into one file |
| **Add cover to first book** | Default on: use the original manga cover automatically |
| **Choose cover image** | Pick any image manually as the cover (overrides auto detection) |
| Convert log | Live progress of every file |

**Cover rules**:
- Auto-detects a **0-prefixed folder** (e.g. `0/cover.jpg`) as the original cover; prefers `cover.*`, else the first image in that folder
- No 0-folder → no extra cover added; the first image naturally becomes the cover page
- Only the first book gets a cover; other chapters do not

**Supported image inputs**: JPG / JPEG / PNG / WebP / GIF / BMP (mixed formats OK)

**After conversion**: the source list is cleared automatically, ready for the next batch.

---

## 2. Supported Sites

**Built-in crawlers** (code-level adapters, most stable):

| Site | Notes |
|---|---|
| Tencent Anime ac.qq.com | Chapter list with names; direct image collection; chapters needing login/VIP are detected and reported |
| ManWa, Copymanga & other built-in sites | Built-in adapters |

**Custom adapter sites** (`custom-sites/*_adapter.json`, preinstalled):

| Adapter | Site | Notes |
|---|---|---|
| `kanman_adapter.json` | kanman.com | Image URLs in page JS variables; full and reliable |
| `zymk_adapter.json` | zymk.cn | Chapter pages redirect to kanman; compatible |
| `cosz_adapter.json` | cosz.com | WordPress gallery; supports lazy-load `data-src` |
| `manwa_adapter.json` | ManWa | Adapter example |

> Note: **paid/VIP chapters** are restricted by the site's account permissions (guests see preview pages only). After login, free chapters download fully; VIP paid chapters need a corresponding membership. The program never bypasses paywalls.

---

## 3. Adding Any New Site (Auto Analyzer wizard)

Open **Auto Site Analyzer** from the main window, 4 steps:

1. **Open & analyze**: paste the new site's homepage; the program analyzes search box / search entry structure
2. **Trial search**: type a keyword; the program lists candidate comic links (filter & fix rules)
3. **Analyze chapters**: open the selected comic detail page; the program scrolls / paginates to find all chapters (with names)
4. **Analyze images → save adapter**: opens a chapter page, analyzes image structure, generates rules and **saves to `custom-sites/newsite_adapter.json`** — reusable forever

After saving, refresh the site list: the new site works like built-in ones (search, chapter list, download).

### Adapter JSON fields

```json
{
  "site_name": "Display name",
  "site_url": "https://site.com/",
  "search": {
    "mode": "get",
    "url_template": "https://site.com/search?keyword={kw}",
    "result_js": "return []; // extract comic links [{url,title},...]"
  },
  "detail": {
    "chapters_js": "return []; // extract chapter links [{url,title},...]",
    "chapters_scroll": false,
    "chapters_reverse": false,
    "cover_js": "return '';  // cover image URL"
  },
  "chapter": {
    "images_js": "return [];  // chapter image URL array",
    "scroll_to_load": false
  }
}
```

- `chapters_scroll: true`: lazy-loaded chapter lists auto-scroll + click "load more" until complete
- `scroll_to_load: true`: lazy-loaded chapter images are scrolled into view before extraction

---

## 3.5 Browser Guide (download needs a browser)

The program drives a **local browser** to open target pages and collect chapter images. Here is how to use it with **Edge** or any other browser:

### Recommended: Microsoft Edge

- **Built into Windows** — the program auto-detects Edge, normally **no setup needed**
- To verify/switch: Settings page → Browser Settings → choose `Edge`

### Other browsers (Chrome / 360 / Firefox ...)

1. Settings page → Browser Settings → select your browser type
2. If the path is not auto-detected, click **Browse** and pick the browser EXE manually:

| Browser | Typical path |
|---|---|
| **Edge** | `C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe` |
| **Chrome** | `C:\Program Files\Google\Chrome\Application\chrome.exe` |
| **360 Safe** | `C:\Program Files (x86)\360\360se6\Application\360se.exe` |
| **Firefox** | `C:\Program Files\Mozilla Firefox\firefox.exe` |

3. Save and restart the program.

### Logging into sites (QR / account)

1. Check **Need login** on the Home page
2. Click **Open Login** → the program opens the browser login page
3. Scan the QR / sign in → click **Login Done**
4. The login is stored as an **encrypted container**: next time this site needs login, the program auto-logs-in (no QR scan). See the vault section below.
5. CAPTCHA / sliders: follow the on-page instructions (you may complete them manually in the browser window).

> The browser window **closes automatically** after chapter loading / downloads finish — do not close it manually mid-download.

---

## 4. Environment & Installation

**Requires Python 3.10** (tested on `Python 3.10.10`), dependencies:

```
DrissionPage
aiohttp
aiofiles
execjs        # requires Node.js installed
pycryptodome
cryptography    # Argon2id (vault password derivation)
requests
requests_file
lxml
```

Install (admin shell):

```bat
pip install DrissionPage aiohttp aiofiles execjs pycryptodome cryptography requests requests_file lxml beautifulsoup4 img2pdf
```

Node.js: https://nodejs.org/ (runtime for execjs).

## 5. One-Click Start

Double-click **`start.bat`** (auto-checks Python, network and dependencies, then opens the GUI).

> Missing module? `pip install <module>` then start again.

### Self-check & logs

- Every launch runs a **self-check** (Python version / required modules / network socket / core files / writable dirs); results go to **`startup_diag.log`**
- Unexpected errors are written to **`crash.log`** (so even a "black window flashed by" can be diagnosed)
- If a blocking error is found, a dialog shows the log file location

### Network repair (WinError 10038)

If the program reports a network-stack failure (socket error WinError 10038) or cannot reach the internet:

1. Right-click **`fix-network-stack.bat`** → **Run as administrator** (resets Winsock / TCP-IP / DNS)
2. **Restart your PC**
3. The program can connect normally afterwards

> Format conversion is fully local and works without internet.

## 6. Adding Your Own Site Crawler (two ways)

- **No coding**: Auto Site Analyzer wizard / "New Adapter" — generate a JSON adapter (see `Docs/How-to-Add-a-New-Site.md`)
- **Python**: "Crawler Source Manager" window → "New from template" → edit → save; sources in `sites_data/` are auto-loaded (see `Docs/How-to-Add-a-Custom-Crawler.md`)

### Standalone EXE (recommended for non-Python users)

`MangaNest.exe` (PyInstaller one-file build, DrissionPage bundled) is provided in **GitHub Releases**:

1. Download the release package and extract (the EXE must stay in the same folder as `sites_data`, `custom-sites`, `config.json`)
2. Double-click `MangaNest.exe` — no Python needed

> Site folders (`sites_data`, `custom-sites`) are read from the EXE's own directory; don't move the EXE alone.

---

## 7. FAQ

| Problem | Cause & fix |
|---|---|
| Black window flashes and closes | BAT line-ending/encoding issue; use the original BAT from the package, or run `python gui.py` manually |
| No module named 'xxx' | Missing dependency; install per section 4 |
| Chapter list shows numbers only | Old bug, fixed; new version shows "Chap N + name" |
| A chapter downloads only 1–2 images | Paid / login-required chapter; guests see preview only; check "Need login" and retry |
| Search shows unrelated results | Some sites' search APIs don't filter by keyword (e.g. kanman shows hot list); adapters apply keyword filtering + fallback |
| Missing chapters / can't reach next page | Lazy-loaded sites need scrolling; new version auto-scrolls + paginates |
| Downloaded images include UI pictures | Restricted to real images inside the chapter container |
| Converted PDF won't open | Old object-number bug; fixed in v1.1 — re-convert |
| Conversion still produces separate files | Use "⚡ One-click whole-book merge" to merge all folders into ONE file |
| "Self-check found problems" dialog | Open `startup_diag.log`; for network issues use `fix-network-stack.bat` (section 5) |

---

## 8. Manga Translation (English → Chinese)

Full guide: **`Docs/Translation.md`**. Short version:

1. Install the open-source **BallonsTranslator** engine into `tools/Ballonstranslator_win_minium/` (the engine + models are **not** in this repository — only our integration code is).
2. Copy `Extensions/BallonsTranslator-custom-modules/trans_*.py` into `<BallonsTranslator>/custom_modules/`.
3. Set `config/config.json` inside BallonsTranslator to `textdetector=ctd`, `ocr=mit48px`, `inpainter=lama_large_512px`, `translator=Hybrid`.
4. In MangaNest: **🈶 Translate** page → pick a chapter folder → **Start**. Or CLI:

```bat
python bt_slice_translate.py --input "<chapter folder>" --output "<output folder>"
python bt_slice_translate.py --input "<chapter folder>" --text-only     # 中英对照.txt only
```

Output: `<output>/*.png` (Chinese pages, same names as source) + `<output>/中英对照.txt`.

Pipeline: slice (~2000px, cut points snap to blank bands) → BallonsTranslator per slice (GPU, LaMa inpaint + NLLB translation) → missing slices auto re-run → OCR-empty blocks restored from original art → residual-English ink cleanup on the inpainted base → composite Chinese text layer back with an exclusion mask → stitch pages.

Known limits:
- Translation quality is offline-model grade (NLLB-200-distilled-600M); short exclamations and name puns are occasionally literal.
- Inpainted flat-colour bubbles can show a faintly different rectangle shade (rare; cosmetic only).
- CPU-only machines are ~10× slower; GPU ≥4 GB VRAM recommended.

---

## 9. Disclaimer (see `Docs/DISCLAIMER.txt` for English & Chinese)

1. This software is **for personal learning, research and technical exchange only; commercial use is strictly prohibited**.
2. Downloaded content is owned by the original authors/platforms; **delete within 24 hours** and support the official releases.
3. Users must obey each site's terms of service; all consequences of using this software are the user's own responsibility.
4. This software **provides no paywall bypass, membership cracking or piracy distribution**; VIP content requires proper permission on the platform.
5. Provided "as is"; the author makes no warranty of fitness, stability or completeness, and accepts no direct or indirect liability.

By using this software you agree to the terms above.

import time
import re
import json
import random
import threading
from urllib.parse import quote

from utils import is_normal_url


class MgreadCrawler:
    """Mgread.io (mgread.io) 爬虫 —— 2026-11 实测解析

    站点结构（实测确认）：
      - 传统 WordPress 主题 SSR，主域无 JS 挑战门；但 requests 直连会被 TLS 重置
        （10054），故统一走真实浏览器上下文，NEEDS_BROWSER=True。
      - 站内搜索：GET /?s=书名 （WordPress 默认搜索），结果为 a[href*="/manga/"]
        详情链接形如 /manga/{slug}/，需排除带 chapter- 的章节链接。
      - 详情页：章节链接 /manga/{slug}/chapter-{n}/，容器 .chapter-list .chapter-item；
        章节列表分页 /manga/{slug}/chapter/page/{n}/#chapter-list，需翻页抓全；
        页面默认新→旧，按 chapter 号升序整理为旧→新。
      - 阅读页：容器 .chapter-body；图片真实地址在 data-original-src 属性，
        首图直接在 src，其余 src 为 SVG 懒加载占位；真漫画图 CDN 为 mg.mgread.io。
      - 封面：meta og:image（同域 wp-content/uploads 目录）。

    法律提示：本插件仅用于 Python 自动化学习研究，禁止用于抓取、保存、传播受版权
    保护的漫画资源，请遵守目标站点用户协议与著作权相关法律法规。
    """

    # 站点元数据
    SITE_NAME = 'Mgread'
    SITE_URL = 'https://mgread.io/'
    REQUIRES_LOGIN = False
    NEEDS_BROWSER = True
    # 英文站：中文关键词搜索时由 crawler.search_comic 自动译成英文再搜
    SEARCH_LANG = 'en'

    # 站点配置
    CONFIG = {
        'site_url': 'https://mgread.io/',
        'locators': {
            'cover_image': "xpath://meta[@property='og:image' or @name='og:image']",
        },
        'image_attr': 'src',
        'chapter_group_size': None,
        'image_referer': 'https://mgread.io/',
        # 该站 CDN 会重置 requests 的 TLS 连接（10054），但 aiohttp 可直连 200，
        # 故默认走协程(aiohttp)通道，不用纯多线程(requests)通道。
        'download_mode': 'coroutine',
        # 2026-11 起站点前置 Cloudflare 人机验证（"Just a moment"）：
        # 固定浏览器配置目录，手动验证一次后 cf_clearance 长期复用，不用每次重验。
        'persistent_profile': True,
        'challenge_wait': 240,
        # 海外服务器瞬时不通很常见：同一页多刷新几次通常就能进
        'page_attempts': 8,
        'plain_timeout': 8,
    }

    # 页面状态探测：内容是否就绪 / 是否 Cloudflare 人机验证 / 是否连接被重置
    # 挑战判定只认「标题特征 / 正文提示 / Turnstile 验证框」，不用 #challenge-* 元素
    # 是否存在（正常页面 DOM 里也可能残留这些 id，会造成误判）
    _PROBE_JS = (
        "const sel=arguments[0];"
        "const cnt=document.querySelectorAll(sel).length;"
        "const t=(document.title||'');"
        "const body=(document.body?document.body.innerText:'').slice(0,400);"
        "const err=(location.href.indexOf('chrome-error://')===0)"
        "||/ERR_CONNECTION|ERR_SSL|无法访问此页面|已重置连接/i.test(body);"
        "const ch=t.indexOf('Just a moment')>-1||t.indexOf('Attention Required')>-1"
        "||/verify you are human|checking your browser|确认您是真人/i.test(body)"
        "||!!document.querySelector('iframe[src*=\"challenges.cloudflare.com\"]');"
        "return {count:cnt, challenge:ch, err:err, title:t, href:location.href};"
    )

    # 章节翻页上限（防死循环）
    _MAX_CHAPTER_PAGES = 300

    def __init__(self, crawler):
        self.crawler = crawler
        self.locators = crawler.locators
        self.image_attr = crawler.image_attr
        # 章节表缓存：[{url, title}]
        self._chapter_entries = None
        self._cover_url = None

    # ---------- 搜索 ----------
    @staticmethod
    def _detail_has_chapters(target_comic_tab):
        """轻量检测详情页是否含章节链接（空壳/未上线作品显示 No chapters found）。"""
        try:
            n = target_comic_tab.run_js(
                "return document.querySelectorAll('.chapter-list a[href*=\"/chapter-\"]').length"
            ) or 0
            return n > 0
        except Exception:
            return False

    @staticmethod
    def _query_tokens(text):
        """把查询/标题切成有意义的英文词元，去掉停用词与短词（撇号视为分隔符）。"""
        stop = {'the', 'a', 'an', 'of', 'to', 'my', 'me', 'i', 'and', 'in', 'on',
                'is', 'was', 'with', 'after', 'before', 'for', 'at', 'it', 'this',
                'that', 'you', 'your', 'his', 'her', 'be', 'not', 'from', 'by',
                'im', 'its', 's', 't', 'do', 'does', 'did', 'are', 'were', 'have', 'has'}
        raw = re.findall(r"[a-z0-9]+", (text or '').lower().replace('’', ''))
        return [t for t in raw if len(t) >= 3 and t not in stop]

    def _rank_by_relevance(self, query, candidates, threshold=0.5):
        """按词重合度给候选打分排序，过滤完全不相关的搜索结果。

        规则：查询词元命中率 >= threshold 才算相关；候选间按命中率从高到低排。
        查询含 2 个以上有效词时，要求至少命中 2 个，避免只靠一个通用词
        （如 youngest / princess）就误判成相关作品。
        """
        q_tokens = self._query_tokens(query)
        if not q_tokens:
            # 中文等非英文查询无法做英文词重合打分：直接判为无相关结果，
            # 由 crawler.search_comic 的中文->英文翻译流程兜底
            return []
        min_hits = 2 if len(q_tokens) >= 2 else 1
        exact_slug = re.sub(r'[^a-z0-9]+', '-', query.lower()).strip('-')
        scored = []
        for cand in candidates:
            haystack = self._query_tokens(
                (cand.get('text') or '') + ' ' + cand['url'].replace('-', ' ')
            )
            haystack_set = set(haystack) | set(
                re.findall(r'[a-z0-9]+', cand['url'].lower())
            )
            hits = sum(1 for t in q_tokens if t in haystack_set)
            if hits < min_hits:
                continue
            score = hits / len(q_tokens)
            slug = cand['url'].rstrip('/').split('/')[-1].lower()
            if exact_slug and slug == exact_slug:
                # 查询词与 slug 完全一致（如 solo leveling -> solo-leveling）时优先精确命中，
                # 避免被同名衍生作（solo-leveling-arise / -ragnarok）排到前面。
                score = 1.0
                cand['exact'] = True
            if score >= threshold:
                cand['score'] = round(score, 2)
                scored.append(cand)
        scored.sort(key=lambda c: (not c.get('exact'), -c['score'], -len(c['url'])))
        if scored:
            print('候选相关性: ' + ' | '.join(
                f"{c['url'].rstrip('/').split('/')[-1]}({c['score']})" for c in scored[:5]
            ))
        return scored

    @staticmethod
    def _wait_chapter_links(tab, timeout=1.8, interval=0.3):
        """轮询等待详情页章节链接渲染完成，就绪即返回，避免无脑固定 sleep。"""
        start = time.time()
        while True:
            try:
                n = tab.run_js(
                    "return document.querySelectorAll('.chapter-list a[href*=\"/chapter-\"]').length"
                ) or 0
                if n > 0:
                    return n
            except Exception:
                pass
            if time.time() - start >= timeout:
                return 0
            time.sleep(interval)

    def _probe(self, tab, selector):
        """读取页面状态：count / challenge / err / title / href；失败返回空字典。"""
        try:
            return tab.run_js(self._PROBE_JS, selector) or {}
        except Exception:
            return {}

    def _goto_ready(self, tab, url, selector, label='页面', attempts=None, timeout=None):
        """打开 url 并等待内容就绪，自动处理站点侧两类拦截：

          1. Cloudflare 人机验证（`Just a moment` / Turnstile 复选框）：
             有头模式下提示用户手动点一次，程序轮询等待；无头模式直接报错并指引切有头
             （不实现任何自动破解验证码的逻辑）；
          2. 连接被重置 / 页面长时间空白（该站为海外服务器，常见瞬时不通）：
             自动刷新重试，间隔逐次加长（含随机抖动），多次刷新通常就能进。
        """
        timeout = timeout or int(self.CONFIG.get('challenge_wait', 240))
        plain_timeout = int(self.CONFIG.get('plain_timeout', 8))
        attempts = attempts or int(self.CONFIG.get('page_attempts', 8))
        last_err = ''
        for attempt in range(1, attempts + 1):
            try:
                tab.get(url, timeout=15)
            except Exception as e:
                last_err = f"打开失败: {e}"
                print(f"{label} 打开异常（第{attempt}/{attempts}次）: {e}")
            else:
                start = time.time()
                warned = False
                while time.time() - start < timeout:
                    st = self._probe(tab, selector)
                    if st.get('count'):
                        if warned:
                            print(f"{label} 已通过人机验证，继续执行")
                        if attempt > 1:
                            print(f"{label} 第{attempt}次尝试成功")
                        return True
                    if st.get('err'):
                        last_err = '连接被重置(ERR_CONNECTION_RESET)'
                        print(f"{label} 连接被重置，准备重试（第{attempt}/{attempts}次）")
                        break
                    if st.get('challenge'):
                        if not warned:
                            warned = True
                            if getattr(self.crawler, 'headless', False):
                                raise Exception(
                                    f"{label} 命中 Cloudflare 人机验证，无头模式无法完成验证。"
                                    "请在 设置 → 浏览器设置 切换为「有头（显示窗口）」，"
                                    "重新运行并在弹出的窗口里完成一次『Verify you are human』；"
                                    "验证结果会保存到持久化配置目录，之后可直接用无头模式。"
                                )
                            print(f"{label} 遇到 Cloudflare 人机验证：请在弹出的浏览器窗口中"
                                  "完成『Verify you are human』验证，程序会自动等待并通过。")
                    else:
                        last_err = '页面内容超时未出现'
                        if time.time() - start > plain_timeout:
                            # 既没验证页也没内容：多半是连接被瞬时掐断，刷新比干等有效
                            print(f"{label} 页面空白 {plain_timeout}s，刷新重试"
                                  f"（第{attempt}/{attempts}次）")
                            break
                    time.sleep(1.2)
                else:
                    # 等待超时（既没就绪也没报错）
                    if warned and not getattr(self.crawler, 'headless', False):
                        last_err = '人机验证未在时限内完成'
                    print(f"{label} 等待超时（{timeout}s，第{attempt}/{attempts}次）")
            if attempt < attempts:
                wait = min(3.0 + attempt * 1.5, 15.0) + random.uniform(0, 2.0)
                print(f"{label} {wait:.1f}s 后重新加载")
                time.sleep(wait)
        raise Exception(
            f"{label} 连续 {attempts} 次尝试仍未就绪（{last_err or '原因未知'}）。"
            "若为 Cloudflare 人机验证请切到「有头（显示窗口）」模式手动验证一次；"
            "若为连接重置请稍后重试或更换网络出口。"
        )

    def _rest_search(self, keyword, per_page=20):
        """走 WordPress REST 搜索接口（/wp-json/wp/v2/search）取候选作品。

        相比整页 /?s= 搜索：单次约 1~2.5s（整页约 9s），且直接拿到完整标题，
        便于做相关性打分。优先用 aiohttp 直连（主域 REST 实测可用），
        失败再退回浏览器 fetch（要求当前标签在同源页面）。
        只接受 subtype='manga' 且 url 形如 /manga/{slug}/。
        """
        from urllib.parse import quote as _q
        api = (f"https://mgread.io/wp-json/wp/v2/search?search={_q(keyword)}"
               f"&per_page={per_page}&subtype=any")
        raw = self._rest_fetch_aiohttp(api)
        if not raw:
            raw = self._rest_fetch_browser(api)
        if not raw:
            raise Exception('REST 搜索无响应（aiohttp 与浏览器 fetch 均失败）')
        try:
            data = json.loads(raw)
        except Exception:
            return []
        if not isinstance(data, list):
            return []
        results = []
        seen = set()
        for item in data:
            if (item.get('subtype') or '') != 'manga':
                continue
            url = item.get('url') or ''
            m = re.search(r'/manga/([^/?#]+)/?', url)
            if not m:
                continue
            slug = m.group(1)
            if slug in seen:
                continue
            seen.add(slug)
            results.append({
                'url': url if url.endswith('/') else url + '/',
                'text': ' '.join((item.get('title') or '').split()),
            })
        print(f"REST 搜索 '{keyword}' 命中 {len(results)} 个候选作品")
        return results

    @staticmethod
    def _rest_fetch_aiohttp(api_url, retries=3):
        """aiohttp 直连 REST（主域只能走 aiohttp，requests 会被 TLS 重置）。"""
        import asyncio
        import aiohttp

        async def _run():
            timeout = aiohttp.ClientTimeout(total=12)
            async with aiohttp.ClientSession(timeout=timeout) as s:
                async with s.get(api_url, headers={
                    'User-Agent': 'Mozilla/5.0',
                    'Referer': 'https://mgread.io/',
                    'Accept': 'application/json',
                }) as r:
                    if r.status != 200:
                        return ''
                    return await r.text()

        for attempt in range(retries):
            try:
                return asyncio.run(_run())
            except Exception as e:
                print(f"REST(aiohttp) 第{attempt + 1}次失败: {e}")
                time.sleep(0.6)
        return ''

    def _rest_fetch_browser(self, api_url):
        """浏览器内 fetch 兜底（只在当前标签属于 mgread.io 同源时才会成功）。"""
        js = (
            "return (async () => {"
            "const r = await fetch(arguments[0], {credentials:'include'});"
            "if (!r.ok) return '';"
            "return await r.text();"
            "})();"
        )
        try:
            return self.crawler.tab.run_js(js, api_url) or ''
        except Exception as e:
            print(f"REST(浏览器 fetch) 失败: {e}")
            return ''

    def _fetch_page_stats(self, url):
        """在当前标签内 fetch 页面 HTML，返回 {status, n(章节链接数), chl(是否挑战页)}。

        用于「先判断再开页面」：章节第 N 页超出范围时站点返回 200 但无章节链接
        （或 404），可直接结束翻页，不用反复刷新干等；被挑战页/网络错误拦住时返回空，
        调用方按未知处理。
        """
        try:
            js = (
                "return (async () => {"
                "const r = await fetch(arguments[0], {credentials:'include'});"
                "const t = await r.text();"
                "const chl=/Just a moment|challenge-platform|verify you are human/i.test(t);"
                "return {status:r.status, chl:chl, n:(t.match(/\\/chapter-\\d+\\//g)||[]).length};"
                "})();"
            )
            return self.crawler.tab.run_js(js, url) or {}
        except Exception:
            return {}

    def _fetch_chapter_links(self, url):
        """在当前标签内 fetch 章节分页 HTML，直接解析出章节链接列表。

        返回 {status, chl, links:[{href,text}]}；被挑战页/网络错误拦住返回 {}，
        由调用方回退到浏览器渲染。
        """
        try:
            js = (
                "return (async () => {"
                "const r = await fetch(arguments[0], {credentials:'include'});"
                "const t = await r.text();"
                "const chl=/Just a moment|challenge-platform|verify you are human/i.test(t);"
                "if (chl) return {status:r.status, chl:true, links:[]};"
                "const doc = new DOMParser().parseFromString(t,'text/html');"
                "const links = Array.from(doc.querySelectorAll('.chapter-list a[href*=\"/chapter-\"]'))"
                ".map(a=>({href:a.getAttribute('href'), text:(a.innerText||'').trim()}));"
                "return {status:r.status, chl:false, links:links};"
                "})();"
            )
            return self.crawler.tab.run_js(js, url) or {}
        except Exception:
            return {}

    def _fetch_detail_has_chapters(self, url):
        """在当前标签内 fetch 候选详情页 HTML，统计章节链接数，预筛空壳。

        返回 True=含章节；False=明确判定为空壳（HTTP 200 且确无章节链接）。
        fetch 失败、非 200、或返回 Cloudflare 挑战页时无法判断，保守放行（True），
        避免把正常作品误杀成空壳。
        """
        st = self._fetch_page_stats(url)
        if st.get('n'):
            return True
        if st.get('status') != 200 or st.get('chl'):
            return True   # 无法判断（被验证页/错误页拦截），保守放行
        return False

    def search_comic(self, comic_name, comic_id=None):
        """支持三种入口：
          1. comic_id 或粘贴的详情页/章节页链接 -> 解析 slug 直达；
          2. 关键词 -> /?s= 搜索，逐个打开候选详情检测，选首个含章节的作品；
          3. 候选均为空壳/未上线时抛出明确异常。
        """
        # 1) 链接 / ID 直达：comic_id 支持完整 URL 或纯 slug；comic_name 仅识别完整 URL
        slug = None
        if comic_id:
            slug = self._slug_from_url(comic_id)
            if not slug and re.match(r'^[A-Za-z0-9\-]+$', str(comic_id).strip()):
                slug = str(comic_id).strip()
        if not slug and comic_name:
            slug = self._slug_from_url(comic_name)
        if slug:
            detail_url = f"https://mgread.io/manga/{slug}/"
            print(f"按链接/ID打开详情页: {detail_url}")
            target_comic_tab = self.crawler.page.new_tab(detail_url)
            self._goto_ready(target_comic_tab, detail_url,
                             '.chapter-list a[href*="/chapter-"]', label='详情页')
            return target_comic_tab

        search_url = f"https://mgread.io/?s={quote(comic_name)}"
        print(f"正在搜索漫画: {comic_name}")
        # 优先走 WordPress REST 搜索接口：单次 0.7~1.1s，直接返回标题+链接 JSON，
        # 不用整页加载（整页 /?s= 一次约 9s），多关键词变体也能快速比较。
        results = []
        rest_ok = True
        try:
            results = self._rest_search(comic_name)
        except Exception as e:
            rest_ok = False
            print(f"REST 搜索异常，改用整页搜索: {e}")

        if not results and not rest_ok:
            # 兜底：整页搜索（REST 不可用或被拦截时）
            print(f"搜索URL: {search_url}")
            js = (
                "return Array.from(document.querySelectorAll('a[href*=\"/manga/\"]'))"
                ".map(a=>({href:a.getAttribute('href'), text:(a.innerText||'').trim()}));"
            )
            self.crawler.tab.get(search_url, timeout=8)
            # 搜索页可能先落到人机验证或被重置；最多等 60s，失败交给下面的重试循环
            try:
                self._goto_ready(self.crawler.tab, search_url, 'a[href*="/manga/"]',
                                 timeout=90, label='搜索结果页')
            except Exception as e:
                print(f"搜索结果页等待未就绪: {e}")

            # 搜索页偶发加载慢/被重置/挑战门刷新，轮询等待结果链接出现，最多重试 3 次
            items = []
            for attempt in range(3):
                try:
                    time.sleep(0.8)
                    items = self.crawler.tab.run_js(js) or []
                except Exception as e:
                    print(f"搜索页读取异常(第{attempt + 1}次): {e}")
                    items = []
                if items:
                    break
                print(f"搜索结果尚未加载，重试 ({attempt + 1}/3)")
                try:
                    self.crawler.tab.get(search_url)
                except Exception:
                    pass

            # 只保留详情级链接 /manga/{slug}/，排除章节链接（含 chapter-）与作者/分类
            seen = set()
            for it in items:
                href = it.get('href') or ''
                if not href or 'chapter-' in href:
                    continue
                m = re.search(r'/manga/([^/?#]+)/?', href)
                if not m:
                    continue
                slug = m.group(1)
                if slug in seen:
                    continue
                seen.add(slug)
                url = href if href.startswith('http') else 'https://mgread.io' + href
                results.append({'url': url, 'text': ' '.join((it.get('text') or '').split())})

        if not results:
            raise Exception(f"搜索'{comic_name}'失败，未取到漫画链接")

        # 相关性排序：WordPress 搜索会模糊返回无关作品（如搜不到时给出热门作品），
        # 这里按查询词与候选标题/slug 的词重合度打分，过滤掉完全不相关的候选。
        results = self._rank_by_relevance(comic_name, results)
        if not results:
            raise Exception(
                f"搜索'{comic_name}'没有相关结果（站点只返回了无关作品），"
                f"请换关键词，或直接粘贴作品详情页链接"
            )

        # 先用 fetch 读取候选详情 HTML 预筛空壳（不开新标签，比逐个开标签快数倍）；
        # 命中含章节的作品后再打开详情标签作为目标。
        chosen_tab = None
        for cand in results:
            if not self._fetch_detail_has_chapters(cand['url']):
                print(f"跳过空壳/无章节作品: {cand['url']}")
                continue
            chosen_tab = self.crawler.page.new_tab(cand['url'])
            self._goto_ready(chosen_tab, cand['url'], '.chapter-list a[href*="/chapter-"]',
                             label='详情页')
            print(f"搜索到: {cand['text']} -> {cand['url']}")
            break

        if chosen_tab is None:
            raise Exception(f"搜索'{comic_name}'未找到含章节的作品（候选均为空壳/未上线）")
        return chosen_tab

    # ---------- 封面 ----------
    def get_cover_image(self, target_comic_tab):
        """封面：优先 og:image（name/property 兼容）；兜底详情页封面大图。"""
        if self._cover_url:
            print(f"封面图片URL: {self._cover_url}")
            return self._cover_url
        try:
            og = target_comic_tab.run_js(
                "const m=document.querySelector(\"meta[property='og:image'],meta[name='og:image']\");"
                "return m ? m.content : '';"
            )
            if og and is_normal_url(og):
                self._cover_url = og
                print(f"封面图片URL: {og}")
                return og
        except Exception as e:
            print(f"读取 og:image 失败: {e}")

        # 兜底：详情页封面大图（排除 logo）
        try:
            js = (
                "const imgs=Array.from(document.querySelectorAll('img'));"
                "const c=imgs.find(i=>{const s=i.getAttribute('src')||'';"
                "return s.indexOf('/wp-content/uploads/')>-1"
                " && s.indexOf('logo')===-1;});"
                "return c?c.getAttribute('src'):'';"
            )
            img = target_comic_tab.run_js(js)
            if img and is_normal_url(img):
                self._cover_url = img
                print(f"封面图片URL(详情页img): {img}")
                return img
        except Exception as e:
            print(f"读取详情页封面img失败: {e}")
        return None

    # ---------- 章节列表 ----------
    @staticmethod
    def _slug_from_url(url):
        m = re.search(r'/manga/([^/?#]+)', url or '')
        return m.group(1) if m else None

    def _collect_chapter_entries(self, target_comic_tab):
        """翻页抓取详情页全部章节，按 chapter 号升序（旧→新）。

        返回 [{url, title}]，num=1 对应最早一话。
        """
        if self._chapter_entries is not None:
            return self._chapter_entries

        slug = self._slug_from_url(target_comic_tab.url or '')
        entries = []
        if not slug:
            print("未能从详情页解析漫画 slug")
            self._chapter_entries = entries
            return entries

        # 详情页仍在当前标签时先缓存封面（后续翻页会离开详情页，避免读不到 og:image）
        if not self._cover_url:
            self.get_cover_image(target_comic_tab)

        by_key = {}
        base = f"https://mgread.io/manga/{slug}"

        for page_no in range(1, self._MAX_CHAPTER_PAGES + 1):
            if page_no == 1:
                page_url = f"{base}/"
                try:
                    self._goto_ready(target_comic_tab, page_url,
                                     '.chapter-list a[href*="/chapter-"]',
                                     label='章节第1页')
                    raw = target_comic_tab.run_js(
                        "return Array.from(document.querySelectorAll"
                        "('.chapter-list a[href*=\"/chapter-\"]'))"
                        ".map(a=>({href:a.getAttribute('href'), text:(a.innerText||'').trim()}));"
                    ) or []
                except Exception as e:
                    print(f"章节第1页加载失败: {e}")
                    break
            else:
                # 第 2 页起优先用 HTTP fetch 取静态章节列表（不渲染页面，几秒即可），
                # 顺带判断「分页已到尽头」；fetch 被拦截时才回退浏览器加载。
                raw = []
                st = self._fetch_chapter_links(f"{base}/chapter/page/{page_no}/")
                if st and st.get('links'):
                    raw = st['links']
                    print(f"章节第{page_no}页（HTTP）取到 {len(raw)} 条链接")
                elif st and st.get('status') in (404, 410):
                    print(f"章节第{page_no}页不存在（HTTP {st.get('status')}），结束翻页")
                    break
                elif st and st.get('status') == 200 and not st.get('chl'):
                    print(f"章节第{page_no}页无章节链接，结束翻页")
                    break
                else:
                    page_url = f"{base}/chapter/page/{page_no}/#chapter-list"
                    try:
                        self._goto_ready(target_comic_tab, page_url,
                                         '.chapter-list a[href*="/chapter-"]',
                                         label=f'章节第{page_no}页')
                        raw = target_comic_tab.run_js(
                            "return Array.from(document.querySelectorAll"
                            "('.chapter-list a[href*=\"/chapter-\"]'))"
                            ".map(a=>({href:a.getAttribute('href'), text:(a.innerText||'').trim()}));"
                        ) or []
                    except Exception as e:
                        print(f"章节第{page_no}页加载失败: {e}（后续章节可能不完整，建议重跑）")
                        break

            new_count = 0
            for item in raw:
                href = item.get('href') or ''
                m = re.search(r'/chapter-(\d+)/?', href)
                if not m:
                    continue
                chap_no = int(m.group(1))
                if chap_no in by_key:
                    continue
                title = self._clean_title(item.get('text') or '')
                if not href.startswith('http'):
                    href = 'https://mgread.io' + href
                by_key[chap_no] = {'url': href, 'title': title}
                new_count += 1

            # 本页无新章节：翻页结束
            if new_count == 0:
                break

        ordered = [by_key[k] for k in sorted(by_key)]
        for e in ordered:
            if not e['title']:
                e['title'] = f"第{self._chapter_num_key(e)}话"
        entries = ordered
        print(f"章节列表: {len(entries)} 个唯一章节")
        self._chapter_entries = entries
        return entries

    @staticmethod
    def _chapter_num_key(entry):
        m = re.search(r'/chapter-(\d+)/?', entry.get('url') or '')
        return int(m.group(1)) if m else 0

    # 标题尾部噪声：更新时间（月/周/天/年）、浏览量、评论数等
    _TITLE_NOISE_RE = re.compile(
        r'(\d+\s*(months?|weeks?|days?|年|个月|周|天|月前|年前|天前)'
        r'|\d+\.\d+\s*[KM]?|\b\d+\s*[KM]?\b)\s*$', re.I)

    @classmethod
    def _clean_title(cls, text):
        """剥离章节标题尾部混入的更新时间/浏览量/评论数噪声。"""
        title = ' '.join((text or '').split())
        if not title:
            return ''
        # 反复去掉尾部噪声段，直到标题稳定
        for _ in range(3):
            new = cls._TITLE_NOISE_RE.sub('', title).strip()
            if new == title:
                break
            title = new
        return title

    def get_chapter_count(self, target_comic_tab):
        entries = self._collect_chapter_entries(target_comic_tab)
        print(f"检测到 {len(entries)} 个章节")
        return len(entries)

    def get_chapter_list(self, target_comic_tab):
        """返回完整章节表（含标题）：[{num, url, title}, ...]，供 GUI 显示章节名。"""
        entries = self._collect_chapter_entries(target_comic_tab)
        return [
            {'num': i + 1, 'url': e['url'], 'title': e.get('title') or f"第{i + 1}章"}
            for i, e in enumerate(entries)
        ]

    # ---------- 章节图片 ----------
    @staticmethod
    def _page_no(url):
        fname = url.split('/')[-1].split('?')[0]
        m = re.search(r'(\d+)\.(?:jpg|jpeg|png|webp)', fname, re.I)
        return int(m.group(1)) if m else 10 ** 9

    def _collect_images_from_page(self, chapter_tab):
        """从阅读页 .chapter-body 提取真实漫画页 URL（data-original-src 优先）。"""
        raw_urls = []
        try:
            js = (
                "return Array.from(document.querySelectorAll('.chapter-body img'))"
                ".map(i=>({u:i.getAttribute('data-original-src')||i.getAttribute('src')||'',"
                "alt:i.alt||''}));"
            )
            raw_urls = chapter_tab.run_js(js) or []
        except Exception as e:
            print(f"章节图片提取失败: {e}")
            raw_urls = []

        def _is_real_comic_image(u):
            if not is_normal_url(u):
                return False
            if u.startswith('data:image'):
                return False
            bad = ['gravatar', 'logo', '/wp-content/uploads/', 'favicon',
                   'apple-icon', 'default_cover']
            if any(b in u for b in bad):
                return False
            # 真漫画图位于 mg.mgread.io 域
            return 'mg.mgread.io' in u

        cleaned, seen = [], set()
        for item in raw_urls:
            u = (item.get('u') if isinstance(item, dict) else item) or ''
            u = u.strip()
            if not _is_real_comic_image(u):
                continue
            u = u.split('?')[0]
            if u in seen:
                continue
            seen.add(u)
            cleaned.append(u)

        cleaned.sort(key=self._page_no)
        print(f"共提取 {len(cleaned)} 张图片")
        return cleaned

    def collect_chapter_images(self, chapter_info, max_wait_time=5):
        chapter_num = chapter_info['chapter_num']
        chapter_url = chapter_info['url']
        main_tab = chapter_info['main_tab']
        print(f"正在处理章节{chapter_num}: {chapter_url}")

        herf_list = []
        chapter_tab = None
        try:
            chapter_tab = main_tab.new_tab(chapter_url)
            # 阅读页同样可能被人机验证或连接重置拦住：等验证通过 + 图片容器就绪
            self._goto_ready(chapter_tab, chapter_url, '.chapter-body img',
                             label=f'章节{chapter_num}阅读页')

            herf_list = self._collect_images_from_page(chapter_tab)
        except Exception as e:
            print(f"处理章节{chapter_num}时出错: {e}")
            herf_list = []
        finally:
            if chapter_tab:
                try:
                    chapter_tab.close()
                except Exception:
                    pass

        return {
            'chapter_num': chapter_num,
            'title': chapter_info.get('title', ''),
            'url': chapter_url,
            'herf_list': herf_list,
        }

    def _ensure_site_clear(self):
        """开跑前先过一次人机验证：主标签打开首页，等验证通过（失败则抛出明显报错）。

        通过后 cf_clearance 落在持久化配置目录，后续详情页/章节页/阅读页复用同一会话。
        """
        try:
            self.crawler.tab.get('https://mgread.io/', timeout=10)
        except Exception as e:
            print(f"打开首页失败: {e}")
        self._goto_ready(self.crawler.tab, 'https://mgread.io/',
                         'a[href*="/manga/"], .chapter-list', label='站点首页')

    def collect_chapters_images(self, target_comic_tab, chapter_start=1, chapter_end=0,
                                max_threads=3, progress_callback=None):
        print(f"设置最大同时收集线程数: {max_threads}")

        self._ensure_site_clear()

        entries = self._collect_chapter_entries(target_comic_tab)
        all_chapters_num = len(entries)
        print(f"总章节数: {all_chapters_num}")
        if all_chapters_num == 0:
            print("未找到任何章节链接")
            return []

        actual_start = max(chapter_start, 1)
        actual_end = min(chapter_end, all_chapters_num) if chapter_end > 0 else all_chapters_num
        if actual_start > all_chapters_num:
            print(f"起始章节 {actual_start} 超过总章节数 {all_chapters_num}")
            return []
        print(f"将下载第 {actual_start}-{actual_end} 章，共 {actual_end - actual_start + 1} 章")

        all_chapters_data = []
        current_chapter = actual_start
        while current_chapter <= actual_end:
            group_end = min(current_chapter + max_threads - 1, actual_end)
            print(f"\n处理章节范围: {current_chapter}-{group_end}")

            batch = []
            for num in range(current_chapter, group_end + 1):
                entry = entries[num - 1]
                batch.append({
                    'chapter_num': num,
                    'url': entry['url'],
                    'title': entry.get('title', ''),
                    'main_tab': self.crawler.tab,
                })
                print(f"准备处理第{num}章节: {entry['url']}")

            threads, results = [], []

            def wrapper(info):
                results.append(self.collect_chapter_images(info))

            for info in batch:
                t = threading.Thread(target=wrapper, args=(info,))
                threads.append(t)
                t.start()
            for t in threads:
                t.join()

            all_chapters_data.extend(results)
            for _ in results:
                if progress_callback:
                    progress_callback()
            current_chapter = group_end + 1

        return all_chapters_data

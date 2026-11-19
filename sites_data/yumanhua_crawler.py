import time
import threading
import re
import random
from urllib.parse import quote

from utils import is_normal_url

# 通知类章节标题特征（休刊/公告/延期等，非真实漫画章节）
_NOTICE_TITLE_RE = re.compile(r'休刊|公告|通知|请假|延迟|延期|抱歉|停更|休更|双更|更新|预告')

# 真章节标题样式：「第76话 标题」/「第12章 标题」/「总697·标题」/「697·标题」
# 注意：旧版只认「数字/总N」开头，会把漫画客的「第76话 …」整段最新章节当成非章节丢掉，
# 导致只加载到 /morechapter 的旧章节（实测 74 章 vs 实际 94 章）。
_CHAPTER_TEXT_RE = re.compile(r'^(?:第\s*\d+\s*[话章]|(?:总\s*)?\d)')
# 章节号提取（用于 num 与站点一致，标题里去掉「第N话」前缀避免 GUI 重复编号）
_CHAPTER_NO_RE = re.compile(r'^第\s*(\d+)\s*[话章]\s*')

# ===== 漫画客节奏（由设置页『漫画客节奏』控制，v1.6.8） =====
# 默认关闭批次限速 = 与其他站点一致，完全跟随全局设置（模拟人工开关 / 多线程高速下载开关）。
# 开启后使用下面的批次档：每下 10 张整批停 2.5~5 分钟，章节之间停 5~10 分钟，单章 2 线程/2 并发。
# 开关值来自 config.YUMANHUA_BATCH_LIMIT（由 GUI 写入，存 config.json）。
_BATCH_PROFILE = dict(cc=1, threads=2, conc=2, delay=0.0, every=10, lo=150.0, hi=300.0, chlo=300.0, chhi=600.0)
_LIMIT_KEYS = (
    'download_threads', 'download_concurrency', 'download_delay',
    'download_pause_every', 'download_pause_extra_min', 'download_pause_extra_max',
    'chapter_pause_min', 'chapter_pause_max',
)


class YumanhuaCrawler:
    """漫画客(yumanhua.com)爬虫 —— 已按源码项目规范重写（2026-09 重新解析核验）

    站点结构（重新实测确认）：
      - 站内搜索是 AJAX：POST /s ，表单参数 k=书名
        返回 JSON：{"code":"200","data":[{"id":"漫画token","imgurl":"封面","name":"书名","remarks":"最新话"}]}
        => 漫画详情页 URL = http://yumanhua.com/{id}/
      - 完整章节列表是 AJAX：POST /morechapter ，参数 id=漫画token
        返回 JSON：{"data":[{"chapterid":"..","chaptername":".."}, ...]}，从新到旧
        => 章节阅读页 URL = http://yumanhua.com/{漫画id}/{章节id}.html
      - 章节页/详情页为"下拉式"，图片滚动懒加载：初始 src 是 /static/images/load.gif 占位，
        滚动后才把真实地址写入 img 的 data-src（真实图在 ecombdimg.com / shimolife.com 等多个 CDN）。
        必须先滚动到底触发懒加载，再统一读 data-src。
      - 真实图片 URL 是 http(s) 明文（非 blob/data:），所以下载走 HTTP 通道（非浏览器渲染）。
        但 CDN 可能按 Referer/UA 防盗链，因此 CONFIG 声明 image_referer=http://yumanhua.com/，
        下载器会自动带上 Referer。
    """

    # 站点元数据
    SITE_NAME = '漫画客'
    SITE_URL = 'http://yumanhua.com/'
    REQUIRES_LOGIN = False
    # 搜索/章节列表依赖页面内 fetch 取 id，需要浏览器；图片本身走 HTTP 下载
    NEEDS_BROWSER = True

    # 站点配置
    CONFIG = {
        'site_url': 'http://yumanhua.com/',
        'locators': {
            # 详情页封面：位于 div.book-cover > div.thumbnail > img（已实测）
            'cover_image': 'xpath://div[contains(@class,"book-cover")]//img',
            # 章节列表走 /morechapter 接口，不再依赖 DOM（见 get_chapter_count）
            'chapter_item': 'xpath://div[contains(@class,"book-cover")]//img',
        },
        'image_attr': 'data-src',
        'chapter_group_size': None,
        # 图片在 ecombdimg.com / shimolife.com 等 CDN，与站点不同域，带 Referer 防防盗链
        'image_referer': 'http://yumanhua.com/',
        # 站点限速：默认不加（与其他站点一致）；
        # 设置页勾选『漫画客批次限速』后，才由 _apply_batch_limit() 写入慢速档参数
        'chapter_concurrency': 0,
    }

    def _apply_batch_limit(self):
        """按设置页的『漫画客节奏』开关，写入/移除本站限速参数。

        关闭（默认）：不覆盖任何下载参数，完全跟随全局（模拟人工 / 高速下载开关）。
        开启：每 10 张整批停 2.5~5 分钟、章间停 5~10 分钟、单章 2 线程/2 并发、章节串行。
        """
        try:
            import config
            batch = bool(getattr(config, 'YUMANHUA_BATCH_LIMIT', False))
        except Exception:
            batch = False
        cfg = self.CONFIG
        if batch:
            cfg.update({
                'chapter_concurrency': _BATCH_PROFILE['cc'],
                'download_threads': _BATCH_PROFILE['threads'],
                'download_concurrency': _BATCH_PROFILE['conc'],
                'download_delay': _BATCH_PROFILE['delay'],
                'download_pause_every': _BATCH_PROFILE['every'],
                'download_pause_extra_min': _BATCH_PROFILE['lo'],
                'download_pause_extra_max': _BATCH_PROFILE['hi'],
                'chapter_pause_min': _BATCH_PROFILE['chlo'],
                'chapter_pause_max': _BATCH_PROFILE['chhi'],
            })
            print("漫画客节奏：批次限速（每 10 张整批停 2.5~5 分钟，章间 5~10 分钟）")
        else:
            for key in _LIMIT_KEYS:
                cfg.pop(key, None)
            cfg['chapter_concurrency'] = 0
            print("漫画客节奏：跟随全局设置（模拟人工/高速下载开关）")
        return batch

    def __init__(self, crawler):
        self.crawler = crawler
        self.locators = crawler.locators
        self.image_attr = crawler.image_attr
        # 缓存：{comic_id: [chapter_dict, ...]}，避免多次 get_chapter_count 重复请求
        self._chapters_cache = {}
        # 搜索阶段捕获到的封面 URL（search 结果自带 imgurl，直接复用）
        self._cover_url = None
        # 按设置页开关决定本站节奏（批次限速 / 跟随全局）
        self._apply_batch_limit()

    # ---------- 搜索（本站为 AJAX POST /s，非可导航 URL） ----------
    def _js_fetch_to_window(self, tab, path, body, var):
        """在指定标签页上下文里发 POST fetch，把结果 JSON 存到 window[var]。"""
        js = (
            f"fetch('{self.SITE_URL}{path}',{{method:'POST',"
            "headers:{'Content-Type':'application/x-www-form-urlencoded'},"
            f"body:'{body}'}})"
            f".then(r=>r.text()).then(t=>{{window['{var}']=(JSON.parse(t).data||[])}})"
        )
        return tab.run_js(js)

    def _wait_window(self, tab, var, timeout=6.0):
        """轮询读取指定标签页的 window[var]，直到非空/超时。"""
        waited = 0.0
        while waited < timeout:
            val = tab.run_js(f'return window.{var}')
            if val:
                return val
            time.sleep(0.5)
            waited += 0.5
        return None

    def search_comic(self, comic_name, comic_id=None):
        """优先用 comic_id 直接打开详情页；否则 POST /s 搜到 id 再打开。"""
        if comic_id:
            detail_url = f"{self.SITE_URL}{comic_id}/"
            print(f"按ID打开详情页: {detail_url}")
            target_comic_tab = self.crawler.page.new_tab(detail_url)
            time.sleep(3)
            return target_comic_tab

        payload = quote(comic_name)
        try:
            self.crawler.tab.get(self.SITE_URL)
            time.sleep(2)
            self._js_fetch_to_window(self.crawler.tab, 's', f'k={payload}', '__yhu')
            found = self._wait_window(self.crawler.tab, '__yhu')
            found = (found or [])[0] if isinstance(found, list) else (found or None)
            if found and found.get('id'):
                comic_id = found['id']
                self._cover_url = found.get('imgurl') or None
                print(f"搜索到: {found.get('name')} id={comic_id} 封面={'有' if self._cover_url else '无'}")
        except Exception as e:
            print(f"接口搜索失败: {e}")

        if not comic_id:
            raise Exception(f"搜索'{comic_name}'失败，未取到漫画 id")

        detail_url = f"{self.SITE_URL}{comic_id}/"
        print(f"打开详情页: {detail_url}")
        target_comic_tab = self.crawler.page.new_tab(detail_url)
        time.sleep(3)
        return target_comic_tab

    def get_cover_image(self, target_comic_tab):
        """封面：优先搜索阶段捕获的 imgurl；否则从详情页 div.book-cover 取。"""
        if self._cover_url:
            print(f"封面图片URL: {self._cover_url}")
            return self._cover_url
        try:
            img_ele = target_comic_tab.ele(self.locators['cover_image'], timeout=15)
            cover_url = img_ele.attr('src') or img_ele.attr('data-src')
            if cover_url and not cover_url.startswith('/static'):
                print(f"封面图片URL: {cover_url}")
                return cover_url
        except Exception as e:
            print(f"获取封面图片失败: {e}")
        return None

    # ---------- 章节列表（完整列表走 POST /morechapter，返回新->旧） ----------
    def _extract_comic_id(self, target_comic_tab):
        import re
        try:
            url = target_comic_tab.url
            m = re.search(r'yumanhua\.com/([^/]+)/?$', url)
            return m.group(1) if m else None
        except Exception:
            return None

    def _fetch_all_chapters(self, target_comic_tab):
        """合并 详情页DOM最新章节 + /morechapter 旧章节，去重并过滤通知，得到完整章节表（带缓存）。

        实测（2026-09）：漫画客的章节列表拆成两部分——
          - 详情页「章节列表」区域只渲染最新一截（如 总697→总680，共 ~18 条），底部有"更多话"按钮；
          - POST /morechapter 返回的是更早的旧章节（如 总679→001）。
        若只用接口会漏掉最新章节；若只用 DOM 会漏掉旧章节。因此两者合并。

        实测（2026-11-13，戏精妈咪在线Battle/TRETTcf）：本站标题格式是「第95话 标题」，
        旧版 DOM 过滤正则只认「数字/总N」开头，把最新一截（第76~95话）全丢掉，只剩接口 75 条
        （含 1 条“预告”被过滤）= 74 章，用户报“章节数量少了”。现改用 _CHAPTER_TEXT_RE 兼容「第N话」，
        实得 94 章（第1~95话，站点自身缺第56话）。
        """
        comic_id = self._extract_comic_id(target_comic_tab)
        if not comic_id:
            return []

        cached = self._chapters_cache.get(comic_id)
        if cached is not None:
            return cached

        def _clean(entries):
            """过滤空/通知类条目，返回 [{'chapterid','chaptername'}]"""
            out = []
            for c in entries:
                cid = (c or {}).get('chapterid')
                name = (c or {}).get('chaptername') or (c or {}).get('title') or ''
                if not cid:
                    continue
                name = name.strip()
                # 通知类过滤只针对「无章节号」的条目（如“预告/休刊公告”），
                # 标题里恰好含“更新/预告”但带「第N话」的真章节要保留
                if _NOTICE_TITLE_RE.search(name) and not _CHAPTER_NO_RE.match(name):
                    continue
                out.append({'chapterid': cid, 'chaptername': name})
            return out

        # 1) 详情页 DOM：最新章节（新->旧）
        dom_chapters = []
        try:
            js = (
                "var sec=Array.from(document.querySelectorAll('div,section,ul'))"
                ".filter(e=>e.textContent.indexOf('章节列表')>-1 && e.textContent.indexOf('更多话')>-1);"
                "var root=sec.length?sec[0]:document;"
                "return Array.from(root.querySelectorAll('a'))"
                f".filter(a=>a.href.indexOf('/{comic_id}/')>-1 && /\\.html$/.test(a.href))"
                ".map(a=>({href:a.href,text:(a.textContent||'').trim()}));"
            )
            raw = target_comic_tab.run_js(js) or []
            for item in raw:
                href = (item or {}).get('href') or ''
                text = (item or {}).get('text') or ''
                chid = href.rstrip('/').split('/')[-1].replace('.html', '')
                # 只收“第N话 … / 总N·标题 / N·标题”形式的真章节，排除“开始阅读”等按钮链接
                if chid and _CHAPTER_TEXT_RE.match(text.strip()):
                    dom_chapters.append({'chapterid': chid, 'chaptername': text})
        except Exception as e:
            print(f"解析详情页章节列表失败: {e}")

        # 2) /morechapter 接口：旧章节（新->旧）
        api_chapters = []
        try:
            self._js_fetch_to_window(target_comic_tab, 'morechapter', f'id={comic_id}', '__ch')
            data = self._wait_window(target_comic_tab, '__ch') or []
            api_chapters = [{'chapterid': c.get('chapterid'), 'chaptername': c.get('chaptername', '')} for c in data]
        except Exception as e:
            print(f"获取章节列表失败: {e}")

        # 3) 合并：DOM(最新) 在前 + 接口(旧) 在后，按 chapterid 去重，过滤通知
        seen = set()
        merged = []
        for ch in _clean(dom_chapters) + _clean(api_chapters):
            cid = ch['chapterid']
            if cid not in seen:
                seen.add(cid)
                merged.append(ch)

        # 4) 反转成 旧->新，num 必须连续 1..N（collect_chapters_images 用 num-1 取列表下标，
        #    站内编号会跳号如缺第56话，不能直接用站点编号当 num）；
        #    标题保留站内「第N话 …」原文，站点真实编号仍在标题里可见
        chapters = []
        for i, ch in enumerate(reversed(merged), 1):
            title = (ch.get('chaptername') or '').strip() or f"第{i}话"
            chapters.append({
                'num': i,
                'url': f"{self.SITE_URL}{comic_id}/{ch['chapterid']}.html",
                'title': title,
            })
        self._chapters_cache[comic_id] = chapters
        print(f"共获取 {len(chapters)} 个章节URL（详情页最新+接口旧章合并，通知已过滤）")
        return chapters

    def get_chapter_count(self, target_comic_tab):
        chs = self._fetch_all_chapters(target_comic_tab)
        print(f"检测到 {len(chs)} 个章节")
        return len(chs)

    def get_chapter_list(self, target_comic_tab):
        """返回完整章节表（含每章标题）：[{num, url, title}, ...]，供GUI显示章节名"""
        return self._fetch_all_chapters(target_comic_tab)

    # ---------- 章节图片（下拉式，需滚动触发懒加载） ----------
    def _human_scroll(self, chapter_tab):
        """本站专用：滚动触发懒加载。

        - 跟随全局设置且模拟人工关闭 / 命中多线程高速下载：直接分段快速到底（只为触发懒加载）
        - 模拟人工开启：一屏一屏慢滚，但把停顿压到 0.1~0.25s，减少空等
        """
        try:
            import config as _cfg
            fast = bool(getattr(_cfg, 'FAST_THREAD', False)) or \
                   (not bool(getattr(_cfg, 'HUMAN_MODE', True)))
        except Exception:
            fast = False
        try:
            vh = int(chapter_tab.run_js('return window.innerHeight') or 900)
        except Exception:
            vh = 900
        if vh <= 0:
            vh = 900
        try:
            total = int(chapter_tab.run_js('return document.body.scrollHeight') or 0)
        except Exception:
            total = 0

        start_time = time.time()
        pos = 0
        screens = 0
        no_grow = 0

        if fast:
            # 快速档：3 屏一步滑到底，只要懒加载被触发
            max_screens = 30
            while pos < total and screens < max_screens:
                pos = min(pos + int(vh * 3), total)
                try:
                    chapter_tab.run_js(f'window.scrollTo(0, {pos})')
                except Exception:
                    break
                time.sleep(0.05)
                screens += 1
                try:
                    h = int(chapter_tab.run_js('return document.body.scrollHeight') or total)
                except Exception:
                    h = total
                if h > total:
                    total = h
                if screens >= 3 and pos >= total:
                    break
            print(f"快速翻页完成：{screens} 屏，耗时 {time.time() - start_time:.1f}s")
        else:
            max_screens = 260
            while pos < total and screens < max_screens:
                # 每屏滚动 1.2~1.8 倍视口高度
                pos = min(pos + int(vh * random.uniform(1.2, 1.8)), total)
                try:
                    chapter_tab.run_js(f'window.scrollTo(0, {pos})')
                except Exception as e:
                    print(f"滚动失败: {e}")
                    break
                time.sleep(random.uniform(0.1, 0.25))
                screens += 1

                # 每 30~45 屏回翻一点，模拟来回浏览
                if screens % random.randint(30, 45) == 0 and pos > vh:
                    back = max(0, pos - int(vh * random.uniform(0.2, 0.4)))
                    try:
                        chapter_tab.run_js(f'window.scrollTo(0, {back})')
                        time.sleep(random.uniform(0.2, 0.4))
                    except Exception:
                        pass
                    pos = back

                # 每 30~45 屏多停一次（等懒加载）
                if screens % random.randint(30, 45) == 0:
                    time.sleep(random.uniform(1.0, 2.0))

                # 页面可能继续变长（懒加载），同步一次
                try:
                    h = int(chapter_tab.run_js('return document.body.scrollHeight') or total)
                except Exception:
                    h = total
                if h <= total:
                    no_grow += 1
                else:
                    total = h
                    no_grow = 0
                if no_grow >= 2 and pos >= total:
                    break
            print(f"模拟真人翻页完成：{screens} 屏，耗时 {time.time() - start_time:.1f}s")

        # 到底就立即结束：不再额外停等，采集完马上关标签
        try:
            chapter_tab.run_js('window.scrollTo(0, document.body.scrollHeight)')
        except Exception:
            pass
        time.sleep(0.1)

    def get_chapter_image_urls(self, chapter_tab):
        """像真人翻页一样滚动，把懒加载图片 data-src 刷出来，再收集真实漫画页 URL。

        真漫画页都放在 .chapter-img-box 容器内（CDN 为 *.shimolife.com）；
        页面底部的“猜你喜欢/相关推荐”封面（ecombdimg.com）和 UI 图标（/static/）
        都在容器外，因此只收集容器内的图，避免混入无关图片。
        """
        herf_list = []

        self._human_scroll(chapter_tab)

        # 兜底：如果翻页后仍有图片没触发懒加载，再做一次到底确认（不再长时间循环）
        try:
            if int(chapter_tab.run_js("return document.querySelectorAll('.chapter-img-box img[data-src], .chapter-img-box img[src]').length") or 0) == 0:
                chapter_tab.run_js('window.scrollTo(0, document.body.scrollHeight)')
                time.sleep(1.5)
        except Exception:
            pass

        try:
            urls = chapter_tab.run_js(
                "return Array.from(document.querySelectorAll('.chapter-img-box img'))"
                ".map(function(i){var d=i.getAttribute('data-src')||'';var s=i.getAttribute('src')||'';return d||s;})"
                ".filter(function(u){return u && u.indexOf('http')===0 && u.indexOf('load.gif')<0;})"
            ) or []
            if not urls:
                # 兜底：万一某章容器类名不同，退回收集全部非占位/非图标图
                print("章节容器 .chapter-img-box 未找到，退回通用收集（可能含推荐图）")
                urls = chapter_tab.run_js(
                    "return Array.from(document.querySelectorAll('img'))"
                    ".map(function(i){var d=i.getAttribute('data-src')||'';var s=i.getAttribute('src')||'';return d||s;})"
                    ".filter(function(u){return u && u.indexOf('http')===0 && u.indexOf('load.gif')<0 && u.indexOf('/static/')<0;})"
                ) or []
            for u in urls:
                if is_normal_url(u):
                    herf_list.append(u)
            print(f"共提取 {len(herf_list)} 张图片")
        except Exception as e:
            print(f"提取图片URL失败: {e}")

        return herf_list

    def collect_chapter_images(self, chapter_info, max_wait_time=5):
        chapter_num = chapter_info['chapter_num']
        chapter_url = chapter_info['url']
        main_tab = chapter_info['main_tab']
        print(f"正在处理章节{chapter_num}: {chapter_url}")

        # 并行采集时，标签页由主线程统一创建/关闭（跨线程 new_tab/close 容易卡死）
        chapter_tab = chapter_info.get('tab')
        owns_tab = chapter_tab is None
        try:
            if chapter_tab is None:
                chapter_tab = main_tab.new_tab(chapter_url)
                time.sleep(1.0)

            retry_count = 0
            max_retries = 3
            while retry_count <= max_retries:
                try:
                    n = chapter_tab.run_js("return document.querySelectorAll('img').length") or 0
                    if int(n) > 0:
                        break
                except Exception:
                    pass
                if retry_count < max_retries:
                    retry_count += 1
                    print(f"章节{chapter_num} 未检测到图片，第{retry_count}次重新加载...")
                    chapter_tab.get(chapter_url)
                    time.sleep(1.0)
                else:
                    print(f"章节{chapter_num} 已达最大重试次数({max_retries})")
                    return {
                        'chapter_num': chapter_num,
                        'title': chapter_info.get('title', ''),
                        'herf_list': [],
                    }

            herf_list = self.get_chapter_image_urls(chapter_tab)

        except Exception as e:
            print(f"处理章节{chapter_num}时出错: {e}")
            herf_list = []
        finally:
            # 自己创建的标签才由自己关；并行模式由主线程统一关，避免跨线程关页卡死
            if owns_tab and chapter_tab is not None:
                try:
                    chapter_tab.close()
                except Exception:
                    pass

        return {
            'chapter_num': chapter_num,
            'title': chapter_info.get('title', ''),
            'herf_list': herf_list,
        }

    def collect_chapters_images(self, target_comic_tab, chapter_start=1, chapter_end=0,
                                max_threads=3, progress_callback=None):
        # 设置页可能刚改过节奏，这里再同步一次
        self._apply_batch_limit()
        # 站点限速：仅批次限速模式下把同时处理的章节数压到 CONFIG.chapter_concurrency；
        # 跟随全局模式下不限（交给全局『模拟人工/高速下载』开关与 GUI 的标签页数）
        try:
            import config as _cfg
            follow_global = (not bool(getattr(_cfg, 'YUMANHUA_BATCH_LIMIT', False))) or \
                            (not bool(getattr(_cfg, 'HUMAN_MODE', True)))
        except Exception:
            follow_global = True
        cap = 0 if follow_global else int((self.CONFIG or {}).get('chapter_concurrency') or 0)
        if cap and max_threads > cap:
            print(f"章节并发按站点限速下调: {max_threads} -> {cap}")
            max_threads = cap
        print(f"设置最大同时收集线程数: {max_threads}")

        chapter_urls = self._fetch_all_chapters(target_comic_tab)
        all_chapters_num = len(chapter_urls)
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
                batch.append({
                    'chapter_num': num,
                    'url': chapter_urls[num - 1]['url'],
                    'title': chapter_urls[num - 1].get('title', ''),
                    'main_tab': self.crawler.tab,
                })
                print(f"准备处理第{num}章节: {chapter_urls[num - 1]['url']}")

            # 并行：标签页在本线程（主线程）连续快速打开（不逐个等待），
            # 工作线程用 Barrier 同步起跑、只操作自己的标签页，采完再一次性全部关闭。
            # DrissionPage 在同一线程里 new_tab/close 才稳，跨线程建页/关页是之前卡住的根因。
            open_tabs = []
            if len(batch) > 1:
                for info in batch:
                    try:
                        t = self.crawler.page.new_tab(info['url'])
                        info['tab'] = t
                        open_tabs.append(t)
                    except Exception as e:
                        print(f"章节{info['chapter_num']} 预开标签失败，改为串行处理: {e}")
                        info['tab'] = None
                if open_tabs:
                    # 等页面出图（最多 1.2s）：出图就立刻开跑，不再固定死等 2 秒
                    deadline = time.time() + 1.2
                    while time.time() < deadline:
                        ready = True
                        for _t in open_tabs:
                            try:
                                n = int(_t.run_js(
                                    "return document.querySelectorAll('.chapter-img-box img, img').length"
                                ) or 0)
                            except Exception:
                                n = 0
                            if n <= 0:
                                ready = False
                                break
                        if ready:
                            break
                        time.sleep(0.15)

            threads, results = [], []
            barrier = threading.Barrier(len(batch)) if open_tabs and len(batch) == len(open_tabs) else None
            def wrapper(info):
                if barrier is not None:
                    try:
                        barrier.wait(timeout=60)   # 三页同时开翻
                    except Exception:
                        pass
                try:
                    results.append(self.collect_chapter_images(info))
                finally:
                    info['done'] = True

            starts = {}
            for info in batch:
                info['done'] = False
                info['closed'] = False
                t = threading.Thread(target=wrapper, args=(info,))
                threads.append(t)
                starts[info['chapter_num']] = time.time()
                t.start()

            # 每章一滑到底就立刻关掉它自己的标签页（不用等其它章节），
            # 单章超过 300 秒则强制关闭，避免“一直不动、旧窗口也不关”的死等。
            while any(t.is_alive() for t in threads):
                for info in batch:
                    tab = info.get('tab')
                    if info.get('closed') or tab is None:
                        continue
                    over_time = time.time() - starts[info['chapter_num']] > 300
                    if info.get('done') or over_time:
                        if over_time and not info.get('done'):
                            print(f"章节{info['chapter_num']} 超过300秒未完成，强制关闭标签页")
                        # 主线程按 id 批量关（比单页 close 快，且不会卡在页面 unload）
                        try:
                            tid = tab.tab_id
                            self.crawler.page.close_tabs([tid])
                        except Exception:
                            try:
                                tab.close()
                            except Exception:
                                pass
                        info['closed'] = True
                        if info.get('done'):
                            print(f"章节{info['chapter_num']} 采集完成，已关闭标签页")
                time.sleep(0.2)

            # 兜底：只收尾还没关掉的标签页（已关的不再重复 close，二次 close 会白等）
            left_ids = []
            for info in batch:
                if info.get('closed'):
                    continue
                tab = info.get('tab')
                if tab is None:
                    continue
                try:
                    left_ids.append(tab.tab_id)
                except Exception:
                    pass
                info['closed'] = True
            if left_ids:
                try:
                    self.crawler.page.close_tabs(left_ids)
                except Exception:
                    pass

            all_chapters_data.extend(results)
            for _ in results:
                if progress_callback:
                    progress_callback()
            current_chapter = group_end + 1

        return all_chapters_data

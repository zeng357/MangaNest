import time
import threading
import re
from urllib.parse import quote

from utils import is_normal_url

# 通知类章节标题特征（休刊/公告/延期等，非真实漫画章节）
_NOTICE_TITLE_RE = re.compile(r'休刊|公告|通知|请假|延迟|延期|抱歉|停更|休更|双更|更新|预告')


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
    }

    def __init__(self, crawler):
        self.crawler = crawler
        self.locators = crawler.locators
        self.image_attr = crawler.image_attr
        # 缓存：{comic_id: [chapter_dict, ...]}，避免多次 get_chapter_count 重复请求
        self._chapters_cache = {}
        # 搜索阶段捕获到的封面 URL（search 结果自带 imgurl，直接复用）
        self._cover_url = None

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
                if _NOTICE_TITLE_RE.search(name):
                    continue
                out.append({'chapterid': cid, 'chaptername': name.strip()})
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
                # 只收“总N·标题 / N·标题”形式的真章节，排除“开始阅读”等按钮链接
                if chid and re.match(r'^(总\s*)?\d', text.strip()):
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

        # 4) 反转成 旧->新，num=1 对应最早一话
        chapters = []
        for i, ch in enumerate(reversed(merged), 1):
            chapters.append({
                'num': i,
                'url': f"{self.SITE_URL}{comic_id}/{ch['chapterid']}.html",
                'title': ch.get('chaptername') or f"第{i}话",
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
    def get_chapter_image_urls(self, chapter_tab):
        """先滚动到底把懒加载图片 data-src 刷出来，再收集真实漫画页 URL。

        真漫画页都放在 .chapter-img-box 容器内（CDN 为 *.shimolife.com）；
        页面底部的“猜你喜欢/相关推荐”封面（ecombdimg.com）和 UI 图标（/static/）
        都在容器外，因此只收集容器内的图，避免混入无关图片。
        """
        herf_list = []

        try:
            last_h = -1
            for _ in range(60):
                h = chapter_tab.run_js('return document.body.scrollHeight') or 0
                chapter_tab.run_js('window.scrollTo(0, document.body.scrollHeight)')
                time.sleep(0.4)
                if h == last_h:
                    time.sleep(1.2)
                    h2 = chapter_tab.run_js('return document.body.scrollHeight') or 0
                    if h2 == h:
                        break
                    last_h = h2
                else:
                    last_h = h
        except Exception as e:
            print(f"滚动触发懒加载出错: {e}")

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

        try:
            chapter_tab = main_tab.new_tab(chapter_url)
            time.sleep(2)

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
                    time.sleep(2)
                else:
                    print(f"章节{chapter_num} 已达最大重试次数({max_retries})")
                    chapter_tab.close()
                    return {
                        'chapter_num': chapter_num,
                        'title': chapter_info.get('title', ''),
                        'herf_list': [],
                    }

            herf_list = self.get_chapter_image_urls(chapter_tab)
            chapter_tab.close()

        except Exception as e:
            print(f"处理章节{chapter_num}时出错: {e}")
            herf_list = []

        return {
            'chapter_num': chapter_num,
            'title': chapter_info.get('title', ''),
            'herf_list': herf_list,
        }

    def collect_chapters_images(self, target_comic_tab, chapter_start=1, chapter_end=0,
                                max_threads=3, progress_callback=None):
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

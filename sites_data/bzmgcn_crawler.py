import time
import re
import threading
from urllib.parse import quote, urlparse, parse_qs

from utils import is_normal_url

# 通知类章节标题特征（休刊/公告/延期等，非真实漫画章节）
_NOTICE_TITLE_RE = re.compile(r'休刊|公告|通知|请假|延迟|延期|抱歉|停更|休更|双更|预告')


class BzmgcnCrawler:
    """包子漫画CN (cn.bzmgcn.com) 爬虫 —— 2026-11 实测解析

    站点结构（实测确认）：
      - 主域 https://cn.bzmgcn.com/ ，受 __gatekeeper_challenge JS 挑战门保护，
        纯 HTTP(requests) 直连返回 403，必须用真实浏览器过闸，故 NEEDS_BROWSER=True。
      - 站内搜索：GET /search?q=书名 ，结果为 a[href*="/comic/"] 静态链接；
        同一漫画会有“分类标签”与“书名”两条同 href 记录，取文本最长者（书名）。
        详情页 URL = https://cn.bzmgcn.com/comic/{slug}
      - 章节列表：详情页 a.comics-chapters__item，href 形如
        /user/page_direct?comic_id={slug}&section_slot={s}&chapter_slot={c}
        列表含首尾重复项，需按 (section_slot, chapter_slot) 去重，chapter_slot 升序（旧→新）。
      - 阅读页：page_direct 自动 302 到阅读域 https://cn.cnbzmg.com/comic/chapter/{slug}/{s}_{c}.html；
        阅读域间歇弹挑战门/错误页（标题 cn.cnbzmg.com、body 高度 463、容器 0），需多次刷新过闸。
      - 章节图片：容器 .comic-contain__item 内 img / amp-img，真实图在 src 或 data-src；
        真漫画图 CDN 为 *.bzcdn.net/scomic/；页面底部”猜你喜欢”封面在 static-tw.bzmgcn.com/cover/，
        APP 下载栏图在 /bzmhapp-data/，静态图标在 /static/，均需过滤。
      - 图片为下拉懒加载：优先读前端变量 REQ_FILES，否则分段滚动触发后从 DOM 收集，
        再以整页 HTML 正则兜底；最后按文件名页码升序、去缓存戳、去重。

    法律提示：本插件仅用于 Python 自动化学习研究，禁止用于抓取、保存、传播受版权保护的漫画资源，
    请遵守目标站点用户协议与著作权相关法律法规。
    """

    # 站点元数据
    SITE_NAME = '包子漫画CN'
    SITE_URL = 'https://cn.bzmgcn.com/'
    REQUIRES_LOGIN = False
    # 挑战门 + 章节/图片均需浏览器上下文
    NEEDS_BROWSER = True

    # 站点配置
    CONFIG = {
        'site_url': 'https://cn.bzmgcn.com/',
        'locators': {
            # 封面优先读 og:image 元信息（页面用 name 属性），兜底在 get_cover_image 内用 JS 取
            'cover_image': "xpath://meta[@name='og:image']",
        },
        'image_attr': 'src',
        'chapter_group_size': None,
        # CDN 与站点不同域，下载时带上主域 Referer 防防盗链
        'image_referer': 'https://cn.bzmgcn.com/',
        'download_mode': 'thread_only',
    }

    def __init__(self, crawler):
        self.crawler = crawler
        self.locators = crawler.locators
        self.image_attr = crawler.image_attr
        # 章节表缓存：[{url, title}]，避免多次 get_chapter_count 重复解析
        self._chapter_entries = None
        # 搜索/封面阶段捕获到的封面 URL
        self._cover_url = None

    # ---------- 搜索 ----------
    def search_comic(self, comic_name, comic_id=None):
        """优先按 comic_id 直接打开详情页；否则访问 /search?q= 取首个有效结果。"""
        if comic_id:
            detail_url = f"https://cn.bzmgcn.com/comic/{comic_id}/"
            print(f"按ID打开详情页: {detail_url}")
            target_comic_tab = self.crawler.page.new_tab(detail_url)
            time.sleep(3)
            return target_comic_tab

        search_url = f"https://cn.bzmgcn.com/search?q={quote(comic_name)}"
        print(f"正在搜索漫画: {comic_name}")
        print(f"搜索URL: {search_url}")

        self.crawler.tab.get(search_url)
        time.sleep(4)

        js = (
            "return Array.from(document.querySelectorAll('a[href*=\"/comic/\"]'))"
            ".map(a=>({href:a.getAttribute('href'), text:(a.innerText||'').trim()}))"
            ".filter(x=>x.href && x.href!=='/comic/sitemap' && x.text);"
        )
        items = self.crawler.tab.run_js(js) or []

        # 同一 href 可能对应“分类标签”和“书名”两条，保留文本最长（书名）那条
        by_href = {}
        for it in items:
            href = it['href']
            if href not in by_href or len(it['text']) > len(by_href[href]['text']):
                by_href[href] = it

        if not by_href:
            raise Exception(f"搜索'{comic_name}'失败，未取到漫画链接")

        chosen_href = next(iter(by_href.keys()))
        chosen_text = by_href[chosen_href]['text'].replace('\n', ' ')
        if not chosen_href.startswith('http'):
            chosen_href = 'https://cn.bzmgcn.com' + chosen_href
        print(f"搜索到: {chosen_text} -> {chosen_href}")

        target_comic_tab = self.crawler.page.new_tab(chosen_href)
        time.sleep(3)
        return target_comic_tab

    def get_cover_image(self, target_comic_tab):
        """封面：优先 og:image（页面用 name 属性而非 property）；其次按 slug 拼封面；
        最后兜底取详情页非推荐小图的封面 img。"""
        if self._cover_url:
            print(f"封面图片URL: {self._cover_url}")
            return self._cover_url
        try:
            # 页面 meta 为 name="og:image"（无 property 属性），两种都兼容
            og = target_comic_tab.run_js(
                "const m=document.querySelector(\"meta[name='og:image'], meta[property='og:image']\");"
                "return m ? m.content : '';"
            )
            if og and is_normal_url(og):
                self._cover_url = og
                print(f"封面图片URL: {og}")
                return og
        except Exception as e:
            print(f"读取 og:image 失败: {e}")

        # 兜底1：按详情页 slug 拼静态封面
        try:
            m = re.search(r'/comic/([^/?#]+)', target_comic_tab.url or '')
            if m:
                slug = m.group(1)
                guess = f"https://static-tw.bzmgcn.com/cover/{slug}.jpg"
                if is_normal_url(guess):
                    self._cover_url = guess
                    print(f"封面图片URL(按slug拼接): {guess}")
                    return guess
        except Exception:
            pass

        # 兜底2：详情页封面大图（排除 w=90 推荐小图）
        try:
            js = (
                "const imgs=Array.from(document.querySelectorAll('img'));"
                "const c=imgs.find(i=>{const s=i.getAttribute('src')||'';"
                "return s.indexOf('/cover/')>-1 && s.indexOf('w=90')===-1;});"
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
    def _collect_chapter_entries(self, target_comic_tab):
        """从详情页 a.comics-chapters__item 提取去重后的章节表（旧→新）。

        返回 [{url, title}]，按 (section_slot, chapter_slot) 升序，num=1 对应最早一话。
        """
        if self._chapter_entries is not None:
            return self._chapter_entries

        entries = []
        try:
            js = (
                "return Array.from(document.querySelectorAll('a.comics-chapters__item'))"
                ".map(a=>({href:a.getAttribute('href'), text:(a.innerText||'').trim()}));"
            )
            raw = target_comic_tab.run_js(js) or []

            by_key = {}
            for item in raw:
                href = item.get('href') or ''
                m = re.search(r'section_slot=(\d+).*?chapter_slot=(\d+)', href)
                if not m:
                    continue
                key = (int(m.group(1)), int(m.group(2)))
                title = ' '.join((item.get('text') or '').split())
                if _NOTICE_TITLE_RE.search(title):
                    continue
                if key not in by_key:
                    by_key[key] = {'url': href, 'title': title}

            ordered = [by_key[k] for k in sorted(by_key)]
            for e in ordered:
                if not e['url'].startswith('http'):
                    e['url'] = 'https://cn.bzmgcn.com' + e['url']
            entries = ordered
            print(f"章节列表: {len(entries)} 个唯一章节（通知类已过滤）")
        except Exception as e:
            print(f"获取章节列表失败: {e}")

        self._chapter_entries = entries
        return entries

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

    # ---------- 章节图片（下拉懒加载） ----------
    @staticmethod
    def _to_reader_url(chapter_url):
        """把主域 page_direct 链接解析成阅读域直链，绕过 302 跳转链路。

        形如 /user/page_direct?comic_id={slug}&section_slot={s}&chapter_slot={c}
        -> https://cn.cnbzmg.com/comic/chapter/{slug}/{s}_{c}.html
        解析失败则原样返回。
        """
        try:
            q = parse_qs(urlparse(chapter_url).query)
            slug = (q.get('comic_id') or [''])[0]
            s = (q.get('section_slot') or [''])[0]
            c = (q.get('chapter_slot') or [''])[0]
            if slug and s and c:
                return f"https://cn.cnbzmg.com/comic/chapter/{slug}/{s}_{c}.html"
        except Exception:
            pass
        return chapter_url

    @staticmethod
    def _page_no(url):
        """从图片文件名提取页码数字用于排序；无数字则排到最后。"""
        fname = url.split('/')[-1].split('?')[0]
        m = re.search(r'(\d+)\.(?:jpg|jpeg|png|webp)', fname, re.I)
        return int(m.group(1)) if m else 10 ** 9

    def _read_req_files(self, chapter_tab):
        """优先从前端运行时变量 REQ_FILES 直接取图片 URL（结构不确定，宽松兼容）。"""
        try:
            js = (
                "const v=window.REQ_FILES; const out=[];"
                "if(typeof v==='string'){out.push(v);}"
                "else if(Array.isArray(v)){v.forEach(x=>{"
                "if(x==null)return;"
                "if(typeof x==='string'){out.push(x);}"
                "else if(typeof x==='object'){"
                "['url','src','file','name','image','path'].forEach(k=>{"
                "if(typeof x[k]==='string')out.push(x[k]);});}});}"
                "else if(v&&typeof v==='object'){"
                "Object.keys(v).forEach(k=>{if(typeof v[k]==='string')out.push(v[k]);});}"
                "return out;"
            )
            return chapter_tab.run_js(js) or []
        except Exception as e:
            print(f"读取 REQ_FILES 失败: {e}")
            return []

    def _collect_images_from_page(self, chapter_tab):
        """分段滚动触发懒加载后，按 REQ_FILES → DOM → HTML正则 顺序收集真实漫画页 URL。"""
        raw_urls = []

        # 1) 前端运行时变量 REQ_FILES（若存在则最完整）
        req_urls = self._read_req_files(chapter_tab)
        if req_urls:
            raw_urls.extend(req_urls)
            print(f"从 REQ_FILES 读取到 {len(req_urls)} 个候选地址")

        # 2) 分段滚动触发懒加载：window 与内部阅读容器同时滚，出图数连续两轮不增长即停
        try:
            last_count, stable_rounds = -1, 0
            for _ in range(30):
                chapter_tab.run_js(
                    "const el=document.querySelector('.chapter-main')||"
                    "document.querySelector('.comic-chapter');"
                    "if(el) el.scrollBy(0, el.clientHeight);"
                    "window.scrollBy(0, window.innerHeight);"
                )
                time.sleep(0.7)
                cur = chapter_tab.run_js(
                    "return document.querySelectorAll"
                    "('.comic-contain__item img, .comic-contain__item amp-img').length"
                ) or 0
                if cur == last_count:
                    stable_rounds += 1
                    if stable_rounds >= 2:
                        break
                else:
                    stable_rounds = 0
                    last_count = cur
            chapter_tab.run_js("window.scrollTo(0, 0)")
            time.sleep(0.6)
        except Exception as e:
            print(f"滚动触发懒加载出错: {e}")

        # 3) 漫画容器内 img / amp-img
        try:
            js = (
                "return Array.from(document.querySelectorAll"
                "('.comic-contain__item img, .comic-contain__item amp-img'))"
                ".map(i=>i.getAttribute('data-src')||i.getAttribute('src')||'')"
                ".filter(Boolean);"
            )
            raw_urls.extend(chapter_tab.run_js(js) or [])
        except Exception as e:
            print(f"容器内图片提取失败: {e}")

        # 4) 兜底：整页 HTML 正则提取 amp-img（参照同框架包子漫画）
        if not any('scomic' in u for u in raw_urls):
            try:
                html = chapter_tab.html or ''
                for src in re.findall(
                    r'<amp-img[^>]*?(?:data-)?src=["\']([^"\']+)["\']', html
                ):
                    raw_urls.append(src)
            except Exception as e:
                print(f"HTML 正则兜底提取失败: {e}")

        def _is_real_comic_image(u):
            if not is_normal_url(u):
                return False
            # 排除：推荐封面、APP 下载栏、静态图标、favicon、内联 data 图
            bad = ['/cover/', '/bzmhapp-data/', '/static/', 'favicon',
                   'apple-icon', 'default_cover', 'data:image']
            if any(b in u for b in bad):
                return False
            # 真漫画图位于 bzcdn.net/scomic/ 路径
            return 'scomic' in u or 'bzcdn.net' in u

        # 清洗：去缓存参数 query、过滤、去重（保序）
        cleaned, seen = [], set()
        for u in raw_urls:
            u = (u or '').strip()
            if not _is_real_comic_image(u):
                continue
            u = u.split('?')[0]  # 去掉 ?r=xxx 等缓存戳
            if u in seen:
                continue
            seen.add(u)
            cleaned.append(u)

        # 按文件名页码升序，保证 1,2,3...N 顺序
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
        reader_url = self._to_reader_url(chapter_url)
        if reader_url != chapter_url:
            print(f"章节{chapter_num} 阅读直链: {reader_url}")
        try:
            chapter_tab = main_tab.new_tab(reader_url)
            time.sleep(2)

            # 阅读域间歇弹挑战门/错误页：容器=0、body 高度 463、标题为 cn.cnbzmg.com
            # 均视为未过闸，需多次刷新；实测通常第 3~4 次刷新后正常。
            max_retries = 6
            ready = False
            for attempt in range(max_retries + 1):
                try:
                    state = chapter_tab.run_js("""
                        const items=document.querySelectorAll('.comic-contain__item').length;
                        const h=document.body?document.body.scrollHeight:0;
                        const t=document.title||'';
                        return {items:items, height:h, title:t};
                    """) or {}
                except Exception:
                    # 页面刷新/重载瞬间上下文丢失，等待后下一轮再判
                    state = {}
                    time.sleep(1.5)

                n = int(state.get('items') or 0)
                h = int(state.get('height') or 0)
                title = state.get('title') or ''

                if n > 0 and h > 600 and title != 'cn.cnbzmg.com':
                    ready = True
                    time.sleep(3)  # 过闸后留时间让首屏内容渲染
                    break

                if attempt < max_retries:
                    print(f"章节{chapter_num} 未过闸/未加载(容器={n} 高度={h})，第{attempt + 1}次刷新...")
                    try:
                        chapter_tab.get(reader_url)
                    except Exception:
                        pass
                    time.sleep(6)
                else:
                    print(f"章节{chapter_num} 已达最大重试次数({max_retries})，跳过该章")

            if ready:
                herf_list = self._collect_images_from_page(chapter_tab)
            else:
                herf_list = []

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
            'herf_list': herf_list,
        }

    def collect_chapters_images(self, target_comic_tab, chapter_start=1, chapter_end=0,
                                max_threads=3, progress_callback=None):
        print(f"设置最大同时收集线程数: {max_threads}")

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

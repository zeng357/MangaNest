# -*- coding: utf-8 -*-
"""
通用规则爬虫（适配器引擎）
==========================
用一个 JSON「站点适配器」描述任何漫画站点的 搜索/章节列表/图片 规则，
程序即可支持该站点，无需写 Python 代码。

适配器 JSON 结构（可在 GUI「新建适配器」中填写，或直接放入 自定义站点/ 目录）：

{
  "site_name": "示例漫画站",
  "site_url": "https://example.com/",
  "image_attr": "src",
  "search": {
    "mode": "get | post_fetch",
    "url_template": "https://example.com/search?keyword={kw}",   # GET 模式用
    "post_path": "/s",            # POST 模式：接口路径
    "post_key": "k",              # POST 模式：表单键名
    "result_js": "return ...;"    # 搜索结果JS：返回 [{url, title}, ...]
  },
  "detail": {
    "chapters_js": "return ...;", # 章节列表JS：返回 [{url, title}, ...]
    "chapters_reverse": false,    # 列表是"新->旧"顺序时填 true（程序会倒序成 旧->新）
    "cover_js": "return ...;"     # 封面图JS：返回封面URL字符串（可省略）
  },
  "chapter": {
    "images_js": "return ...;",   # 章节图片JS：返回图片URL数组
    "scroll_to_load": false       # 章节页需要滚动才加载图片时填 true
  }
}

详细说明见《如何添加新站点.md》。
"""
import time
import threading
from urllib.parse import urljoin, quote

from utils import is_normal_url


def _scroll_all_tab(tab):
    """滚动 窗口 + 内部滚动容器（漫画阅读器常用）各一步，派发 scroll 事件。
    返回本次总滚动位移；0 表示没有可滚动的了。"""
    try:
        return int(tab.run_js("""
var all=document.querySelectorAll('*');
var sels=[];
for (var i=0;i<all.length;i++){
  var e=all[i];
  var cs=window.getComputedStyle(e);
  var oy=cs.overflowY, ox=cs.overflowX;
  if((oy==='auto'||oy==='scroll'||ox==='auto'||ox==='scroll') &&
     (e.scrollHeight>e.clientHeight+200 || e.scrollWidth>e.clientWidth+200)){
    sels.push(e);
  }
}
sels.sort(function(a,b){
  return ((b.scrollHeight-b.clientHeight)+(b.scrollWidth-b.clientWidth))-((a.scrollHeight-a.clientHeight)+(a.scrollWidth-a.clientWidth));
});
sels=sels.slice(0,2);
var before=window.scrollY+window.scrollX;
for (var j=0;j<sels.length;j++){ before += sels[j].scrollTop + sels[j].scrollLeft; }
for (var k=0;k<sels.length;k++){
  var c=sels[k];
  var st=Math.max(500, Math.max(c.clientHeight, c.clientWidth)*0.8);
  c.scrollTop=c.scrollTop+st;
  c.scrollLeft=c.scrollLeft+st;
  c.dispatchEvent(new Event('scroll'));
}
window.scrollTo(window.scrollX, window.scrollY+Math.max(500, window.innerHeight*0.8));
window.dispatchEvent(new Event('scroll'));
var after=window.scrollY+window.scrollX;
for (var m=0;m<sels.length;m++){ after += sels[m].scrollTop + sels[m].scrollLeft; }
return after-before;
""") or 0)
    except Exception:
        return 0


def scroll_incremental_tab(tab, max_steps=120, delay=0.25):
    """滚动 窗口+内部滚动容器（阅读器/章节列表通用），逐步+派发scroll事件，
    连续2次无位移即停止。解决 kanman 等阅读器内部容器懒加载问题。"""
    try:
        stuck = 0
        for _ in range(max_steps):
            moved = _scroll_all_tab(tab)
            time.sleep(delay)
            if moved <= 0:
                stuck += 1
                if stuck >= 2:
                    break
            else:
                stuck = 0
    except Exception:
        pass


def _click_more_tab(tab, max_rounds=1):
    """点击 查看更多/下一页/加载更多 按钮，返回是否点击"""
    try:
        clicked = tab.run_js("""
var done=false;
document.querySelectorAll('a,button,span,div,p').forEach(function(e){
  if(done) return;
  var t=(e.textContent||'').trim().replace(/\\s+/g,' ');
  var ok = t.indexOf('查看更多')>-1||t.indexOf('展开全部')>-1||t.indexOf('加载更多')>-1||t.indexOf('查看全部')>-1||t.indexOf('下一页')>-1||t.indexOf('下一组')>-1;
  if(ok && t.length<12){ try{ e.click(); done=true; }catch(err){} }
});
return done;
""") or False
        return bool(clicked)
    except Exception:
        return False


class RuleSiteCrawler:
    """按适配器规则抓取的通用爬虫（需要浏览器渲染）"""
    NEEDS_BROWSER = True
    REQUIRES_LOGIN = False
    SITE_NAME = '通用规则站点'
    SITE_URL = ''
    CONFIG = {'site_url': '', 'locators': {}, 'image_attr': 'src'}

    def __init__(self, crawler, adapter=None):
        self.crawler = crawler
        # 实例未传入适配器时，回退到类属性 ADAPTER（适配器站点由 site_discovery 动态生成类）
        self.adapter = adapter or getattr(type(self), 'ADAPTER', None) or {}
        self._chapters_cache = {}  # tab_url -> chapters

    # ---------- 工具 ----------
    def _cfg(self, *keys, default=None):
        cur = self.adapter
        for k in keys:
            if not isinstance(cur, dict):
                return default
            cur = cur.get(k)
            if cur is None:
                return default
        return cur

    def _run_js(self, tab, js, label=''):
        try:
            return tab.run_js(js)
        except Exception as e:
            print(f"[规则站点] {label} 执行JS失败: {e}")
            return None

    # ---------- 搜索 ----------
    def search_comic(self, comic_name, comic_id=None):
        if comic_id:
            url = comic_id if comic_id.startswith('http') else urljoin(self.SITE_URL, comic_id)
            print(f"[规则站点] 按ID打开: {url}")
            self.crawler.tab.get(url)
            time.sleep(2)
            tab = self.crawler.page.new_tab(url)
            time.sleep(2)
            return tab

        search_cfg = self._cfg('search', default={}) or {}
        mode = search_cfg.get('mode', 'get')
        if mode == 'post_fetch':
            post_path = search_cfg.get('post_path', '/s')
            post_key = search_cfg.get('post_key', 'k')
            print(f"[规则站点] POST接口搜索: {post_path}?{post_key}={comic_name}")
            self.crawler.tab.get(self.SITE_URL)
            time.sleep(2)
            js = (
                f"fetch('{urljoin(self.SITE_URL, post_path)}',{{method:'POST',"
                "headers:{'Content-Type':'application/x-www-form-urlencoded'},"
                f"body:'{post_key}={comic_name}'}})"
                ".then(r=>r.text()).then(t=>{var d=JSON.parse(t);window.__rs=(d&&d.data)||[];})"
            )
            self._run_js(self.crawler.tab, js, 'POST搜索')
            time.sleep(2.5)
            tab = self.crawler.tab
        else:
            url_tpl = search_cfg.get('url_template') or (self.SITE_URL + 'search?keyword={kw}')
            url = url_tpl.replace('{kw}', quote(comic_name))
            print(f"[规则站点] GET搜索: {url}")
            self.crawler.tab.get(url)
            time.sleep(3)
            tab = self.crawler.tab

        result_js = search_cfg.get('result_js')
        if not result_js:
            print("[规则站点] 适配器缺少 search.result_js")
            return None
        entries = self._run_js(tab, result_js, '搜索结果') or []
        if not isinstance(entries, list):
            entries = []
        entries = [e for e in entries if isinstance(e, dict) and e.get('url')]
        if not entries:
            print(f"[规则站点] 未找到「{comic_name}」的搜索结果")
            return None
        for i, e in enumerate(entries[:10]):
            print(f"  [结果{i+1}] {e.get('title','')}  {e['url'][:90]}")
        first = entries[0]
        detail_url = first['url']
        if not detail_url.startswith('http'):
            detail_url = urljoin(self.SITE_URL, detail_url)
        print(f"[规则站点] 打开详情页: {detail_url}")
        tab = self.crawler.page.new_tab(detail_url)
        time.sleep(2.5)
        return tab

    # ---------- 章节列表 ----------
    def _fetch_chapters(self, target_comic_tab):
        url = target_comic_tab.url
        if url in self._chapters_cache:
            return self._chapters_cache[url]
        chapters_js = self._cfg('detail', 'chapters_js')
        if not chapters_js:
            print("[规则站点] 适配器缺少 detail.chapters_js")
            return []
        if self._cfg('detail', 'chapters_scroll', default=False):
            # 循环：点 下一页/查看更多 → 增量滚动 → 重跑章节JS → 合并去重，连续3轮无增长才停
            seen = set()
            no_growth = 0
            raw = self._run_js(target_comic_tab, chapters_js, '章节列表') or []
            for _ in range(30):
                for e in (raw if isinstance(raw, list) else []):
                    if isinstance(e, dict) and e.get('url'):
                        seen.add(e['url'])
                before = len(seen)
                _click_more_tab(target_comic_tab)
                scroll_incremental_tab(target_comic_tab, max_steps=40, delay=0.25)
                time.sleep(0.6)
                raw = self._run_js(target_comic_tab, chapters_js, '章节列表') or []
                for e in (raw if isinstance(raw, list) else []):
                    if isinstance(e, dict) and e.get('url'):
                        seen.add(e['url'])
                if len(seen) > before:
                    no_growth = 0
                else:
                    no_growth += 1
                    if no_growth >= 3:
                        break
        else:
            raw = self._run_js(target_comic_tab, chapters_js, '章节列表') or []
        if not isinstance(raw, list):
            raw = []
        entries = []
        seen = set()
        for e in raw:
            if not isinstance(e, dict):
                continue
            u = e.get('url') or ''
            t = (e.get('title') or '').strip() or f"第{len(entries) + 1}话"
            if not u.startswith('http'):
                u = urljoin(self.SITE_URL, u)
            if u in seen:
                continue
            seen.add(u)
            entries.append({'url': u, 'title': t})
        if self._cfg('detail', 'chapters_reverse', default=False):
            entries.reverse()
        chapters = [{'num': i, 'url': e['url'], 'title': e['title']} for i, e in enumerate(entries, 1)]
        self._chapters_cache[url] = chapters
        print(f"[规则站点] 共获取 {len(chapters)} 个章节URL")
        return chapters

    def get_chapter_count(self, target_comic_tab):
        chs = self._fetch_chapters(target_comic_tab)
        print(f"检测到 {len(chs)} 个章节")
        return len(chs)

    def get_chapter_list(self, target_comic_tab):
        """返回带标题的完整章节表：[{num, url, title}, ...]"""
        return self._fetch_chapters(target_comic_tab)

    # ---------- 封面 ----------
    def get_cover_image(self, target_comic_tab):
        cover_js = self._cfg('detail', 'cover_js')
        if not cover_js:
            return None
        url = self._run_js(target_comic_tab, cover_js, '封面')
        if isinstance(url, str) and url:
            print(f"封面图片URL: {url}")
            return url
        return None

    # ---------- 章节图片 ----------
    def get_chapter_image_urls(self, chapter_tab):
        herf_list = []
        if self._cfg('chapter', 'scroll_to_load', default=False):
            try:
                scroll_incremental_tab(chapter_tab, max_steps=90)
                time.sleep(0.8)
            except Exception as e:
                print(f"[规则站点] 滚动触发懒加载出错: {e}")
        images_js = self._cfg('chapter', 'images_js')
        if not images_js:
            print("[规则站点] 适配器缺少 chapter.images_js")
            return herf_list
        urls = self._run_js(chapter_tab, images_js, '章节图片') or []
        if not isinstance(urls, list):
            urls = []
        bad = ('load.gif', '/static/', 'space.gif', 'visitor.png', '/product/',
               '/status/', 'gold.png', 'coins.png', 'ticket.png',
               'recommend.png', 'mascot', 'logo')
        seen = set()
        for u in urls:
            if (isinstance(u, str) and u.startswith('http')
                    and not any(b in u for b in bad)
                    and is_normal_url(u) and u not in seen):
                seen.add(u)
                herf_list.append(u)
        print(f"共提取 {len(herf_list)} 张图片")
        return herf_list

    # ---------- 单章收集 ----------
    def collect_chapter_images(self, chapter_info):
        chapter_num = chapter_info['chapter_num']
        chapter_url = chapter_info['url']
        main_tab = chapter_info['main_tab']
        print(f"正在处理章节{chapter_num}: {chapter_url}")
        try:
            chapter_tab = main_tab.new_tab(chapter_url)
            time.sleep(2.5)

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
            'url': chapter_url,
        }

    # ---------- 批量收集（多线程） ----------
    def collect_chapters_images(self, target_comic_tab, chapter_start=1, chapter_end=0,
                                max_threads=3, progress_callback=None):
        print(f"设置最大同时收集线程数: {max_threads}")
        chapter_urls = self._fetch_chapters(target_comic_tab)
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

            batch_chapters_info = []
            for num in range(current_chapter, group_end + 1):
                batch_chapters_info.append({
                    'chapter_num': num,
                    'url': chapter_urls[num - 1]['url'],
                    'title': chapter_urls[num - 1].get('title', ''),
                    'main_tab': self.crawler.tab,
                })

            threads = []
            results = []

            def thread_wrapper(chapter_info):
                result = self.collect_chapter_images(chapter_info)
                results.append(result)

            for chapter_info in batch_chapters_info:
                thread = threading.Thread(target=thread_wrapper, args=(chapter_info,))
                threads.append(thread)
                thread.start()

            for thread in threads:
                thread.join()

            all_chapters_data.extend(results)
            for _ in results:
                if progress_callback:
                    progress_callback()
            current_chapter = group_end + 1

        return all_chapters_data

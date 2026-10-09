# -*- coding: utf-8 -*-
"""
站点自动分析引擎
================
给一个漫画网站地址，自动分析出它的 搜索方式 / 章节列表 / 图片规则，
生成可永久保存的「站点适配器」JSON（存入 自定义站点/ 目录后即可下载）。

分析策略（启发式，无需人工写JS）：
  1. 搜索页：自动找搜索输入框 → 用漫画名试搜 → 按 URL/标题特征挑出"漫画详情链接"
  2. 详情页：自动找"章节链接"（URL 形态 + 标题含 第N话/Chapter 等）→ 判断章节顺序
  3. 章节页：自动找真漫画图（容器类名 + 懒加载属性 + 过滤小图/占位图）
"""
import re
import time
import socket
import tempfile
from urllib.parse import urljoin
from collections import Counter
import os

# ---------- 浏览器 ----------
def open_browser(browser_path, headless=False):
    """打开一个用于分析的浏览器（复用程序的Chrome设置）"""
    from DrissionPage import ChromiumOptions, ChromiumPage
    co = ChromiumOptions()
    if browser_path:
        co.set_browser_path(browser_path)
    debug_port = None
    for port in range(9233, 9333):
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
                s.bind(('127.0.0.1', port))
                debug_port = port
                break
        except OSError:
            continue
    co.set_local_port(debug_port or 9223)
    try:
        _profile_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'analysis_profile')
        os.makedirs(_profile_dir, exist_ok=True)
    except Exception:
        _profile_dir = tempfile.mkdtemp(prefix='comic_ana_')
    co.set_user_data_path(_profile_dir)
    co.set_argument("--disable-backgrounding-occluded-windows")
    co.set_argument("--disable-renderer-backgrounding")
    co.set_argument("--disable-background-timer-throttling")
    # 伪装成普通浏览器，避免网站（如kanman）对自动化/无头浏览器返回阉割版页面
    co.set_argument("--disable-blink-features=AutomationControlled")
    co.set_argument("--no-first-run")
    co.set_argument("--no-default-browser-check")
    co.set_argument("--start-maximized")
    try:
        co.set_user_agent("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                          "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36")
    except Exception:
        pass
    if headless:
        co.headless()
        co.set_argument("--disable-gpu")
        co.set_argument("--no-sandbox")
    return ChromiumPage(co)


def js(tab, code):
    try:
        return tab.run_js(code)
    except Exception as e:
        return None


# ---------- 1. 搜索框识别 ----------
SEARCH_INPUT_JS = """
var out=[];
document.querySelectorAll('input').forEach(function(i){
  var t=(i.type||'').toLowerCase();
  var n=(i.name||'').toLowerCase(); var id=(i.id||'').toLowerCase();
  var ph=(i.placeholder||'').toLowerCase();
  if(t==='text'||t==='search'||n.indexOf('keyword')>-1||n==='q'||n==='kw'||n==='wd'
     ||id.indexOf('search')>-1||id.indexOf('keyword')>-1
     ||ph.indexOf('搜索')>-1||ph.indexOf('漫画')>-1||ph.indexOf('name')>-1){
    var f=i.closest('form');
    out.push({name:i.name||'',id:i.id||'',placeholder:i.placeholder||'',
              formAction:f?(f.getAttribute('action')||''):'',
              formMethod:f?(f.getAttribute('method')||'get').toLowerCase():'get',
              formId:f?(f.id||''):''});
  }
});
return out;
"""


def detect_search_input(tab):
    """返回候选搜索框列表 [{name,id,placeholder,formAction,formMethod,formId}]"""
    return js(tab, SEARCH_INPUT_JS) or []


def build_search_url(site_url, item, keyword):
    """由搜索框信息生成GET搜索地址（GET模式），返回url或None"""
    action = item.get('formAction') or ''
    name = item.get('name') or item.get('id') or 'keyword'
    if action.startswith('http'):
        base = action
    else:
        base = urljoin(site_url, action) if action else site_url
    sep = '&' if '?' in base else '?'
    return f"{base}{sep}{name}={keyword}"


def build_search_url_candidates(site_url, item, keyword):
    """生成一组候选搜索地址（按成功率从高到低）：
    有表单action→用它；无action→依次尝试 /search/?name= 、/search?name= 、站点?name= ，
    最后再试常见参数名 keyword/q（部分站输入框name不规范）。"""
    name = item.get('name') or item.get('id') or 'keyword'
    action = item.get('formAction') or ''
    cands = []
    if action:
        base = urljoin(site_url, action) if not action.startswith('http') else action
        sep = '&' if '?' in base else '?'
        cands.append(f"{base}{sep}{name}={keyword}")
    for path in ('search/', 'search'):
        base = site_url.rstrip('/') + '/' + path
        sep = '&' if '?' in base else '?'
        cands.append(f"{base}{sep}{name}={keyword}")
    base = site_url.rstrip('/') + '/'
    cands.append(f"{base}?{name}={keyword}")
    for alt in ('keyword', 'q'):
        if alt != name:
            cands.append(f"{base}?{alt}={keyword}")
    # 去重保序
    seen, out = set(), []
    for u in cands:
        if u not in seen:
            seen.add(u)
            out.append(u)
    return out


def looks_like_search_results(url, links, score_fn, allowed_netloc=None):
    """页面是否像搜索结果页：地址含search且页面有链接，或已有详情类链接"""
    if 'search' in url.lower() and len(links) >= 3:
        return True
    return any(score_fn(l['href'], (l.get('ti') or l.get('title') or ''), allowed_netloc) > 0 for l in links)


def submit_search(tab, item, keyword):
    """POST表单搜索：填值并提交，返回是否成功"""
    sel = _input_selector(item)
    if not sel:
        return False
    code = ("var i=document.querySelector('%s');if(!i){return false;}i.value=%r;"
            "var f=i.closest('form');if(f){f.submit();return true;}return false;"
            % (sel, keyword))
    return bool(js(tab, code))


def _input_selector(item):
    if item.get('id'):
        return '#' + re.sub(r'[^A-Za-z0-9_\\-]', '', item['id'])
    if item.get('name'):
        return 'input[name="%s"]' % item['name']
    return None


# ---------- 链接特征打分 ----------
DETAIL_HREF_RE = re.compile(r'/(book|manhua|comic|manga|detail|info|read|cartoon|dm|list|ku|mh)/', re.I)
DETAIL_DIGIT_RE = re.compile(r'/\d+(\.html?)?$')
BAD_HREF = ('search', 'login', 'register', 'tag', 'category', 'static', '.css', '.js',
            '.png', '.jpg', '.gif', 'javascript', 'about', 'help', 'user', 'favorite', 'collect')
NAV_HREF = ('manhua-new', 'manhua-rank', 'manhua-jp', 'manhua-original', 'manhua-rexue',
            'manhua-top', 'manhua-all', 'comichistory', 'bookmarker', 'vipindex', 'download',
            'rss-', 'brand-', 'fenlei', 'sort', 'update', 'rank', 'new', 'all', 'list', 'top',
            'movie', 'video', 'play', 'donghua', 'animation')
NAV_TITLES = ('更新', '排行', '日漫', '历史', '收藏', '原创', '热血', '神鬼', '首页',
              'VIP', '登录', '下载APP', '更多', '全部历史', '点击登录', '礼包', '全网搜', '搜索')


def _same_domain(href, allowed_netloc):
    """链接是否属于本站（忽略 www. 前缀差异）"""
    if not allowed_netloc:
        return True
    try:
        from urllib.parse import urlsplit
        host = urlsplit(href).netloc.lower()
        base = allowed_netloc.lower()
        if host == base:
            return True
        if host.startswith('www.') and host[4:] == base:
            return True
        if base.startswith('www.') and base[4:] == host:
            return True
        return False
    except Exception:
        return False


def score_detail(href, title, allowed_netloc=None):
    if not href.startswith('http'):
        return -99
    if not _same_domain(href, allowed_netloc):
        return -99
    if any(b in href for b in BAD_HREF):
        return -99
    if any(b in href for b in NAV_HREF):
        return -99
    if title in NAV_TITLES or any(title.startswith(t) for t in NAV_TITLES if len(t) > 2):
        return -99
    s = 0
    # 章节标题（第N话/回）是章节不是漫画详情，降权
    if CHAPTER_TITLE_RE.search(title or ''):
        s -= 8
    if DETAIL_HREF_RE.search(href):
        s += 6
    if DETAIL_DIGIT_RE.search(href):
        s += 4
    # 纯数字ID根路径（如 /5471/）更像漫画详情页
    try:
        from urllib.parse import urlsplit
        path = urlsplit(href).path
        if re.fullmatch(r'/\d+/?', path):
            s += 3
    except Exception:
        pass
    if re.search(r'(book|comic|manhua|manga|id)', href, re.I):
        s += 2
    if title and len(title) >= 2 and not re.match(r'^[\d\s·\-]+$', title):
        s += 1
    elif not (title or '').strip():
        s -= 5  # 空标题：多半是图片/图标链接，不是漫画详情
    return s


CHAPTER_HREF_RE = re.compile(r'/(chapter|read|view|pic|mh|manhua|comic|book|detail|info|dm|content)/?\d', re.I)
CHAPTER_TITLE_RE = re.compile(r'第\s*\d+[话章节卷回]|(总)?\s*\d+[话章节回]|chapter\s*\d+|ep\.?\d+', re.I)
BAD_TITLES = ('开始阅读', '立即阅读', '马上阅读', '阅读全文', '阅读', '下一页', '上一页',
              '目录', '评论', '简介', '下载APP', '上一话', '下一话', '详情', '收藏')


def score_chapter(href, title, allowed_netloc=None):
    if not href.startswith('http'):
        return -99
    if not _same_domain(href, allowed_netloc):
        return -99
    if any(b in href for b in BAD_HREF):
        return -99
    if any(b in href for b in NAV_HREF):
        return -99
    if title in NAV_TITLES:
        return -99
    s = 0
    if CHAPTER_HREF_RE.search(href):
        s += 5
    if CHAPTER_TITLE_RE.search(title):
        s += 4
    if re.search(r'\d', href):
        s += 1
    if title and 1 <= len(title) <= 40 and title not in BAD_TITLES:
        s += 1
    return s


def collect_links(tab):
    """收集页面上所有 a 链接 [{href,title}]"""
    return js(tab, """
var out=[];
document.querySelectorAll('a').forEach(function(a){
  var h=a.href||''; var t=(a.textContent||'').trim().slice(0,60);
  var ti=a.getAttribute('title')||'';
  if(h.indexOf('http')===0||h.indexOf('/')===0) out.push({href:h,title:t,ti:ti});
});
return out;
""") or []


def extract_comic_name(page_title):
    """从页面标题提取漫画名（去 漫画/在线/阅读 等后缀与分隔符）"""
    if not page_title:
        return ''
    t = re.split(r'[_\-|·—]|漫画|在线|阅读|免费|全集|动态|动画', page_title)[0].strip()
    return t[:20]


def judge_chapter_links(scored, detail_url, page_title):
    """判断章节链接是否真的是“同一部漫画的章节列表”：
    1) 优先按详情页ID过滤（网址第一段相同 = 同一漫画的章节）；
    2) 若全是“裸根链接”且标题分属多部不同漫画 → 这是推荐列表，拒绝；
    3) 其余情况（章节ID与详情ID不同，如 dm5）→ 接受但提示人工核对。
    返回 (verdict, links)：verdict ∈ use / use_warn / reject
    """
    from urllib.parse import urlsplit

    def segs(path):
        return [s for s in path.split('/') if s]

    try:
        idseg = segs(urlsplit(detail_url).path)[0] if segs(urlsplit(detail_url).path) else ''
    except Exception:
        idseg = ''
    same_id = []
    for l in scored:
        psegs = segs(urlsplit(l['href']).path)
        if idseg and len(psegs) >= 2 and psegs[0] == idseg:
            same_id.append(l)
    if len(same_id) >= 2:
        return 'use', same_id

    bare = all(len(segs(urlsplit(l['href']).path)) == 1 for l in scored)
    if bare:
        # 全是裸根链接：检查标题是否属于“多部不同漫画”（推荐列表特征）
        cname = extract_comic_name(page_title)
        others = set()
        for l in scored:
            t = (l.get('ti') or l.get('title') or '').strip()
            head = re.split(r'第\s*\d+[话章节回]', t, maxsplit=1)[0].strip()
            if head and head != cname and head not in ('第', '总'):
                others.add(head)
        if len(others) >= 2:
            return 'reject', []
        return 'use_warn', scored
    return 'use_warn', scored


def pick_top(links, score_fn, limit=8, allowed_netloc=None):
    scored = [(score_fn(l['href'], (l.get('ti') or l.get('title') or ''), allowed_netloc), l) for l in links]
    scored = [x for x in scored if x[0] > 0]
    scored.sort(key=lambda x: -x[0])
    return [l for _, l in scored[:limit]]


# ---------- 2. 章节链接分析与顺序 ----------
def build_href_regex(links, min_count=2):
    """从一批链接里提取“URL形态”生成正则。

    把路径中的连续 字母数字/下划线/连字符 串（如漫画ID、章节ID、数字页码）
    统一替换为占位符，出现最多的形态即为章节/列表链接规律，
    再还原成正则（形如  https://站名/漫画ID/[A-Za-z0-9_-]+.html ）。
    """
    shapes = Counter()
    for l in links:
        h = l['href']
        if not h.startswith('http'):
            continue
        try:
            from urllib.parse import urlsplit
            parts = urlsplit(h)
            path = re.sub(r'[A-Za-z0-9_\-]{2,}', '{SEG}', parts.path)
            shape = f"{parts.scheme}://{parts.netloc}{path}"
        except Exception:
            shape = re.sub(r'[A-Za-z0-9_\-]{2,}', '{SEG}', h)
        shapes[shape] += 1
    if not shapes:
        return None
    shape, cnt = shapes.most_common(1)[0]
    if cnt < min_count:
        return None
    rx = re.escape(shape).replace(r'\{SEG\}', r'[A-Za-z0-9_\-]+')
    return rx


def detect_order(titles):
    """判断标题数字序列：多数递减→新到旧（返回True需倒序）"""
    nums = []
    for t in titles:
        m = re.search(r'第\s*(\d+)|(?:总)?\s*(\d+)(?=[话章节回卷·\s\-:：.])', t)
        if m:
            nums.append(int(next(g for g in m.groups() if g)))
    if len(nums) < 3:
        return False
    desc = sum(1 for i in range(len(nums) - 1) if nums[i] > nums[i + 1])
    return desc > len(nums) / 2


# ---------- 3. 章节图片分析 ----------
def _scroll_all(tab):
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


def scroll_incremental(tab, max_steps=120, delay=0.25):
    """滚动 窗口+内部滚动容器（阅读器/章节列表通用），逐步+派发scroll事件，
    连续2次无位移即停止。解决 kanman 等阅读器内部容器懒加载问题。"""
    try:
        stuck = 0
        for _ in range(max_steps):
            moved = _scroll_all(tab)
            time.sleep(delay)
            if moved <= 0:
                stuck += 1
                if stuck >= 2:
                    break
            else:
                stuck = 0
    except Exception:
        pass


def click_more_buttons(tab, max_rounds=5, include_next=True):
    """点击 查看更多/展开全部/加载更多/下一页 等按钮，加载分页/折叠的章节列表。
    返回本轮是否有点击动作。"""
    try:
        clicked_any = False
        for _ in range(max_rounds):
            clicked = js(tab, """
var done=false;
document.querySelectorAll('a,button,span,div,p').forEach(function(e){
  if(done) return;
  var t=(e.textContent||'').trim().replace(/\\s+/g,' ');
  var ok = t.indexOf('查看更多')>-1||t.indexOf('展开全部')>-1||t.indexOf('加载更多')>-1||t.indexOf('查看全部')>-1;
  if(%s && (t.indexOf('下一页')>-1||t.indexOf('下一组')>-1||t.indexOf('下一批')>-1)) ok = true;
  if(ok && t.length<12){
    try{ e.click(); done=true; }catch(err){}
  }
});
return done;
""" % ('true' if include_next else 'false'))
            if not clicked:
                break
            clicked_any = True
            time.sleep(1.0)
        return clicked_any
    except Exception:
        return False


def wait_page_has_links(tab, timeout=12):
    """等待页面加载出链接（防JS验证/慢加载），返回 (links, page_title)"""
    deadline = time.time() + timeout
    links = []
    title = ''
    while time.time() < deadline:
        try:
            links = collect_links(tab)
        except Exception:
            links = []
        try:
            title = (tab.run_js('document.title') or '') if hasattr(tab, 'run_js') else ''
        except Exception:
            title = ''
        if links:
            break
        time.sleep(1.5)
    return links, title


def collect_with_paging(tab, max_rounds=30, min_growth_rounds=3):
    """循环采集：点 查看更多/下一页 → 滚动到底 → 重新采集 → 合并去重，
    连续 min_growth_rounds 轮无新链接才停止。返回最终全部链接（含跨页）。"""
    seen = set()
    final = []
    no_growth = 0
    for _ in range(max_rounds):
        links = collect_links(tab)
        final = links
        before = len(seen)
        for l in links:
            seen.add(l['href'])
        clicked = click_more_buttons(tab, max_rounds=1)
        scroll_incremental(tab, max_steps=40, delay=0.25)
        time.sleep(0.6)
        links2 = collect_links(tab)
        for l in links2:
            seen.add(l['href'])
        if len(seen) > before:
            no_growth = 0
        else:
            no_growth += 1
            if no_growth >= min_growth_rounds:
                final = links2 if len(links2) >= len(links) else links
                break
    return final


def scroll_to_bottom(tab, rounds=6):
    try:
        last_h = -1
        for _ in range(rounds):
            h = tab.run_js('return document.body.scrollHeight') or 0
            tab.run_js('window.scrollTo(0, document.body.scrollHeight)')
            time.sleep(0.6)
            if h == last_h:
                time.sleep(0.8)
                h2 = tab.run_js('return document.body.scrollHeight') or 0
                if h2 == h:
                    break
                last_h = h2
            else:
                last_h = h
    except Exception:
        pass


IMG_INFO_JS = """
var out=[];
document.querySelectorAll('img').forEach(function(i){
  var src=i.getAttribute('src')||''; var ds=i.getAttribute('data-src')||'';
  var o=i.getAttribute('data-original')||'';
  var cls=[];
  var el=i;
  for(var d=0; d<4 && el && el.parentElement; d++){
    el=el.parentElement;
    var c=(el.className&&typeof el.className==='string')?el.className.trim():'';
    if(c) cls.push(c.split(/\\s+/).slice(0,2).join(' '));
    else if(el.id) cls.push('#'+el.id);
  }
  out.push({src:src.slice(0,120), ds:ds.slice(0,120), o:o.slice(0,120),
            w:i.naturalWidth||i.width||0, h:i.naturalHeight||i.height||0,
            cls:cls.slice(0,3)});
});
return out;
"""


def is_placeholder(url):
    return (not url or 'load.gif' in url or 'data:image' in url
            or '/static/' in url or '.gif' in url or url.startswith('about:')
            or 'visitor.png' in url or 'space.gif' in url or '/product/' in url
            or '/status/' in url or 'gold.png' in url or 'coins.png' in url
            or 'ticket.png' in url or 'recommend.png' in url or 'mascot' in url)


def analyze_images(imgs):
    """从图片信息里推断：容器选择器、真实属性、是否懒加载、真图数量"""
    # 真实图：有 http 地址且非占位
    real = []
    for im in imgs:
        u = im.get('ds') or im.get('o') or im.get('src') or ''
        if u.startswith('http') and not is_placeholder(u):
            real.append(im)
    if not real:
        return {'count': 0, 'selector': None, 'attr': None, 'scroll': False}

    # 懒加载：src是占位但 data-src 是真图
    lazy = any(im.get('ds', '').startswith('http') and is_placeholder(im.get('src', '')) for im in real)

    # 容器：取真图父级出现最多的类名
    cls_counter = Counter()
    for im in real:
        for c in im.get('cls', []):
            if c and len(c) <= 40 and not re.match(r'^(div|body|a|li|ul|span)$', c.split(' ')[0]):
                cls_counter[c] += 1
    selector = None
    if cls_counter:
        top_cls, cnt = cls_counter.most_common(1)[0]
        if cnt >= max(1, len(real) * 0.5):
            first = top_cls.split(' ')[0]
            selector = '.' + first if not first.startswith('#') else first

    # 属性：优先 data-src，其次 data-original，最后 src
    attr = 'src'
    if any(im.get('ds', '').startswith('http') for im in real):
        attr = 'data-src'
    elif any(im.get('o', '').startswith('http') for im in real):
        attr = 'data-original'

    return {'count': len(real), 'selector': selector, 'attr': attr,
            'scroll': lazy, 'sample': [u for im in real for u in
                                       (im.get('ds') or im.get('o') or im.get('src'),)][:3]}


# ---------- 适配器生成 ----------
def build_images_js(selector, attr):
    sel = selector or 'body'
    return ("return Array.from(document.querySelectorAll('%s img'))"
            ".map(function(i){return i.getAttribute('%s')||i.getAttribute('src')||'';})"
            ".filter(function(u){return u&&u.indexOf('http')===0"
            "&&u.indexOf('load.gif')<0&&u.indexOf('/static/')<0&&u.indexOf('space.gif')<0"
            "&&u.indexOf('visitor.png')<0&&u.indexOf('/product/')<0&&u.indexOf('/status/')<0"
            "&&u.indexOf('gold.png')<0&&u.indexOf('coins.png')<0&&u.indexOf('ticket.png')<0"
            "&&u.indexOf('recommend.png')<0&&u.indexOf('mascot')<0;});"
            % (sel, attr))


def build_links_js(rx, slice_n=10, title_filter=None):
    import json
    rxjs = json.dumps(rx)
    if title_filter:
        return ("return Array.from(document.querySelectorAll('a'))"
                ".filter(function(a){return new RegExp(%s,'i').test(a.href);})"
                ".filter(function(a){return %s;})"
                ".slice(0,%d).map(function(a){return {url:a.href,title:(a.getAttribute('title')||a.textContent||'').trim()};});"
                % (rxjs, title_filter, slice_n))
    return ("return Array.from(document.querySelectorAll('a'))"
            ".filter(function(a){return new RegExp(%s,'i').test(a.href);})"
            ".slice(0,%d).map(function(a){return {url:a.href,title:(a.getAttribute('title')||a.textContent||'').trim()};});"
            % (rxjs, slice_n))


def build_chapters_js(rx, require_chapter_title=False):
    """章节列表JS：按URL形态过滤，排除“开始阅读/下一页”等按钮，返回全部章节。
    require_chapter_title=True 时额外要求标题含 第N话/回/章 等字样（防止把漫画详情/推荐混入章节）"""
    import json
    rxjs = json.dumps(rx)
    bad = "['开始阅读','立即阅读','马上阅读','阅读全文','下一页','上一页','上一话','下一话','目录','评论','简介','详情','收藏','下载APP']"
    if require_chapter_title:
        tfilter = ("var t=x.title;return t&&t.length<=40&&%s.indexOf(t)<0"
                   "&&/(第\\s*\\d+[话章节卷回]|(总)?\\s*\\d+[话章节回]|chapter\\s*\\d+|ep\\.?\\d+)/i.test(t);"
                   % bad)
    else:
        tfilter = "var t=x.title;return t&&t.length<=40&&%s.indexOf(t)<0;" % bad
    return ("return Array.from(document.querySelectorAll('a'))"
            ".filter(function(a){return new RegExp(%s,'i').test(a.href);})"
            ".map(function(a){return {url:a.href,title:(a.getAttribute('title')||a.textContent||'').trim()};})"
            ".filter(function(x){%s});"
            % (rxjs, tfilter))

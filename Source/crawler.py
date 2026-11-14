import time
import os
import json
import random

from config import DEFAULT_COOKIES_DIR
from site_discovery import get_site_crawler_class


def human_mode_enabled():
    try:
        import config
        return bool(getattr(config, 'HUMAN_MODE', True))
    except Exception:
        return True


def human_nav_pause():
    """页面跳转后的随机停顿：模拟正常人看页面、等加载的节奏。"""
    if not human_mode_enabled():
        return
    try:
        import config
        lo = float(getattr(config, 'HUMAN_BROWSER_PAUSE_MIN', 0.6))
        hi = float(getattr(config, 'HUMAN_BROWSER_PAUSE_MAX', 1.6))
    except Exception:
        lo, hi = 0.6, 1.6
    if hi < lo:
        lo, hi = hi, lo
    time.sleep(random.uniform(lo, hi))


class HumanTabProxy:
    """浏览器标签代理：页面跳转(get/refresh/back)后自动随机停顿。

    只包一层导航动作，其余属性/方法原样透传给 DrissionPage 的标签对象，
    这样所有站点爬虫的翻页行为都会带上人工节奏，不需要逐站修改。
    """

    NAV_METHODS = ('get', 'refresh', 'back', 'forward')

    def __init__(self, tab):
        object.__setattr__(self, '_tab', tab)

    def __getattr__(self, name):
        attr = getattr(object.__getattribute__(self, '_tab'), name)
        if name in HumanTabProxy.NAV_METHODS and callable(attr):
            def wrapped(*args, **kwargs):
                result = attr(*args, **kwargs)
                human_nav_pause()
                return result
            return wrapped
        return attr

    def __setattr__(self, name, value):
        setattr(object.__getattribute__(self, '_tab'), name, value)


class ComicCrawler:
    def __init__(self, site_name, browser_path, headless=False, cookie_str=None, login_mode=False, cookies_dir=None):
        from DrissionPage import ChromiumOptions, ChromiumPage
        from urllib.parse import urlparse, unquote
        
        self.site_name = site_name
        
        # 动态加载网站爬虫类
        self.site_crawler_class = get_site_crawler_class(site_name)
        self.site_config = self.site_crawler_class.CONFIG
        
        self.locators = self.site_config['locators']
        self.image_attr = self.site_config['image_attr']
        self.cookie_str = cookie_str
        self.login_mode = login_mode
        self.login_completed = False
        self.cookies_dir = cookies_dir if cookies_dir else DEFAULT_COOKIES_DIR
        self.headless = bool(headless)

        # 站点是否需要浏览器：纯requests实现的站点（如喜漫漫画）声明NEEDS_BROWSER=False，
        # 跳过浏览器启动（浏览器打开慢、且favcomic等站点代理出口被WAF拦截需直连）
        self.needs_browser = bool(getattr(self.site_crawler_class, 'NEEDS_BROWSER', True))

        # 未显式提供cookie_str时，尝试加载用户保存的Cookie字符串
        if not self.cookie_str:
            self.cookie_str = self.load_cookie_str()

        if not self.needs_browser:
            self.page = None
            self.tab = None
            print(f"站点 {site_name} 无需浏览器（纯HTTP实现），跳过浏览器启动")
            self.site_crawler = self.site_crawler_class(self)
            if self.cookie_str:
                self.set_cookie()
            return

        print(f"正在初始化浏览器...")
        co = ChromiumOptions()
        co.set_browser_path(browser_path)
        
        # 查找可用端口并直接设置地址，避免端口冲突导致浏览器无法启动或不显示窗口
        import socket
        debug_port = None
        for port in range(9223, 9323):
            try:
                with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
                    s.bind(('127.0.0.1', port))
                    debug_port = port
                    break
            except OSError:
                continue
        if debug_port is None:
            import random
            debug_port = random.randint(9223, 9322)
        co.set_local_port(debug_port)
        print(f"使用调试端口: {debug_port}")
        
        # 每次启动使用独立的用户数据临时目录，避免复用残留目录导致浏览器进程连接异常
        # 例外：站点声明 persistent_profile 时使用固定目录，让 Cloudflare 人机验证的
        # cf_clearance 等 cookie 跨次运行保留（验证一次后长期复用，不用每次重验）
        import tempfile
        if self.site_config.get('persistent_profile'):
            profile_dir = os.path.join(tempfile.gettempdir(), f'comic_dl_profile_{self.site_name}')
            try:
                os.makedirs(profile_dir, exist_ok=True)
            except Exception:
                pass
            user_data_path = profile_dir
            print(f"该站点使用持久化浏览器配置目录（保留人机验证结果）: {profile_dir}")
        else:
            user_data_path = tempfile.mkdtemp(prefix='comic_dl_')
        co.set_user_data_path(user_data_path)
        
        # 禁用窗口遮挡检测与后台节流：窗口被遮挡/最小化时Chrome会把rAF节流到~1fps，
        # 依赖rAF的阅读器（如B漫）翻页动画/图片解密会卡死在第1页
        co.set_argument("--disable-backgrounding-occluded-windows")
        co.set_argument("--disable-renderer-backgrounding")
        co.set_argument("--disable-background-timer-throttling")
        co.set_argument("--disable-features=CalculateNativeWinOcclusion")
        
        # 伪装成普通浏览器（有头/无头都生效），避免网站对自动化浏览器返回阉割版页面
        co.set_argument("--disable-blink-features=AutomationControlled")
        co.set_argument("--no-first-run")
        co.set_argument("--no-default-browser-check")
        co.set_argument("--disable-features=SmartScreen")
        try:
            co.set_user_agent("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                              "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36 Edg/120.0.0.0")
        except Exception:
            pass
        if headless:
            co.headless()
            co.set_argument("--disable-gpu")
            co.set_argument("--no-sandbox")
            co.set_argument("--disable-dev-shm-usage")
            print("已启用无头模式")
        else:
            print("已启用有头模式")

        # 直连模式：站点CONFIG声明direct_connect时禁用系统代理（浏览器直连）
        # 适用于代理出口IP被站点WAF拉黑（直连200/代理403）的站点，如喜漫漫画
        if self.site_config.get('direct_connect'):
            co.set_argument('--no-proxy-server')
            print("该站点启用直连模式（禁用系统代理，浏览器直连访问）")
        else:
            # 代理池：config.json 开启后浏览器也走代理，搜索/详情页/章节页不暴露本机IP
            try:
                import proxy_pool
                endpoint = proxy_pool.browser_proxy()
                if endpoint:
                    co.set_proxy(endpoint)
                    print(f"浏览器已启用代理出口: {proxy_pool.mask_proxy(endpoint)}")
            except Exception as e:
                print(f"浏览器代理设置失败（本次直连）: {e}")
        
        try:
            self.page = ChromiumPage(co)
            self.tab = self.page
        except Exception as e:
            print(f"浏览器连接失败: {e}")
            print("尝试清理残留的调试浏览器进程并重新启动...")
            self._kill_leftover_debug_browsers()
            time.sleep(2)
            self.page = ChromiumPage(co)
            self.tab = self.page

        # 模拟人工：翻页/刷新后自动随机停顿（所有站点统一生效，无需逐站改代码）
        if human_mode_enabled():
            self.tab = HumanTabProxy(self.tab)
            print("已启用模拟人工节奏（页面跳转后随机停顿，读取章节/详情页时不再机械连发）")
        
        # 初始化网站爬虫实例
        self.site_crawler = self.site_crawler_class(self)

        # 有Cookie字符串时自动应用（登录态），后续所有请求/标签页均生效
        if self.cookie_str:
            self.set_cookie()

    def get_cookie_str_path(self):
        if not os.path.exists(self.cookies_dir):
            os.makedirs(self.cookies_dir)
        return os.path.join(self.cookies_dir, f"{self.site_name}_cookie_str.txt")

    def save_cookie_str(self, cookie_str):
        """保存用户粘贴的Cookie字符串到文件"""
        try:
            cookie_str = (cookie_str or '').strip()
            path = self.get_cookie_str_path()
            if not cookie_str:
                if os.path.exists(path):
                    os.remove(path)
                return True
            with open(path, 'w', encoding='utf-8') as f:
                f.write(cookie_str)
            print(f"Cookie字符串已保存到: {path}")
            return True
        except Exception as e:
            print(f"保存Cookie字符串失败: {e}")
            return False

    def load_cookie_str(self):
        """加载已保存的Cookie字符串，无则返回None"""
        try:
            path = self.get_cookie_str_path()
            if os.path.exists(path):
                with open(path, 'r', encoding='utf-8') as f:
                    s = f.read().strip()
                if s:
                    print(f"已从文件加载Cookie字符串: {path}")
                    return s
        except Exception as e:
            print(f"加载Cookie字符串失败: {e}")
        return None

    def has_cookie_str(self):
        return os.path.exists(self.get_cookie_str_path())

    def clear_cookie_str(self):
        try:
            path = self.get_cookie_str_path()
            if os.path.exists(path):
                os.remove(path)
        except Exception as e:
            print(f"清除Cookie字符串失败: {e}")
    
    def _kill_leftover_debug_browsers(self):
        """仅清理带调试端口的残留浏览器进程（上次异常退出遗留），
        不影响用户正常使用的浏览器"""
        import subprocess
        import re
        for name in ('msedge.exe', 'chrome.exe'):
            try:
                r = subprocess.run(
                    ['wmic', 'process', 'where', f"name='{name}'",
                     'get', 'ProcessId,CommandLine', '/format:csv'],
                    capture_output=True, text=True, encoding='gbk', errors='ignore')
                for line in r.stdout.splitlines():
                    if 'remote-debugging-port' in line:
                        m = re.search(r'(\d+)\s*$', line.strip())
                        if m:
                            subprocess.run(['taskkill', '/F', '/PID', m.group(1)],
                                           capture_output=True)
            except Exception:
                pass

    def get_cookies_path(self):
        if not os.path.exists(self.cookies_dir):
            os.makedirs(self.cookies_dir)
        return os.path.join(self.cookies_dir, f"{self.site_name}_cookies.json")
    
    def save_cookies(self):
        if not self.needs_browser:
            print("该站点无需浏览器（纯HTTP），不保存浏览器Cookie")
            return False
        try:
            cookies = self.tab.cookies()
            cookies_path = self.get_cookies_path()
            with open(cookies_path, 'w', encoding='utf-8') as f:
                json.dump(cookies, f, ensure_ascii=False, indent=2)
            print(f"Cookie已保存到: {cookies_path}")
            return True
        except Exception as e:
            print(f"保存Cookie失败: {e}")
            return False
    
    def load_cookies(self):
        if not self.needs_browser:
            return False
        try:
            cookies_path = self.get_cookies_path()
            if not os.path.exists(cookies_path):
                print(f"Cookie文件不存在: {cookies_path}")
                return False
            
            with open(cookies_path, 'r', encoding='utf-8') as f:
                cookies = json.load(f)
            
            self.tab.set.cookies(cookies)
            print(f"已从文件加载Cookie: {cookies_path}")
            return True
        except Exception as e:
            print(f"加载Cookie失败: {e}")
            return False
    
    def has_saved_cookies(self):
        if not self.needs_browser:
            return False
        cookies_path = self.get_cookies_path()
        return os.path.exists(cookies_path)
    
    def open_login_page(self):
        if not self.needs_browser:
            print(f"{self.site_name} 无需浏览器登录，请使用Cookie字符串输入")
            return False
        print(f"正在打开 {self.site_name} 登录页面...")
        self.tab.get(self.site_config['site_url'])
        print(f"请在浏览器中完成登录操作")
        print(f"登录完成后，请在GUI界面点击'登录完成'按钮")
        return True
    
    def complete_login(self):
        self.save_cookies()
        self.login_completed = True
        print(f"登录完成，Cookie已保存")
        return True
    
    def parse_cookie_str(self, cookie_str, domain):
        cookies = []
        items = [item.strip() for item in cookie_str.split(';') if item.strip()]
        
        for item in items:
            if '=' in item:
                name, value = item.split('=', 1)
                name = name.strip()
                value = value.strip()
                
                # 注意：不能对value做unquote！浏览器发送Cookie头时保持原始编码，
                # 解码会破坏SESSDATA等含%编码值的Cookie导致登录态失效
                
                cookies.append({
                    'name': name,
                    'value': value,
                    'domain': domain,
                    'path': '/'
                })
            else:
                cookies.append({
                    'name': item.strip(),
                    'value': '',
                    'domain': domain,
                    'path': '/'
                })
        
        return cookies
    
    def set_cookie(self):
        if not self.cookie_str:
            return False
        if not self.needs_browser:
            # 纯requests站点：cookie_str 由站点爬虫的请求头直接携带
            print("该站点无需浏览器，Cookie将由爬虫请求头直接携带")
            return True
        try:
            from urllib.parse import urlparse
            
            print("正在设置Cookie...")
            netloc = urlparse(self.site_config['site_url']).netloc
            # 使用主域（如 manga.bilibili.com → .bilibili.com），覆盖所有子域API请求
            parts = netloc.split(':')[0].split('.')
            domain = '.' + '.'.join(parts[-2:]) if len(parts) >= 2 else netloc
            
            cookies = self.parse_cookie_str(self.cookie_str, domain)
            print(f"解析到 {len(cookies)} 个Cookie项 (domain={domain})")
            
            self.tab.set.cookies(cookies)
            print("Cookie已设置（后续访问自动生效）")
            return True
        except Exception as e:
            print(f"设置Cookie失败: {e}")
            return False
    
    @staticmethod
    def _english_query_variants(english):
        """由整句英译生成递进缩短的搜索词：整句 -> 去停用词核心组合 -> 最长核心词。

        站点搜索（WordPress /?s=）对整句长标题命中率低，用核心词反而更容易命中。
        """
        import re as _re
        stop = {'the', 'a', 'an', 'of', 'to', 'my', 'me', 'i', 'and', 'in', 'on',
                'is', 'was', 'with', 'after', 'before', 'for', 'at', 'it', 'this',
                'that', 'you', 'your', 'his', 'her', 'be', 'not', 'from', 'by', 'am',
                'im', 'its', 's', 't', 'do', 'does', 'did', 'are', 'were', 'have', 'has'}
        tokens = [_re.sub(r"[’']", '', t) for t in _re.findall(r"[A-Za-z0-9’']+", english)]
        core = [t for t in tokens if len(t) >= 3 and t.lower() not in stop]
        variants = [english]
        if len(core) >= 2:
            # 优先级：最长的两个词（通常是「形容词+名词」或「角色+核心词」）
            by_len = sorted(core, key=len, reverse=True)
            variants.append(' '.join(by_len[:2]))
            variants.append(' '.join(core[:3]))
            for i in range(min(4, len(core))):
                for j in range(i + 1, min(4, len(core))):
                    variants.append(core[i] + ' ' + core[j])
        # 不用单个单词兜底：单词过于宽泛，会命中完全无关的作品
        min_tokens = min(2, len(core))
        seen = set()
        result = []
        for v in variants:
            key = v.lower().strip()
            if not key or key in seen:
                continue
            n_tokens = len([t for t in [_re.sub(r"[’']", '', x)
                                       for x in _re.findall(r"[A-Za-z0-9’']+", v)]
                            if len(t) >= 3 and t.lower() not in stop])
            if n_tokens < min_tokens or n_tokens == 0:
                continue
            seen.add(key)
            result.append(v)
            if len(result) >= 6:
                break
        return result

    def search_comic(self, comic_name, comic_id=None):
        """站点搜索入口：中文关键词搜英文站时，先译成英文再搜。

        规则（只对声明 SEARCH_LANG='en' 的站点生效，中文站行为不变）：
          - 含中文关键词 -> 先翻译成英文，用英文搜；
          - 翻译失败 -> 退回原关键词直搜；
          - 英文搜仍失败且原关键词是中文 -> 再用原关键词兜底搜一次；
          - pure ASCII 关键词 / comic_id 直连：直接走站点实现。
        """
        from search_translate import contains_cjk, translate_keyword_to_english

        site_lang = getattr(self.site_crawler, 'SEARCH_LANG', 'zh')
        need_translate = (
            site_lang == 'en'
            and not comic_id
            and contains_cjk(comic_name)
        )

        if need_translate:
            english = translate_keyword_to_english(comic_name)
            if english:
                print(f"中文关键词译为英文后搜索: {comic_name} -> {english}")
                # 长句直译往往过整，站点搜索命中率低：先整句搜，再逐步用核心词搜
                queries = self._english_query_variants(english)
                last_error = None
                for q in queries:
                    try:
                        if q != english:
                            print(f"用缩短后的英文关键词重试: {q}")
                        return self.site_crawler.search_comic(q, comic_id)
                    except Exception as e:
                        last_error = e
                # 英文站的中文关键词直搜只会命中无关作品，故不再退回中文搜索
                raise Exception(
                    f"中文关键词已译为英文「{english}」但站点未搜到匹配作品：{last_error}\n"
                    f"建议：改用更短的英文关键词（如主角名/核心词），或直接粘贴作品详情页链接"
                )
            print("中文关键词翻译失败，改用原关键词直接搜索")

        try:
            return self.site_crawler.search_comic(comic_name, comic_id)
        except Exception:
            # 非中文站点/纯英文关键词：保持原有异常行为
            raise


    
    def get_cover_image(self, target_comic_tab):
        # 优先使用站点爬虫自定义的get_cover_image
        if hasattr(self.site_crawler, 'get_cover_image') and callable(self.site_crawler.get_cover_image):
            return self.site_crawler.get_cover_image(target_comic_tab)
        # 默认实现：用locators和image_attr
        try:
            coverimg_xpath = self.locators['cover_image']
            coverimg_url = target_comic_tab.ele(coverimg_xpath).attr(self.image_attr)
            print(f"封面图片URL: {coverimg_url}")
            return coverimg_url
        except Exception as e:
            print(f"获取封面图片失败: {e}")
            return None
    
    def get_chapter_count(self, target_comic_tab):
        return self.site_crawler.get_chapter_count(target_comic_tab)

    def get_chapter_list(self, target_comic_tab):
        """返回带标题的完整章节表（站点支持时）；不支持则返回None（章节表仍按数字显示）"""
        if hasattr(self.site_crawler, 'get_chapter_list') and callable(self.site_crawler.get_chapter_list):
            try:
                return self.site_crawler.get_chapter_list(target_comic_tab)
            except Exception as e:
                print(f"获取章节标题失败: {e}")
                return None
        return None
    
    def collect_chapters_images(self, target_comic_tab, chapter_start=1, chapter_end=0, max_threads=10, progress_callback=None):
        return self.site_crawler.collect_chapters_images(
            target_comic_tab,
            chapter_start=chapter_start,
            chapter_end=chapter_end,
            max_threads=max_threads,
            progress_callback=progress_callback
        )

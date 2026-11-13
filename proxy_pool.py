"""代理池：让下载/浏览器走代理出口，避免暴露本机 IP。

支持的代理来源（config.json 或 config.py 配置）：
  - file: 本地代理列表文件（默认 proxies.txt，一行一个）
  - url:  订阅链接/文本地址（http(s) 返回纯文本或 base64 编码的代理列表）
  - api:  按次返回代理的 API 地址（每次刷新取一批，适合“动态 IP”代理服务）

支持的代理写法（一行一个，空行和 # 注释忽略）：
  http://127.0.0.1:7890
  http://user:pass@1.2.3.4:8080
  1.2.3.4:8080
  1.2.3.4:8080:user:pass
  socks5://1.2.3.4:1080        # 仅浏览器可用；图片下载只支持 HTTP/HTTPS 代理

注意：
  - 图片下载按请求轮换代理（round-robin），单个代理连续失败会被冷却一段时间；
  - 浏览器（搜索/详情页/章节页）只能用一个固定出口，用 proxy_browser 指定，
    未指定时取代理池里的第一个（建议填一个“自动换 IP”的网关地址）；
  - proxy_allow_direct=True 时，代理全不可用会回退本机直连；False 则直接报错。
"""

import base64
import json
import os
import random
import re
import threading
import time
import urllib.request

from config import (
    CONFIG_FILE,
    PROXY_ALLOW_DIRECT,
    PROXY_API,
    PROXY_BROWSER,
    PROXY_ENABLED,
    PROXY_FILE,
    PROXY_MAX_TRIES,
    PROXY_SOURCE,
    PROXY_URL,
)

# 失败冷却：连续失败 FAIL_THRESHOLD 次后，冷却 FAIL_COOLDOWN 秒不再使用
FAIL_THRESHOLD = 3
FAIL_COOLDOWN = 90


# ---------------------------------------------------------------- 配置读取
def load_settings():
    """读取代理配置：config.json 优先，缺省回退 config.py 常量。"""
    s = {
        'enabled': bool(PROXY_ENABLED),
        'source': PROXY_SOURCE or 'file',
        'file': PROXY_FILE or 'proxies.txt',
        'url': PROXY_URL or '',
        'api': PROXY_API or '',
        'browser': PROXY_BROWSER or '',
        'allow_direct': bool(PROXY_ALLOW_DIRECT),
        'max_tries': int(PROXY_MAX_TRIES or 3),
    }
    try:
        if os.path.exists(CONFIG_FILE):
            with open(CONFIG_FILE, 'r', encoding='utf-8-sig') as f:
                cfg = json.load(f) or {}
            if 'proxy_enabled' in cfg:
                s['enabled'] = bool(cfg.get('proxy_enabled'))
            if cfg.get('proxy_source'):
                s['source'] = str(cfg['proxy_source'])
            if 'proxy_file' in cfg:
                s['file'] = str(cfg.get('proxy_file') or '')
            if 'proxy_url' in cfg:
                s['url'] = str(cfg.get('proxy_url') or '')
            if 'proxy_browser' in cfg:
                s['browser'] = str(cfg.get('proxy_browser') or '')
            if 'proxy_allow_direct' in cfg:
                s['allow_direct'] = bool(cfg.get('proxy_allow_direct'))
            if 'proxy_max_tries' in cfg:
                try:
                    s['max_tries'] = max(1, int(cfg.get('proxy_max_tries')))
                except Exception:
                    pass
    except Exception as e:
        print(f"读取代理配置失败: {e}")
    return s


def mask_proxy(proxy):
    """日志里脱敏账号密码。"""
    if not proxy:
        return ''
    return re.sub(r'//[^@/]+@', '//***@', str(proxy))


# ---------------------------------------------------------------- 代理解析
def normalize_proxy(line):
    """把各种写法统一成 aiohttp/requests/Chromium 可用的 URL。"""
    if not line:
        return None
    line = str(line).strip().strip('"').strip("'")
    if not line or line.startswith('#'):
        return None
    line = line.split()[0]
    if '://' in line:
        scheme, rest = line.split('://', 1)
        scheme = scheme.lower()
        if scheme not in ('http', 'https', 'socks5', 'socks5h', 'socks4'):
            return None
        return f"{scheme}://{rest}"
    parts = line.split(':')
    if len(parts) == 2:                       # host:port
        return f"http://{parts[0]}:{parts[1]}"
    if len(parts) == 4:                       # host:port:user:pass
        return f"http://{parts[2]}:{parts[3]}@{parts[0]}:{parts[1]}"
    if '@' in line:                           # user:pass@host:port
        return f"http://{line}"
    return None


def parse_proxy_text(text):
    """从纯文本/base64 文本里解析代理列表。"""
    if not text:
        return []
    text = text.strip()
    # base64 订阅常见：整段是 base64 编码的代理行
    if text and '\n' not in text and len(text) > 24:
        try:
            decoded = base64.b64decode(text + '=' * (-len(text) % 4)).decode('utf-8', 'ignore')
            if ':' in decoded:
                text = decoded
        except Exception:
            pass
    out = []
    for line in re.split(r'[\r\n]+', text):
        p = normalize_proxy(line)
        if p:
            out.append(p)
    return out


def fetch_text(url, timeout=12):
    """拉取订阅/API 文本（强制不走系统代理，避免递归）。"""
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
    with opener.open(req, timeout=timeout) as r:
        return r.read().decode('utf-8', 'ignore')


# ---------------------------------------------------------------- 代理池
class ProxyPool:
    def __init__(self, settings=None):
        self.settings = settings or load_settings()
        self.enabled = bool(self.settings.get('enabled'))
        self.allow_direct = bool(self.settings.get('allow_direct'))
        self.max_tries = max(1, int(self.settings.get('max_tries') or 3))
        self._lock = threading.Lock()
        self._proxies = []
        self._idx = 0
        self._fail = {}          # proxy -> [连续失败数, 冷却截止时间]
        self._loaded_at = 0.0
        self.refresh()

    # ---- 载入 ----
    def refresh(self):
        source = (self.settings.get('source') or 'file').lower()
        proxies = []
        try:
            if source == 'url' and self.settings.get('url'):
                proxies = parse_proxy_text(fetch_text(self.settings['url']))
            elif source == 'api' and (self.settings.get('api') or self.settings.get('url')):
                proxies = parse_proxy_text(fetch_text(self.settings.get('api') or self.settings['url']))
            else:
                path = self.settings.get('file') or 'proxies.txt'
                if os.path.exists(path):
                    with open(path, 'r', encoding='utf-8-sig') as f:
                        proxies = parse_proxy_text(f.read())
        except Exception as e:
            print(f"代理池加载失败: {e}")
        # 去重保序
        seen, uniq = set(), []
        for p in proxies:
            if p not in seen:
                seen.add(p)
                uniq.append(p)
        with self._lock:
            self._proxies = uniq
            self._idx = 0
            self._loaded_at = time.time()
        print(f"代理池已载入 {len(uniq)} 个代理（来源: {source}）")
        return len(uniq)

    def size(self):
        with self._lock:
            return len(self._proxies)

    # ---- 取代理 ----
    def _alive(self):
        now = time.time()
        alive = []
        for p in self._proxies:
            st = self._fail.get(p)
            if st and st[1] > now:
                continue
            alive.append(p)
        return alive

    def get_candidates(self):
        """返回本次请求可依次尝试的出口列表；代理耗尽时按配置决定是否追加直连(None)。"""
        with self._lock:
            alive = self._alive()
            if not alive:
                # 全部冷却中：重新加载一次，仍没有则回退
                need_reload = time.time() - self._loaded_at > 30
            else:
                need_reload = False
            if need_reload:
                pass  # 释放锁后再刷新，避免死锁
        if need_reload:
            self.refresh()
            with self._lock:
                alive = self._alive()

        with self._lock:
            if alive:
                picks = []
                for i in range(min(self.max_tries, len(alive))):
                    picks.append(alive[(self._idx + i) % len(alive)])
                self._idx = (self._idx + len(picks)) % max(1, len(alive))
            else:
                picks = []
        # 本次请求的代理出口全部失败后，按配置回退直连（保证能下到，代价是暴露一次本机 IP）
        if self.allow_direct or not picks:
            picks.append(None)
        return picks

    def report(self, proxy, ok):
        """记录代理使用结果：成功清零，连续失败进入冷却。"""
        if not proxy:
            return
        with self._lock:
            if ok:
                self._fail.pop(proxy, None)
                return
            st = self._fail.get(proxy) or [0, 0]
            st[0] += 1
            if st[0] >= FAIL_THRESHOLD:
                st[1] = time.time() + FAIL_COOLDOWN
                st[0] = 0
                print(f"代理连续失败已冷却 {FAIL_COOLDOWN}s: {mask_proxy(proxy)}")
            self._fail[proxy] = st

    def browser_endpoint(self):
        """浏览器使用的固定出口。"""
        explicit = (self.settings.get('browser') or '').strip()
        if explicit:
            return normalize_proxy(explicit) or explicit
        with self._lock:
            alive = self._alive()
            return alive[0] if alive else None


_POOL = None
_POOL_LOCK = threading.Lock()


def get_pool(force_reload=False):
    """获取全局代理池；未启用代理时返回 None。"""
    global _POOL
    settings = load_settings()
    if not settings.get('enabled'):
        return None
    with _POOL_LOCK:
        if _POOL is None or force_reload:
            _POOL = ProxyPool(settings)
        return _POOL


def reset_pool():
    global _POOL
    with _POOL_LOCK:
        _POOL = None


def browser_proxy():
    """给浏览器用的代理地址；未启用/无可用代理返回 None。"""
    pool = get_pool()
    if not pool:
        return None
    return pool.browser_endpoint()


def write_template(path=None):
    """生成带注释的代理列表模板（已存在则不覆盖）。"""
    path = path or (load_settings().get('file') or 'proxies.txt')
    if os.path.exists(path):
        return False
    try:
        with open(path, 'w', encoding='utf-8') as f:
            f.write(
                "# 代理列表：一行一个，空行和 # 注释会被忽略\n"
                "# 支持写法：\n"
                "#   http://127.0.0.1:7890\n"
                "#   http://user:pass@1.2.3.4:8080\n"
                "#   1.2.3.4:8080\n"
                "#   1.2.3.4:8080:user:pass\n"
                "# 提示：图片下载只支持 HTTP/HTTPS 代理；socks5 仅浏览器可用\n"
            )
        return True
    except Exception as e:
        print(f"生成代理模板失败: {e}")
        return False


if __name__ == '__main__':
    s = load_settings()
    print("代理配置:", {k: (mask_proxy(v) if k in ('url', 'api', 'browser') else v) for k, v in s.items()})
    pool = get_pool()
    if pool:
        print("代理数量:", pool.size())
        print("候选:", [mask_proxy(p) for p in pool.get_candidates()])
    else:
        print("代理未启用")

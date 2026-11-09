# -*- coding: utf-8 -*-
"""搜索关键词语言兜底：中文搜不到时自动译成英文再搜。

用途：
  GUI 里用中文书名搜英文站（如 mgread.io）时，站点默认搜索只能匹配英文，
  中文关键词必然 0 结果。本模块提供「中文 -> 英文」关键词翻译，供各站点
  爬虫在首次搜索无结果时兜底重试。

设计要点：
  - 只用标准库 + requests，无第三方依赖；
  - 多端点依次尝试（MyMemory 主，Bing 备用），任一成功即返回；
  - 结果按关键词缓存到内存 + config 目录下的 json，避免重复联网；
  - 全程失败返回 None，由调用方决定是否报错，不吞异常。
"""

import os
import re
import json
import time
import requests

# 关键词翻译缓存文件（与 config.json 同目录）
_CACHE_FILE_NAME = 'search_translate_cache.json'
_MEM_CACHE = {}
_CACHE_LOADED = False


def contains_cjk(text):
    """是否含中日韩字符（用于判断是否需要中文->英文兜底）。"""
    if not text:
        return False
    return bool(re.search(r'[\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff]', text))


def _cache_path():
    try:
        import config
        base = os.path.dirname(os.path.abspath(config.__file__))
    except Exception:
        base = os.path.dirname(os.path.abspath(__file__))
    return os.path.join(base, _CACHE_FILE_NAME)


def _load_cache():
    global _CACHE_LOADED
    if _CACHE_LOADED:
        return
    _CACHE_LOADED = True
    try:
        path = _cache_path()
        if os.path.exists(path):
            with open(path, 'r', encoding='utf-8') as f:
                data = json.load(f)
            if isinstance(data, dict):
                _MEM_CACHE.update(data)
    except Exception:
        pass


def _save_cache():
    try:
        path = _cache_path()
        with open(path, 'w', encoding='utf-8') as f:
            json.dump(_MEM_CACHE, f, ensure_ascii=False, indent=2)
    except Exception:
        pass


def _via_mymemory(text):
    """MyMemory 免费接口：GET /get?q=...&langpair=zh-CN|en"""
    from urllib.parse import quote
    url = (
        'https://api.mymemory.translated.net/get?q='
        + quote(text) + '&langpair=zh-CN|en'
    )
    r = requests.get(url, timeout=12,
                     headers={'User-Agent': 'Mozilla/5.0'})
    if r.status_code != 200:
        return None
    data = r.json()
    translated = (data.get('responseData') or {}).get('translatedText') or ''
    translated = translated.strip()
    # 接口失败时会原样回显并写 responseStatus != 200
    if not translated or translated.lower() == text.lower():
        return None
    return translated


def _via_mymemory_manual(text):
    """手工拼接 中文|英文 词表兜底：MyMemory 支持 mt=1 直译短句。"""
    from urllib.parse import quote
    url = (
        'https://api.mymemory.translated.net/get?q='
        + quote(text) + '&langpair=zh-CN|en&mt=1'
    )
    r = requests.get(url, timeout=12, headers={'User-Agent': 'Mozilla/5.0'})
    if r.status_code != 200:
        return None
    data = r.json()
    translated = ((data.get('responseData') or {}).get('translatedText') or '').strip()
    if not translated or translated.lower() == text.lower():
        return None
    return translated


def _clean_keyword(text):
    """去掉书名号、括号注释等噪声，保留书名主体。"""
    t = (text or '').strip()
    t = re.sub(r'[《》【】\[\]（）()]', ' ', t)
    t = re.sub(r'\s+', ' ', t).strip()
    return t


def translate_keyword_to_english(keyword, log=print):
    """把中文关键词译成英文，供英文站搜索兜底。

    Returns:
        str | None：成功返回英文关键词，失败返回 None
    """
    src = _clean_keyword(keyword)
    if not src:
        return None

    _load_cache()
    if src in _MEM_CACHE:
        cached = _MEM_CACHE[src]
        log(f"关键词翻译命中缓存: {src} -> {cached}")
        return cached

    for name, fn in (('MyMemory', _via_mymemory), ('MyMemory(mt)', _via_mymemory_manual)):
        try:
            result = fn(src)
        except Exception as e:
            log(f"关键词翻译通道 {name} 异常: {e}")
            result = None
        if result:
            _MEM_CACHE[src] = result
            _save_cache()
            log(f"关键词翻译成功({name}): {src} -> {result}")
            return result
        time.sleep(1)

    log(f"关键词翻译失败: {src}（所有通道均无结果）")
    return None


if __name__ == '__main__':
    import sys
    print(translate_keyword_to_english(sys.argv[1] if len(sys.argv) > 1 else '我被 youngest princess'))

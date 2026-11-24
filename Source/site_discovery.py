"""
网站发现模块 - 动态加载和管理站点爬虫

站点只由 sites_data/ 目录下的 .py 文件决定：
- 导入 .py 文件 → 站点出现
- 删除 .py 文件 → 站点消失
"""
import importlib.util
import json
import os
import re
import sys
import shutil


def _get_data_dir():
    """获取数据目录（存放爬虫.py文件）"""
    if getattr(sys, 'frozen', False):
        base_dir = os.path.dirname(sys.executable)
    else:
        base_dir = os.path.dirname(os.path.abspath(__file__))
    data_dir = os.path.join(base_dir, 'sites_data')
    if not os.path.exists(data_dir):
        os.makedirs(data_dir)
    return data_dir


# 缓存: {site_name: (crawler_class, file_path)}
_sites_cache = None


def _load_crawler_from_file(file_path):
    """从文件加载爬虫类"""
    module_name = os.path.splitext(os.path.basename(file_path))[0]
    try:
        spec = importlib.util.spec_from_file_location(module_name, file_path)
        if spec is None:
            print(f"无法加载模块: {file_path}")
            return None
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        for attr_name in dir(module):
            if attr_name.endswith('Crawler'):
                crawler_class = getattr(module, attr_name)
                if hasattr(crawler_class, 'SITE_NAME'):
                    return crawler_class
    except Exception as e:
        print(f"加载 {file_path} 失败: {e}")
    return None


def discover_sites():
    """从 sites_data/ 目录扫描所有 .py 文件加载站点"""
    global _sites_cache
    if _sites_cache is not None:
        return {name: cls for name, (cls, _) in _sites_cache.items()}

    sites = {}
    _sites_cache = {}
    data_dir = _get_data_dir()

    # 扫描目录下的所有 *_crawler.py 文件
    if os.path.exists(data_dir):
        for filename in os.listdir(data_dir):
            if filename.endswith('_crawler.py'):
                file_path = os.path.join(data_dir, filename)
                crawler_class = _load_crawler_from_file(file_path)
                if crawler_class:
                    site_name = crawler_class.SITE_NAME
                    sites[site_name] = crawler_class
                    _sites_cache[site_name] = (crawler_class, file_path)

    # 自定义站点适配器（自定义站点/ 目录下的 *_adapter.json）
    custom_dir = get_custom_sites_dir()
    rule_cls = None
    if os.path.exists(custom_dir):
        for filename in sorted(os.listdir(custom_dir)):
            if filename.endswith('_adapter.json') or filename.endswith('_适配器.json'):
                file_path = os.path.join(custom_dir, filename)
                try:
                    with open(file_path, 'r', encoding='utf-8') as f:
                        adapter = json.load(f)
                    validate_adapter(adapter)
                    if rule_cls is None:
                        rule_cls = _load_rule_crawler_class()
                    crawler_class = _make_rule_site_class(adapter, rule_cls)
                    site_name = crawler_class.SITE_NAME
                    if site_name in sites:
                        print(f"自定义站点 {site_name} 与内置站点重名，已跳过: {filename}")
                        continue
                    sites[site_name] = crawler_class
                    _sites_cache[site_name] = (crawler_class, file_path)
                except Exception as e:
                    print(f"加载自定义站点 {filename} 失败: {e}")

    return sites


def refresh_sites():
    """清除缓存，重新扫描站点"""
    global _sites_cache
    _sites_cache = None
    return discover_sites()


def get_site_crawler_class(site_name):
    """获取指定网站的爬虫类"""
    sites = discover_sites()
    if site_name not in sites:
        available = ', '.join(sites.keys()) if sites else '无'
        raise ValueError(f"不支持的站点: {site_name}。可用站点: {available}")
    return sites[site_name]


def get_all_site_names():
    """获取所有可用网站名称列表"""
    return list(discover_sites().keys())


def get_sites_requiring_login():
    """获取需要登录的网站列表"""
    sites = discover_sites()
    return [name for name, cls in sites.items()
            if getattr(cls, 'REQUIRES_LOGIN', False)]


def _site_supports_cookie_input(cls):
    """站点是否需要Cookie输入：优先读SUPPORTS_COOKIE_INPUT声明，
    未声明时默认等于REQUIRES_LOGIN（需登录的站点才需要Cookie）"""
    if hasattr(cls, 'SUPPORTS_COOKIE_INPUT'):
        return bool(cls.SUPPORTS_COOKIE_INPUT)
    return getattr(cls, 'REQUIRES_LOGIN', False)


def get_sites_supporting_cookie():
    """获取支持Cookie输入的网站列表"""
    sites = discover_sites()
    return [name for name, cls in sites.items()
            if _site_supports_cookie_input(cls)]


def site_supports_cookie(site_name):
    """指定站点是否需要Cookie输入"""
    try:
        cls = get_site_crawler_class(site_name)
    except ValueError:
        return False
    return _site_supports_cookie_input(cls)


def get_site_config(site_name):
    """获取指定网站的配置信息"""
    crawler_class = get_site_crawler_class(site_name)
    return getattr(crawler_class, 'CONFIG', {})


def get_site_download_mode(site_name):
    """获取指定网站的默认下载模式"""
    config = get_site_config(site_name)
    return config.get('download_mode', 'coroutine')


def get_site_file_path(site_name):
    """获取站点文件路径"""
    discover_sites()
    if _sites_cache and site_name in _sites_cache:
        return _sites_cache[site_name][1]
    return None


def get_all_sites_info():
    """获取所有站点详细信息"""
    discover_sites()
    info = []
    if _sites_cache:
        for name, (cls, path) in _sites_cache.items():
            info.append({
                'name': name,
                'file': os.path.basename(path),
                'file_path': path,
                'requires_login': getattr(cls, 'REQUIRES_LOGIN', False),
                'site_url': getattr(cls, 'SITE_URL', ''),
                'is_rule': getattr(cls, 'ADAPTER', None) is not None,
            })
    return info


def _get_unique_filename(data_dir, filename):
    """生成不冲突的文件名"""
    target = os.path.join(data_dir, filename)
    if not os.path.exists(target):
        return filename
    base, ext = os.path.splitext(filename)
    counter = 1
    while True:
        new_filename = f"{base}_{counter}{ext}"
        if not os.path.exists(os.path.join(data_dir, new_filename)):
            return new_filename
        counter += 1


def add_site_file(src_path):
    """
    添加站点文件，复制到sites_data/，重复站点自动过滤

    Returns:
        str: 添加的站点名称

    Raises:
        ValueError: 站点已存在(自动过滤)或文件无效
    """
    if not os.path.isfile(src_path):
        raise ValueError(f"文件不存在: {src_path}")
    if not src_path.endswith('.py'):
        raise ValueError("站点文件必须是.py文件")

    # 加载验证
    crawler_class = _load_crawler_from_file(src_path)
    if not crawler_class:
        raise ValueError("文件中未找到有效的Crawler类（需要以Crawler结尾的类名且包含SITE_NAME属性）")

    site_name = crawler_class.SITE_NAME

    # 自动过滤重复
    existing_sites = discover_sites()
    if site_name in existing_sites:
        raise ValueError(f"站点 '{site_name}' 已存在，已自动过滤")

    # 读取源代码
    with open(src_path, 'r', encoding='utf-8') as f:
        code = f.read()

    # 复制文件到数据目录（自动处理文件名冲突）
    data_dir = _get_data_dir()
    filename = _get_unique_filename(data_dir, os.path.basename(src_path))
    dst_path = os.path.join(data_dir, filename)

    with open(dst_path, 'w', encoding='utf-8') as f:
        f.write(code)

    refresh_sites()
    return site_name


def add_site_folder(folder_path):
    """
    从文件夹添加所有站点文件，重复站点自动过滤

    Returns:
        (added, errors, skipped):
            added - 成功添加列表 [(file, site_name)]
            errors - 失败列表 [(file, error)]
            skipped - 重复过滤列表 [(file, reason)]
    """
    if not os.path.isdir(folder_path):
        raise ValueError(f"文件夹不存在: {folder_path}")

    added = []
    errors = []
    skipped = []

    for file in os.listdir(folder_path):
        if file.endswith('_crawler.py'):
            src_path = os.path.join(folder_path, file)
            try:
                site_name = add_site_file(src_path)
                added.append((file, site_name))
            except ValueError as e:
                err_msg = str(e)
                if '已存在' in err_msg:
                    skipped.append((file, err_msg))
                else:
                    errors.append((file, err_msg))

    return added, errors, skipped


def remove_site(site_name):
    """删除站点（删除.py文件）"""
    discover_sites()
    if site_name not in _sites_cache:
        raise ValueError(f"未找到站点: {site_name}")

    file_path = _sites_cache[site_name][1]

    if os.path.exists(file_path):
        os.remove(file_path)

    # 清理pycache
    pycache_dir = os.path.join(_get_data_dir(), '__pycache__')
    if os.path.exists(pycache_dir):
        base_name = os.path.splitext(os.path.basename(file_path))[0]
        for pyc_file in os.listdir(pycache_dir):
            if pyc_file.startswith(base_name):
                try:
                    os.remove(os.path.join(pycache_dir, pyc_file))
                except:
                    pass

    refresh_sites()
    return True

# ============ 自定义站点适配器（JSON）支持 ============

def get_custom_sites_dir():
    """自定义站点适配器目录：程序目录/自定义站点"""
    if getattr(sys, 'frozen', False):
        base_dir = os.path.dirname(sys.executable)
    else:
        base_dir = os.path.dirname(os.path.abspath(__file__))
    custom_dir = os.path.join(base_dir, '自定义站点')
    if not os.path.exists(custom_dir):
        os.makedirs(custom_dir)
    return custom_dir


def _load_rule_crawler_class():
    """加载通用规则爬虫类（sites_data/rule_crawler.py）"""
    rule_path = os.path.join(_get_data_dir(), 'rule_crawler.py')
    module_name = 'rule_crawler'
    spec = importlib.util.spec_from_file_location(module_name, rule_path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.RuleSiteCrawler


def validate_adapter(adapter):
    """校验适配器结构是否完整，不合法抛出 ValueError"""
    if not isinstance(adapter, dict):
        raise ValueError("适配器必须是JSON对象")
    if not adapter.get('site_name'):
        raise ValueError("缺少「站点名称」")
    if not adapter.get('site_url'):
        raise ValueError("缺少「网站地址」")
    search = adapter.get('search') or {}
    if not search.get('result_js'):
        raise ValueError("缺少「搜索结果JS」(search.result_js)")
    if search.get('mode', 'get') == 'post_fetch' and not search.get('post_path'):
        raise ValueError("接口POST搜索模式需要填写「接口路径」(search.post_path)")
    detail = adapter.get('detail') or {}
    if not detail.get('chapters_js'):
        raise ValueError("缺少「章节列表JS」(detail.chapters_js)")
    chapter = adapter.get('chapter') or {}
    if not chapter.get('images_js'):
        raise ValueError("缺少「章节图片JS」(chapter.images_js)")


def _make_rule_site_class(adapter, rule_cls):
    """根据适配器JSON生成一个站点爬虫类"""
    site_name = adapter['site_name']
    site_url = adapter.get('site_url', '')

    class RuleAdapterSite(rule_cls):
        SITE_NAME = site_name
        SITE_URL = site_url
        CONFIG = {
            'site_url': site_url,
            'locators': {},
            'image_attr': adapter.get('image_attr', 'src'),
        }
        ADAPTER = adapter

    RuleAdapterSite.__name__ = 'RuleSite_' + re.sub(r'\W+', '_', site_name)
    return RuleAdapterSite


def is_rule_site(site_name):
    """是否为适配器（JSON）站点"""
    discover_sites()
    if _sites_cache and site_name in _sites_cache:
        cls = _sites_cache[site_name][0]
        return getattr(cls, 'ADAPTER', None) is not None
    return False


def get_adapter_by_site_name(site_name):
    """按站点名取回适配器JSON（仅适配器站点）"""
    discover_sites()
    if _sites_cache and site_name in _sites_cache:
        cls = _sites_cache[site_name][0]
        return getattr(cls, 'ADAPTER', None)
    return None


def save_custom_adapter(adapter):
    """保存/更新自定义站点适配器到 自定义站点/ 目录，返回站点名"""
    validate_adapter(adapter)
    site_name = adapter['site_name']
    custom_dir = get_custom_sites_dir()
    safe = re.sub(r'[\\/:*?"<>|]+', '_', site_name)
    file_path = os.path.join(custom_dir, f"{safe}_adapter.json")
    with open(file_path, 'w', encoding='utf-8') as f:
        json.dump(adapter, f, ensure_ascii=False, indent=2)
    refresh_sites()
    return site_name


def delete_custom_adapter(site_name):
    """删除自定义站点适配器（仅适配器站点）"""
    discover_sites()
    if _sites_cache and site_name in _sites_cache:
        cls, file_path = _sites_cache[site_name]
        if getattr(cls, 'ADAPTER', None) is None:
            raise ValueError(f"「{site_name}」是内置站点，请用「删除站点」处理")
        if os.path.exists(file_path):
            os.remove(file_path)
        refresh_sites()
        return True
    raise ValueError(f"未找到站点: {site_name}")
import os
import sys
import zipfile

def parse_chapter_expression(text):
    """解析章节表达式 -> 章节号集合，或 None 表示不限（all/留空）

    支持：1,3,5-20 / 1-5,8 / 全角逗号、顿号、空格分隔 / all
    非法输入抛 ValueError。GUI 与 CLI 共用。
    """
    raw = str(text or '').strip()
    if not raw or raw.lower() == 'all' or raw == '*':
        return None
    normalized = (raw.replace('，', ',').replace('、', ',')
                  .replace('～', '-').replace('~', '-').replace('—', '-'))
    nums = set()
    for part in normalized.split(','):
        token = part.strip()
        if not token:
            continue
        try:
            if '-' in token:
                a, b = token.split('-', 1)
                start, end = int(a.strip()), int(b.strip())
                if start > end:
                    start, end = end, start
                if start < 1:
                    raise ValueError(f'章节号必须≥1：{token}')
                nums.update(range(start, end + 1))
            else:
                n = int(token)
                if n < 1:
                    raise ValueError(f'章节号必须≥1：{token}')
                nums.add(n)
        except ValueError:
            raise ValueError(f'无法识别的章节表达式片段：{token}')
    if not nums:
        raise ValueError('章节表达式为空')
    return nums


def ensure_console_safe():
    """入口加固：
    1. GBK控制台下打印✓/⚠等非GBK字符会抛UnicodeEncodeError导致下载中断，
       统一设置 errors='replace' 避免崩溃；
    2. 无控制台打包环境(console=False)中 stdout/stderr 为None，print会抛AttributeError，
       用空写入器兜底。
    应在程序入口(gui.py/main.py)最开头调用。
    """
    class _NullWriter:
        def write(self, s):
            pass
        def flush(self):
            pass

    if sys.stdout is None:
        sys.stdout = _NullWriter()
    if sys.stderr is None:
        sys.stderr = sys.stdout
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, 'reconfigure', None)
        if reconfigure:
            try:
                reconfigure(errors='replace')
            except Exception:
                pass


def is_normal_url(url):
    """检查URL是否有效"""
    return url and ('http' in url or 'https' in url)


def _safe_name(name):
    """目录/压缩包名安全化（与 downloader.comic_folder_name 保持一致）"""
    import re
    s = re.sub(r'[\\/:*?"<>|\r\n\t]', ' ', str(name))
    s = re.sub(r'\s+', ' ', s).strip(' .')
    return s[:85] or '未命名'


def zip_main_folder(comic_name, base_path=None):
    """压缩主文件夹"""
    import shutil
    
    if base_path is None:
        base_path = os.getcwd()
    
    safe = _safe_name(comic_name)
    main_folder = os.path.join(base_path, safe)
    zip_file = os.path.join(base_path, f"{safe}.zip")
    
    print(f"\n正在压缩文件夹: {main_folder}")
    
    with zipfile.ZipFile(zip_file, 'w', zipfile.ZIP_DEFLATED) as zipf:
        for root, dirs, files in os.walk(main_folder):
            for file in files:
                file_path = os.path.join(root, file)
                arcname = os.path.relpath(file_path, base_path)
                zipf.write(file_path, arcname)
    
    print(f"压缩完成: {zip_file}")
    
    print(f"已保留原文件夹: {main_folder}")


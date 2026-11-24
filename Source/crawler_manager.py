# -*- coding: utf-8 -*-
"""爬虫源码管理器 —— 专门用来添加/编辑你自己喜欢的网站的爬虫源码。

窗口功能：
  - 左侧列出 sites_data/ 下所有爬虫文件（显示站点名）
  - 右侧编辑源码（自带高亮友好的编辑区）
  - 「从模板新建」：一键生成带注释的新爬虫骨架，改几处就能用
  - 保存后自动校验（必须有 class XxxCrawler + SITE_NAME），写入 sites_data/ 并刷新站点列表
  - 支持删除、打开目录、参考完整模板（yumanhua_crawler.py）
"""

import os
import re
import sys
import tkinter as tk
from tkinter import ttk, messagebox, filedialog


def _sites_dir():
    """站点目录：exe打包后取 exe 所在目录/sites_data，源码运行取本文件同级/sites_data"""
    if getattr(sys, 'frozen', False):
        return os.path.join(os.path.dirname(sys.executable), 'sites_data')
    return os.path.join(os.path.dirname(os.path.abspath(__file__)), 'sites_data')


SITES_DIR = _sites_dir()

# ============ 简化模板（新手入口，带详细注释） ============
SIMPLE_TEMPLATE = '''import time
import threading
from utils import is_normal_url


class XxxCrawler:
    """新站点爬虫 —— 把 Xxx 换成你的站点名（如 ManhuaCrawler）

    使用步骤（配合《如何添加自定义爬虫.md》教程）：
      1) 改 SITE_NAME / SITE_URL / REQUIRES_LOGIN
      2) 改 CONFIG.locators 里的三个 XPath 定位器（用浏览器F12查看真实元素）
      3) 改 search_comic 里的搜索 URL
      4) 改 get_chapter_image_urls 里的图片容器/属性（最关键）
      5) 保存后刷新站点列表即可使用
    """

    # ===== 站点元数据（必改） =====
    SITE_NAME = '新站点'                      # 站点显示名（下拉框里看到的）
    SITE_URL = 'https://example.com/'          # 站点网址（必改）
    REQUIRES_LOGIN = False                     # 需要登录则填 True

    # ===== 站点配置（必改：定位器 + 图片属性） =====
    CONFIG = {
        'site_url': 'https://example.com/',
        'locators': {
            # 搜索结果里第一条漫画链接（浏览器F12复制XPath）
            'search_result': 'xpath:/html/body/div[1]/a[1]',
            # 详情页封面图
            'cover_image': 'xpath://div[contains(@class,"cover")]//img',
            # 章节列表项（每一项是一个<a>或<li>）
            'chapter_item': 'xpath://ul[contains(@class,"chapter")]/li',
        },
        'image_attr': 'data-src',   # 图片URL属性：常见 src / data-src / data-original
        'chapter_group_size': None,
        'image_referer': 'https://example.com/',   # 图片防盗链：填站点网址（可留空）
    }

    def __init__(self, crawler):
        self.crawler = crawler
        self.locators = crawler.locators
        self.image_attr = crawler.image_attr

    # ---------- 搜索（必改：换成站点真实的搜索地址） ----------
    def search_comic(self, comic_name, comic_id=None):
        # 有漫画ID时直接打开详情页（很多站点ID=数字或字母）
        if comic_id:
            url = comic_id if comic_id.startswith('http') else self.SITE_URL + comic_id + '/'
            print(f"按ID打开详情页: {url}")
            return self.crawler.page.new_tab(url)

        # 搜索URL示例：请改成目标站点的真实搜索格式（F12观察搜索时跳转的网址）
        search_url = self.SITE_URL + 'search?keyword=' + comic_name
        print(f"搜索: {search_url}")
        self.crawler.tab.get(search_url)
        time.sleep(3)
        try:
            ele = self.crawler.tab.ele(self.locators['search_result'], timeout=10)
            href = ele.attr('href') or ''
            if href and not href.startswith('http'):
                href = self.SITE_URL + href.lstrip('/')
            print(f"搜索结果: {href}")
            return self.crawler.page.new_tab(href)
        except Exception as e:
            raise Exception(f"搜索失败: {e}（请检查 search_result 定位器）")

    # ---------- 章节数量 ----------
    def get_chapter_count(self, target_comic_tab):
        divs = target_comic_tab.eles(self.locators['chapter_item'], timeout=10)
        print(f"检测到 {len(divs)} 个章节")
        return len(divs)

    # ---------- 章节表（含章节名，新版GUI用） ----------
    def get_chapter_list(self, target_comic_tab):
        divs = target_comic_tab.eles(self.locators['chapter_item'], timeout=10)
        chapters = []
        for i, d in enumerate(divs, 1):
            try:
                a = d if str(d.tag).lower() == 'a' else d.ele('tag:a')
                href = a.attr('href') or ''
                title = (a.text or d.text or '').strip()
            except Exception:
                href = ''
                title = ''
            if href and not href.startswith('http'):
                href = self.SITE_URL + href.lstrip('/')
            chapters.append({'num': i, 'url': href, 'title': title or f'第{i}话'})
        return chapters

    # ---------- 封面 ----------
    def get_cover_image(self, target_comic_tab):
        try:
            img = target_comic_tab.ele(self.locators['cover_image'], timeout=10)
            return img.attr('src') or img.attr('data-src')
        except Exception:
            return None

    # ---------- 章节图片（最关键，必改） ----------
    def get_chapter_image_urls(self, chapter_tab):
        # 懒加载站点（图片初始是占位图）需要先滚动到底，参考 yumanhua_crawler.py
        # 收集章节页里的真图：请把 'img' 和 data-src 改成站点真实结构
        urls = chapter_tab.run_js(
            "return Array.from(document.querySelectorAll('img'))"
            ".map(function(i){var d=i.getAttribute('data-src')||'';var s=i.getAttribute('src')||'';return d||s;})"
            ".filter(function(u){return u && u.indexOf('http')===0 && u.indexOf('logo')<0;})"
        ) or []
        herf_list = [u for u in urls if is_normal_url(u)]
        print(f"共提取 {len(herf_list)} 张图片")
        return herf_list

    # ---------- 单章图片收集 ----------
    def collect_chapter_images(self, chapter_info, max_wait_time=5):
        chapter_num = chapter_info['chapter_num']
        chapter_url = chapter_info['url']
        main_tab = chapter_info['main_tab']
        print(f"正在处理章节{chapter_num}: {chapter_url}")
        try:
            chapter_tab = main_tab.new_tab(chapter_url)
            time.sleep(2)
            herf_list = self.get_chapter_image_urls(chapter_tab)
            chapter_tab.close()
        except Exception as e:
            print(f"处理章节{chapter_num}出错: {e}")
            herf_list = []
        return {'chapter_num': chapter_num, 'title': chapter_info.get('title', ''), 'herf_list': herf_list}

    # ---------- 批量收集（一般不用改） ----------
    def collect_chapters_images(self, target_comic_tab, chapter_start=1, chapter_end=0,
                                max_threads=3, progress_callback=None):
        chapter_urls = self.get_chapter_list(target_comic_tab)
        all_num = len(chapter_urls)
        print(f"总章节数: {all_num}")
        if all_num == 0:
            return []
        start = max(chapter_start, 1)
        end = min(chapter_end, all_num) if chapter_end > 0 else all_num
        all_data = []
        cur = start
        while cur <= end:
            group_end = min(cur + max_threads - 1, end)
            batch = [
                {'chapter_num': n, 'url': chapter_urls[n - 1]['url'],
                 'title': chapter_urls[n - 1].get('title', ''), 'main_tab': self.crawler.tab}
                for n in range(cur, group_end + 1)
            ]
            results = []
            threads = []

            def wrapper(info):
                results.append(self.collect_chapter_images(info))

            for info in batch:
                t = threading.Thread(target=wrapper, args=(info,))
                threads.append(t)
                t.start()
            for t in threads:
                t.join()
            all_data.extend(results)
            for _ in results:
                if progress_callback:
                    progress_callback()
            cur = group_end + 1
        return all_data
'''


class CrawlerManagerWindow:
    """爬虫源码管理器窗口"""

    def __init__(self, master, on_refresh=None):
        self.on_refresh = on_refresh  # 回调：保存成功后通知主界面刷新站点列表

        self.win = tk.Toplevel(master)
        self.win.title("爬虫源码管理器 - 添加你喜欢的网站")
        self.win.geometry("980x640")
        self.win.configure(bg="#f5f6f8")

        # ===== 顶部说明 =====
        top = ttk.Frame(self.win)
        top.pack(fill=tk.X, padx=8, pady=(8, 4))
        ttk.Label(top, text="在这里添加/编辑你自己的漫画网站爬虫源码（存到 sites_data/ 即可被主界面加载）",
                  font=("微软雅黑", 10)).pack(side=tk.LEFT)
        ttk.Button(top, text="📖 使用教程", command=self.show_help,
                   width=12).pack(side=tk.RIGHT, padx=2)

        # ===== 主体：左列表 + 右编辑器 =====
        body = ttk.Frame(self.win)
        body.pack(fill=tk.BOTH, expand=True, padx=8, pady=4)

        # -- 左侧：文件列表 --
        left = ttk.Frame(body)
        left.pack(side=tk.LEFT, fill=tk.Y, padx=(0, 6))
        ttk.Label(left, text="已有爬虫（站点）:").pack(anchor=tk.W)
        self.listbox = tk.Listbox(left, width=30, height=26, font=("微软雅黑", 9))
        self.listbox.pack(fill=tk.BOTH, expand=True, pady=2)
        self.listbox.bind('<<ListboxSelect>>', self._on_select)

        left_btns = ttk.Frame(left)
        left_btns.pack(fill=tk.X)
        ttk.Button(left_btns, text="刷新列表", command=self._scan_files, width=9).pack(side=tk.LEFT, padx=1)
        ttk.Button(left_btns, text="删除选中", command=self._delete_file, width=9).pack(side=tk.LEFT, padx=1)
        ttk.Button(left_btns, text="打开目录", command=self._open_dir, width=9).pack(side=tk.LEFT, padx=1)

        # -- 右侧：元信息 + 编辑器 --
        right = ttk.Frame(body)
        right.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

        meta = ttk.Frame(right)
        meta.pack(fill=tk.X, pady=(0, 4))
        ttk.Label(meta, text="文件名: ").pack(side=tk.LEFT)
        self.file_var = tk.StringVar(value="")
        ttk.Label(meta, textvariable=self.file_var, font=("微软雅黑", 9), foreground="#1a73e8").pack(side=tk.LEFT)
        ttk.Label(meta, text="    站点名: ").pack(side=tk.LEFT)
        self.name_var = tk.StringVar(value="")
        ttk.Entry(meta, textvariable=self.name_var, width=14).pack(side=tk.LEFT)
        ttk.Label(meta, text="    需要登录: ").pack(side=tk.LEFT)
        self.login_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(meta, variable=self.login_var).pack(side=tk.LEFT)

        self.editor = tk.Text(right, wrap=tk.NONE, font=("Consolas", 10),
                              bg="#1e1f22", fg="#e6e6e6", insertbackground="white",
                              undo=True)
        self.editor.pack(fill=tk.BOTH, expand=True)

        # ===== 底部按钮 =====
        bottom = ttk.Frame(self.win)
        bottom.pack(fill=tk.X, padx=8, pady=6)
        ttk.Button(bottom, text="从模板新建", command=self._new_from_template,
                   width=14).pack(side=tk.LEFT, padx=2)
        ttk.Button(bottom, text="加载参考模板(yumanhua)", command=self._load_reference,
                   width=24).pack(side=tk.LEFT, padx=2)
        ttk.Button(bottom, text="保存到 sites_data", command=self._save_file,
                   width=18).pack(side=tk.RIGHT, padx=2)
        ttk.Button(bottom, text="关闭", command=self.win.destroy,
                   width=8).pack(side=tk.RIGHT, padx=2)

        self._scan_files()

    # ---------- 扫描 sites_data ----------
    def _scan_files(self):
        self.listbox.delete(0, tk.END)
        self._files = []
        os.makedirs(SITES_DIR, exist_ok=True)
        for f in sorted(os.listdir(SITES_DIR)):
            if f.endswith('_crawler.py'):
                self._files.append(f)
                self.listbox.insert(tk.END, f.replace('_crawler.py', ''))
        if not self._files:
            self.listbox.insert(tk.END, "(空) 点「从模板新建」开始")

    def _on_select(self, event):
        sel = self.listbox.curselection()
        if not sel:
            return
        idx = sel[0]
        if idx >= len(self._files):
            return
        self._load_file(self._files[idx])

    def _load_file(self, filename):
        path = os.path.join(SITES_DIR, filename)
        try:
            with open(path, encoding='utf-8') as f:
                code = f.read()
        except Exception:
            with open(path, encoding='gbk', errors='replace') as f:
                code = f.read()
        self.file_var.set(filename)
        self.editor.delete('1.0', tk.END)
        self.editor.insert('1.0', code)
        # 解析站点名
        m = re.search(r'SITE_NAME\s*=\s*[\'"]([^\'"]+)[\'"]', code)
        self.name_var.set(m.group(1) if m else '')
        m2 = re.search(r'REQUIRES_LOGIN\s*=\s*(True|False)', code)
        self.login_var.set((m2 and m2.group(1) == 'True') or False)

    # ---------- 新建模板 ----------
    def _new_from_template(self):
        self.editor.delete('1.0', tk.END)
        self.editor.insert('1.0', SIMPLE_TEMPLATE)
        self.file_var.set("新站点_crawler.py")
        self.name_var.set("新站点")
        self.login_var.set(False)
        messagebox.showinfo("从模板新建",
                            "已生成新爬虫骨架。\n按注释把 站点名/网址/定位器/图片属性 改成目标站点，\n点「保存到 sites_data」。\n详细步骤见《如何添加自定义爬虫.md》")

    def _load_reference(self):
        """加载 yumanhua 完整爬虫作参考（只读到编辑器，可对照改写）"""
        ref = os.path.join(SITES_DIR, 'yumanhua_crawler.py')
        if not os.path.isfile(ref):
            messagebox.showwarning("提示", "参考文件 sites_data/yumanhua_crawler.py 不存在")
            return
        with open(ref, encoding='utf-8') as f:
            code = f.read()
        self.editor.delete('1.0', tk.END)
        self.editor.insert('1.0', code)
        self.file_var.set("参考_yumanhua_crawler.py（仅参考，别直接保存同名）")
        messagebox.showinfo("参考模板",
                            "已加载 yumanhua 完整爬虫供对照。\n它演示了：AJAX搜索、懒加载滚动、真图容器过滤等完整写法。\n对照它改你的新爬虫，再「另存为」新文件名。")

    # ---------- 保存 ----------
    def _save_file(self):
        code = self.editor.get('1.0', tk.END).strip()
        if not code:
            messagebox.showwarning("保存", "源码为空")
            return
        # 校验：必须有 class ...Crawler
        m = re.search(r'class\s+(\w+)Crawler\s*:', code)
        if not m:
            messagebox.showwarning("保存失败", "源码里找不到 class XxxCrawler:（类名以 Crawler 结尾）")
            return
        # 文件名
        fname = self.file_var.get().strip()
        if not fname or fname.startswith('参考') or '(空)' in fname:
            fname = m.group(1) + '_crawler.py'
        if not fname.endswith('.py'):
            fname += '.py'
        if '新站点' in fname or fname == '_crawler.py':
            fname = m.group(1) + '_crawler.py'
        path = os.path.join(SITES_DIR, fname)
        if os.path.isfile(path) and not messagebox.askyesno("确认覆盖", f"文件已存在：{fname}\n是否覆盖？"):
            return
        try:
            with open(path, 'w', encoding='utf-8') as f:
                f.write(code)
        except Exception as e:
            messagebox.showerror("保存失败", f"写入失败: {e}")
            return
        # 校验能否被加载（语法）
        import py_compile
        try:
            py_compile.compile(path, doraise=True)
        except Exception as e:
            messagebox.showwarning("语法警告",
                                   f"文件已保存，但语法检查未通过（不影响查看，但主界面可能加载失败）：\n{e}")
        # 刷新站点缓存 + 通知主界面
        try:
            from site_discovery import refresh_sites
            refresh_sites()
        except Exception:
            pass
        if self.on_refresh:
            try:
                self.on_refresh()
            except Exception:
                pass
        self._scan_files()
        messagebox.showinfo("保存成功", f"已保存: {fname}\n站点列表已刷新，回主界面即可选择「{self.name_var.get()}」")

    # ---------- 删除 ----------
    def _delete_file(self):
        sel = self.listbox.curselection()
        if not sel or sel[0] >= len(self._files):
            messagebox.showinfo("删除", "请先选中要删除的爬虫")
            return
        fname = self._files[sel[0]]
        if not messagebox.askyesno("确认删除", f"确定删除 {fname} 吗？（不可恢复）"):
            return
        try:
            os.remove(os.path.join(SITES_DIR, fname))
        except Exception as e:
            messagebox.showerror("删除失败", str(e))
            return
        try:
            from site_discovery import refresh_sites
            refresh_sites()
        except Exception:
            pass
        if self.on_refresh:
            try:
                self.on_refresh()
            except Exception:
                pass
        self._scan_files()
        messagebox.showinfo("已删除", f"已删除 {fname}")

    def _open_dir(self):
        os.makedirs(SITES_DIR, exist_ok=True)
        os.startfile(SITES_DIR)

    # ---------- 使用教程弹窗 ----------
    def show_help(self):
        help_text = (
            "【如何添加你自己的网站爬虫】\n\n"
            "1. 点「从模板新建」→ 生成带注释的骨架\n"
            "2. 依次改这几处（F12 打开目标站点开发者工具看真实结构）：\n"
            "   · SITE_NAME / SITE_URL —— 站点名和网址\n"
            "   · CONFIG.locators —— 搜索/封面/章节的 XPath 定位器\n"
            "   · search_comic —— 站点真实的搜索地址\n"
            "   · image_attr + get_chapter_image_urls —— 图片属性和容器（最关键）\n"
            "3. 点「保存到 sites_data」→ 自动校验并刷新站点列表\n"
            "4. 回主界面选新站点，搜索→加载章节表→下载\n\n"
            "不会写代码？用「自动分析站点」四步向导，生成 JSON 适配器更简单\n"
            "（适配器=描述规则，无需编程）。详细图文教程见《如何添加自定义爬虫.md》"
        )
        messagebox.showinfo("使用教程", help_text)

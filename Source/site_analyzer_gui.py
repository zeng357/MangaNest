# -*- coding: utf-8 -*-
"""
站点自动分析向导（GUI）
======================
输入一个漫画网站地址 → 自动分析 搜索/章节/图片 → 生成并保存适配器。
适配器保存在 自定义站点/ 目录，永久有效，随时可下载。
"""
import threading
import time
import tkinter as tk
from tkinter import ttk, messagebox

from site_analyzer import (open_browser, detect_search_input, build_search_url,
                           build_search_url_candidates, looks_like_search_results,
                           submit_search, collect_links, pick_top,
                           score_detail, score_chapter, CHAPTER_TITLE_RE,
                           judge_chapter_links, build_href_regex, detect_order,
                           scroll_incremental, scroll_to_bottom, click_more_buttons,
                           collect_with_paging, wait_page_has_links, IMG_INFO_JS, analyze_images,
                           build_images_js, build_links_js, build_chapters_js)


class SiteAnalyzerDialog:
    """四步向导：①搜索识别 ②章节识别 ③图片识别 ④保存适配器"""

    def __init__(self, parent, browser_path, headless, comic_name_hint='', on_save=None):
        self.browser_path = browser_path
        # 分析必须拿到完整页面（无头模式会被部分网站阉割内容），强制有头
        self.headless = False
        self.comic_name_hint = (comic_name_hint or '').strip() or '狐妖'
        self.on_save = on_save

        self.page = None
        self.site_url = ''
        self.search_items = []
        self.search_url_template = ''
        self.detail_url = ''
        self.chapter_links = []      # 打分后的章节链接样本
        self.chapter_regex = None
        self.chapter_count = 0
        self.reverse = False
        self.first_chapter_url = ''
        self.img_result = None
        self._busy = False

        win = tk.Toplevel(parent)
        win.title("自动分析新网站 - 站点适配器向导")
        win.geometry("760x620")
        win.transient(parent)
        win.grab_set()
        self.win = win
        win.protocol("WM_DELETE_WINDOW", self._on_close)

        nb = ttk.Notebook(win)
        nb.pack(fill="both", expand=True, padx=8, pady=8)
        self.nb = nb

        # ---------- Tab1 搜索识别 ----------
        f1 = ttk.Frame(nb, padding=10)
        nb.add(f1, text="① 搜索识别")
        ttk.Label(f1, text="1. 输入漫画网站地址（主页或搜索页，如 https://xxx.com/）").pack(anchor="w")
        r1 = ttk.Frame(f1); r1.pack(fill="x", pady=4)
        self.url_var = tk.StringVar()
        ttk.Entry(r1, textvariable=self.url_var, width=56).pack(side=tk.LEFT, fill="x", expand=True)
        self.btn_open = ttk.Button(r1, text="打开并分析", command=self._open_analyze, width=12)
        self.btn_open.pack(side=tk.LEFT, padx=4)

        ttk.Label(f1, text="检测到的搜索框（自动识别）：").pack(anchor="w", pady=(8, 0))
        self.search_list = tk.Listbox(f1, height=3, font=("微软雅黑", 9))
        self.search_list.pack(fill="x", pady=4)

        ttk.Label(f1, text="2. 自动用漫画名试搜（漫画名：）").pack(anchor="w")
        r2 = ttk.Frame(f1); r2.pack(fill="x", pady=4)
        self.kw_var = tk.StringVar(value=self.comic_name_hint)
        ttk.Entry(r2, textvariable=self.kw_var, width=20).pack(side=tk.LEFT)
        self.btn_search = ttk.Button(r2, text="开始试搜", command=self._do_search, width=12)
        self.btn_search.pack(side=tk.LEFT, padx=4)

        ttk.Label(f1, text="搜索到的漫画（选一个当详情页，双击选中）：").pack(anchor="w", pady=(8, 0))
        self.result_list = tk.Listbox(f1, height=6, font=("微软雅黑", 9))
        self.result_list.pack(fill="both", expand=True, pady=4)
        self.result_list.bind("<Double-Button-1>", lambda e: self._select_detail())
        self.result_list.bind("<<ListboxSelect>>", lambda e: self._on_result_click())
        ttk.Button(f1, text="选中并进入② 分析章节 →", command=self._select_detail).pack(anchor="e")

        self.log1 = ttk.Label(f1, text="", foreground="#909399", wraplength=680)
        self.log1.pack(anchor="w", pady=(6, 0))

        # ---------- Tab2 章节识别 ----------
        f2 = ttk.Frame(nb, padding=10)
        nb.add(f2, text="② 章节识别")
        ttk.Label(f2, text="3. 详情页地址：").pack(anchor="w")
        r3 = ttk.Frame(f2); r3.pack(fill="x", pady=4)
        self.detail_var = tk.StringVar()
        ttk.Entry(r3, textvariable=self.detail_var, width=52).pack(side=tk.LEFT, fill="x", expand=True)
        ttk.Button(r3, text="使用此地址", command=self._use_detail_entry, width=12).pack(side=tk.LEFT, padx=4)
        ttk.Button(r3, text="自动分析章节列表", command=self._analyze_chapters, width=16).pack(side=tk.LEFT, padx=4)
        self.chapter_info = tk.Text(f2, height=8, font=("Consolas", 9), state="disabled")
        self.chapter_info.pack(fill="both", expand=True, pady=4)
        self.log2 = ttk.Label(f2, text="", foreground="#909399", wraplength=680)
        self.log2.pack(anchor="w")

        # ---------- Tab3 图片识别 ----------
        f3 = ttk.Frame(nb, padding=10)
        nb.add(f3, text="③ 图片识别")
        ttk.Label(f3, text="4. 自动打开第一章，分析图片规则：").pack(anchor="w")
        ttk.Button(f3, text="打开第一章并分析图片", command=self._analyze_images, width=20).pack(anchor="w", pady=6)
        self.img_info = tk.Text(f3, height=10, font=("Consolas", 9), state="disabled")
        self.img_info.pack(fill="both", expand=True, pady=4)
        self.log3 = ttk.Label(f3, text="", foreground="#909399", wraplength=680)
        self.log3.pack(anchor="w")

        # ---------- Tab4 保存 ----------
        f4 = ttk.Frame(nb, padding=10)
        nb.add(f4, text="④ 保存适配器")
        ttk.Label(f4, text="5. 确认站点信息，生成可永久使用的适配器：").pack(anchor="w", pady=(0, 8))
        ttk.Label(f4, text="站点名称：").pack(anchor="w")
        self.name_var = tk.StringVar()
        ttk.Entry(f4, textvariable=self.name_var, width=50).pack(fill="x", pady=(0, 6))
        ttk.Label(f4, text="网站地址：").pack(anchor="w")
        self.site_url_var = tk.StringVar()
        ttk.Entry(f4, textvariable=self.site_url_var, width=50).pack(fill="x", pady=(0, 6))
        self.save_info = tk.Text(f4, height=8, font=("Consolas", 9), state="disabled")
        self.save_info.pack(fill="both", expand=True, pady=4)
        self.btn_save = ttk.Button(f4, text="生成并保存适配器", command=self._save, width=20)
        self.btn_save.pack(anchor="e", pady=6)

    # ---------- 工具 ----------
    def _busy_on(self):
        self._busy = True
        for b in (self.btn_open, self.btn_search, self.btn_save):
            try:
                b.config(state="disabled")
            except Exception:
                pass

    def _busy_off(self):
        self._busy = False
        for b in (self.btn_open, self.btn_search, self.btn_save):
            try:
                b.config(state="normal")
            except Exception:
                pass

    def _log(self, label, text):
        label.config(text=text)

    def _append(self, widget, text):
        widget.config(state="normal")
        widget.insert("end", text + "\n")
        widget.see("end")
        widget.config(state="disabled")

    def _thread(self, fn):
        if self._busy:
            return
        self._busy_on()
        threading.Thread(target=lambda: self._safe(fn), daemon=True).start()

    def _safe(self, fn):
        try:
            fn()
        except Exception as e:
            import traceback
            self.win.after(0, lambda: self._log(self.log1, f"出错: {e}\n{traceback.format_exc()[-500:]}"))
        finally:
            self.win.after(0, self._busy_off)

    def _get_tab(self):
        if self.page is None:
            self.page = open_browser(self.browser_path, self.headless)
        return self.page

    # ---------- ① 搜索识别 ----------
    def _open_analyze(self):
        url = self.url_var.get().strip()
        if not url:
            messagebox.showwarning("提示", "请先输入网站地址", parent=self.win)
            return
        if not url.startswith("http"):
            url = "http://" + url
            self.url_var.set(url)
        self.site_url = url

        def fn():
            tab = self._get_tab()
            tab.get(url)
            links, _ = wait_page_has_links(tab, timeout=10)
            if not links:
                time.sleep(2)
                try:
                    tab.refresh()
                except Exception:
                    pass
                wait_page_has_links(tab, timeout=10)
            self.search_items = detect_search_input(tab) or []
            self.win.after(0, self._show_search_items)
        self._thread(fn)

    def _show_search_items(self):
        self.search_list.delete(0, "end")
        if not self.search_items:
            self._log(self.log1, "未检测到搜索框。可能是页面结构特殊，可直接在第②步粘贴「漫画详情页地址」继续。")
            return
        for it in self.search_items[:5]:
            self.search_list.insert("end",
                f"名称={it.get('name') or it.get('id') or '(无)'} | 表单方式={it.get('formMethod','get').upper()} | action={it.get('formAction') or '(当前页)'}")
        self._log(self.log1, f"已识别 {len(self.search_items)} 个搜索框，请点击「开始试搜」验证")

    def _do_search(self):
        if not self.search_items:
            messagebox.showwarning("提示", "先点「打开并分析」识别搜索框", parent=self.win)
            return
        kw = self.kw_var.get().strip() or self.comic_name_hint
        item = self.search_items[0]
        from urllib.parse import urlsplit
        self._allowed_netloc = urlsplit(self.site_url).netloc

        def fn():
            tab = self._get_tab()
            if item.get('formMethod') == 'post':
                ok = submit_search(tab, item, kw)
                if not ok:
                    self.win.after(0, lambda: self._log(self.log1, "POST表单提交失败，改用GET模板"))
                    return
                time.sleep(3)
                used = self.site_url
                self.search_url_template = ''
            else:
                used = None
                for url in build_search_url_candidates(self.site_url, item, kw):
                    tab.get(url)
                    time.sleep(3)
                    links_try = collect_links(tab)
                    if looks_like_search_results(url, links_try, score_detail, getattr(self, '_allowed_netloc', None)):
                        used = url
                        break
                if used is None:
                    # 全部候选都不像结果页，用最后一个
                    used = url
                self.search_url_template = used.replace(kw, '{kw}')
                self.win.after(0, lambda u=used: self._log(self.log1, f"搜索地址(生效): {u}"))
            links = collect_links(tab)
            cands = pick_top(links, score_detail, limit=30, allowed_netloc=getattr(self, '_allowed_netloc', None))
            # 关键词相关性过滤：只保留标题/网址含关键词的漫画，去掉推荐/无关内容
            kw_norm = (kw or '').replace(' ', '')
            matched = [c for c in cands if kw_norm and (kw_norm in (c.get('ti') or c.get('title') or '') or kw_norm in c['href'])]
            if not matched:
                matched = []
            # 按网址去重（保留第一条）
            seen, final = set(), []
            for c in (matched if matched else cands):
                if c['href'] not in seen:
                    seen.add(c['href'])
                    final.append(c)
            self.win.after(0, lambda c=final, m=bool(matched): self._show_results(c, matched=m))
        self._thread(fn)

    def _show_results(self, cands, matched=True):
        self.result_list.delete(0, "end")
        if not cands:
            self._log(self.log1, "没找到像漫画详情页的链接，可换关键词重试，或直接在第②步粘贴详情页地址")
            return
        cands = [c for c in cands if (c.get('ti') or c.get('title') or '').strip()]
        for c in cands:
            self.result_list.insert("end", f"{c['title'][:40]}  →  {c['href'][:70]}")
        if matched:
            self._log(self.log1, f"✅ 找到与关键词相关的漫画 {len(cands)} 个，点一个进入②分析章节")
        else:
            self._log(self.log1, "⚠️ 结果中没有与关键词相关的条目（可能是搜索未生效，这些是页面推荐内容）。\n请换关键词重试，或直接在②步粘贴漫画详情页地址")

    def _on_result_click(self):
        """单击列表项：仅提示，不触发跳转"""
        sel = self.result_list.curselection()
        if sel:
            text = self.result_list.get(sel[0])
            self._log(self.log1, f"已选中: {text.split('  →  ')[-1].strip()[:70]}，点右侧按钮进入②分析章节")

    def _select_detail(self):
        sel = self.result_list.curselection()
        if not sel:
            messagebox.showinfo("提示", "先在列表里点选一个漫画（单击选中，再点按钮）", parent=self.win)
            return
        idx = sel[0]
        text = self.result_list.get(idx)
        href = text.split("  →  ")[-1].strip()
        if not href.startswith("http"):
            messagebox.showwarning("提示", f"所选条目不是有效链接: {href}", parent=self.win)
            return
        self.detail_url = href
        self.detail_var.set(href)
        self._log(self.log1, f"✅ 已选详情页: {href}")
        self._log(self.log2, f"详情页: {href}")
        # 自动跳到② 章节识别
        self.nb.select(1)

    # ---------- ② 章节识别 ----------
    def _use_detail_entry(self):
        url = self.detail_var.get().strip()
        if url:
            self.detail_url = url
            self._log(self.log1, f"已采用详情页: {url}")

    def _analyze_chapters(self):
        if not self.detail_url:
            messagebox.showwarning("提示", "请先在上方填写/选择 漫画详情页地址", parent=self.win)
            return

        def fn():
            tab = self._get_tab()
            # 加载详情页：等待出内容，失败自动刷新重试（防JS验证/网络慢）
            links_all = []
            page_title = ''
            for attempt in range(4):
                try:
                    tab.get(self.detail_url)
                except Exception:
                    pass
                links_all, page_title = wait_page_has_links(tab, timeout=10)
                if links_all:
                    break
                if attempt < 3:
                    try:
                        tab.refresh()
                    except Exception:
                        pass
                    links_all, page_title = wait_page_has_links(tab, timeout=10)
                    if links_all:
                        break
            if not links_all:
                self.win.after(0, lambda t=page_title: self._append(self.chapter_info,
                    f"❌ 页面没有加载出任何内容（可能被网站拦截或网络慢）。\n当前页面标题: {t[:60] or '(空)'}\n请稍后重试，或换个时间再分析。" ))
                return
            # ① 翻页采集：自动点 下一页/查看更多 → 滚动 → 合并，直到章节不再增长
            links_all2 = collect_with_paging(tab, max_rounds=30)
            scored = pick_top(links_all2, score_chapter, limit=800, allowed_netloc=getattr(self, '_allowed_netloc', None))
            links = links_all2
            self.chapters_scroll = True  # 下载时同样执行 点下一页+滚动 以拿到完整章节
            if not scored:
                self.win.after(0, lambda: self._append(self.chapter_info,
                    "❌ 未识别到章节链接。\n可能原因：详情页地址不是漫画详情页（例如是动画/影视/分类页），\n或页面结构特殊。\n请回①步重新选中真漫画，或直接粘贴漫画详情页地址（如 https://www.kanman.com/5471/）。"))
                return
            # 同漫画校验：过滤掉推荐列表/跨漫画链接
            verdict, scored = judge_chapter_links(scored, self.detail_url, page_title)
            if verdict == 'reject':
                self.win.after(0, lambda: self._append(self.chapter_info,
                    "❌ 识别到的链接属于多部不同漫画，像是页面的“推荐/热门”列表，不是章节列表。\n"
                    "这个地址很可能不是漫画详情页（例如是动画/影视/分类页）。\n"
                    "请回①步选中真漫画详情页，或直接粘贴漫画详情页地址（如 https://www.kanman.com/5471/）。"))
                return
            if verdict == 'use_warn':
                self.win.after(0, lambda: self._append(self.chapter_info,
                    "⚠️ 章节链接与详情页ID不一致，已按章节标题识别。请在③步核对打开的是否为正确章节。"))
            self.chapter_links = scored
            rx = build_href_regex(scored, min_count=2)
            if not rx:
                self.win.after(0, lambda: self._append(self.chapter_info, "❌ 章节链接形态太分散，无法自动生成规则。"))
                return
            self.chapter_regex = rx
            # 若大多数章节标题都带“第N话/回”等字样，则章节JS要求标题像章节，防止混入漫画详情
            self.require_chapter_title = (sum(1 for c in scored
                                              if CHAPTER_TITLE_RE.search(c.get('ti') or c.get('title') or ''))
                                          >= max(2, len(scored) * 0.5))
            # 顺序判断用“页面原始顺序”的章节链接（只保留同漫画的）
            keep = {c['href'] for c in scored}
            ordered = [l for l in links if l['href'] in keep]
            self.reverse = detect_order([(c.get('ti') or c.get('title') or '') for c in ordered])
            txt = (f"识别到章节链接: {len(scored)} 个（示例）\n"
                   + "\n".join(f"  {c['title'][:30]}  {c['href'][:60]}" for c in scored[:6])
                   + f"\n章节顺序: {'新→旧，将自动倒序' if self.reverse else '旧→新，无需倒序'}\n规则: {rx[:70]}...")
            self.win.after(0, lambda t=txt: self._append(self.chapter_info, t))
            self.win.after(0, lambda: self._log(self.log2, "章节规则已生成，可到③步分析图片"))
            # 第一章 = 页面顺序中最早的一话（页面新→旧时取最后一条，旧→新时取第一条）
            self.first_chapter_url = (ordered[-1]['href'] if self.reverse
                                      else (ordered[0]['href'] if ordered else scored[0]['href']))
        self._thread(fn)

    # ---------- ③ 图片识别 ----------
    def _analyze_images(self):
        if not self.chapter_regex:
            messagebox.showwarning("提示", "请先完成②章节识别", parent=self.win)
            return
        url = self.first_chapter_url or (self.chapter_links[0]['href'] if self.chapter_links else '')
        if not url:
            messagebox.showwarning("提示", "缺少章节地址", parent=self.win)
            return

        def fn():
            tab = self._get_tab()
            # 加载章节页（最多重试3次）
            for attempt in range(3):
                try:
                    tab.get(url)
                except Exception:
                    pass
                time.sleep(3)
                imgs0 = tab.run_js(IMG_INFO_JS) or []
                if imgs0:
                    break
            # 滚动前先采样（判断页面是否必须滚动才出图）
            real0 = analyze_images(imgs0).get('count', 0)
            scroll_incremental(tab, max_steps=90)
            imgs = tab.run_js(IMG_INFO_JS) or []
            res = analyze_images(imgs)
            if res.get('count', 0) > real0 or (real0 == 0 and res.get('count', 0) > 0):
                res['scroll'] = True  # 页面需要滚动才加载图片
            self.img_result = res
            sel = res.get('selector') or '(未识别到容器，用整页)'
            txt = (f"章节页: {url[:80]}\n"
                   f"真图片数量: {res.get('count')} 张\n"
                   f"图片容器: {sel}\n"
                   f"图片属性: {res.get('attr')}\n"
                   f"需要滚动加载: {'是' if res.get('scroll') else '否'}\n"
                   + ("样例:\n" + "\n".join("  " + u[:70] for u in res.get('sample', [])) if res.get('sample') else ""))
            self.win.after(0, lambda t=txt: self._append(self.img_info, t))
            if res.get('count') == 0:
                self.win.after(0, lambda: self._log(self.log3, "⚠️ 未识别到图片，请检查：该章是否需登录？或页面结构特殊"))
            else:
                self.win.after(0, lambda: self._log(self.log3, "图片规则已生成，可到④步保存适配器"))
        self._thread(fn)

    # ---------- ④ 保存 ----------
    def _save(self):
        if not self.chapter_regex:
            messagebox.showwarning("提示", "请先完成②③步分析", parent=self.win)
            return
        name = self.name_var.get().strip()
        if not name:
            messagebox.showwarning("提示", "请填写站点名称", parent=self.win)
            return
        site_url = self.site_url_var.get().strip() or self.site_url
        if not site_url.startswith("http"):
            site_url = "http://" + site_url

        # 搜索规则
        search = {'mode': 'get', 'url_template': '', 'post_path': '', 'post_key': 'k'}
        if self.search_items:
            item = self.search_items[0]
            if item.get('formMethod') == 'post':
                search['mode'] = 'post_fetch'
                search['post_path'] = item.get('formAction') or '/s'
                search['post_key'] = item.get('name') or item.get('id') or 'k'
                search['result_js'] = ("return Array.from(document.querySelectorAll('a'))"
                                       ".filter(function(a){return new RegExp(%s,'i').test(a.href);})"
                                       ".slice(0,10).map(function(a){return {url:a.href,title:(a.textContent||'').trim()};});"
                                       % __import__('json').dumps(self.chapter_regex))
            else:
                search['url_template'] = self.search_url_template or build_search_url(site_url, item, '{kw}')
                search['result_js'] = build_links_js(self.chapter_regex, 10)
        else:
            search['url_template'] = site_url + 'search?keyword={kw}'
            search['result_js'] = build_links_js(self.chapter_regex, 10)

        img = self.img_result or {'attr': 'src', 'selector': None, 'scroll': False}
        adapter = {
            'site_name': name,
            'site_url': site_url,
            'image_attr': img.get('attr') or 'src',
            'search': search,
            'detail': {
                'chapters_js': build_chapters_js(self.chapter_regex, require_chapter_title=getattr(self, 'require_chapter_title', False)),
                'chapters_reverse': bool(self.reverse),
                'chapters_scroll': bool(getattr(self, 'chapters_scroll', False)),
                'cover_js': None,
            },
            'chapter': {
                'images_js': build_images_js(img.get('selector'), img.get('attr') or 'src'),
                'scroll_to_load': bool(img.get('scroll')),
            },
        }
        self._append(self.save_info, "生成的适配器：\n" + __import__('json').dumps(adapter, ensure_ascii=False, indent=2))
        if self.on_save:
            self.on_save(adapter)
        self.win.destroy()

    def _on_close(self):
        self._close_page()
        self.win.destroy()

    def _close_page(self):
        if self.page is not None:
            try:
                self.page.quit()
            except Exception:
                try:
                    self.page.close()
                except Exception:
                    pass
            self.page = None

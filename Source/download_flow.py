# 公共下载流程 - gui.py 与 main.py 共用，避免两处逻辑重复
import asyncio
import time

import downloader as _dl_new
from utils import zip_main_folder
import stop_control


def get_download_impl():
    """按设置返回下载实现模块。

    'new'    -> downloader.py（v1.4.0 及以后的重试/并发/人工节奏/整批暂停）
    'legacy' -> downloader_legacy.py（v1.4.0 之前那一套原始下载逻辑）
    """
    try:
        import config
        if str(getattr(config, 'DOWNLOAD_LOGIC', 'new') or 'new').lower() == 'legacy':
            import downloader_legacy as _legacy
            return _legacy
    except Exception as e:
        print(f"加载旧版下载逻辑失败，回退新版: {e}")
    return _dl_new


def _apply_name_padding(value):
    """命名补零对两套下载器各自生效（旧版有自己的全局变量）"""
    try:
        _dl_new.set_active_name_padding(value)
    except Exception:
        pass
    try:
        import downloader_legacy as _legacy
        _legacy.set_active_name_padding(value)
    except Exception:
        pass


def _apply_chapter_folder_naming(value):
    try:
        _dl_new.set_active_chapter_folder_naming(value)
    except Exception:
        pass
    try:
        import downloader_legacy as _legacy
        _legacy.set_active_chapter_folder_naming(value)
    except Exception:
        pass



def search_and_open(crawler, comic_name, comic_id=None, log=print):
    """搜索漫画或按ID打开详情页

    Returns:
        (target_comic_tab, comic_name): 详情页标签页与最终漫画名（按ID时可能被站点返回的实际名覆盖）
    """
    if comic_id:
        log("正在通过ID访问漫画...")
        result = crawler.search_comic(comic_name, comic_id)
        if isinstance(result, tuple):
            target_comic_tab, actual_name = result
            log(f"获取到漫画名字: {actual_name}")
            return target_comic_tab, actual_name
        return result, comic_name

    # 站点爬虫内部已打印"正在搜索漫画"日志，此处不重复
    target_comic_tab = crawler.search_comic(comic_name)
    return target_comic_tab, comic_name


def _stream_collect_mode():
    """采集模式：'stream'=边采集边下载（爬一章下一章），'batch'=先全部采集再下载。"""
    try:
        import config
        return str(getattr(config, 'COLLECT_MODE', 'batch') or 'batch').lower() == 'stream'
    except Exception:
        return False


async def _download_cover_async(impl, crawler, target_comic_tab, comic_name, download_path, log):
    """下载封面（加密站点走浏览器渲染，其余走 HTTP，带 Referer/解密）。"""
    log("正在获取封面图片...")
    cover_url = crawler.get_cover_image(target_comic_tab)
    if not cover_url:
        return
    # 加密站点优先使用浏览器渲染提取封面，失败回退HTTP下载
    # （站点CONFIG声明cover_via_browser=False时封面为明文，直接HTTP下载）
    cover_done = False
    cover_cfg = getattr(crawler.site_crawler, 'CONFIG', {})
    if _dl_new.is_browser_render_site(crawler.site_crawler) and cover_cfg.get('cover_via_browser', True):
        cover_done = impl.download_cover_via_browser(
            target_comic_tab, crawler.site_crawler, comic_name,
            download_path if download_path else None)
    if cover_done:
        return
    cover_cfg = getattr(crawler.site_crawler, 'CONFIG', {})
    impl.set_active_referer(cover_cfg.get('image_referer'))
    decrypt_cfg = cover_cfg.get('decrypt')
    decryptor = impl._build_decryptor(crawler.site_crawler) if decrypt_cfg else None
    if decryptor:
        impl.set_active_decryptor(decryptor, decrypt_cfg.get('ext', 'jpg'))
    try:
        await impl.download_cover_image(cover_url, comic_name, download_path if download_path else None)
    finally:
        impl.set_active_referer(None)
        impl.set_active_decryptor(None)


def _finalize_chapter_packages(comic_name, download_path, all_chapters_data,
                               failed_downloads, log):
    """下载完成后统一补一次按章 zip/CBZ。

    新版下载器自己会打包，但旧版下载逻辑（v1.4.0 之前）没有这段；
    这里统一兜底，保证「按章节打包」在两种下载逻辑下都生效。
    只补缺失的包，已存在的不重复压缩。
    """
    try:
        import config
        import os
        if not bool(getattr(config, 'ZIP_PER_CHAPTER', False)):
            return
        if failed_downloads:
            log(f"有 {len(failed_downloads)} 张图片失败，跳过按章打包")
            return
        folder = os.path.join(
            download_path if download_path else os.getcwd(),
            _dl_new.comic_folder_name(comic_name)
        )
        if not os.path.isdir(folder):
            return
        # 旧版下载逻辑不写章节断点；这里统一补标记，保证「跳过已下载章节」两种逻辑都可用
        if not failed_downloads and all_chapters_data:
            for ch in all_chapters_data:
                num = ch.get('chapter_num')
                if num is None:
                    continue
                ch_dir = os.path.join(folder, _dl_new.chapter_folder_name(ch))
                if os.path.isdir(ch_dir) and os.listdir(ch_dir):
                    _dl_new.mark_chapter_done(folder, num, ch.get('title') or '')
        ext = '.cbz' if bool(getattr(config, 'CBZ_MODE', False)) else '.zip'
        image_exts = ('.jpg', '.jpeg', '.png', '.webp', '.gif', '.bmp')
        missing = False
        for name in os.listdir(folder):
            chapter_dir = os.path.join(folder, name)
            if not os.path.isdir(chapter_dir):
                continue
            has_image = False
            for root, _dirs, files in os.walk(chapter_dir):
                if any(os.path.splitext(f)[1].lower() in image_exts for f in files):
                    has_image = True
                    break
            if has_image and not os.path.exists(os.path.join(folder, f'{name}{ext}')):
                missing = True
                break
        if not missing:
            return
        titles = {c.get('chapter_num'): c.get('title') or '' for c in all_chapters_data}
        _dl_new.zip_each_chapter(folder, comic_name=comic_name, chapter_titles=titles)
    except Exception as e:
        log(f"按章打包失败: {e}")


async def _run_stream_async(crawler, target_comic_tab, comic_name, chapter_start, actual_end,
                            download_path, progress_callback,
                            download_thread_count, use_thread_only,
                            first_timeout, retry_timeout,
                            url_progress_callback=None, pre_download_hook=None, log=print,
                            create_zip=False, download_cover=True):
    """边采集边下载：逐章采集 → 立即下载本章 → 继续下一章。

    章节图片 URL 只保留当前这一章，采集完马上进入下载，避免整本采集完才开始下。
    """
    impl = get_download_impl()
    log(f"下载逻辑: {'旧版（v1.4.0 之前）' if impl is not _dl_new else '新版（当前）'}")
    log("采集模式: 边采集边下载（采集完一章立即下载这一章）")

    all_chapters_data = []
    total_images = 0
    failed_downloads = []
    failed_json_path = None
    should_zip = True

    if download_cover and not stop_control.is_stopped():
        await _download_cover_async(impl, crawler, target_comic_tab, comic_name, download_path, log)

    for num in range(chapter_start, actual_end + 1):
        stop_control.check()
        log(f"采集第{num}章图片链接...")
        data = crawler.collect_chapters_images(
            target_comic_tab,
            chapter_start=num,
            chapter_end=num,
            max_threads=1,
            progress_callback=url_progress_callback
        )
        if not data:
            log(f"第{num}章没有采集到图片，跳过")
            continue
        all_chapters_data.extend(data)
        imgs = sum(len(c.get('herf_list') or []) for c in data)
        total_images += imgs
        if pre_download_hook:
            pre_download_hook(total_images)
        if imgs:
            log(f"开始下载第{num}章（{imgs} 张）...")
            fd, fjp, sz = await impl.download_all_chapters(
                data,
                comic_name,
                download_path if download_path else None,
                download_thread_count=download_thread_count,
                progress_callback=progress_callback,
                max_retries=3,
                use_thread_only=use_thread_only,
                first_timeout=first_timeout,
                retry_timeout=retry_timeout,
                site_crawler=crawler.site_crawler
            )
            failed_downloads.extend(fd)
            if fjp:
                failed_json_path = fjp
            should_zip = should_zip and sz
        else:
            log(f"第{num}章无图片链接，跳过下载")

    if stop_control.is_stopped():
        log("已停止下载，跳过压缩与章节收尾")
        should_zip = False
        create_zip = False
    if create_zip and should_zip and all_chapters_data:
        zip_main_folder(comic_name, download_path if download_path else None)
    if not stop_control.is_stopped():
        _finalize_chapter_packages(comic_name, download_path, all_chapters_data,
                                   failed_downloads, log)

    return {
        'chapters_data': all_chapters_data,
        'total_images': total_images,
        'failed_downloads': failed_downloads,
        'failed_json_path': failed_json_path,
        'should_zip': should_zip,
        'create_zip': create_zip,
    }


async def _run_download_async(crawler, target_comic_tab, comic_name, chapter_start, actual_end,
                              max_threads, download_path, progress_callback,
                              download_thread_count, use_thread_only,
                              first_timeout, retry_timeout,
                              url_progress_callback=None, pre_download_hook=None, log=print,
                              create_zip=False, download_cover=True):
    impl = get_download_impl()
    log(f"下载逻辑: {'旧版（v1.4.0 之前）' if impl is not _dl_new else '新版（当前）'}")

    # 采集模式：边采集边下载（爬一章下一章）或 先全部采集再下载
    if _stream_collect_mode():
        return await _run_stream_async(
            crawler, target_comic_tab, comic_name, chapter_start, actual_end,
            download_path, progress_callback, download_thread_count, use_thread_only,
            first_timeout, retry_timeout, url_progress_callback=url_progress_callback,
            pre_download_hook=pre_download_hook, log=log, create_zip=create_zip,
            download_cover=download_cover)

    all_chapters_data = crawler.collect_chapters_images(
        target_comic_tab,
        chapter_start=chapter_start,
        chapter_end=actual_end,
        max_threads=max_threads,
        progress_callback=url_progress_callback
    )

    stop_control.check()
    total_images = sum(len(c['herf_list']) for c in all_chapters_data)
    log(f"将下载 {len(all_chapters_data)} 个章节")
    if pre_download_hook:
        pre_download_hook(total_images)

    if download_cover and not stop_control.is_stopped():
        await _download_cover_async(impl, crawler, target_comic_tab, comic_name, download_path, log)

    failed_downloads, failed_json_path, should_zip = [], None, True
    if all_chapters_data and not stop_control.is_stopped():
        failed_downloads, failed_json_path, should_zip = await impl.download_all_chapters(
            all_chapters_data,
            comic_name,
            download_path if download_path else None,
            download_thread_count=download_thread_count,
            progress_callback=progress_callback,
            max_retries=3,
            use_thread_only=use_thread_only,
            first_timeout=first_timeout,
            retry_timeout=retry_timeout,
            site_crawler=crawler.site_crawler
        )

    if stop_control.is_stopped():
        log("已停止下载，跳过压缩与章节收尾")
        should_zip = False
        create_zip = False
    if create_zip and should_zip and all_chapters_data:
        zip_main_folder(comic_name, download_path if download_path else None)
    if not stop_control.is_stopped():
        _finalize_chapter_packages(comic_name, download_path, all_chapters_data,
                                   failed_downloads, log)

    return {
        'chapters_data': all_chapters_data,
        'total_images': total_images,
        'failed_downloads': failed_downloads,
        'failed_json_path': failed_json_path,
        'should_zip': should_zip,
        'create_zip': create_zip,
    }


def run_download_flow(crawler, comic_name, comic_id=None, chapter_start=1, chapter_end=0,
                      max_threads=5, download_path=None, log=print,
                      progress_callback=None,
                      download_thread_count=4, use_thread_only=False,
                      first_timeout=8, retry_timeout=15,
                      url_progress_callback=None, pre_download_hook=None,
                      pre_collect_hook=None, create_zip=False, image_name_padding=None,
                      chapter_folder_naming=None):
    """执行完整下载流程：搜索→章节数→收集→封面→下载→（可选）压缩

    Args:
        crawler: ComicCrawler实例
        log: 日志输出函数（GUI传append_status，CLI用print）
        progress_callback: 图片下载进度回调
        url_progress_callback: 章节收集进度回调
        pre_download_hook: 下载开始前回调，参数为total_images（供调用方重置进度条）
        pre_collect_hook: 收集前回调，参数为实际下载章节数（供调用方重置URL进度条）
        create_zip: 下载完成后是否生成压缩包，默认False不生成
        image_name_padding: 图片文件名补零位数（0=不补零，3=001格式），None表示保持当前全局设置
        chapter_folder_naming: 章节文件夹命名模式（'number'=数字命名，'title'=章节名命名），
                               None表示保持当前全局设置（默认数字命名）

    Returns:
        dict: {comic_name, total_chapters, actual_start, actual_end,
               chapters_data, total_images, failed_downloads, failed_json_path,
               should_zip, create_zip}
    """
    if image_name_padding is not None:
        _apply_name_padding(image_name_padding)
    if chapter_folder_naming is not None:
        _apply_chapter_folder_naming(chapter_folder_naming)

    target_comic_tab, comic_name = search_and_open(crawler, comic_name, comic_id, log)
    stop_control.check()
    if not target_comic_tab:
        raise RuntimeError("未找到漫画，请检查名称/ID是否正确")
    log("成功打开漫画详情页")

    log("正在获取章节数量...")
    total_chapters = crawler.get_chapter_count(target_comic_tab)
    stop_control.check()

    # 章节计数稳定性校验：部分站点章节列表懒加载/折叠，首次计数可能偏低
    # （如gmh折叠态只渲染约10章、baozimh页面元素未就绪）。连续重读，直到
    # 两次一致或达到最大重读次数，取稳定值（以更大值为准，避免误把部分列表当全部）。
    # 纯requests站点（无需浏览器）章节数来自缓存，恒稳定，跳过校验省时
    if chapter_end <= 0 and getattr(crawler, 'needs_browser', True):  # "到所有章"模式才校验（指定范围时无需关心总数是否偏小）
        max_retries = 3
        last_count = total_chapters
        for attempt in range(max_retries):
            time.sleep(1.5)
            re_count = crawler.get_chapter_count(target_comic_tab)
            if re_count == last_count:
                total_chapters = re_count
                break
            if re_count > last_count:
                last_count = re_count
                total_chapters = re_count
                log(f"章节数变化: {re_count}（等待页面加载完成）")
            if attempt == max_retries - 1:
                total_chapters = max(total_chapters, re_count)
        log(f"章节数稳定: {total_chapters}")

    actual_start = chapter_start
    actual_end = min(chapter_end, total_chapters) if chapter_end > 0 else total_chapters
    actual_count = actual_end - actual_start + 1

    log(f"总章节数: {total_chapters}, 将下载: {actual_start}-{actual_end} 共{actual_count}章")
    if pre_collect_hook:
        pre_collect_hook(actual_count)

    log("正在收集章节图片链接...")
    result = asyncio.run(_run_download_async(
        crawler, target_comic_tab, comic_name, actual_start, actual_end,
        max_threads, download_path, progress_callback,
        download_thread_count, use_thread_only, first_timeout, retry_timeout,
        url_progress_callback=url_progress_callback, pre_download_hook=pre_download_hook, log=log,
        create_zip=create_zip))

    result.update({
        'comic_name': comic_name,
        'total_chapters': total_chapters,
        'actual_start': actual_start,
        'actual_end': actual_end,
    })
    return result


def _to_contiguous_runs(nums):
    """把已排序的章节号列表聚成连续段，如 [1,2,3,5,8,9] -> [(1,3),(5,5),(8,9)]"""
    if not nums:
        return []
    runs = []
    start = prev = nums[0]
    for n in nums[1:]:
        if n == prev + 1:
            prev = n
        else:
            runs.append((start, prev))
            start = prev = n
    runs.append((start, prev))
    return runs


def run_download_flow_selected(crawler, comic_name, selected_numbers, comic_id=None,
                               max_threads=5, download_path=None, log=print,
                               progress_callback=None,
                               download_thread_count=4, use_thread_only=False,
                               first_timeout=8, retry_timeout=15,
                               url_progress_callback=None, pre_download_hook=None,
                               pre_collect_hook=None, create_zip=False,
                               image_name_padding=None, chapter_folder_naming=None):
    """按勾选的具体章节号下载（章节表功能）。

    勾选的章节可能不连续（如第1、3、5、9章）。这里把勾选章节聚成若干连续段，
    每段复用现有 collect_chapters_images(start,end) 接口下载，不改动各站点爬虫。
    漫画详情页只打开一次。

    Args:
        selected_numbers: 勾选的章节号（1为起点）的可迭代对象
    Returns:
        dict: 与 run_download_flow 结构一致（含 chapters_data/failed_downloads 等）
    """
    if image_name_padding is not None:
        _apply_name_padding(image_name_padding)
    if chapter_folder_naming is not None:
        _apply_chapter_folder_naming(chapter_folder_naming)

    target_comic_tab, comic_name = search_and_open(crawler, comic_name, comic_id, log)
    stop_control.check()
    if not target_comic_tab:
        raise RuntimeError("未找到漫画，请检查名称/ID是否正确")
    log("成功打开漫画详情页")

    log("正在获取章节数量...")
    total_chapters = crawler.get_chapter_count(target_comic_tab)
    stop_control.check()
    if getattr(crawler, 'needs_browser', True):
        max_retries = 3
        last_count = total_chapters
        for attempt in range(max_retries):
            time.sleep(1.5)
            re_count = crawler.get_chapter_count(target_comic_tab)
            if re_count == last_count:
                total_chapters = re_count
                break
            if re_count > last_count:
                last_count = re_count
                total_chapters = re_count
                log(f"章节数变化: {re_count}（等待页面加载完成）")
            if attempt == max_retries - 1:
                total_chapters = max(total_chapters, re_count)

    # 归一化勾选章节号：去重、排序、过滤越界
    selected = sorted({int(n) for n in selected_numbers
                       if str(n).strip().isdigit() and 1 <= int(n) <= total_chapters})
    if not selected:
        raise RuntimeError("未选择任何有效章节")

    runs = _to_contiguous_runs(selected)
    log(f"总章节数: {total_chapters}, 已选 {len(selected)} 章（合并为 {len(runs)} 个连续段）")
    if pre_collect_hook:
        pre_collect_hook(len(selected))

    all_chapters_data = []
    total_images = 0
    failed_downloads = []
    failed_json_path = None
    should_zip = True

    for idx, (r_start, r_end) in enumerate(runs):
        stop_control.check()
        log(f"下载连续段 {idx + 1}/{len(runs)}: 第{r_start}章 到 第{r_end}章")
        res = asyncio.run(_run_download_async(
            crawler, target_comic_tab, comic_name, r_start, r_end,
            max_threads, download_path, progress_callback,
            download_thread_count, use_thread_only, first_timeout, retry_timeout,
            url_progress_callback=url_progress_callback,
            pre_download_hook=pre_download_hook, log=log,
            create_zip=False, download_cover=(idx == 0)))
        all_chapters_data.extend(res['chapters_data'])
        total_images += res['total_images']
        failed_downloads.extend(res['failed_downloads'])
        if res['failed_json_path']:
            failed_json_path = res['failed_json_path']
        should_zip = should_zip and res['should_zip']

    if stop_control.is_stopped():
        should_zip = False
        create_zip = False
    if create_zip and should_zip and all_chapters_data:
        zip_main_folder(comic_name, download_path if download_path else None)

    return {
        'comic_name': comic_name,
        'total_chapters': total_chapters,
        'chapters_data': all_chapters_data,
        'total_images': total_images,
        'failed_downloads': failed_downloads,
        'failed_json_path': failed_json_path,
        'should_zip': should_zip,
        'create_zip': create_zip,
    }

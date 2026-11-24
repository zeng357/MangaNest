#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""MangaNest 测试版 · 无头命令行（不启动 GUI）

示例：
  python cli.py --list-sites
  python cli.py --site 漫画客 --comic "藤女" --chapters "1,3,5-20" --out E:\\漫画
  python cli.py --site 漫画客 --comic "藤女" --all --cbz --skip-done
  python cli.py --site 漫画客 --comic-file comics.txt --threads 4
  python cli.py --site 漫画客 --comic "藤女" --export pdf
  python cli.py --site 漫画客 --comic "藤女" --follow --interval 600

退出码：0=全部成功；1=有图片/任务失败；2=参数错误
"""
import argparse
import json
import os
import sys
import time

from utils import ensure_console_safe, parse_chapter_expression
from download_flow import run_download_flow, run_download_flow_selected

APP_DIR = os.path.dirname(os.path.abspath(__file__))


def log(msg, log_file=None):
    stamp = time.strftime('%H:%M:%S')
    line = f'[{stamp}] {msg}'
    print(line)
    if log_file:
        try:
            with open(log_file, 'a', encoding='utf-8') as f:
                f.write(line + '\n')
        except Exception:
            pass


def read_config_json():
    try:
        with open(os.path.join(APP_DIR, 'config.json'), 'r', encoding='utf-8-sig') as f:
            return json.load(f)
    except Exception:
        return {}


def default_download_path():
    cfg = read_config_json()
    path = str(cfg.get('download_path') or '').strip()
    return path or os.getcwd()


def apply_args_to_config(args):
    """把命令行开关写进 config 模块（与 GUI 运行时开关同一套）"""
    import config as cfg
    if args.follow and args.skip_done is None:
        args.skip_done = True
    if args.skip_done is not None:
        cfg.SKIP_DONE_CHAPTERS = bool(args.skip_done)
    if args.per_chapter is not None:
        cfg.ZIP_PER_CHAPTER = bool(args.per_chapter)
    if args.cbz:
        cfg.CBZ_MODE = True
    if args.zip:
        cfg.CBZ_MODE = False
    if args.stream is True:
        cfg.COLLECT_MODE = 'stream'
    elif args.stream is False:
        cfg.COLLECT_MODE = 'batch'
    if args.human is True:
        cfg.HUMAN_MODE = True
    elif args.human is False:
        cfg.HUMAN_MODE = False
    if args.fast is True:
        cfg.FAST_THREAD = True
    elif args.fast is False:
        cfg.FAST_THREAD = False


def build_parser():
    p = argparse.ArgumentParser(
        description='MangaNest 无头下载（CLI）',
        formatter_class=argparse.ArgumentDefaultsHelpFormatter)
    p.add_argument('--list-sites', action='store_true', help='列出可用站点后退出')
    p.add_argument('--site', help='站点名称，如 漫画客')
    p.add_argument('--comic', help='漫画名称')
    p.add_argument('--comic-id', help='漫画ID（腾讯动漫/快看可用）')
    p.add_argument('--comic-file', help='批量文件：每行一个漫画名，可用 # 注释')
    p.add_argument('--chapters', default='', help='章节表达式：all / 1,3,5-20（留空用 --start/--end）')
    p.add_argument('--start', type=int, default=1, help='起始章节')
    p.add_argument('--end', type=int, default=0, help='结束章节（0=最后一章）')
    p.add_argument('--out', default=None, help='下载根目录（默认读 config.json 的 download_path）')
    p.add_argument('--threads', type=int, default=None, help='下载线程数')
    p.add_argument('--tabs', type=int, default=None, help='采集标签页数')
    p.add_argument('--timeout', type=int, default=None, help='首次下载超时（秒）')
    p.add_argument('--retry-timeout', type=int, default=None, help='重试超时（秒）')
    p.add_argument('--skip-done', dest='skip_done', action='store_true', help='跳过已下载章节')
    p.add_argument('--no-skip-done', dest='skip_done', action='store_false', help='不跳过已下载章节')
    p.set_defaults(skip_done=None)
    p.add_argument('--per-chapter', dest='per_chapter', action='store_true', help='按章打包')
    p.add_argument('--no-per-chapter', dest='per_chapter', action='store_false', help='不按章打包')
    p.set_defaults(per_chapter=None)
    p.add_argument('--cbz', action='store_true', help='按章打包为 .cbz（带 ComicInfo.xml）')
    p.add_argument('--zip', action='store_true', help='按章打包为 .zip')
    p.add_argument('--whole-zip', action='store_true', help='下载完成后整本压缩（原“创建压缩包”开关）')
    p.add_argument('--export', choices=['pdf', 'epub', 'cbz', 'mobi'], default=None,
                   help='下载完成后整本导出为指定格式（复用“格式转换”模块）')
    p.add_argument('--export-per-chapter', action='store_true', help='导出时按章节拆分为多个文件')
    p.add_argument('--follow', action='store_true', help='追更模式：定时检查并只补新章节')
    p.add_argument('--interval', type=int, default=600, help='追更轮询间隔（秒）')
    p.add_argument('--rounds', type=int, default=0, help='追更轮数（0=一直跑，Ctrl+C 停止）')
    p.add_argument('--stream', dest='stream', action='store_true', default=None, help='边采集边下载')
    p.add_argument('--batch', dest='stream', action='store_false', help='先采集全部再下载')
    p.add_argument('--human', dest='human', action='store_true', default=None, help='模拟人工节奏')
    p.add_argument('--fast', dest='fast', action='store_true', default=None, help='多线程高速（仅指定站点生效）')
    p.add_argument('--show-browser', action='store_true', help='显示浏览器窗口（默认无头）')
    p.add_argument('--login', action='store_true', help='启用站点登录（读取已保存 Cookies）')
    p.add_argument('--cookie', help='直接传入 Cookie 字符串')
    p.add_argument('--log-file', default=None, help='同时把日志追加写入文件')
    p.set_defaults(stream=None, human=None, fast=None)
    return p


def make_crawler(args, site_name):
    from crawler import ComicCrawler
    import config as cfg
    try:
        browser_path = cfg.BROWSER_PATHS.get('edge') or next(iter(cfg.BROWSER_PATHS.values()))
    except Exception:
        browser_path = r'C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe'
    return ComicCrawler(
        site_name,
        browser_path,
        headless=not args.show_browser,
        cookie_str=args.cookie,
        login_mode=bool(args.login),
        cookies_dir=cfg.DEFAULT_COOKIES_DIR,
    )


def _progress_printer(log_file):
    state = {'last': 0, 'mark': 0}

    def on_progress(inc):
        state['last'] += int(inc or 0)
        mb = state['last'] / 1024 / 1024
        if mb >= state['mark'] + 5:
            state['mark'] = int(mb // 5) * 5
            log(f'已下载 {mb:.1f} MB', log_file)

    return on_progress


def export_downloaded(args, result, out_dir):
    """下载完成后整本导出（复用 converter 模块）"""
    if not args.export:
        return
    from downloader import comic_folder_name
    from converter import convert_comic_folder
    comic_name = result.get('comic_name') or ''
    root = os.path.join(out_dir, comic_folder_name(comic_name))
    if not os.path.isdir(root):
        log(f'导出跳过：找不到目录 {root}', args.log_file)
        return
    fmt = args.export.upper()
    log(f'开始导出 {fmt}（{"按章" if args.export_per_chapter else "整本"}）...', args.log_file)
    ok, fails = convert_comic_folder(
        root, fmt, out_dir, per_chapter=bool(args.export_per_chapter),
        log=lambda m: log(m, args.log_file))
    log(f'导出完成：成功 {ok}，失败 {len(fails)}', args.log_file)


def run_one(args, site_name, comic_name, out_dir, crawler=None, keep_open=False):
    """下载一本漫画；args 已 apply 到 config

    crawler 传入时复用（追更模式），keep_open=True 时不关闭浏览器。
    """
    expression = (args.chapters or '').strip()
    numbers = parse_chapter_expression(expression) if expression else None
    log_file = args.log_file

    common = dict(
        comic_id=args.comic_id,
        download_path=out_dir,
        log=lambda m: log(m, log_file),
        progress_callback=_progress_printer(log_file),
        create_zip=bool(args.whole_zip),
        max_threads=args.tabs if args.tabs is not None else 5,
        download_thread_count=args.threads if args.threads is not None else 4,
        first_timeout=args.timeout if args.timeout is not None else 8,
        retry_timeout=args.retry_timeout if args.retry_timeout is not None else 15,
    )

    own_crawler = crawler is None
    if own_crawler:
        crawler = make_crawler(args, site_name)
    try:
        log(f'站点: {site_name} | 漫画: {comic_name or args.comic_id}', log_file)
        if numbers:
            log(f'章节表达式: {expression}（{len(numbers)} 章）', log_file)
            result = run_download_flow_selected(
                crawler=crawler, comic_name=comic_name,
                selected_numbers=numbers, **common)
        else:
            log(f'章节范围: {args.start} - {args.end or "末章"}', log_file)
            result = run_download_flow(
                crawler=crawler, comic_name=comic_name,
                chapter_start=args.start, chapter_end=args.end, **common)
        return result
    finally:
        if own_crawler and not keep_open:
            try:
                if getattr(crawler, 'page', None):
                    crawler.page.quit()
            except Exception:
                pass


def _collect_jobs(args):
    jobs = []
    if args.comic_file:
        with open(args.comic_file, 'r', encoding='utf-8-sig') as f:
            for line in f:
                name = line.strip()
                if name and not name.startswith('#'):
                    jobs.append(name)
    if args.comic:
        jobs.append(args.comic.strip())
    if not jobs and args.comic_id:
        jobs.append('')
    return jobs


def run_follow(args, site_name, comic_name, out_dir):
    """追更模式：同一浏览器会话内定时检查章节数，只补新章节"""
    crawler = make_crawler(args, site_name)
    failed_total = 0
    round_no = 1
    try:
        while True:
            log(f'===== 第 {round_no} 轮检查：{comic_name} =====', args.log_file)
            try:
                result = run_one(args, site_name, comic_name, out_dir,
                                 crawler=crawler, keep_open=True)
                failed = result.get('failed_downloads') or []
                failed_total += len(failed)
                new_chapters = len(result.get('chapters_data') or [])
                log(f'本轮新增/处理章节 {new_chapters} | 失败 {len(failed)}', args.log_file)
                if new_chapters:
                    export_downloaded(args, result, out_dir)
            except KeyboardInterrupt:
                raise
            except Exception as e:
                failed_total += 1
                log(f'本轮失败: {e}', args.log_file)

            if args.rounds and round_no >= args.rounds:
                break
            round_no += 1
            next_at = time.strftime('%H:%M:%S', time.localtime(time.time() + args.interval))
            log(f'等待 {args.interval} 秒，下次检查 {next_at}（Ctrl+C 停止）', args.log_file)
            time.sleep(max(1, args.interval))
    finally:
        try:
            if getattr(crawler, 'page', None):
                crawler.page.quit()
        except Exception:
            pass
    return 0 if failed_total == 0 else 1


def main(argv=None):
    ensure_console_safe()
    args = build_parser().parse_args(argv)

    if args.list_sites:
        from site_discovery import get_all_site_names
        sites = get_all_site_names()
        print('可用站点：')
        for s in sites:
            print(' -', s)
        return 0

    if not args.site:
        print('缺少 --site（可先用 --list-sites 查看站点名）', file=sys.stderr)
        return 2

    jobs = _collect_jobs(args)
    if not jobs:
        print('缺少 --comic / --comic-id / --comic-file', file=sys.stderr)
        return 2

    apply_args_to_config(args)
    out_dir = args.out or default_download_path()

    total_failed = 0
    started = time.time()
    for idx, name in enumerate(jobs, 1):
        log(f'===== 任务 {idx}/{len(jobs)}: {name or args.comic_id} =====', args.log_file)
        if args.follow:
            try:
                total_failed += 1 if run_follow(args, args.site, name, out_dir) else 0
            except KeyboardInterrupt:
                log('已停止追更', args.log_file)
            continue
        try:
            result = run_one(args, args.site, name, out_dir)
            failed = result.get('failed_downloads') or []
            total_failed += len(failed)
            log(f'✓ 完成《{result.get("comic_name")}》 '
                f'章节 {len(result.get("chapters_data") or [])} | 图片 {result.get("total_images")} '
                f'| 失败 {len(failed)}', args.log_file)
            export_downloaded(args, result, out_dir)
        except Exception as e:
            total_failed += 1
            log(f'✗ 任务失败《{name}》: {e}', args.log_file)

    elapsed = time.time() - started
    log(f'===== 全部结束：{len(jobs)} 个任务，失败 {total_failed}，耗时 {elapsed:.1f}s =====',
        args.log_file)
    return 0 if total_failed == 0 else 1


if __name__ == '__main__':
    sys.exit(main())

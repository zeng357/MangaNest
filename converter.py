# -*- coding: utf-8 -*-
"""
converter.py — 漫画图片格式转换模块（MangaNest 通用漫画下载器）
把下载好的 JPG/PNG/WebP 漫画图片批量转换成市面上常用阅读格式：

  PDF   —— 通用文档格式（Pillow 逐页追加，内存占用低）
  CBZ   —— 漫画阅读器通用打包格式（zip）
  EPUB  —— 主流电子书格式（EPUB3 结构）
  MOBI  —— Kindle 格式（需安装 Calibre，自动检测 ebook-convert）

支持两种组织方式：
  整本合并 —— 一个漫画文件夹（可含多个章节子文件夹）转成一个文件
  每章单独 —— 每个章节子文件夹各转成一个文件（文件名 = 漫画名_章节名）
"""

import os
import re
import io
import zipfile
import threading

# ==================== 基础工具 ====================

_IMG_EXTS = {'.jpg', '.jpeg', '.png', '.webp', '.bmp', '.gif'}


def natural_key(text):
    """自然排序 key：'第2章' < '第10章'（数字按数值比较）"""
    return [int(part) if part.isdigit() else part.lower()
            for part in re.split(r'(\d+)', str(text))]


def collect_images(folder):
    """递归收集文件夹下所有图片，返回按路径自然排序的绝对路径列表"""
    out = []
    for root, _dirs, files in os.walk(folder):
        for f in files:
            if os.path.splitext(f)[1].lower() in _IMG_EXTS:
                out.append(os.path.join(root, f))
    out.sort(key=lambda p: natural_key(os.path.basename(p)))
    return out


def collect_groups(root):
    """把漫画根目录分成（章节）组。

    Returns:
        [(name, [abs_image_path, ...]), ...]
    - 根目录直接含图片 → 单组（name=目录名）
    - 根目录含子文件夹（每章一个文件夹）→ 每个子文件夹一组（name=子文件夹名）
    - 混合 → 子文件夹各成一组 + 根目录下直接放的图片归为一组（name=目录名）
    """
    direct = []
    sub_dirs = []
    try:
        entries = sorted(os.listdir(root), key=natural_key)
    except OSError:
        return []
    for e in entries:
        p = os.path.join(root, e)
        if os.path.isdir(p):
            sub_dirs.append(p)
        elif os.path.splitext(e)[1].lower() in _IMG_EXTS:
            direct.append(p)

    groups = []
    if direct:
        groups.append((os.path.basename(root.rstrip('/\\')) or root, sorted(direct, key=natural_key)))
    for d in sub_dirs:
        # 跳过封面文件夹（以 0 开头的目录，如 "0"/"00封面"）：其中的图只作为封面使用
        base = os.path.basename(d.rstrip('/\\'))
        if re.match(r'^0(?!\d)', base):
            continue
        imgs = collect_images(d)
        if imgs:
            groups.append((base, imgs))
    return groups


def guess_comic_name(root):
    """由目录名猜测漫画名（去掉纯数字/编号前缀与多余分隔符）"""
    name = os.path.basename(root.rstrip('/\\')) or '漫画'
    name = re.sub(r'^[\d\s._\-]+\s*', '', name)
    name = re.sub(r'\s*[_-]\s*\d+\s*$', '', name)
    return name.strip() or '漫画'


def find_cover(root):
    """查找漫画原始封面：以 0 开头的文件夹（下载器封面专用目录）。

    规则（用户实测口径）：
      - 优先取 0 开头文件夹内的 cover.* 文件
      - 其次取该文件夹内第一张图片
      - 不存在 0 开头文件夹 -> 返回 None（此时转换用漫画第一张图当封面）
    """
    try:
        entries = sorted(os.listdir(root), key=natural_key)
    except OSError:
        return None
    for e in entries:
        p = os.path.join(root, e)
        if os.path.isdir(p) and re.match(r'^0(?!\d)', e):
            imgs = collect_images(p)
            if not imgs:
                continue
            for img in imgs:
                base = os.path.splitext(os.path.basename(img))[0].lower()
                if base.startswith('cover'):
                    return img
            return imgs[0]
    return None


def safe_filename(name):
    """把名字整理成 Windows/Linux 都合法的文件名片段"""
    name = re.sub(r'[\\/:*?"<>|\r\n\t]+', '_', str(name)).strip(' .')
    return name or '未命名'


# ==================== PDF 转换 ====================

def _jpeg_info(data):
    """从 JPEG 字节解析 (width, height, components)。失败返回 None。"""
    try:
        if data[0:2] != b'\xff\xd8':
            return None
        i = 2
        n = len(data)
        while i + 9 < n:
            if data[i] != 0xFF:
                i += 1
                continue
            marker = data[i + 1]
            if marker == 0xFF:  # 填充
                i += 1
                continue
            if marker == 0xD8 or 0xD9 <= marker <= 0xDA:
                i += 2
                continue
            seg_len = int.from_bytes(data[i + 2:i + 4], 'big')
            if seg_len < 2:
                return None
            # SOF 段: 0xC0-0xC3, 0xC5-0xC7, 0xC9-0xCB, 0xCD-0xCF
            if 0xC0 <= marker <= 0xCF and marker not in (0xC4, 0xC8, 0xCC):
                height = int.from_bytes(data[i + 5:i + 7], 'big')
                width = int.from_bytes(data[i + 7:i + 9], 'big')
                comps = data[i + 9]
                return (width, height, comps)
            i += 2 + seg_len
    except Exception:
        return None
    return None


def images_to_pdf(image_paths, out_pdf, log=print):
    """手写 PDF 1.4：JPEG 以 DCTDecode 流逐张嵌入，纯标准库、内存 O(1)。

    不依赖 Pillow 的 append 模式（该模式存在 trailer loop 缺陷），
    也不依赖 img2pdf（需网络安装）。支持 JPG/JPEG；其他格式先经 Pillow 转 JPEG。
    """
    from PIL import Image
    import io as _io

    objects = []  # (num, bytes) 有序写入
    kids = []
    page_nums = []
    xref_offsets = {}
    cur = 2  # 对象1=catalog、2=pages 预留，图像/内容/页面对象从3开始编号

    def emit(obj_bytes):
        nonlocal cur
        cur += 1
        objects.append((cur, obj_bytes))
        return cur

    def jpeg_bytes(path):
        """返回 (jpeg_data, width, height, comps)；非JPEG先转码"""
        ext = os.path.splitext(path)[1].lower()
        if ext in ('.jpg', '.jpeg'):
            with open(path, 'rb') as f:
                data = f.read()
            info = _jpeg_info(data)
            if info:
                return data, info[0], info[1], info[2]
            # 损坏/特殊 JPEG 走 Pillow 转码
        img = Image.open(path)
        rgb = img.convert('RGB')
        buf = _io.BytesIO()
        rgb.save(buf, 'JPEG', quality=90)
        rgb.close(); img.close()
        data = buf.getvalue()
        info = _jpeg_info(data) or (1, 1, 3)
        return data, info[0], info[1], info[2]

    for idx, p in enumerate(image_paths, 1):
        data, w, h, comps = jpeg_bytes(p)
        if not w or not h:
            log(f"    PDF 跳过无法解析的图片: {p}")
            continue
        # 图像 XObject
        img_obj = emit(
            b'<< /Type /XObject /Subtype /Image /Width %d /Height %d '
            b'/ColorSpace %s /BitsPerComponent 8 /Filter /DCTDecode '
            b'/Length %d >>\nstream\n%s\nendstream' % (
                w, h,
                b'/DeviceGray' if comps == 1 else b'/DeviceRGB',
                len(data), data))
        # 内容流：铺满整页
        content = b'q %d 0 0 %d 0 0 cm /Im%d Do Q' % (w, h, img_obj)
        content_obj = emit(
            b'<< /Length %d >>\nstream\n%s\nendstream' % (len(content), content))
        # 页面对象
        page_obj = emit(
            b'<< /Type /Page /Parent 2 0 R /MediaBox [0 0 %d %d] '
            b'/Resources << /XObject << /Im%d %d 0 R >> >> '
            b'/Contents %d 0 R >>' % (w, h, img_obj, img_obj, content_obj))
        kids.append(page_obj)
        page_nums.append(page_obj)
        if idx % 50 == 0:
            log(f"    PDF 已写入 {idx}/{len(image_paths)} 页")

    if not page_nums:
        log("    PDF: 没有可转换的图片")
        return False

    # 收集对象字节，记录 xref 偏移
    body = bytearray()
    for num, blob in objects:
        xref_offsets[num] = len(body)
        body += ('%d 0 obj\n' % num).encode()
        body += blob
        body += b'\nendobj\n'

    kids_list = b' '.join(('%d 0 R' % n).encode() for n in kids)
    catalog = b'<< /Type /Catalog /Pages 2 0 R >>'
    pages = b'<< /Type /Pages /Kids [%s] /Count %d >>' % (kids_list, len(kids))

    header = b'%PDF-1.4\n%\xe2\xe3\xcf\xd3\n'
    xref_pos = len(header) + len(body) + len('1 0 obj\n') + len(catalog) + len(b'\nendobj\n') + \
               len('2 0 obj\n') + len(pages) + len(b'\nendobj\n')
    # 逐段拼装并记录偏移
    out = bytearray()
    out += header
    # 对象1 (catalog) 放在开头方便计算：改为顺序 1=catalog, 2=pages
    # 上面 objects 从3开始（因为 emit 从 cur=0 递增到1,2...）——修正：让 1=catalog 2=pages
    # 重新构造：把 catalog 和 pages 作为对象 1、2，其他对象整体平移
    # 简单做法：先写 catalog/pages，再写其余
    preamble = b'1 0 obj\n' + catalog + b'\nendobj\n' + \
               b'2 0 obj\n' + pages + b'\nendobj\n'
    xref_offsets[1] = len(header)
    xref_offsets[2] = len(header) + len(b'1 0 obj\n') + len(catalog) + len(b'\nendobj\n')
    out += preamble
    # 其余对象（从3号开始）
    for num in range(3, cur + 1):
        # 重新定位：用临时变量记录
        pass
    # 上面循环仅占位，改为显式重建
    out = bytearray()
    out += header
    xref_offsets[1] = len(out)
    out += b'1 0 obj\n' + catalog + b'\nendobj\n'
    xref_offsets[2] = len(out)
    out += b'2 0 obj\n' + pages + b'\nendobj\n'
    for num, blob in objects:
        xref_offsets[num] = len(out)
        out += ('%d 0 obj\n' % num).encode()
        out += blob
        out += b'\nendobj\n'

    xref_start = len(out)
    out += b'xref\n0 %d\n' % (cur + 1)
    out += b'0000000000 65535 f \n'
    for num in range(1, cur + 1):
        out += ('%010d 00000 n \n' % xref_offsets[num]).encode()
    out += b'trailer\n<< /Size %d /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF\n' % (cur + 1, xref_start)

    with open(out_pdf, 'wb') as f:
        f.write(bytes(out))
    log(f"    PDF 完成：{len(page_nums)} 页")
    return True


# ==================== CBZ 转换 ====================

def images_to_cbz(image_paths, out_cbz, log=print):
    """把图片打包成 CBZ（zip），保持顺序与扩展名"""
    try:
        with zipfile.ZipFile(out_cbz, 'w', zipfile.ZIP_STORED) as z:
            for idx, p in enumerate(image_paths, 1):
                ext = os.path.splitext(p)[1].lower() or '.jpg'
                z.write(p, f"{idx:04d}{ext}")
                if idx % 100 == 0:
                    log(f"    CBZ 已打包 {idx}/{len(image_paths)} 张")
        return True
    except Exception as e:
        log(f"    CBZ 打包失败: {e}")
        return False


# ==================== EPUB 转换 ====================

def _epub_xhtml(title, image_names):
    """生成一张 EPUB 页面（XHTML），引用 images/ 下的图片"""
    imgs = '\n'.join(
        f'    <img src="images/{name}" alt="" />' for name in image_names)
    return f"""<?xml version="1.0" encoding="utf-8"?>
<!DOCTYPE html>
<html xmlns="http://www.w3.org/1999/xhtml" xmlns:epub="http://www.idpf.org/2007/ops">
<head><title>{title}</title></head>
<body>
  <h2>{title}</h2>
{imgs}
</body>
</html>"""


def images_to_epub(image_paths, out_epub, comic_name='漫画', chapter_name='', log=print):
    """把图片打包成 EPUB3 漫画书（图片原样存入 OEBPS/images/）"""
    try:
        total = len(image_paths)
        # 每页最多放 40 张，避免单页 DOM 过大；页面标题带页码范围
        per_page = 40
        # 统一使用重命名后的文件名（与 zip 内 OEBPS/images/ 条目一致）
        renames = []
        for idx, p in enumerate(image_paths, 1):
            ext = os.path.splitext(p)[1].lower() or '.jpg'
            renames.append(f'img{idx:05d}{ext}')
        pages = []
        for i in range(0, total, per_page):
            chunk = renames[i:i + per_page]
            pages.append((f"{i + 1}-{i + len(chunk)}", chunk))

        manifest = []
        spine = []
        for idx, (label, _names) in enumerate(pages, 1):
            manifest.append(
                f'    <item id="p{idx}" href="p{idx}.xhtml" media-type="application/xhtml+xml"/>')
            spine.append(f'    <itemref idref="p{idx}"/>')

        imgs_manifest = []
        for idx, p in enumerate(image_paths, 1):
            ext = os.path.splitext(p)[1].lower()
            mtype = {'jpg': 'image/jpeg', 'jpeg': 'image/jpeg',
                     'png': 'image/png', 'webp': 'image/webp',
                     'gif': 'image/gif', 'bmp': 'image/bmp'}.get(ext, 'image/jpeg')
            imgs_manifest.append(
                f'    <item id="img{idx}" href="images/img{idx:05d}{ext}" media-type="{mtype}"/>')

        title = f"{comic_name} {chapter_name}".strip()
        opf = f"""<?xml version="1.0" encoding="utf-8"?>
<package xmlns="http://www.idpf.org/2007/opf" version="3.0" unique-identifier="bookid">
  <metadata xmlns:dc="http://purl.org/dc/elements/1.1/">
    <dc:identifier id="bookid">manganest-{abs(hash(title))}</dc:identifier>
    <dc:title>{title}</dc:title>
    <dc:language>zh</dc:language>
    <dc:creator>MangaNest</dc:creator>
  </metadata>
  <manifest>
    <item id="nav" href="nav.xhtml" media-type="application/xhtml+xml" properties="nav"/>
{chr(10).join(manifest)}
{chr(10).join(imgs_manifest)}
  </manifest>
  <spine>
{chr(10).join(spine)}
  </spine>
</package>
"""
        nav_links = '\n'.join(
            f'    <li><a href="p{i}.xhtml">{title}（{label}）</a></li>'
            for i, (label, _n) in enumerate(pages, 1)
            if i <= 50)
        nav_xhtml = f"""<?xml version="1.0" encoding="utf-8"?>
<!DOCTYPE html>
<html xmlns="http://www.w3.org/1999/xhtml" xmlns:epub="http://www.idpf.org/2007/ops">
<head><title>{title}</title></head>
<body>
  <nav epub:type="toc"><h1>目录</h1><ol>
{nav_links}
  </ol></nav>
</body>
</html>
"""
        container_xml = """<?xml version="1.0" encoding="UTF-8"?>
<container version="1.0" xmlns="urn:oasis:names:tc:opendocument:xmlns:container">
  <rootfiles>
    <rootfile full-path="OEBPS/content.opf" media-type="application/oebps-package+xml"/>
  </rootfiles>
</container>
"""
        with zipfile.ZipFile(out_epub, 'w', zipfile.ZIP_DEFLATED) as z:
            # EPUB 规范：mimetype 必须是第一个条目且不压缩（ZIP_STORED）
            z.writestr(zipfile.ZipInfo('mimetype'), 'application/epub+zip')
            z.writestr('META-INF/container.xml', container_xml)
            z.writestr('OEBPS/content.opf', opf)
            z.writestr('OEBPS/nav.xhtml', nav_xhtml)
            for i, (label, names) in enumerate(pages, 1):
                z.writestr(f'OEBPS/p{i}.xhtml', _epub_xhtml(
                    f'{chapter_name or comic_name}（{label}）', names))
            for idx, p in enumerate(image_paths, 1):
                ext = os.path.splitext(p)[1].lower() or '.jpg'
                z.write(p, f'OEBPS/images/img{idx:05d}{ext}')
            log(f"    EPUB 已打包 {total} 张图片")
        return True
    except Exception as e:
        log(f"    EPUB 打包失败: {e}")
        return False


# ==================== MOBI 转换（需 Calibre） ====================

def find_calibre():
    """查找 Calibre 的 ebook-convert（先环境变量/常见安装路径）"""
    import shutil
    exe = shutil.which('ebook-convert') or shutil.which('ebook-convert.exe')
    if exe:
        return exe
    candidates = [
        r'C:\Program Files\Calibre2\ebook-convert.exe',
        r'C:\Program Files (x86)\Calibre2\ebook-convert.exe',
        os.path.expanduser(r'~\AppData\Roaming\calibre\ebook-convert.exe'),
    ]
    for c in candidates:
        if os.path.exists(c):
            return c
    return None


def images_to_mobi(image_paths, out_mobi, comic_name='漫画', chapter_name='', log=print):
    """先用 EPUB 中转，再调 Calibre 转 MOBI（Kindle）"""
    exe = find_calibre()
    if not exe:
        log("    MOBI 需要安装 Calibre（https://calibre-ebook.com），当前未检测到")
        return False
    epub_tmp = out_mobi.rsplit('.', 1)[0] + '_tmp.epub'
    try:
        if not images_to_epub(image_paths, epub_tmp, comic_name, chapter_name, log):
            return False
        import subprocess
        proc = subprocess.run(
            [exe, epub_tmp, out_mobi],
            capture_output=True, text=True, timeout=600)
        if proc.returncode != 0:
            log(f"    Calibre 转换失败: {(proc.stderr or proc.stdout)[-300:]}")
            return False
        log(f"    MOBI 转换完成")
        return True
    except Exception as e:
        log(f"    MOBI 转换失败: {e}")
        return False
    finally:
        try:
            if os.path.exists(epub_tmp):
                os.remove(epub_tmp)
        except OSError:
            pass


# ==================== 统一入口 ====================

FORMATS = ['PDF', 'CBZ', 'EPUB', 'MOBI']


def convert_comic_folder(root, fmt, out_dir, per_chapter=False, log=print, cancel_event=None,
                       cover_path=None):
    """转换一个漫画根目录。

    Args:
        root: 漫画文件夹（含图片或含章节子文件夹）
        fmt: 'PDF' / 'CBZ' / 'EPUB' / 'MOBI'
        out_dir: 输出目录
        per_chapter: True=每个章节子文件夹单独出文件；False=整本合并一个文件
        cancel_event: threading.Event，设置后停止
        cover_path: 手动指定封面图；None=自动检测（0 开头文件夹的封面，无则用第一张图）
    Returns:
        (ok_count, fail_list)
    封面规则（用户实测口径）：只有第一个输出的文件添加封面；
      封面取「以 0 开头的文件夹」内的 cover.* 或第一张图；没有则不加
      （整本合并时第一张图自然在最前，相当于以第一张为封面页）。
    """
    log(f"开始转换: {root}")
    comic_name = guess_comic_name(root)
    cover = cover_path or find_cover(root)
    if cover:
        log(f"  封面: {os.path.basename(cover)}")
    groups = collect_groups(root)
    if not groups:
        log("  ✗ 未找到任何图片")
        return 0, [root]

    ok = 0
    fails = []
    total_groups = len(groups)

    def _do_one(group_name, image_paths, out_base, with_cover=False):
        nonlocal ok
        if cancel_event and cancel_event.is_set():
            return
        if with_cover and cover:
            image_paths = [cover] + list(image_paths)
        out_file = os.path.join(out_dir, safe_filename(out_base) + '.' + fmt.lower())
        log(f"  → {fmt} : {os.path.basename(out_file)}（{len(image_paths)} 张）")
        if fmt == 'PDF':
            ok_flag = images_to_pdf(image_paths, out_file, log)
        elif fmt == 'CBZ':
            ok_flag = images_to_cbz(image_paths, out_file, log)
        elif fmt == 'EPUB':
            ok_flag = images_to_epub(image_paths, out_file, comic_name, group_name, log)
        elif fmt == 'MOBI':
            ok_flag = images_to_mobi(image_paths, out_file, comic_name, group_name, log)
        else:
            log(f"  ✗ 不支持的格式: {fmt}")
            return
        if ok_flag:
            ok += 1
            log(f"  ✓ 完成: {os.path.basename(out_file)}")
        else:
            fails.append(out_base)
            log(f"  ✗ 转换失败: {out_base}")

    if per_chapter:
        for gi, (gname, imgs) in enumerate(groups, 1):
            if cancel_event and cancel_event.is_set():
                break
            log(f"[{gi}/{total_groups}] 章节组: {gname}")
            _do_one(gname, imgs, f"{comic_name}_{gname}", with_cover=(gi == 1))
    else:
        # 整本合并：所有组按顺序合并（保持章节顺序 + 每组内图片顺序）
        merged = []
        for _gname, imgs in groups:
            merged.extend(imgs)
        if merged:
            _do_one('', merged, comic_name, with_cover=True)

    log(f"转换结束: 成功 {ok} 个，失败 {len(fails)} 个")
    return ok, fails


def convert_merge_folders(folders, fmt, out_dir, log=print, cancel_event=None,
                          cover_path=None):
    """把多个漫画文件夹【合并】成一个文件（合集模式）。

    Args:
        folders: 多个漫画文件夹路径列表（每个文件夹可以是整部漫画或章节目录）
        fmt: 'PDF' / 'CBZ' / 'EPUB' / 'MOBI'
        out_dir: 输出目录
        cancel_event: threading.Event
        cover_path: 手动封面；None=自动检测（取第一个文件夹的封面，无则用第一张图）
    Returns:
        (ok_count, fail_list)
    合并规则：
      - 按列表顺序合并所有文件夹的图片（每个文件夹内部保持章节顺序）
      - 封面取第一个找到的封面（0 开头文件夹），只作为全书的封面页
      - 输出文件名 = 第一个文件夹的漫画名（多个时加“合集”）
    """
    if not folders:
        log("  没有可合并的文件夹")
        return 0, ['(空列表)']
    comic_name = guess_comic_name(folders[0])
    if len(folders) > 1:
        comic_name = comic_name + '等合集'
    log(f"开始合并 {len(folders)} 个文件夹 → 1 个文件: {comic_name}")

    merged = []
    cover = cover_path
    for fi, f in enumerate(folders, 1):
        if cancel_event and cancel_event.is_set():
            log("—— 已取消 ——")
            break
        groups = collect_groups(f)
        if not groups:
            log(f"  [{fi}/{len(folders)}] 跳过（无图片）: {f}")
            continue
        if cover is None:
            cover = find_cover(f)
        for _gname, imgs in groups:
            merged.extend(imgs)
        log(f"  [{fi}/{len(folders)}] 已合并: {os.path.basename(f)}（{len(merged)} 张累计）")
    if not merged:
        log("  ✗ 未合并到任何图片")
        return 0, [comic_name]

    if cover:
        merged = [cover] + merged
        log(f"  封面: {os.path.basename(cover)}（全书第一页）")

    out_file = os.path.join(out_dir, safe_filename(comic_name) + '.' + fmt.lower())
    log(f"  → {fmt} : {os.path.basename(out_file)}（{len(merged)} 页）")
    if fmt == 'PDF':
        ok_flag = images_to_pdf(merged, out_file, log)
    elif fmt == 'CBZ':
        ok_flag = images_to_cbz(merged, out_file, log)
    elif fmt == 'EPUB':
        ok_flag = images_to_epub(merged, out_file, comic_name, '', log)
    elif fmt == 'MOBI':
        ok_flag = images_to_mobi(merged, out_file, comic_name, '', log)
    else:
        log(f"  ✗ 不支持的格式: {fmt}")
        return 0, [comic_name]
    if ok_flag:
        log(f"  ✓ 完成: {os.path.basename(out_file)}")
        return 1, []
    log(f"  ✗ 合并转换失败: {comic_name}")
    return 0, [comic_name]

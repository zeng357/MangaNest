# -*- coding: utf-8 -*-
"""
用 BallonsTranslator 汉化长条漫画页（切片版）
------------------------------------------------
为什么需要切片：
    BallonsTranslator 的文本检测器会把整张图缩放到 detect_size（默认 1024）再检测。
    条漫单页常是 800x8000 甚至 800x9000，缩放后行高只剩 3~5 像素，
    细行（如带发光描边的标题行）会整行漏检 —— 漏检的英文不会被抹掉，也不会被翻译，
    于是成品里仍然看得见英文。
    把长页切成 ~2000px 的段，检测时几乎 1:1，漏检明显减少。

用法：
    python bt_slice_translate.py --input "<原图目录>" --output "<输出目录>"
    python bt_slice_translate.py --input ... --output ... --slice 2000 --overlap 60
    python bt_slice_translate.py --input ... --output ... --keep-temp      # 保留切片与中间结果
    python bt_slice_translate.py --input "<单张图>" --output "<目录>"       # 也支持单文件

产物：
    <输出目录>\\*.png            汉化后的整页（与原图同名）
    <输出目录>\\中英对照.txt      逐页逐块的原文/译文对照（来源 BallonsTranslator 导出的 txt）
    <输出目录>\\_bt_work\\        切片、BT 结果、日志（--keep-temp 时保留，便于复查）

注意：
    * BT headless 跑完退出码常为 -1073740791（0xC0000409），产物已落盘，属正常崩退。
    * 需要 BallonsTranslator 已按 config\\config.json 配好（本项目为 cuda + MyMemory）。
"""

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import time

from PIL import Image

PROJECT_DIR = os.path.dirname(os.path.abspath(__file__))
BT_ROOT = os.path.join(PROJECT_DIR, 'tools', 'Ballonstranslator_win_minium')
BT_PY = os.path.join(BT_ROOT, 'ballontrans_pylibs_win', 'python.exe')

IMAGE_EXTS = ('.jpg', '.jpeg', '.png', '.webp', '.bmp')


def log(msg):
    print('[bt_slice] ' + msg, flush=True)


def list_images(path):
    if os.path.isfile(path):
        return [path]
    names = sorted(
        [n for n in os.listdir(path) if n.lower().endswith(IMAGE_EXTS)],
        key=lambda s: (len(s), s)
    )
    return [os.path.join(path, n) for n in names]


def _blank_cut(img, target_y, search):
    """在 target_y 附近找最「空白」的行当切点。

    条漫里气泡/文字被硬切在切片边界上时，BT 那一侧会把被切断的英文检测成残行，
    抹不干净就会和中文叠在一起。切在空白带（无文字、无明显渐变）就基本不会出现。
    返回切点 y；找不到合适空白行时原样返回 target_y。
    """
    w, h = img.size
    y0 = max(int(search), 1)
    y1 = min(h - 1, target_y + int(search))
    y_start = max(1, target_y - int(search))
    if y1 - y_start < 3:
        return target_y

    try:
        import numpy as np
        arr = np.asarray(img.convert('L').crop((0, y_start, w, y1)), dtype=np.float32)
        row_std = arr.std(axis=1)
        row_mean = arr.mean(axis=1)
    except Exception:
        row_std = None

    if row_std is None:
        return target_y

    # 空白带：行内几乎没有对比（白底或纯色），且整体偏亮
    ok = (row_std < 10) & (row_mean > 180)
    if not ok.any():
        # 放宽一次：只要求低对比
        ok = row_std < 14
    if not ok.any():
        return target_y

    idx = np.where(ok)[0]
    abs_y = idx + y_start
    best = abs_y[np.argmin(np.abs(abs_y - target_y))]
    return int(best)


def slice_image(src, dst_dir, page_idx, slice_h, overlap):
    """把一张长页切成若干段，返回 [(切片路径, 起始 y, 结束 y, 是否首段, 该段高度)]

    切点会吸附到画面空白带（见 _blank_cut），避免把气泡文字硬切在边界上。
    overlap 保留为相邻段的安全余量（空白切点本身已足够，默认给小值）。
    """
    im = Image.open(src).convert('RGB')
    w, h = im.size
    items = []
    if h <= slice_h:
        p = os.path.join(dst_dir, '%03d_000.png' % page_idx)
        im.save(p)
        return [(p, 0, h, True, h)]

    cuts = [0]
    cur = 0
    i = 0
    while cur < h and i < 200:
        target = cur + slice_h
        if target >= h:
            cuts.append(h)
            break
        cut = _blank_cut(im, target, min(400, slice_h // 4))
        if cut <= cur + slice_h // 2:
            cut = target
        cuts.append(cut)
        cur = cut - overlap if overlap > 0 else cut
        i += 1

    # 依据 cuts 生成图片段
    for k in range(len(cuts) - 1):
        y0 = cuts[k]
        y1 = cuts[k + 1]
        if y0 <= 0:
            y0 = 0
        crop = im.crop((0, y0, w, y1))
        p = os.path.join(dst_dir, '%03d_%03d.png' % (page_idx, k))
        crop.save(p)
        items.append((p, y0, y1, k == 0, y1 - y0))
    return items


def run_bt(chunk_dir, log_path, extra_env=None):
    env = os.environ.copy()
    env['HF_ENDPOINT'] = 'https://hf-mirror.com'
    if extra_env:
        env.update(extra_env)
    cmd = [BT_PY, '-m', 'ballontranslator', '--headless',
           '--exec_dirs', chunk_dir, '--export-translation-txt']
    with open(log_path, 'w', encoding='utf-8', errors='ignore') as f:
        proc = subprocess.run(cmd, cwd=BT_ROOT, stdout=f, stderr=subprocess.STDOUT,
                              env=env, stdin=subprocess.DEVNULL)
    return proc.returncode


def _result_path(result_dir, chunk_path):
    """返回切片对应的结果图路径；不存在返回 None（BT 有时只写 .jpg）"""
    name = os.path.basename(chunk_path)
    p = os.path.join(result_dir, name)
    if os.path.exists(p):
        return p
    alt = os.path.join(result_dir, os.path.splitext(name)[0] + '.jpg')
    if os.path.exists(alt):
        return alt
    return None


def run_bt_with_retry(chunks, work, metas, log_fn, max_rounds=3):
    """跑 BT，并对没有产出结果图的切片自动补跑。

    BT headless 收尾常崩退（0xC0000409），崩之前可能已经漏掉少量切片的结果图；
    漏掉的那段会保留原英文，正是「成品里还有英语」的来源之一。
    补跑做法：把缺失切片复制到一个临时目录单独再跑一轮，结果图搬回 result 目录。
    """
    result_dir = os.path.join(chunks, 'result')
    rc = run_bt(chunks, os.path.join(work, 'bt_run.log'))
    log_fn('BT 退出码 %s' % rc)

    for rnd in range(1, max_rounds + 1):
        missing = []
        for _, meta in metas:
            for path, *_rest in meta:
                if _result_path(result_dir, path) is None:
                    missing.append(path)
        if not missing:
            if rnd == 1:
                log_fn('全部切片均有结果')
            break
        log_fn('第 %d 轮补跑，缺 %d 段：%s'
               % (rnd, len(missing), ', '.join(os.path.basename(m) for m in missing)))
        sub = os.path.join(work, 'retry%d' % rnd)
        os.makedirs(sub, exist_ok=True)
        for p in missing:
            shutil.copy2(p, os.path.join(sub, os.path.basename(p)))
        sub_rc = run_bt(sub, os.path.join(work, 'bt_run_retry%d.log' % rnd))
        log_fn('补跑退出码 %s' % sub_rc)
        sub_result = os.path.join(sub, 'result')
        if not os.path.isdir(sub_result):
            continue
        os.makedirs(result_dir, exist_ok=True)
        for n in os.listdir(sub_result):
            src_p = os.path.join(sub_result, n)
            if os.path.isfile(src_p):
                shutil.copy2(src_p, os.path.join(result_dir, n))

    still = []
    for _, meta in metas:
        for path, *_rest in meta:
            if _result_path(result_dir, path) is None:
                still.append(os.path.basename(path))
    if still:
        log_fn('补跑后仍缺 %d 段：%s' % (len(still), ', '.join(still)))
    return rc


def restore_unrecognized(chunks, log_fn):
    """把「OCR 没认出文字」的空块还原成原图。

    BT 对于识别不出文字的区域照样会 inpaint 抹掉，于是花样大字（如喊叫的
    “RIA!??”）被抹成空洞，气泡里什么都不剩。这类块既然没文字可译，
    就保留原图更合理。依据是 BT 导出的工程 json（pages[切片名] -> 块列表）。
    """
    jsons = [os.path.join(chunks, n) for n in os.listdir(chunks)
             if n.startswith('imgtrans_') and n.endswith('.json')] if os.path.isdir(chunks) else []
    if not jsons:
        return 0

    pages = {}
    for p in jsons:
        try:
            with open(p, 'r', encoding='utf-8') as f:
                d = json.load(f)
            pages.update(d.get('pages') or {})
        except Exception as e:
            log_fn('读工程 json 失败 %s: %s' % (os.path.basename(p), e))

    restored = 0
    for page_name, blocks in pages.items():
        if not blocks:
            continue
        chunk_path = os.path.join(chunks, page_name)
        if not os.path.exists(chunk_path):
            continue
        res_path = _result_path(os.path.join(chunks, 'result'), chunk_path)
        if not res_path:
            continue
        src = Image.open(chunk_path).convert('RGB')
        res = Image.open(res_path).convert('RGB')
        changed = False
        for b in blocks:
            # BT 工程里的 text 可能是字符串，也可能是按行的字符串列表
            raw_text = b.get('text')
            if isinstance(raw_text, (list, tuple)):
                raw_text = ' '.join(str(x) for x in raw_text)
            if (raw_text or '').strip():
                continue
            xyxy = b.get('xyxy') or []
            if len(xyxy) != 4:
                continue
            x1, y1, x2, y2 = [int(round(v)) for v in xyxy]
            pad = 8
            x1 = max(0, x1 - pad); y1 = max(0, y1 - pad)
            x2 = min(src.size[0], x2 + pad); y2 = min(src.size[1], y2 + pad)
            if x2 <= x1 or y2 <= y1:
                continue
            res.paste(src.crop((x1, y1, x2, y2)), (x1, y1))
            changed = True
            restored += 1
        if changed:
            res.save(res_path)
    if restored:
        log_fn('空块还原原图 %d 处（OCR 未识别，属花样大字/音效）' % restored)
    return restored


def _neutralize_residual_text(base_rgb, blocks, return_mask=False):
    """把 BT 没抹干净的残留字迹（句末标点、越出检测框的字）补掉。

    BT 的 mask 只覆盖检测框内的笔画，像「BUT,」里逗号掉在框外这种情况，
    结果图里会留下一个孤零零的英文逗号。这里在「已抹字底图」上，
    对每个块的外扩矩形再做一次局部 inpaint（只对浅底区域生效，避免糊掉画面）。
    """
    import cv2
    import numpy as np

    img = cv2.cvtColor(np.array(base_rgb), cv2.COLOR_RGB2BGR)
    h, w = img.shape[:2]
    total = np.zeros((h, w), np.uint8)
    wm_re = re.compile(r'(LUACOMIC|UACOMIC|DOMAIN|SCAM|\.NET|DSC\.GG|HTTP)', re.IGNORECASE)
    for blk in blocks or []:
        xyxy = blk.get('xyxy') or []
        if len(xyxy) != 4:
            continue
        x0, y0, x1, y1 = [int(v) for v in xyxy]
        is_wm = bool(wm_re.search(' '.join(blk.get('text') or []) if isinstance(blk.get('text'), list)
                                  else str(blk.get('text') or '')))
        pad = 16
        rects = [(max(0, x0 - pad), max(0, y0 - pad), min(w, x1 + pad), min(h, y1 + pad), False)]
        if is_wm:
            # 水印行的残字会溢出检测框很远，整块区域单独开一条「只认亮字」的通道
            rects.append((0, max(0, y0 - 8), w, min(h, y1 + 520), True))
        for rx0, ry0, rx1, ry1, bright_only in rects:
            if rx1 <= rx0 or ry1 <= ry0:
                continue
            roi = img[ry0:ry1, rx0:rx1]
            L = cv2.cvtColor(roi, cv2.COLOR_BGR2GRAY)
            mean_l = float(L.mean())
            if bright_only:
                # 只清「小块的亮字」：成片的亮部（白裙、logo 底光）连通面积大，保留
                raw = (L > 190).astype(np.uint8)
                n, lab, stats, _ = cv2.connectedComponentsWithStats(raw, 8)
                keep = np.zeros_like(raw)
                for i in range(1, n):
                    area = int(stats[i, cv2.CC_STAT_AREA])
                    bh = int(stats[i, cv2.CC_STAT_HEIGHT])
                    if 25 <= area <= 4000 and bh <= 110:
                        keep[lab == i] = 1
                ink = keep.astype(bool)
            elif mean_l >= 170:
                # 浅底：找比底色暗的残字
                bg = cv2.medianBlur(L, 21)
                ink = ((bg.astype(np.int16) - L.astype(np.int16)) > 45) & (L < 190)
            elif is_wm:
                # 深底 + 站点水印行：用 top-hat 抓比周边亮的残字（笔画宽也能吃住）
                k = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (61, 61))
                th = cv2.morphologyEx(L, cv2.MORPH_TOPHAT, k)
                ink = th > 22
            else:
                continue
            m = ink.astype(np.uint8) * 255
            if not m.any():
                continue
            m = cv2.dilate(m, np.ones((3, 3), np.uint8), 1)
            out = cv2.inpaint(roi, m, 4, cv2.INPAINT_TELEA)
            img[ry0:ry1, rx0:rx1] = out
            # 只把真正被抹掉的笔画像素记进掩膜：回贴中文时避开它们，避免残字被搬回来
            total[ry0:ry1, rx0:rx1] |= (m > 0).astype(np.uint8)
    if not total.any():
        return (base_rgb, total.astype(bool)) if return_mask else base_rgb
    cleaned = Image.fromarray(cv2.cvtColor(img, cv2.COLOR_BGR2RGB))
    return (cleaned, total.astype(bool)) if return_mask else cleaned


def compose_slice(base_path, result_path, blocks):
    """底图用 BT 的 inpainted（已抹字），文字层从 BT 的 result 里抠出来贴回去。

    这样 BT 渲染的中文位置/字号完全保留，但底图上残留的英文标点被我们补掉了。
    """
    import numpy as np

    if not (os.path.isfile(base_path) and os.path.isfile(result_path)):
        return None
    base = Image.open(base_path).convert('RGB')
    res = Image.open(result_path).convert('RGB')
    if res.size != base.size:
        res = res.resize(base.size)
    b = np.array(base)
    r = np.array(res)
    d = np.abs(b.astype(np.int16) - r.astype(np.int16)).max(axis=2)
    diff = (d > 10)
    cleaned, cmask = _neutralize_residual_text(base, blocks, return_mask=True)
    c = np.array(cleaned)
    out = c.copy()
    # 清洗过的像素不再从 result 回贴，否则残字会被原样搬回来
    keep = diff & (~cmask)
    out[keep] = r[keep]
    return Image.fromarray(out)


def stitch(src_meta, result_dir, out_path, chunks_dir=None, blocks_map=None):
    """把切片结果按原始 y 区间精确拼回整页（空白切点保证接缝不落在文字上）。"""
    total_h = max(y1 for (_, _, y1, _, _) in src_meta)
    w = Image.open(src_meta[0][0]).size[0]
    canvas = Image.new('RGB', (w, total_h), (255, 255, 255))
    blocks_map = blocks_map or {}

    for path, y0, y1, _is_first, h in src_meta:
        res = _result_path(result_dir, path)
        if res is None:
            log('缺结果，跳过：' + os.path.basename(path))
            continue
        name = os.path.basename(path)
        img = res
        if chunks_dir and name in blocks_map:
            base = os.path.join(chunks_dir, 'inpainted', name)
            composed = compose_slice(base, res, blocks_map.get(name))
            if composed is not None:
                img = composed
        img = Image.open(img).convert('RGB') if isinstance(img, str) else img.convert('RGB')
        if img.size[0] != w:
            img = img.resize((w, img.size[1]))
        if img.size[1] != h:
            img = img.resize((w, h))
        canvas.paste(img.crop((0, 0, w, min(h, img.size[1]))), (0, y0))

    canvas.save(out_path, quality=95)
    return out_path


def load_blocks_map(chunks_dir):
    """读取 BT 的 imgtrans_*.json，返回 {切片文件名: 块列表}。"""
    out = {}
    if not chunks_dir or not os.path.isdir(chunks_dir):
        return out
    for n in os.listdir(chunks_dir):
        if n.startswith('imgtrans_') and n.endswith('.json'):
            p = os.path.join(chunks_dir, n)
            try:
                with open(p, 'r', encoding='utf-8') as f:
                    d = json.load(f)
            except Exception:
                continue
            for k, v in (d.get('pages') or {}).items():
                out[os.path.basename(k)] = v
    return out


def collect_translation(work_dir, out_dir):
    """把 BT 导出的中英对照 txt 汇总成一个文件。"""
    txts = []
    for root, _, files in os.walk(work_dir):
        for n in files:
            if n.endswith('_translation.txt'):
                txts.append(os.path.join(root, n))
    if not txts:
        return None
    dst = os.path.join(out_dir, '中英对照.txt')
    with open(dst, 'w', encoding='utf-8') as out:
        for p in sorted(txts):
            with open(p, 'r', encoding='utf-8', errors='ignore') as f:
                out.write(f.read())
                out.write('\n\n')
    return dst


def translate_folder(src, out_dir=None, text_only=False, slice_h=2000, overlap=60,
                     log=log, keep_temp=False):
    """供 GUI 与命令行共用的入口：把 src（目录或单张图）整章汉化到 out_dir。

    text_only=True 时只导出中英对照 txt，不拼图（省掉拼图与磁盘占用）。
    返回输出目录绝对路径；出错抛异常。
    """
    if not os.path.exists(BT_PY):
        raise RuntimeError('找不到 BallonsTranslator 解释器：' + BT_PY)

    src = os.path.abspath(src)
    if out_dir is None:
        if os.path.isdir(src):
            out_dir = src.rstrip('\\/') + ' 汉化版'
        else:
            out_dir = os.path.join(os.path.dirname(src), '汉化版')
    out_dir = os.path.abspath(out_dir)
    os.makedirs(out_dir, exist_ok=True)

    work = os.path.join(out_dir, '_bt_work')
    chunks = os.path.join(work, 'chunks')
    if os.path.exists(work):
        shutil.rmtree(work, ignore_errors=True)
    os.makedirs(chunks, exist_ok=True)

    images = list_images(src)
    if not images:
        raise RuntimeError('输入目录里没有图片：' + src)
    log('待处理 %d 页，切片高度 %d，重叠 %d' % (len(images), slice_h, overlap))

    t0 = time.time()
    metas = []
    for idx, img in enumerate(images):
        metas.append((img, slice_image(img, chunks, idx, slice_h, overlap)))
    total_chunks = sum(len(m) for _, m in metas)
    log('切片完成，共 %d 段，用时 %.1fs' % (total_chunks, time.time() - t0))

    t1 = time.time()
    log('开始调用 BallonsTranslator（GPU）…')
    run_bt_with_retry(chunks, work, metas, log)
    restore_unrecognized(chunks, log)
    log('BT 阶段结束，用时 %.1f 分钟' % ((time.time() - t1) / 60.0))

    result_dir = os.path.join(chunks, 'result')
    if not os.path.isdir(result_dir):
        raise RuntimeError('BallonsTranslator 未产出结果目录：' + result_dir)

    if not text_only:
        blocks_map = load_blocks_map(chunks)
        for img, meta in metas:
            name = os.path.splitext(os.path.basename(img))[0] + '.png'
            out_path = os.path.join(out_dir, name)
            stitch(meta, result_dir, out_path, chunks_dir=chunks, blocks_map=blocks_map)
            log('已生成 ' + out_path)

    txt = collect_translation(chunks, out_dir)
    if txt:
        log('中英对照：' + txt)

    if not keep_temp:
        shutil.rmtree(work, ignore_errors=True)
    log('全部完成，用时 %.1f 分钟' % ((time.time() - t0) / 60.0))
    return out_dir


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--input', required=True, help='原图目录或单张图')
    ap.add_argument('--output', default=None, help='输出目录（留空 = 源目录旁自动生成「… 汉化版」）')
    ap.add_argument('--slice', type=int, default=2000, dest='slice_h', help='切片高度，默认 2000')
    ap.add_argument('--overlap', type=int, default=60, help='切片重叠，默认 60（切点已吸附空白带）')
    ap.add_argument('--text-only', action='store_true', help='只导出中英对照，不拼图')
    ap.add_argument('--keep-temp', action='store_true', help='保留临时目录')
    args = ap.parse_args()

    try:
        translate_folder(args.input, args.output, text_only=args.text_only,
                         slice_h=args.slice_h, overlap=args.overlap,
                         keep_temp=args.keep_temp)
    except Exception as e:
        log('失败：%s' % e)
        return 5
    return 0


if __name__ == '__main__':
    sys.exit(main())

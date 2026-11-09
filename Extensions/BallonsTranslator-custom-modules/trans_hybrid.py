"""混合翻译通道 Hybrid：MyMemory 优先，额度耗尽/失败自动切本地 NLLB

背景
----
MyMemory 免费接口译漫画对话最自然（质量最高），但按 IP 限当日额度，
429 之后 BallonsTranslator 拿不到译文就会把**英文原文**画回图上，
成品看起来「还是英文」。纯离线 Opus-MT 质量太差，NLLB-600M 尚可。

策略
----
逐块翻译：先打 MyMemory；一旦判定额度耗尽（返回 429 / MYMEMORY WARNING /
多次失败），本进程余下所有块直接走本地 NLLB，不再浪费时间重试。
另外对原文做「大写归一化」：漫画字幕多为全大写，NLLB 对全大写译文明显变差
（孤儿院译成「手术时代」），转成正常大小写后恢复正常，同时修 OCR 常见的
把 I 认成 1、O 认成 0 之类。

放置位置：<BallonsTranslator>/custom_modules/trans_hybrid.py（文件名必须以 trans_ 开头）

依赖
----
    ballontrans_pylibs_win\\python.exe -m pip install transformers==4.46.3 sentencepiece
模型
----
    data\\models\\nllb-200-distilled-600M（约 2.4GB）
    sentencepiece 在含中文路径下报 Illegal byte sequence，故建 ASCII 目录联接
    C:\\bt_models\\nllb-200-distilled-600M 指向实际目录。
"""

import json
import os
import re
import threading
import time
import urllib.parse
import urllib.request
from copy import deepcopy

import torch

from ballontranslator.modules.translators.base import (
    BaseTranslator,
    register_translator,
)

PROGRAM_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

NLLB_MODEL_DIR_CANDIDATES = [
    os.environ.get('NLLB_MODEL_DIR', ''),
    r'C:\bt_models\nllb-200-distilled-600M',
    os.path.join(PROGRAM_DIR, 'data', 'models', 'nllb-200-distilled-600M'),
]


def _resolve_nllb_dir():
    for d in NLLB_MODEL_DIR_CANDIDATES:
        if d and os.path.isdir(d):
            return d
    return NLLB_MODEL_DIR_CANDIDATES[-1]


NLLB_MODEL_DIR = _resolve_nllb_dir()

NLLB_LANG = {
    'en': 'eng_Latn',
    'zh': 'zho_Hans',
    'zh-tw': 'zho_Hant',
    'ja': 'jpn_Jpan',
    'ko': 'kor_Hang',
}

MYMEMORY_URL = 'https://api.mymemory.translated.net/get'

# ---------------------------------------------------------------- 文本预处理

_DIGIT_AS_LETTER = str.maketrans({
    '0': 'O',
    '1': 'I',
    '5': 'S',
    '7': 'T',
})


def _fix_ocr_digits(text: str) -> str:
    """把夹在字母中间的 0/1/5/7 还原成 O/I/S/T（RIGH7 AWAY -> RIGHT AWAY）。"""

    def repl(m):
        return m.group(0).translate(_DIGIT_AS_LETTER)

    return re.sub(r'(?<=\w)[0157](?=\w)', repl, text)


def _normalize_case(text: str) -> str:
    """全大写 → 正常句首大写。已混合大小写的文本原样返回。"""
    letters = [c for c in text if c.isalpha()]
    if not letters:
        return text
    upper = sum(1 for c in letters if c.isupper())
    if upper / len(letters) < 0.65:
        return text
    low = text.lower()
    # 句末后重新大写，保留问号感叹号
    return re.sub(r'(^|[.!?]\s+)([a-z])', lambda m: m.group(1) + m.group(2).upper(), low)


def _normalize(text: str) -> str:
    t = (text or '').replace('’', "'").replace('‘', "'")
    t = re.sub(r'\s+', ' ', t).strip()
    t = _fix_ocr_digits(t)
    # OCR 把断行/装饰识别成一串逗号，例如「翻译,,,,阅读」→「翻译,阅读」
    t = re.sub(r'\s*[,，]{2,}\s*', ', ', t)
    t = re.sub(r'[,，]\s*[,，]$', ',', t)
    t = _normalize_case(t)
    return t


# 水印/网址/站点署名：这类文本不翻译，原样保留，避免出现「UACOMIC.NET -> 互联网」这种胡译
# 强规则：只要出现站点/网址/社群标识，无论多长都原样保留
_WATERMARK_STRONG = re.compile(
    r'(https?://|www\.|\.(?:net|com|org|io)\b|discord|dsc\.gg|patreon|luacomic|uacomic|@[\w.]+)',
    re.IGNORECASE,
)
# 弱规则：只有短文本（字幕/署名）才保留
_WATERMARK_WEAK = re.compile(
    r'(comic|manga|scan|raw|fan[\s-]?trans|translate|©|™)',
    re.IGNORECASE,
)


_URL_RE = re.compile(
    r'(https?://\S+|www\.\S+|[\w-]+\.(?:net|com|org|io)\S*|dsc\.gg/\S+|@[\w.]+)',
    re.IGNORECASE,
)
_SHORT_TAG_RE = re.compile(r'(comic|manga|scan|raw|fan[\s-]?trans|translate|discord|patreon|©|™)',
                           re.IGNORECASE)


def _is_watermark(text: str) -> bool:
    """网址/社群号这类行不翻译（避免 UACOMIC.NET -> 互联网）。

    判定不按长度，而是看「去掉网址后还剩多少实义字母」：
    - LUACOMIC.NET、DSC.GG/LUASCANS  → 剩不下东西 → 保留原文
    - READ AT LUACOMIC.NET FOR THE FASTEST RELEASES! → 有实义 → 正常翻译
    """
    t = (text or '').strip()
    if not t:
        return False
    stripped = _URL_RE.sub(' ', t)
    letters = sum(1 for c in stripped if c.isalnum())
    if stripped != t and letters < 10:
        return True
    if _SHORT_TAG_RE.search(t) and letters < 12:
        return True
    return False


# 译错的专有名词，出图前纠正（NLLB 把 Discord 译成「分歧」）
_GLOSSARY_FIX = [
    ('分歧', 'Discord'),
    ('不和睦', 'Discord'),
    ('Ria', '莉娅'),
    ('RIA', '莉娅'),
]


def _apply_glossary(text: str) -> str:
    for a, b in _GLOSSARY_FIX:
        text = text.replace(a, b)
    return text


_SPLIT_SENT = re.compile(r'([，,。！!？?；;、\n]+)')


def _dedupe_repeat(text: str) -> str:
    """NLLB 偶尔陷入重复（同一句被抄好几遍），合并掉重复子句。"""
    parts = _SPLIT_SENT.split(text or '')
    if len(parts) < 3:
        return text
    out, seen = [], []
    last_key = None
    for i in range(0, len(parts), 2):
        seg = parts[i]
        key = re.sub(r'\s+', '', seg)
        if key and key == last_key:
            continue
        if key:
            seen.append(key)
            last_key = key
        out.append(seg)
        if i + 1 < len(parts):
            out.append(parts[i + 1])
    joined = ''.join(out)
    # 整体是「同一句 × N」或只有一两种变体反复出现的情况
    if seen and len(seen) >= 2 and len(set(seen)) == 1:
        return seen[0]
    if seen:
        cnt = {}
        for s in seen:
            cnt[s] = cnt.get(s, 0) + 1
        if len(seen) >= 3 and max(cnt.values()) >= 2 and len(cnt) <= 2:
            return max(cnt, key=len)
    return re.sub(r'[，,]{2,}', '，', joined).strip(' ，,')


def _postprocess(src: str, out: str) -> str:
    out = _apply_glossary(_dedupe_repeat(out or ''))
    # 译文里残留的英文标点：句末英文逗号/句点换成中文，去掉多余分隔
    out = re.sub(r'\s*,\s*$', '，', out)
    out = re.sub(r'[，,]{2,}', '，', out).strip()
    out = re.sub(r'[，,]+$', '', out)
    # 尾部残留的英文句点/逗号组合（「任务,.」）
    out = out.rstrip(' \t\r\n')
    out = re.sub(r'[，,\.．]+$', '', out).rstrip(' \t\r\n')
    # OCR 把水印识别成的拉丁乱码（(LUACOMIC.NET)ANY) 之类）直接丢掉
    out = re.sub(r'[（(]\s*[A-Za-z0-9\.\-_/:]{3,}(?:[)）]?[A-Za-z0-9\.\-_/:]*[)）]?)', '', out).strip()
    urls = re.findall(r'(?:https?://[^\s)）]+|[\w-]+\.(?:net|com|org|io)|dsc\.gg/[\w]+)',
                      src or '', re.IGNORECASE)
    if urls and out:
        u = urls[0].rstrip('.,!')
        if u.lower() not in out.lower():
            if re.search(r'read\s+(?:on|at)', src or '', re.IGNORECASE):
                out = '%s，请到 %s 阅读' % (out, u)
            else:
                out = '%s（%s）' % (out, u)
    return out


# ---------------------------------------------------------------- 参数

HYBRID_PARAMS = {
    'description': 'MyMemory 优先（质量最好），当日额度耗尽自动切本地 NLLB（离线、无额度）',
    'timeout': {
        'type': 'spinbox',
        'options': [5, 60],
        'value': 20,
        'display_name': 'MyMemory 超时(秒)',
    },
    'retries': {
        'type': 'spinbox',
        'options': [0, 5],
        'value': 2,
        'display_name': 'MyMemory 重试次数',
    },
    'normalize': {
        'type': 'checkbox',
        'value': True,
        'display_name': '原文大写归一化/修 OCR 数字',
    },
    'batch_size': {
        'type': 'spinbox',
        'options': [1, 32],
        'value': 8,
        'display_name': 'NLLB 批大小',
    },
    'num_beams': {
        'type': 'spinbox',
        'options': [1, 8],
        'value': 4,
        'display_name': 'NLLB beam 数',
    },
    'use_fp16': {
        'type': 'checkbox',
        'value': True,
        'display_name': 'NLLB 半精度(省显存)',
    },
}


@register_translator('Hybrid')
class TransHybrid(BaseTranslator):

    params = deepcopy(HYBRID_PARAMS)

    # 逐块翻译，行数才不会错位
    concate_text = False

    _lock = threading.Lock()
    _nllb_model = None
    _nllb_tok = None

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # 本次任务内 MyMemory 是否已不可用（额度耗尽）
        self._my_dead = False
        self._my_dead_reason = ''

    def _setup_translator(self):
        self.lang_map = {
            'Auto': 'en',
            'English': 'en',
            '简体中文': 'zh',
            '繁體中文': 'zh-tw',
            '日本語': 'ja',
            '한국어': 'ko',
        }
        self.api_lang_source = NLLB_LANG.get(self.lang_map.get(self.lang_source, 'en'), 'eng_Latn')
        self.api_lang_target = NLLB_LANG.get(self.lang_map.get(self.lang_target, 'zh'), 'zho_Hans')

    def set_source(self, lang: str):
        super().set_source(lang)
        self.api_lang_source = NLLB_LANG.get(self.lang_map.get(self.lang_source, 'en'), 'eng_Latn')

    def set_target(self, lang: str):
        super().set_target(lang)
        self.api_lang_target = NLLB_LANG.get(self.lang_map.get(self.lang_target, 'zh'), 'zho_Hans')

    # ------------------------------------------------------------ MyMemory

    def _mymemory_one(self, text: str) -> str:
        """返回译文；不可用/失败返回 None（并把 self._my_dead 置 True 的条件交给调用处判断）"""
        pair = 'en|zh-CN'
        if self.lang_target == '繁體中文':
            pair = 'en|zh-TW'
        url = '%s?q=%s&langpair=%s&de=%s' % (
            MYMEMORY_URL,
            urllib.parse.quote(text),
            urllib.parse.quote(pair, safe='|'),
            'comic@local',
        )
        try:
            timeout = float(self.get_param_value('timeout'))
        except Exception:
            timeout = 20.0
        try:
            req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                payload = json.loads(resp.read().decode('utf-8', 'replace'))
            data = payload.get('responseData') or {}
            out = (data.get('translatedText') or '').strip()
            status = payload.get('responseStatus')
            details = str(payload.get('responseDetails') or '')
            if 'MYMEMORY WARNING' in details.upper() or 'QUOTA' in details.upper():
                self._my_dead = True
                self._my_dead_reason = details[:120]
                return None
            if status not in (200, '200'):
                if str(status) in ('429', '403'):
                    self._my_dead = True
                    self._my_dead_reason = 'status %s' % status
                return None
            if not out or out.upper() == text.upper():
                return None
            return out
        except Exception as exc:  # noqa: BLE001
            self.logger.warning('MyMemory 请求失败: %r' % exc)
            return None

    def _mymemory_batch(self, texts):
        try:
            retries = int(self.get_param_value('retries'))
        except Exception:
            retries = 2
        out = []
        for t in texts:
            res = None
            for _ in range(retries + 1):
                res = self._mymemory_one(t)
                if res or self._my_dead:
                    break
                time.sleep(1.0)
            out.append(res)
            if self._my_dead:
                # 额度已耗尽，后面全部交给 NLLB
                break
        return out

    # ------------------------------------------------------------ NLLB

    def _load_nllb(self):
        if TransHybrid._nllb_model is not None:
            return TransHybrid._nllb_model, TransHybrid._nllb_tok
        with TransHybrid._lock:
            if TransHybrid._nllb_model is not None:
                return TransHybrid._nllb_model, TransHybrid._nllb_tok
            if not os.path.isdir(NLLB_MODEL_DIR):
                raise RuntimeError('缺少离线模型目录：%s' % NLLB_MODEL_DIR)
            from transformers import AutoModelForSeq2SeqLM, AutoTokenizer

            self.logger.info('加载本地 NLLB-200-distilled-600M ...')
            tok = AutoTokenizer.from_pretrained(NLLB_MODEL_DIR, src_lang='eng_Latn')
            model = AutoModelForSeq2SeqLM.from_pretrained(NLLB_MODEL_DIR)
            device = 'cuda' if torch.cuda.is_available() else 'cpu'
            try:
                fp16 = bool(self.get_param_value('use_fp16'))
            except Exception:
                fp16 = True
            if device == 'cuda' and fp16:
                model = model.half()
            model.to(device)
            model.eval()
            TransHybrid._nllb_tok = tok
            TransHybrid._nllb_model = model
            self.logger.info('NLLB 已加载（%s）' % device)
            return model, tok

    def _nllb_batch(self, texts):
        model, tok = self._load_nllb()
        try:
            batch_size = int(self.get_param_value('batch_size'))
        except Exception:
            batch_size = 8
        try:
            num_beams = int(self.get_param_value('num_beams'))
        except Exception:
            num_beams = 4

        target_id = tok.convert_tokens_to_ids(self.api_lang_target)
        device = next(model.parameters()).device
        out = []
        for i in range(0, len(texts), max(1, batch_size)):
            chunk = texts[i:i + batch_size]
            if all(not c for c in chunk):
                out.extend([''] * len(chunk))
                continue
            try:
                enc = tok(chunk, return_tensors='pt', padding=True, truncation=True,
                          max_length=512).to(device)
                with torch.no_grad():
                    gen = model.generate(**enc, forced_bos_token_id=target_id,
                                         max_length=512, num_beams=num_beams,
                                         early_stopping=True)
                res = tok.batch_decode(gen, skip_special_tokens=True)
            except Exception as exc:  # noqa: BLE001
                self.logger.error('NLLB 翻译失败: %r' % exc)
                res = ['' for _ in chunk]
            out.extend([(r or '').strip() for r in res])
        return out

    # ------------------------------------------------------------ 入口

    def _translate(self, src_list):
        raw = [(t or '').strip() for t in (src_list or [])]
        if not raw:
            return []

        try:
            do_norm = bool(self.get_param_value('normalize'))
        except Exception:
            do_norm = True
        texts = [_normalize(t) if do_norm else t for t in raw]

        results = [None] * len(texts)
        skipped = set()

        # 网址/社群号、纯符号行：不给译文（BT 会抹掉原字且不写字），画面上不留英文/符号
        for i, t in enumerate(raw):
            if _is_watermark(t) or not any(c.isalnum() for c in t):
                results[i] = ''
                skipped.add(i)

        if not self._my_dead:
            my = self._mymemory_batch(texts)
            for i, r in enumerate(my):
                if i in skipped:
                    continue
                results[i] = r
            if self._my_dead:
                self.logger.warning('MyMemory 不可用，本次转用本地 NLLB：%s' % self._my_dead_reason)

        missing = [i for i, r in enumerate(results) if not r and i not in skipped]
        if missing:
            nllb_in = [texts[i] for i in missing]
            nllb_out = self._nllb_batch(nllb_in)
            for idx, txt in zip(missing, nllb_out):
                results[idx] = txt

        return [_postprocess(raw[i], r or '') for i, r in enumerate(results)]

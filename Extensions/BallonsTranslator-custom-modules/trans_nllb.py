"""离线英中翻译器 NLLB（BallonsTranslator 自定义模块）

为什么用它：
    MyMemory 按 IP 限当日额度（429 后 BT 会把英文原文画回图上），
    Opus-MT 对漫画口语质量太差（orphanage 译成「电视剧」）。
    NLLB-200-distilled-600M 多语模型英中质量明显更好，且完全离线、无额度限制。

放置位置：<BallonsTranslator>/custom_modules/trans_nllb.py（文件名必须以 trans_ 开头）

依赖：
    ballontrans_pylibs_win\\python.exe -m pip install transformers==4.46.3 sentencepiece
模型：
    data\\models\\nllb-200-distilled-600M（约 2.4GB）
    sentencepiece 在含中文的路径下会报 Illegal byte sequence，因此再建一个 ASCII 联接到：
    C:\\bt_models\\nllb-200-distilled-600M
"""

import os
import threading
from copy import deepcopy

import torch

from ballontranslator.modules.translators.base import (
    BaseTranslator,
    register_translator,
)

PROGRAM_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

MODEL_DIR_CANDIDATES = [
    os.environ.get('NLLB_MODEL_DIR', ''),
    r'C:\bt_models\nllb-200-distilled-600M',
    os.path.join(PROGRAM_DIR, 'data', 'models', 'nllb-200-distilled-600M'),
]


def _resolve_model_dir():
    for d in MODEL_DIR_CANDIDATES:
        if d and os.path.isdir(d):
            return d
    return MODEL_DIR_CANDIDATES[-1]


MODEL_DIR = _resolve_model_dir()

# NLLB 的 Flores-200 语言码
NLLB_LANG = {
    'en': 'eng_Latn',
    'zh': 'zho_Hans',
    'zh-tw': 'zho_Hant',
    'ja': 'jpn_Jpan',
    'ko': 'kor_Hang',
}

NLLB_PARAMS = {
    'description': '离线 NLLB-200 英中翻译（本地模型，无额度限制，质量优于 Opus-MT）',
    'batch_size': {
        'type': 'spinbox',
        'options': [1, 32],
        'value': 8,
        'display_name': '批大小',
    },
    'num_beams': {
        'type': 'spinbox',
        'options': [1, 8],
        'value': 4,
        'display_name': 'beam 数',
    },
    'use_fp16': {
        'type': 'checkbox',
        'value': True,
        'display_name': '半精度(省显存)',
    },
}


@register_translator('NLLB')
class TransNLLB(BaseTranslator):

    params = deepcopy(NLLB_PARAMS)

    # 逐块翻译，避免拼接后行数错位
    concate_text = False

    _lock = threading.Lock()
    _model = None
    _tokenizer = None

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

    def _load(self):
        if TransNLLB._model is not None:
            return TransNLLB._model, TransNLLB._tokenizer

        with TransNLLB._lock:
            if TransNLLB._model is not None:
                return TransNLLB._model, TransNLLB._tokenizer

            if not os.path.isdir(MODEL_DIR):
                raise RuntimeError('缺少离线模型目录：%s' % MODEL_DIR)

            from transformers import AutoModelForSeq2SeqLM, AutoTokenizer

            self.logger.info('加载离线翻译模型 NLLB-200-distilled-600M ...')
            tok = AutoTokenizer.from_pretrained(MODEL_DIR, src_lang='eng_Latn')
            model = AutoModelForSeq2SeqLM.from_pretrained(MODEL_DIR)

            device = 'cuda' if torch.cuda.is_available() else 'cpu'
            try:
                fp16 = bool(self.get_param_value('use_fp16'))
            except Exception:
                fp16 = True
            if device == 'cuda' and fp16:
                model = model.half()
            model.to(device)
            model.eval()
            TransNLLB._tokenizer = tok
            TransNLLB._model = model
            self.logger.info('NLLB 已加载（%s%s）' % (device, ' fp16' if device == 'cuda' and fp16 else ''))
            return model, tok

    def _translate(self, src_list):
        texts = [(t or '').strip() for t in (src_list or [])]
        if not texts:
            return []

        model, tok = self._load()
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
            # 空串直接给空，不进模型
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

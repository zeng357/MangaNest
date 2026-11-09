"""离线英中翻译器 OpusMT（BallonsTranslator 自定义模块）

为什么需要它：
    在线免费通道（MyMemory）按 IP 限当日额度，一超额就返回 429，
    BallonsTranslator 拿不到译文时会把原文（英文）画回图上，成品里就还是英文。
    这里改用本地 Helsinki-NLP/opus-mt-en-zh 模型（约 300MB，装在
    data/models/opus-mt-en-zh），完全不依赖网络，速度也更快，译文比免费机翻通顺。

放置位置：<BallonsTranslator>/custom_modules/trans_opus.py
    文件名必须以 trans_ 开头，否则 lazy registry 扫不到。

依赖：
    ballontrans_pylibs_win\\python.exe -m pip install transformers sentencepiece sacremoses
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

# 注意：sentencepiece 在 Windows 上加载含中文的路径会报 Illegal byte sequence，
# 因此模型走一个纯 ASCII 路径（项目里建一个目录联接指向实际目录）：
#   mklink /J C:\bt_models\opus-mt-en-zh "<项目>\tools\Ballonstranslator_win_minium\data\models\opus-mt-en-zh"
# 也支持用环境变量 OPUS_MODEL_DIR 指定别的 ASCII 路径。
MODEL_DIR_CANDIDATES = [
    os.environ.get('OPUS_MODEL_DIR', ''),
    r'C:\bt_models\opus-mt-en-zh',
    os.path.join(PROGRAM_DIR, 'data', 'models', 'opus-mt-en-zh'),
]


def _resolve_model_dir():
    for d in MODEL_DIR_CANDIDATES:
        if d and os.path.isdir(d):
            return d
    return MODEL_DIR_CANDIDATES[-1]


MODEL_DIR = _resolve_model_dir()

OPUS_PARAMS = {
    'description': '离线 Opus-MT 英中翻译（本地模型，无网络额度限制）',
    'batch_size': {
        'type': 'spinbox',
        'options': [1, 32],
        'value': 8,
        'display_name': '批大小',
    },
    'max_length': {
        'type': 'spinbox',
        'options': [64, 1024],
        'value': 512,
        'display_name': '最长输出 token',
    },
}


@register_translator('OpusMT')
class TransOpus(BaseTranslator):

    params = deepcopy(OPUS_PARAMS)

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
            '繁體中文': 'zh',
        }
        self.api_lang_source = self.lang_map.get(self.lang_source, 'en')
        self.api_lang_target = self.lang_map.get(self.lang_target, 'zh')

    def set_source(self, lang: str):
        super().set_source(lang)
        self.api_lang_source = self.lang_map.get(self.lang_source, 'en')

    def set_target(self, lang: str):
        super().set_target(lang)
        self.api_lang_target = self.lang_map.get(self.lang_target, 'zh')

    def _get_device(self):
        try:
            return 'cuda' if torch.cuda.is_available() else 'cpu'
        except Exception:
            return 'cpu'

    def _load(self):
        if TransOpus._model is not None:
            return TransOpus._model, TransOpus._tokenizer

        with TransOpus._lock:
            if TransOpus._model is not None:
                return TransOpus._model, TransOpus._tokenizer

            if not os.path.isdir(MODEL_DIR):
                raise RuntimeError(
                    '缺少离线模型目录：%s\n请先下载 Helsinki-NLP/opus-mt-en-zh 到该目录' % MODEL_DIR)

            from transformers import MarianMTModel, MarianTokenizer

            self.logger.info('加载离线翻译模型 opus-mt-en-zh ...')
            tok = MarianTokenizer.from_pretrained(MODEL_DIR)
            model = MarianMTModel.from_pretrained(MODEL_DIR)
            device = self._get_device()
            model.to(device)
            model.eval()
            TransOpus._tokenizer = tok
            TransOpus._model = model
            self.logger.info('离线翻译模型已加载（%s）' % device)
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
            max_length = int(self.get_param_value('max_length'))
        except Exception:
            max_length = 512

        device = next(model.parameters()).device
        out = []
        for i in range(0, len(texts), max(1, batch_size)):
            chunk = texts[i:i + batch_size]
            try:
                enc = tok(chunk, return_tensors='pt', padding=True, truncation=True, max_length=512).to(device)
                with torch.no_grad():
                    gen = model.generate(**enc, max_length=max_length, num_beams=4, early_stopping=True)
                res = tok.batch_decode(gen, skip_special_tokens=True)
            except Exception as exc:  # noqa: BLE001
                self.logger.error('OpusMT 翻译失败: %r' % exc)
                res = ['' for _ in chunk]
            # 模型对空串会输出空或异常，兜底成空字符串（BT 会跳过绘制，不再把英文画回去）
            out.extend([(r or '').strip() for r in res])
        return out

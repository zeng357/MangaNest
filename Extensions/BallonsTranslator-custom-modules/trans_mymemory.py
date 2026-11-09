"""MyMemory 翻译器（BallonsTranslator 自定义模块）

用途：在国内网络环境下 Google/DeepL 均不可用时，提供一个免密钥、可直连的
英译中机器翻译通道。API 地址：
    https://api.mymemory.translated.net/get?q=...&langpair=en|zh-CN

放置位置：<BallonsTranslator>/custom_modules/mymemory.py
在设置面板的「翻译器」下拉框中会多出一个 MyMemory 选项。
"""

from copy import deepcopy

import requests

from ballontranslator.modules.translators.base import (
    BaseTranslator,
    register_translator,
)

MYMEMORY_URL = 'https://api.mymemory.translated.net/get'

MYMEMORY_PARAMS = {
    'description': 'MyMemory 免密钥翻译（推荐 English -> 简体中文）',
    'timeout': {
        'type': 'spinbox',
        'options': [5, 60],
        'value': 20,
        'display_name': '请求超时(秒)',
    },
    'retries': {
        'type': 'spinbox',
        'options': [0, 5],
        'value': 2,
        'display_name': '失败重试次数',
    },
}


@register_translator('MyMemory')
class TransMyMemory(BaseTranslator):

    params = deepcopy(MYMEMORY_PARAMS)

    # 每个文本框单独请求，避免拼接后被接口截断造成行数错位
    concate_text = False

    def _setup_translator(self):
        # MyMemory 的语言代码
        self.lang_map = {
            'Auto': 'en',
            '简体中文': 'zh-CN',
            '繁體中文': 'zh-TW',
            '日本語': 'ja',
            'English': 'en',
            '한국어': 'ko',
            'Français': 'fr',
            'Deutsch': 'de',
            'Español': 'es',
            'русский язык': 'ru',
            'Italiano': 'it',
            'Português': 'pt',
        }
        self.api_lang_source = self.lang_map[self.lang_source]
        self.api_lang_target = self.lang_map[self.lang_target]

    def set_source(self, lang: str):
        super().set_source(lang)
        self.api_lang_source = self.lang_map[self.lang_source]

    def set_target(self, lang: str):
        super().set_target(lang)
        self.api_lang_target = self.lang_map[self.lang_target]

    def _translate(self, src_list):
        return [self._translate_one(text) for text in src_list]

    def _translate_one(self, text: str) -> str:
        text = (text or '').strip()
        if not text:
            return ''

        timeout = 20
        retries = 2
        try:
            timeout = int(self.get_param_value('timeout'))
        except Exception:
            pass
        try:
            retries = int(self.get_param_value('retries'))
        except Exception:
            pass

        langpair = f'{self.api_lang_source}|{self.api_lang_target}'

        for attempt in range(retries + 1):
            try:
                resp = requests.get(
                    MYMEMORY_URL,
                    params={'q': text, 'langpair': langpair},
                    timeout=timeout,
                )
                resp.raise_for_status()
                data = resp.json()
                status = data.get('responseStatus')
                if status != 200:
                    raise RuntimeError(str(data.get('responseDetails', status)))
                if data.get('quotaFinished'):
                    raise RuntimeError('MyMemory 当日免费额度已用完')

                result = (data.get('responseData') or {}).get('translatedText', '')
                result = (result or '').strip()
                if result:
                    return result
                return text
            except Exception as exc:  # noqa: BLE001
                if attempt == retries:
                    self.logger.error(f'MyMemory 翻译失败: {text!r} -> {exc}')
                    return text
        return text

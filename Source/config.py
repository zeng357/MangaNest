# 通用配置文件 - 只包含全局配置，不包含网站特定信息

EXPORT_FORMATS = {
    'original': '原始图片格式',
    'pdf': 'pdf格式'
}

PDF_MODES = {
    'per_chapter': '每个章节一个PDF',
    'single': '每张图片单独转PDF'
}

DEFAULT_EXPORT_FORMAT = 'original'
DEFAULT_PDF_MODE = 'per_chapter'

# 图片文件名补零位数：0=不补零(1,2,3...)，3=三位补零(001,002,003...)
DEFAULT_IMAGE_NAME_PADDING = 3

# 章节文件夹命名模式：'number'=保持数字命名(1,2,3...)；'title'=使用章节名命名(1 第1话 xxx)
DEFAULT_CHAPTER_FOLDER_NAMING = 'number'

BROWSER_PATHS = {
    'edge': r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
    'chrome': r"C:\Program Files\Google\Chrome\Application\chrome.exe"
}

DEFAULT_SITE = '拷贝漫画'

DEFAULT_COOKIES_DIR = 'cookies'

CONFIG_FILE = 'config.json'

# ---------- 代理（默认关闭，配置后所有请求走代理出口，不暴露本机 IP） ----------
PROXY_ENABLED = False          # 总开关（GUI『设置-代理设置』可改，存 config.json）
PROXY_SOURCE = 'file'          # file=本地列表 / url=订阅链接 / api=按次取动态IP的接口
PROXY_FILE = 'proxies.txt'     # 代理列表文件（一行一个）
PROXY_URL = ''                 # 订阅链接（source=url 时使用）
PROXY_API = ''                 # 动态代理API（source=api 时使用）
PROXY_BROWSER = ''             # 浏览器固定出口，如 http://127.0.0.1:7890（留空取池里第一个）
PROXY_ALLOW_DIRECT = True      # 代理全部不可用时是否回退本机直连
PROXY_MAX_TRIES = 3            # 单张图片最多尝试几个不同代理出口

# ---------- 下载逻辑（GUI『设置-下载设置』可切换，存 config.json） ----------
# 'new'    = v1.4.0 及以后的下载器（aiohttp 优先、失败多轮重下、人工节奏、整批暂停门）
# 'legacy' = v1.4.0 之前那套原始下载逻辑（downloader_legacy.py，原样保留）
DOWNLOAD_LOGIC = 'new'

# ---------- 模拟人工（全站默认生效：随机间隔、限并发、随机 UA，降低触发站点风控/封禁） ----------
HUMAN_MODE = True              # 总开关；关闭后按各站点 CONFIG 的固定参数下载
HUMAN_DELAY_MIN = 0.3          # 每张图片额外随机等待下限（秒）
HUMAN_DELAY_MAX = 0.9          # 每张图片额外随机等待上限（秒）
HUMAN_CHAPTER_PAUSE_MIN = 1.5  # 章节之间随机停顿下限（秒）
HUMAN_CHAPTER_PAUSE_MAX = 4.0  # 章节之间随机停顿上限（秒）
HUMAN_MAX_CONCURRENCY = 3      # 单章图片并发上限（再高容易被判定为机器人）
HUMAN_MAX_THREADS = 2          # 下载线程上限
HUMAN_BROWSER_PAUSE_MIN = 0.6  # 浏览器每次翻页/刷新后的随机停顿下限（秒）
HUMAN_BROWSER_PAUSE_MAX = 1.6  # 浏览器每次翻页/刷新后的随机停顿上限（秒）
HUMAN_UA_POOL = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/124.0.0.0 Safari/537.36 Edg/124.0.2478.67",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/125.0.0.0 Safari/537.36 Edg/125.0.2535.51",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/126.0.0.0 Safari/537.36 Edg/126.0.2592.56",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/127.0.0.0 Safari/537.36 Edg/127.0.2651.74",
]

# ---------- 多线程高速下载（仅指定网站；GUI『设置-下载设置』可改，存 config.json） ----------
# FAST_THREAD 由 GUI 按「开关 + 当前站点是否在名单内」折算后写入；True 时该站点绕过模拟人工的
# 线程/并发上限与停顿，直接按下面两个固定值下载（其它站点不受影响）
FAST_THREAD = False
FAST_THREAD_SITES = []          # 启用高速下载的站点名单（空=不启用）
FAST_THREAD_CONCURRENCY = 8      # 高速模式：单章图片并发
FAST_THREAD_WORKERS = 8          # 高速模式：下载线程数

# ---------- 采集模式（设置页『采集模式』控制） ----------
# 'batch'（默认）= 先采集全部章节图片链接，再统一下载
# 'stream'      = 边采集边下载：采集完一章立即下载这一章，然后继续下一章
COLLECT_MODE = 'batch'

# ---------- 漫画客(yumanhua) 节奏（设置页『漫画客节奏』控制） ----------
# False（默认）= 跟随全局设置，与其他站点一致（模拟人工 / 高速下载开关说了算）
# True = 批次限速：每 10 张整批停 2.5~5 分钟、章间 5~10 分钟、单章 2 线程/2 并发
YUMANHUA_BATCH_LIMIT = False


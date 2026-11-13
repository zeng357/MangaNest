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


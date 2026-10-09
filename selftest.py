# -*- coding: utf-8 -*-
"""程序自检模块：启动时检查运行环境，结果自动写入日志文件。

检查项：
  1. Python 版本（建议 3.8+）
  2. 必需第三方模块（aiohttp / execjs / PIL / requests / DrissionPage）
  3. 系统网络层 socket（可创建 socket、可解析 DNS）
  4. 核心程序文件完整性（downloader / download_flow / converter / gui / 配置）
  5. 配置目录可写性

任何一项有问题都会写入 startup_diag.log（UTF-8 中文可读），
同时把未捕获异常自动写入 crash.log。
"""
import os
import sys
import time
import traceback

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DIAG_LOG = os.path.join(BASE_DIR, 'startup_diag.log')
CRASH_LOG = os.path.join(BASE_DIR, 'crash.log')

# 必需模块（缺失会导致程序无法运行）
REQUIRED_MODULES = ['aiohttp', 'execjs', 'PIL', 'requests', 'tkinter']
# 可选模块（缺失给出警告，不阻断启动）
OPTIONAL_MODULES = ['DrissionPage', 'lxml', 'bs4', 'img2pdf']

CORE_FILES = ['downloader.py', 'download_flow.py', 'converter.py', 'gui.py',
              'start.bat', 'start.vbs']


def _check_modules(mods, required=True):
    """逐个尝试导入模块，返回 (项名, 状态, 说明) 列表"""
    results = []
    for m in mods:
        try:
            __import__(m)
            results.append((m, 'OK', '已安装'))
        except Exception as e:
            tag = '错误' if required else '警告'
            results.append((m, tag, f'缺失：{e.__class__.__name__} {e}'))
    return results


def _check_socket():
    """检查系统网络层：创建 socket + DNS 解析（修复 WinError 10038 场景）"""
    lines = []
    # 1) 创建 socket
    try:
        import socket
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.close()
        lines.append(('网络层', 'OK', 'socket 创建正常'))
    except Exception as e:
        err = getattr(e, 'winerror', None) or e.__class__.__name__
        lines.append(('网络层', '错误',
                      f'socket 创建失败（WinError {err}）：多为系统网络栈故障，'
                      f'建议①重启电脑 ②管理员运行 netsh winsock reset 后重启 '
                      f'③退出加速器/VPN/代理软件'))
        return lines
    # 2) DNS 解析
    try:
        import socket
        socket.getaddrinfo('pypi.org', 443)
        lines.append(('DNS 解析', 'OK', '域名解析正常'))
    except Exception as e:
        err = getattr(e, 'winerror', None) or e.__class__.__name__
        lines.append(('DNS 解析', '错误',
                      f'域名解析失败（WinError {err}）：网络不通或被拦截，'
                      f'检查网络连接/代理设置'))
    return lines


def _check_files():
    lines = []
    for f in CORE_FILES:
        p = os.path.join(BASE_DIR, f)
        if os.path.isfile(p):
            lines.append((f, 'OK', f'{os.path.getsize(p)} 字节'))
        else:
            lines.append((f, '警告', '缺失（可能影响对应功能）'))
    return lines


def _check_writable():
    try:
        test = os.path.join(BASE_DIR, '.diag_tmp')
        with open(test, 'w', encoding='utf-8') as fh:
            fh.write('ok')
        os.remove(test)
        return [('配置目录', 'OK', '可写')]
    except Exception as e:
        return [('配置目录', '错误', f'不可写：{e}（程序无法保存配置）')]


def run_selftest(log_path=None):
    """执行全部自检，结果写入日志文件，返回 (是否全通过, 日志行列表)"""
    log_path = log_path or DIAG_LOG
    lines = []
    now = time.strftime('%Y-%m-%d %H:%M:%S')
    py_ver = sys.version.split()[0]
    _ok_ver = (sys.version_info.major, sys.version_info.minor) >= (3, 8)
    lines.append((f'Python {py_ver}', 'OK' if _ok_ver else '警告',
                  sys.version.replace('\n', ' ')))
    lines += _check_modules(REQUIRED_MODULES, required=True)
    lines += _check_modules(OPTIONAL_MODULES, required=False)
    lines += _check_socket()
    lines += _check_files()
    lines += _check_writable()

    ok = all(tag == 'OK' for _n, tag, _d in lines)
    # 仅存在「错误」级问题才算影响使用的严重异常（可选模块缺失等「警告」不阻断）
    has_error = any(tag == '错误' for _n, tag, _d in lines)
    # 写日志
    try:
        with open(log_path, 'w', encoding='utf-8') as fh:
            fh.write('=' * 60 + '\n')
            fh.write(f'漫画下载器 自检报告\n生成时间：{now}\n')
            fh.write(f'运行目录：{BASE_DIR}\n')
            fh.write('=' * 60 + '\n')
            for name, tag, desc in lines:
                fh.write(f'[{tag}] {name}：{desc}\n')
            fh.write('-' * 60 + '\n')
            fh.write(f'总结：{"全部正常" if ok else "存在异常（见上方 [错误]/[警告] 项）"}\n')
    except Exception as e:
        lines.append(('日志写入', '错误', f'无法写入 {log_path}：{e}'))

    return ok, has_error, lines, log_path


def install_crash_hook():
    """把未捕获异常自动写入 crash.log（防止黑窗一闪而过看不到错误）"""
    def _hook(exc_type, exc_value, exc_tb):
        msg = ''.join(traceback.format_exception(exc_type, exc_value, exc_tb))
        try:
            with open(CRASH_LOG, 'a', encoding='utf-8') as fh:
                fh.write('\n' + '=' * 60 + '\n')
                fh.write(f'崩溃时间：{time.strftime("%Y-%m-%d %H:%M:%S")}\n')
                fh.write(msg)
                fh.write('=' * 60 + '\n')
        except Exception:
            pass
        # 仍打印到控制台（start.bat 会捕获显示）
        print(msg, file=sys.stderr)
        sys.__excepthook__(exc_type, exc_value, exc_tb)

    sys.excepthook = _hook


if __name__ == '__main__':
    ok, has_error, lines, path = run_selftest()
    for name, tag, desc in lines:
        print(f'[{tag}] {name}：{desc}')
    print(f'\n日志已写入：{path}\n结果：{"全部正常" if ok else "存在异常"}')

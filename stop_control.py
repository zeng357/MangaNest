# 下载停止控制：GUI 点“停止下载”后，下载流程和各下载器通过这里统一判断
import threading


class DownloadCancelled(Exception):
    """用户主动停止下载"""
    pass


_event = threading.Event()


def clear():
    _event.clear()


def request_stop():
    _event.set()


def is_stopped():
    return _event.is_set()


def check():
    """已请求停止时抛出 DownloadCancelled，供各下载循环提前退出"""
    if _event.is_set():
        raise DownloadCancelled('用户已停止下载')

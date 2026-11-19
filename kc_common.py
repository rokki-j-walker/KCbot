# -*- coding: utf-8 -*-
"""Общие утилиты KCbot.

Сюда выносится код, который раньше был продублирован в 2-4 файлах
(см. план, Фаза 1: find_window x4, путь сейва x3, load_bounties x2,
запись JSON x3). Модуль должен оставаться лёгким: только stdlib, никаких
тяжёлых импортов (bot3/evo), чтобы его можно было безопасно импортировать
из любого скрипта и из frozen-сборки.
"""
import json
import os
import tempfile


def atomic_json_dump(path, obj, indent=2):
    """Записать JSON атомарно: временный файл + os.replace.

    Запись напрямую в целевой файл опасна: обрыв/ошибка в момент dump
    оставляет битый JSON, который потом читатели молча отбрасывают
    (best_genome -> дефолты, история эволюции -> пустой список).
    """
    directory = os.path.dirname(path)
    if directory:
        os.makedirs(directory, exist_ok=True)
    # тот же каталог, чтобы os.replace оставался на одной файловой системе
    fd, tmp = tempfile.mkstemp(prefix='.tmp_', suffix='.json', dir=directory or None)
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as f:
            json.dump(obj, f, ensure_ascii=False, indent=indent)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def read_json(path, default=None):
    """Прочитать JSON, при любой ошибке вернуть default (не падать)."""
    try:
        with open(path, 'r', encoding='utf-8') as f:
            return json.load(f)
    except (OSError, ValueError):
        return default


def _process_is(pid, name):
    """Принадлежит ли процесс pid образу с именем name (подстрока)."""
    import ctypes
    try:
        h = ctypes.windll.kernel32.OpenProcess(0x0410, False, pid)  # QUERY|VM_READ
        if not h:
            return False
        buf = ctypes.create_unicode_buffer(260)
        ctypes.windll.psapi.GetModuleBaseNameW(h, None, buf, 260)
        ctypes.windll.kernel32.CloseHandle(h)
        return name.lower() in buf.value.lower()
    except Exception:
        return False


def find_window(title='Kingdom', require_process=True):
    """Найти окно игры -> hwnd или None.

    Единая реализация: раньше она была продублирована в шести файлах
    (bot3, evo, restart, human_trainer, shot, baseline) и разошлась —
    shot.py вообще не проверял процесс и мог взять чужое окно с похожим
    заголовком.

    Здесь: заголовок + (по умолчанию) имя процесса, из кандидатов берётся
    самое крупное видимое окно, а не первое в z-порядке.
    """
    import ctypes
    import ctypes.wintypes as wt

    user32 = ctypes.windll.user32
    res = []
    EnumProc = ctypes.WINFUNCTYPE(ctypes.c_bool, wt.HWND, wt.LPARAM)

    def cb(hwnd, lparam):
        if not user32.IsWindowVisible(hwnd):
            return True
        n = user32.GetWindowTextLengthW(hwnd)
        if n == 0:
            return True
        buf = ctypes.create_unicode_buffer(n + 1)
        user32.GetWindowTextW(hwnd, buf, n + 1)
        if title.lower() not in buf.value.lower():
            return True
        if require_process:
            pid = wt.DWORD()
            user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
            if not _process_is(pid.value, 'kingdom'):
                return True
        area = 0
        r = wt.RECT()
        if user32.GetWindowRect(hwnd, ctypes.byref(r)):
            area = max(0, r.right - r.left) * max(0, r.bottom - r.top)
        res.append((hwnd, area))
        return True

    user32.EnumWindows(EnumProc(cb), 0)
    if not res:
        return None
    res.sort(key=lambda x: x[1], reverse=True)
    return res[0][0]

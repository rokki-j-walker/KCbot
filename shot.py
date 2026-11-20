# -*- coding: utf-8 -*-
"""Скриншот окна игры (PrintWindow, фолбэк - ImageGrab)."""
import ctypes
import ctypes.wintypes as wt
import sys
import time

import kc_common
from PIL import Image, ImageGrab

user32 = ctypes.windll.user32
gdi32 = ctypes.windll.gdi32

PW_RENDERFULLCONTENT = 2


def find_window(title='Kingdom'):
    """Единая реализация - kc_common.find_window.

    Раньше здесь НЕ проверялся процесс: скриншот мог снять чужое окно
    с похожим заголовком («Kingdom» в титуле браузера и т.п.).
    """
    return kc_common.find_window(title)


def focus_window(hwnd):
    """Поднять окно на передний план (для фолбэк-снимка)."""
    user32.keybd_event(0x12, 0, 0, 0)               # Alt down - обход
    user32.SwitchToThisWindow(hwnd, True)           # ограничений foreground
    user32.keybd_event(0x12, 0, 2, 0)               # Alt up
    for _ in range(20):
        if user32.GetForegroundWindow() == hwnd:
            return True
        time.sleep(0.05)
    return False


class _BIH(ctypes.Structure):
    _fields_ = [('biSize', wt.DWORD), ('biWidth', wt.LONG),
                ('biHeight', wt.LONG), ('biPlanes', wt.WORD),
                ('biBitCount', wt.WORD), ('biCompression', wt.DWORD),
                ('biSizeImage', wt.DWORD), ('biXPelsPerMeter', wt.LONG),
                ('biYPelsPerMeter', wt.LONG), ('biClrUsed', wt.DWORD),
                ('biClrImportant', wt.DWORD)]


def _printwindow(hwnd):
    """Снимок содержимого окна (не зависит от Z-порядка и перекрытий).

    Возвращает Image или None. Для Unity-окон старых версий работает;
    если кадр оказался полностью чёрным - отказываемся (фолбэк ниже).
    """
    r = wt.RECT()
    user32.GetWindowRect(hwnd, ctypes.byref(r))
    w, h = r.right - r.left, r.bottom - r.top
    if w <= 0 or h <= 0:
        return None
    hdc = user32.GetWindowDC(hwnd)
    mem = gdi32.CreateCompatibleDC(hdc)
    bmp = gdi32.CreateCompatibleBitmap(hdc, w, h)
    gdi32.SelectObject(mem, bmp)
    try:
        if not user32.PrintWindow(hwnd, mem, PW_RENDERFULLCONTENT):
            return None
        bih = _BIH()
        bih.biSize = ctypes.sizeof(_BIH)
        bih.biWidth = w
        bih.biHeight = -h                 # top-down
        bih.biPlanes = 1
        bih.biBitCount = 32
        buf = (ctypes.c_ubyte * (w * h * 4))()
        if gdi32.GetDIBits(mem, bmp, 0, h, buf, ctypes.byref(bih), 0) == 0:
            return None
        img = Image.frombuffer('RGB', (w, h), bytes(buf), 'raw', 'BGRX', 0, 1)
        if img.convert('L').getextrema() == (0, 0):
            return None                   # полностью чёрный - не годится
        return img
    finally:
        gdi32.DeleteObject(bmp)
        gdi32.DeleteDC(mem)
        user32.ReleaseDC(hwnd, hdc)


def _grab_screen(hwnd):
    """Фолбэк: ImageGrab региона экрана. Окно поднимаем в TOPMOST - чат и
    прочие окна могут быть поверх всех, обычного фокуса мало."""
    r = wt.RECT()
    user32.GetWindowRect(hwnd, ctypes.byref(r))
    box = (r.left, r.top, r.right, r.bottom)
    focus_window(hwnd)
    user32.SetWindowPos(hwnd, -1, 0, 0, 0, 0, 0x0001 | 0x0002)   # TOPMOST
    try:
        time.sleep(0.15)
        return ImageGrab.grab(bbox=box, all_screens=True)
    finally:
        user32.SetWindowPos(hwnd, -2, 0, 0, 0, 0, 0x0001 | 0x0002)  # NOTOPMOST


def main():
    out = sys.argv[1] if len(sys.argv) > 1 else 'shot.png'
    hwnd = find_window()
    if not hwnd:
        print('окно не найдено')
        return 1
    img = _printwindow(hwnd)
    how = 'PrintWindow'
    if img is None:
        img = _grab_screen(hwnd)
        how = 'ImageGrab+TOPMOST'
    img.save(out)
    print('окно %s, %s -> %s %s' % (hwnd, how, out, img.size))
    return 0


if __name__ == '__main__':
    sys.exit(main())

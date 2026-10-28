# -*- coding: utf-8 -*-
"""Скриншот окна игры (PIL + Win32)."""
import ctypes
import ctypes.wintypes as wt
import sys

from PIL import ImageGrab

user32 = ctypes.windll.user32
gdi32 = ctypes.windll.gdi32


def find_window(title='Kingdom'):
    res = []
    EnumWindowsProc = ctypes.WINFUNCTYPE(ctypes.c_bool, wt.HWND, wt.LPARAM)

    def cb(hwnd, lparam):
        if not user32.IsWindowVisible(hwnd):
            return True
        n = user32.GetWindowTextLengthW(hwnd)
        if n == 0:
            return True
        buf = ctypes.create_unicode_buffer(n + 1)
        user32.GetWindowTextW(hwnd, buf, n + 1)
        if title.lower() in buf.value.lower():
            res.append((hwnd, buf.value))
        return True

    user32.EnumWindows(EnumWindowsProc(cb), 0)
    return res[0][0] if res else None


def main():
    out = sys.argv[1] if len(sys.argv) > 1 else 'shot.png'
    hwnd = find_window()
    if not hwnd:
        print('окно не найдено')
        return
    r = wt.RECT()
    user32.GetWindowRect(hwnd, ctypes.byref(r))
    box = (r.left, r.top, r.right, r.bottom)
    print('окно', hwnd, box)
    img = ImageGrab.grab(bbox=box, all_screens=True)
    img.save(out)
    print('сохранено', out, img.size)


if __name__ == '__main__':
    main()

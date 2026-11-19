# -*- coding: utf-8 -*-
"""Скриншот окна игры (PIL + Win32)."""
import ctypes
import ctypes.wintypes as wt
import sys

import kc_common
from PIL import ImageGrab

user32 = ctypes.windll.user32
gdi32 = ctypes.windll.gdi32


def find_window(title='Kingdom'):
    """Единая реализация - kc_common.find_window.

    Раньше здесь НЕ проверялся процесс: скриншот мог снять чужое окно
    с похожим заголовком («Kingdom» в титуле браузера и т.п.).
    """
    return kc_common.find_window(title)


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

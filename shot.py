# -*- coding: utf-8 -*-
"""Скриншот окна игры (PIL + Win32)."""
import ctypes
import ctypes.wintypes as wt
import sys
import time

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


def focus_window(hwnd):
    """Поднять окно на передний план (иначе ImageGrab снимает РЕГИОН
    экрана поверх окна - чужие окна попадают в кадр, аудит 2026-11-20)."""
    user32.keybd_event(0x12, 0, 0, 0)               # Alt down - обход
    user32.SwitchToThisWindow(hwnd, True)           # ограничений foreground
    user32.keybd_event(0x12, 0, 2, 0)               # Alt up
    for _ in range(20):
        if user32.GetForegroundWindow() == hwnd:
            return True
        time.sleep(0.05)
    return False


def main():
    out = sys.argv[1] if len(sys.argv) > 1 else 'shot.png'
    hwnd = find_window()
    if not hwnd:
        print('окно не найдено')
        return 1
    if not focus_window(hwnd):
        print('!! окно не удалось поднять - кадр может быть перекрыт')
    r = wt.RECT()
    user32.GetWindowRect(hwnd, ctypes.byref(r))
    box = (r.left, r.top, r.right, r.bottom)
    print('окно', hwnd, box)
    img = ImageGrab.grab(bbox=box, all_screens=True)
    img.save(out)
    print('сохранено', out, img.size)
    return 0


if __name__ == '__main__':
    sys.exit(main() or 0)

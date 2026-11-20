# -*- coding: utf-8 -*-
"""Зажать/отпустить клавиши (для обучения и меню)."""
import ctypes
import ctypes.wintypes as wt
import sys
import time

import kc_common

user32 = ctypes.windll.user32
INPUT_KEYBOARD = 1
KEYEVENTF_SCANCODE = 0x0008
KEYEVENTF_KEYUP = 0x0002

SC = {
    'a': 0x1E, 'd': 0x20, 's': 0x1F, 'w': 0x1B, 'shift': 0x2A,
    'left': 0x4B, 'right': 0x4D, 'up': 0x48, 'down': 0x50,
    'enter': 0x1C, 'esc': 0x01, 'space': 0x39, 'f8': 0x77, 'f9': 0x78,
}


def focus_game():
    """Перевести фокус на окно игры. False - окна нет или фокус не взят.

    Без этой проверки SendInput шлёт клавиши в ЛЮБОЕ активное окно
    (редактор, чат) - та же ловушка, что была у --dry (аудит B1).
    """
    hwnd = kc_common.find_window('Kingdom')
    if not hwnd:
        print('!! окно игры не найдено - нажатия никуда не отправляю')
        return False
    # трюк Alt: без него SwitchToThisWindow часто запрещён ограничениями
    # Windows на смену foreground-окна (тот же приём, что в bot3.force_focus)
    user32.keybd_event(0x12, 0, 0, 0)               # Alt down
    user32.SwitchToThisWindow(hwnd, True)
    user32.keybd_event(0x12, 0, 2, 0)               # Alt up
    for _ in range(20):
        if user32.GetForegroundWindow() == hwnd:
            return True
        time.sleep(0.05)
    print('!! не удалось перевести фокус на игру - отменяю нажатия')
    return False


class KEYBDINPUT(ctypes.Structure):
    _fields_ = [('wVk', wt.WORD), ('wScan', wt.WORD), ('dwFlags', wt.DWORD),
                ('time', wt.DWORD), ('dwExtraInfo', ctypes.POINTER(wt.ULONG))]


class _INPUTUNION(ctypes.Union):
    _fields_ = [('ki', KEYBDINPUT), ('pad', ctypes.c_byte * 32)]


class INPUT(ctypes.Structure):
    _anonymous_ = ('u',)
    _fields_ = [('type', wt.DWORD), ('u', _INPUTUNION)]


def send(sc, up=False):
    flags = KEYEVENTF_SCANCODE | (KEYEVENTF_KEYUP if up else 0)
    inp = INPUT(type=INPUT_KEYBOARD)
    inp.ki = KEYBDINPUT(0, sc, flags, 0, None)
    user32.SendInput(1, ctypes.byref(inp), ctypes.sizeof(INPUT))


def main():
    args = sys.argv[1:]
    if not args:
        print('usage: hold.py <key> [seconds]')
        return
    key = args[0].lower()
    sec = float(args[1]) if len(args) > 1 else 1.0
    sc = SC.get(key)
    if sc is None:
        print('неизвестная клавиша', key)
        return
    if not focus_game():
        return 1
    send(sc, False)
    print('держу %s %.1f с' % (key, sec))
    time.sleep(sec)
    send(sc, True)
    print('отпустил')
    return 0


if __name__ == '__main__':
    main()

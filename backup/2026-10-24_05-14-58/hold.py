# -*- coding: utf-8 -*-
"""Зажать/отпустить клавиши (для обучения и меню)."""
import ctypes
import ctypes.wintypes as wt
import sys
import time

user32 = ctypes.windll.user32
INPUT_KEYBOARD = 1
KEYEVENTF_SCANCODE = 0x0008
KEYEVENTF_KEYUP = 0x0002

SC = {
    'a': 0x1E, 'd': 0x20, 's': 0x1F, 'w': 0x1B, 'shift': 0x2A,
    'left': 0x4B, 'right': 0x4D, 'up': 0x48, 'down': 0x50,
    'enter': 0x1C, 'esc': 0x01, 'space': 0x39, 'f8': 0x77, 'f9': 0x78,
}


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
    send(sc, False)
    print('держу %s %.1f с' % (key, sec))
    time.sleep(sec)
    send(sc, True)
    print('отпустил')


if __name__ == '__main__':
    main()

# -*- coding: utf-8 -*-
"""
restart.py - мягко перезапустить игру Kingdom:
  1) закрыть окно (WM_CLOSE) -> игра успевает сохранить партию
  2) дождаться выхода процесса
  3) запустить Kingdom.exe заново
  4) поставить окно в 0,0 1280x720

py restart.py            # закрыть + запустить
py restart.py --close    # только закрыть
"""
import ctypes
import ctypes.wintypes as wt
import os
import subprocess
import sys
import time

import gamepath

GAME_DIR = gamepath.require_game_dir()
GAME_EXE = os.path.join(GAME_DIR, 'Kingdom.exe')
SAVE = os.path.join(os.environ['USERPROFILE'], r'AppData\LocalLow\noio\Kingdom\storage_v26_AUTO.dat')

user32 = ctypes.windll.user32
kernel32 = ctypes.windll.kernel32

WM_CLOSE = 0x0010
SW_RESTORE = 9
EnumWindowsProc = ctypes.WINFUNCTYPE(ctypes.c_bool, wt.HWND, wt.LPARAM)


def find_window(title='Kingdom'):
    res = []

    def cb(hwnd, lparam):
        if not user32.IsWindowVisible(hwnd):
            return True
        n = user32.GetWindowTextLengthW(hwnd)
        if n == 0:
            return True
        buf = ctypes.create_unicode_buffer(n + 1)
        user32.GetWindowTextW(hwnd, buf, n + 1)
        if title.lower() in buf.value.lower():
            res.append(hwnd)
        return True

    user32.EnumWindows(EnumWindowsProc(cb), 0)
    return res[0] if res else None


def pid_of(hwnd):
    p = wt.DWORD()
    user32.GetWindowThreadProcessId(hwnd, ctypes.byref(p))
    return p.value


def alive(pid):
    h = kernel32.OpenProcess(0x1000, False, pid)   # PROCESS_QUERY_LIMITED_INFORMATION
    if not h:
        return False
    code = wt.DWORD()
    kernel32.GetExitCodeProcess(h, ctypes.byref(code))
    kernel32.CloseHandle(h)
    return code.value == 259                        # STILL_ACTIVE


def save_info():
    try:
        st = os.stat(SAVE)
        return '%d bytes  %s' % (st.st_size, time.strftime('%H:%M:%S', time.localtime(st.st_mtime)))
    except OSError:
        return 'нет файла'


def main():
    only_close = '--close' in sys.argv
    print('сохранение до: %s' % save_info())
    hwnd = find_window()
    if hwnd:
        pid = pid_of(hwnd)
        print('окно hwnd=%s pid=%s -> WM_CLOSE' % (hwnd, pid))
        user32.PostMessageW(hwnd, WM_CLOSE, 0, 0)
        for i in range(60):
            time.sleep(0.5)
            if not alive(pid):
                print('процесс завершился за %.1f с' % (i * 0.5 + 0.5))
                break
        else:
            print('!! процесс не завершился за 30 с')
            if '--kill' in sys.argv:
                kernel32.TerminateProcess(kernel32.OpenProcess(1, False, pid), 1)
                print('принудительно убит')
    else:
        print('окно не найдено (игра не запущена?)')

    time.sleep(2)
    print('сохранение после выхода: %s' % save_info())
    if only_close:
        return

    print('запускаю %s' % GAME_EXE)
    subprocess.Popen([GAME_EXE], cwd=os.path.dirname(GAME_EXE))
    hwnd = None
    for i in range(120):
        time.sleep(1)
        hwnd = find_window()
        if hwnd:
            print('окно появилось через %d с: hwnd=%s' % (i + 1, hwnd))
            break
    if not hwnd:
        print('!! окно не появилось')
        return
    time.sleep(3)
    user32.ShowWindow(hwnd, SW_RESTORE)
    # размер и позицию окна не меняем - используем текущие
    r = wt.RECT()
    user32.GetWindowRect(hwnd, ctypes.byref(r))
    print('окно: %d,%d %dx%d' % (r.left, r.top, r.right - r.left, r.bottom - r.top))


if __name__ == '__main__':
    main()

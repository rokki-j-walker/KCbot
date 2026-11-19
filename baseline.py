# -*- coding: utf-8 -*-
"""baseline.py - создание эталонного сейва `saves/baseline.dat`.

Зачем: эволюция перед КАЖДЫМ эпизодом восстанавливает baseline в сейв игры,
чтобы все боты начинали с одинакового состояния (воспроизводимость).

Если baseline нет - раньше evo.py просто падал с ошибкой «запусти сначала
новую игру», и приходилось руками доходить до первой ночи. Теперь бот умеет
сделать это сам.

Логика переноса из C# BaselineBuilder.cs (удалён в Фазе 0, см. коммит
f04830f). Основана на декомпилированной Assembly-CSharp.dll:
  - PersistentManager.GetSavePath("AUTO") = persistentDataPath/storage_*_AUTO.dat
  - Menu.OnButtonNewGame -> подтверждение -> Game.Restart()
  - IntroSequence / Game.AbortIntroAndTutorial() - скип интро
  - Menu.OnButtonSaveAndQuit() -> Save("AUTO") + выход

Запуск отдельно:
    py baseline.py                 # обеспечить baseline
    py baseline.py --force         # пересоздать принудительно
"""
import argparse
import ctypes
import os
import shutil
import subprocess
import sys
import time

import gamepath
import kc_common

HERE = os.path.dirname(os.path.abspath(__file__))
SAVES_DIR = os.path.join(HERE, 'saves')
BASELINE = os.path.join(SAVES_DIR, 'baseline.dat')

SC_ENTER = 0x1C
SC_ESC = 0x01
SC_S = 0x1F

WM_CLOSE = 0x0010
MOUSEEVENTF_LEFTDOWN = 0x0002
MOUSEEVENTF_LEFTUP = 0x0004

user32 = ctypes.windll.user32
kernel32 = ctypes.windll.kernel32


class RECT(ctypes.Structure):
    _fields_ = [('left', ctypes.c_long), ('top', ctypes.c_long),
                ('right', ctypes.c_long), ('bottom', ctypes.c_long)]


def _default_log(msg):
    print(msg, flush=True)


def exists():
    return os.path.isfile(BASELINE)


def autosave_path():
    """Путь к сейву игры (единая точка правды - gamepath)."""
    return gamepath.save_file()


# ------------------------------------------------------------------- окно/ввод
def find_window(title='Kingdom'):
    """Окно игры. Единая реализация - kc_common.find_window."""
    return kc_common.find_window(title)


def pid_of(hwnd):
    import ctypes.wintypes as wt
    p = wt.DWORD()
    user32.GetWindowThreadProcessId(hwnd, ctypes.byref(p))
    return p.value


def alive(pid):
    h = kernel32.OpenProcess(0x1000, False, pid)
    if not h:
        return False
    code = ctypes.wintypes.DWORD()
    kernel32.GetExitCodeProcess(h, ctypes.byref(code))
    kernel32.CloseHandle(h)
    return code.value == 259  # STILL_ACTIVE


def force_focus(hwnd):
    """Забрать фокус: Alt снимает ограничение Windows на SetForegroundWindow."""
    user32.keybd_event(0x12, 0, 0, 0)
    user32.keybd_event(0x12, 0, 2, 0)
    user32.ShowWindow(hwnd, 9)                 # SW_RESTORE
    user32.BringWindowToTop(hwnd)
    user32.SetForegroundWindow(hwnd)
    user32.SetActiveWindow(hwnd)
    for _ in range(20):
        if user32.GetForegroundWindow() == hwnd:
            return True
        time.sleep(0.1)
    return user32.GetForegroundWindow() == hwnd


def tap(sc, hold=0.12):
    """Короткое нажатие клавиши."""
    _key(sc, False)
    time.sleep(hold)
    _key(sc, True)


def hold_key(sc, seconds):
    _key(sc, False)
    time.sleep(seconds)
    _key(sc, True)


def _key(sc, up):
    """Нажатие/отпускание через SendInput (scan-код)."""
    import bot3
    bot3._send(sc, up)


def window_rect(hwnd):
    r = RECT()
    if not user32.GetWindowRect(hwnd, ctypes.byref(r)):
        return None
    return r


def click(x, y):
    user32.SetCursorPos(int(x), int(y))
    time.sleep(0.2)
    user32.mouse_event(MOUSEEVENTF_LEFTDOWN, 0, 0, 0, 0)
    time.sleep(0.1)
    user32.mouse_event(MOUSEEVENTF_LEFTUP, 0, 0, 0, 0)
    time.sleep(0.4)


def read_state(game_dir):
    """Последняя строка KC| из kcdbg.txt (без проверки свежести: игра только
    что запустилась, логгер может ещё не писать)."""
    import bot3
    path = os.path.join(game_dir, 'Kingdom_Data', 'kcdbg.txt')
    try:
        size = os.path.getsize(path)
        if size == 0:
            return None
        with open(path, 'rb') as f:
            f.seek(max(0, size - 262144))
            data = f.read().decode('utf-8', 'replace')
    except OSError:
        return None
    last = None
    for line in data.splitlines():
        if line.startswith('KC|'):
            last = line
    try:
        return bot3.parse_line(last) if last else None
    except Exception:
        return None


def close_game(log=_default_log, timeout=30):
    """Мягкое закрытие: WM_CLOSE -> ожидание -> принудительно."""
    hwnd = find_window()
    if not hwnd:
        return True
    pid = pid_of(hwnd)
    user32.PostMessageW(hwnd, WM_CLOSE, 0, 0)
    for i in range(int(timeout * 2)):
        time.sleep(0.5)
        if not alive(pid):
            log('  игра закрыта за %.1f с' % ((i + 1) * 0.5))
            return True
    log('  !! игра не закрылась за %d с - убиваю' % timeout)
    try:
        subprocess.run(['taskkill', '/F', '/IM', 'Kingdom.exe'],
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except OSError:
        pass
    time.sleep(2)
    return find_window() is None


def wait_window(seconds, log=_default_log):
    t0 = time.time()
    while time.time() - t0 < seconds:
        hwnd = find_window()
        if hwnd:
            return hwnd
        time.sleep(0.5)
    return None


# ------------------------------------------------------------ основной сценарий
def has_autosave():
    p = autosave_path()
    try:
        return os.path.isfile(p) and os.path.getsize(p) > 0
    except OSError:
        return False


def ensure(game_dir=None, log=_default_log, force=False):
    """Гарантировать наличие baseline. True = baseline готов.

    1. Уже есть -> ок
    2. Есть сейв игры -> копируем его
    3. Нет ни того, ни другого -> сами проигрываем игру до автосейва
    """
    if exists() and not force:
        log('baseline уже существует: %s' % BASELINE)
        return True

    game_dir = game_dir or gamepath.require_game_dir()
    exe = os.path.join(game_dir, 'Kingdom.exe')
    if not os.path.isfile(exe):
        log('!! не найден файл игры: %s' % exe)
        return False

    os.makedirs(SAVES_DIR, exist_ok=True)
    src = autosave_path()

    if os.path.isfile(src) and not force:
        try:
            shutil.copyfile(src, BASELINE)
            log('baseline создан из существующего сейва: %s' % src)
            return True
        except OSError as e:
            log('!! не удалось скопировать сейв: %s' % e)

    log('сейв не найден - запускаю новую игру для создания сохранения')
    if not create_new_game_save(game_dir, log):
        return False

    if not has_autosave():
        log('!! сейв так и не появился: %s' % src)
        return False
    try:
        shutil.copyfile(src, BASELINE)
    except OSError as e:
        log('!! не удалось скопировать сейв: %s' % e)
        return False
    log('baseline создан: %s' % BASELINE)
    return True


def create_new_game_save(game_dir, log=_default_log):
    """Сыграть новую игру до автосейва (после первой ночи)."""
    speed_file = os.path.join(game_dir, 'Kingdom_Data', 'speed.txt')
    old_speed = None
    try:
        if find_window():
            log('закрываю висящую игру...')
            close_game(log)
            time.sleep(2)

        # ускоряем игру - быстрее дойти до автосейва
        try:
            if os.path.isfile(speed_file):
                with open(speed_file, 'r', encoding='utf-8', errors='replace') as f:
                    old_speed = f.read()
            with open(speed_file, 'w', encoding='utf-8') as f:
                f.write('8')
        except OSError as e:
            log('  !! не удалось записать скорость: %s' % e)

        exe = os.path.join(game_dir, 'Kingdom.exe')
        log('запускаю игру (новая игра начнётся автоматически)...')
        subprocess.Popen([exe], cwd=game_dir)

        hwnd = wait_window(90, log)
        if not hwnd:
            log('!! окно игры не появилось за 90 с')
            return False
        time.sleep(6)                       # сплэш + загрузка

        # несколько попыток: Enter (закрыть меню) + удержание S (скип обучения)
        for attempt in range(6):
            focused = force_focus(hwnd)
            log('  попытка %d: фокус=%s' % (attempt + 1, 'да' if focused else 'нет'))
            tap(SC_ENTER, 0.12)
            time.sleep(0.6)
            # Скип обучения: подсказка просит «нажать вниз» = клавиша S,
            # держать 2-3 секунды (проверено вживую; 5 с избыточно).
            hold_key(SC_S, 3.0)
            time.sleep(1.0)
            st = read_state(game_dir)
            if st is not None:
                log('    состояние: playing=%s day=%s purse=%s'
                    % (st.playing, st.day, st.purse))
            if has_autosave():
                log('  автосейв появился')
                break
            if st is not None and st.playing and st.day >= 1:
                log('  игра началась')

        # запасной путь: Esc -> меню -> SAVE + QUIT (кнопка внизу слева)
        if not has_autosave():
            log('автосейва нет - пробую SAVE + QUIT из меню')
            try:
                force_focus(hwnd)
                tap(SC_ESC, 0.12)
                time.sleep(1.2)
                h2 = find_window()
                r = window_rect(h2) if h2 else None
                if r:
                    w = r.right - r.left
                    h = r.bottom - r.top
                    x = r.left + int(w * 0.125)     # 12.5% ширины
                    y = r.top + int(h * 0.686)      # 68.6% высоты
                    log('  нажимаю SAVE + QUIT (%d,%d)' % (x, y))
                    click(x, y)
                    time.sleep(4.0)
            except Exception as e:
                log('  !! не удалось нажать SAVE + QUIT: %s' % e)

        # ждём автосейв (появляется после первой ночи)
        log('жду автосейв (после первой ночи, до 4 минут)...')
        for i in range(240):
            if has_autosave():
                break
            if i % 30 == 0:
                log('  ожидание... %d с' % i)
            time.sleep(1.0)

        # восстанавливаем скорость
        if old_speed is not None:
            try:
                with open(speed_file, 'w', encoding='utf-8') as f:
                    f.write(old_speed)
            except OSError:
                pass

        if has_autosave():
            log('сейв найден, закрываю игру...')
            close_game(log)
            return True

        log('!! сейв не появился за 4 минуты')
        return False
    finally:
        # скорость восстанавливаем в любом случае (даже при исключении)
        if old_speed is not None:
            try:
                with open(speed_file, 'w', encoding='utf-8') as f:
                    f.write(old_speed)
            except OSError:
                pass


def main(argv=None):
    ap = argparse.ArgumentParser(
        prog='baseline.py',
        description='Создать saves/baseline.dat (эталонный сейв для эволюции).')
    ap.add_argument('--game-dir', default=None,
                    help='папка игры (по умолчанию - из настроек лаунчера)')
    ap.add_argument('--force', action='store_true',
                    help='пересоздать baseline, даже если он уже есть')
    args = ap.parse_args(argv)

    game_dir = (args.game_dir or '').strip().strip('"') or gamepath.game_dir()
    if not game_dir:
        raise SystemExit(
            'Не найдена папка игры. Укажите её в лаунчере KC Bot '
            '(кнопка «Папка игры») или задайте KCBOT_GAME_DIR.')

    ok = ensure(game_dir, force=args.force)
    return 0 if ok else 1


if __name__ == '__main__':
    sys.exit(main())

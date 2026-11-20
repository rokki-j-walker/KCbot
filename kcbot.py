# -*- coding: utf-8 -*-
"""kcbot.py - единая точка входа для всех CLI-инструментов KCbot.

Раньше каждый инструмент был отдельным скриптом (recorder.py, hold.py,
shot.py, ...) с собственным ручным разбором аргументов. Теперь они живут
подкомандами одного CLI (решение D30):

    py kcbot.py record --duration 120 --output human_1.json
    py kcbot.py convert --input human_1.json --output human_genome_1.json
    py kcbot.py restart [--close] [--kill]
    py kcbot.py hold s 3
    py kcbot.py shot out.png
    py kcbot.py human-train --sessions 4 --duration 120
    py kcbot.py baseline [--force]
    py kcbot.py patch | unpatch | patch-status
    py kcbot.py diag
    py kcbot.py bot --own-focus --speed 3
    py kcbot.py evo --pop 50 --rounds 15

Каждая подкоманда лишь перенаправляет sys.argv в main() соответствующего
модуля: так существующие argparse/ручные парсеры продолжают работать без
переделки, а поведение не меняется.
"""
import argparse
import importlib
import sys

# подкоманда -> (модуль, подставляемые argv перед пользовательскими)
SUBCOMMANDS = {
    'record':       ('recorder',       []),
    'convert':      ('converter',      []),
    'restart':      ('restart',        []),
    'hold':         ('hold',           []),
    'shot':         ('shot',           []),
    'human-train':  ('human_trainer',  []),
    'baseline':     ('baseline',       []),
    'patch':        ('patcher',        ['--patch']),
    'unpatch':      ('patcher',        ['--unpatch']),
    'patch-status': ('patcher',        ['--status']),
    'bot':          ('bot3',           []),
    'evo':          ('evo',           []),
}

DESCRIPTION = 'Единый CLI KCbot. Подкоманды: ' + ', '.join(sorted(SUBCOMMANDS))


def _diag(args):
    """Диагностика: папка игры, патч, окно, свежесть kcdbg.txt."""
    import os
    import time

    import gamepath
    import patcher

    print('=== KCbot диагностика ===')
    gd = gamepath.game_dir()
    print('папка игры : %s' % (gd or 'НЕ ЗАДАНА'))
    if gd:
        exe = os.path.join(gd, 'Kingdom.exe')
        print('Kingdom.exe: %s' % ('есть' if os.path.isfile(exe) else 'НЕ НАЙДЕН'))

    code, msg = patcher.status(gd or None)
    print('патч       : %s (%s)' % (msg, code))

    try:
        import baseline
        hwnd = baseline.find_window()
        print('окно игры  : %s' % (hwnd if hwnd else 'не запущена'))
        if hwnd:
            r = baseline.window_rect(hwnd)
            if r:
                print('  геометрия: %d,%d %dx%d' % (
                    r.left, r.top, r.right - r.left, r.bottom - r.top))
    except Exception as e:
        print('окно игры  : ошибка %r' % (e,))

    if gd:
        kcdbg = os.path.join(gd, 'Kingdom_Data', 'kcdbg.txt')
        if os.path.isfile(kcdbg):
            age = time.time() - os.path.getmtime(kcdbg)
            print('kcdbg.txt  : %d байт, обновлялся %.1f с назад%s'
                  % (os.path.getsize(kcdbg), age,
                     '  <- УСТАРЕЛ' if age > 5 else ''))
            try:
                import bot3
                st = bot3.read_state()
                if st is None:
                    print('состояние  : нет данных')
                else:
                    print('состояние  : playing=%s day=%d time=%.1f px=%.1f'
                          % (st.playing, st.day, st.time, st.px))
            except Exception as e:
                print('состояние  : ошибка %r' % (e,))
        else:
            print('kcdbg.txt  : НЕТ (игра не запущена или патч не применён)')

    return 0


def _speed(args):
    """Скорость игры: показать/записать speed.txt (читает KCLog5, ~1-2 с)."""
    import os

    import gamepath
    gd = gamepath.game_dir()
    if not gd:
        print('!! папка игры не задана')
        return 1
    path = os.path.join(gd, 'Kingdom_Data', 'speed.txt')
    if not args:
        try:
            with open(path, 'r', encoding='utf-8') as f:
                print('speed.txt: %s' % f.read().strip())
        except OSError:
            print('speed.txt: нет файла (игра создаст при старте со значением 1)')
        return 0
    try:
        v = float(args[0].replace(',', '.'))
    except ValueError:
        print('!! значение не число: %r' % args[0])
        return 1
    if not (0.0 < v <= 10.0):
        # диапазон KCLog5: >0 и <=10; для ботов bot3 дополнительно клампит 1..3
        print('!! допустим диапазон (0, 10], получено %s' % v)
        return 1
    with open(path, 'w', encoding='utf-8') as f:
        f.write(str(v))
    print('speed.txt = %s -> игра применит за ~1-2 с' % v)
    return 0


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)

    ap = argparse.ArgumentParser(
        prog='kcbot.py', description=DESCRIPTION,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('command', nargs='?', choices=sorted(SUBCOMMANDS) + ['diag', 'speed'],
                    help='подкоманда')
    ap.add_argument('args', nargs=argparse.REMAINDER,
                    help='аргументы подкоманды (см. py <модуль>.py --help)')
    ns = ap.parse_args(argv)

    if not ns.command:
        ap.print_help()
        print('\nПодробности подкоманды:')
        for cmd in sorted(SUBCOMMANDS):
            print('  py kcbot.py %-14s -> %s.py' % (cmd, SUBCOMMANDS[cmd][0]))
        print('  py kcbot.py diag          -> диагностика')
        print('  py kcbot.py speed 0.5     -> скорость игры (для своих сессий)')
        return 0

    if ns.command == 'diag':
        return _diag(ns)

    if ns.command == 'speed':
        return _speed(ns.args)

    mod_name, prefix = SUBCOMMANDS[ns.command]
    # перехват sys.argv: так работают и argparse, и ручные парсеры
    sys.argv = [mod_name + '.py'] + prefix + list(ns.args)
    mod = importlib.import_module(mod_name)
    result = mod.main()
    return int(result) if isinstance(result, int) else 0


if __name__ == '__main__':
    sys.exit(main())

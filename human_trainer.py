# -*- coding: utf-8 -*-
"""
human_trainer.py — обучение бота на игре человека.

Запуск:
    py human_trainer.py --sessions 4 --duration 120

Процесс:
  1. Запускает игру.
  2. Ждёт 5 сек (скип обучения).
  3. Записывает действия игрока 2 минуты.
  4. Сохраняет запись.
  5. Перезапускает игру.
  6. Повторяет 4 раза.
  7. Конвертирует записи в геномы.
  8. Сохраняет геномы в human_genome_1..4.json.

После этого можно запустить эволюцию с подмешиванием:
  py evo.py --pop 50 --rounds 15 --episode 20 --human-genomes human_genome_1.json,human_genome_2.json,...
"""
import json
import os
import subprocess
import sys
import time

import gamepath

HERE = os.path.dirname(os.path.abspath(__file__))
GAME_DIR = gamepath.require_game_dir()
GAME_EXE = os.path.join(GAME_DIR, 'Kingdom.exe')
SAVE = os.path.join(os.environ['USERPROFILE'], 'AppData', 'LocalLow',
                    'noio', 'Kingdom', 'storage_v26_AUTO.dat')
BASELINE = os.path.join(HERE, 'saves', 'baseline.dat')


def find_window(title='Kingdom'):
    import ctypes, ctypes.wintypes as wt
    u = ctypes.windll.user32
    res = []
    def cb(hwnd, lp):
        if not u.IsWindowVisible(hwnd):
            return True
        n = u.GetWindowTextLengthW(hwnd)
        if n == 0:
            return True
        b = ctypes.create_unicode_buffer(n + 1)
        u.GetWindowTextW(hwnd, b, n + 1)
        if title.lower() in b.value.lower():
            res.append(hwnd)
        return True
    u.EnumWindows(ctypes.WINFUNCTYPE(ctypes.c_bool, wt.HWND, wt.LPARAM)(cb), 0)
    return res[0] if res else None


def restart_game():
    subprocess.run(['taskkill', '/F', '/IM', 'Kingdom.exe'],
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    time.sleep(2)
    if os.path.exists(BASELINE):
        import shutil
        shutil.copyfile(BASELINE, SAVE)
    subprocess.Popen([GAME_EXE], cwd=GAME_DIR,
                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    # ждём окно
    for _ in range(60):
        time.sleep(1)
        if find_window():
            break
    time.sleep(5)
    # скип обучения
    from hold import send as _send
    _send(0x1F, False)
    time.sleep(5)
    _send(0x1F, True)
    time.sleep(2)


def record_session(duration, output):
    import bot3
    print('  Запись %d сек...' % duration, flush=True)
    recording = []
    t0 = time.time()
    last_state = None
    while time.time() - t0 < duration:
        st = bot3.read_state()
        if st is not None:
            entry = {
                't': round(time.time() - t0, 2),
                'px': st.px, 'purse': st.purse, 'action': st.action,
                'pay_state': st.pay_state, 'sel': st.sel.base if st.sel else None,
                'sel_price': st.sel.price if st.sel else 0, 'hc': st.hc,
                'day': st.day, 'time': st.time, 'playing': st.playing,
                'arc': st.arc, 'wrk': st.wrk, 'frm': st.frm, 'knt': st.knt,
                'wall': st.wall, 'shop': st.shop, 'farm': st.farm,
                'portal': st.portal, 'enemies': st.enemies,
                'ground_coins': st.ground_coins,
            }
            if last_state is None or entry != last_state:
                recording.append(entry)
                last_state = entry
        time.sleep(0.25)
    with open(output, 'w', encoding='utf-8') as f:
        json.dump(recording, f, ensure_ascii=False, indent=2)
    print('  Записано %d кадров -> %s' % (len(recording), output), flush=True)


def main():
    sessions = 4
    duration = 120.0
    speed = 1.0
    for i, a in enumerate(sys.argv):
        if a == '--sessions' and i + 1 < len(sys.argv):
            sessions = int(sys.argv[i + 1])
        if a == '--duration' and i + 1 < len(sys.argv):
            duration = float(sys.argv[i + 1])
        if a == '--speed' and i + 1 < len(sys.argv):
            speed = float(sys.argv[i + 1])

    print('=== Обучение на игре человека: %d сессий по %.0f сек (скорость %.1fx) ===' % (sessions, duration, speed))
    # записать скорость в файл для игры
    speed_file = os.path.join(GAME_DIR, 'Kingdom_Data', 'speed.txt')
    try:
        with open(speed_file, 'w') as f:
            f.write(str(speed))
        print('Скорость игры: %.1fx (записано в %s)' % (speed, speed_file))
    except Exception as e:
        print('Не смог записать скорость: %r' % (e,))

    for s in range(1, sessions + 1):
        print()
        print('--- Сессия %d / %d ---' % (s, sessions))
        print('  Запускаю игру...')
        restart_game()
        print('  Игра запущена. Играйте %d секунд!' % duration)
        out = os.path.join(HERE, 'data', 'human_%d.json' % s)
        record_session(duration, out)
        if s < sessions:
            print('  Перезапуск игры для следующей сессии...')
            time.sleep(2)

    print()
    print('=== Конвертация записей в геномы ===')
    # старые геномы удаляем, чтобы в эволюцию не попали записи прошлых запусков
    import glob as _glob
    for old in _glob.glob(os.path.join(HERE, 'data', 'human_genome_*.json')):
        try:
            os.remove(old)
        except OSError:
            pass
    for s in range(1, sessions + 1):
        inp = os.path.join(HERE, 'data', 'human_%d.json' % s)
        out = os.path.join(HERE, 'data', 'human_genome_%d.json' % s)
        subprocess.run([sys.executable, os.path.join(HERE, 'converter.py'),
                        '--input', inp, '--output', out], cwd=HERE)
        print('  human_genome_%d.json создан' % s)

    print()
    print('=== Готово! ===')
    print('Геномы: human_genome_1.json .. human_genome_%d.json' % sessions)
    print('Для эволюции с подмешиванием запустите:')
    print('  py evo.py --pop 50 --rounds 15 --episode 20 --human-genomes human_genome_1.json,human_genome_2.json,...')


if __name__ == '__main__':
    main()
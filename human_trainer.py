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
import kc_common

HERE = os.path.dirname(os.path.abspath(__file__))
DATA = kc_common.data_dir()
# Папка игры НЕ запрашивается на импорте (иначе import human_trainer падает
# и --help невозможен) - резолвится в main().
GAME_DIR = ''
GAME_EXE = ''
SAVE = gamepath.save_file()
BASELINE = os.path.join(DATA, 'saves', 'baseline.dat')


def find_window(title='Kingdom'):
    """Единая реализация - kc_common.find_window (проверка процесса)."""
    return kc_common.find_window(title)


def focus_game(hwnd):
    """Вернуть фокус на окно игры: S должен уйти именно в неё.

    Без этого SendInput уходит в окно, активное на момент запуска
    (терминал/редактор/лаунчер) - скип обучения не срабатывал, а 5-секундное
    зажатие S портило текст в чужом окне.
    """
    import ctypes
    u = ctypes.windll.user32
    # трюк с Alt: Windows разрешает SetForegroundWindow, если предварительно
    # снять ограничение через нажатие Alt
    u.keybd_event(0x12, 0, 0, 0)          # Alt down
    u.keybd_event(0x12, 0, 2, 0)          # Alt up
    u.SetForegroundWindow(hwnd)
    u.SwitchToThisWindow(hwnd, True)
    for _ in range(20):
        if u.GetForegroundWindow() == hwnd:
            return True
        time.sleep(0.1)
    return u.GetForegroundWindow() == hwnd


def restart_game():
    global GAME_DIR, GAME_EXE
    subprocess.run(['taskkill', '/F', '/IM', 'Kingdom.exe'],
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    time.sleep(2)
    if os.path.exists(BASELINE):
        import shutil
        shutil.copyfile(BASELINE, SAVE)
    subprocess.Popen([GAME_EXE], cwd=GAME_DIR,
                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    # ждём окно; если оно не появилось - прекращаем сессию, а не пишем пустоту
    hwnd = None
    for _ in range(60):
        time.sleep(1)
        hwnd = find_window()
        if hwnd:
            break
    if not hwnd:
        raise RuntimeError('окно игры не появилось за 60 с - сессия прервана')
    if not focus_game(hwnd):
        print('  !! не удалось забрать фокус у окна игры', flush=True)
    time.sleep(5)
    # Скип обучения: подсказка на экране просит «нажать вниз», а «вниз» = S.
    # Достаточно 2-3 секунд (проверено вживую) - но на всякий случай
    # повторяем, пока игра не перейдёт в playing=True.
    from hold import send as _send
    import bot3
    for _attempt in range(3):
        _send(0x1F, False)
        time.sleep(3.0)
        _send(0x1F, True)
        time.sleep(1.5)
        st = bot3.read_state()
        if st is not None and st.playing:
            print('  обучение пропущено (попытка %d)' % (_attempt + 1), flush=True)
            break
        print('  игра ещё не началась, повторяю скип', flush=True)
    time.sleep(2)


def record_session(duration, output):
    import bot3
    print('  Запись %d сек...' % duration, flush=True)
    os.makedirs(os.path.dirname(output) or '.', exist_ok=True)
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
    global GAME_DIR, GAME_EXE
    GAME_DIR = gamepath.require_game_dir()
    GAME_EXE = os.path.join(GAME_DIR, 'Kingdom.exe')
    if not os.path.isfile(GAME_EXE):
        raise SystemExit('!! не найден файл игры: %s' % GAME_EXE)

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
    # D7: потолок 3x - на 4x и выше тайминги игры ломаются
    if not (1.0 <= speed <= 3.0):
        raise SystemExit('!! --speed должен быть от 1 до 3, получено: %s' % speed)

    print('=== Обучение на игре человека: %d сессий по %.0f сек (скорость %.1fx) ===' % (sessions, duration, speed))
    os.makedirs(os.path.join(DATA, 'data'), exist_ok=True)
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
        out = os.path.join(DATA, 'data', 'human_%d.json' % s)
        record_session(duration, out)
        if s < sessions:
            print('  Перезапуск игры для следующей сессии...')
            time.sleep(2)

    print()
    print('=== Конвертация записей в геномы ===')
    # Сначала конвертируем и проверяем результат, и только потом чистим старое:
    # иначе при ошибке конвертера прошлые геномы уже удалены, а новые не созданы.
    import glob as _glob
    created = []
    failed = []
    for s in range(1, sessions + 1):
        inp = os.path.join(DATA, 'data', 'human_%d.json' % s)
        out = os.path.join(DATA, 'data', 'human_genome_%d.json' % s)
        rc = subprocess.run([sys.executable, os.path.join(HERE, 'converter.py'),
                             '--input', inp, '--output', out],
                            cwd=HERE).returncode
        if rc == 0 and os.path.exists(out):
            created.append(out)
            print('  human_genome_%d.json создан' % s)
        else:
            failed.append(s)
            print('  !! human_genome_%d.json НЕ создан (converter.py rc=%s)'
                  % (s, rc))

    if failed:
        raise SystemExit(
            '!! конвертация не удалась для сессий %s - старые геномы НЕ тронуты, '
            'проверьте записи data/human_*.json' % failed)

    # чистим только лишние геномы прошлых запусков (с другим числом сессий)
    keep = set(os.path.basename(p) for p in created)
    for old in _glob.glob(os.path.join(DATA, 'data', 'human_genome_*.json')):
        if os.path.basename(old) not in keep:
            try:
                os.remove(old)
                print('  удалён устаревший %s' % os.path.basename(old))
            except OSError:
                pass

    print()
    print('=== Готово! ===')
    print('Геномы: human_genome_1.json .. human_genome_%d.json' % sessions)
    print('Для эволюции с подмешиванием запустите:')
    print('  py evo.py --pop 50 --rounds 15 --episode 20 --human-genomes human_genome_1.json,human_genome_2.json,...')


if __name__ == '__main__':
    main()
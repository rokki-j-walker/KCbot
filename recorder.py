# -*- coding: utf-8 -*-
"""
recorder.py — запись состояния игры в JSON (для обучения бота).

Запуск:
    py recorder.py --duration 120 --output human_1.json

Читает kcdbg.txt (пишет вклеенный логгер KCLog5) каждые 0.25 сек.
Фокус окна не нужен - скрипт ничего не нажимает.
"""
import json
import os
import sys
import time

import gamepath

HERE = os.path.dirname(os.path.abspath(__file__))
# Папка игры НЕ запрашивается на импорте (иначе import recorder падает).
# Проверка - в main(), там же берётся путь к kcdbg у bot3.
sys.path.insert(0, HERE)
import bot3


def parse_args(argv=None):
    import argparse
    ap = argparse.ArgumentParser(
        prog='recorder.py',
        description='Запись состояния игры Kingdom: Classic в JSON '
                    '(для последующей конвертации в геном).')
    ap.add_argument('--duration', type=float, default=120.0,
                    help='длительность записи, сек (по умолчанию 120)')
    ap.add_argument('--output', default='human_recording.json',
                    help='выходной файл, относительно папки скрипта '
                         '(по умолчанию human_recording.json)')
    return ap.parse_args(argv)


def main():
    args = parse_args()
    duration = args.duration
    output = args.output

    if not gamepath.game_dir():
        raise SystemExit(
            'Не найдена папка игры. Укажите её в лаунчере KC Bot '
            '(кнопка «Папка игры») или задайте KCBOT_GAME_DIR.')

    out_path = os.path.join(HERE, output)
    os.makedirs(os.path.dirname(out_path) or '.', exist_ok=True)
    if os.path.exists(out_path):
        print('!! файл %s будет перезаписан' % out_path)

    print('Запись %d секунд -> %s' % (duration, out_path))
    print('Играйте в игру. Запись начнётся через 3 секунды...', flush=True)
    time.sleep(3)

    recording = []
    t0 = time.time()
    last_state = None
    while time.time() - t0 < duration:
        st = bot3.read_state()
        if st is not None:
            # записываем компактное состояние
            entry = {
                't': round(time.time() - t0, 2),
                'px': st.px,
                'purse': st.purse,
                'action': st.action,
                'pay_state': st.pay_state,
                'sel': st.sel.base if st.sel else None,
                'sel_price': st.sel.price if st.sel else 0,
                'hc': st.hc,
                'day': st.day,
                'time': st.time,
                'playing': st.playing,
                'arc': st.arc,
                'wrk': st.wrk,
                'frm': st.frm,
                'knt': st.knt,
                'wall': st.wall,
                'shop': st.shop,
                'farm': st.farm,
                'portal': st.portal,
                'enemies': st.enemies,
                'ground_coins': st.ground_coins,
            }
            # записываем только если состояние изменилось
            if last_state is None or entry != last_state:
                recording.append(entry)
                last_state = entry
        time.sleep(0.25)

    # сохраняем (папка создана в начале main)
    try:
        with open(out_path, 'w', encoding='utf-8') as f:
            json.dump(recording, f, ensure_ascii=False, indent=2)
    except OSError as e:
        raise SystemExit('!! не удалось сохранить запись в %s: %s' % (out_path, e))
    print('Записано %d кадров в %s' % (len(recording), out_path), flush=True)


if __name__ == '__main__':
    main()
# -*- coding: utf-8 -*-
"""
recorder.py — запись действий игрока в Kingdom: Classic.

Запуск:
    py recorder.py --duration 120 --output human_1.json

Игра должна быть запущена и в фокусе.
Бот читает kcdbg.txt каждые 0.25 сек и записывает состояние.
После завершения создаёт файл с записью (для обучения бота).
"""
import json
import os
import sys
import time

import gamepath

HERE = os.path.dirname(os.path.abspath(__file__))
GAME_DIR = gamepath.require_game_dir()
KCDBG = os.path.join(GAME_DIR, 'Kingdom_Data', 'kcdbg.txt')

# добавляем путь к bot3 для парсера
sys.path.insert(0, HERE)
import bot3


def main():
    duration = 120.0
    output = 'human_recording.json'
    for i, a in enumerate(sys.argv):
        if a == '--duration' and i + 1 < len(sys.argv):
            duration = float(sys.argv[i + 1])
        if a == '--output' and i + 1 < len(sys.argv):
            output = sys.argv[i + 1]

    print('Запись %d секунд -> %s' % (duration, output))
    print('Играйте в игру. Запись начнётся через 3 секунды...')
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

    # сохраняем
    out_path = os.path.join(HERE, output)
    with open(out_path, 'w', encoding='utf-8') as f:
        json.dump(recording, f, ensure_ascii=False, indent=2)
    print('Записано %d кадров в %s' % (len(recording), out_path))


if __name__ == '__main__':
    main()
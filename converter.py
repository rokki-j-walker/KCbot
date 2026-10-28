# -*- coding: utf-8 -*-
"""
converter.py — конвертация записи игрока в геном.

Запуск:
    py converter.py --input human_1.json --output human_genome_1.json

Анализирует запись и создаёт геном, имитирующий стиль игрока.
"""
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import bot3


def analyze(recording):
    """Анализирует запись и возвращает словарь метрик."""
    if not recording:
        return {}

    # время игры
    total_time = recording[-1]['t'] - recording[0]['t']
    if total_time <= 0:
        total_time = 1

    # сбор монет
    coins_collected = 0
    prev_purse = recording[0]['purse']
    for e in recording:
        if e['purse'] > prev_purse:
            coins_collected += e['purse'] - prev_purse
        prev_purse = e['purse']

    # траты
    coins_spent = 0
    prev_purse = recording[0]['purse']
    for e in recording:
        if e['purse'] < prev_purse:
            coins_spent += prev_purse - e['purse']
        prev_purse = e['purse']

    # покупки (изменение sel)
    buys = 0
    prev_sel = None
    for e in recording:
        if e['sel'] is not None and e['sel'] != prev_sel:
            buys += 1
        prev_sel = e['sel']

    # найм (изменение citizens)
    # считаем по изменению arc+wrk+frm+knt+citizens
    # (упрощённо: считаем по изменению P count, но P не в записи)
    # используем arc+wrk+frm+knt как proxy
    prev_cit = recording[0]['arc'] + recording[0]['wrk'] + recording[0]['frm'] + recording[0]['knt']
    recruited = 0
    for e in recording:
        cur_cit = e['arc'] + e['wrk'] + e['frm'] + e['knt']
        if cur_cit > prev_cit:
            recruited += cur_cit - prev_cit
        prev_cit = cur_cit

    # бег vs ходьба
    run_time = sum(1 for e in recording if e['action'] == 'Run')
    walk_time = sum(1 for e in recording if e['action'] == 'Walk')
    total_actions = run_time + walk_time
    run_ratio = run_time / max(1, total_actions)

    # средняя позиция (где играет)
    avg_px = sum(e['px'] for e in recording) / len(recording)

    # максимальное расстояние от 0
    max_dist = max(abs(e['px']) for e in recording)

    # враги
    enemy_encounters = sum(1 for e in recording if e['enemies'] > 0)
    enemy_ratio = enemy_encounters / len(recording)

    # дни
    days = recording[-1]['day'] - recording[0]['day']

    # стамина (средняя)
    # (не в записи, пропускаем)

    return {
        'total_time': total_time,
        'coins_collected': coins_collected,
        'coins_spent': coins_spent,
        'buys': buys,
        'recruited': recruited,
        'run_ratio': run_ratio,
        'avg_px': avg_px,
        'max_dist': max_dist,
        'enemy_encounters': enemy_encounters,
        'enemy_ratio': enemy_ratio,
        'days': days,
        'coins_per_minute': coins_collected / (total_time / 60.0) if total_time > 0 else 0,
        'buys_per_minute': buys / (total_time / 60.0) if total_time > 0 else 0,
    }


def genome_from_analysis(analysis):
    """Создаёт геном на основе анализа записи игрока."""
    g = dict(bot3.DEFAULT_GENOME)

    # адаптируем параметры под стиль игрока
    # если игрок много бегает -> увеличить sprint_min_dist
    if analysis['run_ratio'] > 0.5:
        g['sprint_min_dist'] = 5.0
        g['sprint_min_stamina'] = 0.2
    else:
        g['sprint_min_dist'] = 12.0
        g['sprint_min_stamina'] = 0.4

    # если игрок собирает много монет -> увеличить tree_prio (больше фарма)
    if analysis['coins_per_minute'] > 2:
        g['tree_prio'] = 70
    elif analysis['coins_per_minute'] > 1:
        g['tree_prio'] = 80
    else:
        g['tree_prio'] = 90

    # если игрок много покупает -> уменьшить keep_coins (тратить больше)
    if analysis['buys_per_minute'] > 1:
        g['keep_coins'] = 0
    else:
        g['keep_coins'] = 1

    # если игрок нанимает много -> увеличить target_workers
    if analysis['recruited'] > 3:
        g['target_workers'] = 6
        g['target_archers'] = 8
    elif analysis['recruited'] > 1:
        g['target_workers'] = 5
        g['target_archers'] = 7

    # если игрок часто встречает врагов -> увеличить danger_dist
    if analysis['enemy_ratio'] > 0.3:
        g['danger_dist'] = 15.0
        g['flee_dist'] = 25.0
    else:
        g['danger_dist'] = 11.0
        g['flee_dist'] = 22.0

    # налоги: если игрок собирает налоги -> tax_min_coins ниже
    if analysis['coins_per_minute'] > 1.5:
        g['tax_min_coins'] = 1

    return g


def main():
    inp = None
    out = 'human_genome.json'
    for i, a in enumerate(sys.argv):
        if a == '--input' and i + 1 < len(sys.argv):
            inp = sys.argv[i + 1]
        if a == '--output' and i + 1 < len(sys.argv):
            out = sys.argv[i + 1]

    if not inp:
        print('Использование: py converter.py --input recording.json --output genome.json')
        return

    with open(inp, 'r', encoding='utf-8') as f:
        recording = json.load(f)

    print('Анализ записи: %d кадров, %.1f сек' % (len(recording), recording[-1]['t'] - recording[0]['t']))
    analysis = analyze(recording)
    print()
    print('=== Метрики игрока ===')
    for k, v in analysis.items():
        print('  %s: %s' % (k, v))

    genome = genome_from_analysis(analysis)
    out_path = os.path.join(HERE, out)
    bot3.save_genome(out_path, genome)
    print()
    print('Геном сохранён: %s' % out_path)
    print('Ключевые параметры:')
    for k in ['sprint_min_dist', 'sprint_min_stamina', 'tree_prio', 'keep_coins',
              'target_workers', 'target_archers', 'danger_dist', 'flee_dist', 'tax_min_coins']:
        print('  %s = %s' % (k, genome.get(k)))


if __name__ == '__main__':
    main()
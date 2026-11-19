# -*- coding: utf-8 -*-
"""Дифф-тест Bot.execute(): эталон vs текущий код.

execute() - диспетчер задач (idle/recruit_walk/recruit_drop/crown/flee/
coin/tax/pay). Тест гоняет его по всем типам task на реальных кадрах,
подменяя побочные эффекты (ввод, ходьбу, do_pay, metrics) на запись в
журнал, и снимает снимок изменяемого состояния после каждого кадра.

Запуск:
    py exectest.py ref     # снять эталон ДО правки
    py exectest.py check   # сравнить (зелёный/красный прогон)
"""
import io
import os
import sys
import tempfile

import tracktest

REF = os.path.join(tempfile.gettempdir(), 'kc_exec_ref.txt')

DT = 0.25                       # шаг времени на кадр, сек
KINDS = ['idle', 'recruit_walk', 'recruit_drop', 'crown',
         'flee', 'coin', 'tax', 'pay']


class Clock(object):
    def __init__(self):
        self.t = 0.0


def task_for(i, st):
    """Какой task выполняем на кадре i (покрывает все ветки диспетчера)."""
    if i < 12:
        return ('tax', 12.5)            # налог идёт подряд: ветка ожидания
    if i % 11 == 5:
        return ('bogus', None)          # неизвестный task: молча игнор
    kind = KINDS[i % len(KINDS)]
    if kind == 'recruit_walk':
        return (kind, 7.5)
    if kind == 'crown':
        return (kind, -3.0)
    if kind == 'flee':
        return (kind, ('Goblin', 20.0, True, 15.0))
    if kind == 'coin':
        return (kind, 4.0)
    if kind == 'tax':
        return (kind, 12.5)
    if kind == 'pay':
        # pay с None обязан молча вернуться; остальные - дойти до do_pay
        return (kind, None if i % 3 == 0 else ('payable', i))
    return (kind, None)                 # idle, recruit_drop


def snap(bot):
    """Снимок состояния, которое execute() имеет право менять."""
    return ('retreat_until=%.2f retreat_x=%.1f tax_until=%.2f '
            'drop_until=%.2f last_drop=%.2f check_time=%.2f check_p=%r '
            'last_recruit=%r coin_black=%r') % (
        bot._retreat_until, bot._retreat_x, bot.tax_until,
        getattr(bot, '_recruit_drop_until', 0.0),
        getattr(bot, 'last_recruit_drop', 0.0),
        getattr(bot, '_recruit_check_time', 0.0),
        getattr(bot, '_recruit_check_p_count', None),
        bot.stat.get('last_recruit'),
        sorted(bot.coin_black.items()))


def run():
    import bot3
    clock = Clock()
    bot = bot3.Bot(dry=True, log=lambda *a: None)
    bot.now = lambda: clock.t
    bot.fit['start'] = 0.0

    calls = []
    # побочные эффекты -> журнал (сами они тестируются в других проверках)
    bot.note = lambda k, v: calls.append('note %s | %s' % (k, v))
    bot.walk_to = lambda st, x, sprint=False, tol=0.5: calls.append(
        'walk_to x=%.3f sprint=%s tol=%.2f' % (x, sprint, tol))
    bot.release_s = lambda: calls.append('release_s')
    bot.stop_all = lambda: calls.append('stop_all')
    bot.stop_move = lambda: calls.append('stop_move')
    bot.do_pay = lambda st, p: calls.append('do_pay %r' % (p,))
    bot.metrics = lambda: {'score': 0.0}    # score иначе зависит от time.time()

    states = tracktest.load_states()
    for i, st in enumerate(states):
        clock.t += DT
        # ретрит после броска монеты: ветка до разбора task
        if i % 7 == 3:
            bot._retreat_until = clock.t + 1.0
            bot._retreat_x = 3.0
        bot.task = task_for(i, st)
        try:
            bot.execute(st)
        except Exception as e:
            print('execute() упал на кадре %d (task=%r): %r' % (i, bot.task, e))
            raise
        calls.append('-- frame %d task=%r' % (i, bot.task))
        calls.append('   state ' + snap(bot))
    return calls


def main():
    mode = sys.argv[1] if len(sys.argv) > 1 else 'check'
    calls = run()

    if mode == 'ref':
        io.open(REF, 'w', encoding='utf-8').write('\n'.join(calls))
        print('эталон execute() снят: %d записей' % len(calls))
        return 0

    if not os.path.isfile(REF):
        print('нет эталона. Снимите ДО правки: py exectest.py ref')
        return 2

    ref = io.open(REF, encoding='utf-8').read().split('\n')
    if ref == calls:
        print('ЗЕЛЁНЫЙ ПРОГОН: %d записей, 0 расхождений' % len(calls))
        return 0

    print('КРАСНЫЙ ПРОГОН: было %d строк, стало %d' % (len(ref), len(calls)))
    diffs = 0
    for a, b in zip(ref, calls):
        if a != b:
            diffs += 1
            if diffs <= 20:
                print('   было: %s\n  стало: %s' % (a, b))
    print('расхождений: %d' % diffs)
    return 1


if __name__ == '__main__':
    sys.exit(main())

# -*- coding: utf-8 -*-
"""Дифф-тест Bot.do_pay(): эталон vs текущий код.

do_pay() - конечный автомат оплаты (подход -> остановка -> кружки ->
удержание S -> сканирование/отказ). Тест гоняет его по 14 сценариям,
покрывающим все фазы, подменяя побочные эффекты на запись в журнал,
и снимает снимок изменяемого состояния после каждого кадра.

Запуск:
    py paytest.py ref     # снять эталон ДО правки
    py paytest.py check   # сравнить (зелёный/красный прогон)
"""
import io
import os
import re
import sys
import tempfile

import tracktest

REF = os.path.join(tempfile.gettempdir(), 'kc_pay_ref.txt')

DT = 0.25                       # шаг времени на кадр, сек
N_CASES = 14                    # сценариев в расписании


class Clock(object):
    def __init__(self):
        self.t = 0.0


def setup(i, st, p, bot, t):
    """Свести автомат в состояние сценария i % 14."""
    # база: «свежий» кадр, все поля определены
    bot.pay_phase = 'pay'
    bot.pay_target = None
    bot.scan_x = st.px
    bot.scan_i = 0
    bot.scan_t = 0.0
    # t-5 проходит ворота 1.2/speed, но не триггерит giveup (7.0)
    bot.pay_since = t - 5.0
    bot._pay_last_px = st.px
    bot._pay_still_since = t - 10.0
    bot.pay_started = t - 0.1
    bot.pay_purse0 = st.purse
    st.sel = None
    st.pay_state = 'None'

    # смена цели: finish_pay на чужом Payable (блок до подхода)
    if i % 5 == 0 and len(st.payables) > 1:
        bot.pay_target = st.payables[-1]

    case = i % N_CASES
    if case in (0, 11):
        bot.scan_x = st.px + 30.0            # далеко -> идём к точке
    elif case == 1:
        # инерция: чётные проходы - _pay_last_px is None, нечётные - движение
        bot._pay_last_px = (None if (i // N_CASES) % 2 == 0
                            else st.px - 1.0)
    elif case == 2:
        bot.scan_t = 0.5                     # sel=None -> ротация точки
    elif case == 3:
        bot.pay_since = t - 10.0             # sel=None, пора сдаваться
    elif case == 4:
        st.sel = p                           # выбран -> держим S
        st.purse = 5
    elif case == 5:
        st.sel = p
        st.purse = 0                         # монет нет -> ранний выход
    elif case == 6:
        st.sel = p
        st.pay_state = 'Holding'             # уже оплачивается -> выход
    elif case == 7:
        bot.pay_phase = 'hold'               # ждём: ps='None', 0.2 < 0.6
    elif case == 8:
        bot.pay_phase = 'hold'               # ps='None' дольше 0.6 -> finish
        bot.pay_started = t - 10.0
    elif case == 9:
        bot.pay_phase = 'hold'               # таймаут -> note + finish
        bot.pay_started = t - 1000.0
        st.pay_state = 'Holding'
    elif case == 10:
        bot.pay_phase = 'hold'               # транзакция идёт -> ждём
        bot.pay_started = t - 0.1
        st.pay_state = 'Transaction'
    elif case == 12:
        bot._pay_still_since = 0.0           # только остановились -> ждём кружки
    elif case == 13:
        bot.pay_since = t - 1.0              # ворота 1.2/speed: молчаливое ожидание
        st.sel = p
        st.purse = 5


def snap(bot, st, p):
    """Снимок состояния, которое do_pay() имеет право менять."""
    return ('phase=%s started=%.2f purse0=%r target=%s scan_x=%.3f '
            'scan_i=%d scan_t=%.2f since=%.2f last_px=%r still=%.2f '
            'black=%r fail_tree=%d fail_buy=%d sel=%r purse=%s ps=%s') % (
        bot.pay_phase, bot.pay_started, bot.pay_purse0,
        repr(bot.pay_target), bot.scan_x, bot.scan_i, bot.scan_t,
        bot.pay_since, repr(bot._pay_last_px), bot._pay_still_since,
        sorted(bot.pay_black.items()),
        bot.fit.get('failed_tree', 0), bot.fit.get('failed_buy', 0),
        repr(st.sel), st.purse, st.pay_state)


def run():
    import bot3
    clock = Clock()
    bot = bot3.Bot(dry=True, log=lambda *a: None)
    bot.now = lambda: clock.t
    bot.fit['start'] = 0.0

    calls = []
    # побочные эффекты -> журнал; 0x... вычищаем на всякий случай
    scrub = lambda s: re.sub(r'0x[0-9a-fA-F]+', '0xX', str(s))
    bot.note = lambda k, v: calls.append('note %s | %s' % (k, scrub(v)))
    bot.walk_to = lambda st, x, sprint=False, tol=0.5: calls.append(
        'walk_to x=%.3f sprint=%s tol=%.2f' % (x, sprint, tol))
    bot.release_s = lambda: calls.append('release_s')
    bot.stop_move = lambda: calls.append('stop_move')
    bot.finish_pay = lambda st, p: calls.append('finish_pay %r' % (p,))

    states = tracktest.load_states()
    for i, st in enumerate(states):
        if not st.payables:
            calls.append('-- frame %d skip (нет payables)' % i)
            continue
        clock.t += DT
        p = st.payables[0]
        setup(i, st, p, bot, clock.t)
        try:
            bot.do_pay(st, p)
        except Exception as e:
            print('do_pay() упал на кадре %d (case=%d): %r'
                  % (i, i % N_CASES, e))
            raise
        calls.append('-- frame %d case=%d p=%r' % (i, i % N_CASES, p))
        calls.append('   state ' + snap(bot, st, p))
    return calls


def main():
    mode = sys.argv[1] if len(sys.argv) > 1 else 'check'
    calls = run()

    if mode == 'ref':
        io.open(REF, 'w', encoding='utf-8').write('\n'.join(calls))
        print('эталон do_pay() снят: %d записей' % len(calls))
        return 0

    if not os.path.isfile(REF):
        print('нет эталона. Снимите ДО правки: py paytest.py ref')
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

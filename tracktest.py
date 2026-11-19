# -*- coding: utf-8 -*-
"""Дифф-тест Bot.track(): эталон vs текущий код.

track() зависит от wall-clock, поэтому часы подменяются детерминированными:
каждый кадр двигает время на 0.25 с (как реальный логгер KCLog5).

Запуск:
    py tracktest.py ref     # снять эталон ДО правки
    py tracktest.py check   # сравнить (зелёный/красный прогон)
"""
import io
import os
import random
import sys
import tempfile

SAMPLES = os.path.join(tempfile.gettempdir(), 'kc_samples.txt')
REF = os.path.join(tempfile.gettempdir(), 'kc_fit_ref.txt')

DT = 0.25          # шаг времени на кадр, сек
SEED = 12345       # на случай, если track использует random


def load_states():
    import bot3
    if not os.path.isfile(SAMPLES):
        print('нет образцов - сначала: py difftest.py status')
        sys.exit(2)
    lines = [l for l in io.open(SAMPLES, encoding='utf-8').read().split('\n') if l]
    out = []
    for l in lines:
        try:
            st = bot3.parse_line(l)
        except Exception:
            continue
        if st is not None:
            out.append(st)
    return out


class Clock(object):
    def __init__(self):
        self.t = 0.0

    def tick(self, dt=DT):
        self.t += dt


def run_track():
    import bot3
    random.seed(SEED)
    clock = Clock()
    bot = bot3.Bot(dry=True, log=lambda *a: None)
    # подменяем время: и self.now(), и точку отсчёта эпизода
    bot.now = lambda: clock.t
    bot.fit['start'] = 0.0
    bot.fit['last_interaction'] = 0.0
    bot.fit['last_track_time'] = 0.0

    n = 0
    for st in load_states():
        clock.tick()
        try:
            bot.track(st)
        except Exception as e:
            print('track() упал на кадре %d: %r' % (n, e))
            raise
        n += 1

    fit = dict(bot.fit)
    lines = ['%s=%r' % (k, fit[k]) for k in sorted(fit)]
    return lines, n


def main():
    mode = sys.argv[1] if len(sys.argv) > 1 else 'check'
    fit, n = run_track()

    if mode == 'ref':
        io.open(REF, 'w', encoding='utf-8').write('\n'.join(fit))
        print('эталон track() снят: %d кадров, %d полей fit' % (n, len(fit)))
        return 0

    if not os.path.isfile(REF):
        print('нет эталона. Снимите ДО правки: py tracktest.py ref')
        return 2

    ref = io.open(REF, encoding='utf-8').read().split('\n')
    if len(ref) != len(fit):
        print('КРАСНЫЙ ПРОГОН: полей %d -> %d' % (len(ref), len(fit)))
        for a, b in zip(ref, fit):
            if a != b:
                print('   было: %s\n   стало: %s' % (a, b))
        return 1

    diffs = 0
    for a, b in zip(ref, fit):
        if a != b:
            diffs += 1
            print('   было: %s\n   стало: %s' % (a, b))
    if diffs:
        print('КРАСНЫЙ ПРОГОН: расхождений %d из %d' % (diffs, len(ref)))
        return 1
    print('ЗЕЛЁНЫЙ ПРОГОН: %d кадров, %d полей fit, 0 расхождений'
          % (n, len(fit)))
    return 0


if __name__ == '__main__':
    sys.exit(main())

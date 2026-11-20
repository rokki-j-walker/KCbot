# -*- coding: utf-8 -*-
"""speedtest.py - живая проверка контроллера скорости KCLog5 (DLL-фикса).

С фиксом после меню rate возвращается к ~0.30 (3x пере-применяется).
Без фикса stuck на 1x -> ~0.10.
"""
import sys
import time

sys.path.insert(0, '.')
import bot3
import hold

SPD = r'c:\program files (x86)\steam\steamapps\common\Kingdom\Kingdom_Data\speed.txt'


def read():
    return bot3.read_state()


def wait_playing(timeout=75.0):
    """По схеме evo: >8 с не-playing -> S на3 с, повтор каждые6 с."""
    t0 = time.time()
    not_playing_since = None
    last_hold = 0.0
    while time.time() - t0 < timeout:
        st = read()
        if st is not None and st.playing:
            return True
        now = time.time()
        if not_playing_since is None:
            not_playing_since = now
        elif now - not_playing_since > 8 and now - last_hold > 6:
            if hold.focus_game():
                hold.send(0x1F, False)
                time.sleep(3.0)
                hold.send(0x1F, True)
                last_hold = now
            not_playing_since = now
    return False


def rate(seconds=6.0):
    """rate при живом playing; None если состояние недоступно."""
    st0 = read()
    if st0 is None or not st0.playing:
        return None
    t0 = time.time()
    time.sleep(seconds)
    st1 = read()
    if st1 is None:
        return None
    dt = st1.time - st0.time
    if dt < 0:
        return None
    return dt / (time.time() - t0)


def rate_retry(attempts=3):
    for _ in range(attempts):
        r = rate()
        if r is not None:
            return r
        time.sleep(2.0)
    return None


def tap_esc():
    hold.send(0x01, False)
    time.sleep(0.15)
    hold.send(0x01, True)


def main():
    open(SPD, 'w').write('3.0')
    time.sleep(2.5)

    if not wait_playing():
        print('FAIL: playing не дождался')
        return 2

    r1 = rate_retry()
    print('rate BEFORE menu : %s' % ('%.3f' % r1 if r1 else 'None'))
    if r1 is None:
        print('FAIL: замер до меню недоступен')
        return 3

    hold.focus_game()
    tap_esc()                       # открыть меню (timeScale=0)
    time.sleep(2.0)
    tap_esc()                       # HideMenu() -> timeScale=1f
    time.sleep(2.5)                 # опрос KCLog5: раз в1 с

    r2 = rate_retry()
    print('rate AFTER  menu : %s   (fix: ~%.2f | bug: ~%.2f)'
          % ('%.3f' % r2 if r2 else 'None', r1, r1 / 3.0))

    if r2 is None:
        print('FAIL: замер после меню недоступен')
        return 3
    if r2 >= r1 * 0.8:
        print('RESULT: FIX CONFIRMED (%.3f -> %.3f)' % (r1, r2))
        return 0
    print('RESULT: SPEED LOST (%.3f -> %.3f)' % (r1, r2))
    return 1


if __name__ == '__main__':
    sys.exit(main())

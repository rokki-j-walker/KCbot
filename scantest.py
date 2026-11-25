# -*- coding: utf-8 -*-
"""scantest.py - скан-тесты: проверяют исходники, а не поведение.

Термин из [[09 Словарь терминов]]: тест, читающий исходный текст кода и
проверяющий наличие/отсутствие строк или структур.

Запуск:  py scantest.py        (0 = зелёный прогон, 1 = красный)
"""
import glob
import io
import os
import re
import sys

FAILURES = []


def check(name, ok, detail=''):
    print('  [%s] %s%s' % ('OK' if ok else 'FAIL', name,
                           ('  -> ' + detail) if detail else ''))
    if not ok:
        FAILURES.append(name)


def read(path):
    return io.open(path, encoding='utf-8').read()


def all_py():
    """Весь код проекта, БЕЗ самих тестов (иначе тест находит себя)."""
    skip = {'scantest.py', 'difftest.py'}
    return '\n'.join(read(p) for p in glob.glob('*.py')
                     if os.path.basename(p) not in skip)


# ---------------------------------------------------------------- bounties
def test_bounties():
    print('bounties.txt')
    keys, seen, dups = [], {}, []
    for i, line in enumerate(io.open('bounties.txt', encoding='utf-8'), 1):
        s = line.split('#', 1)[0].strip()
        if '=' not in s:
            continue
        k = s.split('=', 1)[0].strip()
        if k in seen:
            dups.append('%s (стр.%d и %d)' % (k, seen[k], i))
        seen[k] = i
        keys.append((k, i))

    check('нет дублей ключей', not dups, '; '.join(dups))

    code = all_py()
    unused = [k for k, _ in keys
              if not re.search(r"['\"]%s['\"]" % re.escape(k), code)]
    check('нет ключей, которые код не читает', not unused,
          '%d шт: %s' % (len(unused), ', '.join(unused[:8])))

    # парсер обязан отрезать инлайн-комментарий иначе ключ теряется
    import bot3
    with io.open('_scantest_bounties.tmp', 'w', encoding='utf-8') as f:
        f.write('some_key = 7.5   # хвостовой комментарий\n')
    try:
        got = bot3.load_bounties('_scantest_bounties.tmp')
    finally:
        os.remove('_scantest_bounties.tmp')
    check('парсер читает ключ с хвостовым комментарием',
          got.get('some_key') == 7.5, repr(got))


# ----------------------------------------------------------------- локали
def test_locales():
    print('locales')
    import json
    ru = json.load(io.open('locales/ru.json', encoding='utf-8'))
    en = json.load(io.open('locales/en.json', encoding='utf-8'))
    check('ru и en имеют одинаковый набор ключей',
          set(ru) == set(en),
          'только ru: %s; только en: %s' % (sorted(set(ru) - set(en)),
                                            sorted(set(en) - set(ru))))
    check('в локализации больше 80 ключей', len(ru) > 80, str(len(ru)))


# ------------------------------------------------------------- структура
def test_structure():
    print('структура')
    py = all_py()

    # реальная логика поиска окна должна быть в одном месте; остальные
    # find_window - тонкие делегаты (в них нет EnumWindows)
    n_enum = len(re.findall(r'\bEnumWindows\(', py))
    check('логика поиска окна (EnumWindows) в одном месте', n_enum == 1,
          'найдено %d' % n_enum)

    check('нет shell=True в launcher.py',
          'shell=True' not in read('launcher.py'))
    check('нет matplotlib в launcher.py',
          'import matplotlib' not in read('launcher.py'))
    check('нет C#-патчера в tools/',
          not os.path.exists(os.path.join('tools', 'patch.exe')))
    check('нет дубля формулы score_episode',
          py.count('def score_episode(') == 1 and
          'bot3_module().fitness' in py)

    # B14: ввод не должен блокировать главный цикл
    b3 = read('bot3.py')
    check('B14: нет блокирующего time.sleep(3.0)', 'time.sleep(3.0)' not in b3)
    check('B14: нет sleep в нажатиях (0.15/0.1)',
          'time.sleep(0.15' not in b3 and 'time.sleep(0.1 /' not in b3)
    check('B14: press_for определён и используется',
          'def press_for(' in b3 and 'self.press_for(' in b3)
    check('B14: _release_due вызывается в step()',
          re.search(r'def step\(self[^)]*\).*?self\._release_due\(',
                    b3, re.S) is not None)

    # 2026-11-23: успешная оплата не должна определяться только по
    # pay_purse0 - st.purse: подбор монет даёт отрицательный spent и
    # ложный pay_disabled (106 из 112 случаев в прогоне).
    check('pay: успех учитывает ps:Completed',
          "'Completed'" in b3 and 'pay_saw_completed' in b3)
    check('pay: учитывается минимум кошелька',
          'pay_purse_min' in b3 and 'spent_min' in b3)

    # глобальный конфиг изменяется только при старте: все 3 записи в
    # load_genome() (заливка генома, D17). Во время цикла - никогда.
    # (Аудит предлагал CFG/BOUNTIES -> объект; инвариант закреплён проверкой.)
    m_lg = re.search(r'\ndef load_genome\(.*?(?=\ndef )', py, re.S)
    outside = py.replace(m_lg.group(0), '') if m_lg else py
    assigns = re.findall(
        r'\b(?:CFG|BOUNTIES)\[[^\]]*\](?:\[[^\]]*\])*\s*(?:[+\-*/]=|=(?!=))'
        r'|\b(?:CFG|BOUNTIES)\.(?:update|pop|setdefault|clear)\('
        r'|\bdel\s+(?:CFG|BOUNTIES)\[', outside)
    check('CFG/BOUNTIES записываются только в load_genome()',
          m_lg is not None and not assigns, '; '.join(assigns[:5]))

    # R2/Д27: fit - объект со схемой; словарных обращений к нему не осталось
    check('R2: класс Fit объявлен', 'class Fit(' in b3)
    soup = re.findall(r"\.fit\[|\.fit\.get\(|\bf\['\w+'\]|\bf\.get\('",
                      b3)
    check('R2: нет словарных обращений к fit (суп ключей убран)',
          not soup, '; '.join(soup[:5]))

    # один read_state на такт: main читает состояние и ПЕРЕДАЁТ в step
    # (раньше step читал заново - два чтения и разные кадры, аудит)
    check('main передаёт st в step() (нет двойного чтения)',
          'bot.step(st)' in b3)

    # 2026-11-23: launch_after в frozen-сборке наследовал KC_RUN_MODULE=evo
    # и вместо лаунчера запускал скрытое обучение. Среда должна очищаться,
    # а запуск из лаунчера - не порождать второй лаунчер.
    evo = read('evo.py')
    check('launch_after использует launcher_env()',
          'child_env = launcher_env()' in evo)
    check('launch_after пропускается при KC_FROM_LAUNCHER',
          "os.environ.get('KC_FROM_LAUNCHER')" in evo)
    check('лаунчер выставляет KC_FROM_LAUNCHER',
          "env['KC_FROM_LAUNCHER'] = '1'" in read('launcher.py'))

    # Поведенческая проверка: среда лаунчера не должна содержать режим evo.
    # Иначе frozen-exe запустит второе обучение вместо UI (баг 2026-11-23).
    import evo as _evo
    keys = ('KC_RUN_MODULE', 'KC_EVO_ARGS', 'KC_FROM_LAUNCHER')
    old = {k: os.environ.get(k) for k in keys}
    try:
        for k in keys:
            os.environ[k] = 'x'
        env = _evo.launcher_env()
        check('launcher_env удаляет режим evo', all(k not in env for k in keys),
              ', '.join(k for k in keys if k in env))
    finally:
        for k, v in old.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v

    # Оценка времени: episode задан в ИГРОВЫХ секундах (решение 2026-11-25).
    # 6 раундов × 50 ботов × 2 оценки, episode 20 + increment 5:
    #   игровое = 100 × (25+30+35+40+45+50) = 22500 с = 6 ч 15 мин
    #            (ОДИНАКОВО при 1x и 3x)
    #   реальное 1x = 22500 + 600×16.9 = 32640 с = 9 ч 04 мин
    #   реальное 3x = 7500 + 600×16.9 = 17640 с = 4 ч 54 мин
    # Перезапуск (16.9 с) реальный и на скорость не делится.
    import launcher as _launcher
    cfg = {'pop': 50, 'rounds': 6, 'episode': 20.0,
           'episode_increment': 5.0, 'restart': True, 'eval_repeats': 2,
           'speed': 1.0}
    real1, game1, _h, _m, _d = _launcher.calc_time(cfg)
    cfg3 = dict(cfg, speed=3.0)
    real3, game3, _h3, _m3, _d3 = _launcher.calc_time(cfg3)
    check('calc_time 1x ≈ 9 ч 04 мин', abs(real1 - 32640) <= 300,
          '%.0f с (%.1f ч)' % (real1, real1 / 3600.0))
    check('calc_time 3x ≈ 4 ч 54 мин', abs(real3 - 17640) <= 300,
          '%.0f с (%.1f ч)' % (real3, real3 / 3600.0))
    check('игровое время не зависит от скорости',
          abs(game1 - game3) < 1e-6 and abs(game1 - 22500) <= 1,
          '1x=%.0f 3x=%.0f' % (game1, game3))
    check('реальное на 3x меньше, чем на 1x', real3 < real1,
          '1x=%.0f 3x=%.0f' % (real1, real3))

    # Формула игрового времени в самом боте: игровые = реальные × скорость.
    import time as _time
    import bot3 as _bot3
    b1 = _bot3.Bot(dry=True, log=lambda *a: None, speed=1.0)
    b3 = _bot3.Bot(dry=True, log=lambda *a: None, speed=3.0)
    for _b in (b1, b3):
        _b.fit.start = _time.time() - 10.0   # 10 реальных секунд назад
    check('game_seconds: на 1x 10 реальных с = 10 игровых',
          abs(b1.game_seconds() - 10.0) < 0.5, '%.1f' % b1.game_seconds())
    check('game_seconds: на 3x 10 реальных с = 30 игровых',
          abs(b3.game_seconds() - 30.0) < 0.5, '%.1f' % b3.game_seconds())
    b3.fit.paused_total = 4.0                # 4 реальных с на паузе
    check('game_seconds: пауза вычитается',
          abs(b3.game_seconds() - 18.0) < 0.5, '%.1f' % b3.game_seconds())
    # Структурно: выход из эпизода и таймаут обязаны быть по игровым секундам.
    check('выход из эпизода по игровым секундам',
          'bot.game_seconds() >= t_end' in read('bot3.py'))
    check('таймаут evo делит episode на скорость',
          'episode / sp + 60' in read('evo.py'))
    check('calc_time делит episode на скорость', 'ep / speed' in
          read('launcher.py'))

    # D52: миграция старого конфига (episode в РЕАЛЬНЫХ секундах -> игровым).
    import json as _json
    _cfg_path = _launcher.CONFIG_FILE
    _bak = None
    if os.path.exists(_cfg_path):
        _bak = io.open(_cfg_path, encoding='utf-8').read()
    try:
        # старый конфиг на 3x: episode=20 реальных -> 60 игровых
        io.open(_cfg_path, 'w', encoding='utf-8').write(_json.dumps(
            {'episode': 20.0, 'episode_increment': 5.0, 'speed': 3.0}))
        got = _launcher.load_config()
        check('миграция: 20 реальных на 3x -> 60 игровых',
              abs(got['episode'] - 60.0) < 1e-6 and
              abs(got['episode_increment'] - 15.0) < 1e-6,
              'episode=%s inc=%s' % (got['episode'],
                                     got['episode_increment']))
        check('миграция: маркер семантики проставлен',
              got.get('time_semantics') == 'game_v1',
              str(got.get('time_semantics')))
        # повторная загрузка не должна умножать второй раз
        io.open(_cfg_path, 'w', encoding='utf-8').write(_json.dumps(got))
        got2 = _launcher.load_config()
        check('миграция идемпотентна', abs(got2['episode'] - 60.0) < 1e-6,
              'episode=%s' % got2['episode'])
        # конфиг без ключа episode: дефолт не должен умножаться на скорость
        io.open(_cfg_path, 'w', encoding='utf-8').write(_json.dumps(
            {'speed': 3.0}))
        got3 = _launcher.load_config()
        check('миграция: отсутствующий episode берёт игровой дефолт',
              abs(got3['episode'] - 60.0) < 1e-6,
              'episode=%s' % got3['episode'])
    finally:
        try:
            if _bak is not None:
                io.open(_cfg_path, 'w', encoding='utf-8').write(_bak)
            elif os.path.exists(_cfg_path):
                os.remove(_cfg_path)
        except OSError:
            pass


# ----------------------------------------------------------------- сборка
def test_build_files():
    print('сборка')
    check('KCbot.spec версионируется', os.path.exists('KCbot.spec'))
    spec = read('KCbot.spec')
    check('spec включает bot3.py как файл', "'bot3.py'" in spec)
    check('spec без upx', 'upx=True' not in spec)
    check('requirements.txt существует', os.path.exists('requirements.txt'))
    check('KCbot.iss существует', os.path.exists('KCbot.iss'))


def main():
    print('=== СКАН-ТЕСТЫ ===')
    test_bounties()
    test_locales()
    test_structure()
    test_build_files()
    print()
    if FAILURES:
        print('КРАСНЫЙ ПРОГОН: %d проверок не прошли' % len(FAILURES))
        for f in FAILURES:
            print('   !!', f)
        return 1
    print('ЗЕЛЁНЫЙ ПРОГОН: все проверки прошли')
    return 0


if __name__ == '__main__':
    sys.exit(main())

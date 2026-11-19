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
          re.search(r'def step\(self\):\s*\n\s*self\._release_due\(', b3) is not None)

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

# -*- coding: utf-8 -*-
"""difftest.py - дифференциальный тест парсера состояния игры.

Нужен при ЛЮБОЙ правке parse_line/State: даёт зелёный или красный прогон
на реальных данных, а не на выдуманных.

Как работает
------------
1. Собирает реальные строки KC| из <game>/Kingdom_Data/kcdbg.txt
   (последние 2 МБ) и дополняет их синтетическими строками на ветки,
   которых в живых данных нет (cit, sel с ~, pt/fallback, неизвестные
   секции, пустые значения).
2. Разбирает каждую строку и сериализует все поля State.
3. Сравнивает с сохранённым эталоном.

Запуск:
    py difftest.py ref     # снять эталон (ЗЕПЕРЕД рефакторингом!)
    py difftest.py check   # сравнить текущий код с эталоном
    py difftest.py status  # только покрытие, без сравнения

Любое расхождение = КРАСНЫЙ ПРОГОН, код не отдавать.
"""
import io
import os
import re
import sys
import tempfile

SAMPLES = os.path.join(tempfile.gettempdir(), 'kc_samples.txt')
REF = os.path.join(tempfile.gettempdir(), 'kc_state_ref.txt')

# секции, которые умеет parse_line - для отчёта о покрытии
KNOWN_SECTIONS = ['sel', 'ps', 'as', 'crown', 'gc', 'camps', 'ports', 'stat',
                  'tow', 'cit', 'kng', 'tool', 'walls', 'farms', 'shops',
                  'pay', 'enm']

# ветки, которых обычно нет в живой игре - добираем синтетикой
SYNTHETIC = [
    'KC|px=1.0,py=2.0|cit:Peasant@12.5:7,Peasant@-4:0|day=1|',
    'KC|px=0.0|cit:Knight@3|day=2|',
    'KC|px=5.0|sel:PayableTree,10@5.5~MyName|day=3|',
    'KC|px=5.0|sel:-|day=3|',
    'KC|px=7.0|pt:1.0,hp=3,purse=7,hc=1|day=4|',
    'KC|px=8.0|zzz:abc|day=5|',
    'KC|px=9.0|crown:-|gc:|camps:|day=6|',
    'KC|px=10.0|enm:Troll@3.5:1|day=7|',
    'KC|px=11.0|enm:Ogre@-2.0:0|day=8|',
    'KC|px=12.0|ports:17:3,88:2:1|day=9|',
    'KC|px=13.0|stat:Archer@2.4:1.5,Worker@0.5|day=10|',
    'KC|px=14.0|tow:5.5:1,Peasant@-3:12,Peasant@9|day=11|',
    'KC|px=15.0|kng:2.0:Knight:Idle,3.5|day=12|',
    'KC|px=16.0|tool:Hammer@1.5,Bow@-2|day=13|',
    'KC|px=17.0|walls:1.5:2:0,9.0:1|day=14|',
    'KC|px=18.0|farms:2.5:True,7.0:False|day=15|',
    'KC|px=19.0|shops:Mason@3.0|day=16|',
    'KC|px=20.0|pay:PayableWall,25@4.0~Wall0~1|day=17|',
    'KC|px=21.0|pay:PayableTree,10@5.5~Tree_3|day=18|',
    'KC|px=0.0,py=0.0|playing=False,next=|day=0|',
    'KC|px=-32.75,py=0.125,st=0.5|day=19|',
]


def collect_samples():
    """Реальные KC| из kcdbg.txt + синтетика на недостающие ветки."""
    import gamepath
    kcdbg = os.path.join(gamepath.game_dir(), 'Kingdom_Data', 'kcdbg.txt')
    real = []
    try:
        size = os.path.getsize(kcdbg)
        with open(kcdbg, 'rb') as f:
            f.seek(max(0, size - 2_000_000))
            for raw in f:
                line = raw.decode('utf-8', 'replace').rstrip('\r\n')
                if line.startswith('KC|'):
                    real.append(line)
                    if len(real) >= 3000:
                        break
    except OSError:
        pass
    seen = set(real)
    extra = [l for l in SYNTHETIC if l not in seen]
    with io.open(SAMPLES, 'w', encoding='utf-8') as f:
        f.write('\n'.join(real + extra) + '\n')
    return real, extra


def load_samples():
    if not os.path.isfile(SAMPLES):
        collect_samples()
    with io.open(SAMPLES, encoding='utf-8') as f:
        return [l for l in f.read().split('\n') if l]


def dump_states():
    import bot3
    out, bad = [], 0
    for i, line in enumerate(load_samples()):
        try:
            st = bot3.parse_line(line)
        except Exception as e:
            bad += 1
            out.append('%04d EXC %r' % (i, e))
            continue
        if st is None:
            out.append('%04d None' % i)
            continue
        fields = ['%s=%r' % (k, v) for k, v in sorted(vars(st).items())]
        out.append('%04d %s' % (i, ' '.join(fields)))
    return out, bad


def coverage():
    """Какие секции/kv реально покрыты образцами."""
    import collections
    sec, kv = collections.Counter(), collections.Counter()
    for line in load_samples():
        for part in line[3:].split('|'):
            m = re.match(r'^([A-Za-z]+):(.*)$', part, re.S)
            if m:
                sec[m.group(1)] += 1
                if m.group(1) == 'KC':
                    for kp in m.group(2).split(','):
                        if '=' in kp:
                            kv[kp.split('=', 1)[0].strip()] += 1
            else:
                sec['<без имени>'] += 1
                for kp in part.split(','):
                    if '=' in kp:
                        kv[kp.split('=', 1)[0].strip()] += 1
    missing = [s for s in KNOWN_SECTIONS if not sec.get(s)]
    print('строк образцов : %d' % len(load_samples()))
    print('покрыто секций : %d из %d' % (len(KNOWN_SECTIONS) - len(missing),
                                         len(KNOWN_SECTIONS)))
    print('   НЕ покрыто  : %s' % (', '.join(missing) if missing else 'нет'))
    print('kv-ключи       : %d' % len(kv))
    return not missing


def main():
    mode = sys.argv[1] if len(sys.argv) > 1 else 'check'

    if mode == 'status':
        return 0 if coverage() else 1

    states, bad = dump_states()

    if mode == 'ref':
        with io.open(REF, 'w', encoding='utf-8') as f:
            f.write('\n'.join(states))
        coverage()
        print('эталон снят: %d состояний (ошибок парсинга: %d)'
              % (len(states), bad))
        return 0

    if not os.path.isfile(REF):
        print('нет эталона. Снимите ДО рефакторинга: py difftest.py ref')
        return 2

    with io.open(REF, encoding='utf-8') as f:
        ref = f.read().split('\n')

    if len(ref) != len(states):
        print('КРАСНЫЙ ПРОГОН: состояний %d -> %d' % (len(ref), len(states)))
        return 1

    diffs, shown = 0, 0
    for i, (a, b) in enumerate(zip(ref, states)):
        if a != b:
            diffs += 1
            if shown < 5:
                print('  строка %d:\n    эталон: %s\n    сейчас: %s'
                      % (i, a[:280], b[:280]))
                shown += 1
    if bad:
        print('ошибок парсинга: %d' % bad)
    if diffs:
        print('КРАСНЫЙ ПРОГОН: расхождений %d из %d' % (diffs, len(ref)))
        return 1
    print('ЗЕЛЁНЫЙ ПРОГОН: %d строк, 0 расхождений, ошибок парсинга %d'
          % (len(states), bad))
    return 0


if __name__ == '__main__':
    sys.exit(main())

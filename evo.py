# -*- coding: utf-8 -*-
"""
Эволюция ботов для Kingdom: Classic.

Запуск:
    py evo.py --baseline          # сделать baseline из текущей сохранёнки (один раз)
    py evo.py --pop 12 --rounds 10 --episode 30

Что делает:
  - берёт базовую сохранёнку (baseline.dat) - состояние свежей партии
  - для каждого поколения: создаёт POP ботов (12 по умолчанию)
  - каждый бот играет EPISODE секунд (начинаем с 30), затем "умирает"
  - отбираем 3 наилучших стратегии, от них мутируем 4 бота каждую (12 всего)
  - боты с наименьшим числом очков отбрасываются
  - время жизни бота увеличивается с каждым поколением
  - повторяем ROUNDS раз
"""
import argparse
import baseline
import ctypes
import gamepath
import json
import kc_common
import os
import random
import shutil
import subprocess
import sys
import time

if getattr(sys, 'frozen', False):
    HERE = getattr(sys, '_MEIPASS', os.path.dirname(sys.executable))
else:
    HERE = os.path.dirname(os.path.abspath(__file__))
# Папка игры задаётся лаунчером через --game-dir (сохраняется в настройках приложения).
GAME_DIR = ''
# Идёт ли обучение: если да - игра обязана быть закрыта даже при исключении.
_TRAINING = False
# Единая точка правды — gamepath.save_file() (раньше путь был захардкожен здесь,
# в restart.py и human_trainer.py с жёсткой версией storage_v26).
SAVE = gamepath.save_file()
# Данные (геномы, история, baseline, логи) - в writable-каталог: в frozen-сборке
# HERE = _MEIPASS, который стирается при пересборке и неписьменен в
# Program Files (аудит S2). В разработке DATA == HERE, поведение прежнее.
DATA = kc_common.data_dir()
SAVES_DIR = os.path.join(DATA, 'saves')
BASELINE = os.path.join(SAVES_DIR, 'baseline.dat')
BEST_GENOME = os.path.join(DATA, 'data', 'best_genome.json')
HISTORY = os.path.join(DATA, 'data', 'evo_history.json')
BOT = os.path.join(HERE, 'bot3.py')       # ресурс, только чтение
PYTHON = sys.executable


def user32():
    return ctypes.windll.user32


def find_window(title='Kingdom'):
    """Единая реализация - kc_common.find_window (проверка процесса)."""
    return kc_common.find_window(title)


def close_game(timeout=10.0):
    """Закрыть игру и УБЕДИТЬСЯ, что она закрылась. True - закрыта.

    Аудит 2026-11-20: раньше здесь был безусловный taskkill и печать
    «Игра закрыта.» БЕЗ проверки - если taskkill не срабатывал, игра
    висела в фоне часами (факт: после обучения игра жила ~9 часов, пока
    пользователь не закрыл лаунчер). Теперь: kill -> ждём исчезновения
    окна -> при необходимости повтор -> честный результат.
    """
    subprocess.run(['taskkill', '/F', '/IM', 'Kingdom.exe'],
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    deadline = time.time() + timeout
    while time.time() < deadline:
        if find_window() is None:
            return True
        time.sleep(0.5)
    # последняя попытка и ещё одна проверка
    subprocess.run(['taskkill', '/F', '/IM', 'Kingdom.exe'],
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    time.sleep(1.0)
    return find_window() is None


def wait_window(timeout=90):
    t0 = time.time()
    while time.time() - t0 < timeout:
        h = find_window()
        if h:
            return h
        time.sleep(1)
    return None


def focus_window(hwnd):
    if not hwnd:
        return False
    fg = user32().GetForegroundWindow()
    cur = ctypes.windll.kernel32.GetCurrentThreadId()
    tid = user32().GetWindowThreadProcessId(fg, None) if fg else 0
    attached = False
    if tid and tid != cur:
        user32().AttachThreadInput(cur, tid, True)
        attached = True
    try:
        user32().ShowWindow(hwnd, 9)
        user32().BringWindowToTop(hwnd)
        user32().SetForegroundWindow(hwnd)
        user32().SetActiveWindow(hwnd)
    finally:
        if attached:
            user32().AttachThreadInput(cur, tid, False)
    return user32().GetForegroundWindow() == hwnd


def hold_s(seconds):
    from hold import send as _send
    _send(0x1F, False)
    time.sleep(seconds)
    _send(0x1F, True)


def tap(sc, hold=0.08):
    from hold import send as _send
    _send(sc, False)
    time.sleep(hold)
    _send(sc, True)
    time.sleep(0.3)


def restart_game():
    """Убить игру, восстановить baseline, запустить игру, дождаться playing=True."""
    if not GAME_DIR:
        raise RuntimeError('не указана папка игры (--game-dir)')
    subprocess.run(['taskkill', '/F', '/IM', 'Kingdom.exe'],
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    time.sleep(2)
    if os.path.exists(BASELINE):
        shutil.copyfile(BASELINE, SAVE)
    subprocess.Popen([os.path.join(GAME_DIR, 'Kingdom.exe')],
                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    hwnd = wait_window(90)
    if not hwnd:
        raise RuntimeError('игра не запустилась')
    time.sleep(8)
    focus_window(hwnd)
    time.sleep(2)
    # ждём пока интро пройдёт и playing станет True (до 90 с).
    # Если игра встала на экран новой игры/интро (подсказка «нажать вниз»,
    # а «вниз» = S) - зажимаем S сами. Достаточно 2-3 с, но зажимаем только
    # после 8 с непрерывного playing=False, чтобы не нажимать во время
    # обычной загрузки (там playing=False бывает пару секунд).
    kcdbg = os.path.join(GAME_DIR, 'Kingdom_Data', 'kcdbg.txt')
    t0 = time.time()
    not_playing_since = None
    last_hold = 0.0
    while time.time() - t0 < 90:
        try:
            size = os.path.getsize(kcdbg)
            with open(kcdbg, 'rb') as f:
                f.seek(max(0, size - 65536))
                data = f.read().decode('utf-8', 'replace')
            last = None
            for line in data.splitlines():
                if line.startswith('KC|'):
                    last = line
            if last and 'playing=True' in last:
                return hwnd
            if last and 'playing=False' in last:
                now = time.time()
                if not_playing_since is None:
                    not_playing_since = now
                if (now - not_playing_since >= 8.0
                        and now - last_hold >= 6.0):
                    focus_window(hwnd)
                    hold_s(3.0)
                    last_hold = time.time()
                    print('  интро: зажал S для скипа', flush=True)
            else:
                not_playing_since = None
        except Exception:
            pass
        time.sleep(1)
    print('  !! playing=True не дождался за 90 с', flush=True)
    return hwnd


def load_genome(path):
    with open(path, 'r', encoding='utf-8') as f:
        return json.load(f)


def save_genome(path, g):
    """Атомарная запись (kc_common): обрыв не оставляет битый best_genome."""
    kc_common.atomic_json_dump(path, g)


_DEFAULT_GENOME_CACHE = None


def default_genome():
    """DEFAULT_GENOME из bot3.py.

    Кэшируется: раньше модуль исполнялся заново при КАЖДОМ клампе мутации
    (importlib + exec 92 КБ ради одного словаря).
    """
    global _DEFAULT_GENOME_CACHE
    if _DEFAULT_GENOME_CACHE is None:
        import importlib.util
        spec = importlib.util.spec_from_file_location('bot3', BOT)
        m = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(m)
        _DEFAULT_GENOME_CACHE = dict(m.DEFAULT_GENOME)
    return dict(_DEFAULT_GENOME_CACHE)


# Диапазоны генома. Мутация не должна выворачивать логику бота: отрицательные
# цели найма (нанимать никогда), отрицательные веса покупок, нулевые дистанции.
# Ранее клампился только 5 ключей, остальные уходили за границы (аудит E7).
GENOME_BOUNDS = {
    # пороги и таймауты - строго положительные
    'danger_dist': (0.1, None),
    'flee_dist': (0.1, None),
    'pay_tol': (0.05, None),
    'pay_giveup': (0.1, None),
    'coin_timeout': (0.1, None),
    'sprint_min_dist': (0.0, None),
    'sprint_min_stamina': (0.0, 1.0),
    'pay_black_time': (0.0, None),
    'coin_black_time': (0.0, None),
    # счётчики и пороги - неотрицательные
    'keep_coins': (0, None),
    'low_gold_threshold': (0, None),
    'tax_min_coins': (0, None),
    'tree_prio': (0, None),
    'target_archers': (0, None),
    'target_workers': (0, None),
    'target_farmers': (0, None),
    'target_knights': (0, None),
}


def clamp_genome(g):
    """Вернуть геном, приведённый к допустимым диапазонам.

    За границей диапазона значение заменяется на дефолт (а не отрезается
    краем): так бот получает осмысленный параметр вместо краевого значения.
    """
    out = dict(g)
    defaults = None
    for k, v in out.items():
        if isinstance(v, bool) or not isinstance(v, (int, float)):
            continue
        if k in GENOME_BOUNDS:
            lo, hi = GENOME_BOUNDS[k]
        elif k.startswith('w_'):
            lo, hi = 0, None          # веса покупок (меньше = важнее)
        else:
            continue
        bad = (v < lo) or (hi is not None and v > hi)
        if bad:
            if defaults is None:
                defaults = default_genome()
            out[k] = defaults.get(k, lo)
    return out


def mutate(g, rate=0.15, strength=0.25):
    out = dict(g)
    for k, v in list(g.items()):
        if not isinstance(v, (int, float)) or isinstance(v, bool):
            continue
        if random.random() < rate:
            delta = random.uniform(-strength, strength)
            new = v * (1 + delta)
            if isinstance(v, int) or (isinstance(v, float) and float(v).is_integer() and abs(v) < 100):
                new = int(round(new))
            out[k] = new
    return clamp_genome(out)


def crossover(a, b):
    out = {}
    for k in a:
        out[k] = a[k] if random.random() < 0.5 else b.get(k, a[k])
    return out


# --- D25: единая формула скоринга --------------------------------------------
# Раньше здесь была ПОЛНАЯ КОПИЯ bot3.fitness (~75 строк), и копии УЖЕ разошлись:
#   * regular_buys в evo вычитал только инструменты -> покупка замка получала
#     'buy' ВТОРОЙ раз поверх buy_castle_base/buy_castle_upgrade;
#   * отличались дефолты: coin_collect 5.0 vs 2.0, buy 10.0 vs 5.0,
#     crown_lost -50 vs -40.
# Итог: очки, которые показывал бот (SC=), не совпадали с оценкой эволюции -
# отбор шёл не по той метрике, которую видел пользователь.
#
# Теперь bot3.fitness - единственная точка правды.

_bot3 = None


def _bot3_module():
    """Ленивый импорт bot3: модуль тяжёлый, нужен только для формулы."""
    global _bot3
    if _bot3 is None:
        import bot3
        _bot3 = bot3
    return _bot3


def score_episode(m):
    """Очки эпизода для отбора. Делегирует в bot3.fitness (решение D25)."""
    return _bot3_module().fitness(m)


def _f12_pressed():
    u = ctypes.windll.user32
    return bool(u.GetAsyncKeyState(0x7B) & 0x01)  # F12 toggle bit


def run_episode(genome, episode, restart, generation=1, speed=3.0, log_file=None):
    gpath = os.path.join(DATA, '_tmp_genome.json')
    rpath = os.path.join(DATA, '_tmp_result.json')
    save_genome(gpath, genome)
    for p in (rpath,):
        try:
            os.remove(p)
        except OSError:
            pass
    if _f12_pressed():
        print('Остановка по F12', flush=True)
        raise SystemExit
    if restart:
        restart_game()
    hwnd = find_window()
    if hwnd:
        focus_window(hwnd)
    time.sleep(2)
    lpath = os.path.join(DATA, '_tmp_live.json')
    try:
        os.remove(lpath)
    except OSError:
        pass
    cmd = [PYTHON, BOT, '--genome', gpath, '--episode', str(episode),
           '--result', rpath, '--live-result', lpath, '--own-focus',
           '--generation', str(generation), '--speed', str(speed)]
    if GAME_DIR:
        cmd += ['--game-dir', GAME_DIR]
    if log_file:
        cmd += ['--log-file', log_file]
    # PYTHONUTF8=1: вывод ботов - UTF-8 в любом случае. Без флага (прямой
    # запуск `py evo.py` мимо лаунчера) боты пишут в локальную кодировку,
    # и кириллица превращается в кракозабры у любого читателя-UTF8 (аудит).
    env = os.environ.copy()
    env['PYTHONUTF8'] = '1'
    if getattr(sys, 'frozen', False):
        env['KC_RUN_MODULE'] = 'bot3'
        # drop the script path; pass args after exe
        child_cmd = [sys.executable] + cmd[1:]
        proc = subprocess.Popen(child_cmd, cwd=HERE, env=env)
    else:
        proc = subprocess.Popen(cmd, cwd=HERE, env=env)
    t0 = time.time()
    pause_time = 0.0
    last_iter = time.time()
    try:
        while proc.poll() is None:
            time.sleep(2)
            now = time.time()
            iter_elapsed = now - last_iter
            # F12 - остановить всё (бот, обучение, игра)
            if _f12_pressed():
                print('    !! F12 - остановка', flush=True)
                proc.kill()
                proc.wait()
                # убить игру
                subprocess.run(['taskkill', '/F', '/IM', 'Kingdom.exe'],
                               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                raise SystemExit
            # проверяем живой результат: смерть короля - завершаем эпизод
            paused = False
            if os.path.exists(lpath):
                try:
                    with open(lpath, 'r', encoding='utf-8') as f:
                        m_live = json.load(f)
                    paused = m_live.get('paused', False)
                    if m_live.get('deaths', 0) > 0:
                        print('    !! смерть короля - завершаем эпизод', flush=True)
                        proc.kill()
                        proc.wait()
                        break
                except Exception:
                    pass
            # если бот на паузе - не считаем это время в таймаут
            if paused:
                pause_time += iter_elapsed
                print('    .. пауза (%.0f сек накоплено)' % pause_time, flush=True)
            last_iter = now
            # таймаут с учётом паузы
            if now - t0 - pause_time > episode + 60:
                proc.kill()
                proc.wait()
                break
    except KeyboardInterrupt:
        proc.kill()
        proc.wait()
        subprocess.run(['taskkill', '/F', '/IM', 'Kingdom.exe'],
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        raise
    except SystemExit:
        raise
    # читаем результат (финальный или живой)
    for p in (rpath, lpath):
        if os.path.exists(p):
            try:
                with open(p, 'r', encoding='utf-8') as f:
                    m = json.load(f)
                m['score'] = score_episode(m)
                # Для отбора: пауза искажает эпизод (игра идёт, бот нет).
                m['pause_time'] = pause_time
                m['wall_seconds'] = time.time() - t0
                return m
            except Exception:
                pass
    return None


def mutation_strength(rnd, args):
    """Сила мутации убывает по раундам: mut_strength * mut_decay^(раунд-1), не ниже mut_min."""
    return max(args.mut_min, args.mut_strength * (args.mut_decay ** (rnd - 1)))


# ---------------------------------------------------------------- CEM / Pareto
def _is_num(v):
    return isinstance(v, (int, float)) and not isinstance(v, bool)


def _genome_bounds(key, value):
    """Границы ключа генома (те же, что в clamp_genome)."""
    if key in GENOME_BOUNDS:
        return GENOME_BOUNDS[key]
    if key.startswith('w_'):
        return 0, None
    return None, None


def _sigma_for(key, value, sigma0):
    lo, hi = _genome_bounds(key, value)
    if lo is not None and hi is not None:
        span = float(hi - lo)
    else:
        span = max(abs(float(value)) * 2.0, 1.0)
    sigma = max(0.02, span * max(0.02, sigma0))
    if isinstance(value, int):
        sigma = max(0.5, sigma)          # целые не должны «замерзать»
    return sigma


class CEMOptimizer(object):
    """Cross-Entropy Method для непрерывного генома.

    Вместо mutation/crossover: выборка из N(mean, sigma^2), отбор элиты,
    пересчёт mean/sigma по элите. Для целых генов — округление, для
    ограниченных — clamp_genome. Это устойчивее GA на ~31 параметре.
    """

    def __init__(self, mean, sigma0=0.2):
        self.mean = {k: v for k, v in mean.items() if _is_num(v)}
        self.sigma = {k: _sigma_for(k, v, sigma0)
                      for k, v in self.mean.items()}

    def sample(self, n):
        out = []
        for _ in range(max(0, n)):
            g = dict(self.mean)
            for k, v in self.mean.items():
                x = v + random.gauss(0.0, self.sigma[k])
                if isinstance(v, int):
                    x = int(round(x))
                g[k] = x
            out.append(clamp_genome(g))
        return out

    def update(self, ranked, elite_frac=0.5):
        """ranked: список dict с ключом 'genome' (лучшие первыми)."""
        if not ranked:
            return
        n = max(2, int(round(len(ranked) * max(0.05, elite_frac))))
        n = min(n, len(ranked))
        elite = ranked[:n]
        weights = [float(n - i) for i in range(n)]
        wsum = sum(weights)
        for k, old in self.mean.items():
            vals = []
            for r in elite:
                try:
                    vals.append(float(r['genome'][k]))
                except (KeyError, TypeError, ValueError):
                    vals.append(float(old))
            mean = sum(w * v for w, v in zip(weights, vals)) / wsum
            var = sum(w * (v - mean) ** 2
                      for w, v in zip(weights, vals)) / wsum
            sigma = max(_sigma_for(k, mean, 0.02), var ** 0.5)
            if isinstance(old, int):
                mean = int(round(mean))
            self.mean[k] = mean
            self.sigma[k] = sigma
        self.mean = clamp_genome(self.mean)


def economy_value(m):
    """Скаляр «качества экономики» для Pareto-отбора (не для очков)."""
    return (m.get('workers', 0) * 4.0
            + m.get('archers', 0) * 3.0
            + m.get('farmers', 0) * 3.0
            + m.get('knights', 0) * 3.0
            + m.get('tool_buys', 0) * 2.0
            + m.get('tree_chops_with_workers', 0) * 1.5
            + m.get('buildings_with_workers', 0) * 2.0
            + m.get('castle_upgrade_buys_with_workers', 0) * 4.0)


def _dominates(a, b):
    """Pareto-доминирование по (выживание, очки, экономика)."""
    keys = ('survival', 'score', 'economy')
    return (all(a.get(k, 0) >= b.get(k, 0) for k in keys)
            and any(a.get(k, 0) > b.get(k, 0) for k in keys))


def pareto_sort(items):
    """Pareto: сначала ВСЕ выжившие, затем погибшие; внутри — по очкам/экономике.

    Чистый Pareto допускал бота с высокой оценкой, но смертью, в первом
    фронте. Для обучения это плохо: смерть должна быть жёстким минусом.
    """
    groups = [
        [x for x in items if x.get('survival', 0) >= 1],
        [x for x in items if x.get('survival', 0) < 1],
    ]
    ranked = []
    front_no = 0
    for group in groups:
        rest = list(group)
        while rest:
            front = [x for x in rest
                     if not any(_dominates(y, x) for y in rest if y is not x)]
            if not front:                # защита от вырожденного сравнения
                front = [rest[0]]
            for x in front:
                x['pareto_rank'] = front_no
            front.sort(key=lambda x: x.get('score', 0), reverse=True)
            ranked.extend(front)
            used = {id(x) for x in front}
            rest = [x for x in rest if id(x) not in used]
            front_no += 1
    return ranked


def aggregate_evals(genome, evals):
    """Свести несколько повторных эпизодов одного генома в одну запись."""
    if not evals:
        return {'score': -1e9, 'score_std': 0.0, 'evals': 0,
                'survival': 0, 'economy': 0.0, 'genome': genome,
                'deaths': 0, 'crown_lost': 0}
    out = {}
    keys = set()
    for m in evals:
        keys.update(m.keys())
    for k in keys:
        if k in ('genome', 'paused'):
            continue
        vals = [m.get(k) for m in evals if _is_num(m.get(k))]
        if vals:
            out[k] = sum(vals) / len(vals)
    scores = [m.get('score', -1e9) for m in evals]
    out['score'] = sum(scores) / len(scores)
    mean = out['score']
    out['score_std'] = (sum((s - mean) ** 2 for s in scores) / len(scores)) ** 0.5
    out['evals'] = len(evals)
    out['deaths'] = sum(m.get('deaths', 0) for m in evals)
    out['crown_lost'] = sum(m.get('crown_lost', 0) for m in evals)
    out['survival'] = 0 if (out['deaths'] or out['crown_lost']) else 1
    out['economy'] = economy_value(out)
    out['genome'] = genome
    return out



def main():
    kc_common.ensure_utf8_stdout()   # кириллица в пайпе = UTF-8, не cp1251
    ap = argparse.ArgumentParser()
    # D19: дефолты как в C#-версии (50/15/20/5) - она давала более полную
    # выборку за раунд; лаунчер передаёт значения явно, поэтому расхождение
    # проявлялось только при прямом запуске.
    ap.add_argument('--pop', type=int, default=50)
    ap.add_argument('--rounds', type=int, default=15)
    ap.add_argument('--episode', type=float, default=20.0)
    # D8: дефолт лаунчера (0.1), а не 0.25 из старого evo.py
    ap.add_argument('--top-percent', type=float, default=0.1,
                    help='доля элиты для отбора (по умолчанию 0.1 = 10%%)')
    ap.add_argument('--elite-window', type=int, default=3,
                    help='сколько последних эпизодов элиты усредняется (сглаживание шума)')
    ap.add_argument('--crossover-rate', type=float, default=0.5,
                    help='доля потомков от скрещивания двух родителей (остальные - мутации)')
    ap.add_argument('--mut-strength', type=float, default=0.15,
                    help='стартовая сила мутации (первый раунд)')
    ap.add_argument('--mut-decay', type=float, default=0.9,
                    help='множитель силы мутации за каждый раунд (0.9 = -10%% за раунд)')
    ap.add_argument('--mut-min', type=float, default=0.03,
                    help='минимальная сила мутации')
    # Новый оптимизатор (2026-11-22): CEM устойчивее GA на ~31 параметре.
    ap.add_argument('--optimizer', choices=('cem', 'ga'), default='cem',
                    help='алгоритм поиска: cem (по умолчанию) или ga')
    ap.add_argument('--eval-repeats', type=int, default=1,
                    help='сколько эпизодов оценивать на геном (2 = меньше шума, x2 время)')
    ap.add_argument('--pareto', dest='pareto', action='store_true', default=True,
                    help='Pareto-отбор по выживанию/очкам/экономике (по умолчанию)')
    ap.add_argument('--no-pareto', dest='pareto', action='store_false',
                    help='сортировать только по очкам')
    ap.add_argument('--cem-sigma', type=float, default=0.2,
                    help='стартовая относительная ширина поиска CEM')
    ap.add_argument('--elite-frac', type=float, default=0.5,
                    help='доля элиты для CEM/Pareto (0.5 = половина популяции)')
    # D7: потолок 3x - на 4x и выше тайминги игры ломаются
    ap.add_argument('--speed', type=float, default=3.0,
                    help='скорость игры, максимум 3x (4x и выше ломает игру)')
    ap.add_argument('--episode-increment', type=float, default=5.0,
                    help='на сколько секунд увеличивается жизнь бота с каждым поколением')
    ap.add_argument('--restart', action='store_true', default=True)
    ap.add_argument('--no-restart', dest='restart', action='store_false')
    ap.add_argument('--baseline', action='store_true',
                    help='сделать baseline из текущей сохранёнки и выйти')
    ap.add_argument('--resetAll', action='store_true',
                    help='сбросить всё обучение: удалить геном, историю, baseline, логи')
    ap.add_argument('--human-genomes', type=str, default=None,
                    help='пути к геномам человека через запятую (human_genome_1.json,...)')
    ap.add_argument('--game-dir', type=str, default=None,
                    help='папка с установленной игрой Kingdom: Classic')
    args = ap.parse_args()

    # D7: жёсткий потолок скорости. Раньше --speed вообще не валидировался -
    # можно было передать хоть 50, и игра ломалась по таймингам.
    if not (1.0 <= args.speed <= 3.0):
        raise SystemExit('!! --speed должен быть от 1 до 3 (на 4x+ игра '
                         'ломается), получено: %s' % args.speed)
    if not (0.0 < args.top_percent <= 1.0):
        raise SystemExit('!! --top-percent должен быть в (0, 1], получено: %s'
                         % args.top_percent)
    if args.eval_repeats < 1:
        args.eval_repeats = 1
    args.eval_repeats = min(5, args.eval_repeats)
    if not (0.0 < args.elite_frac <= 1.0):
        args.elite_frac = 0.5

    global GAME_DIR
    GAME_DIR = (args.game_dir or '').strip().strip('"') \
        or (os.environ.get('KCBOT_GAME_DIR') or '').strip() \
        or gamepath.game_dir()

    # Загрузка геномов ручного обучения (только если лаунчер их передал).
    human_genomes = []
    if args.human_genomes:
        for path in args.human_genomes.split(","):
            path = path.strip()
            if not path:
                continue
            if not os.path.isabs(path):
                path = os.path.join(DATA, path)
            if os.path.exists(path):
                try:
                    human_genomes.append(load_genome(path))
                    print("  загружен human геном:", path)
                except Exception as e:
                    # один битый файл не должен ронять всё обучение
                    print("  !! human геном %s не читается (%r) - пропускаю"
                          % (path, e))
    if human_genomes:
        print("  human геномов: %d" % len(human_genomes))

    os.makedirs(SAVES_DIR, exist_ok=True)
    # папка для логов
    log_session = time.strftime('%Y-%m-%d_%H-%M-%S')
    LOGS_DIR = os.path.join(DATA, 'logs', log_session)
    os.makedirs(LOGS_DIR, exist_ok=True)

    if args.resetAll:
        print('=== сброс всего обучения ===')
        for f in [BEST_GENOME, HISTORY, BASELINE]:
            try:
                os.remove(f)
                print('  удалён:', f)
            except FileNotFoundError:
                print('  не найден:', f)
        # записи и геномы ручного обучения
        import glob as _glob
        for f in sorted(_glob.glob(os.path.join(DATA, 'data', 'human_*.json'))):
            try:
                os.remove(f)
                print('  удалён:', f)
            except OSError:
                pass
        # очистка папки логов
        logs_dir = os.path.join(DATA, 'logs')
        if os.path.exists(logs_dir):
            shutil.rmtree(logs_dir)
            print('  очищена папка:', logs_dir)
        os.makedirs(logs_dir, exist_ok=True)
        # автоматически делаем baseline из текущей сохранёнки
        # (если сейва нет - baseline.py сам запустит игру и создаст его, D32)
        if baseline.ensure(GAME_DIR, log=print, force=True):
            print('  baseline пересоздан')
        else:
            print('  !! не удалось создать baseline')
        print('=== сброс завершён ===')
        return

    if args.baseline:
        # D32: раньше здесь была только копия УЖЕ существующего сейва, а при
        # его отсутствии - ошибка «запусти сначала новую игру». Теперь
        # baseline.py сам запускает игру и доводит её до автосейва.
        ok = baseline.ensure(GAME_DIR, log=print)
        if not ok:
            print('!! не удалось создать baseline')
        return

    if not baseline.exists():
        print('baseline отсутствует - создаю автоматически (D32)...')
        if not baseline.ensure(GAME_DIR, log=print):
            print('!! не удалось создать baseline')
            return

    # С этого момента мы обязаны закрыть игру даже при исключении/Ctrl+C.
    # Раньше main() не был обёрнут в try/finally: RuntimeError или сбой Popen
    # роняли обучение и оставляли Kingdom.exe висеть.
    global _TRAINING
    _TRAINING = True

    best = None
    if os.path.exists(BEST_GENOME):
        try:
            best = load_genome(BEST_GENOME)
        except Exception as e:
            # битый JSON не должен убивать всю сессию - откатываемся к дефолту
            print('!! %s не читается (%r) - использую геном по умолчанию'
                  % (os.path.basename(BEST_GENOME), e))
            best = None
    if best is None:
        best = default_genome()
        save_genome(BEST_GENOME, best)
        print('стартовый геном:', BEST_GENOME)

    history = []
    if os.path.exists(HISTORY):
        try:
            with open(HISTORY, 'r', encoding='utf-8') as f:
                history = json.load(f)
        except Exception:
            history = []

    print('=== эволюция: pop=%d rounds=%d episode=%.0f restart=%s '
          'optimizer=%s evals=%d pareto=%s ===' %
          (args.pop, args.rounds, args.episode, args.restart,
           args.optimizer, args.eval_repeats, args.pareto))

    elite = dict(best)
    elite_hist = []
    prev_ranked = []
    cem = CEMOptimizer(elite, args.cem_sigma) \
        if args.optimizer == 'cem' else None
    for rnd in range(1, args.rounds + 1):
        if _f12_pressed():
            print('Остановка по F12')
            subprocess.run(['taskkill', '/F', '/IM', 'Kingdom.exe'],
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            return
        # формула: episode + episode_increment * поколение
        episode_len = args.episode + args.episode_increment * rnd
        s_now = mutation_strength(rnd, args)
        print('\n--- раунд %d / %d  (время жизни: %.0f с, сила мутации: %.3f, оптимизатор: %s) ---' %
              (rnd, args.rounds, episode_len, s_now, args.optimizer))
        if cem is not None:
            pop = [dict(elite)] + cem.sample(max(0, args.pop - 1))
        else:
            # старая GA-ветка оставлена для A/B-сравнения
            pop = [dict(elite)]
            if rnd == 1 or not prev_ranked:
                while len(pop) < args.pop:
                    pop.append(mutate(elite, rate=0.2, strength=s_now))
            else:
                tops = prev_ranked[:max(1, int(len(prev_ranked) * args.top_percent))]
                n_cross = int((args.pop - 1) * args.crossover_rate)
                for _ in range(n_cross):
                    pa, pb = random.choice(tops), random.choice(tops)
                    child = crossover(pa['genome'], pb['genome'])
                    pop.append(mutate(child, rate=0.2, strength=s_now))
                while len(pop) < args.pop:
                    parent = random.choice(tops)
                    pop.append(mutate(parent['genome'], rate=0.2, strength=s_now))
        # подмешивание human геномов (вместо худших): заменяем последние N ботов
        if human_genomes:
            n_h = min(len(human_genomes), max(1, args.pop - 1))
            keep = max(1, args.pop - n_h)
            if len(pop) > keep:
                pop = pop[:keep]
            for hg in list(human_genomes)[:n_h]:
                g = dict(hg) if rnd == 1 else mutate(hg, rate=0.1, strength=0.1)
                pop.append(g)
            while len(pop) < args.pop:
                pop.append(mutate(elite, rate=0.2, strength=s_now))
            pop = pop[:args.pop]

        results = []
        for i, g in enumerate(pop, 1):
            if _f12_pressed():
                print('Остановка по F12')
                close_game(timeout=5.0)
                return
            evals = []
            for rep in range(args.eval_repeats):
                retries = 0
                m = None
                while retries < 3:
                    print('  [%d/%d] эпизод... (оценка %d/%d, попытка %d)' %
                          (i, len(pop), rep + 1, args.eval_repeats,
                           retries + 1), flush=True)
                    suffix = ('' if args.eval_repeats == 1
                              else '_оценка%d' % (rep + 1))
                    log_path = os.path.join(
                        LOGS_DIR, '%d поколения_%d бота%s.log' %
                        (rnd, i, suffix))
                    m = run_episode(g, episode_len, args.restart,
                                    generation=rnd, speed=args.speed,
                                    log_file=log_path)
                    if m is None:
                        print('    !! нет результата')
                        break
                    if (m.get('seconds', 0) < 5
                            or (m.get('distance', 0) == 0
                                and m.get('coins_in', 0) == 0)):
                        retries += 1
                        print('    !! бот не контролировал игру '
                              '(seconds=%.1f, dist=%.1f) - перезапуск' %
                              (m.get('seconds', 0), m.get('distance', 0)),
                              flush=True)
                        if retries < 3:
                            continue
                    # Пауза F8 делает эпизод несравнимым; пауза из-за потери
                    # фокуса — легитимна (метрики стоят, таймер продлён).
                    manual_pause = max(
                        0.0, m.get('pause_time', 0) - m.get('focus_pause_time', 0))
                    if manual_pause > 2.0:
                        retries += 1
                        print('    !! эпизод с паузой %.0f с - не засчитываем'
                              % manual_pause, flush=True)
                        if retries < 3:
                            continue
                    break
                if m is None:
                    m = {'score': -1e9, 'coins_in': 0, 'coins_spent': 0,
                         'buys': 0, 'buildings': 0,
                         'buildings_with_workers': 0, 'citizens': 0,
                         'crown_lost': 0, 'deaths': 0, 'seconds': 0,
                         'genome': g}
                m['genome'] = g
                m['eval_index'] = rep + 1
                evals.append(m)
                print('    очки=%.1f coins_in=%.0f spent=%.0f buys=%d '
                      'build=%d cit=%d workers=%.0f crown_lost=%d' %
                      (m.get('score', -1e9), m.get('coins_in', 0),
                       m.get('coins_spent', 0), m.get('buys', 0),
                       m.get('buildings', 0), m.get('citizens', 0),
                       m.get('workers', 0), m.get('crown_lost', 0)),
                      flush=True)
            agg = aggregate_evals(g, evals)
            results.append(agg)
            print('    итог: очки=%.1f ±%.1f (оценок %d) экономика=%.1f '
                  'выживание=%d' %
                  (agg.get('score', -1e9), agg.get('score_std', 0),
                   agg.get('evals', 0), agg.get('economy', 0),
                   agg.get('survival', 0)), flush=True)

        # --- отбор: Pareto (выживание/очки/экономика) или чистые очки ---
        for r in results:
            r['rank_score'] = r.get('score', -1e9)
        elite_res = results[0] if results else None
        if elite_res and elite_res.get('score', -1e9) > -1e8:
            elite_hist.append(elite_res['score'])
            elite_hist = elite_hist[-args.elite_window:]
            elite_res['rank_score'] = sum(elite_hist) / len(elite_hist)
        if args.pareto:
            ranked = pareto_sort(results)
        else:
            ranked = sorted(results,
                            key=lambda r: r.get('rank_score', -1e9),
                            reverse=True)
        if ranked:
            if ranked[0] is not elite_res:
                elite = dict(ranked[0]['genome'])
                elite_hist = ([ranked[0]['score']]
                              if ranked[0].get('score', -1e9) > -1e8 else [])
            else:
                elite = dict(ranked[0]['genome'])
        prev_ranked = ranked
        best = elite
        save_genome(BEST_GENOME, best)
        if cem is not None:
            cem.update(ranked, args.elite_frac)

        for idx, r in enumerate(results, 1):
            history.append({
                'score': r.get('score', -1e9),
                'score_std': r.get('score_std', 0),
                'evals': r.get('evals', 0),
                'survival': r.get('survival', 0),
                'economy': r.get('economy', 0),
                'pareto_rank': r.get('pareto_rank', 0),
                'coins_in': r.get('coins_in', 0),
                'coins_spent': r.get('coins_spent', 0),
                'buys': r.get('buys', 0),
                'buildings': r.get('buildings', 0),
                'buildings_completed': r.get('buildings_completed', 0),
                'buildings_with_workers': r.get('buildings_with_workers', 0),
                'buildings_without_workers': r.get('buildings_without_workers', 0),
                'workers': r.get('workers', 0),
                'archers': r.get('archers', 0),
                'farmers': r.get('farmers', 0),
                'knights': r.get('knights', 0),
                'citizens': r.get('citizens', 0),
                'crown_lost': r.get('crown_lost', 0),
                'crown_picked': r.get('crown_picked', 0),
                'deaths': r.get('deaths', 0),
                'recruited_beggars': r.get('recruited_beggars', 0),
                'tool_buys': r.get('tool_buys', 0),
                'castle_base_buys': r.get('castle_base_buys', 0),
                'castle_base_buys_without_workers': r.get('castle_base_buys_without_workers', 0),
                'castle_upgrade_buys_with_workers': r.get('castle_upgrade_buys_with_workers', 0),
                'trees_chopped': r.get('trees_chopped', 0),
                'trees_marked': r.get('trees_marked', 0),
                'tree_chops_with_workers': r.get('tree_chops_with_workers', 0),
                'unpicked_coins': r.get('unpicked_coins', 0),
                'inactivity_penalty': r.get('inactivity_penalty', 0),
                'pause_time': r.get('pause_time', 0),
                'focus_pause_time': r.get('focus_pause_time', 0),
                'wall_seconds': r.get('wall_seconds', 0),
                'failed_coin': r.get('failed_coin', 0),
                'failed_buy': r.get('failed_buy', 0),
                'failed_recruit': r.get('failed_recruit', 0),
                'final_purse': r.get('final_purse', 0),
                'distance': r.get('distance', 0),
                'seconds': r.get('seconds', 0),
                'round': rnd, 'episode': episode_len,
                'bot': idx, 'run': log_session,
                'genome': r.get('genome', {})})
        # атомарно: обрыв в момент записи не должен затирать всю историю
        kc_common.atomic_json_dump(HISTORY, history)
        print('  лучший очки=%.1f (топ-3: %s)' %
              (ranked[0].get('score', -1e9) if ranked else -1e9,
               ', '.join('%.1f' % r.get('score', -1e9)
                         for r in ranked[:3])))
        print('  лучший геном сохранён:', BEST_GENOME)

        # время жизни увеличивается по формуле: episode + episode_increment * поколение
        # (уже пересчитывается в начале каждого раунда)

    print('\n=== готово. best_genome.json обновлён ===')

    # закрыть игру после завершения обучения (с проверкой - см. close_game)
    print('Закрытие игры...', flush=True)
    if close_game():
        print('Игра закрыта.', flush=True)
    else:
        print('!! игра НЕ закрылась - закройте её вручную', flush=True)

    # запустить лаунчер после обучения (если включено в настройках)
    try:
        import json as _json
        _appdata = os.environ.get('APPDATA', os.path.expanduser('~'))
        _cfg_path = os.path.join(_appdata, 'KCbot', 'launcher_config.json')
        if not os.path.exists(_cfg_path):
            _cfg_path = os.path.join(DATA, 'data', '.launcher_config.json')
        if os.path.exists(_cfg_path):
            with open(_cfg_path, 'r', encoding='utf-8') as _f:
                _cfg = _json.load(_f)
            if _cfg.get('launch_after', False):
                print('Запуск лаунчера...', flush=True)
                # DEVNULL: лаунчер не должен наследовать наш stdout - иначе
                # пайп остаётся открытым ПОСЛЕ выхода evo и любая обёртка
                # (pipe/Tee) виснет часами (аудит2026-11-20: команда обучения
                # ждала закрытия пайпа9 часов). CREATE_NO_WINDOW: в dev
                # python.exe не создаёт лишнее окно консоли.
                subprocess.Popen(
                    [sys.executable, os.path.join(HERE, 'launcher.py')],
                    cwd=HERE,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    creationflags=(0x08000000 if os.name == 'nt' else 0))
    except Exception:
        pass


if __name__ == '__main__':
    try:
        main()
    except KeyboardInterrupt:
        print('\nОстановлено пользователем (Ctrl+C)')
    except SystemExit:
        raise
    except Exception as e:
        print('!! обучение прервано: %r' % (e,))
        raise
    finally:
        # игра не должна остаться висеть после падения/прерывания обучения.
        # (при штатном завершении main() уже закрыл игру - повторный taskkill
        #  безвреден)
        if _TRAINING:
            close_game(timeout=5.0)
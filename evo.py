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
import ctypes
import ctypes.wintypes as wt
import gamepath
import json
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
# Единая точка правды — gamepath.save_file() (раньше путь был захардкожен здесь,
# в restart.py и human_trainer.py с жёсткой версией storage_v26).
SAVE = gamepath.save_file()
SAVES_DIR = os.path.join(HERE, 'saves')
BASELINE = os.path.join(SAVES_DIR, 'baseline.dat')
BEST_GENOME = os.path.join(HERE, 'data', 'best_genome.json')
HISTORY = os.path.join(HERE, 'data', 'evo_history.json')
BOT = os.path.join(HERE, 'bot3.py')
PYTHON = sys.executable


def user32():
    return ctypes.windll.user32


def find_window(title='Kingdom'):
    res = []

    def cb(hwnd, lp):
        # сначала проверяем процесс: окно должно принадлежать Kingdom.exe
        pid = wt.DWORD()
        user32().GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        is_kingdom = False
        try:
            hproc = ctypes.windll.kernel32.OpenProcess(0x0410, False, pid.value)
            if hproc:
                modbuf = ctypes.create_unicode_buffer(260)
                ctypes.windll.psapi.GetModuleBaseNameW(hproc, None, modbuf, 260)
                ctypes.windll.kernel32.CloseHandle(hproc)
                if 'kingdom' in modbuf.value.lower():
                    is_kingdom = True
        except Exception:
            pass
        if not is_kingdom:
            return True
        # процесс Kingdom — проверяем заголовок
        n = user32().GetWindowTextLengthW(hwnd)
        if n == 0:
            return True
        buf = ctypes.create_unicode_buffer(n + 1)
        user32().GetWindowTextW(hwnd, buf, n + 1)
        title_val = buf.value.strip()
        if title_val.lower() == title.lower() or title.lower() in title_val.lower():
            visible = user32().IsWindowVisible(hwnd)
            area = 0
            if visible:
                r = wt.RECT()
                user32().GetWindowRect(hwnd, ctypes.byref(r))
                area = (r.right - r.left) * (r.bottom - r.top)
            res.append((hwnd, area, visible))
        return True

    EnumProc = ctypes.WINFUNCTYPE(ctypes.c_bool, wt.HWND, wt.LPARAM)
    user32().EnumWindows(EnumProc(cb), 0)
    if res:
        res.sort(key=lambda x: (x[2], x[1]), reverse=True)
        return res[0][0]
    # fallback: по подстроке
    res2 = []
    def cb2(hwnd, lp):
        if not user32().IsWindowVisible(hwnd):
            return True
        n = user32().GetWindowTextLengthW(hwnd)
        if n == 0:
            return True
        buf = ctypes.create_unicode_buffer(n + 1)
        user32().GetWindowTextW(hwnd, buf, n + 1)
        if title.lower() in buf.value.lower():
            res2.append(hwnd)
        return True
    user32().EnumWindows(EnumProc(cb2), 0)
    return res2[0] if res2 else None


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
    # ждём пока интро пройдёт и playing станет True (до 90 с)
    kcdbg = os.path.join(GAME_DIR, 'Kingdom_Data', 'kcdbg.txt')
    t0 = time.time()
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
        except Exception:
            pass
        time.sleep(1)
    return hwnd


def load_genome(path):
    with open(path, 'r', encoding='utf-8') as f:
        return json.load(f)


def save_genome(path, g):
    with open(path, 'w', encoding='utf-8') as f:
        json.dump(g, f, ensure_ascii=False, indent=2)


def default_genome():
    import importlib.util
    spec = importlib.util.spec_from_file_location('bot3', BOT)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return dict(m.DEFAULT_GENOME)


def mutate(g, rate=0.15, strength=0.25):
    out = dict(g)
    for k, v in list(g.items()):
        if not isinstance(v, (int, float)):
            continue
        if random.random() < rate:
            delta = random.uniform(-strength, strength)
            new = v * (1 + delta)
            if isinstance(v, int) or (isinstance(v, float) and float(v).is_integer() and abs(v) < 100):
                new = int(round(new))
            out[k] = new
    for k in ('danger_dist', 'flee_dist', 'pay_tol', 'coin_timeout', 'pay_giveup'):
        if k in out and out[k] <= 0:
            out[k] = default_genome()[k]
    return out


def crossover(a, b):
    out = {}
    for k in a:
        out[k] = a[k] if random.random() < 0.5 else b.get(k, a[k])
    return out


BOUNTIES_FILE = os.path.join(HERE, 'bounties.txt')


def load_bounties(path=None):
    path = path or BOUNTIES_FILE
    result = {}
    try:
        with open(path, 'r', encoding='utf-8') as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith('#'):
                    continue
                if '=' in line:
                    key, _, val = line.partition('=')
                    key = key.strip()
                    val = val.strip()
                    try:
                        result[key] = float(val)
                    except ValueError:
                        pass
    except Exception:
        pass
    return result


BOUNTIES = load_bounties()


def score_episode(m):
    b = BOUNTIES
    s = 0.0
    s += b.get('survival', 0.5) * m.get('seconds', 0)
    s += b.get('coin_collect', 5.0) * m.get('coins_in', 0)
    s += b.get('coin_spend', 4.0) * m.get('coins_spent', 0)
    # покупки: базовые + инструменты по отдельности
    tool_buys = m.get('tool_buys', 0)
    regular_buys = m.get('buys', 0) - tool_buys
    s += b.get('buy', 10.0) * regular_buys
    s += b.get('buy_hammer', 15.0) * m.get('hammer_buys', 0)
    s += b.get('buy_bow', 15.0) * m.get('bow_buys', 0)
    s += b.get('buy_scythe', 15.0) * m.get('scythe_buys', 0)
    s += b.get('buy_castle_base', 60.0) * m.get('castle_base_buys', 0)
    s += b.get('buy_castle_upgrade', 40.0) * m.get('castle_upgrade_buys', 0)
    s += b.get('building_start', 2.0) * m.get('buildings_started', 0)
    s += b.get('building_complete', 10.0) * m.get('buildings_completed', 0)
    s += b.get('recruit_beggar', 10.0) * m.get('recruited_beggars', 0)
    s += b.get('first_archer_bonus', 15.0) * m.get('first_archer', 0)
    s += b.get('first_worker_bonus', 15.0) * m.get('first_worker', 0)
    s += b.get('movement_walk', 0.5) * m.get('walk_distance', 0)
    s += b.get('movement_run', 0.7) * m.get('run_distance', 0)
    s += b.get('horse_exhaustion', -3.0) * m.get('horse_exhaustion', 0)
    s += b.get('horse_rest', 2.0) * m.get('horse_rest_time', 0) / 5.0
    s += b.get('crown_lost', -50.0) * m.get('crown_lost', 0)
    s += b.get('crown_pickup', 5.0) * m.get('crown_picked', 0)
    s += b.get('death', -1000.0) * m.get('deaths', 0)
    # штраф за бездействие с прогрессией
    periods = int(m.get('inactivity_penalty', 0))
    progression = b.get('inactivity_progression', 1.1)
    idle_total = 0.0
    for i in range(periods):
        idle_total += progression ** i
    day = max(1, int(m.get('day', 1)))
    if day >= 5:
        day_factor = 0.0
    else:
        day_factor = 1.0 + b.get('inactivity_day_bonus', 0.1) * (day - 1)
    s += b.get('idle', -1.0) * idle_total * day_factor
    # отметка дерева (небольшой бонус)
    s += b.get('tree_mark', 2.0) * m.get('trees_marked', 0)
    # срубленное дерево (большой бонус)
    s += b.get('tree_chop', 5.0) * m.get('trees_chopped', 0)
    # штраф за попытку рубки без рабочих
    s += b.get('tree_chop_no_workers', -0.5) * m.get('tree_chops_without_workers', 0)
    # штраф за неудачные попытки (коэффициент из bounties.txt)
    ratio = b.get('failure_penalty_ratio', 0.25)
    s -= b.get('coin_collect', 5.0) * ratio * m.get('failed_coin', 0)
    s -= b.get('buy', 10.0) * ratio * m.get('failed_buy', 0)
    s -= b.get('recruit_beggar', 10.0) * ratio * m.get('failed_recruit', 0)
    s -= b.get('tree_chop', 5.0) * ratio * m.get('failed_tree', 0)
    # потеря монет от врага (только реальные потери, не найм)
    s += b.get('coin_lost_enemy', -10.0) * m.get('coins_lost', 0)
    # не подобранные монеты (градиент)
    n_unpicked = m.get('unpicked_coins', 0)
    if n_unpicked > 0:
        max_pen = b.get('unpicked_coin_max', 5.0)
        min_pen = b.get('unpicked_coin_min', 1.0)
        threshold = b.get('unpicked_coin_threshold', 30.0)
        if n_unpicked >= threshold:
            per_coin = min_pen
        else:
            per_coin = max_pen - (max_pen - min_pen) * (n_unpicked - 1) / (threshold - 1)
        per_coin = max(min_pen, per_coin)
        s += b.get('unpicked_coin', -1.0) * per_coin * n_unpicked
    gen = m.get('generation', 1)
    threshold = 10.0 * gen
    if m.get('coins_in', 0) == 0 and m.get('buys', 0) == 0 and m.get('buildings', 0) == 0:
        if m.get('seconds', 0) > threshold:
            s += b.get('no_interaction', -10.0) * (m.get('seconds', 0) / threshold)
    if m.get('final_purse', 0) == 0 and m.get('coins_spent', 0) > 0:
        if m.get('coins_in', 0) < m.get('coins_spent', 0):
            s += b.get('full_spend_penalty', -5.0)
    return s



def _f12_pressed():
    u = ctypes.windll.user32
    return bool(u.GetAsyncKeyState(0x7B) & 0x01)  # F12 toggle bit


def run_episode(genome, episode, restart, generation=1, speed=4.0, log_file=None):
    gpath = os.path.join(HERE, '_tmp_genome.json')
    rpath = os.path.join(HERE, '_tmp_result.json')
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
    lpath = os.path.join(HERE, '_tmp_live.json')
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
    if getattr(sys, 'frozen', False):
        env = os.environ.copy()
        env['KC_RUN_MODULE'] = 'bot3'
        # drop the script path; pass args after exe
        child_cmd = [sys.executable] + cmd[1:]
        proc = subprocess.Popen(child_cmd, cwd=HERE, env=env)
    else:
        proc = subprocess.Popen(cmd, cwd=HERE)
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
                return m
            except Exception:
                pass
    return None


def mutation_strength(rnd, args):
    """Сила мутации убывает по раундам: mut_strength * mut_decay^(раунд-1), не ниже mut_min."""
    return max(args.mut_min, args.mut_strength * (args.mut_decay ** (rnd - 1)))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--pop', type=int, default=12)
    ap.add_argument('--rounds', type=int, default=10)
    ap.add_argument('--episode', type=float, default=30.0)
    ap.add_argument('--top-percent', type=float, default=0.25,
                    help='процент лучших ботов для отбора (по умолчанию 0.25 = 25%%)')
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
    ap.add_argument('--speed', type=float, default=4.0,
                    help='скорость игры (1x, 2x, 4x, 8x)')
    ap.add_argument('--episode-increment', type=float, default=10.0,
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
                path = os.path.join(HERE, path)
            if os.path.exists(path):
                human_genomes.append(load_genome(path))
                print("  загружен human геном:", path)
    if human_genomes:
        print("  human геномов: %d" % len(human_genomes))

    os.makedirs(SAVES_DIR, exist_ok=True)
    # папка для логов
    log_session = time.strftime('%Y-%m-%d_%H-%M-%S')
    LOGS_DIR = os.path.join(HERE, 'logs', log_session)
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
        for f in sorted(_glob.glob(os.path.join(HERE, 'data', 'human_*.json'))):
            try:
                os.remove(f)
                print('  удалён:', f)
            except OSError:
                pass
        # очистка папки логов
        logs_dir = os.path.join(HERE, 'logs')
        if os.path.exists(logs_dir):
            shutil.rmtree(logs_dir)
            print('  очищена папка:', logs_dir)
        os.makedirs(logs_dir, exist_ok=True)
        # автоматически делаем baseline из текущей сохранёнки
        if os.path.exists(SAVE):
            shutil.copyfile(SAVE, BASELINE)
            print('  baseline создан из:', SAVE)
        else:
            print('  !! сохранёнка не найдена:', SAVE)
        print('=== сброс завершён ===')
        return

    if args.baseline:
        if not os.path.exists(SAVE):
            print('!! сохранёнка не найдена:', SAVE)
            return
        shutil.copyfile(SAVE, BASELINE)
        print('baseline сохранён:', BASELINE)
        return

    if not os.path.exists(BASELINE):
        print('!! нет baseline. Запусти сначала новую игру, потом:')
        print('   py evo.py --baseline')
        return

    best = None
    if os.path.exists(BEST_GENOME):
        best = load_genome(BEST_GENOME)
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

    print('=== эволюция: pop=%d rounds=%d episode=%.0f restart=%s ===' %
          (args.pop, args.rounds, args.episode, args.restart))

    elite = dict(best)
    elite_hist = []
    prev_ranked = []
    for rnd in range(1, args.rounds + 1):
        if _f12_pressed():
            print('Остановка по F12')
            subprocess.run(['taskkill', '/F', '/IM', 'Kingdom.exe'],
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            return
        # формула: episode + episode_increment * поколение
        episode_len = args.episode + args.episode_increment * rnd
        s_now = mutation_strength(rnd, args)
        print('\n--- раунд %d / %d  (время жизни: %.0f с, сила мутации: %.3f) ---' %
              (rnd, args.rounds, episode_len, s_now))
        # элитизм: лучший геном идёт в популяцию без изменений (и переигрывается снова)
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
            n_h = len(human_genomes)
            # обрезаем популяцию, чтобы освободить место для human
            if len(pop) + n_h > args.pop:
                pop = pop[:args.pop - n_h]
            for hg in human_genomes:
                g = dict(hg) if rnd == 1 else mutate(hg, rate=0.1, strength=0.1)
                pop.append(g)
            # гарантируем размер популяции
            while len(pop) < args.pop:
                pop.append(mutate(elite, rate=0.2, strength=s_now))
            pop = pop[:args.pop]

        results = []
        for i, g in enumerate(pop, 1):
            if _f12_pressed():
                print('Остановка по F12')
                subprocess.run(['taskkill', '/F', '/IM', 'Kingdom.exe'],
                               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                return
            # попытки запуска бота (до 3 раз, если не контролирует игру)
            retries = 0
            m = None
            while retries < 3:
                print('  [%d/%d] эпизод... (попытка %d)' % (i, len(pop), retries + 1), flush=True)
                log_path = os.path.join(LOGS_DIR, '%d поколения_%d бота.log' % (rnd, i))
                m = run_episode(g, episode_len, args.restart, generation=rnd,
                                speed=args.speed, log_file=log_path)
                if m is None:
                    print('    !! нет результата')
                    m = {'score': -1e9, 'coins_in': 0, 'coins_spent': 0, 'buys': 0,
                         'buildings': 0, 'buildings_with_workers': 0, 'citizens': 0, 'crown_lost': 0,
                         'seconds': 0, 'genome': g}
                    break
                # проверяем: контролировал ли бот игру?
                if m.get('seconds', 0) < 5 or (m.get('distance', 0) == 0 and m.get('coins_in', 0) == 0):
                    retries += 1
                    print('    !! бот не контролировал игру (seconds=%.1f, dist=%.1f) - перезапуск' %
                          (m.get('seconds', 0), m.get('distance', 0)), flush=True)
                    if retries < 3:
                        continue
                m['genome'] = g
                break
            if m is None:
                m = {'score': -1e9, 'coins_in': 0, 'coins_spent': 0, 'buys': 0,
                     'buildings': 0, 'buildings_with_workers': 0, 'citizens': 0, 'crown_lost': 0,
                     'seconds': 0, 'genome': g}
            else:
                if 'genome' not in m:
                    m['genome'] = g
            results.append(m)
            print('    очки=%.1f coins_in=%.0f spent=%.0f buys=%d build=%d cit=%d crown_lost=%d' %
                  (m['score'], m['coins_in'], m['coins_spent'], m['buys'],
                   m['buildings'], m['citizens'], m['crown_lost']), flush=True)

        # ранжирование: оценка элиты = среднее по последним эпизодам (сглаживание шума)
        for idx, r in enumerate(results, 1):
            r['rank_score'] = r['score'] = r['score']
        elite_res = results[0]
        if elite_res['score'] > -1e8:
            elite_hist.append(elite_res['score'])
            elite_hist = elite_hist[-args.elite_window:]
        if elite_hist:
            elite_res['rank_score'] = sum(elite_hist) / len(elite_hist)
        ranked = sorted(results, key=lambda r: r['rank_score'], reverse=True)
        if ranked[0] is not elite_res:
            # элита сменилась: новый лидер, история его оценок начинается заново
            elite = dict(ranked[0]['genome'])
            elite_hist = [ranked[0]['score']] if ranked[0]['score'] > -1e8 else []
        prev_ranked = ranked
        best = elite
        save_genome(BEST_GENOME, best)
        for idx, r in enumerate(results, 1):
            history.append({'score': r['score'], 'coins_in': r['coins_in'],
                            'coins_spent': r['coins_spent'], 'buys': r['buys'],
                            'buildings': r['buildings'],
                            'buildings_completed': r.get('buildings_completed', 0),
                            'citizens': r['citizens'],
                            'crown_lost': r['crown_lost'],
                            'crown_picked': r.get('crown_picked', 0),
                            'deaths': r.get('deaths', 0),
                            'recruited_beggars': r.get('recruited_beggars', 0),
                            'seconds': r['seconds'],
                            'round': rnd, 'episode': episode_len,
                            'bot': idx, 'run': log_session,
                            'genome': r['genome']})
        with open(HISTORY, 'w', encoding='utf-8') as f:
            json.dump(history, f, ensure_ascii=False, indent=2)
        print('  лучший очки=%.1f (топ-3: %s)' %
              (results[0]['score'],
               ', '.join('%.1f' % r['score'] for r in results[:3])))
        print('  лучший геном сохранён:', BEST_GENOME)

        # время жизни увеличивается по формуле: episode + episode_increment * поколение
        # (уже пересчитывается в начале каждого раунда)

    print('\n=== готово. best_genome.json обновлён ===')

    # закрыть игру после завершения обучения
    print('Закрытие игры...', flush=True)
    subprocess.run(['taskkill', '/F', '/IM', 'Kingdom.exe'],
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    print('Игра закрыта.', flush=True)

    # запустить лаунчер после обучения (если включено в настройках)
    try:
        import json as _json
        _appdata = os.environ.get('APPDATA', os.path.expanduser('~'))
        _cfg_path = os.path.join(_appdata, 'KCbot', 'launcher_config.json')
        if not os.path.exists(_cfg_path):
            _cfg_path = os.path.join(HERE, 'data', '.launcher_config.json')
        if os.path.exists(_cfg_path):
            with open(_cfg_path, 'r', encoding='utf-8') as _f:
                _cfg = _json.load(_f)
            if _cfg.get('launch_after', False):
                print('Запуск лаунчера...', flush=True)
                subprocess.Popen([sys.executable, os.path.join(HERE, 'launcher.py')],
                                 cwd=HERE)
    except Exception:
        pass


if __name__ == '__main__':
    main()
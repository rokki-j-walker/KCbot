#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
Бот для Kingdom: Classic (Steam 368230).

Управляет ТОЛЬКО королём:
    A / D  - идти влево / вправо
    Shift  - галоп (ускорение)
    S      - контекстное действие: оплата (когда под королём выбран Payable)
             или бросок монеты (если Payable не выбран - бот так не делает)

Состояние игры читается из <Game>_Data/kcdbg.txt - его пишет логгер KCLog5.dll,
вклеенный в Assembly-CSharp.dll (патч через Mono.Cecil).

    py bot3.py --dry      # только решения, без нажатий клавиш
    py bot3.py --once     # одно решение и выход
    py bot3.py            # реальное управление
"""
import ctypes
import ctypes.wintypes as wt
import json
import os
import random
import re
import sys
import time

GAME_DIR = r'C:\Program Files (x86)\Steam\steamapps\common\Kingdom'
KCDBG = os.path.join(GAME_DIR, 'Kingdom_Data', 'kcdbg.txt')
SPEED_FILE = os.path.join(GAME_DIR, 'Kingdom_Data', 'speed.txt')
HERE = os.path.dirname(os.path.abspath(__file__))
LOGFILE = os.path.join(HERE, 'bot_log.txt')

# ---------------------------------------------------------------- Win32 ввод
user32 = ctypes.windll.user32

INPUT_KEYBOARD = 1
KEYEVENTF_SCANCODE = 0x0008
KEYEVENTF_KEYUP = 0x0002

SC_A = 0x1E
SC_D = 0x20
SC_S = 0x1F
SC_ESC = 0x01
SC_LSHIFT = 0x2A


class KEYBDINPUT(ctypes.Structure):
    _fields_ = [('wVk', wt.WORD), ('wScan', wt.WORD), ('dwFlags', wt.DWORD),
                ('time', wt.DWORD), ('dwExtraInfo', ctypes.POINTER(wt.ULONG))]


class _INPUTUNION(ctypes.Union):
    _fields_ = [('ki', KEYBDINPUT), ('pad', ctypes.c_byte * 32)]


class INPUT(ctypes.Structure):
    _anonymous_ = ('u',)
    _fields_ = [('type', wt.DWORD), ('u', _INPUTUNION)]


def _send(sc, up=False):
    flags = KEYEVENTF_SCANCODE | (KEYEVENTF_KEYUP if up else 0)
    inp = INPUT(type=INPUT_KEYBOARD)
    inp.ki = KEYBDINPUT(0, sc, flags, 0, None)
    user32.SendInput(1, ctypes.byref(inp), ctypes.sizeof(INPUT))


class Input(object):
    """Клавиатура: удержание/отпускание + окно игры."""

    def __init__(self):
        self.held = set()
        self.hwnd = None

    def find_window(self, title='Kingdom'):
        res = []
        EnumWindowsProc = ctypes.WINFUNCTYPE(ctypes.c_bool, wt.HWND, wt.LPARAM)

        def cb(hwnd, lparam):
            # сначала проверяем процесс: окно должно принадлежать Kingdom.exe
            pid = wt.DWORD()
            user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
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
            n = user32.GetWindowTextLengthW(hwnd)
            if n == 0:
                return True
            buf = ctypes.create_unicode_buffer(n + 1)
            user32.GetWindowTextW(hwnd, buf, n + 1)
            title_val = buf.value.strip()
            area = 0
            visible = user32.IsWindowVisible(hwnd)
            if visible:
                r = wt.RECT()
                user32.GetWindowRect(hwnd, ctypes.byref(r))
                area = (r.right - r.left) * (r.bottom - r.top)
            res.append((hwnd, title_val, area, visible))
            return True

        user32.EnumWindows(EnumWindowsProc(cb), 0)
        if res:
            # сортируем: сначала видимые, потом по площади (больше = главное окно)
            res.sort(key=lambda x: (x[3], x[2]), reverse=True)
            self.hwnd = res[0][0]
            return self.hwnd
        # fallback: ищем по подстроке (старый метод)
        res2 = []
        def cb2(hwnd, lparam):
            if not user32.IsWindowVisible(hwnd):
                return True
            n = user32.GetWindowTextLengthW(hwnd)
            if n == 0:
                return True
            buf = ctypes.create_unicode_buffer(n + 1)
            user32.GetWindowTextW(hwnd, buf, n + 1)
            if title.lower() in buf.value.lower():
                res2.append((hwnd, buf.value))
            return True
        user32.EnumWindows(EnumWindowsProc(cb2), 0)
        if res2:
            self.hwnd = res2[0][0]
            return self.hwnd
        return None

    def is_focused(self):
        return bool(self.hwnd) and user32.GetForegroundWindow() == self.hwnd

    def force_focus(self):
        if not self.hwnd:
            return False
        if self.is_focused():
            return True
        fg = user32.GetForegroundWindow()
        cur = ctypes.windll.kernel32.GetCurrentThreadId()
        tid = user32.GetWindowThreadProcessId(fg, None) if fg else 0
        attached = False
        if tid and tid != cur:
            user32.AttachThreadInput(cur, tid, True)
            attached = True
        try:
            user32.ShowWindow(self.hwnd, 9)          # SW_RESTORE
            user32.BringWindowToTop(self.hwnd)
            user32.SetForegroundWindow(self.hwnd)
            user32.SetActiveWindow(self.hwnd)
        finally:
            if attached:
                user32.AttachThreadInput(cur, tid, False)
        return self.is_focused()

    def down(self, sc):
        if sc not in self.held:
            _send(sc, False)
            self.held.add(sc)

    def up(self, sc):
        if sc in self.held:
            _send(sc, True)
            self.held.discard(sc)

    def release(self):
        for sc in list(self.held):
            self.up(sc)


# --------------------------------------------------------------- разбор лога
NUM = r'-?\d+(?:\.\d+)?'


class Payable(object):
    __slots__ = ('type', 'price', 'x', 'name', 'enabled')

    def __init__(self, type_, price, x, name='', enabled=True):
        self.type = type_
        self.price = price
        self.x = x
        self.name = name or ''
        self.enabled = bool(enabled)

    @property
    def base(self):
        """Имя GameObject без '(Clone)' и хвостов: 'Castle5(Clone)' -> 'Castle5'."""
        n = self.name.split('(')[0].strip()
        return n

    def __repr__(self):
        return 'Payable(%s,%d,%.1f,%s)' % (self.type, self.price, self.x, self.name)


class State(object):
    def __init__(self):
        self.raw = ''
        self.px = 0.0
        self.py = 0.0
        self.stamina = 1.0
        self.purse = 0
        self.hc = 1                # hasCrown
        self.crown_x = None
        self.sel = None            # выбранный Payable
        self.pay_state = 'None'    # None/Holding/Transaction/Completed/Cancelling
        self.action = 'Stand'       # Stand/Walk/Run/Eat/Rear
        self.pay_timer = 0.0
        self.day = 0
        self.time = 0.0
        self.playing = False
        self.next = ''
        self.daytime = False
        self.safe = False
        self.cf = 0.0
        self.arc = self.wrk = self.frm = self.knt = 0
        self.wall = self.shop = self.farm = self.portal = self.camp = self.work = 0
        self.attacking = False
        self.enemies = 0
        self.troll = self.ogre = self.squid = 0
        self.ground_coins = []
        self.camps = []
        self.portals = []
        self.statues = []
        self.slots = []             # (x, free) - слоты башен
        self.citizens = []          # (tag, x, coins) A/W/F/K/P/B
        self.knights = []
        self.tools = []
        self.walls = []
        self.farms = []
        self.shops = []
        self.payables = []
        self.enemy_list = []

    def cit(self, tag):
        return [(x, c) for (t, x, c) in self.citizens if t == tag]

    def statue(self, deity):
        for d, x, ch in self.statues:
            if d == deity:
                return (x, ch)
        return None

    def nearest_enemy(self, x):
        best = None
        bd = 1e9
        for (n, ex, atk) in self.enemy_list:
            d = abs(ex - x)
            if d < bd:
                bd = d
                best = (n, ex, atk, d)
        return best

    def pay_by_name(self, needle):
        for p in self.payables:
            if needle.lower() in p.base.lower():
                return p
        return None

    def shop_price(self, item):
        sx = None
        for it, x in self.shops:
            if it == item:
                sx = x
        if sx is None:
            return None
        best = None
        for p in self.payables:
            if p.type == 'PayableShop' and abs(p.x - sx) < 1.5:
                if best is None or p.price < best.price:
                    best = p
        return best


def _f(s):
    try:
        return float(s)
    except ValueError:
        return 0.0


def parse_line(line):
    st = State()
    st.raw = line
    if not line.startswith('KC|'):
        return None
    for part in line[3:].split('|'):
        m = re.match(r'^([A-Za-z]+):(.*)$', part, re.S)
        name = val = None
        if m:
            name, val = m.group(1), m.group(2)

        if name == 'sel':
            if val and val != '-':
                mm = re.match(r'([A-Za-z0-9_]+),(-?\d+)@(-?[\d.]+)(?:~([^,]*))?', val)
                if mm:
                    st.sel = Payable(mm.group(1), int(mm.group(2)),
                                     float(mm.group(3)), (mm.group(4) or '').strip())
        elif name == 'ps':
            st.pay_state = val.strip()
        elif name == 'as':
            st.action = val.strip()
        elif name == 'crown':
            if val and val != '-':
                st.crown_x = _f(val.split(',')[0])
        elif name == 'gc':
            st.ground_coins = [_f(v) for v in val.split(',') if v.strip()]
        elif name == 'camps':
            st.camps = [_f(v) for v in val.split(',') if v.strip()]
        elif name == 'ports':
            for p in val.split(','):
                if ':' in p:
                    a = p.split(':')
                    st.portals.append((_f(a[0]), int(_f(a[1])),
                                       int(_f(a[2])) if len(a) > 2 else 0))
        elif name == 'stat':
            for p in val.split(','):
                if '@' in p:
                    a, _, b = p.partition('@')
                    parts = b.split(':')
                    st.statues.append((a.strip(), _f(parts[0]),
                                       _f(parts[1]) if len(parts) > 1 else 0.0))
        elif name == 'tow':
            # слоты башен (x:free) + граждане (TAG@x:coins / TAG@x)
            for p in val.split(','):
                p = p.strip()
                if not p:
                    continue
                if '@' in p:
                    tag, _, rest = p.partition('@')
                    tag = tag.strip()
                    if ':' in rest:
                        xx, _, cc = rest.partition(':')
                        st.citizens.append((tag, _f(xx), int(_f(cc))))
                    else:
                        st.citizens.append((tag, _f(rest), 0))
                elif ':' in p:
                    a = p.split(':')
                    st.slots.append((_f(a[0]), int(_f(a[1]))))
        elif name == 'cit':
            for p in val.split(','):
                if '@' in p:
                    a, _, b = p.partition('@')
                    parts = b.split(':')
                    st.citizens.append((a.strip(), _f(parts[0]),
                                        int(_f(parts[1])) if len(parts) > 1 else 0))
        elif name == 'kng':
            for p in val.split(','):
                if ':' in p:
                    a = p.split(':')
                    st.knights.append((_f(a[0]), a[1] if len(a) > 1 else '?',
                                        a[2] if len(a) > 2 else '?'))
        elif name == 'tool':
            for p in val.split(','):
                if '@' in p:
                    a, _, b = p.partition('@')
                    st.tools.append((a.strip(), _f(b)))
        elif name == 'walls':
            for p in val.split(','):
                if ':' in p:
                    a = p.split(':')
                    st.walls.append((_f(a[0]), int(_f(a[1])),
                                     int(_f(a[2])) if len(a) > 2 else -1))
        elif name == 'farms':
            for p in val.split(','):
                if ':' in p:
                    a = p.split(':')
                    st.farms.append((_f(a[0]), a[1].strip() == 'True'))
        elif name == 'shops':
            for p in val.split(','):
                if '@' in p:
                    a, _, b = p.partition('@')
                    st.shops.append((a.strip(), _f(b)))
        elif name == 'pay':
            for m in re.finditer(r'([A-Za-z0-9_]+),(-?\d+)@(-?[\d.]+)(?:~([^,]*))?', val):
                raw_name = (m.group(4) or '').strip()
                enabled = True
                if '~' in raw_name:
                    parts = raw_name.rsplit('~', 1)
                    if parts[-1] in ('0', '1'):
                        enabled = (parts[-1] == '1')
                        raw_name = parts[0].strip()
                st.payables.append(Payable(m.group(1), int(m.group(2)),
                                            float(m.group(3)), raw_name, enabled))
        elif name == 'enm':
            for p in val.split(','):
                if '@' in p:
                    a, _, b = p.partition('@')
                    parts = b.split(':')
                    st.enemy_list.append((a.strip(), _f(parts[0]),
                                          parts[1] == '1' if len(parts) > 1 else False))
        elif name is None or name in ('KC',):
            for kv in part.split(','):
                if '=' not in kv:
                    continue
                k, _, v = kv.partition('=')
                k = k.strip()
                v = v.strip()
                if k == 'px':
                    st.px = _f(v)
                elif k == 'py':
                    st.py = _f(v)
                elif k == 'st':
                    st.stamina = _f(v)
                elif k == 'purse':
                    st.purse = int(_f(v))
                elif k == 'hc':
                    st.hc = int(_f(v))
                elif k == 'day':
                    st.day = int(_f(v))
                elif k == 'time':
                    st.time = _f(v)
                elif k == 'playing':
                    st.playing = (v == 'True')
                elif k == 'next':
                    st.next = v
                elif k == 'dayT':
                    st.daytime = (v == 'True')
                elif k == 'safe':
                    st.safe = (v == 'True')
                elif k == 'cf':
                    st.cf = _f(v)
                elif k == 'arc':
                    st.arc = int(_f(v))
                elif k == 'wrk':
                    st.wrk = int(_f(v))
                elif k == 'frm':
                    st.frm = int(_f(v))
                elif k == 'knt':
                    st.knt = int(_f(v))
                elif k == 'wall':
                    st.wall = int(_f(v))
                elif k == 'shop':
                    st.shop = int(_f(v))
                elif k == 'farm':
                    st.farm = int(_f(v))
                elif k == 'portal':
                    st.portal = int(_f(v))
                elif k == 'camp':
                    st.camp = int(_f(v))
                elif k == 'work':
                    st.work = int(_f(v))
                elif k == 'atk':
                    st.attacking = (v == 'True')
                elif k == 'en':
                    st.enemies = int(_f(v))
                elif k == 'troll':
                    st.troll = int(_f(v))
                elif k == 'ogre':
                    st.ogre = int(_f(v))
                elif k == 'squid':
                    st.squid = int(_f(v))
        else:
            # неизвестная именованная секция - разбираем как key=value
            for kv in part.split(','):
                if '=' not in kv:
                    continue
                k, _, v = kv.partition('=')
                k = k.strip()
                if k == 'hp':
                    pass
                elif k == 'purse':
                    st.purse = int(_f(v))
                elif k == 'hc':
                    st.hc = int(_f(v))
    return st


def read_state(path=KCDBG):
    """Последняя строка KC| из лога."""
    try:
        size = os.path.getsize(path)
        with open(path, 'rb') as f:
            f.seek(max(0, size - 262144))
            data = f.read().decode('utf-8', 'replace')
    except OSError:
        return None
    last = None
    for line in data.splitlines():
        if line.startswith('KC|'):
            last = line
    return parse_line(last) if last else None


# ------------------------------------------------------------------- конфиг
CFG = dict(
    target_archers=6,
    target_workers=4,
    target_farmers=6,
    target_knights=2,
    danger_dist=11.0,       # враг ближе - убегаем
    flee_dist=22.0,
    keep_coins=1,           # 1 = оставлять 1 монету в запасе
    low_gold_threshold=5,   # если монет меньше этого - фармить деревья
    tax_min_coins=2,        # у кого брать налог (монет > этого)
    tax_wait=2.2,           # сколько стоять у налогоплательщика
    pay_tol=0.12,           # насколько близко встать к Payable
    pay_giveup=3.0,         # сколько секунд искать точку оплаты, потом забить
    pay_black_time=90.0,    # на сколько забыть неоплачиваемый объект
    coin_timeout=5.0,       # сколько ждать монету, которую не подобрали
    coin_black_time=40.0,
    tree_prio=90,           # деревья - самый дешёвый доход
    sprint_min_dist=8.0,
    sprint_min_stamina=0.30,
    idle_x=None,            # None = у костра
    # веса покупок (меньше = важнее) - по умолчанию из DEFAULT_GENOME
    w_mason=12, w_castle=14, w_workshop=16, w_wall3=18,
    w_wall2=19, w_wall1=20, w_wall0=25, w_tower=28,
    w_farmhouse=32, w_statue=34, w_border=36, w_steed=38,
    w_merchant=40, w_tool_scythe=12, w_tool_bow=13, w_tool_hammer=11,
)

# куда пробовать встать относительно объекта, если он не выбирается
SCAN = [0.0, 0.5, -0.5, 1.0, -1.0, 0.25, -0.25, 1.6, -1.6]

# ------------------------------------------------------------------ геном
# геном = набор параметров стратегии. Эволюция (evo.py) мутирует эти числа
# и отбирает варианты, которые набрали больше очков.
DEFAULT_GENOME = {
    # пороги поведения
    'keep_coins': 1,
    'low_gold_threshold': 5,
    'tax_min_coins': 2,
    'danger_dist': 11.0,
    'flee_dist': 22.0,
    'pay_tol': 0.12,
    'pay_giveup': 7.0,
    'coin_timeout': 5.0,
    'sprint_min_dist': 8.0,
    'sprint_min_stamina': 0.30,
    'tree_prio': 90,
    # сколько хотим граждан
    'target_archers': 6,
    'target_workers': 4,
    'target_farmers': 6,
    'target_knights': 2,
    # веса покупок (меньше = важнее)
    'w_mason': 12,
    'w_castle': 14,
    'w_workshop': 16,
    'w_wall3': 18,
    'w_wall2': 19,
    'w_wall1': 20,
    'w_wall0': 25,
    'w_tower': 28,
    'w_farmhouse': 32,
    'w_statue': 34,
    'w_border': 36,
    'w_steed': 38,
    'w_merchant': 40,
    'w_tool_scythe': 12,
    'w_tool_bow': 13,
    'w_tool_hammer': 11,
}

INT_GENOME = ('keep_coins', 'tax_min_coins', 'target_archers', 'target_workers',
              'target_farmers', 'target_knights', 'tree_prio')


def load_genome(path=None):
    """Читает геном (JSON) и вливает его в настройки бота."""
    g = dict(DEFAULT_GENOME)
    path = path or os.path.join(HERE, 'genome.json')
    try:
        with open(path, 'r', encoding='utf-8') as f:
            data = json.load(f)
        if isinstance(data, dict):
            for k, v in data.items():
                if k in g:
                    g[k] = v
    except Exception:
        pass
    for k, v in g.items():
        if k.startswith('w_'):
            CFG[k] = float(v)
        elif k in INT_GENOME:
            CFG[k] = int(float(v))
        elif k in CFG:
            CFG[k] = float(v)
    return g


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


def fitness(m):
    """Очки стратегии за эпизод. Значения из bounties.txt."""
    b = BOUNTIES
    s = 0.0
    s += b.get('coin_collect', 2.0) * m.get('coins_in', 0)
    s += b.get('coin_spend', 4.0) * m.get('coins_spent', 0)
    # базовые покупки (без инструментов и замка)
    regular_buys = m.get('buys', 0) - m.get('tool_buys', 0) - m.get('castle_base_buys', 0) - m.get('castle_upgrade_buys', 0)
    s += b.get('buy', 5.0) * regular_buys
    # инструменты
    s += b.get('buy_hammer', 15.0) * m.get('hammer_buys', 0)
    s += b.get('buy_bow', 15.0) * m.get('bow_buys', 0)
    s += b.get('buy_scythe', 15.0) * m.get('scythe_buys', 0)
    # замок: первая база и апгрейды
    s += b.get('buy_castle_base', 60.0) * m.get('castle_base_buys', 0)
    s += b.get('buy_castle_upgrade', 40.0) * m.get('castle_upgrade_buys', 0)
    s += b.get('building_start', 2.0) * m.get('buildings_started', 0)
    s += b.get('building_complete', 10.0) * m.get('buildings_completed', 0)
    s += b.get('recruit_beggar', 10.0) * m.get('recruited_beggars', 0)
    # бонус за первого лучника и первого рабочего
    s += b.get('first_archer_bonus', 15.0) * m.get('first_archer', 0)
    s += b.get('first_worker_bonus', 15.0) * m.get('first_worker', 0)
    s += b.get('movement_walk', 0.5) * m.get('walk_distance', 0)
    s += b.get('movement_run', 0.7) * m.get('run_distance', 0)
    s += b.get('horse_exhaustion', -3.0) * m.get('horse_exhaustion', 0)
    # бонус за отдых лошади
    s += b.get('horse_rest', 2.0) * m.get('horse_rest_time', 0) / 5.0
    s += b.get('crown_lost', -40.0) * m.get('crown_lost', 0)
    s += b.get('crown_pickup', 5.0) * m.get('crown_picked', 0)
    s += b.get('death', -1000.0) * m.get('deaths', 0)
    # штраф за бездействие с прогрессией: каждый период +X% больше
    periods = int(m.get('inactivity_penalty', 0))
    progression = b.get('inactivity_progression', 1.1)
    idle_total = 0.0
    for i in range(periods):
        idle_total += progression ** i
    # с каждым днём штраф растёт на inactivity_day_bonus, с 5-го дня отключается
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
    s -= b.get('buy', 5.0) * ratio * m.get('failed_buy', 0)
    s -= b.get('recruit_beggar', 10.0) * ratio * m.get('failed_recruit', 0)
    s -= b.get('tree_chop', 5.0) * ratio * m.get('failed_tree', 0)
    # потеря монет от врага
    s += b.get('coin_lost_enemy', -10.0) * m.get('coins_lost', 0)
    # не подобранные монеты (градиент: 1 монета = -5, 30+ = -1 за каждую)
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
    s += b.get('survival', 0.5) * m.get('seconds', 0)
    return s



class BotLog(object):
    def __init__(self, path=None):
        if path is None:
            path = LOGFILE
        self.path = path
        # создать директорию если не существует
        d = os.path.dirname(path)
        if d and not os.path.exists(d):
            os.makedirs(d, exist_ok=True)
        self.f = open(path, 'a', encoding='utf-8')

    def __call__(self, *a):
        msg = ' '.join(str(x) for x in a)
        try:
            self.f.write('[%s] %s\n' % (time.strftime('%H:%M:%S'), msg))
            self.f.flush()
        except Exception:
            pass
        print(msg, flush=True)


# ---------------------------------------------------------------------- бот
class Bot(object):
    def __init__(self, dry=False, inp=None, log=None, own_focus=False,
                 skip_tutorial=False, generation=1, speed=1.0):
        self.dry = dry
        self.inp = inp or Input()
        self.log = log or BotLog()
        self.own_focus = own_focus
        self.skip_tutorial = skip_tutorial
        self.generation = generation
        self.speed = max(0.1, speed)
        self.task = None
        self.last_recruit_drop = 0.0
        self.episode_end = None   # время окончания эпизода (для отображения оставшегося)
        # масштабируем временные константы под скорость симуляции
        self.coin_timeout = CFG['coin_timeout'] / self.speed
        self.pay_giveup = CFG['pay_giveup'] / self.speed
        self.tax_wait = CFG['tax_wait'] / self.speed
        self.pay_timeout_base = 0.5 / self.speed
        self.scan_interval = 0.45 / self.speed
        self.recruit_check = 5.0 / self.speed
        self.esc_cooldown = 3.0 / self.speed
        self.skip_cooldown = 30.0 / self.speed
        self.loop_sleep = 0.2 / self.speed
        self.ts = {}
        self.last_focus = 0.0
        self.pay_target = None
        self.pay_phase = None
        self.pay_started = 0.0
        self.pay_purse0 = 0
        self.pay_wait_sel = 0.0
        self.pay_since = 0.0          # когда начали искать точку оплаты
        self.scan_x = None            # текущая точка оплаты
        self.scan_i = 0
        self.scan_t = 0.0
        self.pay_black = {}           # неоплачиваемые объекты -> до какого времени
        self.nudge = 0.0
        self.coin_target = None
        self.coin_since = 0.0
        self.coin_purse = 0
        self.coin_black = {}
        self.tax_until = 0.0
        self.tax_target = None
        self.stat = {}
        self.skip_until = 0.0          # до какого времени держим S (скип обучения)
        self.last_skip = 0.0
        self.fit = dict(purse0=None, last_purse=0, coins_in=0.0, coins_spent=0.0,
                        buys=0, buildings=0, citizens=0, crown_lost=0,
                        tool_buys=0, hammer_buys=0, bow_buys=0, scythe_buys=0,
                        castle_base_buys=0, castle_upgrade_buys=0,
                        castle_upgrade_buys_with_workers=0,
                        castle_upgrade_buys_without_workers=0,
                        buildings_with_workers=0, buildings_without_workers=0,
                        prev_buildings=None, prev_trees=0,
                        buildings_started=0, buildings_completed=0,
                        max_buildings=0, min_trees=9999,
                        trees_marked=0, trees_chopped=0,
                        first_archer=0, first_worker=0,
                        recruited_beggars=0,
                        distance=0.0, walk_distance=0.0, run_distance=0.0, last_px=None,
                        horse_exhaustion=0, horse_rest_time=0.0, last_track_time=None,
                        tree_chops_with_workers=0, tree_chops_without_workers=0,
                        failed_coin=0, failed_buy=0, failed_recruit=0, failed_tree=0,
                        unpicked_coins=0, coins_lost=0,
                        last_interaction=time.time(), inactivity_penalty=0.0,
                        start=time.time(), base=None)

    # ------------------------------------------------------------- утилиты
    def now(self):
        return time.time()

    def say(self, *a):
        self.log(*a)

    def note(self, key, val):
        if self.stat.get(key) != val:
            self.stat[key] = val
            score = getattr(self, '_last_score', None)
            # оставшееся время (если есть таймер) или прошедшее
            if self.episode_end is not None:
                remaining = max(0, self.episode_end - time.time())
            else:
                remaining = time.time() - self.fit.get('start', time.time()) if hasattr(self, 'fit') else 0
            if key == 'task':
                # формат: SC=ОЧКИ T="время" ts="задача" -> координаты
                parts = str(val).split(' -> ', 1)
                task_desc = parts[0]
                rest = ' -> ' + parts[1] if len(parts) > 1 else ''
                if score is not None:
                    self.say('SC=%.1f T="%.1f" ts="%s"%s' % (score, remaining, task_desc, rest))
                else:
                    self.say('* %s = %s' % (key, val))
            else:
                if score is not None:
                    self.say('SC=%.1f T="%.1f" * %s = %s' % (score, remaining, key, val))
                else:
                    self.say('* %s = %s' % (key, val))

    # ------------------------------------------------------------- метрики
    def track(self, st):
        """Считаем метрики эпизода - по ним эволюция оценивает стратегию."""
        f = self.fit
        if f['base'] is None:
            f['base'] = dict(purse=st.purse, wall=st.wall, shop=st.shop,
                             farm=st.farm, portal=st.portal, arc=st.arc,
                             wrk=st.wrk, frm=st.frm, knt=st.knt, hc=st.hc,
                             p0=len(st.cit('P')))
            f['last_purse'] = st.purse
            f['last_px'] = st.px
            # стартовые здания и деревья не считаем как построенные/срубленные
            f['max_buildings'] = st.wall + st.shop + st.farm
            f['min_trees'] = len([p for p in st.payables if p.type == 'PayableTree'])
            return
        f['day'] = st.day
        d = st.purse - f['last_purse']
        if d > 0:
            f['coins_in'] += d
            f['last_interaction'] = self.now()   # монета собрана = успех
        elif d < 0:
            f['coins_spent'] += -d
            # если не покупка — это потеря монеты (враг/переполнение)
            # монета, брошенная для найма бомжа, и оплата покупки - не потеря
            recruit_window = self.now() < getattr(self, '_recruit_drop_until', 0.0)
            if self.pay_phase != 'hold' and not recruit_window:
                f['coins_lost'] = f.get('coins_lost', 0) + (-d)
            # таймер НЕ сбрасывается здесь — только при успешном действии
        f['last_purse'] = st.purse
        # отслеживаем не подобранные монеты
        if len(st.ground_coins) > f.get('unpicked_coins', 0):
            f['unpicked_coins'] = len(st.ground_coins)
        # отслеживаем первого лучника и первого рабочего
        b = f['base']
        if 'arc0' not in b:
            b['arc0'] = st.arc
            b['wrk0'] = st.wrk
        if b.get('arc0', 1) == 0 and st.arc >= 1 and f.get('first_archer', 0) == 0:
            f['first_archer'] = 1
            f['last_interaction'] = self.now()
        if b.get('wrk0', 1) == 0 and st.wrk >= 1 and f.get('first_worker', 0) == 0:
            f['first_worker'] = 1
            f['last_interaction'] = self.now()
        # сохраняем базовые значения
        # отслеживание завершённого строительства (по изменению walls/shops/farms)
        # используем max, чтобы не считать перестройки
        cur_buildings = st.wall + st.shop + st.farm
        max_buildings = f.get('max_buildings', 0)
        if cur_buildings > max_buildings:
            f['buildings_completed'] = f.get('buildings_completed', 0) + (cur_buildings - max_buildings)
            f['max_buildings'] = cur_buildings
        elif max_buildings == 0:
            f['max_buildings'] = cur_buildings
        # отслеживание срубленных деревьев (дерево исчезло из payables)
        # используем min, чтобы не считать появление новых деревьев
        cur_trees = len([p for p in st.payables if p.type == 'PayableTree'])
        min_trees = f.get('min_trees', 9999)
        if cur_trees < min_trees and min_trees != 9999:
            f['trees_chopped'] = f.get('trees_chopped', 0) + (min_trees - cur_trees)
            f['min_trees'] = cur_trees
        elif min_trees == 9999:
            f['min_trees'] = cur_trees
        # дистанция (ходьба vs бег)
        if f['last_px'] is not None:
            dist = abs(st.px - f['last_px'])
            f['distance'] += dist
            if st.action == 'Run':
                f['run_distance'] = f.get('run_distance', 0.0) + dist
            else:
                f['walk_distance'] = f.get('walk_distance', 0.0) + dist
        f['last_px'] = st.px
        # переутомление лошади (стамина упала до 0)
        if st.stamina <= 0.01 and f.get('last_stamina', 1.0) > 0.01:
            f['horse_exhaustion'] = f.get('horse_exhaustion', 0) + 1
        f['last_stamina'] = st.stamina
        # отдых лошади: если стамина низкая и бот не бежит
        threshold = BOUNTIES.get('horse_rest_threshold', 0.5)
        if st.stamina < threshold and st.action != 'Run':
            now = self.now()
            if f.get('last_track_time') is not None:
                dt = now - f['last_track_time']
                f['horse_rest_time'] = f.get('horse_rest_time', 0.0) + dt
            f['last_track_time'] = now
        else:
            f['last_track_time'] = self.now()
        # проверка найма: если бросали монету, но Peasant не появился
        if getattr(self, '_recruit_check_time', 0) > 0 and self.now() > self._recruit_check_time:
            if len(st.cit('P')) > getattr(self, '_recruit_check_p_count', 0):
                # найм удался - сбрасываем таймер бездействия
                if hasattr(self, 'fit'):
                    self.fit['last_interaction'] = self.now()
                    self.fit['recruited_beggars'] = self.fit.get('recruited_beggars', 0) + 1
            else:
                # найм не удался - штраф
                if hasattr(self, 'fit'):
                    self.fit['failed_recruit'] = self.fit.get('failed_recruit', 0) + 1
            self._recruit_check_time = 0.0
        # штраф за бездействие: каждые 5 сек без взаимодействия
        idle_time = self.now() - f['last_interaction']
        expected_penalty = int(idle_time / 5.0)
        if expected_penalty > f['inactivity_penalty']:
            f['inactivity_penalty'] = expected_penalty
        b = f['base']
        f['buildings'] = ((st.wall - b['wall']) + (st.shop - b['shop'])
                          + (st.farm - b['farm']) + (st.portal - b['portal']))
        # строительство засчитывается только если есть рабочие с молотками
        prev = f.get('prev_buildings', None)
        if prev is None:
            f['prev_buildings'] = f['buildings']
        else:
            delta = f['buildings'] - prev
            if delta > 0:
                if st.wrk >= 1:
                    f['buildings_with_workers'] = f.get('buildings_with_workers', 0) + delta
                else:
                    f['buildings_without_workers'] = f.get('buildings_without_workers', 0) + delta
            f['prev_buildings'] = f['buildings']
        p_count = len(st.cit('P'))
        f['citizens'] = ((st.arc - b['arc']) + (st.wrk - b['wrk'])
                         + (st.frm - b['frm']) + (st.knt - b['knt'])
                         + (p_count - b['p0']))
        # корона: потеря (удар врага), подъём обратно и смерть
        prev_hc = f.get('prev_hc', 1)
        if prev_hc == 1 and st.hc == 0:
            f['crown_lost'] += 1
        if prev_hc == 0 and st.hc == 1:
            f['crown_picked'] = f.get('crown_picked', 0) + 1
        f['prev_hc'] = st.hc
        down = (st.hc == 0)
        if not down:
            f['death_latch'] = False
        # атака врага в радиусе 3 от короля: считаем именно начало атаки (фронт), а не каждый тик
        attacking_now = any(atk and abs(ex - st.px) < 3.0 for (_n, ex, atk) in st.enemy_list)
        onset = attacking_now and not f.get('attacking_prev', False)
        f['attacking_prev'] = attacking_now
        # корона исчезла с земли, а король её не поднимал: унесли в портал
        crown_gone = f.get('prev_crown_x') is not None and st.crown_x is None and down
        f['prev_crown_x'] = st.crown_x if down else None
        # смерть: корона уже была потеряна, монет нет и атаковали снова, либо корону унесли в портал
        if down and prev_hc == 0 and not f.get('death_latch', False):
            if (onset and st.purse == 0) or crown_gone:
                f['deaths'] = f.get('deaths', 0) + 1
                f['death_latch'] = True

    def metrics(self):
        f = self.fit
        m = dict(coins_in=f['coins_in'], coins_spent=f['coins_spent'],
                 buys=f['buys'], buildings=f['buildings'],
                 citizens=f['citizens'], crown_lost=f['crown_lost'],
                 recruited_beggars=f.get('recruited_beggars', 0),
                 day=f.get('day', 1),
                 deaths=f.get('deaths', 0),
                 crown_picked=f.get('crown_picked', 0),
                 buildings_started=f.get('buildings_started', 0),
                 buildings_completed=f.get('buildings_completed', 0),
                 max_buildings=f.get('max_buildings', 0),
                 min_trees=f.get('min_trees', 9999),
                 trees_marked=f.get('trees_marked', 0),
                 trees_chopped=f.get('trees_chopped', 0),
                 first_archer=f.get('first_archer', 0),
                 first_worker=f.get('first_worker', 0),
                 tool_buys=f.get('tool_buys', 0),
                 hammer_buys=f.get('hammer_buys', 0),
                 bow_buys=f.get('bow_buys', 0),
                 scythe_buys=f.get('scythe_buys', 0),
                 castle_base_buys=f.get('castle_base_buys', 0),
                 castle_upgrade_buys=f.get('castle_upgrade_buys', 0),
                 castle_upgrade_buys_with_workers=f.get('castle_upgrade_buys_with_workers', 0),
                 castle_upgrade_buys_without_workers=f.get('castle_upgrade_buys_without_workers', 0),
                 buildings_with_workers=f.get('buildings_with_workers', 0),
                 buildings_without_workers=f.get('buildings_without_workers', 0),
                 distance=f['distance'], walk_distance=f.get('walk_distance', 0.0),
                 run_distance=f.get('run_distance', 0.0),
                 horse_exhaustion=f.get('horse_exhaustion', 0),
                 horse_rest_time=f.get('horse_rest_time', 0.0),
                 final_purse=f['last_purse'],
                 inactivity_penalty=f['inactivity_penalty'],
                 tree_chops_with_workers=f.get('tree_chops_with_workers', 0),
                 tree_chops_without_workers=f.get('tree_chops_without_workers', 0),
                 failed_coin=f.get('failed_coin', 0),
                 failed_buy=f.get('failed_buy', 0),
                 failed_recruit=f.get('failed_recruit', 0),
                 failed_tree=f.get('failed_tree', 0),
                 unpicked_coins=f.get('unpicked_coins', 0),
                 coins_lost=f.get('coins_lost', 0),
                 generation=self.generation,
                 seconds=time.time() - f['start'])
        m['score'] = fitness(m)
        return m

    # -------------------------------------------------------------- ввод
    def stop_move(self):
        self.inp.up(SC_A)
        self.inp.up(SC_D)
        self.inp.up(SC_LSHIFT)

    def release_s(self):
        self.inp.up(SC_S)

    def stop_all(self):
        self.stop_move()
        self.release_s()

    def set_sprint(self, want, st):
        """Галоп. В игре спринт стартует по GetButtonDown(Shift),
        поэтому при каждом старте надо отпустить и снова нажать Shift."""
        if not want:
            self.inp.up(SC_LSHIFT)
            return
        if st is not None and st.action == 'Run':
            self.inp.down(SC_LSHIFT)
            return
        if SC_LSHIFT in self.inp.held:
            self.inp.up(SC_LSHIFT)      # сброс, чтобы сработал ButtonDown
        else:
            self.inp.down(SC_LSHIFT)

    def move_to(self, dx, sprint=False, st=None):
        if dx < 0:
            self.inp.up(SC_D)
            self.inp.down(SC_A)
        else:
            self.inp.up(SC_A)
            self.inp.down(SC_D)
        self.set_sprint(sprint, st)

    def walk_to(self, st, x, sprint=False, tol=0.5):
        """Идти к x. True - дошли."""
        dx = x - st.px
        if abs(dx) <= tol:
            self.stop_move()
            return True
        want_sprint = (sprint and abs(dx) > CFG['sprint_min_dist']
                       and st.stamina > CFG['sprint_min_stamina'])
        self.move_to(dx, want_sprint, st)
        return False

    def safe_sprint(self, st):
        """Галоп - только если рядом нет врага."""
        n = st.nearest_enemy(st.px)
        return n is None or n[3] > 26.0

    # --------------------------------------------------------- приоритеты
    def buy_priority(self, st, p):
        if not getattr(p, 'enabled', True):
            return None
        base = p.base
        low = base.lower()
        free_citizens = bool(st.cit('P'))
        bonus_key = None
        if p.type == 'PayableTree':
            prio = CFG['tree_prio']
            bonus_key = 'free_citizen_bonus_tree'
        elif p.type == 'PayableUpgrade':
            if 'mason' in low:
                prio = CFG['w_mason']
                bonus_key = 'free_citizen_bonus_mason'
            elif 'castle' in low:
                if 'castle0' in low:
                    prio = CFG['w_castle']
                    bonus_key = 'free_citizen_bonus_castle_base'
                else:
                    prio = CFG['w_castle']
                    bonus_key = 'free_citizen_bonus_castle_upgrade'
            elif 'farmhouse' in low:
                prio = CFG['w_farmhouse']
                bonus_key = 'free_citizen_bonus_farmhouse'
            elif 'wall3' in low:
                prio = CFG['w_wall3']
                bonus_key = 'free_citizen_bonus_wall'
            elif 'wall2' in low:
                prio = CFG['w_wall2']
                bonus_key = 'free_citizen_bonus_wall'
            elif 'wall1' in low:
                prio = CFG['w_wall1']
                bonus_key = 'free_citizen_bonus_wall'
            elif 'tower' in low:
                prio = CFG['w_tower']
                bonus_key = 'free_citizen_bonus_tower'
            elif 'wall0' in low:
                prio = CFG['w_wall0']
                bonus_key = 'free_citizen_bonus_wall'
            else:
                prio = 55
                bonus_key = None
        elif p.type == 'Statue':
            s = st.statue('Archer') if 'archer' in low else st.statue('Worker')
            if s and s[1] >= 2.4:
                return None
            prio = CFG['w_statue']
            bonus_key = 'free_citizen_bonus_statue'
        elif p.type == 'PayableWorkshop':
            prio = CFG['w_workshop']
            bonus_key = 'free_citizen_bonus_workshop'
        elif p.type == 'PayableShop':
            return self.tool_priority(st, p)
        elif p.type == 'PayableBorder':
            if st.knt >= CFG['target_knights']:
                return None
            if not st.cit('P'):
                return None
            prio = CFG['w_border']
            bonus_key = 'free_citizen_bonus_border'
        elif p.type == 'Steed':
            prio = CFG['w_steed']
            bonus_key = 'free_citizen_bonus_steed'
        elif p.type == 'Merchant':
            prio = CFG['w_merchant']
            bonus_key = 'free_citizen_bonus_merchant'
        else:
            return None
        if free_citizens and bonus_key:
            bonus = BOUNTIES.get(bonus_key, 0.0)
            if bonus > 0:
                prio = max(1, prio - bonus)
        # бонус обороны по дням: с каждым днём оборона приоритетнее
        # ограничение: не превышает profession_missing_bonus (5.0)
        defense_items = ('wall', 'tower', 'border', 'knight')
        is_defense = any(d in low for d in defense_items) or p.type == 'PayableBorder'
        if is_defense:
            defense_bonus = BOUNTIES.get('defense_day_bonus', 0.05) * st.day
            cap = BOUNTIES.get('profession_missing_bonus', 5.0) - 0.1
            defense_bonus = min(defense_bonus, cap)
            if defense_bonus > 0:
                prio = max(1, prio - defense_bonus)
        # штраф за дальность от базы (0): предпочитаем ближние точки взаимодействия
        prio = prio + abs(p.x) * 0.02
        # расстояние от игрока до объекта: X = |P - A|
        dist_to_player = abs(st.px - p.x)
        prio = prio + dist_to_player * 0.01
        # расстояние до ближайшего другого объекта: предпочитаем кластеры
        others = [q for q in st.payables if q is not p]
        if others:
            nearest_other = min(abs(p.x - q.x) for q in others)
            prio = prio + nearest_other * 0.01
        return prio

    def tool_priority(self, st, p):
        free_citizens = st.cit('P')
        if not free_citizens:
            return None
        item = None
        for it, x in st.shops:
            if abs(x - p.x) < 1.5:
                item = it
                break
        if item is None:
            return None
        if item == 'ToolScythe' and st.frm < CFG['target_farmers']:
            prio = CFG['w_tool_scythe']
            # бонус если фермеров вообще нет
            if st.frm == 0:
                prio = max(1, prio - BOUNTIES.get('profession_missing_bonus', 5.0))
        elif item == 'ToolBow' and st.arc < CFG['target_archers']:
            prio = CFG['w_tool_bow']
            # бонус если лучников вообще нет
            if st.arc == 0:
                prio = max(1, prio - BOUNTIES.get('profession_missing_bonus', 5.0))
        elif item == 'ToolHammer' and st.wrk < CFG['target_workers']:
            prio = CFG['w_tool_hammer']
            # бонус если рабочих вообще нет
            if st.wrk == 0:
                prio = max(1, prio - BOUNTIES.get('profession_missing_bonus', 5.0))
        else:
            return None
        # бонус за свободных граждан
        bonus = BOUNTIES.get('free_citizen_tool_bonus', 0.0)
        if bonus > 0 and free_citizens:
            prio = max(1, prio - bonus)
        # бонус за свободных граждан (из bounties)
        # бонус обороны по дням для лучников (buy_bow)
        if item == 'ToolBow':
            defense_bonus = BOUNTIES.get('defense_day_bonus', 0.05) * st.day
            cap = BOUNTIES.get('profession_missing_bonus', 5.0) - 0.1
            defense_bonus = min(defense_bonus, cap)
            if defense_bonus > 0:
                prio = max(1, prio - defense_bonus)
        return prio


    def best_buy(self, st, affordable_only=True):
        """Лучшая покупка: (приоритет, Payable). При affordable_only берём
        только то, на что хватает монет."""
        best = None
        # сначала предпочитаем точки ближе к базе (|x| <= 50), если такие есть
        close = [p for p in st.payables if abs(p.x) <= 50]
        pool = close if close else list(st.payables)
        for p in pool:
            prio = self.buy_priority(st, p)
            if prio is None:
                continue
            if self.blocked(p):
                continue
            if affordable_only and not self.can_afford(st, p):
                continue
            # при равном приоритете предпочитаем точки ближе к базе (0)
            key = (prio, abs(p.x))
            if best is None or key < best[0]:
                best = (key, p)
        return (best[0][0], best[1]) if best else None

    def can_afford(self, st, p):
        return st.purse >= p.price + (0 if p.type == 'PayableTree' else CFG['keep_coins'])

    # ============================================================ решение
    def blocked(self, p):
        """Объект в чёрном списке (не оплачивается - например, дерево в глубине леса)."""
        key = '%s@%.0f' % (p.base, p.x)
        return self.pay_black.get(key, 0) > self.now()

    def decide(self, st):
        # 0. корона упала - подобрать немедленно
        if st.hc == 0 and st.crown_x is not None:
            return ('crown', st.crown_x)

        # 1. враг рядом - уходить (король не умеет драться)
        n = st.nearest_enemy(st.px)
        if n is not None and n[3] < CFG['danger_dist']:
            return ('flee', n)

        # 2. недоплаченная покупка - додержать S
        if self.pay_target is not None:
            return ('pay', self.pay_target)

        # 3. наём бомжей (высокий приоритет - нужны граждане)
        t = self.recruit_task(st)
        if t:
            return t

        # 4. покупка (если хватает монет) - рандом среди 3 лучших
        # НО: если золота мало и есть рабочие - фармим деревья
        low_gold = st.purse < CFG.get('low_gold_threshold', 5) and st.purse >= 1
        if low_gold and st.wrk >= 1:
            t = self.tree_task(st, None)
            if t:
                return t
        affs = []
        close_payables = [p for p in st.payables if abs(p.x) <= 50]
        pool = close_payables if close_payables else st.payables
        for p in pool:
            prio = self.buy_priority(st, p)
            if prio is None or self.blocked(p):
                continue
            if not self.can_afford(st, p):
                continue
            affs.append((prio, p))
        affs.sort(key=lambda x: (x[0], abs(x[1].x)))
        if affs:
            # рандом среди 3 лучших (с весом по приоритету)
            top = affs[:min(3, len(affs))]
            prio, p = random.choice(top)
            self.note('buy', '%s(%s) %d @%.1f' % (p.base, p.type, p.price, p.x))
            return ('pay', p)

        # 4. наём бомжей: бросить монету рядом с бомжом (B) -> он станет Peasant
        t = self.recruit_task(st)
        if t:
            return t

        # 5. доход: налоги (стоим рядом - гражданин сам отдаёт монеты)
        t = self.tax_task(st)
        if t:
            return t

        # 5. монеты на земле (нищие их растащат)
        t = self.coin_task(st)
        if t:
            return t

        # 6. заработок: рубим дерево (1 монета), пока не хватит на цель
        t = self.tree_task(st, self.best_buy(st, False))
        if t:
            return t

        # 7. ждём у костра
        return ('idle', None)

    def coin_task(self, st):
        """Монета на земле. Если её не удаётся подобрать (забрал нищий) - забиваем."""
        now = self.now()
        # собрать кандидатов (до 3 ближайших) для рандома
        candidates = []
        for c in st.ground_coins:
            if self.coin_black.get(c, 0) > now:
                continue
            d = abs(c - st.px)
            if d > 45:
                continue
            candidates.append((d, c))
        candidates.sort(key=lambda x: x[0])
        if not candidates:
            self.coin_target = None
            return None
        # рандом среди 3 ближайших
        pick_idx = random.randrange(min(3, len(candidates)))
        c = candidates[pick_idx][1]
        if self.coin_target is None or abs(c - self.coin_target) > 1.5:
            self.coin_target = c
            self.coin_since = now
            self.coin_purse = st.purse
        elif now - self.coin_since > self.coin_timeout and st.purse <= self.coin_purse:
            # монету не подобрали - скорее всего забрал нищий
            self.coin_black[c] = now + CFG['coin_black_time']
            self.coin_target = None
            self.note('coin_skip', '%.1f' % c)
            if hasattr(self, 'fit'):
                self.fit['failed_coin'] = self.fit.get('failed_coin', 0) + 1
            return None
        return ('coin', c)

    def recruit_task(self, st):
        """Нанять бомжа: бросить монету рядом с B (он подберёт и станет Peasant).
        Нужно чтобы sel был None (иначе S пойдёт на оплату)."""
        if st.purse < 1:
            return None
        # если граждан уже достаточно - не нанимаем
        total_citizens = st.arc + st.wrk + st.frm + st.knt + len(st.cit('P'))
        target_total = (CFG['target_archers'] + CFG['target_workers']
                        + CFG['target_farmers'] + CFG['target_knights'])
        if total_citizens >= target_total:
            return None
        # есть ли бомжи рядом?
        beggars = [(x, c) for (t, x, c) in st.citizens if t == 'B']
        if not beggars:
            self.note('recruit', 'нет бомжей рядом (px=%.1f, beggars=%d)' % (st.px, len(beggars)))
            return None
        self.note('recruit', 'бомжи рядом: %d, ближайший=%.1f' % (len(beggars), min(beggars, key=lambda b: abs(b[0]-st.px))[0]))
        # предпочитаем бомжей, ближайших к базе (костру, позиция ~0)
        # сортируем по расстоянию до базы
        beggars_sorted = sorted(beggars, key=lambda b: abs(b[0]))
        # ближайший к базе бомж
        base_near = [b for b in beggars if abs(b[0] - st.px) < 12]
        if not base_near:
            # идём к бомжу, ближайшему к базе
            target = beggars_sorted[0]
            if abs(target[0] - st.px) > 80:
                return None
            return ('recruit_walk', target[0])
        # бомж рядом - бросаем монету к ближайшему к базе (только если sel is None)
        if st.sel is not None:
            return None   # не можем бросить - сначала уйти от Payable
        if self.now() - getattr(self, 'last_recruit_drop', 0) < 3.0 / self.speed:
            return None   # кулдаун
        nearest = min(base_near, key=lambda b: abs(b[0]))  # ближайший к базе
        return ('recruit_drop', nearest[0])

    def tax_task(self, st):
        """Кто отдаёт налог: F/A/W с монетами > 1. Нужно стоять рядом (не бежать)."""
        targets = [(x, c) for (t, x, c) in st.citizens
                   if t in ('F', 'A', 'W') and c > CFG['tax_min_coins']]
        if not targets:
            self.tax_until = 0.0
            return None
        near = [t for t in targets if abs(t[0] - st.px) < 16]
        pick = (min(near, key=lambda t: abs(t[0] - st.px)) if near
                else max(targets, key=lambda t: t[1]))
        if abs(pick[0] - st.px) > 110:
            return None
        return ('tax', pick[0])

    def tree_task(self, st, goal):
        """Если на цель не хватает монет - рубим ближайшее дерево."""
        need = 0
        if goal is not None:
            need = goal[1].price
        if need > 0 and st.purse >= need:
            return None                      # денег хватает - покупаем
        # рубить дерево можно только если есть хотя бы 1 монета
        if st.purse < 1:
            return None
        trees = [p for p in st.payables
                 if p.type == 'PayableTree' and not self.blocked(p)]
        if not trees:
            return None
        # предпочитаем ближайшие деревья (не дальше 40 единиц)
        near = [t for t in trees if abs(t.x - st.px) <= 40]
        pool = near if near else trees
        pool.sort(key=lambda p: abs(p.x - st.px))
        # рандом среди 3 ближайших
        t = random.choice(pool[:min(3, len(pool))])
        if abs(t.x - st.px) > 120:
            return None
        # отслеживаем: рубка с рабочими или без
        if hasattr(self, 'fit'):
            if st.wrk >= 1:
                self.fit['tree_chops_with_workers'] = self.fit.get('tree_chops_with_workers', 0) + 1
            else:
                self.fit['tree_chops_without_workers'] = self.fit.get('tree_chops_without_workers', 0) + 1
        return ('pay', t)

    # =========================================================== исполнение
    def execute(self, st):
        kind, payload = self.task

        if kind == 'idle':
            self.stop_all()
            # если есть монеты и нет рабочих - идём к ближайшему бомжу
            beggars = [x for (t, x, c) in st.citizens if t == 'B']
            if beggars and st.purse >= 1 and st.wrk < CFG['target_workers']:
                target = min(beggars, key=lambda x: abs(x - st.px))
                if abs(target - st.px) < 80:
                    self.note('task', 'IDLE -> beggar @%.1f' % target)
                    self.walk_to(st, target, sprint=False, tol=1.0)
                    return
            # если есть монеты и нет граждан - идём к костру (там бомжи)
            if st.purse >= 1 and not st.citizens:
                # идём к 0 (костёр)
                self.note('task', 'IDLE -> campfire')
                self.walk_to(st, 0.0, sprint=False, tol=2.0)
                return
            # текущий счёт (для отображения в консоли)
            _m = self.metrics()
            _score = _m.get('score', 0)
            self.note('status', 'SCORE=%.1f | d%d t%.1f purse=%d arc=%d wrk=%d frm=%d knt=%d '
                               'wall=%d portal=%d en=%d' %
                       (_score, st.day, st.time, st.purse, st.arc, st.wrk, st.frm, st.knt,
                        st.wall, st.portal, st.enemies))
            return

        if kind == 'recruit_walk':
            x = payload
            self.note('task', 'RECRUIT walk -> %.1f' % x)
            self.release_s()
            self.walk_to(st, x, sprint=False, tol=1.0)
            return

        if kind == 'recruit_drop':
            # бросить монету (кратковременно нажать S, когда sel is None)
            self.note('task', 'RECRUIT drop coin @%.1f' % st.px)
            if not self.dry:
                self.inp.down(SC_S)
                time.sleep(0.15 / self.speed)
                self.inp.up(SC_S)
            self.last_recruit_drop = self.now()
            self._recruit_drop_until = self.now() + 2.0
            self.stat['last_recruit'] = self.now()
            # проверим через 2 сек, появился ли новый Peasant
            self._recruit_check_time = self.now() + self.recruit_check
            self._recruit_check_purse = st.purse
            self._recruit_check_p_count = len(st.cit('P'))
            return

        if kind == 'crown':
            self.note('task', 'CROWN @%.1f' % payload)
            self.release_s()
            self.walk_to(st, payload, sprint=self.safe_sprint(st), tol=1.0)
            return

        if kind == 'flee':
            name, ex, atk, d = payload
            away = 1 if st.px >= ex else -1
            target = st.px + away * CFG['flee_dist']
            self.note('task', 'FLEE %s@%.1f d=%.0f' % (name, ex, d))
            self.release_s()
            self.walk_to(st, target, sprint=True, tol=2.0)
            return

        if kind == 'coin':
            self.note('task', 'COIN @%.1f' % payload)
            self.release_s()
            self.walk_to(st, payload, sprint=self.safe_sprint(st), tol=0.6)
            return

        if kind == 'tax':
            x = payload
            near = None
            for (t, cx, c) in st.citizens:
                if t in ('F', 'A', 'W') and c > CFG['tax_min_coins'] and abs(cx - st.px) < 2.2:
                    near = (t, cx, c)
                    break
            if near:
                self.stop_move()
                self.release_s()
                if self.tax_until == 0.0:
                    self.tax_until = self.now() + CFG['tax_wait']
                if self.now() < self.tax_until:
                    return
                self.tax_until = 0.0
                return
            self.tax_until = 0.0
            self.note('task', 'TAX -> %.1f' % x)
            self.release_s()
            # налог снимается только когда король НЕ бежит
            self.walk_to(st, x, sprint=False, tol=0.6)
            return

        if kind == 'pay':
            p = payload
            if p is None:
                return
            self.do_pay(st, p)

    # -------------------------------------------------------- оплата
    def same_payable(self, a, b):
        if a is None or b is None:
            return False
        if a.type != b.type:
            return False
        if a.name and b.name:
            return a.name == b.name
        return abs(a.x - b.x) < 0.6

    def do_pay(self, st, p):
        now = self.now()

        # --- фаза: держим S
        if self.pay_phase == 'hold':
            ps = st.pay_state
            if ps == 'None':
                # транзакция завершилась (или откатилась)
                if now - self.pay_started > 0.6:
                    self.finish_pay(st, p)
                return
            if now - self.pay_started > self.pay_timeout(p.price):
                self.note('pay_timeout', '%s %d' % (p.base, p.price))
                self.finish_pay(st, p)
            return

        # --- цель сменилась - поиск точки сначала
        if self.pay_target is not None and not self.same_payable(self.pay_target, p):
            self.finish_pay(st, self.pay_target)
        self.pay_target = p
        if self.scan_x is None:
            self.scan_x = p.x
            self.scan_i = 0
            self.scan_t = 0.0
            self.pay_since = now

        # --- идём к точке оплаты
        target = self.scan_x
        dx = target - st.px
        if abs(dx) > CFG['pay_tol']:
            self.release_s()
            self.note('task', 'PAY %s(%d) -> %.2f (px=%.2f)' %
                      (p.base, p.price, target, st.px))
            self.walk_to(st, target, sprint=False, tol=CFG['pay_tol'])
            return

        # --- стоим на месте
        self.stop_move()
        if st.sel is not None and self.same_payable(st.sel, p):
            if st.purse <= 0:
                return
            if st.pay_state != 'None':
                return
            self.pay_phase = 'hold'
            self.pay_started = now
            self.pay_purse0 = st.purse
            self.note('task', 'PAY %s price=%d purse=%d' % (p.base, p.price, st.purse))
            if not self.dry:
                self.inp.down(SC_S)
            return

        # --- объект не выбирается: ищем точку рядом
        self.scan_t += 0.2
        if self.scan_t >= 0.45:
            self.scan_t = 0.0
            self.scan_i = (self.scan_i + 1) % len(SCAN)
            self.scan_x = p.x + SCAN[self.scan_i]
            self.note('pay_scan', '%s @%.1f -> %.2f sel=%s' %
                      (p.base, p.x, self.scan_x, st.sel))
        if now - self.pay_since > self.pay_giveup:
            key = '%s@%.0f' % (p.base, p.x)
            self.pay_black[key] = now + 5.0    # короткий бан - деревья могут стать доступными через 5 с
            self.note('pay_giveup', '%s @%.1f' % (p.base, p.x))
            if hasattr(self, 'fit'):
                if 'Tree' in p.base:
                    self.fit['failed_tree'] = self.fit.get('failed_tree', 0) + 1
                else:
                    self.fit['failed_buy'] = self.fit.get('failed_buy', 0) + 1
            self.finish_pay(st, p)
        return

    def pay_timeout(self, price):
        return 0.5 + 0.35 * max(1, price) + 1.2

    def finish_pay(self, st, p):
        self.release_s()
        spent = self.pay_purse0 - st.purse
        # если монеты не списались — объект недоступен (условия не выполнены): бан на 60 с
        if spent <= 0:
            key = '%s@%.0f' % (p.base, p.x)
            self.pay_black[key] = self.now() + 60.0
            self.note('pay_disabled', '%s @%.1f (spent=%d)' % (p.base, p.x, spent))
        self.note('pay_done', '%s цена=%d потрачено=%d' % (p.base, p.price, spent))
        # успешная покупка: сбрасываем таймер бездействия
        if spent > 0 and spent >= p.price:
            if hasattr(self, 'fit'):
                self.fit['last_interaction'] = self.now()
        # отслеживаем покупки инструментов (только если успешно)
        if hasattr(self, 'fit') and spent > 0 and spent >= p.price:
            self.fit['buys'] = self.fit.get('buys', 0) + 1
            # отслеживаем начало строительства/рубки
            if 'Tree' in p.base:
                self.fit['trees_marked'] = self.fit.get('trees_marked', 0) + 1
            elif p.type == 'PayableUpgrade':
                self.fit['buildings_started'] = self.fit.get('buildings_started', 0) + 1
            low = p.base.lower()
            if 'castle0' in low:
                self.fit['castle_base_buys'] = self.fit.get('castle_base_buys', 0) + 1
            elif 'castle' in low:
                self.fit['castle_upgrade_buys'] = self.fit.get('castle_upgrade_buys', 0) + 1
                if st.wrk >= 1:
                    self.fit['castle_upgrade_buys_with_workers'] = self.fit.get('castle_upgrade_buys_with_workers', 0) + 1
                else:
                    self.fit['castle_upgrade_buys_without_workers'] = self.fit.get('castle_upgrade_buys_without_workers', 0) + 1
            if 'toolhammer' in low or 'hammer' in low:
                self.fit['hammer_buys'] = self.fit.get('hammer_buys', 0) + 1
                self.fit['tool_buys'] = self.fit.get('tool_buys', 0) + 1
            elif 'toolbow' in low or 'bow' in low:
                self.fit['bow_buys'] = self.fit.get('bow_buys', 0) + 1
                self.fit['tool_buys'] = self.fit.get('tool_buys', 0) + 1
            elif 'toolscythe' in low or 'scythe' in low:
                self.fit['scythe_buys'] = self.fit.get('scythe_buys', 0) + 1
                self.fit['tool_buys'] = self.fit.get('tool_buys', 0) + 1
        self.pay_target = None
        self.pay_phase = None
        self.pay_purse0 = 0
        self.nudge = 0.0
        self.pay_wait_sel = 0.0
        self.scan_x = None
        self.scan_i = 0
        self.scan_t = 0.0
        self.pay_since = 0.0
        self.last_recruit_drop = 0.0
        # взаимодействие
        if hasattr(self, 'fit') and self.fit.get('last_interaction') is not None:
            self.fit['last_interaction'] = self.now()

    # ============================================================== главный
    def ensure_focus(self, need_input=True):
        """Бот НЕ перехватывает фокус: если игрок переключился в другое окно,
        бот ждёт, пока пользователь сам вернётся в игру (иначе он не сможет
        управлять королём - игра получает клавиши только в фокусе).

        own_focus=True - режим эволюции: бот сам забирает фокус игры.
        """
        if self.inp.is_focused():
            self.note('focus', 'игра в фокусе')
            return True
        if self.own_focus:
            if self.now() - self.last_focus < 2.5:
                return False
            self.last_focus = self.now()
            ok = self.inp.force_focus()
            self.note('focus', 'забрал фокус' if ok else 'не смог')
            return ok
        self.note('focus', 'жду фокус игры')
        return False

    def hotkey(self):
        """F8 - пауза, F9 - стоп. Ctrl+Shift+End обрабатывается evo.py."""
        if user32.GetAsyncKeyState(0x78) & 1:     # F9
            return 'quit'
        if user32.GetAsyncKeyState(0x77) & 1:     # F8
            return 'pause'
        return None

    def step(self):
        st = read_state()
        if st is None:
            self.note('no_data', 1)
            return
        if not st.playing:
            self.stop_all()
            # если открыто меню настроек или другое меню - закрыть по Esc
            if not hasattr(self, "_esc_pressed_time"):
                self._esc_pressed_time = 0.0
            if self.now() - self._esc_pressed_time > self.esc_cooldown:
                if not self.dry:
                    self.inp.down(0x1C)  # SC_ENTER
                    time.sleep(0.1 / self.speed)
                    self.inp.up(0x1C)
                self._esc_pressed_time = self.now()
                self.note("esc_menu", "нажат Enter для закрытия меню/настроек")
            # скип обучения/интро: зажать S на 5 секунд (только если игра в фокусе
            # или бот в режиме эволюции, чтобы не мешать пользователю)
            # скип обучения (только если включён флаг --skip-tutorial)
            if self.skip_tutorial:
                # продолжаем уже начатый скип
                if self.skip_until > 0:
                    if self.now() < self.skip_until:
                        if not self.dry:
                            self.inp.down(SC_S)
                    else:
                        if not self.dry:
                            self.inp.up(SC_S)
                        self.skip_until = 0.0
                # или начинаем новый скип (с кулдауном) - забираем фокус разово
                elif self.now() - self.last_skip > self.skip_cooldown:
                    self.inp.force_focus()
                    if self.inp.is_focused() or self.own_focus:
                        self.skip_until = self.now() + 5.0 / self.speed
                        self.last_skip = self.now()
                        self.note('skip_tutorial', 'начал')
                        if not self.dry:
                            self.inp.down(SC_S)
            self.note('not_playing', st.next)
            return

# перешли в игру - отпустить S, если держали
        if self.skip_until != 0.0:
            self.inp.up(SC_S)
            self.skip_until = 0.0
        new = self.decide(st)
        self.task = new
        # обновляем текущий счёт для вывода в консоль
        try:
            self._last_score = self.metrics().get('score', 0)
        except Exception:
            pass
        if not self.dry:
            need = self.task[0] not in ('idle',)
            if not self.ensure_focus(need):
                self.stop_all()
                return
        self.execute(st)


def main():
    dry = '--dry' in sys.argv
    once = '--once' in sys.argv
    own_focus = '--own-focus' in sys.argv
    skip_tutorial = '--skip-tutorial' in sys.argv
    generation = 1
    speed = 1.0
    for i, a in enumerate(sys.argv):
        if a == '--speed' and i + 1 < len(sys.argv):
            try:
                speed = float(sys.argv[i + 1])
            except ValueError:
                speed = 1.0
    for i, a in enumerate(sys.argv):
        if a == '--generation' and i + 1 < len(sys.argv):
            try:
                generation = int(sys.argv[i + 1])
            except ValueError:
                generation = 1
    genome = None
    for i, a in enumerate(sys.argv):
        if a == '--genome' and i + 1 < len(sys.argv):
            genome = sys.argv[i + 1]
    episode = None
    for i, a in enumerate(sys.argv):
        if a == '--episode' and i + 1 < len(sys.argv):
            try:
                episode = float(sys.argv[i + 1])
            except ValueError:
                episode = None
    result_path = None
    for i, a in enumerate(sys.argv):
        if a == '--result' and i + 1 < len(sys.argv):
            result_path = sys.argv[i + 1]
    log_file = None
    for i, a in enumerate(sys.argv):
        if a == '--log-file' and i + 1 < len(sys.argv):
            log_file = sys.argv[i + 1]
    live_result_path = None
    for i, a in enumerate(sys.argv):
        if a == '--live-result' and i + 1 < len(sys.argv):
            live_result_path = sys.argv[i + 1]
    if genome:
        load_genome(genome)
    inp = Input()
    log = BotLog(log_file) if log_file else BotLog()
    # записываем скорость в файл для игры
    try:
        with open(SPEED_FILE, 'w') as f:
            f.write(str(speed))
        log('скорость игры: %.1fx (записано в %s)' % (speed, SPEED_FILE))
    except Exception as e:
        log('не смог записать скорость: %r' % (e,))
    hwnd = inp.find_window('Kingdom')
    log('окно игры: %s  режим: %s  эпизод: %s  own_focus: %s' %
        (hwnd, 'DRY' if dry else 'LIVE', episode, own_focus))
    if not hwnd:
        log('!! окно игры не найдено')
        return
    bot = Bot(dry=dry, inp=inp, log=log, own_focus=own_focus,
              skip_tutorial=skip_tutorial, generation=generation, speed=speed)
    bot._last_live_write = 0.0
    paused = False
    t_end = (time.time() + episode) if episode else None
    bot.episode_end = t_end
    try:
        while True:
            try:
                hk = bot.hotkey()
                if hk == 'quit':
                    log('стоп по F9')
                    break
                if hk == 'pause':
                    paused = not paused
                    inp.release()
                    log('ПАУЗА' if paused else 'ПРОДОЛЖАЮ')
                if paused:
                    time.sleep(0.2 / speed)
                    continue
                st = read_state()
                if st is not None:
                    bot._last_state = st
                    bot.track(st)
                bot.step()
                # запись живого результата (для раннего убийства при отрицательном счёте)
                if live_result_path and (not hasattr(bot, '_last_live_write') or time.time() - bot._last_live_write > 5.0):
                    bot._last_live_write = time.time()
                    try:
                        m = bot.metrics()
                        with open(live_result_path, 'w', encoding='utf-8') as f:
                            json.dump(m, f, ensure_ascii=False)
                    except Exception:
                        pass
            except Exception as e:
                log('ошибка: %r' % (e,))
                bot.stop_all()
                time.sleep(1.0)
            if once:
                break
            if t_end and time.time() >= t_end:
                break
            time.sleep(0.2 / speed)
    except KeyboardInterrupt:
        pass
    finally:
        inp.release()
        m = bot.metrics()
        log('итог эпизода: %s' % (m,))
        if result_path:
            try:
                with open(result_path, 'w', encoding='utf-8') as f:
                    json.dump(m, f, ensure_ascii=False, indent=2)
            except Exception as e:
                log('не смог записать результат: %r' % (e,))
        log('остановлено')


if __name__ == '__main__':
    main()

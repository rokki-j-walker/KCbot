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
from __future__ import annotations

import ctypes
import ctypes.wintypes as wt
import gamepath
import json
import kc_common
import os
import random
import re
import sys
import time

def _argv_value(name: str) -> str | None:
    """Значение аргумента командной строки вида `--name value`."""
    try:
        i = sys.argv.index(name)
        return sys.argv[i + 1]
    except (ValueError, IndexError):
        return None


# Папка игры задаётся лаунчером через --game-dir (сохраняется в настройках приложения).
GAME_DIR = _argv_value('--game-dir') or os.environ.get('KCBOT_GAME_DIR', '') or gamepath.game_dir()
KCDBG = os.path.join(GAME_DIR, 'Kingdom_Data', 'kcdbg.txt') if GAME_DIR else ''
SPEED_FILE = os.path.join(GAME_DIR, 'Kingdom_Data', 'speed.txt') if GAME_DIR else ''
HERE = os.path.dirname(os.path.abspath(__file__))
# Данные - в writable-каталог (см. kc_common.data_dir, аудит S2);
# bounties.txt остаётся ресурсом рядом со скриптом.
DATA = kc_common.data_dir()
LOGFILE = os.path.join(DATA, 'bot_log.txt')

# ---------------------------------------------------------------- Win32 ввод
user32 = ctypes.windll.user32

INPUT_KEYBOARD = 1
KEYEVENTF_SCANCODE = 0x0008
KEYEVENTF_KEYUP = 0x0002

SC_A = 0x1E
SC_D = 0x20
SC_S = 0x1F
SC_LSHIFT = 0x2A


class KEYBDINPUT(ctypes.Structure):
    _fields_ = [('wVk', wt.WORD), ('wScan', wt.WORD), ('dwFlags', wt.DWORD),
                ('time', wt.DWORD), ('dwExtraInfo', ctypes.POINTER(wt.ULONG))]


class _INPUTUNION(ctypes.Union):
    _fields_ = [('ki', KEYBDINPUT), ('pad', ctypes.c_byte * 32)]


class INPUT(ctypes.Structure):
    _anonymous_ = ('u',)
    _fields_ = [('type', wt.DWORD), ('u', _INPUTUNION)]


def _send(sc: int, up: bool = False) -> None:
    flags = KEYEVENTF_SCANCODE | (KEYEVENTF_KEYUP if up else 0)
    inp = INPUT(type=INPUT_KEYBOARD)
    inp.ki = KEYBDINPUT(0, sc, flags, 0, None)
    user32.SendInput(1, ctypes.byref(inp), ctypes.sizeof(INPUT))


class Input(object):
    """Клавиатура: удержание/отпускание + окно игры.

    dry=True - ничего не отправлять и не хватать фокус: бот считает решения,
    но ни одно нажатие не уходит ни в одно окно (раньше --dry шлёт A/D/Shift
    и проверку фокуса в dry пропускали - клавиши уходили в редактор/чат).
    """

    def __init__(self, dry: bool = False) -> None:
        self.held = set()
        self.hwnd = None
        self.dry = bool(dry)

    def find_window(self, title: str = 'Kingdom'):
        """Найти окно игры (единая реализация - kc_common.find_window).

        Здесь сохраняется только контракт Input: запомнить self.hwnd.
        Логика поиска (проверка процесса + выбор самого крупного видимого
        окна) вынесена в kc_common - раньше она была продублирована в шести
        файлах и разошлась.
        """
        self.hwnd = kc_common.find_window(title)
        return self.hwnd

    def is_focused(self) -> bool:
        return bool(self.hwnd) and user32.GetForegroundWindow() == self.hwnd

    def force_focus(self) -> bool:
        if self.dry:
            return False
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

    def down(self, sc: int) -> None:
        if self.dry:
            return
        if sc not in self.held:
            _send(sc, False)
            self.held.add(sc)

    def up(self, sc: int) -> None:
        if self.dry:
            return
        if sc in self.held:
            _send(sc, True)
            self.held.discard(sc)

    def release(self) -> None:
        for sc in list(self.held):
            self.up(sc)


# --------------------------------------------------------------- разбор лога
NUM = r'-?\d+(?:\.\d+)?'


class Payable(object):
    __slots__ = ('type', 'price', 'x', 'name', 'enabled')

    def __init__(self, type_: str, price: int, x: float,
                 name: str = '', enabled: bool = True) -> None:
        self.type = type_
        self.price = price
        self.x = x
        self.name = name or ''
        self.enabled = bool(enabled)

    @property
    def base(self) -> str:
        """Имя GameObject без '(Clone)' и хвостов: 'Castle5(Clone)' -> 'Castle5'."""
        n = self.name.split('(')[0].strip()
        return n

    def __repr__(self) -> str:
        return 'Payable(%s,%d,%.1f,%s)' % (self.type, self.price, self.x, self.name)


class State(object):
    def __init__(self) -> None:
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

    def cit(self, tag: str) -> list:
        return [(x, c) for (t, x, c) in self.citizens if t == tag]

    def statue(self, deity) -> tuple | None:
        for d, x, ch in self.statues:
            if d == deity:
                return (x, ch)
        return None

    def nearest_enemy(self, x: float):
        best = None
        bd = 1e9
        for (n, ex, atk) in self.enemy_list:
            d = abs(ex - x)
            if d < bd:
                bd = d
                best = (n, ex, atk, d)
        return best

    def pay_by_name(self, needle: str) -> Payable | None:
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


class Fit(object):
    """Накопитель метрик эпизода: явная схема вместо «супа» ключей (R2/Д27).

    Раньше 13 ключей создавалось лениво (.get(k, default) / 'k' not in f) -
    полный набор нигде не был объявлен, опечатка молча давала None/0.
    Все поля объявлены здесь и сгруппированы по доменам. as_flat() отдаёт
    плоский dict с ПРЕЖНИМИ именами ключей (эталон tracktest, отладка);
    формат metrics()/JSON-контракта bot3<->evo не меняется.
    """

    def __init__(self) -> None:
        # --- состояние трекера (не попадает в metrics/fitness) ---
        self.base = None                    # снапшот первого кадра (+arc0/wrk0)
        self.purse0 = None
        self.last_purse = 0
        self.last_px = None
        self.last_interaction = time.time()
        self.last_move_time = time.time()
        self.last_track_time = None
        self.last_stamina = 1.0
        self.start = time.time()
        self.inactivity_penalty = 0.0
        self.focus_pause_total = 0.0    # фокус потерян: метрики стоят
        self.paused_total = 0.0         # F8 + focus, не идут в seconds
        self.prev_day = None                # ленивые prev_* - теперь объявлены
        self.prev_enemies = None
        self.prev_portals = None
        self.prev_hc = 1
        self.prev_buildings = None
        self.prev_crown_x = None
        self.attacking_prev = False
        self.death_latch = False
        self.game_over_detected = False
        self.not_playing_streak = 0
        # --- монеты ---
        self.coins_in = 0.0
        self.coins_spent = 0.0
        self.coins_lost = 0
        self.unpicked_coins = 0
        # --- покупки ---
        self.buys = 0
        self.tool_buys = 0
        self.hammer_buys = 0
        self.bow_buys = 0
        self.scythe_buys = 0
        self.castle_base_buys = 0
        self.castle_base_buys_without_workers = 0
        self.castle_upgrade_buys = 0
        self.castle_upgrade_buys_with_workers = 0
        self.castle_upgrade_buys_without_workers = 0
        # --- здания и граждане ---
        self.buildings = 0
        self.buildings_started = 0
        self.buildings_completed = 0
        self.buildings_with_workers = 0
        self.buildings_without_workers = 0
        self.workers = 0
        self.archers = 0
        self.farmers = 0
        self.knights = 0
        self.max_buildings = 0
        self.citizens = 0
        # --- деревья ---
        self.trees_marked = 0
        self.trees_chopped = 0
        self.min_trees = 9999
        self.tree_chops_with_workers = 0
        self.tree_chops_without_workers = 0
        # --- корона и смерти ---
        self.crown_lost = 0
        self.crown_picked = 0
        self.deaths = 0
        # --- день и прогресс ---
        self.day = 1
        self.first_archer = 0
        self.first_worker = 0
        # --- дистанция ---
        self.distance = 0.0
        self.walk_distance = 0.0
        self.run_distance = 0.0
        # --- лошадь ---
        self.horse_exhaustion = 0
        self.horse_rest_time = 0.0
        # --- найм и отказы ---
        self.recruited_beggars = 0
        self.failed_recruit = 0
        self.failed_coin = 0
        self.failed_buy = 0
        self.failed_tree = 0

    def as_flat(self) -> dict:
        """Плоский dict с прежними именами ключей (эталон/отладка)."""
        return dict(self.__dict__)


def _f(s: str) -> float:
    try:
        return float(s)
    except ValueError:
        return 0.0



# --- обработчики секций KC| ----------------------------------------
# Тело каждой ветки перенесено из parse_line без изменений (сдвиг
# отступа на 8) - дифф-тест на реальных строк kcdbg подтверждает
# эквивалентность. Пороговый порядок задаёт _SECTION_PARSERS ниже.

def _sec_sel(st: State, val: str) -> None:
    if val and val != '-':
        mm = re.match(r'([A-Za-z0-9_]+),(-?\d+)@(-?[\d.]+)(?:~([^,]*))?', val)
        if mm:
            st.sel = Payable(mm.group(1), int(mm.group(2)),
                             float(mm.group(3)), (mm.group(4) or '').strip())


def _sec_ps(st: State, val: str) -> None:
    st.pay_state = val.strip()


def _sec_as(st: State, val: str) -> None:
    st.action = val.strip()


def _sec_crown(st: State, val: str) -> None:
    if val and val != '-':
        st.crown_x = _f(val.split(',')[0])


def _sec_gc(st: State, val: str) -> None:
    st.ground_coins = [_f(v) for v in val.split(',') if v.strip()]


def _sec_camps(st: State, val: str) -> None:
    st.camps = [_f(v) for v in val.split(',') if v.strip()]


def _sec_ports(st: State, val: str) -> None:
    for p in val.split(','):
        if ':' in p:
            a = p.split(':')
            st.portals.append((_f(a[0]), int(_f(a[1])),
                               int(_f(a[2])) if len(a) > 2 else 0))


def _sec_stat(st: State, val: str) -> None:
    for p in val.split(','):
        if '@' in p:
            a, _, b = p.partition('@')
            parts = b.split(':')
            st.statues.append((a.strip(), _f(parts[0]),
                               _f(parts[1]) if len(parts) > 1 else 0.0))


def _sec_tow(st: State, val: str) -> None:
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


def _sec_cit(st: State, val: str) -> None:
    for p in val.split(','):
        if '@' in p:
            a, _, b = p.partition('@')
            parts = b.split(':')
            st.citizens.append((a.strip(), _f(parts[0]),
                                int(_f(parts[1])) if len(parts) > 1 else 0))


def _sec_kng(st: State, val: str) -> None:
    for p in val.split(','):
        if ':' in p:
            a = p.split(':')
            st.knights.append((_f(a[0]), a[1] if len(a) > 1 else '?',
                                a[2] if len(a) > 2 else '?'))


def _sec_tool(st: State, val: str) -> None:
    for p in val.split(','):
        if '@' in p:
            a, _, b = p.partition('@')
            st.tools.append((a.strip(), _f(b)))


def _sec_walls(st: State, val: str) -> None:
    for p in val.split(','):
        if ':' in p:
            a = p.split(':')
            st.walls.append((_f(a[0]), int(_f(a[1])),
                             int(_f(a[2])) if len(a) > 2 else -1))


def _sec_farms(st: State, val: str) -> None:
    for p in val.split(','):
        if ':' in p:
            a = p.split(':')
            st.farms.append((_f(a[0]), a[1].strip() == 'True'))


def _sec_shops(st: State, val: str) -> None:
    for p in val.split(','):
        if '@' in p:
            a, _, b = p.partition('@')
            st.shops.append((a.strip(), _f(b)))


def _sec_pay(st: State, val: str) -> None:
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


def _sec_enm(st: State, val: str) -> None:
    for p in val.split(','):
        if '@' in p:
            a, _, b = p.partition('@')
            parts = b.split(':')
            st.enemy_list.append((a.strip(), _f(parts[0]),
                                  parts[1] == '1' if len(parts) > 1 else False))


def _kv_main(st: State, part: str) -> None:
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


def _kv_fallback(st: State, part: str) -> None:
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


_SECTION_PARSERS = {
    'sel': _sec_sel,
    'ps': _sec_ps,
    'as': _sec_as,
    'crown': _sec_crown,
    'gc': _sec_gc,
    'camps': _sec_camps,
    'ports': _sec_ports,
    'stat': _sec_stat,
    'tow': _sec_tow,
    'cit': _sec_cit,
    'kng': _sec_kng,
    'tool': _sec_tool,
    'walls': _sec_walls,
    'farms': _sec_farms,
    'shops': _sec_shops,
    'pay': _sec_pay,
    'enm': _sec_enm,
}


def parse_line(line: str) -> State | None:
    """Разобрать строку KC|... -> State или None.

    Диспетчер: именованные секции уходят в _SECTION_PARSERS,
    блок key=value без имени и неизвестные секции - в _kv_main /
    _kv_fallback. Логика веток не менялась (D27).
    """
    st = State()
    st.raw = line
    if not line.startswith('KC|'):
        return None
    for part in line[3:].split('|'):
        m = re.match(r'^([A-Za-z]+):(.*)$', part, re.S)
        name = val = None
        if m:
            name, val = m.group(1), m.group(2)
        fn = _SECTION_PARSERS.get(name)
        if fn is not None:
            fn(st, val)
        elif name is None or name in ('KC',):
            _kv_main(st, part)
        else:
            _kv_fallback(st, part)
    return st


KCDBG_MAX = 256 * 1024 * 1024   # 256 МБ
_last_cleanup = 0.0              # когда последний раз чистили лог
# Логгер KCLog5 пишет каждые 0.25 с. Если файл молчит дольше этого лимита -
# патч слётся, игра зависла или закрыта. Раньше бот брал последнюю СТАРУЮ строку
# и бесконечно действовал по несуществующему состоянию (шёл к мёртвой цели).
KCDBG_STALE = 5.0
_last_stale_warn = 0.0
_last_size = 0


def _cleanup_kcdbg(path: str) -> None:
    """Периодически чистим разросшийся kcdbg.txt (игра пишет в него бесконечно)."""
    global _last_cleanup
    now = time.time()
    if now - _last_cleanup < 5.0:
        return
    _last_cleanup = now
    try:
        if os.path.getsize(path) > KCDBG_MAX:
            with open(path, 'wb'):
                pass
            print('kcdbg.txt превысил лимит - очищен')
    except OSError:
        pass


def read_state(path: str = KCDBG) -> State | None:
    """Последняя строка KC| из лога, либо None если данных нет/они устарели."""
    global _last_stale_warn, _last_size
    _cleanup_kcdbg(path)
    try:
        size = os.path.getsize(path)
        age = time.time() - os.path.getmtime(path)
        with open(path, 'rb') as f:
            f.seek(max(0, size - 262144))
            data = f.read().decode('utf-8', 'replace')
    except OSError:
        return None
    # логгер молчит - не выдаём старое состояние за актуальное
    if size > 0 and age > KCDBG_STALE:
        if size != _last_size or time.time() - _last_stale_warn > 30.0:
            _last_stale_warn = time.time()
            _last_size = size
            print('!! kcdbg.txt не обновлялся %.1f с - патч слётся или игра '
                  'зависла; бот остановлен до появления данных' % age)
        return None
    _last_size = size
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
    flee_dist=28.0,
    keep_coins=1,           # 1 = оставлять 1 монету в запасе
    low_gold_threshold=5,   # если монет меньше этого - фармить деревья
    tax_min_coins=2,        # у кого брать налог (монет > этого)
    tax_wait=2.2,           # сколько стоять у налогоплательщика
    pay_tol=0.6,            # насколько близко встать к Payable (было 0.12 - бот слишком долго ёрзал)
    pay_giveup=7.0,         # сколько секунд искать точку оплаты, потом забить
                            # (согласовано с DEFAULT_GENOME: раньше здесь стояло
                            #  3.0, и одиночный запуск работал иначе, чем
                            #  эволюция с геномом - аудит B11 / решение D17)
    coin_timeout=5.0,       # сколько ждать монету, которую не подобрали
    coin_black_time=15.0,
    tree_prio=90,           # деревья - самый дешёвый доход
    sprint_min_dist=4.0,
    sprint_min_stamina=0.15,
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
    'flee_dist': 28.0,
    'pay_tol': 0.6,
    'pay_giveup': 7.0,
    'coin_timeout': 5.0,
    'sprint_min_dist': 4.0,
    'sprint_min_stamina': 0.15,
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


def save_genome(path: str, g: dict) -> None:
    """Атомарная запись генома (см. kc_common.atomic_json_dump)."""
    kc_common.atomic_json_dump(path, g)


def load_genome(path: str | None = None) -> dict:
    """Читает геном (JSON) и вливает его в настройки бота."""
    g = dict(DEFAULT_GENOME)
    path = path or os.path.join(DATA, 'genome.json')
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


def load_bounties(path: str | None = None) -> dict:
    """Прочитать bounties.txt -> dict ключ= float.

    Ошибки не молчат: битые строки и дубли ключей печатаются один раз,
    иначе правка в файле незаметно не применялась (аудит: 'coin_collect = 5,0'
    тихо терялся, дубли ключей перебивали первое вхождение).
    """
    path = path or BOUNTIES_FILE
    result = {}
    seen = {}
    errors = []
    try:
        with open(path, 'r', encoding='utf-8') as f:
            for lineno, raw in enumerate(f, 1):
                # отрезаем хвостовой комментарий: 'key = 1.0 # пояснение'
                line = raw.split('#', 1)[0].strip()
                if not line or '=' not in line:
                    continue
                key, _, val = line.partition('=')
                key = key.strip()
                val = val.strip()
                try:
                    num = float(val)
                except ValueError:
                    errors.append('%s:%d: %s = %r (не число, пропущено)'
                                  % (os.path.basename(path), lineno, key, val))
                    continue
                if key in seen:
                    errors.append('%s:%d: ключ %r продублирован '
                                  '(был в строке %d, перезаписан)'
                                  % (os.path.basename(path), lineno, key, seen[key]))
                seen[key] = lineno
                result[key] = num
    except OSError as e:
        errors.append('%s: %s (используются значения по умолчанию из кода)'
                      % (path, e))
    for msg in errors:
        print('!! bounties: %s' % msg)
    return result


BOUNTIES = load_bounties()


def fitness(m: dict) -> float:
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
    # бонус за инструменты как класс (экономика должна развиваться)
    s += b.get('tool_bonus', 0.0) * m.get('tool_buys', 0)
    # финальный состав экономики (не только первый юнит)
    s += b.get('worker_bonus', 0.0) * m.get('workers', 0)
    s += b.get('archer_bonus', 0.0) * m.get('archers', 0)
    s += b.get('farmer_bonus', 0.0) * m.get('farmers', 0)
    s += b.get('knight_bonus', 0.0) * m.get('knights', 0)
    # здания, построенные при наличии рабочих, ценнее «пустых»
    s += b.get('building_with_workers', 0.0) * m.get('buildings_with_workers', 0)
    # улучшение замка с рабочими поощряется, «голый» Castle0 — нет
    s += b.get('castle_upgrade_workers', 0.0) * m.get('castle_upgrade_buys_with_workers', 0)
    s += b.get('castle_base_no_workers', 0.0) * m.get('castle_base_buys_without_workers', 0)
    # неудачные попытки — штраф (раньше почти не влияли)
    s += b.get('failed_buy_penalty', 0.0) * m.get('failed_buy', 0)
    s += b.get('failed_recruit_penalty', 0.0) * m.get('failed_recruit', 0)
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
    # реальные рубки с рабочими (trees_chopped иногда не ловит исчезновение)
    s += b.get('tree_chop_workers', 0.0) * m.get('tree_chops_with_workers', 0)
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
    """Append-логгер бота с ротацией.

    Раньше файл рос неограниченно: запись с flush() на каждую строку, статус
    меняется каждые 0.2 с -> десятки МБ за сессию (аудит P1). Теперь при
    превышении MAX_BYTES текущий файл уезжает в bot_log.old.txt, а запись
    начинается заново.
    """

    MAX_BYTES = 20 * 1024 * 1024     # 20 МБ на один файл лога

    def __init__(self, path: str | None = None,
                 max_bytes: int | None = None) -> None:
        if path is None:
            path = LOGFILE
        self.path = path
        self.max_bytes = self.MAX_BYTES if max_bytes is None else max_bytes
        # создать директорию если не существует
        d = os.path.dirname(path)
        if d and not os.path.exists(d):
            os.makedirs(d, exist_ok=True)
        self.f = open(path, 'a', encoding='utf-8')
        try:
            self._written = os.path.getsize(path)
        except OSError:
            self._written = 0

    def __call__(self, *a) -> None:
        msg = ' '.join(str(x) for x in a)
        line = '[%s] %s\n' % (time.strftime('%H:%M:%S'), msg)
        try:
            self.f.write(line)
            self.f.flush()
            self._written += len(line.encode('utf-8', 'replace'))
            if self._written >= self.max_bytes:
                self._rotate()
        except Exception:
            pass
        print(msg, flush=True)

    def _rotate(self) -> None:
        """Закрыть текущий файл, сложить его в .old и начать новый."""
        try:
            self.f.close()
        except Exception:
            pass
        old = self.path + '.old'
        try:
            if os.path.exists(old):
                os.remove(old)
            os.replace(self.path, old)
        except OSError:
            pass
        try:
            self.f = open(self.path, 'w', encoding='utf-8')
        except OSError:
            self.f = open(os.devnull, 'w', encoding='utf-8')
        self._written = 0

    def close(self) -> None:
        try:
            self.f.close()
        except Exception:
            pass


# ---------------------------------------------------------------------- бот
class Bot(object):
    def __init__(self, dry: bool = False, inp: Input | None = None, log=None,
                 own_focus: bool = False, skip_tutorial: bool = False,
                 generation: int = 1, speed: float = 1.0) -> None:
        self.dry = dry
        self.inp = inp or Input(dry=dry)
        self.log = log or BotLog()
        self.own_focus = own_focus
        self.skip_tutorial = skip_tutorial
        self.generation = generation
        self.speed = max(0.1, speed)
        self.task = None
        self.last_recruit_drop = 0.0
        self.episode_end = None   # бюджет эпизода в ИГРОВЫХ секундах (для отображения оставшегося)
        self.pause_started = None  # начало текущей паузы (F8/фокус), если идёт
        # масштабируем временные константы под скорость симуляции
        self.coin_timeout = CFG['coin_timeout'] / self.speed
        self.pay_giveup = CFG['pay_giveup'] / self.speed
        self.tax_wait = CFG['tax_wait'] / self.speed
        self.recruit_check = 5.0 / self.speed
        self.esc_cooldown = 3.0 / self.speed
        self._tool_buy_until = 0.0   # пауза после покупки инструмента
        self.skip_cooldown = 30.0 / self.speed
        self.ts = {}
        self.last_focus = 0.0
        self.pay_target = None
        self.pay_phase = None
        self.pay_started = 0.0
        self.pay_purse0 = 0
        self.pay_purse_min = None
        self.pay_saw_completed = False
        self.pay_since = 0.0          # когда начали искать точку оплаты
        self._pay_last_px = None      # для проверки реальной остановки
        self._pay_still_since = 0.0
        self._recruit_check_time = 0.0
        self._recruit_check_p_count = 0
        self._retreat_until = 0.0
        self._retreat_x = 0.0
        self.scan_x = None            # текущая точка оплаты
        self.scan_i = 0
        self.scan_t = 0.0
        self.pay_black = {}           # неоплачиваемые объекты -> до какого времени
        self.coin_target = None
        self.coin_since = 0.0
        self.coin_purse = 0
        self.coin_black = {}
        self.tax_until = 0.0
        self.tax_target = None
        self.stat = {}
        self.skip_until = 0.0          # до какого времени держим S (скип обучения)
        self.last_skip = 0.0
        # неблокирующие нажатия (B14): клавиша -> время отпускания;
        # обрабатывается _release_due() в начале step()
        self._press_until = {}
        self._intro_skip_time = 0.0    # кулдаун кадра скипа обучения (day == 0)
        self._intro_hold = 0.0         # до какого времени держим S на интро
        self.fit = Fit()

    # ------------------------------------------------------------- утилиты
    def now(self) -> float:
        return time.time()

    def game_seconds(self) -> float:
        """Игровые секунды эпизода = (реальное время - паузы) × скорость.

        Time.timeScale = скорость, поэтому игровое время растёт в speed раз
        быстрее реального. Паузы (F8 и потеря фокуса) вычитаются: во время
        паузы симуляция стоит. Игровые секунды не зависят от скорости -
        на 1x и 3x эпизод живёт одинаковое игровое время, но разное реальное.
        """
        paused = self.fit.paused_total
        ps = getattr(self, 'pause_started', None)
        if ps is not None:
            # пауза ещё идёт - paused_total обновится только при возобновлении
            paused += time.time() - ps
        return max(0.0, time.time() - self.fit.start - paused) * self.speed

    def say(self, *a) -> None:
        self.log(*a)

    def note(self, key: str, val) -> None:
        if self.stat.get(key) != val:
            self.stat[key] = val
            score = getattr(self, '_last_score', None)
            # оставшееся время (если есть таймер) или прошедшее.
            # episode_end задан в игровых секундах - в консоль выводим
            # остаток РЕАЛЬНЫХ секунд (сколько ждать по часам).
            if self.episode_end is not None:
                remaining = max(0.0, self.episode_end - self.game_seconds()) \
                    / max(0.1, self.speed)
            else:
                remaining = time.time() - self.fit.start if hasattr(self, 'fit') else 0
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
        """Считаем метрики эпизода - по ним эволюция оценивает стратегию.

        Оркестратор: тело разбито на секции _track_* (рефакторинг D27).
        Порядок вызовов обязан совпадать с прежним порядком тела: секции
        читают и пишут общие поля self.fit (last_interaction, prev_hc и др.).
        """
        if self.fit.base is None:
            self._track_init(st)
            return
        if self._track_game_over(st):
            return
        d = self._track_coins(st)
        self._track_firsts(st)
        self._track_built(st)
        self._track_trees(st)
        self._track_distance(st)
        self._track_horse(st)
        self._track_world(st, d)
        self._track_recruit(st)
        self._track_idle_penalty()
        self._track_buildings(st)
        self._track_crown_death(st)

    # ------------------------------------------------------- секции track()
    def _track_init(self, st: State) -> None:
        """Первый кадр: базовые значения, по которым считаем все дельты."""
        f = self.fit
        f.base = dict(purse=st.purse, wall=st.wall, shop=st.shop,
                         farm=st.farm, portal=st.portal, arc=st.arc,
                         wrk=st.wrk, frm=st.frm, knt=st.knt, hc=st.hc,
                         p0=len(st.cit('P')))
        f.last_purse = st.purse
        f.last_px = st.px
        # стартовые здания и деревья не считаем как построенные/срубленные
        f.max_buildings = st.wall + st.shop + st.farm
        f.min_trees = len([p for p in st.payables if p.type == 'PayableTree'])

    def _track_game_over(self, st: State) -> bool:
        """Экран проигрыша (The Crown Lost): фиксируем смерть, метрики не трогаем.

        True - эпизод завершён, track() должен остановиться.
        """
        f = self.fit
        if not st.playing and st.hc == 0:
            f.not_playing_streak += 1
        else:
            f.not_playing_streak = 0
        if (f.not_playing_streak >= 3
                and self.now() - f.start > 3.0):
            if not f.game_over_detected:
                f.game_over_detected = True
                if not f.death_latch:
                    f.deaths = f.deaths + 1
                    f.death_latch = True
                self.note('world', 'ЭКРАН ПРОИГРЫША: The Crown Lost')
            return True
        return False

    def _track_coins(self, st: State) -> int:
        """День: смена суток, приток/траты/потери монет, монеты на земле.

        Возвращает d - изменение purse за кадр (его же ждёт _track_world
        при проверке налогов).
        """
        f = self.fit
        f.day = st.day
        d = st.purse - f.last_purse
        if d > 0:
            f.coins_in += d
            f.last_interaction = self.now()   # монета собрана = успех
        elif d < 0:
            f.coins_spent += -d
            # если не покупка — это потеря монеты (враг/переполнение)
            # монета, брошенная для найма бомжа, и оплата покупки - не потеря
            recruit_window = self.now() < getattr(self, '_recruit_drop_until', 0.0)
            # потеря от врага: только если враг атакует короля
            enemy_attacking = any(
                atk and abs(ex - st.px) < CFG['danger_dist']
                for (_n, ex, atk) in st.enemy_list)
            if self.pay_phase != 'hold' and not recruit_window and enemy_attacking:
                f.coins_lost = f.coins_lost + (-d)
            # таймер НЕ сбрасывается здесь — только при успешном действии
        f.last_purse = st.purse
        # пик монет на земле (это НЕ «промахи»; штраф из фитнеса убран)
        if len(st.ground_coins) > f.unpicked_coins:
            f.unpicked_coins = len(st.ground_coins)
        return d

    def _track_firsts(self, st: State) -> None:
        """Первый лучник и первый рабочий (оба фиксируются как успех)."""
        f = self.fit
        b = f.base
        if 'arc0' not in b:
            b['arc0'] = st.arc
            b['wrk0'] = st.wrk
        if b.get('arc0', 1) == 0 and st.arc >= 1 and f.first_archer == 0:
            f.first_archer = 1
            f.last_interaction = self.now()
        if b.get('wrk0', 1) == 0 and st.wrk >= 1 and f.first_worker == 0:
            f.first_worker = 1
            f.last_interaction = self.now()

    def _track_built(self, st: State) -> None:
        """Завершённое строительство: рост wall+shop+farm (max не считает перестройки)."""
        f = self.fit
        cur_buildings = st.wall + st.shop + st.farm
        max_buildings = f.max_buildings
        if cur_buildings > max_buildings:
            delta = cur_buildings - max_buildings
            f.buildings_completed = f.buildings_completed + delta
            f.max_buildings = cur_buildings
            self.note('world', 'построено %d зданий (wall=%d shop=%d farm=%d)' %
                      (delta, st.wall, st.shop, st.farm))
        elif max_buildings == 0:
            f.max_buildings = cur_buildings

    def _track_trees(self, st: State) -> None:
        """Срубленные деревья: дерево исчезло из payables (min не считает новые)."""
        f = self.fit
        cur_trees = len([p for p in st.payables if p.type == 'PayableTree'])
        min_trees = f.min_trees
        if cur_trees < min_trees and min_trees != 9999:
            chopped = min_trees - cur_trees
            f.trees_chopped = f.trees_chopped + chopped
            f.min_trees = cur_trees
            self.note('world', 'срублено %d деревьев (осталось %d)' % (chopped, cur_trees))
        elif min_trees == 9999:
            f.min_trees = cur_trees

    def _track_distance(self, st: State) -> None:
        """Пройденная дистанция: ходьба vs бег."""
        f = self.fit
        if f.last_px is not None:
            dist = abs(st.px - f.last_px)
            f.distance += dist
            # реальное движение, а не «просто прошло время»: иначе
            # штраф за бездействие получали боты, которые ехали к цели
            if dist > 0.3:
                f.last_move_time = self.now()
            if st.action == 'Run':
                f.run_distance = f.run_distance + dist
            else:
                f.walk_distance = f.walk_distance + dist
        f.last_px = st.px

    def _track_horse(self, st: State) -> None:
        """Лошадь: переутомление (стамина <= 0) и накопленное время отдыха."""
        f = self.fit
        if st.stamina <= 0.01 and f.last_stamina > 0.01:
            f.horse_exhaustion = f.horse_exhaustion + 1
        f.last_stamina = st.stamina
        # отдых лошади: если стамина низкая и бот не бежит
        threshold = BOUNTIES.get('horse_rest_threshold', 0.5)
        if st.stamina < threshold and st.action != 'Run':
            now = self.now()
            if f.last_track_time is not None:
                dt = now - f.last_track_time
                f.horse_rest_time = f.horse_rest_time + dt
            f.last_track_time = now
        else:
            f.last_track_time = self.now()

    def _track_world(self, st: State, d: int) -> None:
        """События мира: смена дня, враги, порталы, корона, налоги."""
        f = self.fit
        # смена дня
        if f.prev_day is None:
            f.prev_day = st.day
        elif st.day != f.prev_day:
            self.note('world', 'день %d -> %d (время=%.1f)' % (f.prev_day, st.day, st.time))
            f.prev_day = st.day
        # враги
        cur_enemies = len(st.enemy_list)
        if f.prev_enemies is None:
            f.prev_enemies = cur_enemies
        elif cur_enemies > f.prev_enemies:
            new_enemies = cur_enemies - f.prev_enemies
            names = ', '.join(e[0] for e in st.enemy_list[-new_enemies:])
            self.note('world', 'появилось %d врагов: %s' % (new_enemies, names))
        f.prev_enemies = cur_enemies
        # порталы
        if f.prev_portals is None:
            f.prev_portals = st.portal
        elif st.portal != f.prev_portals:
            if st.portal > f.prev_portals:
                self.note('world', 'открылся портал (всего %d)' % st.portal)
            else:
                self.note('world', 'портал закрыт (всего %d)' % st.portal)
            f.prev_portals = st.portal
        # корона (счётчики ниже, в _track_crown_death - здесь только заметки)
        if f.prev_hc == 1 and st.hc == 0:
            self.note('world', 'КОРОНА ПОТЕРЯНА! (purse=%d)' % st.purse)
        if f.prev_hc == 0 and st.hc == 1:
            self.note('world', 'корона подобрана')
        # налоги (purse увеличился от налога, не от монет на земле)
        if d > 0 and not any(abs(c - st.px) < 3 for c in st.ground_coins):
            if d > 1:
                self.note('world', 'налог собран: +%d монет (purse=%d)' % (d, st.purse))

    def _track_recruit(self, st: State) -> None:
        """Проверка найма: бросали монету, но Peasant не появился."""
        if getattr(self, '_recruit_check_time', 0) > 0 and self.now() > self._recruit_check_time:
            if len(st.cit('P')) > getattr(self, '_recruit_check_p_count', 0):
                # найм удался - сбрасываем таймер бездействия
                if hasattr(self, 'fit'):
                    self.fit.last_interaction = self.now()
                    self.fit.recruited_beggars = self.fit.recruited_beggars + 1
            else:
                # найм не удался - штраф
                if hasattr(self, 'fit'):
                    self.fit.failed_recruit = self.fit.failed_recruit + 1
            self._recruit_check_time = 0.0

    def _track_idle_penalty(self) -> None:
        """Штраф за бездействие: каждые 5 сек без движения И взаимодействия."""
        f = self.fit
        idle_time = self.now() - max(f.last_interaction, f.last_move_time)
        expected_penalty = int(idle_time / 5.0)
        if expected_penalty > f.inactivity_penalty:
            f.inactivity_penalty = expected_penalty

    def _track_buildings(self, st: State) -> None:
        """Дельта зданий (с рабочими/без них) и численность граждан."""
        f = self.fit
        b = f.base
        f.buildings = ((st.wall - b['wall']) + (st.shop - b['shop'])
                          + (st.farm - b['farm']) + (st.portal - b['portal']))
        # строительство засчитывается только если есть рабочие с молотками
        prev = f.prev_buildings
        if prev is None:
            f.prev_buildings = f.buildings
        else:
            delta = f.buildings - prev
            if delta > 0:
                if st.wrk >= 1:
                    f.buildings_with_workers = f.buildings_with_workers + delta
                else:
                    f.buildings_without_workers = f.buildings_without_workers + delta
            f.prev_buildings = f.buildings
        p_count = len(st.cit('P'))
        f.citizens = ((st.arc - b['arc']) + (st.wrk - b['wrk'])
                         + (st.frm - b['frm']) + (st.knt - b['knt'])
                         + (p_count - b['p0']))
        # итоговый состав экономики (для фитнеса: рабочие/лучники/фермеры)
        f.workers = max(0, st.wrk - b['wrk'])
        f.archers = max(0, st.arc - b['arc'])
        f.farmers = max(0, st.frm - b['frm'])
        f.knights = max(0, st.knt - b['knt'])

    def _track_crown_death(self, st: State) -> None:
        """Корона (потеря/подбор), фронт атаки врага и засчёт смерти."""
        f = self.fit
        prev_hc = f.prev_hc
        if prev_hc == 1 and st.hc == 0:
            f.crown_lost += 1
        if prev_hc == 0 and st.hc == 1:
            f.crown_picked = f.crown_picked + 1
        f.prev_hc = st.hc
        down = (st.hc == 0)
        if not down:
            f.death_latch = False
        # атака врага в радиусе 3 от короля: считаем именно начало атаки (фронт), а не каждый тик
        attacking_now = any(atk and abs(ex - st.px) < 3.0 for (_n, ex, atk) in st.enemy_list)
        onset = attacking_now and not f.attacking_prev
        f.attacking_prev = attacking_now
        # корона исчезла с земли, а король её не поднимал: унесли в портал
        crown_gone = f.prev_crown_x is not None and st.crown_x is None and down
        f.prev_crown_x = st.crown_x if down else None
        # смерть: корона уже была потеряна, монет нет и атаковали снова, либо корону унесли в портал
        if down and prev_hc == 0 and not f.death_latch:
            if (onset and st.purse == 0) or crown_gone:
                f.deaths = f.deaths + 1
                f.death_latch = True

    def metrics(self) -> dict:
        f = self.fit
        m = dict(coins_in=f.coins_in, coins_spent=f.coins_spent,
                 buys=f.buys, buildings=f.buildings,
                 citizens=f.citizens, crown_lost=f.crown_lost,
                 recruited_beggars=f.recruited_beggars,
                 day=f.day,
                 deaths=f.deaths,
                 crown_picked=f.crown_picked,
                 buildings_started=f.buildings_started,
                 buildings_completed=f.buildings_completed,
                 max_buildings=f.max_buildings,
                 min_trees=f.min_trees,
                 trees_marked=f.trees_marked,
                 trees_chopped=f.trees_chopped,
                 first_archer=f.first_archer,
                 first_worker=f.first_worker,
                 tool_buys=f.tool_buys,
                 hammer_buys=f.hammer_buys,
                 bow_buys=f.bow_buys,
                 scythe_buys=f.scythe_buys,
                 castle_base_buys=f.castle_base_buys,
                 castle_base_buys_without_workers=f.castle_base_buys_without_workers,
                 castle_upgrade_buys=f.castle_upgrade_buys,
                 castle_upgrade_buys_with_workers=f.castle_upgrade_buys_with_workers,
                 castle_upgrade_buys_without_workers=f.castle_upgrade_buys_without_workers,
                 buildings_with_workers=f.buildings_with_workers,
                 buildings_without_workers=f.buildings_without_workers,
                 workers=f.workers, archers=f.archers,
                 farmers=f.farmers, knights=f.knights,
                 distance=f.distance, walk_distance=f.walk_distance,
                 run_distance=f.run_distance,
                 horse_exhaustion=f.horse_exhaustion,
                 horse_rest_time=f.horse_rest_time,
                 final_purse=f.last_purse,
                 inactivity_penalty=f.inactivity_penalty,
                 tree_chops_with_workers=f.tree_chops_with_workers,
                 tree_chops_without_workers=f.tree_chops_without_workers,
                 failed_coin=f.failed_coin,
                 failed_buy=f.failed_buy,
                 failed_recruit=f.failed_recruit,
                 failed_tree=f.failed_tree,
                 unpicked_coins=f.unpicked_coins,
                 coins_lost=f.coins_lost,
                 focus_pause_time=f.focus_pause_total,
                 generation=self.generation,
                 seconds=self.game_seconds())
        m['score'] = fitness(m)
        return m

    # -------------------------------------------------------------- ввод
    def stop_move(self) -> None:
        self.inp.up(SC_A)
        self.inp.up(SC_D)
        self.inp.up(SC_LSHIFT)

    def release_s(self) -> None:
        self.inp.up(SC_S)

    def press_for(self, key: int, dur: float) -> None:
        """Нажать клавишу на dur секунд, НЕ блокируя цикл (B14).

        Раньше здесь был time.sleep(dur): цикл замирал целиком - F9 не
        отвечал, read_state не читался. Теперь отпускание планируется и
        выполняется в _release_due() в начале step().
        """
        if self.dry:
            return
        self.inp.down(key)
        self._press_until[key] = self.now() + dur

    def _release_due(self) -> None:
        """Отпустить клавиши, у которых вышло время (B14)."""
        if not self._press_until:
            return
        now = self.now()
        for key in [k for k, t in list(self._press_until.items()) if now >= t]:
            del self._press_until[key]
            self.inp.up(key)

    def stop_all(self) -> None:
        self.stop_move()
        self.release_s()

    def set_sprint(self, want: bool, st: State | None) -> None:
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

    def move_to(self, dx: float, sprint: bool = False,
                st: State | None = None) -> None:
        if dx < 0:
            self.inp.up(SC_D)
            self.inp.down(SC_A)
            self._last_move_dir = -1
        else:
            self.inp.up(SC_A)
            self.inp.down(SC_D)
            self._last_move_dir = 1
        self.set_sprint(sprint, st)

    def walk_to(self, st: State, x: float, sprint: bool = False,
                tol: float = 0.5) -> bool:
        """Идти к x. True - дошли."""
        dx = x - st.px
        if abs(dx) <= tol:
            self.stop_move()
            return True
        want_sprint = (sprint and abs(dx) > CFG['sprint_min_dist']
                       and st.stamina > CFG['sprint_min_stamina'])
        self.move_to(dx, want_sprint, st)
        return False

    def safe_sprint(self, st: State) -> bool:
        """Галоп - только если рядом нет врага."""
        n = st.nearest_enemy(st.px)
        return n is None or n[3] > 26.0

    # --------------------------------------------------------- приоритеты
    def buy_priority(self, st: State, p: Payable) -> float | None:
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

    def tool_priority(self, st: State, p: Payable) -> float | None:
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


    def best_buy(self, st: State, affordable_only: bool = True) -> tuple | None:
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

    def can_afford(self, st: State, p: Payable) -> bool:
        return st.purse >= p.price + (0 if p.type == 'PayableTree' else CFG['keep_coins'])

    # ============================================================ решение
    def blocked(self, p: Payable) -> bool:
        """Объект в чёрном списке (не оплачивается - например, дерево в глубине леса)."""
        key = '%s@%.0f' % (p.base, p.x)
        return self.pay_black.get(key, 0) > self.now()

    def decide(self, st: State) -> tuple:
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

        # много монет на земле - собираем, чтобы не копить штраф unpicked_coins
        if len(st.ground_coins) >= 5:
            t = self.coin_task(st)
            if t:
                return t

        # 2.5 первая база: без неё нет экономики и мастерских.
        #     Пока база не куплена, не тратим монеты на наём: иначе бот
        #     уходит в бомжей и покупает базу только к концу эпизода.
        if self.fit.castle_base_buys == 0:
            castle = None
            for p in st.payables:
                if p.type == 'PayableUpgrade' and 'castle0' in p.base.lower():
                    castle = p
                    break
            if castle is not None:
                if self.can_afford(st, castle) and not self.blocked(castle):
                    self.note('buy', '%s(%s) %d @%.1f' %
                              (castle.base, castle.type,
                               castle.price, castle.x))
                    return ('pay', castle)
                t = self.coin_task(st)
                if t:
                    return t
                t = self.tax_task(st)
                if t:
                    return t
                return ('idle', None)

        # 2.6 инструменты: если хватает монет - покупаем до найма.
        #     Наём съедал все монеты, поэтому рабочие/лучники почти не
        #     появлялись (прогон 3: 0.17 рабочего и 0.20 лучника).
        best_tool = None
        # после покупки инструмента ждём обновления st.wrk/arc: иначе бот
        # успевает купить второй такой же инструмент, пока первый не засчитан
        if self.now() >= self._tool_buy_until:
            for p in st.payables:
                if p.type != 'PayableShop' or self.blocked(p):
                    continue
                if not self.can_afford(st, p):
                    continue
                pr = self.tool_priority(st, p)
                if pr is None:
                    continue
                key = (pr, abs(p.x))
                if best_tool is None or key < best_tool[0]:
                    best_tool = (key, p)
        if best_tool is not None:
            p = best_tool[1]
            self.note('buy', '%s(%s) %d @%.1f' %
                      (p.base, p.type, p.price, p.x))
            return ('pay', p)

        # 2.7 первый инструмент: при 2 свободных крестьянах копим на него,
        #     а не тратим всё на новых бомжей.
        if (self.fit.tool_buys == 0 and len(st.cit('P')) >= 2
                and any(p.type == 'PayableShop' and not self.blocked(p)
                        and self.tool_priority(st, p) is not None
                        for p in st.payables)):
            t = self.coin_task(st)
            if t:
                return t
            t = self.tax_task(st)
            if t:
                return t
            return ('idle', None)

        # 3. наём бомжей (высокий приоритет - нужны граждане)
        t = self.recruit_task(st)
        if t:
            return t

        # 4. если золота мало - собираем налоги (приоритет)
        low_gold = st.purse < CFG.get('low_gold_threshold', 5) and st.purse >= 0
        if low_gold:
            t = self.tax_task(st)
            if t:
                return t
        # 5. покупка (если хватает монет) - рандом среди 3 лучших
        # НО: если золота мало и есть рабочие - фармим деревья
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

    def coin_task(self, st: State) -> tuple | None:
        """Монета на земле. Держимся одной цели, пока она не исчезнет."""
        now = self.now()
        # если цель ещё на земле - не переключаемся (иначе король дёргается между монетами)
        if self.coin_target is not None:
            still = any(abs(self.coin_target - c) < 0.6 for c in st.ground_coins)
            if still and self.coin_black.get(self.coin_target, 0) <= now:
                if now - self.coin_since > self.coin_timeout and st.purse <= self.coin_purse:
                    self.coin_black[self.coin_target] = now + CFG['coin_black_time']
                    self.note('coin_skip', '%.1f' % self.coin_target)
                    if hasattr(self, 'fit'):
                        self.fit.failed_coin = self.fit.failed_coin + 1
                    self.coin_target = None
                else:
                    return ('coin', self.coin_target)
            else:
                self.coin_target = None
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
        # берём ближайшую монету и фиксируем её
        c = candidates[0][1]
        self.coin_target = c
        self.coin_since = now
        self.coin_purse = st.purse
        return ('coin', c)

    def recruit_task(self, st: State) -> tuple | None:
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
        base_near = [b for b in beggars if abs(b[0] - st.px) < 3]  # монета падает у короля - бомж должен быть рядом
        if not base_near:
            # идём к бомжу, ближайшему к базе
            target = beggars_sorted[0]
            if abs(target[0] - st.px) > 80:
                return None
            return ('recruit_walk', target[0])
        # бомж рядом - бросаем монету к ближайшему к базе (только если sel is None)
        if st.sel is not None:
            return None   # не можем бросить - сначала уйти от Payable
        if self.now() - getattr(self, 'last_recruit_drop', 0) < 5.0 / self.speed:
            return None   # кулдаун
        nearest = min(base_near, key=lambda b: abs(b[0]))  # ближайший к базе
        return ('recruit_drop', nearest[0])

    def tax_task(self, st: State) -> tuple | None:
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

    def tree_task(self, st: State, goal) -> tuple | None:
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
        # (счётчик увеличивается ТОЛЬКО при успешной оплате — см. finish_pay;
        #  раньше он рос на каждом выборе цели и давал очки за попытки)
        return ('pay', t)

    # =========================================================== исполнение
    def execute(self, st: State) -> None:
        """Выполнить текущий task: диспетчер по его типу.

        Рефакторинг D27: ветки вынесены в _exec_*. Порядок и поведение
        прежние; неизвестный task, как и раньше, молча игнорируется.
        """
        # после броска монеты отходим, чтобы бомж успел её подобрать
        if self.now() < self._retreat_until:
            self.note('task', 'RECRUIT retreat -> %.1f' % self._retreat_x)
            self.release_s()
            self.walk_to(st, self._retreat_x, sprint=False, tol=1.0)
            return
        kind, payload = self.task
        if kind == 'idle':
            self._exec_idle(st)
        elif kind == 'recruit_walk':
            self._exec_recruit_walk(st, payload)
        elif kind == 'recruit_drop':
            self._exec_recruit_drop(st)
        elif kind == 'crown':
            self._exec_crown(st, payload)
        elif kind == 'flee':
            self._exec_flee(st, payload)
        elif kind == 'coin':
            self._exec_coin(st, payload)
        elif kind == 'tax':
            self._exec_tax(st, payload)
        elif kind == 'pay':
            self._exec_pay(st, payload)

    def _exec_idle(self, st: State) -> None:
        """Нет задач: к бомжу / к костру / к базе, иначе статус в консоль."""
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
        # нет задач - возвращаемся к базе (там спавн бомжей и налоги)
        if abs(st.px) > 5.0:
            self.note('task', 'IDLE -> base')
            self.walk_to(st, 0.0, sprint=False, tol=3.0)
            return
        # текущий счёт (для отображения в консоли)
        _m = self.metrics()
        _score = _m.get('score', 0)
        self.note('status', 'SCORE=%.1f | d%d t%.1f purse=%d arc=%d wrk=%d frm=%d knt=%d '
                            'wall=%d portal=%d en=%d' %
                   (_score, st.day, st.time, st.purse, st.arc, st.wrk, st.frm, st.knt,
                    st.wall, st.portal, st.enemies))

    def _exec_recruit_walk(self, st: State, payload) -> None:
        """Найм: подойти к бомжу перед броском монеты."""
        x = payload
        self.note('task', 'RECRUIT walk -> %.1f' % x)
        self.release_s()
        self.walk_to(st, x, sprint=False, tol=1.0)

    def _exec_recruit_drop(self, st: State) -> None:
        """Найм: бросить монету и отойти, чтобы бомж её подобрал."""
        self.note('task', 'RECRUIT drop coin @%.1f' % st.px)
        # нажатие без блокировки цикла (B14): отпускание планируется,
        # физически S отпустит _release_due() или release_s() в ретрите
        self.press_for(SC_S, 0.15 / self.speed)
        self.last_recruit_drop = self.now()
        self._recruit_drop_until = self.now() + 2.0
        self.stat['last_recruit'] = self.now()
        # запоминаем состояние до найма и проверяем, появился ли Peasant
        self._recruit_check_time = self.now() + self.recruit_check
        self._recruit_check_p_count = len(st.cit('P'))
        # не подбираем свою же монету
        self.coin_black[st.px] = self.now() + 4.0
        # монета падает назад от короля -> уходим ВПЕРЁД (по направлению движения)
        forward = 1.0 if getattr(self, '_last_move_dir', 1) >= 0 else -1.0
        self._retreat_x = st.px + forward * 4.0
        self._retreat_until = self.now() + 1.2

    def _exec_crown(self, st: State, payload) -> None:
        """Поднять корону."""
        self.note('task', 'CROWN @%.1f' % payload)
        self.release_s()
        self.walk_to(st, payload, sprint=self.safe_sprint(st), tol=1.0)

    def _exec_flee(self, st: State, payload) -> None:
        """Убежать от врага (дальше от него, через CFG['flee_dist'])."""
        name, ex, atk, d = payload
        away = 1 if st.px >= ex else -1
        target = st.px + away * CFG['flee_dist']
        self.note('task', 'FLEE %s@%.1f d=%.0f' % (name, ex, d))
        self.release_s()
        self.walk_to(st, target, sprint=True, tol=2.0)

    def _exec_coin(self, st: State, payload) -> None:
        """Подобрать монету на земле."""
        self.note('task', 'COIN @%.1f' % payload)
        self.release_s()
        self.walk_to(st, payload, sprint=self.safe_sprint(st), tol=0.6)

    def _exec_tax(self, st: State, payload) -> None:
        """Собрать налог с подданного (ждём CFG['tax_wait'], если он рядом)."""
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

    def _exec_pay(self, st: State, payload) -> None:
        """Оплатить Payable (здания/деревья/налоги) - см. do_pay()."""
        p = payload
        if p is None:
            return
        self.do_pay(st, p)

    # -------------------------------------------------------- оплата
    def same_payable(self, a: Payable | None, b: Payable | None) -> bool:
        if a is None or b is None:
            return False
        if a.type != b.type:
            return False
        if a.name and b.name:
            return a.name == b.name
        return abs(a.x - b.x) < 0.6

    def do_pay(self, st: State, p: Payable) -> None:
        """Оплата Payable: конечный автомат из фаз (рефакторинг D27).

        Фазы по порядку: удержание S -> смена цели -> подход -> остановка
        и кружки -> выбор объекта -> сканирование точек/отказ. Фаза,
        вернувшая True, останавливает кадр (как прежние ранние return).
        """
        now = self.now()
        if self.pay_phase == 'hold':
            self._pay_hold(st, p, now)
            return
        self._pay_switch_target(st, p, now)
        if self._pay_walk(st, p, now):
            return
        if self._pay_wait_circles(st, p, now):
            return
        if self._pay_select(st, p, now):
            return
        self._pay_scan(st, p, now)

    def _pay_hold(self, st: State, p: Payable, now: float) -> None:
        """Фаза 'hold': держим S, ждём конца транзакции или таймаута."""
        ps = st.pay_state
        # Успех нельзя определять только по pay_purse0 - st.purse: во время
        # оплаты бот может подобрать монеты, и тогда spent станет
        # отрицательным, хотя покупка прошла (баг: 106 из 112 pay_disabled
        # с spent<0). Игра пишет ps:Completed - запоминаем его.
        if ps == 'Completed':
            self.pay_saw_completed = True
        if (self.pay_purse_min is None
                or st.purse < self.pay_purse_min):
            self.pay_purse_min = st.purse
        if ps == 'None':
            # транзакция завершилась (или откатилась)
            if now - self.pay_started > 0.6:
                self.finish_pay(st, p)
            return
        if now - self.pay_started > self.pay_timeout(p.price):
            self.note('pay_timeout', '%s %d' % (p.base, p.price))
            self.finish_pay(st, p)
        return

    def _pay_switch_target(self, st: State, p: Payable, now: float) -> None:
        """Цель сменилась - сброс и поиск точки сначала (кадр не останавливает)."""
        if self.pay_target is not None and not self.same_payable(self.pay_target, p):
            self.finish_pay(st, self.pay_target)
        self.pay_target = p
        if self.scan_x is None:
            self.scan_x = p.x
            self.scan_i = 0
            self.scan_t = 0.0
            self.pay_since = now

    def _pay_walk(self, st: State, p: Payable, now: float) -> bool:
        """Идём к точке оплаты. True - ушли ходить, кадр завершён."""
        target = self.scan_x
        dx = target - st.px
        if abs(dx) > CFG['pay_tol']:
            self.release_s()
            self.note('task', 'PAY %s(%d) -> %.2f (px=%.2f)' %
                      (p.base, p.price, target, st.px))
            self.walk_to(st, target, sprint=False, tol=CFG['pay_tol'])
            return True
        return False

    def _pay_wait_circles(self, st: State, p: Payable, now: float) -> bool:
        """Полная остановка и ожидание кружков оплаты. True - ждём."""
        # полностью остановиться: кружки оплаты появляются только в покое
        self.stop_move()
        if self._pay_last_px is None or abs(st.px - self._pay_last_px) >= 0.05:
            # король ещё движется (инерция) - ждём
            self._pay_last_px = st.px
            self._pay_still_since = now
            return True
        if self._pay_still_since == 0.0:
            self._pay_still_since = now
        if now - self._pay_still_since < 0.5 / self.speed:
            self.note('task', 'PAY stop, ждём кружки %s' % p.base)
            return True
        # стоим неподвижно 0.5с - даём игре показать кружки
        if self.pay_since > 0 and now - self.pay_since < 1.2 / self.speed:
            return True
        return False

    def _pay_select(self, st: State, p: Payable, now: float) -> bool:
        """Объект выбран - держим S. True - кадр завершён."""
        if st.sel is None or not self.same_payable(st.sel, p):
            return False
        if st.purse <= 0:
            return True
        if st.pay_state != 'None':
            return True
        self.pay_phase = 'hold'
        self.pay_started = now
        self.pay_purse0 = st.purse
        self.pay_purse_min = st.purse
        self.pay_saw_completed = False
        self.note('task', 'PAY %s price=%d purse=%d' % (p.base, p.price, st.purse))
        if not self.dry:
            self.inp.down(SC_S)
        return True

    def _pay_scan(self, st: State, p: Payable, now: float) -> None:
        """Объект не выбирается: ищем точку рядом, либо сдаёмся (giveup)."""
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
                    self.fit.failed_tree = self.fit.failed_tree + 1
                else:
                    self.fit.failed_buy = self.fit.failed_buy + 1
            self.finish_pay(st, p)
        return

    def pay_timeout(self, price: int) -> float:
        return 0.5 + 0.35 * max(1, price) + 1.2

    def finish_pay(self, st: State, p: Payable) -> None:
        self.release_s()
        spent = max(0, self.pay_purse0 - st.purse)
        # Запасной признак: минимальный остаток за время удержания S. Если
        # после списания бот подобрал монеты, итоговый st.purse выше, но
        # минимум всё равно показывает списание.
        pmin = (self.pay_purse_min if self.pay_purse_min is not None
                else st.purse)
        spent_min = max(0, self.pay_purse0 - pmin)
        paid = (self.pay_saw_completed or spent >= p.price
                or spent_min >= p.price)
        # если монеты не списались — объект недоступен (условия не выполнены): бан на 60 с
        if not paid:
            key = '%s@%.0f' % (p.base, p.x)
            self.pay_black[key] = self.now() + 20.0
            self.note('pay_disabled', '%s @%.1f (spent=%d)' % (p.base, p.x, spent))
        # в лог не выводим отрицательное «потрачено»: при успешной покупке
        # показываем хотя бы цену (монеты, подобранные в транзакции, — доход)
        shown = max(p.price, spent) if paid else spent
        self.note('pay_done', '%s цена=%d потрачено=%d' % (p.base, p.price, shown))
        # успешная покупка: сбрасываем таймер бездействия
        if paid:
            if hasattr(self, 'fit'):
                self.fit.last_interaction = self.now()
        # отслеживаем покупки инструментов (только если успешно)
        if hasattr(self, 'fit') and paid:
            self.fit.buys = self.fit.buys + 1
            # отслеживаем начало строительства/рубки
            if 'Tree' in p.base:
                self.fit.trees_marked = self.fit.trees_marked + 1
                # успешная оплата дерева; рабочие рубят его быстрее
                if st.wrk >= 1:
                    self.fit.tree_chops_with_workers = (
                        self.fit.tree_chops_with_workers + 1)
                else:
                    self.fit.tree_chops_without_workers = (
                        self.fit.tree_chops_without_workers + 1)
            elif p.type == 'PayableUpgrade':
                self.fit.buildings_started = self.fit.buildings_started + 1
            low = p.base.lower()
            if 'castle0' in low:
                self.fit.castle_base_buys = self.fit.castle_base_buys + 1
                if st.wrk < 1:
                    self.fit.castle_base_buys_without_workers = (
                        self.fit.castle_base_buys_without_workers + 1)
            elif 'castle' in low:
                self.fit.castle_upgrade_buys = self.fit.castle_upgrade_buys + 1
                if st.wrk >= 1:
                    self.fit.castle_upgrade_buys_with_workers = self.fit.castle_upgrade_buys_with_workers + 1
                else:
                    self.fit.castle_upgrade_buys_without_workers = self.fit.castle_upgrade_buys_without_workers + 1
            if 'toolhammer' in low or 'hammer' in low:
                self.fit.hammer_buys = self.fit.hammer_buys + 1
                self.fit.tool_buys = self.fit.tool_buys + 1
            elif 'toolbow' in low or 'bow' in low:
                self.fit.bow_buys = self.fit.bow_buys + 1
                self.fit.tool_buys = self.fit.tool_buys + 1
            elif 'toolscythe' in low or 'scythe' in low:
                self.fit.scythe_buys = self.fit.scythe_buys + 1
                self.fit.tool_buys = self.fit.tool_buys + 1
            if 'hammer' in low or 'bow' in low or 'scythe' in low:
                self._tool_buy_until = self.now() + 3.0 / self.speed
        self.pay_target = None
        self.pay_phase = None
        self.pay_purse0 = 0
        self.pay_purse_min = None
        self.pay_saw_completed = False
        self.scan_x = None
        self.scan_i = 0
        self.scan_t = 0.0
        self.pay_since = 0.0
        self._pay_last_px = None
        self._pay_still_since = 0.0
        self._recruit_check_time = 0.0
        self._recruit_check_p_count = 0
        self._retreat_until = 0.0
        self._retreat_x = 0.0
        self.last_recruit_drop = 0.0
        # взаимодействие
        if hasattr(self, 'fit') and self.fit.last_interaction is not None:
            self.fit.last_interaction = self.now()

    # ============================================================== главный
    def ensure_focus(self) -> bool:
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

    def hotkey(self) -> str | None:
        """F8 - пауза, F9 - стоп бота. F12 обрабатывается evo.py."""
        if user32.GetAsyncKeyState(0x78) & 1:     # F9
            return 'quit'
        if user32.GetAsyncKeyState(0x77) & 1:     # F8
            return 'pause'
        return None

    def step(self, st: State | None = None) -> None:
        """Один такт бота.

        st можно передать извне: main() читает состояние один раз на такт.
        Раньше step() читал ЗАНОВО - два чтения kcdbg за такт (расход) и
        track/step могли увидеть РАЗНЫЕ кадры (аудит: двойное чтение).
        При st=None (внутренний вызов/восстановление) - читаем сами.
        """
        self._release_due()       # отпустить клавиши с вышедшим сроком (B14)
        if st is None:
            st = read_state()
        if st is None:
            # Клавиши обязательно отпускаем: иначе зажатые A/D/Shift продолжают
            # уходить в окно, пока логгер молчит (патч слётся / игра висит).
            self.stop_all()
            self.note('no_data', 1)
            return
        if not st.playing:
            self.stop_all()
            # если открыто меню настроек или другое меню - закрыть по Esc
            if not hasattr(self, "_esc_pressed_time"):
                self._esc_pressed_time = 0.0
            if self.now() - self._esc_pressed_time > self.esc_cooldown:
                # нажатие Enter без блокировки цикла (B14)
                self.press_for(0x1C, 0.1 / self.speed)   # SC_ENTER
                self._esc_pressed_time = self.now()
                self.note("esc_menu", "нажат Enter для закрытия меню/настроек")
            # скип обучения/интро: зажать S на 5 секунд (только если игра в фокусе
            # или бот в режиме эволюции, чтобы не мешать пользователю)
            # скип обучения (только если включён флаг --skip-tutorial)
            if self.skip_tutorial and self.now() - self.fit.start < 30.0:
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
            # Скип обучения/интро: подсказка на экране просит «нажать вниз»,
            # а «вниз» в этой игре = клавиша S. Достаточно 2-3 секунд
            # (проверено вживую; 5 с работает, но избыточно).
            # Удержание НЕ блокирует цикл (B14): stop_all() в начале ветки
            # отпускает S каждый кадр - держим его пере-нажатием, как skip_until.
            if not self.dry and st.day == 0:
                if self.now() < self._intro_hold:
                    self.inp.down(SC_S)            # продолжаем удержание
                elif self._intro_hold > 0.0:
                    self.inp.up(SC_S)              # время вышло (страховка)
                    self._intro_hold = 0.0
                elif self.now() - self._intro_skip_time > 5.0:
                    self._intro_skip_time = self.now()
                    self._intro_hold = self.now() + 3.0
                    self.inp.force_focus()
                    self.inp.down(SC_S)
                    self.note('intro_skip', 'удержание S для скипа обучения')
            self.note('not_playing', st.next)
            return

# перешли в игру - отпустить S, если держали
        if self.skip_until != 0.0:
            self.inp.up(SC_S)
            self.skip_until = 0.0
        if self._intro_hold != 0.0:
            self.inp.up(SC_S)
            self._intro_hold = 0.0
        new = self.decide(st)
        self.task = new
        # обновляем текущий счёт для вывода в консоль
        try:
            self._last_score = self.metrics().get('score', 0)
        except Exception:
            pass
        if not self.dry:
            if not self.ensure_focus():
                self.stop_all()
                return
        self.execute(st)


def main() -> None:
    kc_common.ensure_utf8_stdout()   # кириллица в пайпе = UTF-8, не cp1251
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
    # D7: потолок 3x. Раньше значение писалось в speed.txt как есть -
    # можно было сломать игру таймингами.
    if speed > 3.0:
        print('!! --speed ограничен 3x (на 4x+ игра ломается), было %s' % speed)
        speed = 3.0
    if speed < 1.0:
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
    if not GAME_DIR and not dry:
        print('!! папка игры не задана (--game-dir), запустите через лаунчер KC Bot')
        return
    if genome:
        load_genome(genome)
    inp = Input(dry=dry)
    log = BotLog(log_file) if log_file else BotLog()
    # записываем скорость в файл для игры (в dry-режиме не трогаем игру)
    if not dry:
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
    pause_started = None
    focus_paused = False
    focus_lost_at = 0.0
    last_force_focus = 0.0
    game_paused_by_us = False

    def _ensure_game_focus(timeout=3.0):
        """Для F8-продолжения: сначала фокус игры, потом ESC."""
        if inp.is_focused():
            return True
        try:
            inp.force_focus()
        except Exception as e:
            log('force_focus: %r' % (e,))
        end = time.time() + timeout
        while time.time() < end:
            if inp.is_focused():
                return True
            time.sleep(0.05)
        log('!! не удалось вернуть фокус игры')
        return False

    def _game_in_menu():
        """Игра на паузе/в меню? (не шлём лишний ESC)."""
        if dry:
            return False
        st = read_state()
        return bool(st is not None and not st.playing and st.hc > 0)

    def _game_not_playing():
        """Игра уже не идёт (меню или экран проигрыша) - ESC не нужен."""
        if dry:
            return False
        st = read_state()
        return bool(st is not None and not st.playing)

    def _tap_esc():
        """ESC с явным отпусканием: в паузе step() не крутится."""
        if dry:
            return
        inp.down(0x01)
        time.sleep(0.08)
        inp.up(0x01)

    # Бюджет эпизода - в ИГРОВЫХ секундах: реальная длительность = episode/speed.
    t_end = episode if episode else None
    bot.episode_end = t_end
    try:
        while True:
            try:
                hk = bot.hotkey()
                if hk == 'quit':
                    log('стоп по F9')
                    break
                if hk == 'pause':
                    if not paused:
                        paused = True
                        pause_started = time.time()
                        bot.pause_started = pause_started
                        inp.release()
                        if _game_not_playing():
                            log('ПАУЗА (игра уже не идёт — ESC не нужен)')
                        elif _ensure_game_focus():
                            # ESC уходит в активное окно, поэтому сначала фокус.
                            if _game_not_playing():
                                log('ПАУЗА (игра уже не идёт — ESC не нужен)')
                            else:
                                _tap_esc()
                                game_paused_by_us = True
                                log('ПАУЗА (ESC: игра остановлена)')
                        else:
                            log('ПАУЗА (фокус не вернулся — ESC не отправлен)')
                    else:
                        if _ensure_game_focus():
                            if _game_in_menu():
                                _tap_esc()
                                log('ПРОДОЛЖАЮ (ESC: снимаю паузу игры)')
                            elif game_paused_by_us and read_state() is None:
                                # Логгер молчит во время паузы/перехода: мы сами
                                # поставили игру на ESC, значит снимаем её сами.
                                _tap_esc()
                                log('ПРОДОЛЖАЮ (логгер молчит — снимаю паузу)')
                            else:
                                log('ПРОДОЛЖАЮ (игра не в меню — ESC не нужен)')
                        else:
                            log('ПРОДОЛЖАЮ без ESC: фокус не вернулся')
                        game_paused_by_us = False
                        extra = 0.0
                        if pause_started is not None:
                            extra = time.time() - pause_started
                            pause_started = None
                            # t_end в игровых секундах не продлеваем: игровые
                            # часы и так стоят во время паузы (game_seconds()).
                            bot.fit.paused_total += extra
                        bot.pause_started = None
                        paused = False
                        # пауза не должна превращаться в «бездействие»
                        try:
                            bot.fit.last_move_time = time.time()
                            bot.fit.last_interaction = time.time()
                        except Exception:
                            pass
                        log('ПРОДОЛЖАЮ (+%.1f с к эпизоду)' % extra)
                if paused:
                    # записываем live-результат с флагом паузы, чтобы evo.py не считал время паузы в таймаут
                    if live_result_path and (not hasattr(bot, '_last_live_write') or time.time() - bot._last_live_write > 2.0):
                        bot._last_live_write = time.time()
                        try:
                            m = bot.metrics()
                            m['paused'] = True
                            with open(live_result_path, 'w', encoding='utf-8') as f:
                                json.dump(m, f, ensure_ascii=False)
                        except Exception:
                            pass
                    time.sleep(0.2 / speed)
                    continue
                # --- потеря фокуса игрой: сон, метрики стоят ---
                if not dry and not inp.is_focused():
                    now = time.time()
                    if not focus_paused:
                        focus_paused = True
                        focus_lost_at = now
                        bot.pause_started = focus_lost_at
                        inp.release()
                        log('ФОКУС ПОТЕРЯН — пауза (жду возврата)')
                    if (focus_lost_at and now - focus_lost_at >= 300.0
                            and now - last_force_focus > 5.0):
                        last_force_focus = now
                        log('ФОКУС НЕ ВОЗВРАЩАЛСЯ 5 МИН — возвращаю принудительно')
                        try:
                            inp.force_focus()
                        except Exception as e:
                            log('force_focus: %r' % (e,))
                    if live_result_path and (not hasattr(bot, '_last_live_write')
                                             or now - bot._last_live_write > 2.0):
                        bot._last_live_write = now
                        try:
                            m = bot.metrics()
                            m['paused'] = True
                            m['focus_paused'] = True
                            with open(live_result_path, 'w', encoding='utf-8') as f:
                                json.dump(m, f, ensure_ascii=False)
                        except Exception:
                            pass
                    time.sleep(0.2 / speed)
                    continue
                if focus_paused:
                    lost = time.time() - focus_lost_at
                    focus_paused = False
                    focus_lost_at = 0.0
                    bot.fit.focus_pause_total += lost
                    bot.fit.paused_total += lost
                    # t_end в игровых секундах не продлеваем: пауза уже
                    # вычтена из игровых часов (game_seconds()).
                    bot.pause_started = None
                    bot.fit.last_move_time = time.time()
                    bot.fit.last_interaction = time.time()
                    log('ФОКУС ВОЗВРАЩЁН (+%.1f с к эпизоду)' % lost)
                st = read_state()
                if st is not None:
                    if bot.track(st):
                        # Экран проигрыша: track() уже зафиксировал смерть.
                        # Раньше цикл игнорировал возврат и продолжал слать
                        # ввод на экране «The Crown Lost» (жалоба пользователя).
                        log('игра окончена (The Crown Lost) - остановка')
                        break
                bot.step(st)     # тот же кадр, что у track (без2-го чтения)
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
            if t_end and bot.game_seconds() >= t_end:
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

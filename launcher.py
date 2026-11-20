# -*- coding: utf-8 -*-
"""
launcher.py - лаунчер обучения KingdomBot.

Запуск:
    py launcher.py
"""
import collections
import ctypes
import gamepath
import json
import kc_common
import os
import patcher
import queue
import subprocess
import sys
import threading
import tkinter as tk
from tkinter import ttk

# Дизайн-библиотека (решение по дизайну 2026-11-21): современные виджеты
# со своим DPI-масштабированием - убирает «лесенки» дробного tk scaling.
import customtkinter as ctk

try:
    import sv_ttk
    HAS_SV_TTK = True
except ImportError:
    HAS_SV_TTK = False

# matplotlib больше не используется: график эволюции убран (решение D3),
# прогресс показывается числом в статусбаре.

if getattr(sys, 'frozen', False):
    HERE = getattr(sys, '_MEIPASS', os.path.dirname(sys.executable))
else:
    HERE = os.path.dirname(os.path.abspath(__file__))

# ---------- Локализация ----------
LOCALE_DIR = os.path.join(HERE, 'locales')
_CURRENT_LOCALE = {}


def load_locale(lang):
    global _CURRENT_LOCALE
    path = os.path.join(LOCALE_DIR, '%s.json' % lang)
    if not os.path.exists(path):
        path = os.path.join(LOCALE_DIR, 'ru.json')
    try:
        with open(path, encoding='utf-8') as f:
            _CURRENT_LOCALE = json.load(f)
    except Exception:
        _CURRENT_LOCALE = {}
    return _CURRENT_LOCALE


def t(key, **kwargs):
    val = _CURRENT_LOCALE.get(key, key)
    try:
        return val.format(**kwargs) if kwargs else val
    except Exception:
        return val


def t_raw(key):
    """Перевод БЕЗ .format() - для многострочных текстов, в которых могут
    встретиться фигурные/угловые скобки (справка, шаблоны путей)."""
    return _CURRENT_LOCALE.get(key, key)


# Загрузить локаль по умолчанию (из конфига подгрузится позже в App)
load_locale('ru')
APPDATA_DIR = os.path.join(os.environ.get('APPDATA', os.path.expanduser('~')), 'KCbot')
CONFIG_FILE = os.path.join(APPDATA_DIR, 'launcher_config.json')
HISTORY_FILE = os.path.join(kc_common.data_dir(), 'data', 'evo_history.json')

RESTART_OVERHEAD = 30.0

# Минимальная популяция для нормального обучения (генетический алгоритм)
def apply_taskbar_style(win):
    """Показать окно (включая overrideredirect) в панели задач Windows."""
    try:
        win.update_idletasks()
        hwnd = ctypes.windll.user32.GetAncestor(win.winfo_id(), 2)  # GA_ROOT
        if not hwnd:
            hwnd = win.winfo_id()
        GWL_EXSTYLE = -20
        WS_EX_APPWINDOW = 0x00040000
        WS_EX_TOOLWINDOW = 0x00000080
        try:
            get_long = ctypes.windll.user32.GetWindowLongPtrW
            set_long = ctypes.windll.user32.SetWindowLongPtrW
        except AttributeError:
            get_long = ctypes.windll.user32.GetWindowLongW
            set_long = ctypes.windll.user32.SetWindowLongW
        ctypes.windll.user32.ShowWindow(hwnd, 0)  # SW_HIDE
        style = get_long(hwnd, GWL_EXSTYLE)
        style = (style & ~WS_EX_TOOLWINDOW) | WS_EX_APPWINDOW
        set_long(hwnd, GWL_EXSTYLE, style)
        ctypes.windll.user32.ShowWindow(hwnd, 5)  # SW_SHOW
        ctypes.windll.user32.SetWindowPos(hwnd, 0, 0, 0, 0, 0,
                                         0x0002 | 0x0001 | 0x0004 | 0x0020 | 0x0040)
        try:
            icon_path = os.path.join(HERE, 'assets', 'kc_bot.ico')
            LR_LOADFROMFILE = 0x00000010
            IMAGE_ICON = 1
            hicon = ctypes.windll.user32.LoadImageW(None, icon_path, IMAGE_ICON, 0, 0, LR_LOADFROMFILE)
            if hicon:
                WM_SETICON = 0x0080
                ctypes.windll.user32.SendMessageW(hwnd, WM_SETICON, 0, hicon)
                ctypes.windll.user32.SendMessageW(hwnd, WM_SETICON, 1, hicon)
        except Exception:
            pass
    except Exception:
        pass


def setup_scrollbar_style():
    try:
        import tkinter.ttk as _ttk
        _style = _ttk.Style()
        cfg = load_config()
        theme_name = cfg.get('theme', 'dark')
        colors = COLOR_SCHEMES[resolve_theme(theme_name)]
        bg = colors.get('entry_bg', '#252525')
        fg = colors.get('fg', '#e0e0e0')
        trough = colors.get('bg', '#1a1a1a')
        hover = colors.get('btn_hover', bg)
        accent = colors.get('accent', bg)
        for orient in ('Vertical', 'Horizontal'):
            _style.configure('%s.TScrollbar' % orient,
                            background=bg, troughcolor=trough,
                            bordercolor=trough, arrowcolor=fg, width=12)
            _style.map('%s.TScrollbar' % orient,
                       background=[('active', hover), ('pressed', accent)])
    except Exception:
        pass

os.makedirs(APPDATA_DIR, exist_ok=True)

DEFAULTS = {
    'pop': 50,
    'rounds': 15,
    'episode': 20.0,
    'episode_increment': 5.0,
    'speed': 3.0,           # D7: потолок 3x, на 4x+ игра ломается
    'top_percent': 0.1,
    'restart': True,
    'launch_after': False,
    'theme': 'dark',
    'console_content': 'both',
    'show_console': True,
    'show_params': True,
    'show_detail': True,
    'detail_side': 'left',
    'console_side': 'right',
    'detail_detached': False,
    'console_detached': False,
    'language': 'ru',
}

def system_base_theme():
    """'dark'/'light' по системной настройке Windows.

    Реестр HKCU\\Software\\Microsoft\\Windows\\CurrentVersion\\Themes\\Personalize
    -> AppsUseLightTheme (0 = тёмная). Повторяет ThemeService.ResolveSystemBase
    из старой WPF-версии (решение D29).
    """
    try:
        import winreg
        with winreg.OpenKey(
                winreg.HKEY_CURRENT_USER,
                r'Software\Microsoft\Windows\CurrentVersion\Themes\Personalize') as k:
            v = winreg.QueryValueEx(k, 'AppsUseLightTheme')[0]
            return 'light' if v else 'dark'
    except Exception:
        return 'dark'


def resolve_theme(name):
    """Развернуть выбранную тему в конкретную схему цветов.

    'system' не лежит в COLOR_SCHEMES - он разворачивается в 'dark'/'light'
    по настройке Windows в момент применения.
    """
    if name == 'system':
        return system_base_theme()
    return name if name in COLOR_SCHEMES else 'dark'


def is_light_theme(name):
    """Светлая ли итоговая схема (для sv_ttk и контрастов)."""
    return resolve_theme(name) in ('light', 'white', 'light_blue', 'light_green')


COLOR_SCHEMES = {
    'dark': {
        'bg': '#1a1a1a', 'fg': '#e0e0e0', 'entry_bg': '#252525',
        'accent': '#4CAF50', 'graph_bg': '#202020', 'graph_fg': '#e0e0e0',
        'title_bg': '#252525', 'btn_hover': '#333333', 'border': '#333333',
        'panel_bg': '#222222',
    },
    'light': {
        'bg': '#f0f0f0', 'fg': '#1e1e1e', 'entry_bg': '#ffffff',
        'accent': '#0078D4', 'graph_bg': '#ffffff', 'graph_fg': '#1e1e1e',
        'title_bg': '#e0e0e0', 'btn_hover': '#d0d0d0', 'border': '#cccccc',
        'panel_bg': '#f5f5f5',
    },
    'blue': {
        'bg': '#0d1b2a', 'fg': '#e0e1dd', 'entry_bg': '#1b263b',
        'accent': '#3a86ff', 'graph_bg': '#1b263b', 'graph_fg': '#e0e1dd',
        'title_bg': '#1b263b', 'btn_hover': '#2a3a5a', 'border': '#2a3a5a',
        'panel_bg': '#152238',
    },
    'green': {
        'bg': '#0a1f0a', 'fg': '#d4e8d4', 'entry_bg': '#1a2f1a',
        'accent': '#52b788', 'graph_bg': '#1a2f1a', 'graph_fg': '#d4e8d4',
        'title_bg': '#1a2f1a', 'btn_hover': '#2a3f2a', 'border': '#2a3f2a',
        'panel_bg': '#152a15',
    },
    'white': {
        'bg': '#ffffff', 'fg': '#000000', 'entry_bg': '#f5f5f5',
        'accent': '#000000', 'graph_bg': '#ffffff', 'graph_fg': '#000000',
        'title_bg': '#f0f0f0', 'btn_hover': '#e0e0e0', 'border': '#e0e0e0',
        'panel_bg': '#fafafa',
    },
    'black': {
        'bg': '#000000', 'fg': '#ffffff', 'entry_bg': '#111111',
        'accent': '#ffffff', 'graph_bg': '#000000', 'graph_fg': '#ffffff',
        'title_bg': '#111111', 'btn_hover': '#222222', 'border': '#222222',
        'panel_bg': '#0a0a0a',
    },
    # Светлые с акцентами Fluent 2 (D29): нейтрали взяты из ThemeService.cs
    # старой WPF-версии (paper/card/body/divider/border).
    'light_blue': {
        'bg': '#f5f5f5', 'fg': '#1a1a1a', 'entry_bg': '#ffffff',
        'accent': '#0067C0', 'graph_bg': '#ffffff', 'graph_fg': '#1a1a1a',
        'title_bg': '#e0e0e0', 'btn_hover': '#e8e8e8', 'border': '#c4c4c4',
        'panel_bg': '#ffffff',
    },
    'light_green': {
        'bg': '#f5f5f5', 'fg': '#1a1a1a', 'entry_bg': '#ffffff',
        'accent': '#107C10', 'graph_bg': '#ffffff', 'graph_fg': '#1a1a1a',
        'title_bg': '#e0e0e0', 'btn_hover': '#e8e8e8', 'border': '#c4c4c4',
        'panel_bg': '#ffffff',
    },
}

# Имя темы -> ключ локали. 'system' разворачивается в dark/light по реестру.
THEME_KEYS = {
    'system': 'theme_system',
    'dark': 'theme_dark',
    'light': 'theme_light',
    'light_blue': 'theme_light_blue',
    'light_green': 'theme_light_green',
    'blue': 'theme_blue',
    'green': 'theme_green',
    'white': 'theme_white',
    'black': 'theme_black',
}


# ---------- Шрифты и цвета карточек (Fluent, целые размеры) ----------
_FONT_CACHE = {}


def F(size, weight='normal', mono=False):
    """Кэшированный шрифт: Segoe UI / Consolas, только целые размеры.

    Дробные размеры при ручном tk scaling давали «лесенки» - CTk сам
    масштабирует по DPI, размеры задаём как есть.
    """
    key = (size, weight, mono)
    if key not in _FONT_CACHE:
        _FONT_CACHE[key] = ctk.CTkFont(family='Consolas' if mono else 'Segoe UI',
                                       size=size, weight=weight)
    return _FONT_CACHE[key]


def FT(size, family='Consolas'):
    """Шрифт для tk-виджетов (Text) с поправкой на масштаб Windows.

    CTk масштабирует свои шрифты сам, tk - нет: без поправки текст в
    tk.Text был мельче остального интерфейса при масштабе125%.
    """
    return (family, max(8, int(round(size * win_ui_scale()))))


def card_color(colors):
    """Фон карточки: на ступень светлее/темнее фона окна (Fluent surface)."""
    return colors.get('card') or colors['entry_bg']


# ---------- Дизайн-токены: ОДИН размер на класс элементов ----------
# (жалобы: «везде разные размеры кнопок, разные размеры шрифтов»;
#  эталон структуры - MSI Center: крупный заголовок секции, единые
#  высоты контролов, ровные отступы)
H_HEADER = 48        # высота шапки окна (кнопки шапки = во всю высоту)
H_BTN = 40           # высота ВСЕХ содержательных кнопок
H_INPUT = 40         # высота полей ввода (= кнопке - строкы выровнены)
FS_TITLE = 18        # название окна
FS_SECTION = 15      # заголовок раздела (Параметры, Детали, Папка игры)
FS_BODY = 13         # подписи, переключатели, кнопки, статус-кнопки
FS_CAPTION = 11      # комментарии под полями, статусы
FS_VALUE = 16        # значение в поле (моно, вправо)
FS_MONO = 13         # моно-тела: результат, консоль, панель деталей
PAD_CARD = 16        # внутренний отступ карточек/разделов
GAP_SECTION = 16     # вертикальный ритм между блоками


def _self_shot(path):
    """Снять ОКНО САМОГО приложения изнутри (отладочный хук).

    Внешние захватчики на двухмониторной/DPI-конфигурации ловили не то
    окно; Tk в своём процессе знает свои координаты физически.
    Включается переменной окружения KCBOT_UI_SHOT=<путь.png>.
    """
    try:
        from PIL import ImageGrab
        root = _self_shot.root
        root.update_idletasks()
        x, y = root.winfo_rootx(), root.winfo_rooty()
        w, h = root.winfo_width(), root.winfo_height()
        if x >= 0 and y >= 0:
            img = ImageGrab.grab(bbox=(x, y, x + w, y + h), all_screens=False)
        else:
            img = ImageGrab.grab(bbox=(x, y, x + w, y + h), all_screens=True)
        img.save(path)
        print('UI-SHOT saved: %s %s' % (path, img.size), flush=True)
    except Exception as e:
        print('UI-SHOT failed: %r' % (e,), flush=True)


def _shade(hex_color, factor):
    """Затемнить #RRGGBB на factor (<1 - темнее) для hover-состояний."""
    h = hex_color.lstrip('#')
    try:
        r, g, b = (int(h[i:i + 2], 16) for i in (0, 2, 4))
    except (ValueError, IndexError):
        return hex_color
    return '#%02x%02x%02x' % tuple(
        max(0, min(255, int(v * factor))) for v in (r, g, b))


def win_ui_scale():
    """Масштаб интерфейса Windows из реестра (125% -> 1.25).

    GetDpiForSystem в процессе без DPI-awareness честно возвращает96 -
    врёт; AppliedDPI в HKCU всегда отражает выбранный пользователем
    масштаб. Читаем его и подстраиваем CTk/геометрию (жалоба: «при125%
    не всё влезает»).
    """
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER,
                            r'Control Panel\Desktop\WindowMetrics') as k:
            applied, _type = winreg.QueryValueEx(k, 'AppliedDPI')
        if applied:
            return applied / 96.0
    except Exception:
        pass
    try:
        dpi = ctypes.windll.user32.GetDpiForSystem()
        if dpi:
            return dpi / 96.0
    except Exception:
        pass
    return 1.0


def _work_area():
    """Рабочая область экрана (без панели задач) в px: (ширина, высота)."""
    class _RECT(ctypes.Structure):
        _fields_ = [('left', ctypes.c_long), ('top', ctypes.c_long),
                    ('right', ctypes.c_long), ('bottom', ctypes.c_long)]
    r = _RECT()
    try:
        # SPI_GETWORKAREA =0x0030
        ctypes.windll.user32.SystemParametersInfoW(0x0030, 0,
                                                   ctypes.byref(r), 0)
        return r.right - r.left, r.bottom - r.top
    except Exception:
        return 1920, 1040


# Скорость: показ «1x/2x/3x», хранение «1/2/3» (как в конфиге/CLI)
SPEED_DISPLAY = {'1': '1x', '2': '2x', '3': '3x'}
SPEED_REVERSE = {v: k for k, v in SPEED_DISPLAY.items()}
SPEED_VALUES = ['1x', '2x', '3x']


def _norm_int_str(value):
    """'3.0' / 3 / '3' -> '3' (конфиг хранит speed float)."""
    try:
        return str(int(float(str(value))))
    except (ValueError, TypeError):
        return str(value)


class _OptionField(object):
    """CTkOptionMenu с интерфейсом Entry (get/insert/delete/configure/bind).

    Поле «Скорость» теперь список (решение по концепции), но живёт в
    self.entries как обычная строка ввода - get_params/_validate_all/
    _reset_defaults работают без переделки.
    """

    def __init__(self, menu, var, display=None, reverse=None):
        self.menu = menu
        self.var = var
        self._d = display or {}
        self._r = reverse or {}

    def get(self):
        v = self.var.get().strip()
        return self._r.get(v, v)

    def insert(self, index, value):          # сброс параметров
        self.var.set(self._d.get(_norm_int_str(value),
                                self._d.get(str(value), str(value))))

    def delete(self, first, last=None):      # insert сразу перезапишет
        pass

    def configure(self, **kw):               # валидация пытается красить поле
        allowed = {}
        for k in ('fg_color', 'button_color', 'button_hover_color',
                  'text_color', 'state'):
            if k in kw:
                allowed[k] = kw[k]
        if allowed:
            try:
                self.menu.configure(**allowed)
            except Exception:
                pass

    def bind(self, *args, **kwargs):         # FocusOut/Return не нужны
        pass


def load_config():
    old_config = os.path.join(kc_common.data_dir(), 'data', '.launcher_config.json')
    if not os.path.exists(CONFIG_FILE) and os.path.exists(old_config):
        try:
            import shutil
            shutil.copy2(old_config, CONFIG_FILE)
        except Exception:
            pass
    try:
        with open(CONFIG_FILE, 'r', encoding='utf-8') as f:
            data = json.load(f)
        for k, v in DEFAULTS.items():
            data.setdefault(k, v)
        return data
    except Exception:
        data = dict(DEFAULTS)
        try:
            with open(CONFIG_FILE, 'w', encoding='utf-8') as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
        except Exception:
            pass
        return data


def save_config(cfg):
    try:
        with open(CONFIG_FILE, 'w', encoding='utf-8') as f:
            json.dump(cfg, f, ensure_ascii=False, indent=2)
    except Exception:
        pass


def build_command(cfg):
    """Список аргументов запуска evo.py.

    Список, а не строка: subprocess.Popen работает без shell, поэтому не
    тянет cmd.exe и не интерпретирует кавычки/спецсимволы (аудит №14).
    Интерпретатор берём из sys.executable, а не из хардкода 'py' (лаунчер
    уже работает на нужном python; 'py' требует наличие лаунчера в PATH).
    """
    cmd = [sys.executable, 'evo.py',
           '--pop', str(cfg['pop']),
           '--rounds', str(cfg['rounds']),
           '--episode', str(cfg['episode']),
           '--episode-increment', str(cfg['episode_increment']),
           '--speed', str(cfg['speed']),
           '--top-percent', str(cfg['top_percent'])]
    cmd.append('--restart' if cfg.get('restart', True) else '--no-restart')
    return cmd


def calc_time(params):
    pop = params['pop']
    rounds = params['rounds']
    episode = params['episode']
    increment = params['episode_increment']
    restart_overhead = RESTART_OVERHEAD if params['restart'] else 0.0

    total_seconds = 0.0
    details = []
    for rnd in range(1, rounds + 1):
        ep = episode + increment * rnd
        per_bot = ep + restart_overhead
        round_time = pop * per_bot
        total_seconds += round_time
        details.append((rnd, ep, round_time))

    hours = total_seconds / 3600.0
    minutes = (total_seconds % 3600) / 60.0
    return total_seconds, hours, minutes, details


def make_resizable(win):
    """Разрешить растягивание окна с кастомным заголовком.

    overrideredirect(True) снимает рамку растягивания - окно консоли
    нельзя было изменить (жалоба пользователя). WS_THICKFRAME возвращает
    хват за края/угол; вызывать ПОСЛЕ overrideredirect (wm снимает стиль).
    """
    try:
        import ctypes
        user32 = ctypes.windll.user32
        hwnd = win.winfo_id()
        real = user32.GetParent(hwnd) or hwnd
        GWL_STYLE = -16
        WS_THICKFRAME = 0x00040000
        style = user32.GetWindowLongW(real, GWL_STYLE)
        user32.SetWindowLongW(real, GWL_STYLE, style | WS_THICKFRAME)
    except Exception:
        pass


def create_themed_toplevel(parent, title, colors, width=800, height=600, attach_callback=None, geometry_key=None):
    """Создаёт Toplevel окно с кастомным заголовком и тёмной темой."""
    win = tk.Toplevel(parent)
    win.title(title)
    win.overrideredirect(True)
    make_resizable(win)
    if geometry_key is None:
        geometry_key = title
    win._geometry_key = geometry_key
    # Восстановить сохранённую геометрию
    _saved = load_config().get('window_geometries', {}).get(geometry_key)
    if _saved:
        win.geometry(_saved)
    else:
        # Центрировать относительно родителя
        try:
            px = parent.winfo_x(); py = parent.winfo_y()
            pw = parent.winfo_width(); ph = parent.winfo_height()
            x = px + (pw - width) // 2
            y = py + (ph - height) // 2
            win.geometry('%dx%d+%d+%d' % (width, height, x, y))
        except Exception:
            win.geometry('%dx%d' % (width, height))
    # Сохранить геометрию при закрытии
    def _save_geo(event=None):
        try:
            cfg = load_config()
            cfg.setdefault('window_geometries', {})
            cfg['window_geometries'][geometry_key] = win.geometry()
            save_config(cfg)
        except Exception:
            pass
    win.bind('<Destroy>', _save_geo)
    win.configure(bg=colors['bg'])
    # Иконка
    icon_path = os.path.join(HERE, 'assets', 'kc_bot.ico')
    if os.path.exists(icon_path):
        try:
            win.iconbitmap(icon_path)
        except Exception:
            pass

    # Заголовок (токены единой типографики)
    title_bar = tk.Frame(win, bg=colors['title_bg'], height=H_BTN)
    title_bar.pack(fill=tk.X, side=tk.TOP)
    title_bar.pack_propagate(False)

    title_label = tk.Label(title_bar, text=title, bg=colors['title_bg'],
                           fg=colors['fg'], font=('Segoe UI', 13, 'bold'))
    title_label.pack(side=tk.LEFT, padx=12)

    btn_frame = tk.Frame(title_bar, bg=colors['title_bg'])
    btn_frame.pack(side=tk.RIGHT)

    # Кнопка прикрепления (если есть callback)
    if attach_callback:
        attach_btn = tk.Button(btn_frame, text='\u2199', font=('Segoe UI', 13),
                               bg=colors['title_bg'], fg=colors['fg'], relief=tk.FLAT,
                               borderwidth=0, width=4, cursor='hand2', command=attach_callback)
        attach_btn.pack(side=tk.LEFT)
        attach_btn.bind('<Enter>', lambda e: attach_btn.configure(bg=colors['btn_hover']))
        attach_btn.bind('<Leave>', lambda e: attach_btn.configure(bg=colors['title_bg']))

    # Кнопки управления
    def do_minimize(w):
        w.overrideredirect(False)
        w.iconify()
        w.bind('<Map>', lambda e, win=w: _restore_window(win))

    def _restore_window(win):
        win.unbind('<Map>')
        win.after(100, lambda: _finish_restore(win))

    def _finish_restore(win):
        if win.state() in ('normal', 'zoomed'):
            win.overrideredirect(True)
            make_resizable(win)      # wm снял стиль - возвращаем ресайз
            win.update_idletasks()
            win.update()
            apply_taskbar_style(win)
        else:
            win.after(50, lambda: _finish_restore(win))

    def do_maximize(w):
        """Развернуть/вернуть (раньше кнопка была мёртвой)."""
        if getattr(w, '_maximized', False):
            w.geometry(getattr(w, '_normal_geo', '%dx%d' % (width, height)))
            w._maximized = False
        else:
            w._normal_geo = w.geometry()
            w.geometry('%dx%d+0+0' % (w.winfo_screenwidth(),
                                     w.winfo_screenheight()))
            w._maximized = True

    for text, cmd, hover_bg in [('\u2014', lambda w=win: do_minimize(w), colors['btn_hover']),
                                ('\u25A1', lambda w=win: do_maximize(w), colors['btn_hover']),
                                ('\u2715', win.destroy, '#e81123')]:
        btn = tk.Button(btn_frame, text=text, font=('Segoe UI', 13),
                        bg=colors['title_bg'], fg=colors['fg'], relief=tk.FLAT,
                        borderwidth=0, width=6, cursor='hand2', command=cmd)
        btn.pack(side=tk.LEFT)
        btn.bind('<Enter>', lambda e, b=btn, h=hover_bg: b.configure(bg=h))
        btn.bind('<Leave>', lambda e, b=btn, bg=colors['title_bg']: b.configure(bg=bg))

    # Перетаскивание
    def start_move(event):
        win._drag_x = event.x
        win._drag_y = event.y
    def do_move(event):
        x = win.winfo_x() + event.x - win._drag_x
        y = win.winfo_y() + event.y - win._drag_y
        win.geometry('+%d+%d' % (x, y))

    title_bar.bind('<Button-1>', start_move)
    title_bar.bind('<B1-Motion>', do_move)
    title_label.bind('<Button-1>', start_move)
    title_label.bind('<B1-Motion>', do_move)

    # Панель задач
    try:
        win.update_idletasks()
        apply_taskbar_style(win)

        # ITaskbarList недоступен в этом окружении — полагаемся на WS_EX_APPWINDOW
    except Exception:
        pass

    # Контент
    content = tk.Frame(win, bg=colors['bg'])
    content.pack(fill=tk.BOTH, expand=True)

    return win, content


class ConsoleWindow:
    """Консоль вывода: кольцевой буфер, батчинг, фильтры, поиск, экспорт.

    Требования (решение D28):
      * кольцевой буфер - в памяти и в Text держим только MAX_LINES строк,
        иначе при тысячах строк/с виджет упирается в память и тормозит;
      * батчинг - строки накапливаются и вставляются одним вызовом раз в
        FLUSH_MS мс, а не по одной (иначе Tk встаёт на каждом insert);
      * авто-скролл сам отключается, когда пользователь отмотал назад;
      * фильтр по уровню, поиск, очистка, копирование, экспорт в файл.
    График удалён (решение D3) - прогресс показывается числом в статусбаре.

    Потоки: строки приходят из reader-потока через queue.Queue и читаются
    только в главном потоке (Tk нельзя трогать из потока - раньше именно
    это зависало кнопку «Старт»).
    """

    MAX_LINES = 5000        # сколько строк храним/показываем
    FLUSH_MS = 80           # как часто сбрасываем накопленное в виджет
    MAX_QUEUE = 20000       # потолок очереди до _tick (защита от утечки)

    def __init__(self, parent, colors, detached=False, content='both',
                 on_attach=None, on_finish=None):
        # content/on_attach/on_finish оставлены для совместимости со старыми
        # вызовами; график больше не строится.
        self.parent = parent
        self.colors = colors
        self.detached = detached
        self.on_finish = on_finish

        # потокобезопасная очередь: пишет reader-поток, читает главный
        self._queue = queue.Queue()
        self._pending = []              # накоплено до батч-сброса
        # кольцевой буфер: (уровень, строка); память ограничена жёстко
        self._buffer = collections.deque(maxlen=self.MAX_LINES)
        self._follow = True             # автоскролл включён
        self._after_id = None
        self._find_pos = None

        self.process = None             # совместимость: раньше был процесс
        self.reader_thread = None
        self.running = False
        self._stopped_by_user = False

        if detached:
            self.window, self._content = create_themed_toplevel(
                parent, t('console_title'), colors, 900, 620, on_attach,
                geometry_key='console_window')
        else:
            self.window = tk.Frame(parent, bg=colors['bg'])
            self._content = self.window

        self._build_ui()
        self._tick()

    # ------------------------------------------------------------- построение
    def _build_ui(self):
        c = self.colors

        # --- панель инструментов (единые размеры с основным окном) ---
        bar = ctk.CTkFrame(self._content, fg_color='transparent',
                           corner_radius=0)
        bar.pack(fill=tk.X, padx=PAD_CARD, pady=(PAD_CARD, 8))

        self._filter_labels = [t('console_filter_all'),
                               t('console_filter_warn'),
                               t('console_filter_error')]
        # None = показывать всё; иначе - только эти уровни и выше по важности
        self._filter_levels = [None, ('warn', 'error'), ('error',)]
        self.filter_var = tk.StringVar(value=self._filter_labels[0])

        # строка1: список фильтра (эталонный вид + масштаб Windows) + поиск
        row1 = ctk.CTkFrame(bar, fg_color='transparent', corner_radius=0)
        row1.pack(fill=tk.X)
        # CTkOptionMenu вместо ttk.Combobox: вид приложения (пользователь:
        # «выпадающий список не оформлен в стиле приложения»)
        flt = ctk.CTkOptionMenu(row1, values=self._filter_labels,
                                variable=self.filter_var,
                                command=lambda _v: self._rebuild(),
                                width=170, height=H_BTN, font=F(FS_BODY),
                                fg_color=c['entry_bg'],
                                text_color=c['fg'],
                                button_color=c['btn_hover'],
                                button_hover_color=c['accent'],
                                dropdown_fg_color=c['entry_bg'],
                                dropdown_text_color=c['fg'],
                                dropdown_hover_color=c['btn_hover'],
                                corner_radius=4)
        flt.pack(side=tk.LEFT, padx=(0, 8))

        self.search_var = tk.StringVar()
        ent = ctk.CTkEntry(row1, textvariable=self.search_var, width=240,
                           height=H_BTN, font=F(FS_BODY), corner_radius=4)
        ent.pack(side=tk.LEFT, padx=(0, 8))
        ent.bind('<Return>', lambda e: self._find_next())

        self._mkbtn(row1, t('console_find'), self._find_next)
        self._mkbtn(row1, t('console_clear'), self.clear)

        # строка2: автоскролл слева; Копировать/Экспорт/статус справа
        # (строка1 при900px переполнялась - последние кнопки обрезались)
        row2 = ctk.CTkFrame(bar, fg_color='transparent', corner_radius=0)
        row2.pack(fill=tk.X, pady=(6, 0))
        self.follow_var = tk.BooleanVar(value=True)
        ctk.CTkSwitch(row2, text=t('console_autoscroll'),
                      variable=self.follow_var,
                      command=self._follow_toggled, font=F(FS_BODY),
                      text_color=c['fg'], progress_color=c['accent']
                      ).pack(side=tk.LEFT)
        right2 = ctk.CTkFrame(row2, fg_color='transparent', corner_radius=0)
        right2.pack(side=tk.RIGHT)
        self.status_var = tk.StringVar(value=t('console_status', n=0))
        self._mkbtn(right2, t('console_copy'), self.copy_all)
        self._mkbtn(right2, t('console_export'), self.export)
        ctk.CTkLabel(right2, textvariable=self.status_var, font=F(FS_CAPTION),
                     text_color='#9a9a9a', anchor='e').pack(
            side=tk.LEFT, padx=(6, 2))

        # --- текст ---
        body = tk.Frame(self._content, bg=c['bg'])
        body.pack(fill=tk.BOTH, expand=True, padx=PAD_CARD, pady=(8, PAD_CARD))

        scroll = ttk.Scrollbar(body, orient=tk.VERTICAL)
        self.console_text = tk.Text(body, font=FT(FS_MONO),
                                    bg=c['entry_bg'], fg=c['fg'],
                                    insertbackground=c['fg'],
                                    selectbackground='#264f78',
                                    relief=tk.FLAT, borderwidth=1,
                                    wrap=tk.WORD,
                                    yscrollcommand=self._on_yscroll)
        scroll.configure(command=self._scrollbar_cmd)
        self.console_text.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        scroll.pack(side=tk.RIGHT, fill=tk.Y)
        self._scrollbar = scroll

        self.console_text.tag_config('found', background='#7a5c00',
                                     foreground='#ffffff')
        # пользователь сам крутит - гасим автоскролл
        self.console_text.bind('<MouseWheel>', self._on_user_scroll, add='+')
        scroll.bind('<ButtonRelease-1>', self._on_user_scroll, add='+')
        scroll.bind('<B1-Motion>', self._on_user_scroll, add='+')

    def _mkbtn(self, parent, label, cmd):
        return ctk.CTkButton(parent, text=label, command=cmd, height=H_BTN,
                             corner_radius=4, font=F(FS_BODY),
                             fg_color='transparent', border_width=1,
                             border_color=self.colors['border'],
                             text_color=self.colors['fg'],
                             hover_color=self.colors['btn_hover']).pack(
            side=tk.LEFT, padx=4)

    # ------------------------------------------------------------ потокобезопасность
    def append(self, text):
        """Добавить текст. Можно вызывать из ЛЮБОГО потока."""
        if not text:
            return
        self._queue.put(text)
        # Предохранитель от утечки: _tick перестаёт планироваться, когда окно
        # уничтожено, а reader-поток продолжает писать - без потолка очередь
        # росла бы неограниченно за долгую сессию (аудит). Вытесняем старое.
        if self._queue.qsize() > self.MAX_QUEUE:
            try:
                self._queue.get_nowait()
            except queue.Empty:
                pass

    def _tick(self):
        """Главный цикл консоли: раз в FLUSH_MS мс сбрасываем накопленное."""
        try:
            if not (hasattr(self, 'window') and self.window.winfo_exists()):
                return
        except tk.TclError:
            return
        self._drain()
        self._after_id = self.window.after(self.FLUSH_MS, self._tick)

    def _drain(self):
        got = False
        while True:
            try:
                item = self._queue.get_nowait()
            except queue.Empty:
                break
            got = True
            self._pending.append((self._level_of(item), item))
        if got:
            self._flush()

    @staticmethod
    def _level_of(line):
        """Определить уровень по тексту: вывод движка идёт обычным текстом."""
        s = line.lstrip()
        if s.startswith('!!') or 'Traceback' in s or 'Error:' in s \
                or 'ERROR' in s or 'SystemExit' in s:
            return 'error'
        if s.startswith('!') or 'WARN' in s:
            return 'warn'
        return 'info'

    def _active_levels(self):
        try:
            idx = self._filter_labels.index(self.filter_var.get())
        except ValueError:
            idx = 0
        return self._filter_levels[idx]

    def _passes(self, level):
        levels = self._active_levels()
        return levels is None or level in levels

    def _flush(self):
        if self.console_text is None:
            self._pending = []
            return
        chunk = [txt for lvl, txt in self._pending if self._passes(lvl)]
        self._buffer.extend(self._pending)
        self._pending = []
        if chunk:
            # один insert на весь батч - а не по одному на строку
            self.console_text.insert(tk.END, ''.join(chunk))
            self._trim()
            if self._follow:
                self.console_text.see(tk.END)
        self._update_status()

    def _trim(self):
        """Держим в виджете не больше MAX_LINES строк."""
        try:
            total = int(self.console_text.index('end-1c').split('.')[0])
        except (tk.TclError, ValueError):
            return
        if total > self.MAX_LINES:
            self.console_text.delete('1.0', '%d.0' % (total - self.MAX_LINES + 1))

    def _update_status(self):
        self.status_var.set(t('console_status', n=len(self._buffer)))

    # ------------------------------------------------------------- скроллинг
    def _on_yscroll(self, first, last):
        # _scrollbar создаётся ПОСЛЕ Text, а Tk может вызвать колбэк сразу
        sb = getattr(self, '_scrollbar', None)
        if sb is not None:
            sb.set(first, last)

    def _scrollbar_cmd(self, *args):
        self.console_text.yview(*args)
        self._sync_follow()

    def _on_user_scroll(self, event=None):
        self._sync_follow()

    def _sync_follow(self):
        try:
            at_end = self.console_text.yview()[1] >= 0.999
        except (tk.TclError, AttributeError):
            return
        self._follow = at_end
        try:
            self.follow_var.set(at_end)
        except tk.TclError:
            pass

    def _follow_toggled(self):
        self._follow = bool(self.follow_var.get())
        if self._follow and self.console_text is not None:
            self.console_text.see(tk.END)

    # ------------------------------------------------------------- операции
    def reset(self):
        """Очистить всё перед новым запуском."""
        self._pending = []
        self._buffer.clear()
        self._find_pos = None
        if self.console_text is not None:
            self.console_text.delete('1.0', tk.END)
            self.console_text.tag_remove('found', '1.0', tk.END)
        self._update_status()

    def clear(self):
        self.reset()

    def _rebuild(self):
        """Перестроить виджет после смены фильтра (буфер не трогаем)."""
        if self.console_text is None:
            return
        self.console_text.delete('1.0', tk.END)
        visible = [txt for lvl, txt in self._buffer if self._passes(lvl)]
        if visible:
            self.console_text.insert(tk.END, ''.join(visible))
        self._update_status()
        if self._follow:
            self.console_text.see(tk.END)

    def copy_all(self):
        if self.console_text is None:
            return
        try:
            self.clipboard_clear()
            self.clipboard_append(self.console_text.get('1.0', 'end-1c'))
            self.update()
        except tk.TclError:
            pass

    def export(self):
        from tkinter import filedialog
        path = filedialog.asksaveasfilename(
            defaultextension='.txt',
            filetypes=[('Text', '*.txt'), ('All files', '*.*')])
        if not path:
            return
        try:
            with open(path, 'w', encoding='utf-8') as f:
                f.write(''.join(txt for _lvl, txt in self._buffer))
            self.append('\n' + t('console_export_done', path=path) + '\n')
        except OSError as e:
            self.append('\n' + t('console_export_fail', err=e) + '\n')

    def _find_next(self):
        if self.console_text is None:
            return
        q = self.search_var.get()
        if not q:
            return
        self.console_text.tag_remove('found', '1.0', tk.END)
        start = self._find_pos or '1.0'
        pos = self.console_text.search(q, start, stopindex=tk.END, nocase=True)
        if not pos:                     # дошли до конца - начинаем заново
            pos = self.console_text.search(q, '1.0', stopindex=tk.END,
                                           nocase=True)
        if not pos:
            self._find_pos = None
            return
        end = '%s+%dc' % (pos, len(q))
        self.console_text.tag_add('found', pos, end)
        self.console_text.see(pos)
        self._find_pos = end

    # ------------------------------------------------------------- жизненный цикл
    def stop(self):
        self._stopped_by_user = True
        self.running = False

    def destroy(self):
        self.stop()
        if self._after_id is not None:
            try:
                self.window.after_cancel(self._after_id)
            except (tk.TclError, AttributeError):
                pass
            self._after_id = None
        if hasattr(self, 'window') and self.window.winfo_exists():
            self.window.destroy()


class App:
    def __init__(self, root):
        self.root = root
        self.config = load_config()
        self._migrate_config()
        load_locale(self.config.get('language', 'ru'))
        self.colors = COLOR_SCHEMES[resolve_theme(self.config.get('theme', 'dark'))]
        # Масштаб интерфейса: CTk обычно определяет DPI сам, но сверяемся с
        # честным источником - AppliedDPI из реестра (масштаб под параметры
        # ОС; GetDpiForSystem в unaware-процессе врёт и отдаёт96).
        _sc = win_ui_scale()
        try:
            if abs(ctk.ScalingTracker.get_window_scaling(self.root) - _sc) > 0.02:
                ctk.set_widget_scaling(_sc)
                ctk.set_window_scaling(_sc)
        except Exception:
            pass
        self.console_window = None
        self.process_running = False
        self.settings_dialog = None
        self._maximized = False
        self._normal_geometry = '1000x700'
        self.detail_panel = None
        self.console_panel = None

        root.geometry(self._normal_geometry)
        root.minsize(800, 500)
        root.configure(fg_color=self.colors['bg'])

        # Кастомный заголовок (overrideredirect) — отключено, окно не показывалось
        # # root.overrideredirect(True)

        self._build_title_bar()
        self._build_main()
        # Периодическое обновление подробностей (каждые 2 секунды)
        self._schedule_detail_update()
        # Применить стиль панели задач после отображения окна
        pass  # _apply_taskbar_style отключён (скрывал окно)

        # Проверка патча игры при запуске (после отрисовки окна, чтобы
        # модальный диалог не встал раньше, чем окно показалось)
        self._patch_prompted = False
        self.root.after(400, lambda: self._ensure_patched(
            t('patch_needed'), check_dir=True))

    # ------------------------------------------------------------ патч игры
    def _ensure_patched(self, message, check_dir=False):
        """Проверить патч Assembly-CSharp.dll и предложить пропатчить.

        Возвращает True, если игра пропатчена (или патч применён успешно).
        Повторно не спрашивает в рамках одной сессии, если пользователь
        отказался.
        """
        if check_dir and not self._refresh_game_dir_status():
            return False                # папка игры не настроена - не спорим
        if self._patch_prompted:
            return False                # уже спрашивали и получили отказ

        from tkinter import messagebox
        game_dir = (self.game_dir_var.get() or '').strip()
        try:
            code, msg = patcher.status(game_dir or None)
        except Exception as e:
            messagebox.showwarning(t('patch_title'),
                                   t('patch_check_fail', err=e))
            return False

        if code == 'ok':
            return True
        if code == 'missing':
            # папка игры не настроена - об этом уже сообщает статус-строка
            return False

        # not_patched -> спрашиваем про патч
        if not messagebox.askyesno(t('patch_title'), message):
            self._patch_prompted = True
            return False

        # затем - про бэкап (решение D22)
        make_backup = messagebox.askyesno(t('patch_title'),
                                          t('patch_backup_q'))
        logs = []

        def _log(s):
            logs.append(s)
            if self.console_window is not None:
                self.console_window.append('  %s\n' % s)

        if self.console_window is None or not self.console_window.window.winfo_exists():
            self.console_window = ConsoleWindow(self.root, self.colors,
                                                detached=True, content='both')
        self.console_window.append('\n--- %s ---\n' % t('patch_title'))

        try:
            if make_backup:
                patcher.make_backup(game_dir, log=_log)
            ok = patcher.patch(game_dir=game_dir, log=_log)
        except Exception as e:
            ok = False
            _log('!! %r' % (e,))

        if ok:
            messagebox.showinfo(t('patch_title'), t('patch_ok'))
            self.console_window.append('=== %s ===\n' % t('patch_ok'))
            self._patch_prompted = True
            self._refresh_patch_status()
            return True

        messagebox.showerror(t('patch_title'),
                             t('patch_fail', err='\n'.join(logs[-3:]) or '?'))
        self._patch_prompted = True
        self._refresh_patch_status()
        return False

    def _on_speed_change(self, event=None):
        """Смена скорости: игра должна быть пропатчена, иначе лог не пишется."""
        e = self.entries.get('speed')
        if e is None:
            return
        try:
            val = float(e[0].get().strip())
        except (ValueError, TypeError):
            return
        if getattr(self, '_speed_seen', None) == val:
            return
        self._speed_seen = val
        self._ensure_patched(t('patch_speed'))

    def _migrate_config(self):
        """Привести сохранённый конфиг к текущим допустимым диапазонам.

        Без этого устаревшие значения блокируют «Старт» навсегда: после того
        как потолок скорости стал 3x, конфиг с speed=4.0 делал валидацию
        неуспешной, а _validate_all() выключала кнопку. Раньше миграции не
        было вовсе (аудит: «насильственная миграция конфига»).
        """
        changed = False
        for key, rules in self.VALIDATION_RULES.items():
            if key not in self.config:
                continue
            min_val, max_val, _msg = rules
            try:
                fv = float(self.config[key])
            except (TypeError, ValueError):
                self.config[key] = DEFAULTS.get(key, self.config[key])
                changed = True
                continue
            if fv < min_val or fv > max_val:
                self.config[key] = min(max(fv, min_val), max_val)
                changed = True
        if changed:
            try:
                save_config(self.config)
            except OSError:
                pass

    def _build_title_bar(self):
        """Шапка окна (Fluent header).

        Порядок справа налево: Язык, Тема, Справка | ─ □ ✕ - служебные
        кнопки СЛЕВА от управления окном (требование пользователя),
        крестик крайний справа. Кнопки пакуются напрямую в шапку:
        вложенный CTk-фрейм не авто-размерился (propagate у CTk не
        работает) и содержимое уезжало за край окна.
        """
        c = self.colors
        # Обработка закрытия окна (убить обучение и игру)
        self.root.protocol('WM_DELETE_WINDOW', self._on_close)
        self.title_bar = ctk.CTkFrame(self.root, height=H_HEADER, corner_radius=0,
                                      fg_color=c['title_bg'])
        self.title_bar.pack(fill=tk.X, side=tk.TOP)
        self.title_bar.pack_propagate(False)

        # Слева: консоль + название
        self.console_toggle_btn = ctk.CTkButton(
            self.title_bar, text='⌨', width=H_HEADER, height=H_HEADER,
            corner_radius=0,
            fg_color='transparent', text_color=c['fg'], hover_color=c['btn_hover'],
            font=F(17), command=self._toggle_console_panel)
        self.console_toggle_btn.pack(side=tk.LEFT)

        title_label = ctk.CTkLabel(self.title_bar, text='KC Bot',
                                   font=F(FS_TITLE, 'bold'), text_color=c['fg'])
        title_label.pack(side=tk.LEFT, padx=(6, 0))

        # Справа: pack(side=RIGHT) - первый упакованный занимает крайнюю
        # правую позицию, поэтому порядок упаковки обратный желаемому.
        def _cap(text, cmd, w, hover=None):
            b = ctk.CTkButton(self.title_bar, text=text, width=w,
                              height=H_HEADER,
                              corner_radius=0, fg_color='transparent',
                              text_color=c['fg'],
                              hover_color=hover or c['btn_hover'],
                              font=F(FS_BODY), command=cmd)
            b.pack(side=tk.RIGHT)
            return b

        # крайние справа - управление окном (крестик с красным hover)
        _cap('\u2715', self.root.destroy, 46, hover='#e81123')
        _cap('\u25A1', self.toggle_maximize, 46)
        _cap('\u2014', self.minimize, 46)
        # сразу левее них - Справка
        self.help_btn = _cap(t('help_title'), self._open_help, 88)

        # Тема и Язык - списки в стиле фильтра консоли (эталон пользователя):
        # видно текущее значение, выбор из списка, масштаб вместе с Windows
        cur_theme = self.config.get('theme', 'dark')
        theme_names = list(THEME_KEYS)
        theme_labels = [t(THEME_KEYS[n]) for n in theme_names]
        theme_var = tk.StringVar(
            value=(theme_labels[theme_names.index(cur_theme)]
                   if cur_theme in theme_names else theme_labels[0]))
        theme_menu = ctk.CTkOptionMenu(
            self.title_bar, values=theme_labels, variable=theme_var,
            width=170, height=36, corner_radius=4, font=F(FS_BODY),
            fg_color=c['title_bg'], text_color=c['fg'],
            button_color=c['btn_hover'], button_hover_color=c['accent'],
            dropdown_fg_color=c['entry_bg'], dropdown_text_color=c['fg'],
            dropdown_hover_color=c['btn_hover'],
            command=lambda lab: self._apply_theme(
                theme_names[theme_labels.index(lab)]))
        theme_menu.pack(side=tk.RIGHT, padx=(0, 6), pady=6)
        self.theme_btn = theme_menu

        lang_codes = ['ru', 'en']
        lang_labels = [t('lang_name_ru'), t('lang_name_en')]
        cur_lang = self.config.get('language', 'ru')
        lang_var = tk.StringVar(
            value=(lang_labels[lang_codes.index(cur_lang)]
                   if cur_lang in lang_codes else lang_labels[0]))
        lang_menu = ctk.CTkOptionMenu(
            self.title_bar, values=lang_labels, variable=lang_var,
            width=130, height=36, corner_radius=4, font=F(FS_BODY),
            fg_color=c['title_bg'], text_color=c['fg'],
            button_color=c['btn_hover'], button_hover_color=c['accent'],
            dropdown_fg_color=c['entry_bg'], dropdown_text_color=c['fg'],
            dropdown_hover_color=c['btn_hover'],
            command=lambda lab: self._apply_language(
                lang_codes[lang_labels.index(lab)], lab))
        lang_menu.pack(side=tk.RIGHT, padx=(0, 6), pady=6)
        self.lang_btn = lang_menu

        # Перетаскивание окна
        self.title_bar.bind('<Button-1>', self._start_move)
        self.title_bar.bind('<B1-Motion>', self._do_move)
        title_label.bind('<Button-1>', self._start_move)
        title_label.bind('<B1-Motion>', self._do_move)

    def _start_move(self, event):
        if self._maximized:
            return
        self._drag_x = event.x
        self._drag_y = event.y

    def _do_move(self, event):
        if self._maximized:
            return
        x = self.root.winfo_x() + event.x - self._drag_x
        y = self.root.winfo_y() + event.y - self._drag_y
        self.root.geometry('+%d+%d' % (x, y))

    def minimize(self):
        self._was_overrideredirect = True
        self.root.overrideredirect(False)
        self.root.iconify()
        self.root.bind('<Map>', self._on_restore)

    def _on_restore(self, event=None):
        self.root.unbind('<Map>')
        self.root.after(100, self._do_restore)

    def _do_restore(self):
        if self.root.state() in ('normal', 'zoomed'):
            self.root.overrideredirect(True)
            self.root.update_idletasks()
            self.root.update()
            apply_taskbar_style(self.root)
        else:
            self.root.after(50, self._do_restore)

    def toggle_maximize(self):
        if self._maximized:
            self.root.geometry(self._normal_geometry)
            self._maximized = False
        else:
            self._normal_geometry = self.root.geometry()
            sw = self.root.winfo_screenwidth()
            sh = self.root.winfo_screenheight()
            self.root.geometry('%dx%d+0+0' % (sw, sh))
            self._maximized = True

    def _build_game_dir_row(self, c):
        """Карточка «Папка игры»: подпись, путь + кнопки, бейдж патча."""
        box = ctk.CTkFrame(self.params_frame, corner_radius=8,
                           fg_color=card_color(c))
        box.pack(fill=tk.X, padx=PAD_CARD, pady=(0, GAP_SECTION))

        inner = ctk.CTkFrame(box, fg_color='transparent', corner_radius=0)
        inner.pack(fill=tk.X, padx=PAD_CARD, pady=PAD_CARD)

        ctk.CTkLabel(inner, text=t('game_dir_title'), font=F(FS_SECTION, 'bold'),
                     text_color=c['fg'], anchor='w').pack(fill=tk.X)

        path = gamepath.game_dir() or gamepath.autodetect_game_dir()
        self.game_dir_var = tk.StringVar(value=path)
        row = ctk.CTkFrame(inner, fg_color='transparent', corner_radius=0)
        row.pack(fill=tk.X, pady=(8, 0))
        ent = ctk.CTkEntry(row, textvariable=self.game_dir_var, height=H_INPUT,
                           font=F(FS_BODY), corner_radius=4)
        ent.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(0, 10))

        def _mk(label, cmd):
            b = ctk.CTkButton(row, text=label, command=cmd, height=H_BTN,
                              width=140, corner_radius=4, font=F(FS_BODY),
                              fg_color='transparent', border_width=1,
                              border_color=c['border'], text_color=c['fg'],
                              hover_color=c['btn_hover'])
            b.pack(side=tk.LEFT, padx=(0, 8))
            return b

        _mk(t('game_dir_browse'), self._browse_game_dir)
        _mk(t('game_dir_autodetect'), self._autodetect_game_dir)

        self.game_dir_status = ctk.CTkLabel(inner, text='', font=F(FS_CAPTION),
                                            text_color='#ff5555', anchor='w')
        self.game_dir_status.pack(fill=tk.X, pady=(8, 0))
        self._refresh_game_dir_status()

        # Статус патча + кнопка отката (Фаза 7): бейдж слева, кнопка справа
        prow = ctk.CTkFrame(inner, fg_color='transparent', corner_radius=0)
        prow.pack(fill=tk.X, pady=(8, 0))
        self.patch_status = ctk.CTkLabel(prow, text='', font=F(FS_CAPTION),
                                         text_color='#888888', anchor='w')
        self.patch_status.pack(side=tk.LEFT, fill=tk.X, expand=True)
        ctk.CTkButton(prow, text=t('patch_unpatch'), command=self._unpatch_game,
                      height=H_BTN, width=160, corner_radius=4, font=F(FS_BODY),
                      fg_color='transparent', border_width=1,
                      border_color=c['border'], text_color=c['fg'],
                      hover_color=c['btn_hover']).pack(side=tk.RIGHT)
        self._refresh_patch_status()

    def _refresh_patch_status(self):
        """Показать состояние патча рядом с папкой игры."""
        if not hasattr(self, 'patch_status'):
            return
        try:
            code, _msg = patcher.status(
                (self.game_dir_var.get() or '').strip() or None)
        except Exception as e:
            self.patch_status.configure(text=str(e)[:80], text_color='#ff5555')
            return
        label = {'ok': t('patch_state_ok'),
                 'not_patched': t('patch_state_no')}.get(
                     code, t('patch_state_unknown'))
        color = {'ok': '#4CAF50', 'not_patched': '#ff5555'}.get(code, '#888888')
        self.patch_status.configure(text=label, text_color=color)

    def _unpatch_game(self):
        """Откатить патч из бэкапа (с подтверждением)."""
        from tkinter import messagebox
        if not messagebox.askyesno(t('patch_title'), t('patch_unpatch_q')):
            return
        if self.console_window is None or not self.console_window.window.winfo_exists():
            self.console_window = ConsoleWindow(self.root, self.colors,
                                                detached=True, content='both')
        logs = []

        def _log(s):
            logs.append(s)
            self.console_window.append('  %s\n' % s)

        self.console_window.append('\n--- %s ---\n' % t('patch_unpatch'))
        try:
            ok = patcher.unpatch(
                (self.game_dir_var.get() or '').strip() or None, log=_log)
        except Exception as e:
            ok = False
            _log('!! %r' % (e,))
            logs.append(str(e))

        self._refresh_patch_status()
        if ok:
            messagebox.showinfo(t('patch_title'), t('patch_unpatch_ok'))
        else:
            messagebox.showerror(t('patch_title'),
                                 t('patch_unpatch_fail',
                                   err=(logs[-1] if logs else '?')))

    def _refresh_game_dir_status(self):
        p = (self.game_dir_var.get() or '').strip()
        exe = os.path.join(p, 'Kingdom.exe') if p else ''
        if p and os.path.isfile(exe):
            self.game_dir_status.configure(text='', text_color='#888888')
            return True
        self.game_dir_status.configure(text=t('game_dir_missing'), text_color='#ff5555')
        return False

    def _browse_game_dir(self):
        from tkinter import filedialog
        path = filedialog.askdirectory(title=t('game_dir_title'),
                                       initialdir=self.game_dir_var.get() or '')
        if not path:
            return
        self.game_dir_var.set(path)
        self._save_game_dir()

    def _autodetect_game_dir(self):
        path = gamepath.autodetect_game_dir()
        if not path:
            self.game_dir_status.configure(text=t('game_dir_not_found'),
                                        text_color='#ff5555')
            return
        self.game_dir_var.set(path)
        self._save_game_dir()

    def _save_game_dir(self):
        path = (self.game_dir_var.get() or '').strip()
        try:
            gamepath.set_game_dir(path)
        except OSError as e:
            self.game_dir_status.configure(text=str(e), text_color='#ff5555')
            return
        self._refresh_game_dir_status()
        self._refresh_patch_status()

    def _build_main(self):
        """Главное окно: слева карточки параметров, справа панель деталей.

        Правила дизайна (11 Дизайн UI): каждое поле - вертикальная группа
        «подпись -> поле -> комментарий» в одной колонке сетки (раньше
        подпись слева, поле справа, комментарий внизу - глаза теряли
        связь); Старт - первичная (акцент), Стоп - danger, Сброс - ghost.
        """
        c = self.colors
        self.main_frame = ctk.CTkFrame(self.root, fg_color='transparent',
                                       corner_radius=0)
        self.main_frame.pack(fill=tk.BOTH, expand=True, padx=14, pady=(6, 14))
        # Сетка вместо pack: pack_propagate у CTk не работает (проверено) -
        # ширина колонок задаётся только сеткой: параметры растут, панель
        # деталей держит минимум (иначе она схлопывалась в «сливку»)
        self.main_frame.grid_columnconfigure(0, weight=1)
        self.main_frame.grid_columnconfigure(1, minsize=360, weight=0)
        self.main_frame.grid_rowconfigure(0, weight=1)

        # Левая колонка: параметры
        self.params_frame = ctk.CTkFrame(self.main_frame, fg_color='transparent',
                                         corner_radius=0)
        if self.config.get('show_params', True):
            self.params_frame.grid(row=0, column=0, sticky='nsew', padx=(0, 10))

        ctk.CTkLabel(self.params_frame, text=t('params_title'),
                     font=F(FS_SECTION, 'bold'),
                     text_color=c['fg'], anchor='w').pack(
            fill=tk.X, padx=PAD_CARD, pady=(0, GAP_SECTION))

        # --- Папка игры -------------------------------------------------
        # Раньше её писал только C#-лаунчер (удалён в Фазе 0), а Python-версия
        # вообще не умела её задавать - эволюция падала с RuntimeError.
        self._build_game_dir_row(c)

        fields = [
            ('pop', t('param_pop'), 'int'),
            ('rounds', t('param_rounds'), 'int'),
            ('episode', t('param_episode'), 'float'),
            ('episode_increment', t('param_episode_increment'), 'float'),
            ('speed', t('param_speed'), 'float'),
            ('top_percent', t('param_top_percent'), 'float'),
        ]

        # Сетка2 колонки x3 ряда: подпись, поле и комментарий каждого
        # параметра живут в ОДНОЙ группе - связь «поле <-> текст» видна глазом
        grid = ctk.CTkFrame(self.params_frame, fg_color='transparent',
                            corner_radius=0)
        grid.pack(fill=tk.X, padx=PAD_CARD)
        grid.columnconfigure(0, weight=1, uniform='p')
        grid.columnconfigure(1, weight=1, uniform='p')

        self.entries = {}
        self._hint_labels = {}
        self._desc_labels = {}
        for i, (key, label, typ) in enumerate(fields):
            r, col = divmod(i, 2)
            group = ctk.CTkFrame(grid, fg_color='transparent', corner_radius=0)
            group.grid(row=r, column=col, sticky='nsew',
                       padx=(0, 8) if col == 0 else (8, 0),
                       pady=(0, GAP_SECTION))
            # подпись - над СВОИМ полем
            ctk.CTkLabel(group, text=label, font=F(FS_BODY), text_color=c['fg'],
                         anchor='w').pack(fill='x')
            if key == 'speed':
                # Скорость - выпадающий список (концепция): значения только
                # из набора1x/2x/3x (потолок D7), ошибка ввода исключена
                # структурно; вид как у фильтра консоли - эталон пользователя
                var = tk.StringVar(
                    value=SPEED_DISPLAY.get(_norm_int_str(
                        self.config.get(key, DEFAULTS[key])), '3x'))
                menu = ctk.CTkOptionMenu(
                    group, values=SPEED_VALUES, variable=var,
                    height=H_INPUT, font=F(FS_VALUE, mono=True),
                    corner_radius=4, width=140,
                    fg_color=c['entry_bg'], text_color=c['fg'],
                    button_color=c['btn_hover'],
                    button_hover_color=c['accent'],
                    dropdown_fg_color=c['entry_bg'],
                    dropdown_text_color=c['fg'],
                    dropdown_hover_color=c['btn_hover'],
                    command=lambda _v: self._on_speed_change())
                menu.pack(fill='x', pady=(3, 0))
                e = _OptionField(menu, var, SPEED_DISPLAY, SPEED_REVERSE)
                self.entries[key] = (e, typ)
            else:
                e = ctk.CTkEntry(group, height=H_INPUT, font=F(FS_VALUE, mono=True),
                                 justify='right', corner_radius=4)
                e.insert(0, str(self.config.get(key, DEFAULTS[key])))
                e.pack(fill='x', pady=(3, 0))
                self.entries[key] = (e, typ)
                e.bind('<KeyRelease>', lambda ev: self._on_param_change())
                e.bind('<FocusOut>', lambda ev: self._on_param_change())
            # комментарий - под СВОИМ полем; сюда же падает текст ошибки
            desc = ctk.CTkLabel(group, text=t('desc_' + key), font=F(11),
                                text_color='#9a9a9a', anchor='w',
                                justify='left', wraplength=220)
            desc.pack(fill='x', pady=(3, 0))
            self._desc_labels[key] = desc
            hint = ctk.CTkLabel(group, text='', font=F(11),
                                text_color='#ff5555', anchor='w')
            hint.pack(fill='x')
            self._hint_labels[key] = hint

        # Для speed событие смены уходит из CTkOptionMenu(command=...)
        # выше по тексту; блок старых привязок FocusOut/Return удалён.

        # --- Переключатели (Fluent switch) ---------------------------------
        sw_row = ctk.CTkFrame(self.params_frame, fg_color='transparent',
                              corner_radius=0)
        sw_row.pack(fill=tk.X, padx=PAD_CARD, pady=(4, 0))
        self.restart_var = tk.BooleanVar(value=self.config.get('restart', True))
        ctk.CTkSwitch(sw_row, text=t('chk_restart'), variable=self.restart_var,
                      font=F(FS_BODY), text_color=c['fg'],
                      progress_color=c['accent']).pack(side=tk.LEFT, padx=(0, 24))
        self.restart_var.trace_add('write', lambda *a: self.schedule_calculate())

        self.launch_after_var = tk.BooleanVar(value=self.config.get('launch_after', False))
        ctk.CTkSwitch(sw_row, text=t('chk_launch_after'),
                      variable=self.launch_after_var, font=F(FS_BODY),
                      text_color=c['fg'],
                      progress_color=c['accent']).pack(side=tk.LEFT)

        # --- Действия: первичная / danger / ghost ---------------------------
        actions = ctk.CTkFrame(self.params_frame, fg_color='transparent',
                               corner_radius=0)
        actions.pack(fill=tk.X, padx=PAD_CARD, pady=(GAP_SECTION, 0))
        actions.columnconfigure(0, weight=3)
        actions.columnconfigure(1, weight=2)
        actions.columnconfigure(2, weight=2)

        self.start_btn = ctk.CTkButton(
            actions, text=t('btn_start'), command=self.start_training,
            height=H_BTN, corner_radius=4, font=F(FS_BODY, 'bold'),
            fg_color=c['accent'], hover_color=_shade(c['accent'], 0.85),
            text_color='#ffffff')
        self.start_btn.grid(row=0, column=0, sticky='ew', padx=(0, 8))

        self.stop_btn = ctk.CTkButton(
            actions, text=t('btn_stop'), command=self.stop_training,
            height=H_BTN, corner_radius=4, font=F(FS_BODY, 'bold'),
            fg_color='transparent', border_width=1, border_color='#e81123',
            text_color='#e81123', hover_color='#c42b1c')
        # Кнопка Стоп скрыта по умолчанию (показывается при запуске обучения)

        # Кнопка сброса параметров к рекомендованным (ghost)
        def _reset_defaults():
            rec = {'pop': 50, 'rounds': 15, 'episode': 20.0, 'episode_increment': 5.0,
                   'speed': 3.0, 'top_percent': 0.1}
            for key, (e, typ) in self.entries.items():
                if key in rec:
                    e.delete(0, tk.END)
                    e.insert(0, str(rec[key]))
            self._on_param_change()
        ctk.CTkButton(actions, text=t('btn_default'), command=_reset_defaults,
                      height=H_BTN, corner_radius=4, font=F(FS_BODY),
                      fg_color='transparent', text_color=c['fg'],
                      hover_color=c['btn_hover']).grid(row=0, column=2,
                                                       sticky='ew', padx=(8, 0))

        # --- Результат расчёта (одна копия: раньше блок дублировался) ------
        self.result_label = ctk.CTkLabel(self.params_frame, text='',
                                         font=F(FS_VALUE, 'bold', mono=True),
                                         text_color=c['fg'], anchor='w',
                                         justify='left')
        self.result_label.pack(fill=tk.X, padx=PAD_CARD, pady=(GAP_SECTION, 0))
        self.result_detail = ctk.CTkLabel(self.params_frame, text='',
                                          font=F(FS_MONO, mono=True),
                                          text_color='#9a9a9a', anchor='w',
                                          justify='left')
        self.result_detail.pack(fill=tk.X, padx=PAD_CARD)

        # Валидация при запуске
        self.root.after(100, self._validate_pop)

        # Панель подробностей (через _build_detail_panel для консистентности)
        self._build_detail_panel()

        # Консоль - отдельное окно (по умолчанию)
        if not self.config.get('console_detached', True):
            self.config['console_detached'] = True
            save_config(self.config)

        self.root.update_idletasks()
        scale = win_ui_scale()
        self.root.minsize(int(880 * scale), int(580 * scale))
        # Подогнать высоту окна под содержимое с учётом масштаба Windows
        self._fit_window()
        # Повторная подгонка после полной отрисовки: первый замер берётся,
        # пока шрифты/детали ещё не осели (замер завышен - окно растёт с
        # пустым низом; жалоба «снизу много свободного пространства»)
        self.root.after(700, self._fit_window)
    def _fit_window(self):
        """Растянуть окно под содержимое с учётом масштаба Windows.

        При масштабе125% (реестр AppliedDPI=120) CTk увеличивает виджеты -
        сохранённая/дефолтная геометрия перестаёт вмещать содержимое
        (жалоба: «не всё влезло в окно»). Считаем натура��ную высоту и
        ширину содержимого и растим окно, но не больше рабочей области
        экрана (с учётом панели задач).
        """
        try:
            import re as _re
            self.root.update_idletasks()
            # Единицы Tk (= единицы geometry)! Win32-метрики в unaware-
            # процессе дают другую систему координат и окно вылезало за
            # экран (жалоба: «не влезло»). Экран Tk минус панель задач.
            work_w = self.root.winfo_screenwidth() - 8
            work_h = self.root.winfo_screenheight() - 80
            req_w = self.main_frame.winfo_reqwidth() + 32
            req_h = (self.title_bar.winfo_reqheight()
                     + self.main_frame.winfo_reqheight() + 26)
            # Единицы: winfo отдаёт физические пиксели (шрифты уже
            # масштабированы CTk), geometry - логические единицы Tk
            # (проверено: geometry1072 -> winfo1340 при масштабе1.25).
            # Приводим req к логическим, иначе окно оказывается выше
            # контента на ~20% (пустой низ) или ниже (обрезка).
            scale = win_ui_scale()
            content_h = int(req_h / scale) + 8
            geo_str = self.root.geometry()
            m = _re.match(r'(\d+)x(\d+)', geo_str)
            cur_w = int(m.group(1)) if m else req_w
            # позиция может быть и 'x+y', и '-1450+60' (2-й монитор слева)
            pm = _re.search(r'([+-]\d+)([+-]\d+)$', geo_str)
            if pm:
                x, y = int(pm.group(1)), int(pm.group(2))
            else:
                x, y = 80, 60
            # высоту - по содержимому (в логических единицах): и растим,
            # и ужимаем; не выше рабочей области экрана
            new_h = min(max(content_h, 560), work_h)
            # ширина: req без запаса сжимал левую колонку (поле пути
            # обрезалось) - даём +240 на воздух, не уже текущей
            new_w = min(max(cur_w, int(req_w / scale) + 240), work_w)
            import os as _os
            if _os.environ.get('KCBOT_FIT_DEBUG'):
                print('FIT: req=%dx%d work=%dx%d -> %dx%d (geo было %s)'
                      % (req_w, req_h, work_w, work_h, new_w, new_h,
                         self.root.geometry()), flush=True)
            # окно обязано целиком помещаться на экране; координаты со
            # знаком минус (второй монитор) не трогаем - их границы Tk
            # для второго экрана не сообщает
            if x >= 0:
                x = min(max(x, 0), max(0, work_w - new_w))
            if y >= 0:
                y = min(max(y, 0), max(0, work_h - new_h))
            self.root.geometry('%dx%d+%d+%d' % (new_w, new_h, x, y))
            self._normal_geometry = '%dx%d' % (new_w, new_h)
        except Exception:
            pass

    def _build_detail_panel(self):
        c = self.colors
        panel_bg = c.get('panel_bg', c['entry_bg'])
        detached = self.config.get('detail_detached', False)

        # Уничтожить существующие
        if hasattr(self, 'detail_frame') and self.detail_frame is not None:
            try:
                if self.detail_frame.winfo_exists():
                    self.detail_frame.destroy()
            except Exception:
                pass
            self.detail_frame = None
        if hasattr(self, 'detail_window') and self.detail_window is not None:
            try:
                if self.detail_window.winfo_exists():
                    self.detail_window.destroy()
            except Exception:
                pass
            self.detail_window = None

        if detached:
            self._create_detail_window()
            return

        self.detail_frame = ctk.CTkFrame(self.main_frame, fg_color=panel_bg,
                                         corner_radius=8)

        # Заголовок; кнопки переноса ← → удалены (больше не нужны,2026-11-21)
        header = ctk.CTkFrame(self.detail_frame, fg_color=c['title_bg'],
                              corner_radius=0)
        header.pack(fill=tk.X)
        ctk.CTkLabel(header, text=t('detail_title'),
                     font=F(FS_SECTION, 'bold'),
                     text_color=c['fg']).pack(side=tk.LEFT, padx=PAD_CARD,
                                              pady=10)

        content = tk.Frame(self.detail_frame, bg=panel_bg)
        content.pack(fill=tk.BOTH, expand=True)
        self._build_detail_content(content, c)

        # Правая колонка сетки main_frame (minsize задаётся там же)
        self.detail_frame.grid(row=0, column=1, sticky='nsew')

    def _build_detail_content(self, parent, c):
        detail_scroll = ttk.Scrollbar(parent, orient=tk.VERTICAL)
        self.detail_text = tk.Text(parent, font=FT(FS_MONO), wrap=tk.WORD,
                                  width=56,
                                  bg=c.get('panel_bg', c['entry_bg']), fg=c['fg'],
                                  insertbackground=c['fg'],
                                  selectbackground='#264f78',
                                  relief=tk.FLAT, borderwidth=0,
                                  yscrollcommand=detail_scroll.set,
                                  padx=10, pady=10)
        detail_scroll.configure(command=self.detail_text.yview)
        self.detail_text.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        detail_scroll.pack(side=tk.RIGHT, fill=tk.Y)

    def _move_panel(self, panel, direction):
        """Переместить панель влево или вправо."""
        other = 'console' if panel == 'detail' else 'detail'
        panel_side = self.config.get(panel + '_side', 'left' if panel == 'detail' else 'right')
        other_side = self.config.get(other + '_side', 'right' if other == 'console' else 'left')

        if direction == 'left':
            if panel_side == 'right':
                self.config[panel + '_side'] = 'left'
                if other_side == 'left':
                    self.config[other + '_side'] = 'right'
            else:
                return
        else:
            if panel_side == 'left':
                self.config[panel + '_side'] = 'right'
                if other_side == 'right':
                    self.config[other + '_side'] = 'left'
            else:
                return

        save_config(self.config)
        self._rebuild_panels()

    def _rebuild_panels(self):
        # Destroy existing
        for attr in ['detail_frame', 'console_container']:
            w = getattr(self, attr, None)
            if w is not None:
                try:
                    if w.winfo_exists():
                        w.destroy()
                except Exception:
                    pass
                setattr(self, attr, None)
        if hasattr(self, 'console_window') and self.console_window is not None:
            try:
                if self.console_window.window.winfo_exists():
                    self.console_window.destroy()
            except Exception:
                pass
            self.console_window = None
        if hasattr(self, 'detail_window') and self.detail_window is not None:
            try:
                if self.detail_window.winfo_exists():
                    self.detail_window.destroy()
            except Exception:
                pass
            self.detail_window = None

        # Rebuild in order
        order = self._get_panel_order()
        for item in order:
            if item == 'main':
                continue
            if item == 'detail' and not self.config.get('detail_detached', False):
                self._build_detail_panel()
            elif item == 'console' and not self.config.get('console_detached', False):
                self._build_console_panel()

    def _detach_detail(self):
        self.config['detail_detached'] = True
        save_config(self.config)
        if hasattr(self, 'detail_frame') and self.detail_frame is not None and self.detail_frame.winfo_exists():
            self.detail_frame.destroy()
            self.detail_frame = None
        self._create_detail_window()

    def _attach_detail(self):
        self.config['detail_detached'] = False
        save_config(self.config)
        if hasattr(self, 'detail_window') and self.detail_window is not None and self.detail_window.winfo_exists():
            self.detail_window.destroy()
            self.detail_window = None
        self._build_detail_panel()

    def _create_detail_window(self):
        c = self.colors
        panel_bg = c.get('panel_bg', c['entry_bg'])
        # geometry_key - стабильный (не переводится), иначе геометрия окна
        # теряется при смене языка
        self.detail_window, content = create_themed_toplevel(
            self.root, t('detail_title'), c, 320, 600,
            attach_callback=self._attach_detail, geometry_key='detail_window')
        for w in content.winfo_children():
            w.destroy()
        detail_scroll = ttk.Scrollbar(content, orient=tk.VERTICAL)
        self.detail_text = tk.Text(content, font=('Consolas', 9), wrap=tk.WORD,
                                  bg=panel_bg, fg=c['fg'],
                                  insertbackground=c['fg'],
                                  selectbackground='#264f78',
                                  relief=tk.FLAT, borderwidth=0,
                                  yscrollcommand=detail_scroll.set,
                                  padx=8, pady=8)
        detail_scroll.configure(command=self.detail_text.yview)
        self.detail_text.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        detail_scroll.pack(side=tk.RIGHT, fill=tk.Y)

    def _detach_console(self):
        self.config['console_detached'] = True
        save_config(self.config)
        if hasattr(self, 'console_container') and self.console_container is not None and self.console_container.winfo_exists():
            self.console_container.destroy()
            self.console_container = None
        if self.console_window is not None:
            try:
                if self.console_window.window.winfo_exists():
                    self.console_window.destroy()
            except Exception:
                pass
            self.console_window = None
        self._create_console_window()

    def _attach_console(self):
        self.config['console_detached'] = False
        save_config(self.config)
        if hasattr(self, 'console_window') and self.console_window is not None:
            try:
                if self.console_window.window.winfo_exists():
                    self.console_window.destroy()
            except Exception:
                pass
            self.console_window = None

    def _create_console_window(self):
        c = self.colors
        self.console_window = ConsoleWindow(self.root, c, detached=True,
                                           content=self.config.get('console_content', 'both'),
                                           on_attach=self._attach_console)

    def _toggle_console_panel(self):
        if self.console_window is not None and self.console_window.window.winfo_exists():
            self.console_window.destroy()
            self.console_window = None
        else:
            self.console_window = ConsoleWindow(self.root, self.colors, detached=True,
                                               content=self.config.get('console_content', 'both'))
    def _apply_theme(self, name):
        self.config['theme'] = name
        save_config(self.config)
        if HAS_SV_TTK:
            sv_theme = 'light' if is_light_theme(name) else 'dark'
            sv_ttk.set_theme(sv_theme)
        setup_scrollbar_style()
        self.colors = COLOR_SCHEMES[resolve_theme(name)]
        self.root.configure(fg_color=self.colors['bg'])
        # Перестроить интерфейс с новыми цветами
        self._rebuild_ui()

    def _apply_language(self, code, label):
        self.config['language'] = code
        save_config(self.config)
        load_locale(code)
        # Полностью перестроить UI
        self._rebuild_ui()
        try:
            from tkinter import messagebox
            messagebox.showinfo('KC Bot', t('lang_applied', name=label))
        except Exception:
            pass

    def _rebuild_ui(self):
        # Уничтожить старые фреймы
        for attr in ('title_bar', 'main_frame'):
            w = getattr(self, attr, None)
            if w is not None:
                try:
                    if w.winfo_exists():
                        w.destroy()
                except Exception:
                    pass
                setattr(self, attr, None)
        # Перестроить
        self._build_title_bar()
        self._build_main()

    def _open_help(self):
        win, content = create_themed_toplevel(self.root, t('help_title'), self.colors, 760, 620,
                                            geometry_key='help_window')
        c = self.colors
        # Панель поиска
        search_frame = tk.Frame(content, bg=c['bg'])
        search_frame.pack(fill=tk.X, padx=10, pady=(10, 5))
        tk.Label(search_frame, text=t('help_search_placeholder'), bg=c['bg'], fg=c['fg'],
                 font=('Segoe UI', 9)).pack(side=tk.LEFT)
        search_var = tk.StringVar()
        search_entry = tk.Entry(search_frame, textvariable=search_var, bg=c['entry_bg'], fg=c['fg'],
                                insertbackground=c['fg'], relief=tk.FLAT, borderwidth=1, width=30)
        search_entry.pack(side=tk.LEFT, padx=(5, 5))
        prev_btn = tk.Button(search_frame, text='◀', bg=c['entry_bg'], fg=c['fg'], relief=tk.FLAT,
                             cursor='hand2', width=3)
        prev_btn.pack(side=tk.LEFT, padx=2)
        next_btn = tk.Button(search_frame, text='▶', bg=c['entry_bg'], fg=c['fg'], relief=tk.FLAT,
                             cursor='hand2', width=3)
        next_btn.pack(side=tk.LEFT, padx=2)
        count_label = tk.Label(search_frame, text='', bg=c['bg'], fg=c['fg'], font=('Segoe UI', 9))
        count_label.pack(side=tk.LEFT, padx=8)

        text_frame = tk.Frame(content, bg=c['bg'])
        text_frame.pack(fill=tk.BOTH, expand=True, padx=10, pady=5)
        scroll = ttk.Scrollbar(text_frame, orient=tk.VERTICAL)
        help_text = tk.Text(text_frame, wrap=tk.WORD, bg=c['entry_bg'], fg=c['fg'],
                            font=('Segoe UI', 10), relief=tk.FLAT, borderwidth=0,
                            yscrollcommand=scroll.set, padx=8, pady=8)
        scroll.configure(command=help_text.yview)
        help_text.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        scroll.pack(side=tk.RIGHT, fill=tk.Y)

        # Справка целиком лежит в локали одним ключом help_body
        # (96 строк). t_raw() не делает .format() - в тексте есть
        # угловые скобки в путях и шаблонах.
        body = t_raw('help_body').split('\n')

        full_text = '\n'.join(body)
        help_text.insert('1.0', full_text)
        help_text.configure(state=tk.DISABLED)

        # Поиск с навигацией
        matches = []

        def do_search(*args):
            nonlocal matches
            query = search_var.get().strip()
            help_text.configure(state=tk.NORMAL)
            help_text.tag_remove('found', '1.0', tk.END)
            help_text.tag_remove('current', '1.0', tk.END)
            matches = []
            if query:
                idx = '1.0'
                while True:
                    pos = help_text.search(query, idx, nocase=True, stopindex=tk.END)
                    if not pos:
                        break
                    end_pos = '%s+%dc' % (pos, len(query))
                    help_text.tag_add('found', pos, end_pos)
                    matches.append((pos, end_pos))
                    idx = end_pos
            help_text.tag_config('found', background='#ffeb3b', foreground='#000000')
            help_text.tag_config('current', background='#ff9800', foreground='#000000')
            if matches:
                # перейти к первому
                help_text.see(matches[0][0])
                help_text.tag_add('current', matches[0][0], matches[0][1])
                help_text.tag_remove('found', matches[0][0], matches[0][1])
                count_label.configure(text='1/%d' % len(matches))
            else:
                count_label.configure(text='0/0' if query else '')
            help_text.configure(state=tk.DISABLED)

        current_index = [0]

        def go_next():
            if not matches:
                return
            current_index[0] = (current_index[0] + 1) % len(matches)
            i = current_index[0]
            pos, end_pos = matches[i]
            help_text.configure(state=tk.NORMAL)
            # восстановить предыдущий в found
            help_text.tag_remove('current', '1.0', tk.END)
            # перетегать все found заново проще: добавить current поверх
            help_text.see(pos)
            help_text.tag_add('current', pos, end_pos)
            count_label.configure(text='%d/%d' % (i + 1, len(matches)))
            help_text.configure(state=tk.DISABLED)

        def go_prev():
            if not matches:
                return
            current_index[0] = (current_index[0] - 1) % len(matches)
            i = current_index[0]
            pos, end_pos = matches[i]
            help_text.configure(state=tk.NORMAL)
            help_text.tag_remove('current', '1.0', tk.END)
            help_text.see(pos)
            help_text.tag_add('current', pos, end_pos)
            count_label.configure(text='%d/%d' % (i + 1, len(matches)))
            help_text.configure(state=tk.DISABLED)

        # Обернуть do_search чтобы сбрасывать индекс
        orig_search = do_search
        def do_search2(*args):
            current_index[0] = 0
            orig_search(*args)
        search_var.trace_add('write', do_search2)
        search_entry.bind('<Return>', lambda e: go_next())
        prev_btn.configure(command=go_prev)
        next_btn.configure(command=go_next)

    def get_params(self):
        # Валидация pop
        if not self._validate_pop():
            return None
        cfg = {}
        try:
            for key, (e, typ) in self.entries.items():
                val = e.get().strip()
                if typ == 'int':
                    cfg[key] = int(float(val))
                else:
                    cfg[key] = float(val)
        except (ValueError, TypeError):
            return None
        cfg['restart'] = self.restart_var.get()
        cfg['launch_after'] = self.launch_after_var.get()
        return cfg

    def _on_param_change(self):
        # Валидация pop
        self._validate_pop()
        # Пересчёт
        self.schedule_calculate()

    # Сообщения хранятся КЛЮЧАМИ локали, а не текстом: класс создаётся при
    # импорте, а язык может смениться позже - переводим в момент показа.
    VALIDATION_RULES = {
        'pop': (50, 500, 'val_pop'),
        'rounds': (1, 50, 'val_rounds'),
        'episode': (10, 300, 'val_episode'),
        'episode_increment': (0, 60, 'val_increment'),
        'speed': (1, 3, 'val_speed'),
        'top_percent': (0.01, 1.0, 'val_top'),
    }

    def _validate_all(self):
        all_valid = True
        for key, (e, typ) in self.entries.items():
            try:
                val = float(e.get().strip())
            except (ValueError, TypeError):
                val = None

            rules = self.VALIDATION_RULES.get(key)
            if rules is None:
                continue

            min_val, max_val, msg = rules
            hint = self._hint_labels.get(key)
            if val is None or val < min_val or val > max_val:
                # CTk-поле: подсветка через fg_color/border_color
                # (bg/highlight* из tk CTk не принимает - колбэк падал)
                e.configure(fg_color='#3a1a1a', border_color='#ff5555')
                if hint:
                    hint.configure(text=t(msg))
                all_valid = False
            else:
                e.configure(fg_color=self.colors['entry_bg'],
                            border_color=self.colors['border'])
                if hint:
                    hint.configure(text='')

        if all_valid:
            self.start_btn.configure(state=tk.NORMAL)
        else:
            self.start_btn.configure(state=tk.DISABLED)
        return all_valid

    def _validate_pop(self):
        return self._validate_all()

    def schedule_calculate(self):
        if hasattr(self, '_calc_after_id'):
            self.root.after_cancel(self._calc_after_id)
        self._calc_after_id = self.root.after(300, self.calculate)

    def _schedule_detail_update(self):
        try:
            self.calculate()
        except Exception:
            pass
        self.root.after(2000, self._schedule_detail_update)


    def calculate(self):
        cfg = self.get_params()
        if cfg is None:
            if hasattr(self, 'result_label'):
                self.result_label.configure(text=t('err_params_check'))
            return
        total, hours, minutes, details = calc_time(cfg)

        if hasattr(self, 'result_label') and self.result_label.winfo_exists():
            self.result_label.configure(text=t('total_time', sec='%.0f' % total,
                                           h='%.1f' % hours, m='%.0f' % minutes))
        if hasattr(self, 'result_detail') and self.result_detail.winfo_exists():
            self.result_detail.configure(
                text=t('total_bots', n=cfg['pop'] * cfg['rounds']))

        # Обновляем обе панели деталей
        for dt in [self.detail_text, getattr(self, 'detail_right_text', None)]:
            if dt is None:
                continue
            dt.delete('1.0', tk.END)
            dt.insert(tk.END, t('sec_params') + '\n')
            dt.insert(tk.END, '  pop: %d\n' % cfg['pop'])
            dt.insert(tk.END, '  rounds: %d\n' % cfg['rounds'])
            dt.insert(tk.END, t('sec_episode', v='%.1f' % cfg['episode']) + '\n')
            dt.insert(tk.END, t('sec_increment', v='%.1f' % cfg['episode_increment']) + '\n')
            dt.insert(tk.END, '  speed: %.1fx\n' % cfg['speed'])
            dt.insert(tk.END, '  restart: %s\n' % cfg['restart'])
            dt.insert(tk.END, '  top-percent: %.4f\n' % cfg['top_percent'])
            dt.insert(tk.END, '\n' + t('sec_rounds') + '\n')
            for rnd, ep, rt in details:
                dt.insert(tk.END, t('round_line', n='%2d' % rnd, ep='%.0f' % ep,
                                    rt='%.0f' % rt, min='%.1f' % (rt / 60.0)) + '\n')

    def start_training(self):
        cfg = self.get_params()
        if cfg is None:
            if hasattr(self, 'result_label'):
                self.result_label.configure(text=t('err_params'))
            return
        # Папка игры: сохраняем и проверяем ДО запуска. Раньше launчер её не
        # проверял вообще - evo.py падал посреди прогона с RuntimeError.
        self._save_game_dir()
        if not self._refresh_game_dir_status():
            msg = t('game_dir_missing')
            if hasattr(self, 'result_label'):
                self.result_label.configure(text=msg)
            if self.console_window is None or not self.console_window.window.winfo_exists():
                self.console_window = ConsoleWindow(self.root, self.colors,
                                                    detached=True, content='both')
            self.console_window.append('!! %s\n' % msg)
            return
        self.config.update(cfg)
        save_config(self.config)
        cmd = build_command(cfg)
        self.start_btn.pack_forget()
        self.stop_btn.pack(side=tk.LEFT, padx=4)

        # Создать окно консоли если нет
        if self.console_window is None or not self.console_window.window.winfo_exists():
            self.console_window = ConsoleWindow(self.root, self.colors, detached=True,
                                               content='both')

        # Очистить консоль (потокобезопасно, через сам объект консоли)
        if self.console_window is not None:
            self.console_window.reset()

        # Запуск процесса. PYTHONUTF8=1 + encoding='utf-8' - иначе чтение
        # вывода падает по UnicodeDecodeError при cp1251/UTF-8 и поток чтения
        # умирает молча: кнопка «Старт» не восстанавливается (аудит).
        env = os.environ.copy()
        env['PYTHONUTF8'] = '1'
        if getattr(sys, 'frozen', False):
            # '<python> evo.py ...' -> '...' для KC_EVO_ARGS (его читает shlex)
            if len(cmd) > 1 and cmd[1] == 'evo.py':
                args_part = ' '.join(cmd[2:])
            else:
                args_part = ' '.join(cmd)
            env['KC_EVO_ARGS'] = args_part
            env['KC_RUN_MODULE'] = 'evo'
            self.process = subprocess.Popen([sys.executable], shell=False, cwd=HERE,
                                           stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                           text=True, bufsize=1, env=env,
                                           encoding='utf-8', errors='replace')
        else:
            self.process = subprocess.Popen(cmd, shell=False, cwd=HERE,
                                           stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                           text=True, bufsize=1, env=env,
                                           encoding='utf-8', errors='replace')
        self.process_running = True
        self.reader_thread = threading.Thread(target=self._read_output, daemon=True)
        self.reader_thread.start()

    def _read_output(self):
        """Читает вывод процесса. Выполняется в ОТДЕЛЬНОМ потоке.

        В Tk-виджеты отсюда не лезем - только queue (потокобезопасно).
        Раньше здесь был .after() на виджете и не было try/except: любая
        ошибка чтения убивала поток молча и «Старт» зависал навсегда.
        """
        if self.process is None:
            return
        try:
            for line in self.process.stdout:
                if not self.process_running:
                    break
                # перечитываем ссылку на каждом шаге: окно консоли могут
                # пересоздать (смена раскладки) - захваченная один раз
                # ссылка увела бы поток в осиротевший объект
                console = self.console_window
                if console is not None:
                    console.append(line)
            self.process.wait()
        except Exception as e:
            console = self.console_window
            if console is not None:
                console.append('\n' + t('msg_read_err', err='%r' % (e,)) + '\n')
        finally:
            self.process_running = False
            # Игра не должна пережить обучение: evo закрывает её сам (с
            # проверкой), но если процесс обучения убит/упал - подстраховка.
            # Аудит 2026-11-20: незакрытая игра висела в фоне ~9 часов.
            try:
                subprocess.run(['taskkill', '/F', '/IM', 'Kingdom.exe'],
                               stdout=subprocess.DEVNULL,
                               stderr=subprocess.DEVNULL)
            except Exception:
                pass
            console = self.console_window
            if console is not None:
                console.append('\n' + t('msg_done') + '\n')
            # кнопки - только через главный поток
            try:
                self.start_btn.after(0, lambda: self.start_btn.pack(side=tk.LEFT, padx=4))
                self.stop_btn.after(0, lambda: self.stop_btn.pack_forget())
            except (tk.TclError, RuntimeError):
                # TclError - виджет уничтожен; RuntimeError - mainloop уже
                # остановлен (закрытие приложения) - оба случая не фатальны
                pass

    def stop_training(self):
        self.process_running = False
        # Убить процесс обучения (evo.py) и все дочерние (боты) по PID
        if hasattr(self, 'process') and self.process and self.process.poll() is None:
            try:
                subprocess.run(['taskkill', '/F', '/T', '/PID', str(self.process.pid)],
                               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            except Exception:
                pass
            try:
                self.process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self.process.kill()
        # Закрыть игру
        try:
            subprocess.run(['taskkill', '/F', '/IM', 'Kingdom.exe'],
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        except Exception:
            pass
        if self.console_window is not None:
            self.console_window.append('\n' + t('msg_stopped') + '\n')
        self.stop_btn.pack_forget()
        self.start_btn.pack(side=tk.LEFT, padx=4)

    def _on_close(self):
        # Сохранить размер/позицию окна
        try:
            cfg = load_config()
            cfg['window_geometry'] = self.root.geometry()
            save_config(cfg)
        except Exception:
            pass
        # Остановить обучение и закрыть игру
        self.stop_training()
        # Убить игру
        try:
            subprocess.run(['taskkill', '/F', '/IM', 'Kingdom.exe'],
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        except Exception:
            pass
        self.root.destroy()

    def _get_panel_order(self):
        """Возвращает список видимых панелей в порядке слева направо."""
        order = []
        if self.config.get('show_detail', True) and not self.config.get('detail_detached', False):
            order.append('detail')
        if self.config.get('show_console', True) and not self.config.get('console_detached', False):
            order.append('console')
        # main всегда в центре
        # Вставляем main в середину
        # Но если обе панели с одной стороны, main может быть с краю
        # Упрощаем: main всегда посередине
        # Перестраиваем: левые панели, main, правые панели
        left = []
        right = []
        for p in order:
            side = self.config.get(p + '_side', 'left' if p == 'detail' else 'right')
            if side == 'left':
                left.append(p)
            else:
                right.append(p)
        return left + ['main'] + right

if __name__ == '__main__':
    # Режим запуска модуля (замороженное exe)
    mod = os.environ.get('KC_RUN_MODULE')
    if mod in ('evo', 'bot3'):
        if mod == 'evo':
            import shlex
            args_str = os.environ.get('KC_EVO_ARGS', '')
            sys.argv = ['evo.py'] + (shlex.split(args_str) if args_str else [])
            import evo
            evo.main()
        else:
            sys.argv = ['bot3.py'] + sys.argv[1:]
            import bot3
            bot3.main()
        sys.exit(0)
    root = ctk.CTk()
    root.title('KC Bot')
    # Масштаб по DPI делает сам CustomTkinter. Старый ручной
    # tk scaling = dpi/72 давал дробные кегли шрифтов («лесенки») - удалён.
    # Иконка
    icon_path = os.path.join(HERE, 'assets', 'kc_bot.ico')
    if os.path.exists(icon_path):
        try:
            root.iconbitmap(icon_path)
        except Exception:
            try:
                from PIL import Image, ImageTk
                img = Image.open(icon_path)
                photo = ImageTk.PhotoImage(img)
                root.iconphoto(True, photo)
                root._icon_photo = photo
            except Exception:
                pass

    # Восстановить размер/позицию окна. KCBOT_UI_GEOMETRY (напр.
    # '-1450+60') принудительно открывает окно на 2-м мониторе слева -
    # пользователь смотрит экран, пока агент запускает приложение.
    saved_geo = load_config().get('window_geometry', None) if 'load_config' in globals() else None
    forced_geo = os.environ.get('KCBOT_UI_GEOMETRY')
    if forced_geo:
        root.geometry(forced_geo)
    elif saved_geo:
        root.geometry(saved_geo)
    else:
        screen_w = root.winfo_screenwidth()
        screen_h = root.winfo_screenheight()
        win_w, win_h = 1000, 700
        x = (screen_w - win_w) // 2
        y = (screen_h - win_h) // 2
        root.geometry('%dx%d+%d+%d' % (win_w, win_h, x, y))
    root.minsize(800, 500)

    # Кастомный заголовок (overrideredirect)
    root.overrideredirect(True)

    # Показать в панели задач
    try:
        GA_ROOT = 2
        hwnd = ctypes.windll.user32.GetAncestor(root.winfo_id(), GA_ROOT)
        if not hwnd:
            hwnd = root.winfo_id()
        GWL_EXSTYLE = -20
        WS_EX_APPWINDOW = 0x00040000
        WS_EX_TOOLWINDOW = 0x00000080
        try:
            get_long = ctypes.windll.user32.GetWindowLongPtrW
            set_long = ctypes.windll.user32.SetWindowLongPtrW
        except AttributeError:
            get_long = ctypes.windll.user32.GetWindowLongW
            set_long = ctypes.windll.user32.SetWindowLongW
        style = get_long(hwnd, GWL_EXSTYLE)
        style = (style & ~WS_EX_TOOLWINDOW) | WS_EX_APPWINDOW
        set_long(hwnd, GWL_EXSTYLE, style)
        SWP_FRAMECHANGED = 0x0020
        ctypes.windll.user32.SetWindowPos(hwnd, 0, 0, 0, 0, 0, SWP_FRAMECHANGED | 0x0001 | 0x0002 | 0x0004)
    except Exception as e:
        print('taskbar error:', e)

    # Установить иконку в панели задач через WM_SETICON
    try:
        hwnd = ctypes.windll.user32.GetAncestor(root.winfo_id(), 2)
        if not hwnd:
            hwnd = root.winfo_id()
        LR_LOADFROMFILE = 0x00000010
        IMAGE_ICON = 1
        hicon = ctypes.windll.user32.LoadImageW(None, icon_path, IMAGE_ICON, 0, 0, LR_LOADFROMFILE)
        if hicon:
            WM_SETICON = 0x0080
            ICON_SMALL = 0
            ICON_BIG = 1
            ctypes.windll.user32.SendMessageW(hwnd, WM_SETICON, ICON_SMALL, hicon)
            ctypes.windll.user32.SendMessageW(hwnd, WM_SETICON, ICON_BIG, hicon)
    except Exception as e:
        print('icon error:', e)

    root.configure(fg_color='#1e1e1e')

    app = App(root)
    root.deiconify()
    root.update()
    root.lift()

    # Установить иконку в панели задач ПОСЛЕ показа окна (иначе не подхватывается)
    def _apply_icon_now():
        try:
            hwnd = ctypes.windll.user32.GetAncestor(root.winfo_id(), 2)
            if not hwnd:
                hwnd = root.winfo_id()
            icon_path = os.path.join(HERE, 'assets', 'kc_bot.ico')
            LR_LOADFROMFILE = 0x00000010
            IMAGE_ICON = 1
            hicon = ctypes.windll.user32.LoadImageW(None, icon_path, IMAGE_ICON, 0, 0, LR_LOADFROMFILE)
            if hicon:
                WM_SETICON = 0x0080
                ctypes.windll.user32.SendMessageW(hwnd, WM_SETICON, 0, hicon)
                ctypes.windll.user32.SendMessageW(hwnd, WM_SETICON, 1, hicon)
            apply_taskbar_style(root)
        except Exception as e:
            print('icon apply error:', e)

    root.after(50, _apply_icon_now)
    root.after(300, _apply_icon_now)

    # Отладочный автоснимок окна: KCBOT_UI_SHOT=<путь.png> - снять себя
    # через1.5 с после старта и выйти (для проверки дизайна скриншотами)
    _shot = os.environ.get('KCBOT_UI_SHOT')
    if _shot:
        _self_shot.root = root
        root.after(1500, lambda: (_self_shot(_shot), root.destroy()))
    root.mainloop()

    # Сохранить геометрию при выходе
    try:
        cfg = load_config()
        cfg['window_geometry'] = root.geometry()
        save_config(cfg)
    except Exception:
        pass
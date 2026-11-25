# -*- coding: utf-8 -*-
"""
launcher.py - лаунчер обучения KingdomBot.

Запуск:
    py launcher.py
"""
import collections
import ctypes
import gamepath
import hashlib
import json
import kc_common
import os
import patcher
import queue
import shutil
import subprocess
import sys
import tempfile
import threading
import tkinter as tk
import urllib.request
import webbrowser
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

APP_VERSION = '1.0.3'
GITHUB_REPO = 'rokki-j-walker/KCbot'
UPDATE_API = 'https://api.github.com/repos/%s/releases/latest' % GITHUB_REPO
UPDATE_PAGE = 'https://github.com/%s/releases/latest' % GITHUB_REPO


def parse_version(text):
    """'v1.2.3' -> (1, 2, 3); устойчиво к мусору."""
    parts = []
    for p in str(text or '').strip().lstrip('vV').split('.'):
        digits = ''.join(ch for ch in p if ch.isdigit())
        parts.append(int(digits) if digits else 0)
    return tuple(parts) or (0,)


def is_newer(tag):
    return parse_version(tag) > parse_version(APP_VERSION)

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

# Средний простой на перезапуск игры/эпизод. Измерено по 600 логам
# прогона 2026-11-24 (pop=50, rounds=6, eval_repeats=2, speed=3):
# 16.36-17.41 с, среднее ≈16.9. Старое значение 30 давало +2 ч 11 мин
# к оценке (11 ч 15 мин вместо фактических 9 ч 04 мин).
RESTART_OVERHEAD = 16.9

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
    'episode': 60.0,        # игровые секунды (D52): старый дефолт 20 был
                            # реальными, на 3x это 60 игровых - свели к ним
    'episode_increment': 5.0,
    'speed': 3.0,           # D7: потолок 3x, на 4x+ игра ломается
    'top_percent': 0.1,
    'restart': True,
    'launch_after': False,
    'optimizer': 'cem',      # CEM/ES вместо GA (2026-11-22)
    'eval_repeats': 2,       # 2 эпизода на геном = меньше шума
    'theme': 'dark',
    'console_content': 'both',
    'show_console': True,
    'show_params': True,
    'show_detail': True,
    'detail_side': 'left',
    'console_side': 'right',
    'language': 'ru',
    'ui_scale': 'auto',      # auto / 1.0 / 1.1 / 1.25
    # D52: episode/increment в ИГРОВЫХ секундах. Маркер есть только у конфигов,
    # записанных уже в новой семантике - по его отсутствию load_config()
    # понимает, что старые значения даны в РЕАЛЬНЫХ секундах, и переводит их.
    'time_semantics': 'game_v1',
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
        'accent': '#2E7D32', 'graph_bg': '#202020', 'graph_fg': '#e0e0e0',
        'title_bg': '#252525', 'btn_hover': '#333333', 'border': '#333333',
        'panel_bg': '#222222', 'muted': '#9a9a9a', 'success': '#4CAF50',
    },
    'light': {
        'bg': '#f0f0f0', 'fg': '#1e1e1e', 'entry_bg': '#ffffff',
        'accent': '#0078D4', 'graph_bg': '#ffffff', 'graph_fg': '#1e1e1e',
        'title_bg': '#e0e0e0', 'btn_hover': '#d0d0d0', 'border': '#cccccc',
        'panel_bg': '#f5f5f5', 'muted': '#666666', 'success': '#2E7D32',
    },
    'blue': {
        'bg': '#0d1b2a', 'fg': '#e0e1dd', 'entry_bg': '#1b263b',
        'accent': '#1565C0', 'graph_bg': '#1b263b', 'graph_fg': '#e0e1dd',
        'title_bg': '#1b263b', 'btn_hover': '#2a3a5a', 'border': '#2a3a5a',
        'panel_bg': '#152238', 'muted': '#9a9a9a', 'success': '#4CAF50',
    },
    'green': {
        'bg': '#0a1f0a', 'fg': '#d4e8d4', 'entry_bg': '#1a2f1a',
        'accent': '#2E7D32', 'graph_bg': '#1a2f1a', 'graph_fg': '#d4e8d4',
        'title_bg': '#1a2f1a', 'btn_hover': '#2a3f2a', 'border': '#2a3f2a',
        'panel_bg': '#152a15', 'muted': '#9a9a9a', 'success': '#4CAF50',
    },
    'white': {
        'bg': '#ffffff', 'fg': '#000000', 'entry_bg': '#f5f5f5',
        'accent': '#000000', 'graph_bg': '#ffffff', 'graph_fg': '#000000',
        'title_bg': '#f0f0f0', 'btn_hover': '#e0e0e0', 'border': '#e0e0e0',
        'panel_bg': '#fafafa', 'muted': '#666666', 'success': '#2E7D32',
    },
    'black': {
        'bg': '#000000', 'fg': '#ffffff', 'entry_bg': '#111111',
        # Не белый акцент: иначе Start/переключатели становятся белыми
        # кнопками с белым текстом. Тёмно-серый читается на чёрном.
        'accent': '#3f3f3f', 'graph_bg': '#000000', 'graph_fg': '#ffffff',
        'title_bg': '#111111', 'btn_hover': '#222222', 'border': '#222222',
        'panel_bg': '#0a0a0a', 'muted': '#9a9a9a', 'success': '#4CAF50',
    },
    # Светлые с акцентами Fluent 2 (D29): нейтрали взяты из ThemeService.cs
    # старой WPF-версии (paper/card/body/divider/border).
    'light_blue': {
        'bg': '#f5f5f5', 'fg': '#1a1a1a', 'entry_bg': '#ffffff',
        'accent': '#0067C0', 'graph_bg': '#ffffff', 'graph_fg': '#1a1a1a',
        'title_bg': '#e0e0e0', 'btn_hover': '#e8e8e8', 'border': '#c4c4c4',
        'panel_bg': '#ffffff', 'muted': '#666666', 'success': '#2E7D32',
    },
    'light_green': {
        'bg': '#f5f5f5', 'fg': '#1a1a1a', 'entry_bg': '#ffffff',
        'accent': '#107C10', 'graph_bg': '#ffffff', 'graph_fg': '#1a1a1a',
        'title_bg': '#e0e0e0', 'btn_hover': '#e8e8e8', 'border': '#c4c4c4',
        'panel_bg': '#ffffff', 'muted': '#666666', 'success': '#2E7D32',
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
H_BTN = 34           # высота ВСЕХ содержательных кнопок (компактно)
H_INPUT = 34         # высота полей ввода (= кнопке - строки выровнены)
FS_TITLE = 18        # название окна
FS_SECTION = 15      # заголовок раздела (Параметры, Детали, Папка игры)
FS_BODY = 13         # подписи, переключатели, кнопки, статус-кнопки
FS_CAPTION = 11      # комментарии под полями, статусы
FS_VALUE = 16        # значение в поле (моно, вправо)
FS_MONO = 13         # моно-тела: результат, консоль
FS_DETAIL = 9        # панель «Детали» — заметно мельче основного текста
FS_BIG = 12          # финальное время — чуть крупнее текста «Деталей»
PAD_CARD = 12        # внутренний отступ карточек/разделов
GAP_SECTION = 16     # вертикальный ритм между блоками

# ---------- Иконки (моно-глифы Windows) ----------
# Segoe Fluent Icons (Win11) / Segoe MDL2 Assets (Win10) - одинаковые
# коды; если нет ни одного, падаем на Segoe UI (глифы не рисуются, но
# приложение не падает). Иконки заменяют текстовые кнопки, как просил
# пользователь («сделай значки, в стиле тех что уже имеются»).
ICON_SEARCH = '\uE721'
ICON_FOLDER = '\uE8B7'
ICON_PATCH = '\uE90F'
ICON_UNPATCH = '\uE7A7'
ICON_PREV = '\uE76B'
ICON_NEXT = '\uE76C'
ICON_CLEAR = '\uE74D'
ICON_COPY = '\uE8C8'
ICON_SAVE = '\uE74E'
ICON_CONSOLE = '\uE756'
ICON_HELP = '\uE897'
ICON_UPDATE = '\uE72C'      # Sync — проверка обновлений
_ICON_FAMILY = None


def icon_family():
    """Семейство иконочного шрифта (кэшируется после первого окна)."""
    global _ICON_FAMILY
    if _ICON_FAMILY is None:
        try:
            import tkinter.font as tkfont
            fams = set(tkfont.families())
            if 'Segoe Fluent Icons' in fams:
                _ICON_FAMILY = 'Segoe Fluent Icons'
            elif 'Segoe MDL2 Assets' in fams:
                _ICON_FAMILY = 'Segoe MDL2 Assets'
            else:
                _ICON_FAMILY = 'Segoe UI'
        except Exception:
            _ICON_FAMILY = 'Segoe UI'
    return _ICON_FAMILY


def I(size=FS_VALUE):
    """Кэшированный иконочный шрифт (как F, но семейство глифов)."""
    key = ('icon', size)
    if key not in _FONT_CACHE:
        _FONT_CACHE[key] = ctk.CTkFont(family=icon_family(), size=size)
    return _FONT_CACHE[key]


class Tooltip(object):
    """Всплывающая подсказка для кнопок-иконок (иначе смысл неочевиден)."""

    def __init__(self, widget, text, colors=None, delay=450):
        self.widget = widget
        self.text = text
        self.colors = colors or {}
        self.delay = delay
        self._after_id = None
        self._tip = None
        widget.bind('<Enter>', self._schedule, add='+')
        widget.bind('<Leave>', self._hide, add='+')
        widget.bind('<ButtonPress>', self._hide, add='+')

    def _schedule(self, _event=None):
        self._cancel()
        if self.text:
            self._after_id = self.widget.after(self.delay, self._show)

    def _cancel(self):
        if self._after_id is not None:
            try:
                self.widget.after_cancel(self._after_id)
            except Exception:
                pass
            self._after_id = None

    def _show(self):
        self._after_id = None
        if self._tip is not None or not self.text:
            return
        try:
            tip = tk.Toplevel(self.widget)
            tip.overrideredirect(True)
            tip.attributes('-topmost', True)
            bg = self.colors.get('title_bg', '#2b2b2b')
            fg = self.colors.get('fg', '#ffffff')
            label = tk.Label(tip, text=self.text, bg=bg, fg=fg,
                             font=FT(FS_CAPTION, 'Segoe UI'),
                             padx=8, pady=4, relief=tk.FLAT)
            label.pack()
            x = (self.widget.winfo_rootx()
                 + self.widget.winfo_width() // 2
                 - label.winfo_reqwidth() // 2)
            y = self.widget.winfo_rooty() + self.widget.winfo_height() + 5
            tip.geometry('+%d+%d' % (x, y))
            self._tip = tip
        except Exception:
            self._tip = None

    def _hide(self, _event=None):
        self._cancel()
        if self._tip is not None:
            try:
                self._tip.destroy()
            except Exception:
                pass
            self._tip = None


def icon_button(parent, glyph, command, colors, tooltip=None,
                width=H_BTN, height=H_BTN, corner_radius=4,
                hover_color=None, fg_color='transparent',
                text_color=None, size=FS_VALUE):
    """Кнопка-значок в едином стиле (прозрачная, hover как у шапки)."""
    btn = ctk.CTkButton(
        parent, text=glyph, command=command, width=width, height=height,
        corner_radius=corner_radius, fg_color=fg_color,
        hover_color=hover_color or colors['btn_hover'],
        text_color=text_color or colors['fg'], font=I(size))
    if tooltip:
        Tooltip(btn, tooltip, colors)
    return btn


class FluentOptionMenu(ctk.CTkOptionMenu):
    """CTkOptionMenu, где текущий пункт в раскрытом списке помечен ●.

    Требование пользователя: «текущий выбранный элемент должен быть
    отмечен точкой слева от элемента». Пункты пересобираются перед
    каждым открытием - значение могло измениться программно.
    """

    def _open_dropdown_menu(self):
        current = self.get()
        menu = self._dropdown_menu
        menu.delete(0, 'end')
        for value in self._values:
            mark = '\u25cf  ' if value == current else '    '
            menu.add_command(
                label=mark + value.ljust(18),
                command=lambda v=value: self._dropdown_callback(v))
        self._dropdown_menu.open(
            self.winfo_rootx(),
            self.winfo_rooty()
            + self._apply_widget_scaling(self._current_height + 0))
        self._close_on_next_click = True


class SearchBar(ctk.CTkFrame):
    """Единая строка поиска: [поле][лупа][◀][▶][счётчик].

    Одинаковая в консоли и справке (требование пользователя: «поиск как
    в справке, со стрелочками; вместо слова найти - лупа»). Подсвечивает
    все совпадения, текущее - отдельным цветом; Enter/▶ - далее,
    Shift+Enter/◀ - назад; счётчик показывает «текущее/всего».
    """

    def __init__(self, parent, colors, text_widget=None, placeholder='',
                 readonly=False):
        super().__init__(parent, fg_color='transparent', corner_radius=0)
        self.colors = colors
        self.text = text_widget
        self.readonly = readonly
        self.matches = []
        self.current = -1

        # placeholder CTkEntry несовместим с textvariable (баг CTk:
        # _activate_placeholder сравнивает StringVar с ""), поэтому
        # работаем напрямую с полем ввода.
        self.entry = ctk.CTkEntry(
            self, placeholder_text=placeholder,
            height=H_BTN, font=F(FS_BODY), corner_radius=4,
            fg_color=colors.get('entry_bg'), text_color=colors.get('fg'),
            placeholder_text_color=colors.get('muted', '#9a9a9a'),
            border_color=colors.get('border'))
        self.entry.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(0, 6))

        self.find_btn = icon_button(self, ICON_SEARCH, self.next, colors,
                                    tooltip=t('tooltip_search'))
        # Кнопки прижаты к ПРАВОМУ краю (pack side=RIGHT в обратном
        # порядке), а поле тянется на всю оставшуюся ширину - иначе
        # справа оставалась пустая полоса (замечание пользователя).
        self.next_btn = icon_button(self, ICON_NEXT, self.next, colors,
                                    tooltip=t('tooltip_next'))
        self.next_btn.pack(side=tk.RIGHT, padx=(0, 4))
        self.prev_btn = icon_button(self, ICON_PREV, self.prev, colors,
                                    tooltip=t('tooltip_prev'))
        self.prev_btn.pack(side=tk.RIGHT, padx=(0, 4))
        self.find_btn.pack(side=tk.RIGHT, padx=(0, 4))

        self.count_label = ctk.CTkLabel(
            self, text='', font=F(FS_CAPTION),
            text_color=self.colors.get('muted', '#9a9a9a'), anchor='e')
        # Счётчик упаковывается ТОЛЬКО при непустом запросе: иначе он
        # резервировал пустую полосу справа, и поиск не выглядел
        # растянутым во всю ширину окна (замечание пользователя).
        self._count_packed = False

        self.entry.bind('<Return>', lambda e: self.next())
        self.entry.bind('<Shift-Return>', lambda e: self.prev())
        self.entry.bind('<Escape>', lambda e: self._escape())
        self.entry.bind('<KeyRelease>', lambda e: self.refresh())
        if text_widget is not None:
            try:
                text_widget.tag_configure('found', background='#6b5400',
                                          foreground='#ffffff')
                text_widget.tag_configure('current', background='#ff9800',
                                          foreground='#000000')
            except tk.TclError:
                pass
            text_widget.bind('<Control-f>', self._focus, add='+')

    def _focus(self, _event=None):
        self.entry.focus_set()
        try:
            self.entry.select_range(0, tk.END)
        except (tk.TclError, AttributeError):
            pass
        return 'break'

    def _escape(self):
        self.entry.delete(0, tk.END)
        self.entry.focus_set()
        self.refresh()
        return 'break'

    def _set_readonly_state(self, state):
        if self.text is not None and self.readonly:
            try:
                self.text.configure(state=state)
            except tk.TclError:
                pass

    def _clear_tags(self):
        if self.text is None:
            return
        try:
            self.text.tag_remove('found', '1.0', tk.END)
            self.text.tag_remove('current', '1.0', tk.END)
        except tk.TclError:
            pass

    def refresh(self):
        """Пересобрать совпадения (вызывается на каждый ввод символа)."""
        query = self.entry.get().strip()
        self.matches = []
        self.current = -1
        if self.text is None:
            self._update_count()
            return
        self._set_readonly_state(tk.NORMAL)
        self._clear_tags()
        if query:
            idx = '1.0'
            while True:
                try:
                    pos = self.text.search(query, idx, stopindex=tk.END,
                                           nocase=True)
                except tk.TclError:
                    break
                if not pos:
                    break
                end = '%s+%dc' % (pos, len(query))
                try:
                    self.text.tag_add('found', pos, end)
                except tk.TclError:
                    break
                self.matches.append((pos, end))
                idx = end
        self._set_readonly_state(tk.DISABLED)
        if self.matches:
            self.current = 0
            self._highlight_current()
        else:
            self._update_count()

    def reset_matches(self):
        """Текст очищен/перестроен - сбросить подсветку и счётчик."""
        self.matches = []
        self.current = -1
        self._clear_tags()
        self._update_count()

    def _highlight_current(self):
        if self.text is None or not self.matches or self.current < 0:
            return
        pos, end = self.matches[self.current]
        self._set_readonly_state(tk.NORMAL)
        try:
            self.text.tag_remove('current', '1.0', tk.END)
            self.text.tag_add('current', pos, end)
            self.text.see(pos)
        except tk.TclError:
            pass
        self._set_readonly_state(tk.DISABLED)
        self._update_count()

    def next(self):
        if not self.matches:
            self.refresh()
        if not self.matches:
            return 'break'
        self.current = (self.current + 1) % len(self.matches)
        self._highlight_current()
        return 'break'

    def prev(self):
        if not self.matches:
            self.refresh()
        if not self.matches:
            return 'break'
        self.current = (self.current - 1) % len(self.matches)
        self._highlight_current()
        return 'break'

    def _update_count(self):
        query = self.entry.get().strip()
        if not query:
            text = ''
        elif self.matches and self.current >= 0:
            text = '%d/%d' % (self.current + 1, len(self.matches))
        else:
            text = '0/0'
        try:
            self.count_label.configure(text=text)
            if text and not self._count_packed:
                self.count_label.pack(side=tk.RIGHT, padx=(6, 8))
                self._count_packed = True
            elif not text and self._count_packed:
                self.count_label.pack_forget()
                self._count_packed = False
        except tk.TclError:
            pass



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


_UI_SCALE_OVERRIDE = None   # None = системный масштаб


def set_ui_scale(value):
    """Задать ручной масштаб окна (None/'auto' = системный)."""
    global _UI_SCALE_OVERRIDE
    try:
        _UI_SCALE_OVERRIDE = (None if value in (None, '', 'auto')
                              else float(value))
    except (TypeError, ValueError):
        _UI_SCALE_OVERRIDE = None


def win_ui_scale():
    """Масштаб интерфейса Windows из реестра (125% -> 1.25).

    GetDpiForSystem в процессе без DPI-awareness честно возвращает96 -
    врёт; AppliedDPI в HKCU всегда отражает выбранный пользователем
    масштаб. Читаем его и подстраиваем CTk/геометрию (жалоба: «при125%
    не всё влезает»).
    """
    if _UI_SCALE_OVERRIDE is not None:
        return _UI_SCALE_OVERRIDE
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
        # D52: миграция семантики episode (реальные -> игровые секунды).
        # Старый конфиг не содержит time_semantics, значит episode/increment
        # записаны в РЕАЛЬНЫХ секундах. На 3x старые 20 реальных = 60 игровых,
        # поэтому переводим умножением на скорость - обучение не меняется.
        # Мигрируем ТОЛЬКО присутствующие ключи: отсутствующие позже возьмут
        # новый игровой дефолт из DEFAULTS и умножать их нельзя.
        if 'time_semantics' not in data:
            sp = min(3.0, max(1.0, float(data.get('speed', 1.0) or 1.0)))
            for key in ('episode', 'episode_increment'):
                if key in data:
                    try:
                        data[key] = float(data[key]) * sp
                    except (TypeError, ValueError):
                        pass
            data['time_semantics'] = 'game_v1'
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
           '--top-percent', str(cfg['top_percent']),
           '--optimizer', str(cfg.get('optimizer', 'cem')),
           '--eval-repeats', str(int(cfg.get('eval_repeats', 2)))]
    cmd.append('--restart' if cfg.get('restart', True) else '--no-restart')
    return cmd


def calc_time(params):
    """Оценка прогона. episode задан в ИГРОВЫХ секундах.

    Реальное время  = Σ (episode / скорость + перезапуск) × ботов × оценок
    Игровое время   = Σ episode × ботов × оценок   (от скорости не зависит)

    Перезапуск игры (RESTART_OVERHEAD) - реальные секунды, на скорость
    НЕ делится: игра грузится одинаково долго при 1x и 3x.
    """
    pop = params['pop']
    rounds = params['rounds']
    episode = params['episode']
    increment = params['episode_increment']
    speed = min(3.0, max(1.0, float(params.get('speed', 1.0))))
    restart_overhead = RESTART_OVERHEAD if params['restart'] else 0.0
    repeats = max(1, int(params.get('eval_repeats',
                                     DEFAULTS.get('eval_repeats', 1))))

    real_seconds = 0.0
    game_seconds = 0.0
    details = []
    for rnd in range(1, rounds + 1):
        ep = episode + increment * rnd
        round_game = pop * ep * repeats
        round_real = pop * (ep / speed + restart_overhead) * repeats
        game_seconds += round_game
        real_seconds += round_real
        details.append((rnd, ep, round_real, round_game))

    hours = real_seconds / 3600.0
    minutes = (real_seconds % 3600) / 60.0
    return real_seconds, game_seconds, hours, minutes, details


def make_resizable(win, title_color=None, body_color=None):
    """Ресайз окна за края/углы БЕЗ белой рамки Windows.

    WS_THICKFRAME включает нативный ресайз, но Windows 11 рисует у
    такого окна светлую полосу сверху (жалоба пользователя). Поэтому
    ресайз сделан «хватами»: тонкие невидимые Frame по краям/углам с
    нужными курсорами. Цвет хвата = фон под ним (шапка или тело),
    поэтому полос не видно, а тянутся края как у обычного окна.
    """
    if title_color:
        win._resize_title = title_color
    if body_color:
        win._resize_body = body_color
    title_color = title_color or getattr(win, '_resize_title', '#252525')
    body_color = body_color or getattr(win, '_resize_body', '#1e1e1e')

    def _color_for(name):
        return (title_color if ('n' in name or name.endswith('_top'))
                else body_color)

    grips = getattr(win, '_resize_grips', None)
    if grips:
        # уже установлены (повторный вызов после сворачивания/темы) -
        # только обновляем цвета
        win._resize_title = title_color
        win._resize_body = body_color
        for name, frame in grips.items():
            try:
                frame.configure(bg=_color_for(name))
                frame.lift()
            except tk.TclError:
                pass
        return

    size = max(4, int(round(5 * win_ui_scale())))
    state = {}
    win._resize_grips = {}

    def _start(event, name):
        state.clear()
        state.update(name=name, x=event.x_root, y=event.y_root,
                     wx=win.winfo_x(), wy=win.winfo_y(),
                     ww=win.winfo_width(), wh=win.winfo_height())
        return 'break'

    def _drag(event):
        if not state:
            return 'break'
        name = state['name']
        dx = event.x_root - state['x']
        dy = event.y_root - state['y']
        wx, wy = state['wx'], state['wy']
        ww, wh = state['ww'], state['wh']
        try:
            mw, mh = win.minsize()
        except tk.TclError:
            mw, mh = 320, 240
        mw = max(mw, 320)
        mh = max(mh, 240)
        nx, ny, nw, nh = wx, wy, ww, wh
        if 'w' in name:
            nw = ww - dx
            if nw < mw:
                nw = mw
            nx = wx + ww - nw
        if 'e' in name:
            nw = max(mw, ww + dx)
        if 'n' in name:
            nh = wh - dy
            if nh < mh:
                nh = mh
            ny = wy + wh - nh
        if 's' in name:
            nh = max(mh, wh + dy)
        win.geometry('%dx%d+%d+%d' % (nw, nh, nx, ny))
        return 'break'

    def _end(_event=None):
        state.clear()
        return 'break'

    def _mk(name, cursor):
        frame = tk.Frame(win, bg=_color_for(name), cursor=cursor, bd=0,
                         highlightthickness=0, takefocus=0)
        frame.bind('<ButtonPress-1>', lambda e, n=name: _start(e, n))
        frame.bind('<B1-Motion>', _drag)
        frame.bind('<ButtonRelease-1>', _end)
        frame.lift()
        win._resize_grips[name] = frame

    for name, cursor in (('n', 'top_side'), ('s', 'bottom_side'),
                         ('w_top', 'left_side'), ('w_body', 'left_side'),
                         ('e_top', 'right_side'), ('e_body', 'right_side'),
                         ('nw', 'top_left_corner'),
                         ('ne', 'top_right_corner'),
                         ('sw', 'bottom_left_corner'),
                         ('se', 'bottom_right_corner')):
        _mk(name, cursor)

    def _layout(_event=None):
        try:
            W = win.winfo_width()
            H = win.winfo_height()
            hh = int(round(H_HEADER * win_ui_scale()))
            hh = max(size * 2, min(hh, H))
            g = size
            boxes = {
                'n': (0, 0, W, g),
                's': (0, H - g, W, g),
                'w_top': (0, 0, g, hh),
                'w_body': (0, hh, g, max(1, H - hh)),
                'e_top': (W - g, 0, g, hh),
                'e_body': (W - g, hh, g, max(1, H - hh)),
                'nw': (0, 0, g * 2, g * 2),
                'ne': (W - g * 2, 0, g * 2, g * 2),
                'sw': (0, H - g * 2, g * 2, g * 2),
                'se': (W - g * 2, H - g * 2, g * 2, g * 2),
            }
            for name, frame in win._resize_grips.items():
                x, y, w, h = boxes[name]
                frame.place(x=x, y=y, width=w, height=h)
                frame.lift()
        except (tk.TclError, KeyError):
            pass

    win.bind('<Configure>', _layout, add='+')
    win.after_idle(_layout)


def _window_minimize(win):
    """Свернуть окно с overrideredirect (с восстановлением стиля)."""
    try:
        win.overrideredirect(False)
        win.iconify()

        def _on_map(_event=None):
            win.unbind('<Map>')
            win.after(100, lambda: _finish_restore(win))

        def _finish_restore(w):
            if w.state() in ('normal', 'zoomed'):
                w.overrideredirect(True)
                make_resizable(w)      # wm снял стиль - возвращаем ресайз
                w.update_idletasks()
                w.update()
                apply_taskbar_style(w)
            else:
                w.after(50, lambda: _finish_restore(w))

        win.bind('<Map>', _on_map)
    except tk.TclError:
        pass


def _window_toggle_max(win):
    """Развернуть/вернуть (та же логика, что у главного окна)."""
    try:
        if getattr(win, '_maximized', False):
            win.geometry(getattr(win, '_normal_geo', '800x600+0+0'))
            win._maximized = False
        else:
            win._normal_geo = win.geometry()
            win.geometry('%dx%d+0+0' % (win.winfo_screenwidth(),
                                       win.winfo_screenheight()))
            win._maximized = True
    except tk.TclError:
        pass


def create_themed_toplevel(parent, title, colors, width=800, height=600,
                           geometry_key=None, window_icon=None,
                           resizable=True):
    """Toplevel с шапкой в стиле ГЛАВНОГО окна (Fluent, единые токены).

    resizable=True только для консоли: главное окно и справка имеют
    фиксированный размер (требование пользователя).
    """
    win = tk.Toplevel(parent)
    win.title(title)
    win.overrideredirect(True)
    if resizable:
        make_resizable(win, colors['title_bg'], colors['bg'])
    if geometry_key is None:
        geometry_key = title
    win._geometry_key = geometry_key
    # Восстановить сохранённую геометрию
    _saved = load_config().get('window_geometries', {}).get(geometry_key)
    if _saved:
        # Сохранённый пользователем размер/позиция уважается, НО если
        # окно было сохранено на другом мониторе, открываем его на
        # мониторе главного окна (иначе консоль/справка уезжают на
        # основной экран при запуске на втором).
        import re as _re
        m = _re.match(r'(\d+)x(\d+)([+-]\d+)([+-]\d+)$', _saved)
        recenter = False
        if m:
            def _sign(value):
                return 1 if value >= 0 else -1
            try:
                pm = _re.search(r'([+-]\d+)[+-]\d+$', parent.geometry())
                parent_x = int(pm.group(1)) if pm else parent.winfo_x()
            except Exception:
                parent_x = parent.winfo_x()
            if _sign(int(m.group(3))) != _sign(parent_x):
                recenter = True
        if recenter:
            sw, sh = int(m.group(1)), int(m.group(2))
            try:
                px = parent.winfo_x(); py = parent.winfo_y()
                pw = parent.winfo_width(); ph = parent.winfo_height()
                win.geometry('%dx%d+%d+%d'
                             % (sw, sh, px + (pw - sw) // 2,
                                py + (ph - sh) // 2))
            except Exception:
                win.geometry('%dx%d' % (sw, sh))
        else:
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

    # --- шапка как у главного окна ---------------------------------
    title_bar = ctk.CTkFrame(win, height=H_HEADER, corner_radius=0,
                             fg_color=colors['title_bg'])
    title_bar.pack(fill=tk.X, side=tk.TOP)
    title_bar.pack_propagate(False)

    if window_icon:
        ctk.CTkLabel(title_bar, text=window_icon, font=I(FS_VALUE),
                     text_color=colors['fg'], width=H_HEADER).pack(
            side=tk.LEFT, padx=(4, 0))
    title_label = ctk.CTkLabel(title_bar, text=title,
                               font=F(FS_TITLE, 'bold'),
                               text_color=colors['fg'], anchor='w')
    title_label.pack(side=tk.LEFT, padx=(6, 0))

    def _cap(text, cmd, hover=None):
        btn = ctk.CTkButton(title_bar, text=text, width=46,
                            height=H_HEADER, corner_radius=0,
                            fg_color='transparent',
                            text_color=colors['fg'],
                            hover_color=hover or colors['btn_hover'],
                            font=F(FS_BODY), command=cmd)
        btn.pack(side=tk.RIGHT)
        return btn

    # крайние справа - управление окном
    _cap('\u2715', win.destroy, '#e81123')
    if resizable:
        _cap('\u25A1', lambda w=win: _window_toggle_max(w))
    _cap('\u2014', lambda w=win: _window_minimize(w))

    # Служебные значки (очистить/копировать/сохранить...). Окна кладут
    # их ПРЯМО В ШАПКУ: вложенный CTk-фрейм не авто-размерился
    # (propagate у CTk не работает), и значки в узком окне исчезали.
    win._title_bar = title_bar
    win._title_service = title_bar

    # Перетаскивание
    def start_move(event):
        win._drag_x = event.x_root - win.winfo_x()
        win._drag_y = event.y_root - win.winfo_y()

    def do_move(event):
        if getattr(win, '_maximized', False):
            return
        win.geometry('+%d+%d' % (event.x_root - win._drag_x,
                                event.y_root - win._drag_y))

    for w in (title_bar, title_label):
        w.bind('<Button-1>', start_move)
        w.bind('<B1-Motion>', do_move)

    # Панель задач
    try:
        win.update_idletasks()
        apply_taskbar_style(win)
    except Exception:
        pass
    # HWND окончательно готов после первого показа - процедура ресайза
    # ставится ещё раз (ранняя установка могла попасть в дочерний HWND)
    if resizable:
        make_resizable(win)
        win.bind('<Map>',
                 lambda e, w=win: w.after(30, lambda: make_resizable(w)),
                 add='+')

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
                 on_finish=None):
        # content/on_finish оставлены для совместимости со старыми
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
                parent, t('console_title'), colors, 980, 700,
                geometry_key='console_window',
                window_icon=ICON_CONSOLE)
        else:
            self.window = tk.Frame(parent, bg=colors['bg'])
            self._content = self.window

        self._build_ui()
        if detached:
            self._build_title_actions()
        self._tick()

    # ------------------------------------------------------------- построение
    def _build_ui(self):
        c = self.colors

        # --- верхняя панель: фильтр + автоскролл + счётчик ------------
        bar = ctk.CTkFrame(self._content, fg_color='transparent',
                           corner_radius=0)
        bar.pack(fill=tk.X, padx=PAD_CARD, pady=(PAD_CARD, 8))

        self._filter_labels = [t('console_filter_all'),
                               t('console_filter_warn'),
                               t('console_filter_error')]
        # None = показывать всё; иначе - только эти уровни и выше по важности
        self._filter_levels = [None, ('warn', 'error'), ('error',)]
        self.filter_var = tk.StringVar(value=self._filter_labels[0])

        flt = FluentOptionMenu(bar, values=self._filter_labels,
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
        flt.pack(side=tk.LEFT, padx=(0, 16))

        self.follow_var = tk.BooleanVar(value=True)
        ctk.CTkSwitch(bar, text=t('console_autoscroll'),
                      variable=self.follow_var,
                      command=self._follow_toggled, font=F(FS_BODY),
                      text_color=c['fg'], progress_color=c['accent']
                      ).pack(side=tk.LEFT)

        self.status_var = tk.StringVar(value=t('console_status', n=0))
        ctk.CTkLabel(bar, textvariable=self.status_var, font=F(FS_CAPTION),
                     text_color=c.get('muted', '#9a9a9a'), anchor='e').pack(
            side=tk.RIGHT, padx=(8, 2))

        # --- текст + панель поиска снизу (как в справке) --------------
        body = tk.Frame(self._content, bg=c['bg'])
        scroll = ttk.Scrollbar(body, orient=tk.VERTICAL)
        self.console_text = tk.Text(body, font=FT(FS_MONO),
                                    bg=c['entry_bg'], fg=c['fg'],
                                    insertbackground=c['fg'],
                                    selectbackground='#264f78',
                                    relief=tk.FLAT, borderwidth=1,
                                    wrap=tk.WORD,
                                    yscrollcommand=self._on_yscroll)
        scroll.configure(command=self._scrollbar_cmd)
        # скроллбар упакован ПЕРВЫМ: иначе текст с expand забирает всю
        # ширину и полоса прокрутки получает 0px (её не видно)
        scroll.pack(side=tk.RIGHT, fill=tk.Y)
        self.console_text.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        self._scrollbar = scroll

        self.console_text.tag_config('found', background='#6b5400',
                                     foreground='#ffffff')
        # пользователь сам крутит - гасим автоскролл
        self.console_text.bind('<MouseWheel>', self._on_user_scroll, add='+')
        scroll.bind('<ButtonRelease-1>', self._on_user_scroll, add='+')
        scroll.bind('<B1-Motion>', self._on_user_scroll, add='+')

        # поиск ПЕРЕД телом: pack(side=BOTTOM) резервирует нижнюю полосу
        self.search_bar = SearchBar(self._content, c, self.console_text,
                                    placeholder=t('console_search'))
        self.search_bar.pack(side=tk.BOTTOM, fill=tk.X,
                             padx=PAD_CARD, pady=(0, PAD_CARD))
        body.pack(fill=tk.BOTH, expand=True, padx=PAD_CARD, pady=(0, 6))

    def _build_title_actions(self):
        """Значки операций в шапке окна (очистить/копировать/сохранить)."""
        frame = getattr(self.window, '_title_service', None)
        if frame is None:
            return
        c = self.colors
        specs = [(ICON_CLEAR, self.clear, t('console_clear')),
                 (ICON_COPY, self.copy_all, t('console_copy')),
                 (ICON_SAVE, self.export, t('console_export'))]
        # side=RIGHT: первый упакованный встаёт ближе к кнопкам окна,
        # поэтому идём в обратном порядке - слева направо будет
        # очистить, копировать, сохранить
        for glyph, cmd, tip in reversed(specs):
            icon_button(frame, glyph, cmd, c, tooltip=tip,
                        width=H_HEADER, height=H_HEADER,
                        corner_radius=0).pack(side=tk.RIGHT, padx=(2, 0))

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
        if getattr(self, 'search_bar', None) is not None:
            self.search_bar.reset_matches()
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
        if getattr(self, 'search_bar', None) is not None:
            self.search_bar.reset_matches()

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
        """Совместимость: поиск теперь в единой строке SearchBar."""
        if getattr(self, 'search_bar', None) is not None:
            self.search_bar.next()

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
        # ttk-скроллбары: без явной темы они светлые даже в тёмном UI
        # (замечание по скриншотам). sv_ttk + наши цвета.
        if HAS_SV_TTK:
            try:
                sv_ttk.set_theme('light' if is_light_theme(
                    self.config.get('theme', 'dark')) else 'dark')
            except Exception:
                pass
        setup_scrollbar_style()
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
        self._scale_ready = False
        self._update_busy = False
        # Сохраняем позицию/размер, применённые при старте (2-й монитор),
        # а не сбрасываем в 1000x700 — иначе окно уезжает на основной.
        self._normal_geometry = self.root.geometry() or '1000x700'
        self.detail_panel = None
        self.console_panel = None

        root.geometry(self._normal_geometry)
        root.minsize(800, 500)
        root.configure(fg_color=self.colors['bg'])

        # Кастомный заголовок (overrideredirect) — отключено, окно не показывалось
        # # root.overrideredirect(True)

        self._build_title_bar()
        self._build_main()
        # Теперь список масштаба можно обрабатывать (иначе стартовый
        # command у OptionMenu мог вызвать лишний перезапуск).
        self._scale_ready = True
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
            self.schedule_calculate()
            return
        self._speed_seen = val
        # Скорость меняет игровое время (см. «Детали»), поэтому пересчёт
        # нужен сразу, не дожидаясь следующего изменения другого поля.
        self.schedule_calculate()
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
        if self.config.get('optimizer') not in ('cem', 'ga'):
            self.config['optimizer'] = DEFAULTS.get('optimizer', 'cem')
            changed = True
        try:
            rep = int(self.config.get('eval_repeats', 2))
        except (TypeError, ValueError):
            rep = 2
        rep = min(3, max(1, rep))
        if self.config.get('eval_repeats') != rep:
            self.config['eval_repeats'] = rep
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

        # крайние справа - управление окном; разворачивать нельзя:
        # размер главного окна фиксирован (только консоль ресайзится)
        _cap('\u2715', self.root.destroy, 46, hover='#e81123')
        _cap('\u2014', self.minimize, 46)
        # сразу левее них - Справка и Обновить: обе значками, смысл
        # даёт подсказка (как у кнопок консоли)
        self.help_btn = icon_button(
            self.title_bar, ICON_HELP, self._open_help, c,
            width=H_HEADER, height=H_HEADER, corner_radius=0)
        self.help_btn.pack(side=tk.RIGHT)
        Tooltip(self.help_btn, t('help_title'), c)
        # «Обновить» — значок, а не текст (как у кнопок консоли):
        # смысл даёт подсказка, статус (Проверка.../Скачивание...) тоже
        # уходит в подсказку, сам значок не меняется.
        self.update_btn = icon_button(
            self.title_bar, ICON_UPDATE, self._check_updates, c,
            width=H_HEADER, height=H_HEADER, corner_radius=0)
        self.update_btn.pack(side=tk.RIGHT)
        self.update_tooltip = Tooltip(self.update_btn, t('update_check'), c)

        # Тема и Язык - списки в стиле фильтра консоли (эталон пользователя):
        # видно текущее значение, выбор из списка, масштаб вместе с Windows
        cur_theme = self.config.get('theme', 'dark')
        theme_names = list(THEME_KEYS)
        theme_labels = [t(THEME_KEYS[n]) for n in theme_names]
        theme_var = tk.StringVar(
            value=(theme_labels[theme_names.index(cur_theme)]
                   if cur_theme in theme_names else theme_labels[0]))
        theme_menu = FluentOptionMenu(
            self.title_bar, values=theme_labels, variable=theme_var,
            width=200, height=36, corner_radius=4, font=F(FS_BODY),
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
        lang_menu = FluentOptionMenu(
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

        # Масштаб окна — левее языка (пользователь: «тоже списком,
        # слева от языка»). Смена перезапускает лаунчер.
        ui_codes = ['auto', '1.0', '1.1', '1.25']
        ui_labels = [t('ui_scale_auto'), '100%', '110%', '125%']
        cur_scale = str(self.config.get('ui_scale', 'auto'))
        if cur_scale not in ui_codes:
            cur_scale = 'auto'
        scale_var = tk.StringVar(
            value=ui_labels[ui_codes.index(cur_scale)])
        self._ui_scale_var = scale_var
        self._ui_scale_labels = (ui_codes, ui_labels)
        scale_menu = FluentOptionMenu(
            self.title_bar, values=ui_labels, variable=scale_var,
            width=110, height=36, corner_radius=4, font=F(FS_BODY),
            fg_color=c['title_bg'], text_color=c['fg'],
            button_color=c['btn_hover'], button_hover_color=c['accent'],
            dropdown_fg_color=c['entry_bg'], dropdown_text_color=c['fg'],
            dropdown_hover_color=c['btn_hover'],
            command=lambda lab: self._apply_ui_scale(
                ui_codes[ui_labels.index(lab)]))
        scale_menu.pack(side=tk.RIGHT, padx=(0, 6), pady=6)
        self.scale_btn = scale_menu
        Tooltip(scale_menu, t('ui_scale_hint'), c)

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
        box.pack(fill=tk.X, padx=PAD_CARD, pady=(0, 4))

        inner = ctk.CTkFrame(box, fg_color='transparent', corner_radius=0)
        inner.pack(fill=tk.X, padx=12, pady=8)

        ctk.CTkLabel(inner, text=t('game_dir_title'), font=F(FS_SECTION, 'bold'),
                     text_color=c['fg'], anchor='w').pack(fill=tk.X)

        path = gamepath.game_dir() or gamepath.autodetect_game_dir()
        self.game_dir_var = tk.StringVar(value=path)
        row = ctk.CTkFrame(inner, fg_color='transparent', corner_radius=0)
        row.pack(fill=tk.X, pady=(4, 0))
        ent = ctk.CTkEntry(row, textvariable=self.game_dir_var,
                           height=H_INPUT, font=F(FS_BODY),
                           corner_radius=4, fg_color=c['entry_bg'],
                           text_color=c['fg'], border_color=c['border'])
        ent.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(0, 10))

        # Кнопки-значки в одной строке с путём: папка, автопоиск и
        # патч/откат (одна кнопка, значок зависит от состояния патча).
        icon_button(row, ICON_FOLDER, self._browse_game_dir, c,
                    tooltip=t('game_dir_browse')).pack(
            side=tk.LEFT, padx=(0, 6))
        icon_button(row, ICON_SEARCH, self._autodetect_game_dir, c,
                    tooltip=t('game_dir_autodetect')).pack(
            side=tk.LEFT, padx=(0, 6))
        self.patch_btn = icon_button(row, ICON_PATCH, self._on_patch_click, c)
        self.patch_btn.pack(side=tk.LEFT)
        self.patch_tooltip = Tooltip(self.patch_btn, t('patch_apply'), c)

        # Статус патча — сразу под строкой пути (ближе к ней).
        prow = ctk.CTkFrame(inner, fg_color='transparent', corner_radius=0)
        prow.pack(fill=tk.X, pady=(2, 0))
        self.patch_status = ctk.CTkLabel(prow, text='', font=F(FS_CAPTION),
                                         text_color=c.get('muted', '#888888'),
                                         anchor='w')
        self.patch_status.pack(side=tk.LEFT, fill=tk.X, expand=True)

        # Статус папки — только при ошибке: пустая строка раньше отодвигала
        # «патч применён» и раздувала карточку.
        self.game_dir_status = ctk.CTkLabel(inner, text='', font=F(FS_CAPTION),
                                            text_color='#ff5555', anchor='w')
        self._refresh_game_dir_status()
        self._refresh_patch_status()

    def _refresh_patch_status(self):
        """Показать состояние патча и переключить значок патч/откат."""
        if not hasattr(self, 'patch_status'):
            return
        try:
            code, _msg = patcher.status(
                (self.game_dir_var.get() or '').strip() or None)
        except Exception as e:
            code = 'unknown'
            label = str(e)[:80]
            color = '#ff5555'
        else:
            label = {'ok': t('patch_state_ok'),
                     'not_patched': t('patch_state_no')}.get(
                         code, t('patch_state_unknown'))
            color = {'ok': self.colors.get('success', '#4CAF50'),
                     'not_patched': '#ff5555'}.get(
                         code, self.colors.get('muted', '#888888'))
        self.patch_status.configure(text=label, text_color=color)

        if hasattr(self, 'patch_btn'):
            patched = (code == 'ok')
            self.patch_btn.configure(
                text=ICON_UNPATCH if patched else ICON_PATCH)
            if hasattr(self, 'patch_tooltip'):
                self.patch_tooltip.text = (
                    t('patch_unpatch') if patched else t('patch_apply'))

    def _on_patch_click(self):
        """Одна кнопка: пропатчить, если не пропатчено, иначе откатить."""
        try:
            code, _msg = patcher.status(
                (self.game_dir_var.get() or '').strip() or None)
        except Exception:
            code = 'unknown'
        if code == 'ok':
            self._unpatch_game()
        else:
            self._patch_game()

    def _patch_game(self):
        """Ручной запуск патча из строки пути."""
        self._patch_prompted = False
        self._ensure_patched(t('patch_needed'), check_dir=True)

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
            self.game_dir_status.configure(
                text='', text_color=self.colors.get('muted', '#888888'))
            if self.game_dir_status.winfo_manager():
                self.game_dir_status.pack_forget()
            return True
        self.game_dir_status.configure(text=t('game_dir_missing'),
                                       text_color='#ff5555')
        if not self.game_dir_status.winfo_manager():
            self.game_dir_status.pack(fill=tk.X, pady=(2, 0))
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
        self.main_frame.pack(fill=tk.BOTH, expand=True, padx=8, pady=(2, 2))
        # Сетка вместо pack: pack_propagate у CTk не работает (проверено) -
        # ширина колонок задаётся только сеткой: параметры растут, панель
        # деталей держит минимум (иначе она схлопывалась в «сливку»)
        self.main_frame.grid_columnconfigure(0, weight=1)
        self.main_frame.grid_columnconfigure(1, minsize=400, weight=0)
        self.main_frame.grid_rowconfigure(0, weight=1)

        # Левая колонка: параметры
        self.params_frame = ctk.CTkFrame(self.main_frame, fg_color='transparent',
                                         corner_radius=0)
        if self.config.get('show_params', True):
            self.params_frame.grid(row=0, column=0, sticky='nsew', padx=(0, 10))

        ctk.CTkLabel(self.params_frame, text=t('params_title'),
                     font=F(FS_SECTION, 'bold'),
                     text_color=c['fg'], anchor='w').pack(
            fill=tk.X, padx=PAD_CARD, pady=(0, 4))

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
                       pady=(0, 6))
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
                menu = FluentOptionMenu(
                    group, values=SPEED_VALUES, variable=var,
                    height=H_INPUT, font=F(FS_VALUE, mono=True),
                    corner_radius=4, width=300,
                    fg_color=c['entry_bg'], text_color=c['fg'],
                    button_color=c['btn_hover'],
                    button_hover_color=c['accent'],
                    dropdown_fg_color=c['entry_bg'],
                    dropdown_text_color=c['fg'],
                    dropdown_hover_color=c['btn_hover'],
                    command=lambda _v: self._on_speed_change())
                menu.pack(anchor='w', pady=(2, 0))
                e = _OptionField(menu, var, SPEED_DISPLAY, SPEED_REVERSE)
                self.entries[key] = (e, typ)
            else:
                e = ctk.CTkEntry(group, width=300, height=H_INPUT,
                                 font=F(FS_VALUE, mono=True),
                                 justify='right', corner_radius=4,
                                 fg_color=c['entry_bg'], text_color=c['fg'],
                                 border_color=c['border'])
                e.insert(0, str(self.config.get(key, DEFAULTS[key])))
                e.pack(anchor='w', pady=(2, 0))
                self.entries[key] = (e, typ)
                e.bind('<KeyRelease>', lambda ev: self._on_param_change())
                e.bind('<FocusOut>', lambda ev: self._on_param_change())
            # комментарий - под СВОИМ полем; сюда же падает текст ошибки
            desc = ctk.CTkLabel(group, text=t('desc_' + key), font=F(11),
                                text_color=c.get('muted', '#9a9a9a'), anchor='w',
                                justify='left', wraplength=260)
            desc.pack(fill='x', pady=(2, 0))
            self._desc_labels[key] = desc
            hint = ctk.CTkLabel(group, text='', font=F(11),
                                text_color='#ff5555', anchor='w')
            self._hint_labels[key] = hint

        # Для speed событие смены уходит из CTkOptionMenu(command=...)
        # выше по тексту; блок старых привязок FocusOut/Return удалён.

        # --- Баланс качество/время: алгоритм и число оценок ------------
        def _choice_group(row, col, key, label, values, current, desc):
            group = ctk.CTkFrame(grid, fg_color='transparent', corner_radius=0)
            group.grid(row=row, column=col, sticky='nsew',
                       padx=(0, 8) if col == 0 else (8, 0),
                       pady=(0, 6))
            ctk.CTkLabel(group, text=label, font=F(FS_BODY),
                         text_color=c['fg'], anchor='w').pack(fill='x')
            var = tk.StringVar(value=current)
            menu = FluentOptionMenu(
                group, values=values, variable=var,
                command=lambda _v: self.schedule_calculate(),
                height=H_INPUT, font=F(FS_BODY), corner_radius=4,
                width=300,
                fg_color=c['entry_bg'], text_color=c['fg'],
                button_color=c['btn_hover'], button_hover_color=c['accent'],
                dropdown_fg_color=c['entry_bg'], dropdown_text_color=c['fg'],
                dropdown_hover_color=c['btn_hover'])
            menu.pack(anchor='w', pady=(2, 0))
            desc_lbl = ctk.CTkLabel(group, text=desc, font=F(11),
                                    text_color=c.get('muted', '#9a9a9a'), anchor='w',
                                    justify='left', wraplength=260)
            desc_lbl.pack(fill='x', pady=(2, 0))
            self._desc_labels[key] = desc_lbl
            hint = ctk.CTkLabel(group, text='', font=F(11),
                                text_color='#ff5555', anchor='w')
            self._hint_labels[key] = hint
            return var

        opt_labels = [t('optimizer_cem'), t('optimizer_ga')]
        opt_cur = (opt_labels[1] if self.config.get('optimizer', 'cem') == 'ga'
                   else opt_labels[0])
        self.optimizer_var = _choice_group(
            3, 0, 'optimizer', t('param_optimizer'), opt_labels, opt_cur,
            t('desc_optimizer'))

        eval_labels = [t('eval_1'), t('eval_2'), t('eval_3')]
        try:
            eval_cur = int(self.config.get('eval_repeats', 2))
        except (TypeError, ValueError):
            eval_cur = 2
        eval_cur = min(3, max(1, eval_cur))
        self.eval_repeats_var = _choice_group(
            3, 1, 'eval_repeats', t('param_eval_repeats'), eval_labels,
            eval_labels[eval_cur - 1], t('desc_eval_repeats'))

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
        actions.pack(fill=tk.X, padx=PAD_CARD, pady=(6, 0))
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
            rec = {'pop': 50, 'rounds': 15, 'episode': 60.0, 'episode_increment': 5.0,
                   'speed': 3.0, 'top_percent': 0.1}
            for key, (e, typ) in self.entries.items():
                if key in rec:
                    e.delete(0, tk.END)
                    e.insert(0, str(rec[key]))
            try:
                self.optimizer_var.set(t('optimizer_cem'))
                self.eval_repeats_var.set(t('eval_2'))
            except Exception:
                pass
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
        # Показываем только при ошибке, чтобы внизу не оставалось пустой строки.

        # Валидация при запуске
        self.root.after(100, self._validate_pop)

        # Панель подробностей (через _build_detail_panel для консистентности)
        self._build_detail_panel()

        self.root.update_idletasks()
        # minsize — в логических единицах Tk; не умножаем на DPI, иначе
        # окно не может сжаться по содержимому и снизу остаётся пусто.
        self.root.minsize(880, 580)
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
            # winfo_screen* отдаёт физические пиксели, geometry — логические
            # единицы Tk (при масштабе 125%: geometry 1072 -> winfo 1340).
            # Рабочую область тоже переводим в логические, иначе окно
            # получается выше экрана и низ обрезается (жалоба «не всё влезло»).
            scale = win_ui_scale()
            work_w = int((self.root.winfo_screenwidth() - 8) / scale)
            work_h = int((self.root.winfo_screenheight() - 80) / scale)
            req_w = self.main_frame.winfo_reqwidth() + 32
            # Высоту берём по левой колонке: правые «Детали» при нехватке
            # высоты прокручиваются, а внизу основного окна не остаётся
            # пустого места из-за их запрошенной высоты.
            left_h = (self.params_frame.winfo_reqheight()
                      if self.params_frame.winfo_ismapped()
                      else self.main_frame.winfo_reqheight())
            req_h = self.title_bar.winfo_reqheight() + left_h + 16
            content_h = int(req_h / scale) + 4
            geo_str = self.root.geometry()
            # позиция может быть и 'x+y', и '-1450+60' (2-й монитор слева)
            pm = _re.search(r'([+-]\d+)([+-]\d+)$', geo_str)
            if pm:
                x, y = int(pm.group(1)), int(pm.group(2))
            else:
                x, y = 80, 60
            # высоту - по содержимому (в логических единицах): и растим,
            # и ужимаем; не выше рабочей области экрана
            new_h = min(max(content_h, 560), work_h)
            # ширина: поля параметров теперь фиксированные (~300), поэтому
            # окно можно сжать по содержимому, а не держать старую ширину.
            new_w = min(max(int(req_w / scale) + 100, 1100), work_w)
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
        """Панель «Детали» - ВСЕГДА внутри главного окна (правая колонка).

        Отсоединяемых окон «Детали» больше нет (решение пользователя:
        консоль всегда отдельное окно, детали всегда часть основного).
        """
        c = self.colors
        panel_bg = c.get('panel_bg', c['entry_bg'])

        # Уничтожить существующую
        if hasattr(self, 'detail_frame') and self.detail_frame is not None:
            try:
                if self.detail_frame.winfo_exists():
                    self.detail_frame.destroy()
            except Exception:
                pass
            self.detail_frame = None

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
        self.detail_text = tk.Text(parent, font=FT(FS_DETAIL), wrap=tk.WORD,
                                  width=50, height=1,
                                  bg=c.get('panel_bg', c['entry_bg']), fg=c['fg'],
                                  insertbackground=c['fg'],
                                  selectbackground='#264f78',
                                  relief=tk.FLAT, borderwidth=0,
                                  yscrollcommand=detail_scroll.set,
                                  padx=10, pady=10)
        detail_scroll.configure(command=self.detail_text.yview)
        detail_scroll.pack(side=tk.RIGHT, fill=tk.Y)
        self.detail_text.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        # Крупное финальное время + подпись к нему.
        _big = FT(FS_BIG)
        self.detail_text.tag_configure(
            'detail_label', font=FT(8),
            foreground=c.get('muted', '#9a9a9a'))
        self.detail_text.tag_configure(
            'detail_big', font=(_big[0], _big[1], 'bold'),
            foreground=c['fg'])

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

    def _apply_ui_scale(self, code):
        """Сменить масштаб окна: сохранить и перезапустить лаунчер."""
        cur = str(self.config.get('ui_scale', 'auto'))
        if not getattr(self, '_scale_ready', True):
            # Стартовый вызов OptionMenu — вернуть текущее значение.
            codes, labels = self._ui_scale_labels
            if cur in codes:
                self._ui_scale_var.set(labels[codes.index(cur)])
            return
        if code == cur:
            return
        if self.process_running:
            from tkinter import messagebox
            messagebox.showwarning(t('ui_scale_title'),
                                   t('ui_scale_running'))
            # Вернуть отображение текущего значения в списке.
            codes, labels = self._ui_scale_labels
            if cur in codes:
                self._ui_scale_var.set(labels[codes.index(cur)])
            return
        self.config['ui_scale'] = code
        try:
            self.config['window_geometry'] = self.root.geometry()
        except Exception:
            pass
        save_config(self.config)
        self._restart_launcher()

    def _restart_launcher(self):
        """Запустить новый экземпляр с новым масштабом и закрыть старый."""
        import subprocess
        if getattr(sys, 'frozen', False):
            cmd = [sys.executable]
        else:
            cmd = [sys.executable, os.path.join(HERE, 'launcher.py')]
        subprocess.Popen(cmd, cwd=HERE)
        try:
            self.root.destroy()
        except Exception:
            os._exit(0)

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

    # ------------------------------------------------------------ обновления
    def _set_update_button(self, text=None, state=tk.NORMAL):
        """Состояние кнопки обновления.

        Значок постоянный, поэтому статус (Проверка.../Скачивание...) и
        результат уходят во всплывающую подсказку.
        """
        if not hasattr(self, 'update_btn'):
            return
        try:
            self.update_btn.configure(state=state)
        except Exception:
            pass
        tip = getattr(self, 'update_tooltip', None)
        if tip is not None:
            tip.text = text or t('update_check')
            tip._hide()   # скрыть показанную, чтобы новый текст увиделся

    def _check_updates(self):
        from tkinter import messagebox
        if self.process_running:
            messagebox.showwarning(t('update_title'), t('update_running'))
            return
        if self._update_busy:
            return
        self._update_busy = True
        self._set_update_button(t('update_checking'), tk.DISABLED)
        threading.Thread(target=self._fetch_update, daemon=True).start()

    def _fetch_update(self):
        try:
            req = urllib.request.Request(
                UPDATE_API, headers={'User-Agent': 'KCbot-Updater',
                                     'Accept': 'application/vnd.github+json'})
            with urllib.request.urlopen(req, timeout=12) as resp:
                data = json.load(resp)
        except Exception as e:
            self.root.after(0, lambda err=e: self._update_error(err))
            return
        self.root.after(0, lambda: self._update_fetched(data))

    def _update_fetched(self, data):
        from tkinter import messagebox
        self._update_busy = False
        self._set_update_button()
        tag = data.get('tag_name', '')
        if not is_newer(tag):
            messagebox.showinfo(
                t('update_title'),
                t('update_latest', ver='v' + APP_VERSION))
            return
        assets = data.get('assets') or []
        installer = None
        for a in assets:
            name = (a.get('name') or '').lower()
            if name.endswith('.exe') and 'portable' not in name \
                    and a.get('browser_download_url'):
                installer = a
                break
        if installer is None:
            if messagebox.askyesno(
                    t('update_title'),
                    t('update_no_asset', ver=tag)):
                webbrowser.open(data.get('html_url') or UPDATE_PAGE)
            return
        if not messagebox.askyesno(
                t('update_title'),
                t('update_available', ver=tag)):
            return
        self._download_update(installer, tag)

    def _download_update(self, asset, tag):
        self._update_busy = True
        self._set_update_button(t('update_downloading'), tk.DISABLED)
        folder = os.path.join(tempfile.gettempdir(), 'KCbot-update')
        os.makedirs(folder, exist_ok=True)
        path = os.path.join(folder, asset.get('name') or 'KCbot-update.exe')
        threading.Thread(target=self._download_update_worker,
                         args=(asset, tag, path), daemon=True).start()

    def _download_update_worker(self, asset, tag, path):
        try:
            req = urllib.request.Request(
                asset['browser_download_url'],
                headers={'User-Agent': 'KCbot-Updater'})
            with urllib.request.urlopen(req, timeout=90) as resp, \
                    open(path, 'wb') as f:
                shutil.copyfileobj(resp, f, 1024 * 256)
            digest = (asset.get('digest') or '').lower()
            if digest.startswith('sha256:'):
                h = hashlib.sha256()
                with open(path, 'rb') as f:
                    for chunk in iter(lambda: f.read(1024 * 1024), b''):
                        h.update(chunk)
                if h.hexdigest() != digest.split(':', 1)[1]:
                    raise ValueError('SHA256 mismatch')
        except Exception as e:
            try:
                if os.path.exists(path):
                    os.remove(path)
            except OSError:
                pass
            self.root.after(0, lambda err=e: self._update_error(err, True))
            return
        self.root.after(0, lambda: self._update_downloaded(path, tag))

    def _update_downloaded(self, path, tag):
        from tkinter import messagebox
        self._update_busy = False
        self._set_update_button()
        if not messagebox.askyesno(
                t('update_title'),
                t('update_ready', ver=tag, path=path)):
            messagebox.showinfo(t('update_title'),
                                t('update_saved', path=path))
            return
        try:
            cfg = load_config()
            cfg['window_geometry'] = self.root.geometry()
            save_config(cfg)
        except Exception:
            pass
        try:
            subprocess.Popen([path], cwd=os.path.dirname(path))
        except Exception as e:
            messagebox.showerror(t('update_title'),
                                 t('update_failed', err=str(e)[:300]))
            return
        self.root.after(250, self.root.destroy)

    def _update_error(self, err, download=False):
        from tkinter import messagebox
        self._update_busy = False
        self._set_update_button()
        key = 'update_download_failed' if download else 'update_failed'
        messagebox.showwarning(t('update_title'),
                               t(key, err=str(err)[:300]))

    def _open_help(self):
        c = self.colors
        # Одно окно справки: повторный клик поднимает уже открытое
        try:
            if getattr(self, 'help_window', None) is not None:
                if self.help_window.winfo_exists():
                    self.help_window.lift()
                    self.help_window.focus_force()
                    return
        except tk.TclError:
            pass
        self.help_window = None

        win, content = create_themed_toplevel(
            self.root, t('help_title'), c, 900, 720,
            geometry_key='help_window', window_icon=ICON_HELP,
            resizable=False)
        self.help_window = win

        # --- текст справки (шрифт масштабируется от настроек Windows) ---
        body = tk.Frame(content, bg=c['bg'])
        scroll = ttk.Scrollbar(body, orient=tk.VERTICAL)
        pad = max(6, int(round(8 * win_ui_scale())))
        help_text = tk.Text(body, wrap=tk.WORD, bg=c['entry_bg'], fg=c['fg'],
                            insertbackground=c['fg'],
                            selectbackground='#264f78',
                            font=FT(FS_CAPTION, 'Segoe UI'),
                            relief=tk.FLAT, borderwidth=0,
                            yscrollcommand=scroll.set, padx=pad, pady=pad)
        scroll.configure(command=help_text.yview)
        scroll.pack(side=tk.RIGHT, fill=tk.Y)
        help_text.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

        # Справка целиком лежит в локали одним ключом help_body.
        # t_raw() не делает .format() - в тексте есть угловые скобки.
        help_text.insert('1.0', t_raw('help_body'))
        help_text.configure(state=tk.DISABLED)
        self.help_text = help_text

        # --- поиск снизу - точно как в консоли ------------------------
        self.help_search = SearchBar(
            content, c, help_text,
            placeholder=t('help_search_placeholder'), readonly=True)
        self.help_search.pack(side=tk.BOTTOM, fill=tk.X, padx=PAD_CARD,
                              pady=(4, PAD_CARD))
        body.pack(fill=tk.BOTH, expand=True, padx=PAD_CARD,
                  pady=(PAD_CARD, 8))

        def _forget_help(_event=None):
            self.help_window = None
        win.bind('<Destroy>', _forget_help, add='+')

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
        opt_label = self.optimizer_var.get()
        cfg['optimizer'] = ('ga' if opt_label == t('optimizer_ga')
                            else 'cem')
        eval_label = self.eval_repeats_var.get()
        cfg['eval_repeats'] = {t('eval_1'): 1, t('eval_2'): 2,
                               t('eval_3'): 3}.get(eval_label, 2)
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

    def _set_hint(self, hint, text):
        """Показать подсказку об ошибке, не занимая место пустой строкой."""
        if hint is None:
            return
        was = bool(hint.winfo_manager())
        now = bool(text)
        hint.configure(text=text)
        if now and not was:
            hint.pack(fill=tk.X)
        elif was and not now:
            hint.pack_forget()
        if now != was:
            self.root.after(30, self._fit_window)

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
                self._set_hint(hint, t(msg))
                all_valid = False
            else:
                e.configure(fg_color=self.colors['entry_bg'],
                            border_color=self.colors['border'])
                self._set_hint(hint, '')

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


    def _set_result(self, text):
        """Показать/скрыть строку ошибки внизу параметров.

        Пустая строка больше не занимает место: окно подгоняется по
        фактическому содержимому (жалоба «пустое пространство»).
        """
        if not hasattr(self, 'result_label'):
            return
        was = bool(self.result_label.winfo_manager())
        self.result_label.configure(text=text)
        now = bool(text)
        if now and not was:
            self.result_label.pack(fill=tk.X, padx=PAD_CARD, pady=(8, 0))
        elif was and not now:
            self.result_label.pack_forget()
        if now != was:
            self.root.after(50, self._fit_window)

    def calculate(self):
        cfg = self.get_params()
        if cfg is None:
            self._set_result(t('err_params_check'))
            return
        real_total, game_total, hours, minutes, details = calc_time(cfg)

        # Время и количество поколений/ботов — только в «Деталях»;
        # в основном окне строка остаётся только для ошибок.
        self._set_result('')

        # Обновляем обе панели деталей
        for dt in [self.detail_text, getattr(self, 'detail_right_text', None)]:
            if dt is None:
                continue
            dt.delete('1.0', tk.END)
            # Параметры уже видны слева — здесь только итоговое время
            # крупно и расчёт по раундам.
            speed = min(3.0, max(1.0, float(cfg.get('speed', 1.0))))
            # Реальное время зависит от скорости, игровое — нет.
            total_min = int(round(real_total / 60.0))
            if total_min >= 60:
                big = t('detail_time_hm', h='%d' % (total_min // 60),
                        m='%02d' % (total_min % 60))
            else:
                big = t('detail_time_m', m='%d' % total_min)
            game_min = int(round(game_total / 60.0))
            if game_min >= 60:
                game = t('detail_game_time', h='%d' % (game_min // 60),
                         m='%02d' % (game_min % 60))
            else:
                game = t('detail_game_time_m', m='%d' % game_min)
            dt.insert(tk.END, t('detail_total_label',
                                speed='%g' % speed) + '\n', 'detail_label')
            dt.insert(tk.END, big + '\n', 'detail_big')
            dt.insert(tk.END, t('detail_rounds_bots',
                                n=cfg['rounds'],
                                bots=cfg['pop'] * cfg['rounds']) + '\n')
            dt.insert(tk.END, game + '\n\n')
            dt.insert(tk.END, t('detail_speed_note') + '\n\n',
                      'detail_label')
            dt.insert(tk.END, t('sec_rounds') + '\n')
            for rnd, ep, rt, gr in details:
                dt.insert(tk.END, t('round_line', n='%2d' % rnd, ep='%.0f' % ep,
                                    rt='%.0f' % rt, min='%.1f' % (rt / 60.0),
                                    gr='%.0f' % gr) + '\n')

    def start_training(self):
        cfg = self.get_params()
        if cfg is None:
            self._set_result(t('err_params'))
            return
        # Папка игры: сохраняем и проверяем ДО запуска. Раньше launчер её не
        # проверял вообще - evo.py падал посреди прогона с RuntimeError.
        self._save_game_dir()
        if not self._refresh_game_dir_status():
            msg = t('game_dir_missing')
            self._set_result(msg)
            if self.console_window is None or not self.console_window.window.winfo_exists():
                self.console_window = ConsoleWindow(self.root, self.colors,
                                                    detached=True, content='both')
            self.console_window.append('!! %s\n' % msg)
            return
        self.config.update(cfg)
        save_config(self.config)
        cmd = build_command(cfg)
        self._set_running_ui(True)

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
        # evo не должен открывать второй лаунчер: текущий уже открыт.
        env['KC_FROM_LAUNCHER'] = '1'
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
                self.start_btn.after(0, lambda: self._set_running_ui(False))
            except (tk.TclError, RuntimeError):
                # TclError - виджет уничтожен; RuntimeError - mainloop уже
                # остановлен (закрытие приложения) - оба случая не фатальны
                pass

    def _set_running_ui(self, running):
        """Показать «Стоп» во время обучения, иначе «Старт».

        Кнопки живут в grid-контейнере (колонка 0), поэтому переключать
        их нужно grid_remove()/grid(), а не pack - pack в grid-контейнере
        даёт TclError и ломает обработчик закрытия окна (баг найден по
        жалобе: крестик из панели задач не закрывал приложение).
        """
        try:
            if running:
                self.start_btn.grid_remove()
                self.stop_btn.grid(row=0, column=0, sticky='ew',
                                   padx=(0, 8))
            else:
                self.stop_btn.grid_remove()
                self.start_btn.grid(row=0, column=0, sticky='ew',
                                    padx=(0, 8))
        except tk.TclError:
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
        self._set_running_ui(False)

    def _on_close(self):
        # Сохранить размер/позицию окна
        try:
            cfg = load_config()
            cfg['window_geometry'] = self.root.geometry()
            save_config(cfg)
        except Exception:
            pass
        # Остановить обучение и закрыть игру. Любая ошибка остановки НЕ
        # должна мешать закрытию: раньше TclError из stop_training()
        # оставлял окно висеть (жалоба: крестик из панели задач не
        # закрывает приложение, только пишет в консоль).
        try:
            self.stop_training()
        except Exception:
            pass
        try:
            self.root.destroy()
        except Exception:
            os._exit(0)

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
    _boot_cfg = load_config()
    set_ui_scale(_boot_cfg.get('ui_scale', 'auto'))
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
    saved_geo = _boot_cfg.get('window_geometry', None)
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

    # Кастомный заголовок (overrideredirect); главное окно фиксированного
    # размера — ресайз только у консоли.
    root.overrideredirect(True)
    root.resizable(False, False)

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

    # Совместный просмотр UI: KCBOT_UI_CONSOLE=1 - сразу открыть консоль
    # (обычно её открывает кнопка ≡ в шапке)
    if os.environ.get('KCBOT_UI_CONSOLE'):
        root.after(700, app._toggle_console_panel)
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
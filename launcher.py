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
import os
import queue
import subprocess
import sys
import threading
import time
import tkinter as tk
from tkinter import ttk

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


# Загрузить локаль по умолчанию (из конфига подгрузится позже в App)
load_locale('ru')
APPDATA_DIR = os.path.join(os.environ.get('APPDATA', os.path.expanduser('~')), 'KCbot')
CONFIG_FILE = os.path.join(APPDATA_DIR, 'launcher_config.json')
HISTORY_FILE = os.path.join(HERE, 'data', 'evo_history.json')

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
        colors = COLOR_SCHEMES.get(theme_name, COLOR_SCHEMES['dark'])
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
}


def load_config():
    old_config = os.path.join(HERE, 'data', '.launcher_config.json')
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
    cmd = 'py evo.py --pop %d --rounds %d --episode %s --episode-increment %s --speed %s --top-percent %s' % (
        cfg['pop'], cfg['rounds'], cfg['episode'], cfg['episode_increment'],
        cfg['speed'], cfg['top_percent'])
    if cfg.get('restart', True):
        cmd += ' --restart'
    else:
        cmd += ' --no-restart'
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


def create_themed_toplevel(parent, title, colors, width=800, height=600, attach_callback=None, geometry_key=None):
    """Создаёт Toplevel окно с кастомным заголовком и тёмной темой."""
    win = tk.Toplevel(parent)
    win.title(title)
    win.overrideredirect(True)
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

    # Заголовок
    title_bar = tk.Frame(win, bg=colors['title_bg'], height=30)
    title_bar.pack(fill=tk.X, side=tk.TOP)
    title_bar.pack_propagate(False)

    title_label = tk.Label(title_bar, text=title, bg=colors['title_bg'], fg=colors['fg'],
                           font=('Segoe UI', 10))
    title_label.pack(side=tk.LEFT, padx=8)

    btn_frame = tk.Frame(title_bar, bg=colors['title_bg'])
    btn_frame.pack(side=tk.RIGHT)

    # Кнопка прикрепления (если есть callback)
    if attach_callback:
        attach_btn = tk.Button(btn_frame, text='\u2199', font=('Segoe UI', 12),
                               bg=colors['title_bg'], fg=colors['fg'], relief=tk.FLAT,
                               borderwidth=0, width=3, cursor='hand2', command=attach_callback)
        attach_btn.pack(side=tk.LEFT)
        attach_btn.bind('<Enter>', lambda e: attach_btn.config(bg=colors['btn_hover']))
        attach_btn.bind('<Leave>', lambda e: attach_btn.config(bg=colors['title_bg']))

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
            win.update_idletasks()
            win.update()
            apply_taskbar_style(win)
        else:
            win.after(50, lambda: _finish_restore(win))

    for text, cmd, hover_bg in [('\u2014', None, colors['btn_hover']),
                                ('\u25A1', None, colors['btn_hover']),
                                ('\u2715', win.destroy, '#e81123')]:
        if cmd is None:
            if text == '\u2014':
                cmd = lambda w=win: do_minimize(w)
            elif text == '\u25A1':
                cmd = lambda w=win: None  # maximize handled separately
        btn = tk.Button(btn_frame, text=text, font=('Segoe UI', 9),
                        bg=colors['title_bg'], fg=colors['fg'], relief=tk.FLAT,
                        borderwidth=0, width=4, cursor='hand2', command=cmd)
        btn.pack(side=tk.LEFT)
        btn.bind('<Enter>', lambda e, b=btn, h=hover_bg: b.config(bg=h))
        btn.bind('<Leave>', lambda e, b=btn, bg=colors['title_bg']: b.config(bg=bg))

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
        GWL_EXSTYLE = -20
        WS_EX_APPWINDOW = 0x00040000
        WS_EX_TOOLWINDOW = 0x00000080
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
                parent, t('console_title'), colors, 900, 620, on_attach)
        else:
            self.window = tk.Frame(parent, bg=colors['bg'])
            self._content = self.window

        self._build_ui()
        self._tick()

    # ------------------------------------------------------------- построение
    def _build_ui(self):
        c = self.colors

        # --- панель инструментов ---
        bar = tk.Frame(self._content, bg=c['bg'])
        bar.pack(fill=tk.X, padx=5, pady=(5, 2))

        tk.Label(bar, text=t('console_filter_all'), bg=c['bg'], fg=c['fg'],
                 anchor='w').pack(side=tk.LEFT)

        self._filter_labels = [t('console_filter_all'),
                               t('console_filter_warn'),
                               t('console_filter_error')]
        # None = показывать всё; иначе - только эти уровни и выше по важности
        self._filter_levels = [None, ('warn', 'error'), ('error',)]
        self.filter_var = tk.StringVar(value=self._filter_labels[0])
        flt = ttk.Combobox(bar, textvariable=self.filter_var,
                           values=self._filter_labels, state='readonly',
                           width=12)
        flt.pack(side=tk.LEFT, padx=(0, 8))
        flt.bind('<<ComboboxSelected>>', lambda e: self._rebuild())

        self.search_var = tk.StringVar()
        ent = tk.Entry(bar, textvariable=self.search_var, width=24,
                       bg=c['entry_bg'], fg=c['fg'], insertbackground=c['fg'],
                       relief=tk.FLAT, borderwidth=1)
        ent.pack(side=tk.LEFT, padx=(0, 2))
        ent.bind('<Return>', lambda e: self._find_next())

        self._mkbtn(bar, t('console_find'), self._find_next)
        self._mkbtn(bar, t('console_clear'), self.clear)
        self._mkbtn(bar, t('console_copy'), self.copy_all)
        self._mkbtn(bar, t('console_export'), self.export)

        self.follow_var = tk.BooleanVar(value=True)
        tk.Checkbutton(bar, text=t('console_autoscroll'),
                       variable=self.follow_var,
                       command=self._follow_toggled,
                       bg=c['bg'], fg=c['fg'], activebackground=c['bg'],
                       activeforeground=c['fg'], highlightthickness=0
                       ).pack(side=tk.RIGHT, padx=(8, 0))
        self.status_var = tk.StringVar(value=t('console_status', n=0))
        tk.Label(bar, textvariable=self.status_var, bg=c['bg'], fg=c['fg'],
                 anchor='e').pack(side=tk.RIGHT, padx=(0, 8))

        # --- текст ---
        body = tk.Frame(self._content, bg=c['bg'])
        body.pack(fill=tk.BOTH, expand=True, padx=5, pady=(0, 5))

        scroll = ttk.Scrollbar(body, orient=tk.VERTICAL)
        self.console_text = tk.Text(body, font=('Consolas', 10),
                                    bg=c['entry_bg'], fg=c['fg'],
                                    insertbackground=c['fg'],
                                    selectbackground='#264f78',
                                    relief=tk.FLAT, borderwidth=1,
                                    wrap=tk.WORD,
                                    yscrollcommand=self._on_yscroll)
        scroll.config(command=self._scrollbar_cmd)
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
        return tk.Button(parent, text=label, command=cmd,
                         bg=self.colors['entry_bg'], fg=self.colors['fg'],
                         activebackground=self.colors['accent'],
                         activeforeground='#ffffff', relief=tk.FLAT,
                         borderwidth=1, padx=7, pady=1).pack(side=tk.LEFT,
                                                             padx=2)

    # ------------------------------------------------------------ потокобезопасность
    def append(self, text):
        """Добавить текст. Можно вызывать из ЛЮБОГО потока."""
        if not text:
            return
        self._queue.put(text)

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
        self.colors = COLOR_SCHEMES.get(self.config.get('theme', 'dark'), COLOR_SCHEMES['dark'])
        self.console_window = None
        self.process_running = False
        self.settings_dialog = None
        self._maximized = False
        self._normal_geometry = '1000x700'
        self.detail_panel = None
        self.console_panel = None

        root.geometry(self._normal_geometry)
        root.minsize(800, 500)
        root.configure(bg=self.colors['bg'])

        # Кастомный заголовок (overrideredirect) — отключено, окно не показывалось
        # # root.overrideredirect(True)

        self._build_title_bar()
        self._build_main()
        # Периодическое обновление подробностей (каждые 2 секунды)
        self._schedule_detail_update()
        # Применить стиль панели задач после отображения окна
        pass  # _apply_taskbar_style отключён (скрывал окно)

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
        c = self.colors
        # Обработка закрытия окна (убить обучение и игру)
        self.root.protocol('WM_DELETE_WINDOW', self._on_close)
        self.title_bar = tk.Frame(self.root, bg=c['title_bg'], height=30)
        self.title_bar.pack(fill=tk.X, side=tk.TOP)
        self.title_bar.pack_propagate(False)

        # Кнопка выбора темы
        self.theme_btn = tk.Button(self.title_bar, text='\u263c', font=('Segoe UI', 13),
                             bg=c['title_bg'], fg=c['fg'], relief=tk.FLAT,
                             borderwidth=0, cursor='hand2', command=self._open_theme_menu)
        self.theme_btn.pack(side=tk.LEFT, padx=(10, 2))

        # Кнопка выбора языка
        self.lang_btn = tk.Button(self.title_bar, text='\U0001F310', font=('Segoe UI', 11),
                             bg=c['title_bg'], fg=c['fg'], relief=tk.FLAT,
                             borderwidth=0, cursor='hand2', command=self._open_language_menu)
        self.lang_btn.pack(side=tk.LEFT, padx=2)

        # Кнопка справки
        self.help_btn = tk.Button(self.title_bar, text='?', font=('Segoe UI', 12, 'bold'),
                             bg=c['title_bg'], fg=c['fg'], relief=tk.FLAT,
                             borderwidth=0, cursor='hand2', command=self._open_help)
        self.help_btn.pack(side=tk.LEFT, padx=2)

        title_label = tk.Label(self.title_bar, text='KC Bot', bg=c['title_bg'], fg=c['fg'],
                               font=('Segoe UI', 10))
        title_label.pack(side=tk.LEFT, padx=2)

        # Кнопки переключения панелей
        panel_btns = tk.Frame(self.title_bar, bg=c['title_bg'])
        panel_btns.pack(side=tk.LEFT, padx=10)

        self.console_toggle_btn = tk.Button(panel_btns, text='\u2328', font=('Segoe UI', 12),
                                           bg=c['title_bg'], fg=c['fg'], relief=tk.FLAT,
                                           borderwidth=0, cursor='hand2',
                                           command=self._toggle_console_panel)
        self.console_toggle_btn.pack(side=tk.LEFT, padx=2)

        btn_frame = tk.Frame(self.title_bar, bg=c['title_bg'])
        btn_frame.pack(side=tk.RIGHT)

        for text, cmd, hover_bg in [('\u2014', self.minimize, c['btn_hover']),
                                    ('\u25A1', self.toggle_maximize, c['btn_hover']),
                                    ('\u2715', self.root.destroy, '#e81123')]:
            btn = tk.Button(btn_frame, text=text, font=('Segoe UI', 9),
                            bg=c['title_bg'], fg=c['fg'], relief=tk.FLAT,
                            borderwidth=0, width=4, cursor='hand2', command=cmd)
            btn.pack(side=tk.LEFT)
            btn.bind('<Enter>', lambda e, b=btn, h=hover_bg: b.config(bg=h))
            btn.bind('<Leave>', lambda e, b=btn, bg=c['title_bg']: b.config(bg=bg))

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
        """Строка «Папка игры»: путь + Обзор + Автопоиск."""
        box = tk.Frame(self.params_frame, bg=c['bg'])
        box.pack(fill=tk.X, pady=(0, 6))

        tk.Label(box, text=t('game_dir_title'), bg=c['bg'], fg=c['fg'],
                 anchor='w', font=('Segoe UI', 10)).pack(fill=tk.X)

        path = gamepath.game_dir() or gamepath.autodetect_game_dir()
        self.game_dir_var = tk.StringVar(value=path)
        row = tk.Frame(box, bg=c['bg'])
        row.pack(fill=tk.X, pady=(2, 0))
        ent = tk.Entry(row, textvariable=self.game_dir_var, width=30,
                       font=('Segoe UI', 9), bg=c['entry_bg'], fg=c['fg'],
                       insertbackground=c['fg'], relief=tk.FLAT, borderwidth=1)
        ent.pack(side=tk.LEFT, fill=tk.X, expand=True)

        def _mk(label, cmd, width=11):
            tk.Button(row, text=label, command=cmd, width=width,
                      bg=c['entry_bg'], fg=c['fg'],
                      activebackground=c['accent'], activeforeground='#ffffff',
                      relief=tk.FLAT, borderwidth=1).pack(side=tk.LEFT, padx=(4, 0))

        _mk(t('game_dir_browse'), self._browse_game_dir)
        _mk(t('game_dir_autodetect'), self._autodetect_game_dir)

        self.game_dir_status = tk.Label(box, text='', bg=c['bg'],
                                        fg='#ff5555', anchor='w',
                                        font=('Segoe UI', 9))
        self.game_dir_status.pack(fill=tk.X)
        self._refresh_game_dir_status()

    def _refresh_game_dir_status(self):
        p = (self.game_dir_var.get() or '').strip()
        exe = os.path.join(p, 'Kingdom.exe') if p else ''
        if p and os.path.isfile(exe):
            self.game_dir_status.config(text='', fg='#888888')
            return True
        self.game_dir_status.config(text=t('game_dir_missing'), fg='#ff5555')
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
            self.game_dir_status.config(text=t('game_dir_not_found'),
                                        fg='#ff5555')
            return
        self.game_dir_var.set(path)
        self._save_game_dir()

    def _save_game_dir(self):
        path = (self.game_dir_var.get() or '').strip()
        try:
            gamepath.set_game_dir(path)
        except OSError as e:
            self.game_dir_status.config(text=str(e), fg='#ff5555')
            return
        self._refresh_game_dir_status()

    def _build_main(self):
        c = self.colors
        self.main_frame = tk.Frame(self.root, bg=c['bg'])
        self.main_frame.pack(fill=tk.BOTH, expand=True, padx=10, pady=5)

        # Левая панель: параметры
        self.params_frame = tk.Frame(self.main_frame, bg=c['bg'], width=260)
        self.params_frame.pack_propagate(False)
        if self.config.get('show_params', True):
            _params_expand = (self.config.get('detail_side', 'left') == 'right') and not self.config.get('detail_detached', False)
            self.params_frame.pack(side=tk.LEFT, fill=tk.BOTH, expand=_params_expand, padx=(0, 5))

        tk.Label(self.params_frame, text=t('params_title'), bg=c['bg'], fg=c['fg'],
                 font=('Segoe UI', 13, 'bold')).pack(pady=(0, 8))

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

        self.entries = {}
        self._validation_labels = {}
        for key, label, typ in fields:
            row = tk.Frame(self.params_frame, bg=c['bg'])
            row.pack(fill=tk.X, pady=2)
            tk.Label(row, text=label, bg=c['bg'], fg=c['fg'], width=22, anchor='w',
                     font=('Segoe UI', 10)).pack(side=tk.LEFT)
            e = tk.Entry(row, width=6, font=('Consolas', 11), bg=c['entry_bg'], fg=c['fg'],
                         insertbackground=c['fg'], relief=tk.FLAT, borderwidth=1)
            e.insert(0, str(self.config.get(key, DEFAULTS[key])))
            e.pack(side=tk.RIGHT)
            self.entries[key] = (e, typ)
            e.bind('<KeyRelease>', lambda ev: self._on_param_change())
            e.bind('<FocusOut>', lambda ev: self._on_param_change())

            # Комментарий-подсказка под полем
            desc = tk.Label(self.params_frame, text=t('desc_' + key), bg=c['bg'], fg='#888888',
                            font=('Segoe UI', 9), justify=tk.LEFT, wraplength=250, anchor='w')
            desc.pack(fill=tk.X, pady=(0, 4))

        # Подсказки для валидации (под полями)
        self._hint_labels = {}
        for key, label, typ in fields:
            hint_row = tk.Frame(self.params_frame, bg=c['bg'])
            hint_row.pack(fill=tk.X, pady=0)
            hint = tk.Label(hint_row, text='', bg=c['bg'], fg='#ff5555',
                           font=('Segoe UI', 9), anchor='w')
            hint.pack(side=tk.LEFT, padx=(0, 0))
            self._hint_labels[key] = hint

        self.restart_var = tk.BooleanVar(value=self.config.get('restart', True))
        cb = tk.Checkbutton(self.params_frame, text='Перезапуск', variable=self.restart_var,
                            bg=c['bg'], fg=c['fg'], selectcolor=c['entry_bg'],
                            activebackground=c['bg'], activeforeground=c['fg'],
                            font=('Segoe UI', 10))
        cb.pack(anchor='w', pady=3)
        self.restart_var.trace_add('write', lambda *a: self.schedule_calculate())

        self.launch_after_var = tk.BooleanVar(value=self.config.get('launch_after', False))
        cb2 = tk.Checkbutton(self.params_frame, text='Лаунчер после', variable=self.launch_after_var,
                             bg=c['bg'], fg=c['fg'], selectcolor=c['entry_bg'],
                             activebackground=c['bg'], activeforeground=c['fg'],
                             font=('Segoe UI', 10))
        cb2.pack(anchor='w', pady=3)

        btn_frame = tk.Frame(self.params_frame, bg=c['bg'])
        btn_frame.pack(pady=8)
        self.start_btn = tk.Button(btn_frame, text='Старт', command=self.start_training,
                                  bg=c['accent'], fg='white', font=('Segoe UI', 11, 'bold'),
                                  padx=16, pady=4)
        self.start_btn.pack(side=tk.LEFT, padx=4)
        self.stop_btn = tk.Button(btn_frame, text='Стоп', command=self.stop_training,
                                 bg='#f44336', fg='white', font=('Segoe UI', 11, 'bold'),
                                 padx=16, pady=4)
        # Кнопка Стоп скрыта по умолчанию (показывается при запуске обучения)

        # Кнопка сброса параметров к рекомендованным
        def _reset_defaults():
            rec = {'pop': 50, 'rounds': 15, 'episode': 20.0, 'episode_increment': 5.0,
                   'speed': 3.0, 'top_percent': 0.1}
            for key, (e, typ) in self.entries.items():
                if key in rec:
                    e.delete(0, tk.END)
                    e.insert(0, str(rec[key]))
            self._on_param_change()
        tk.Button(self.params_frame, text=t('btn_default'), command=_reset_defaults,
                  bg=c['entry_bg'], fg=c['fg'], relief=tk.FLAT, cursor='hand2').pack(
            fill=tk.X, pady=(8, 4))

        self.result_label = tk.Label(self.params_frame, text='', bg=c['bg'], fg=c['fg'],
                                     font=('Consolas', 12, 'bold'), justify=tk.LEFT)
        self.result_label.pack(pady=8, anchor='w')
        self.result_detail = tk.Label(self.params_frame, text='', bg=c['bg'], fg=c['fg'],
                                     font=('Consolas', 10), justify=tk.LEFT)
        self.result_detail.pack(anchor='w')

        # Валидация при запуске
        self.root.after(100, self._validate_pop)

        # Панель подробностей (через _build_detail_panel для консистентности)
        self._build_detail_panel()

        self.result_label = tk.Label(self.params_frame, text='', bg=c['bg'], fg=c['fg'],
                                    font=('Consolas', 12, 'bold'), justify=tk.LEFT)
        self.result_label.pack(pady=8, anchor='w')
        self.result_detail = tk.Label(self.params_frame, text='', bg=c['bg'], fg=c['fg'],
                                     font=('Consolas', 10), justify=tk.LEFT)
        self.result_detail.pack(anchor='w')

        # Валидация при запуске
        self.root.after(100, self._validate_pop)

        # Консоль - отдельное окно (по умолчанию)
        if not self.config.get('console_detached', True):
            self.config['console_detached'] = True
            save_config(self.config)

        # График внизу (только если консоль встроена - но она всегда отдельная, так что не показываем)
        # График будет в окне консоли

        # Установить размер окна
        self.root.update_idletasks()
        self.root.minsize(600, 400)
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

        self.detail_frame = tk.Frame(self.main_frame, bg=panel_bg, width=280)
        self.detail_frame.pack_propagate(False)

        # Заголовок с кнопками
        header = tk.Frame(self.detail_frame, bg=c['title_bg'], height=26)
        header.pack(fill=tk.X)
        header.pack_propagate(False)

        # Кнопка перемещения влево (скрыта если крайнее левое)
        # Обе стрелки всегда отображаются
        move_left_btn = tk.Button(header, text='\u2190', font=('Segoe UI', 11),
                                 bg=c['title_bg'], fg=c['fg'], relief=tk.FLAT,
                                 borderwidth=0, cursor='hand2',
                                 command=lambda: self._move_panel('detail', 'left'))
        move_left_btn.pack(side=tk.LEFT, padx=2)

        move_right_btn = tk.Button(header, text='\u2192', font=('Segoe UI', 11),
                                  bg=c['title_bg'], fg=c['fg'], relief=tk.FLAT,
                                  borderwidth=0, cursor='hand2',
                                  command=lambda: self._move_panel('detail', 'right'))
        move_right_btn.pack(side=tk.LEFT, padx=2)

        tk.Label(header, text='Детали', bg=c['title_bg'], fg=c['fg'],
                 font=('Segoe UI', 10, 'bold')).pack(side=tk.LEFT, padx=8)

        # Кнопка открепления (справа)

        content = tk.Frame(self.detail_frame, bg=panel_bg)
        content.pack(fill=tk.BOTH, expand=True)
        self._build_detail_content(content, c)

        # Упаковать в правильную позицию
        if self.config.get('detail_side', 'left') == 'left':
            self.detail_frame.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=(0, 5))
        else:
            self.detail_frame.pack(side=tk.RIGHT, fill=tk.BOTH, expand=False, padx=(0, 5))

    def _build_detail_content(self, parent, c):
        detail_scroll = ttk.Scrollbar(parent, orient=tk.VERTICAL)
        self.detail_text = tk.Text(parent, font=('Consolas', 9), wrap=tk.WORD,
                                  bg=c.get('panel_bg', c['entry_bg']), fg=c['fg'],
                                  insertbackground=c['fg'],
                                  selectbackground='#264f78',
                                  relief=tk.FLAT, borderwidth=0,
                                  yscrollcommand=detail_scroll.set,
                                  padx=8, pady=8)
        detail_scroll.config(command=self.detail_text.yview)
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
        self.detail_window, content = create_themed_toplevel(self.root, 'Детали', c, 320, 600, attach_callback=self._attach_detail)
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
        detail_scroll.config(command=self.detail_text.yview)
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
    def apply_and_close():
        self.config['theme'] = theme_var.get()
        self.config['console_content'] = content_var.get()
        save_config(self.config)
        if HAS_SV_TTK:
            sv_theme = 'light' if self.config.get('theme', 'dark') in ('light', 'white') else 'dark'
            sv_ttk.set_theme(sv_theme)
            setup_scrollbar_style()
        c2 = COLOR_SCHEMES.get(self.config.get('theme', 'dark'), COLOR_SCHEMES['dark'])
        self.colors = c2
        self.root.configure(bg=c2['bg'])
        self.title_bar.configure(bg=c2['title_bg'])
        if self.config.get('show_params', True):
            _params_expand = (self.config.get('detail_side', 'left') == 'right') and not self.config.get('detail_detached', False)
            self.params_frame.pack(side=tk.LEFT, fill=tk.BOTH, expand=_params_expand, padx=(0, 5))
        else:
            self.params_frame.pack_forget()
        self._build_detail_panel()
        settings_win.destroy()
        tk.Button(btn_frame, text='Применить', command=apply_and_close, bg=c['accent'], fg='white').pack(side=tk.LEFT, padx=5)
        tk.Button(btn_frame, text='Закрыть', command=settings_win.destroy, bg=c['entry_bg'], fg=c['fg']).pack(side=tk.LEFT, padx=5)

    def _open_theme_menu(self):
        c = self.colors
        menu = tk.Menu(self.root, tearoff=0, bg=c['entry_bg'], fg=c['fg'],
                       activebackground=c['accent'], activeforeground='white')
        for name in COLOR_SCHEMES:
            menu.add_command(label=name, command=lambda n=name: self._apply_theme(n))
        try:
            x = self.theme_btn.winfo_rootx()
            y = self.theme_btn.winfo_rooty() + self.theme_btn.winfo_height()
            menu.post(x, y)
        except Exception:
            menu.post(self.root.winfo_pointerx(), self.root.winfo_pointery())

    def _apply_theme(self, name):
        self.config['theme'] = name
        save_config(self.config)
        if HAS_SV_TTK:
            sv_theme = 'light' if name in ('light', 'white') else 'dark'
            sv_ttk.set_theme(sv_theme)
        setup_scrollbar_style()
        self.colors = COLOR_SCHEMES.get(name, COLOR_SCHEMES['dark'])
        self.root.configure(bg=self.colors['bg'])
        # Перестроить интерфейс с новыми цветами
        self._rebuild_ui()

    def _open_language_menu(self):
        c = self.colors
        menu = tk.Menu(self.root, tearoff=0, bg=c['entry_bg'], fg=c['fg'],
                       activebackground=c['accent'], activeforeground='white')
        for code, label in (('ru', 'Русский'), ('en', 'English')):
            menu.add_command(label=label, command=lambda cd=code, lb=label: self._apply_language(cd, lb))
        try:
            x = self.lang_btn.winfo_rootx()
            y = self.lang_btn.winfo_rooty() + self.lang_btn.winfo_height()
            menu.post(x, y)
        except Exception:
            menu.post(self.root.winfo_pointerx(), self.root.winfo_pointery())

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
        scroll.config(command=help_text.yview)
        help_text.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        scroll.pack(side=tk.RIGHT, fill=tk.Y)

        # Подробное содержимое справки
        body = []
        body.append(t('help_intro'))
        body.append('')
        body.append('=' * 60)
        body.append(t('param_section').upper())
        body.append('=' * 60)
        body.append('')
        params_detail = [
            ('pop', 'Популяция (population)',
             'Сколько ботов (геномов) в одном поколении. Каждый бот играет отдельный эпизод. '
             'Больше = разнообразнее поиск и точнее отбор, но линейно растёт время обучения. '
             'Рекомендуется 30-80. Минимум 50 (защита от вырождения).'),
            ('rounds', 'Раунды (generations)',
             'Сколько поколений будет обучаться бот. Каждый раунд: все боты играют эпизод → отбор лучших → скрещивание/мутация → новое поколение. '
             'Больше раундов = лучше финальный геном. Рекомендуется 10-25.'),
            ('episode', 'Эпизод, сек (episode length)',
             'Длительность одного эпизода в секундах при скорости 1x. За это время бот должен набрать максимум очков. '
             'Короткий эпизод = быстрое обучение, но бот не успевает развиться. Рекомендуется 20-40.'),
            ('episode_increment', 'Прирост, сек (episode increment)',
             'На сколько секунд растёт эпизод каждый следующий раунд. Позволяет постепенно усложнять задачу: сначала короткие эпизоды, потом длинные. '
             'Рекомендуется 3-10. 0 = фиксированная длина.'),
            ('speed', 'Скорость (game speed)',
             'Скорость игры: 1x, 2x, 4x. При 4x обучение в 4 раза быстрее, игра это выдерживает. 8x ломает игру (тайминги). '
             'Рекомендуется 4.0.'),
            ('top_percent', 'Топ (elitism fraction)',
             'Доля лучших ботов, которые переходят в следующее поколение без изменений (элитизм). '
             '0.1 = 10% лучших сохраняются. Остальные 90% — скрещивание/мутация. Рекомендуется 0.1-0.25.'),
        ]
        for key, name, desc in params_detail:
            body.append('%s [%s]' % (name, key))
            body.append('  ' + desc)
            body.append('')
        body.append('Дополнительные (через командную строку / evo.py):')
        body.append('  --elite-window N  Окно сглаживания элитного генома (по умолч. 3).')
        body.append('  --crossover-rate  Вероятность скрещивания (по умолч. 0.5).')
        body.append('  --mut-strength    Начальная сила мутации (по умолч. 0.15).')
        body.append('  --mut-decay       Затухание мутации по раундам (по умолч. 0.9).')
        body.append('  --mut-min         Минимальная сила мутации (по умолч. 0.03).')
        body.append('  --restart         Перезапускать игру между раундами (по умолч. вкл.).')
        body.append('  --no-restart      Не перезапускать игру.')
        body.append('  --baseline        Запустить baseline на случайных геномах.')
        body.append('  --resetAll        Сбросить все данные (геномы, история, baseline).')
        body.append('  --human-genomes   Путь к human_genome_*.json (через запятую).')
        body.append('')
        body.append('=' * 60)
        body.append('КАК РАБОТАЕТ БОТ')
        body.append('=' * 60)
        body.append('')
        body.append(t('how_it_works_text'))
        body.append('')
        body.append('Управление: бот контролирует только короля (клавиши A/D/Shift/S). '
                    'Окно игры не захватывается без необходимости.')
        body.append('Горячие клавиши (обрабатываются в evo.py): F8 — пауза, F9 — стоп бота, F12 — полный стоп.')
        body.append('')
        body.append('Скоринг:')
        body.append('  - Здания/деревья: очки начисляются только при завершении постройки/сруба.')
        body.append('  - Найм: очки только при успешном найме нищего (recruited_beggars).')
        body.append('  - Смерть: -10000 (повторная атака без монет или корона унесена в портал).')
        body.append('  - Бездействие: idle=-5, no_interaction=-5 (смягчено, но не убрано).')
        body.append('  - Прогресс: множитель 1.05, дневной бонус 0.05.')
        body.append('')
        body.append('Геномы людей: файлы human_genome_*.json автоматически подгружаются из папки проекта.')
        body.append('Скорость игры: максимум 4x (8x ломает игру).')
        body.append('')
        body.append('=' * 60)
        body.append('КОМАНДНАЯ СТРОКА')
        body.append('=' * 60)
        body.append('')
        body.append('Запуск обучения вручную:')
        body.append('  py evo.py --pop 50 --rounds 15 --episode 20 --episode-increment 5 --speed 3 --top-percent 0.1')
        body.append('')
        body.append('Все аргументы:')
        body.append('  --pop N               Популяция (умолч. 12)')
        body.append('  --rounds N            Раунды (умолч. 10)')
        body.append('  --episode F           Длина эпизода, сек (умолч. 30.0)')
        body.append('  --episode-increment F Прирост длины (умолч. 10.0)')
        body.append('  --speed F             Скорость игры 1-3 (умолч. 3.0, 4x+ ломает игру)')
        body.append('  --top-percent F       Доля элиты 0-1 (умолч. 0.1)')
        body.append('  --elite-window N      Окно сглаживания (умолч. 3)')
        body.append('  --crossover-rate F    Скрещивание (умолч. 0.5)')
        body.append('  --mut-strength F      Сила мутации (умолч. 0.15)')
        body.append('  --mut-decay F         Затухание (умолч. 0.9)')
        body.append('  --mut-min F           Мин. мутация (умолч. 0.03)')
        body.append('  --restart / --no-restart   Перезапуск игры')
        body.append('  --baseline            Baseline-режим')
        body.append('  --resetAll            Полный сброс')
        body.append('  --human-genomes PATH  Человеческие геномы')
        body.append('')
        body.append('=' * 60)
        body.append('ЛАУНЧЕР (launcher.py)')
        body.append('=' * 60)
        body.append('')
        body.append('Параметры вводятся в левой панели. Кнопка «По умолчанию» сбрасывает к рекомендованным.')
        body.append('Тема: кнопка солнца в заголовке → выбор из списка, применяется на лету.')
        body.append('Язык: кнопка глобуса → Русский/English, локализация из locales/*.json.')
        body.append('Справка: кнопка «?» → это окно. Поиск: введите текст, подсветятся все совпадения,')
        body.append('переход по результатам кнопками ◀ ▶, счётчик показывает текущий/всего.')
        body.append('Панели можно двигать стрелками ← → в заголовке панели.')
        body.append('Консоль и детали можно открепить в отдельные окна (кнопка ↙).')
        body.append('Геометрия всех окон сохраняется в %APPDATA%/KCbot/ и восстанавливается при перезапуске.')
        body.append('')
        body.append('Логи: logs/<время_запуска>/<N> generation_<M> bot.log — не удаляются очисткой.')

        full_text = '\n'.join(body)
        help_text.insert('1.0', full_text)
        help_text.config(state=tk.DISABLED)

        # Поиск с навигацией
        matches = []

        def do_search(*args):
            nonlocal matches
            query = search_var.get().strip()
            help_text.config(state=tk.NORMAL)
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
                count_label.config(text='1/%d' % len(matches))
            else:
                count_label.config(text='0/0' if query else '')
            help_text.config(state=tk.DISABLED)

        current_index = [0]

        def go_next():
            if not matches:
                return
            current_index[0] = (current_index[0] + 1) % len(matches)
            i = current_index[0]
            pos, end_pos = matches[i]
            help_text.config(state=tk.NORMAL)
            # восстановить предыдущий в found
            help_text.tag_remove('current', '1.0', tk.END)
            # перетегать все found заново проще: добавить current поверх
            help_text.see(pos)
            help_text.tag_add('current', pos, end_pos)
            count_label.config(text='%d/%d' % (i + 1, len(matches)))
            help_text.config(state=tk.DISABLED)

        def go_prev():
            if not matches:
                return
            current_index[0] = (current_index[0] - 1) % len(matches)
            i = current_index[0]
            pos, end_pos = matches[i]
            help_text.config(state=tk.NORMAL)
            help_text.tag_remove('current', '1.0', tk.END)
            help_text.see(pos)
            help_text.tag_add('current', pos, end_pos)
            count_label.config(text='%d/%d' % (i + 1, len(matches)))
            help_text.config(state=tk.DISABLED)

        # Обернуть do_search чтобы сбрасывать индекс
        orig_search = do_search
        def do_search2(*args):
            nonlocal matches
            current_index[0] = 0
            orig_search(*args)
        search_var.trace_add('write', do_search2)
        search_entry.bind('<Return>', lambda e: go_next())
        prev_btn.config(command=go_prev)
        next_btn.config(command=go_next)

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

    VALIDATION_RULES = {
        'pop': (50, 500, 'Мин. 50, макс. 500'),
        'rounds': (1, 50, 'Мин. 1, макс. 50'),
        'episode': (10, 300, 'Мин. 10 сек, макс. 300 сек'),
        'episode_increment': (0, 60, 'Мин. 0, макс. 60 сек'),
        'speed': (1, 3, 'Мин. 1x, макс. 3x (на 4x и выше игра ломается)'),
        'top_percent': (0.01, 1.0, 'От 0.01 до 1.0'),
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
                e.config(bg='#3a1a1a', highlightbackground='#ff5555',
                         highlightcolor='#ff5555', highlightthickness=1)
                if hint:
                    hint.config(text=msg)
                all_valid = False
            else:
                e.config(bg=self.colors['entry_bg'], highlightthickness=0)
                if hint:
                    hint.config(text='')

        if all_valid:
            self.start_btn.config(state=tk.NORMAL)
        else:
            self.start_btn.config(state=tk.DISABLED)
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
                self.result_label.config(text='Ошибка: проверьте параметры')
            return
        total, hours, minutes, details = calc_time(cfg)

        if hasattr(self, 'result_label') and self.result_label.winfo_exists():
            self.result_label.config(text='Общее время: %.0f сек = %.1f ч %.0f мин' % (total, hours, minutes))
        if hasattr(self, 'result_detail') and self.result_detail.winfo_exists():
            self.result_detail.config(text='Всего ботов: %d' % (cfg['pop'] * cfg['rounds']))

        # Обновляем обе панели деталей
        for dt in [self.detail_text, getattr(self, 'detail_right_text', None)]:
            if dt is None:
                continue
            dt.delete('1.0', tk.END)
            dt.insert(tk.END, '=== Параметры ===\n')
            dt.insert(tk.END, '  pop: %d\n' % cfg['pop'])
            dt.insert(tk.END, '  rounds: %d\n' % cfg['rounds'])
            dt.insert(tk.END, '  episode: %.1f сек\n' % cfg['episode'])
            dt.insert(tk.END, '  episode-increment: %.1f сек\n' % cfg['episode_increment'])
            dt.insert(tk.END, '  speed: %.1fx\n' % cfg['speed'])
            dt.insert(tk.END, '  restart: %s\n' % cfg['restart'])
            dt.insert(tk.END, '  top-percent: %.4f\n' % cfg['top_percent'])
            dt.insert(tk.END, '\n=== По раундам ===\n')
            for rnd, ep, rt in details:
                dt.insert(tk.END, '  Раунд %2d: episode=%.0f сек, раунд=%.0f сек (%.1f мин)\n' %
                          (rnd, ep, rt, rt / 60.0))

    def start_training(self):
        cfg = self.get_params()
        if cfg is None:
            if hasattr(self, 'result_label'):
                self.result_label.config(text='Ошибка: неверные параметры')
            return
        # Папка игры: сохраняем и проверяем ДО запуска. Раньше launчер её не
        # проверял вообще - evo.py падал посреди прогона с RuntimeError.
        self._save_game_dir()
        if not self._refresh_game_dir_status():
            msg = t('game_dir_missing')
            if hasattr(self, 'result_label'):
                self.result_label.config(text=msg)
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
            args_part = cmd
            if args_part.startswith('py evo.py '):
                args_part = args_part[len('py evo.py '):]
            env['KC_EVO_ARGS'] = args_part
            env['KC_RUN_MODULE'] = 'evo'
            self.process = subprocess.Popen([sys.executable], shell=False, cwd=HERE,
                                           stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                           text=True, bufsize=1, env=env,
                                           encoding='utf-8', errors='replace')
        else:
            self.process = subprocess.Popen(cmd, shell=True, cwd=HERE,
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
        console = self.console_window
        try:
            for line in self.process.stdout:
                if not self.process_running:
                    break
                if console is not None:
                    console.append(line)
            self.process.wait()
        except Exception as e:
            if console is not None:
                console.append('\n!! ошибка чтения вывода: %r\n' % (e,))
        finally:
            self.process_running = False
            if console is not None:
                console.append('\n=== Обучение завершено ===\n')
            # кнопки - только через главный поток
            try:
                self.start_btn.after(0, lambda: self.start_btn.pack(side=tk.LEFT, padx=4))
                self.stop_btn.after(0, lambda: self.stop_btn.pack_forget())
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
            self.console_window.append('\n=== Остановлено пользователем ===\n')
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
    root = tk.Tk()
    root.title('KC Bot')
    # DPI-awareness
    try:
        dpi = root.winfo_fpixels('1i')
        root.tk.call('tk', 'scaling', dpi / 72.0)
    except Exception:
        pass
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

    # Восстановить размер/позицию окна
    saved_geo = load_config().get('window_geometry', None) if 'load_config' in globals() else None
    if saved_geo:
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

    root.configure(bg='#1e1e1e')

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

    root.mainloop()

    # Сохранить геометрию при выходе
    try:
        cfg = load_config()
        cfg['window_geometry'] = root.geometry()
        save_config(cfg)
    except Exception:
        pass
# -*- coding: utf-8 -*-
"""
launcher.py - лаунчер обучения KingdomBot.

Запуск:
    py launcher.py
"""
import ctypes
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

try:
    import matplotlib
    matplotlib.use('TkAgg')
    from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg
    from matplotlib.figure import Figure
    HAS_MPL = True
except ImportError:
    HAS_MPL = False

if getattr(sys, 'frozen', False):
    HERE = os.path.dirname(sys.executable)
else:
    HERE = os.path.dirname(os.path.abspath(__file__))
APPDATA_DIR = os.path.join(os.environ.get('APPDATA', os.path.expanduser('~')), 'KCbot')
CONFIG_FILE = os.path.join(APPDATA_DIR, 'launcher_config.json')
HISTORY_FILE = os.path.join(HERE, 'data', 'evo_history.json')

RESTART_OVERHEAD = 30.0

# Минимальная популяция для нормального обучения (генетический алгоритм)
MIN_POP = 50


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
    except Exception:
        pass


class GUID(ctypes.Structure):
    _fields_ = [
        ('Data1', ctypes.c_ulong),
        ('Data2', ctypes.c_ushort),
        ('Data3', ctypes.c_ushort),
        ('Data4', ctypes.c_ubyte * 8),
    ]

    def __init__(self, guid_str):
        import uuid
        u = uuid.UUID(guid_str)
        b = u.bytes
        self.Data1 = int.from_bytes(b[0:4], 'little')
        self.Data2 = int.from_bytes(b[4:6], 'little')
        self.Data3 = int.from_bytes(b[6:8], 'little')
        for i in range(8):
            self.Data4[i] = b[8 + i]


def setup_scrollbar_style():
    # Заглушка: sv_ttk сам стилизует ttk-виджеты.
    return

os.makedirs(APPDATA_DIR, exist_ok=True)

DEFAULTS = {
    'pop': 50,
    'rounds': 15,
    'episode': 20.0,
    'episode_increment': 5.0,
    'speed': 4.0,
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


def load_history_scores():
    try:
        with open(HISTORY_FILE, 'r', encoding='utf-8') as f:
            history = json.load(f)
        from collections import defaultdict
        by_round = defaultdict(list)
        for r in history:
            by_round[r.get('round', 0)].append(r.get('score', 0))
        rounds = sorted(by_round.keys())
        avgs = [sum(by_round[r]) / len(by_round[r]) for r in rounds]
        return rounds, avgs
    except Exception:
        return [], []


def create_themed_toplevel(parent, title, colors, width=800, height=600, attach_callback=None):
    """Создаёт Toplevel окно с кастомным заголовком и тёмной темой."""
    win = tk.Toplevel(parent)
    win.title(title)
    win.overrideredirect(True)
    win.geometry('%dx%d' % (width, height))
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
    """Окно консоли с выводом и графиком."""

    def __init__(self, parent, colors, detached=False, content='both', on_attach=None, on_finish=None):
        self.parent = parent
        self.colors = colors
        self.detached = detached
        self.content = content
        self.on_attach = None  # attach button removed
        self.on_finish = on_finish
        self.process = None
        self.reader_thread = None
        self.running = False
        self._stopped_by_user = False
        self._finished_posted = False
        self._output_queue = queue.Queue()
        self.graph_detail_window = None

        if detached:
            self.window, self._content = create_themed_toplevel(parent, 'Консоль обучения', colors, 800, 600, on_attach)
        else:
            self.window = tk.Frame(parent, bg=colors['bg'])
            self._content = self.window

        self._build_ui()

    def _build_ui(self):
        c = self.colors
        content = self.content

        paned = tk.PanedWindow(self._content, orient=tk.VERTICAL, bg=c['bg'],
                               sashwidth=4, sashrelief=tk.FLAT)
        paned.pack(fill=tk.BOTH, expand=True, padx=5, pady=5)

        if content in ('console', 'both'):
            self.console_frame = tk.Frame(paned, bg=c['bg'])
            paned.add(self.console_frame, minsize=60, stretch='always')

            console_scroll = ttk.Scrollbar(self.console_frame, orient=tk.VERTICAL)
            self.console_text = tk.Text(self.console_frame, font=('Consolas', 10),
                                        bg=c['entry_bg'], fg=c['fg'],
                                        insertbackground=c['fg'],
                                        selectbackground='#264f78',
                                        relief=tk.FLAT, borderwidth=1,
                                        yscrollcommand=console_scroll.set,
                                        wrap=tk.WORD)
            console_scroll.config(command=self.console_text.yview)
            self.console_text.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
            console_scroll.pack(side=tk.RIGHT, fill=tk.Y)
        else:
            self.console_text = None
            self.console_frame = None

        if HAS_MPL and content in ('graph', 'both'):
            self.graph_frame = tk.Frame(paned, bg=c['graph_bg'])
            paned.add(self.graph_frame, minsize=60, stretch='always')

            self.fig = Figure(figsize=(8, 3), dpi=80, facecolor=c['graph_bg'])
            self.ax = self.fig.add_subplot(111)
            self.ax.set_facecolor(c['graph_bg'])
            self.ax.tick_params(colors=c['graph_fg'])
            for spine in self.ax.spines.values():
                spine.set_color(c['graph_fg'])

            self.canvas = FigureCanvasTkAgg(self.fig, master=self.graph_frame)
            self.canvas.get_tk_widget().pack(fill=tk.BOTH, expand=True)
            self.canvas.get_tk_widget().bind('<Double-Button-1>', self.open_graph_detail)
            self.canvas.get_tk_widget().bind('<MouseWheel>', self._on_mousewheel_zoom)
            self.canvas.get_tk_widget().bind('<Button-4>', self._on_mousewheel_zoom)
            self.canvas.get_tk_widget().bind('<Button-5>', self._on_mousewheel_zoom)

            self._update_graph()
        else:
            self.canvas = None
            self.graph_frame = None

    def open_graph_detail(self, event=None):
        if self.graph_detail_window is not None and self.graph_detail_window.winfo_exists():
            self.graph_detail_window.lift()
            return
        if not HAS_MPL:
            return
        c = self.colors
        self.graph_detail_window, gd_content = create_themed_toplevel(
            self.parent if not self.detached else self.window, 'График скоров (подробно)',
            self.colors, 1000, 700)

        # Панель инструментов
        toolbar_frame = tk.Frame(gd_content, bg=c['bg'])
        toolbar_frame.pack(fill=tk.X, padx=5, pady=3)
        tk.Button(toolbar_frame, text='Растянуть X', command=lambda: self._zoom_detail(1.3),
                  bg=c['entry_bg'], fg=c['fg']).pack(side=tk.LEFT, padx=2)
        tk.Button(toolbar_frame, text='Сжать X', command=lambda: self._zoom_detail(0.7),
                  bg=c['entry_bg'], fg=c['fg']).pack(side=tk.LEFT, padx=2)
        tk.Button(toolbar_frame, text='Сброс', command=self._reset_detail_zoom,
                  bg=c['entry_bg'], fg=c['fg']).pack(side=tk.LEFT, padx=2)
        tk.Button(toolbar_frame, text='Панорама', command=self._toggle_pan_detail,
                  bg=c['entry_bg'], fg=c['fg']).pack(side=tk.LEFT, padx=2)

        # Поле информации о точке
        self.graph_info_label = tk.Label(gd_content, text='Наведите мышь на точку графика',
                                        bg=c['entry_bg'], fg=c['fg'],
                                        font=('Consolas', 10), anchor='w', padx=10)
        self.graph_info_label.pack(fill=tk.X, padx=5, pady=3)

        # График
        self.detail_fig = Figure(figsize=(10, 6), dpi=100, facecolor=c['graph_bg'])
        self.detail_ax = self.detail_fig.add_subplot(111)
        self.detail_ax.set_facecolor(c['graph_bg'])
        self.detail_ax.tick_params(colors=c['graph_fg'])
        for spine in self.detail_ax.spines.values():
            spine.set_color(c['graph_fg'])

        self.detail_canvas = FigureCanvasTkAgg(self.detail_fig, master=gd_content)
        self.detail_canvas.get_tk_widget().pack(fill=tk.BOTH, expand=True, padx=5, pady=5)

        from matplotlib.backends.backend_tkagg import NavigationToolbar2Tk
        nav_frame = tk.Frame(gd_content, bg=c['bg'])
        nav_frame.pack(fill=tk.X)
        nav = NavigationToolbar2Tk(self.detail_canvas, nav_frame)
        nav.update()

        # Привязки
        self.detail_canvas.get_tk_widget().bind('<MouseWheel>', self._on_detail_mousewheel)
        self.detail_canvas.get_tk_widget().bind('<Button-4>', self._on_detail_mousewheel)
        self.detail_canvas.get_tk_widget().bind('<Button-5>', self._on_detail_mousewheel)
        self.detail_canvas.get_tk_widget().bind('<Motion>', self._on_detail_hover)
        self.detail_canvas.get_tk_widget().bind('<ButtonPress-1>', self._start_pan_detail)
        self.detail_canvas.get_tk_widget().bind('<B1-Motion>', self._do_pan_detail)
        self.detail_canvas.get_tk_widget().bind('<ButtonRelease-1>', self._end_pan_detail)

        self._panning_detail = False
        self._pan_start_detail = None
        self._original_xlim = None
        self._original_ylim = None

        self._draw_detail_graph()

    def _draw_detail_graph(self):
        if not HAS_MPL:
            return
        rounds, avgs = load_history_scores()
        self.detail_ax.clear()
        self.detail_ax.set_facecolor(self.colors['graph_bg'])
        self.detail_ax.tick_params(colors=self.colors['graph_fg'])
        for spine in self.detail_ax.spines.values():
            spine.set_color(self.colors['graph_fg'])
        if rounds:
            self.detail_ax.plot(rounds, avgs, 'o-', color=self.colors['accent'],
                                linewidth=2, markersize=8, picker=5)
            self.detail_ax.fill_between(rounds, avgs, alpha=0.2, color=self.colors['accent'])
            for r, a in zip(rounds, avgs):
                self.detail_ax.annotate('%.0f' % a, (r, a), textcoords='offset points',
                                        xytext=(0, 10), ha='center', fontsize=8,
                                        color=self.colors['graph_fg'])
        self.detail_ax.set_xlabel('Раунд', color=self.colors['graph_fg'])
        self.detail_ax.set_ylabel('Средний скор', color=self.colors['graph_fg'])
        self.detail_ax.set_title('Средний скор по раундам', color=self.colors['graph_fg'])
        self.detail_ax.grid(True, alpha=0.3)
        if self._original_xlim is None:
            self._original_xlim = self.detail_ax.get_xlim()
            self._original_ylim = self.detail_ax.get_ylim()
        self.detail_canvas.draw()

    def _zoom_detail(self, factor):
        if not hasattr(self, 'detail_ax'):
            return
        xlim = self.detail_ax.get_xlim()
        x_center = (xlim[0] + xlim[1]) / 2
        x_range = (xlim[1] - xlim[0]) / factor
        new_xlim = (x_center - x_range / 2, x_center + x_range / 2)
        self.detail_ax.set_xlim(new_xlim)
        # Автоматически подгоняем Y по видимым данным
        rounds, avgs = load_history_scores()
        if rounds:
            visible = [(r, a) for r, a in zip(rounds, avgs) if new_xlim[0] <= r <= new_xlim[1]]
            if visible:
                ys = [a for _, a in visible]
                y_min, y_max = min(ys), max(ys)
                margin = (y_max - y_min) * 0.15 if y_max != y_min else 10
                self.detail_ax.set_ylim(y_min - margin, y_max + margin)
        self.detail_canvas.draw()

    def _reset_detail_zoom(self):
        if not hasattr(self, 'detail_ax'):
            return
        if self._original_xlim is not None:
            self.detail_ax.set_xlim(self._original_xlim)
            self.detail_ax.set_ylim(self._original_ylim)
        self.detail_canvas.draw()

    def _toggle_pan_detail(self):
        self._panning_detail = not self._panning_detail
        if hasattr(self, 'graph_info_label'):
            if self._panning_detail:
                self.graph_info_label.config(text='Режим панорамы: зажмите ЛКМ и тащите')
            else:
                self.graph_info_label.config(text='Наведите мышь на точку графика')

    def _start_pan_detail(self, event):
        if not self._panning_detail:
            return
        try:
            xdata, ydata = self.detail_ax.transData.inverted().transform((event.x, event.y))
            self._pan_start_detail = (xdata, ydata)
        except Exception:
            self._pan_start_detail = None

    def _do_pan_detail(self, event):
        if not self._panning_detail or self._pan_start_detail is None:
            return
        try:
            xdata, ydata = self.detail_ax.transData.inverted().transform((event.x, event.y))
        except Exception:
            return
        if xdata is None or ydata is None:
            return
        dx = self._pan_start_detail[0] - xdata
        dy = self._pan_start_detail[1] - ydata
        xlim = self.detail_ax.get_xlim()
        ylim = self.detail_ax.get_ylim()
        self.detail_ax.set_xlim(xlim[0] + dx, xlim[1] + dx)
        self.detail_ax.set_ylim(ylim[0] + dy, ylim[1] + dy)
        self.detail_canvas.draw()
        self._pan_start_detail = (xdata, ydata)

    def _end_pan_detail(self, event):
        self._pan_start_detail = None

    def _on_detail_mousewheel(self, event):
        if hasattr(event, 'delta'):
            factor = 1.1 if event.delta > 0 else 0.9
        elif event.num == 4:
            factor = 1.1
        elif event.num == 5:
            factor = 0.9
        else:
            return
        self._zoom_detail(factor)

    def _on_detail_hover(self, event):
        if not hasattr(self, 'graph_info_label'):
            return
        # Конвертируем координаты tkinter в координаты данных
        try:
            xdata, ydata = self.detail_ax.transData.inverted().transform((event.x, event.y))
        except Exception:
            xdata, ydata = None, None
        if xdata is None or ydata is None:
            self.graph_info_label.config(text='Наведите мышь на точку графика')
            return
        rounds, avgs = load_history_scores()
        if not rounds:
            return
        dists = [abs(r - xdata) for r in rounds]
        idx = dists.index(min(dists))
        r = rounds[idx]
        a = avgs[idx]
        self.graph_info_label.config(text='Раунд %d: средний скор = %.1f  (всего раундов: %d)' % (r, a, len(rounds)))
    def _on_mousewheel_zoom(self, event):
        if not HAS_MPL or not hasattr(self, 'canvas') or self.canvas is None:
            return
        if hasattr(event, 'delta'):
            factor = 1.1 if event.delta > 0 else 0.9
        elif event.num == 4:
            factor = 1.1
        elif event.num == 5:
            factor = 0.9
        else:
            return
        xlim = self.ax.get_xlim()
        x_center = (xlim[0] + xlim[1]) / 2
        x_range = (xlim[1] - xlim[0]) / factor
        new_xlim = (x_center - x_range / 2, x_center + x_range / 2)
        self.ax.set_xlim(new_xlim)
        # Автоматически подгоняем Y
        rounds, avgs = load_history_scores()
        if rounds:
            visible = [(r, a) for r, a in zip(rounds, avgs) if new_xlim[0] <= r <= new_xlim[1]]
            if visible:
                ys = [a for _, a in visible]
                y_min, y_max = min(ys), max(ys)
                margin = (y_max - y_min) * 0.15 if y_max != y_min else 10
                self.ax.set_ylim(y_min - margin, y_max + margin)
        self.canvas.draw()


    def _update_graph(self):
        if not HAS_MPL or not hasattr(self, 'canvas') or self.canvas is None:
            return
        rounds, avgs = load_history_scores()
        self.ax.clear()
        self.ax.set_facecolor(self.colors['graph_bg'])
        self.ax.tick_params(colors=self.colors['graph_fg'])
        for spine in self.ax.spines.values():
            spine.set_color(self.colors['graph_fg'])
        if rounds:
            self.ax.plot(rounds, avgs, 'o-', color=self.colors['accent'], linewidth=2, markersize=4)
            self.ax.fill_between(rounds, avgs, alpha=0.2, color=self.colors['accent'])
        self.ax.set_xlabel('Раунд', color=self.colors['graph_fg'], fontsize=9)
        self.ax.set_ylabel('Скор', color=self.colors['graph_fg'], fontsize=9)
        self.ax.set_title('Средний скор по раундам', color=self.colors['graph_fg'], fontsize=10)
        self.ax.grid(True, alpha=0.3)
        self.canvas.draw()

    def append_text(self, text):
        if self.console_text is not None:
            self.console_text.insert(tk.END, text)
            self.console_text.see(tk.END)

    def start_process(self, cmd):
        if self.console_text is not None:
            self.console_text.delete('1.0', tk.END)
        self.running = True
        self._stopped_by_user = False
        self._finished_posted = False
        try:
            while True:
                self._output_queue.get_nowait()
        except queue.Empty:
            pass
        self.process = subprocess.Popen(cmd, shell=True, cwd=HERE,
                                       stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                       text=True, bufsize=1)
        self.reader_thread = threading.Thread(target=self._read_output, daemon=True)
        self.reader_thread.start()
        self._schedule_graph_update()
        self._schedule_poll()

    def _read_output(self):
        if self.process is None:
            return
        try:
            for line in self.process.stdout:
                if not self.running:
                    break
                self._output_queue.put(line)
            self.process.wait()
        except Exception:
            pass
        finally:
            if not self._finished_posted:
                self._finished_posted = True
                self._output_queue.put(None)

    def _schedule_poll(self):
        if hasattr(self, 'window') and self.window.winfo_exists():
            self.window.after(100, self._poll_output)

    def _poll_output(self):
        if not hasattr(self, 'window') or not self.window.winfo_exists():
            return
        try:
            while True:
                item = self._output_queue.get_nowait()
                if item is None:
                    self.running = False
                    if self.console_text is not None and not self._stopped_by_user:
                        self.append_text('\n=== Обучение завершено ===\n')
                    self._update_graph()
                    if self.on_finish is not None:
                        try:
                            self.on_finish()
                        except Exception:
                            pass
                    return
                if self.console_text is not None:
                    self.append_text(item)
        except queue.Empty:
            pass
        if self.running or not self._output_queue.empty():
            self.window.after(100, self._poll_output)

    def _schedule_graph_update(self):
        if self.running:
            self._update_graph()
            if hasattr(self, 'window') and self.window.winfo_exists():
                self.window.after(10000, self._schedule_graph_update)
        else:
            self._update_graph()

    def stop(self):
        self._stopped_by_user = True
        was_running = self.running
        self.running = False
        if self.process and self.process.poll() is None:
            self.process.terminate()
            try:
                self.process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self.process.kill()
            if self.console_text is not None:
                self.append_text('\n=== Остановлено пользователем ===\n')
        elif was_running and self.console_text is not None:
            self.append_text('\n=== Остановлено пользователем ===\n')

    def destroy(self):
        self.stop()
        if hasattr(self, 'window') and self.window.winfo_exists():
            self.window.destroy()


class SidePanel:
    """Боковая панель с кнопкой открепления/прикрепления."""

    def __init__(self, parent, title, colors, content_builder, detached=False, width=300):
        self.parent = parent
        self.colors = colors
        self.detached = detached
        self.title = title
        self.content_builder = content_builder
        self.width = width
        self.detached_window = None

        if detached:
            self.window, self._content = create_themed_toplevel(parent, title, colors, width, 600)
            self._build_detached()
        else:
            self.window = tk.Frame(parent, bg=colors['bg'], width=width)
            self.window.pack_propagate(False)
            self._build_attached()

    def _build_attached(self):
        c = self.colors
        header = tk.Frame(self.window, bg=c['title_bg'], height=26)
        header.pack(fill=tk.X)
        header.pack_propagate(False)

        tk.Label(header, text=self.title, bg=c['title_bg'], fg=c['fg'],
                 font=('Segoe UI', 10, 'bold')).pack(side=tk.LEFT, padx=8)

        detach_btn = tk.Button(header, text='\u2197', font=('Segoe UI', 11),
                               bg=c['title_bg'], fg=c['fg'], relief=tk.FLAT,
                               borderwidth=0, cursor='hand2',
                               command=self.detach)
        detach_btn.pack(side=tk.RIGHT, padx=4)

        content_frame = tk.Frame(self.window, bg=c['bg'])
        content_frame.pack(fill=tk.BOTH, expand=True)
        self.content_builder(content_frame, c)

    def _build_detached(self):
        c = self.colors
        # Кнопка прикрепления в заголовке (уже есть заголовок от create_themed_toplevel)
        header = self.window.winfo_children()[0]
        attach_btn = tk.Button(header, text='\u2199', font=('Segoe UI', 12),
                               bg=c['title_bg'], fg=c['fg'], relief=tk.FLAT,
                               borderwidth=0, cursor='hand2',
                               command=self.attach)
        attach_btn.pack(side=tk.RIGHT, padx=4)

        content_frame = tk.Frame(self._content, bg=c['bg'])
        content_frame.pack(fill=tk.BOTH, expand=True)
        self.content_builder(content_frame, c)

    def detach(self):
        self.detached = True
        for w in self.window.winfo_children():
            w.destroy()
        self.window.pack_forget()
        self.window, self._content = create_themed_toplevel(self.parent, self.title, self.colors, self.width, 600)
        self._build_detached()
        self.window.geometry('+%d+%d' % (self.parent.winfo_x() + 100, self.parent.winfo_y() + 100))

    def attach(self):
        self.detached = False
        self.window.destroy()
        self.window = tk.Frame(self.parent, bg=self.colors['bg'], width=self.width)
        self.window.pack_propagate(False)
        self._build_attached()

    def destroy(self):
        if hasattr(self, 'window') and self.window.winfo_exists():
            self.window.destroy()


class SettingsDialog:
    """Окно настроек (Toplevel)."""

    def __init__(self, parent, config, on_apply, colors):
        self.parent = parent
        self.config = dict(config)
        self.on_apply = on_apply
        self.colors = colors

        self.win, self._content = create_themed_toplevel(parent, 'Настройки', colors, 640, 520)
        self.win.grab_set()

        c = colors
        self.win.configure(bg=c['bg'])

        main_frame = tk.Frame(self._content, bg=c['bg'])
        main_frame.pack(fill=tk.BOTH, expand=True, padx=15, pady=15)

        left_col = tk.Frame(main_frame, bg=c['bg'])
        left_col.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=(0, 10))

        right_col = tk.Frame(main_frame, bg=c['bg'])
        right_col.pack(side=tk.RIGHT, fill=tk.BOTH, expand=True)

        # === Левая колонка: Внешний вид ===
        tk.Label(left_col, text='Внешний вид', bg=c['bg'], fg=c['fg'],
                 font=('Segoe UI', 12, 'bold')).pack(pady=(0, 5), anchor='w')

        tk.Label(left_col, text='Цветовая схема:', bg=c['bg'], fg=c['fg'],
                 font=('Segoe UI', 10)).pack(anchor='w')
        self.theme_var = tk.StringVar(value=self.config.get('theme', 'dark'))
        for name in COLOR_SCHEMES:
            tk.Radiobutton(left_col, text=name, variable=self.theme_var, value=name,
                           bg=c['bg'], fg=c['fg'], selectcolor=c['entry_bg'],
                           activebackground=c['bg'], activeforeground=c['fg']).pack(anchor='w', padx=10)

        # === Правая колонка: Панели ===
        tk.Label(right_col, text='Панели', bg=c['bg'], fg=c['fg'],
                 font=('Segoe UI', 12, 'bold')).pack(pady=(0, 5), anchor='w')

        tk.Label(right_col, text='Содержимое консоли:', bg=c['bg'], fg=c['fg'],
                 font=('Segoe UI', 10)).pack(anchor='w')
        self.content_var = tk.StringVar(value=self.config.get('console_content', 'both'))
        for val, label in [('console', 'Только вывод'), ('graph', 'Только график'),
                           ('both', 'Вывод + график')]:
            tk.Radiobutton(right_col, text=label, variable=self.content_var, value=val,
                           bg=c['bg'], fg=c['fg'], selectcolor=c['entry_bg'],
                           activebackground=c['bg'], activeforeground=c['fg']).pack(anchor='w', padx=10)

        # Показывать/скрывать панели управляется кнопками в заголовке

        # Кнопки
        btn_frame = tk.Frame(self._content, bg=c['bg'])
        btn_frame.pack(pady=15)
        tk.Button(btn_frame, text='Применить', command=self.apply, bg=c['accent'], fg='white').pack(side=tk.LEFT, padx=5)
        tk.Button(btn_frame, text='Закрыть', command=self.win.destroy, bg=c['entry_bg'], fg=c['fg']).pack(side=tk.LEFT, padx=5)

    def apply(self):
        self.config['theme'] = self.theme_var.get()
        self.config['console_panel'] = self.panel_var.get() if hasattr(self, 'panel_var') else self.config.get('console_panel', 'right')
        self.config['console_content'] = self.content_var.get()
        self.config['show_console'] = self.show_console_var.get()
        self.config['show_params'] = self.show_params_var.get()
        save_config(self.config)
        if HAS_SV_TTK:
            sv_theme = 'light' if self.config.get('theme', 'dark') in ('light', 'white') else 'dark'
            sv_ttk.set_theme(sv_theme)
            setup_scrollbar_style()
        self.on_apply(self.config)


class App:
    def __init__(self, root):
        self.root = root
        self.config = load_config()
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

    def _apply_taskbar_style(self):
        apply_taskbar_style(self.root)

    def _build_title_bar(self):
        c = self.colors
        # Обработка закрытия окна (убить обучение и игру)
        self.root.protocol('WM_DELETE_WINDOW', self._on_close)
        self.title_bar = tk.Frame(self.root, bg=c['title_bg'], height=30)
        self.title_bar.pack(fill=tk.X, side=tk.TOP)
        self.title_bar.pack_propagate(False)

        gear_btn = tk.Button(self.title_bar, text='\u2699', font=('Segoe UI', 13),
                             bg=c['title_bg'], fg=c['fg'], relief=tk.FLAT,
                             borderwidth=0, cursor='hand2', command=self.toggle_settings)
        gear_btn.pack(side=tk.LEFT, padx=(10, 2))

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

    def _build_main(self):
        c = self.colors
        self.main_frame = tk.Frame(self.root, bg=c['bg'])
        self.main_frame.pack(fill=tk.BOTH, expand=True, padx=10, pady=5)

        # Левая панель: параметры
        self.params_frame = tk.Frame(self.main_frame, bg=c['bg'], width=260)
        self.params_frame.pack_propagate(False)
        if self.config.get('show_params', True):
            self.params_frame.pack(side=tk.LEFT, fill=tk.Y, padx=(0, 5))

        tk.Label(self.params_frame, text='Параметры', bg=c['bg'], fg=c['fg'],
                 font=('Segoe UI', 13, 'bold')).pack(pady=(0, 8))

        fields = [
            ('pop', 'Популяция', 'int'),
            ('rounds', 'Раунды', 'int'),
            ('episode', 'Эпизод, сек', 'float'),
            ('episode_increment', 'Прирост, сек', 'float'),
            ('speed', 'Скорость', 'float'),
            ('top_percent', 'Топ', 'float'),
        ]

        self.entries = {}
        self._validation_labels = {}
        for key, label, typ in fields:
            row = tk.Frame(self.params_frame, bg=c['bg'])
            row.pack(fill=tk.X, pady=2)
            tk.Label(row, text=label, bg=c['bg'], fg=c['fg'], width=16, anchor='w',
                     font=('Segoe UI', 10)).pack(side=tk.LEFT)
            e = tk.Entry(row, width=10, font=('Consolas', 11), bg=c['entry_bg'], fg=c['fg'],
                         insertbackground=c['fg'], relief=tk.FLAT, borderwidth=1)
            e.insert(0, str(self.config.get(key, DEFAULTS[key])))
            e.pack(side=tk.RIGHT)
            self.entries[key] = (e, typ)
            e.bind('<KeyRelease>', lambda ev: self._on_param_change())
            e.bind('<FocusOut>', lambda ev: self._on_param_change())

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

        self.result_label = tk.Label(self.params_frame, text='', bg=c['bg'], fg=c['fg'],
                                    font=('Consolas', 12, 'bold'), justify=tk.LEFT)
        self.result_label.pack(pady=8, anchor='w')
        self.result_detail = tk.Label(self.params_frame, text='', bg=c['bg'], fg=c['fg'],
                                     font=('Consolas', 10), justify=tk.LEFT)
        self.result_detail.pack(anchor='w')

        # Валидация при запуске
        self.root.after(100, self._validate_pop)

        # Правая панель: подробности
        self.detail_frame = tk.Frame(self.main_frame, bg=c.get('panel_bg', c['entry_bg']), width=300)
        self.detail_frame.pack_propagate(False)
        self.detail_frame.pack(side=tk.RIGHT, fill=tk.Y, padx=(5, 0))

        dh = tk.Frame(self.detail_frame, bg=c['title_bg'], height=26)
        dh.pack(fill=tk.X)
        dh.pack_propagate(False)
        tk.Label(dh, text='Детали', bg=c['title_bg'], fg=c['fg'],
                 font=('Segoe UI', 10, 'bold')).pack(side=tk.LEFT, padx=8)

        ds = ttk.Scrollbar(self.detail_frame, orient=tk.VERTICAL)
        self.detail_text = tk.Text(self.detail_frame, font=('Consolas', 10), wrap=tk.WORD,
                                  bg=c.get('panel_bg', c['entry_bg']), fg=c['fg'],
                                  insertbackground=c['fg'], selectbackground='#264f78',
                                  relief=tk.FLAT, borderwidth=0, yscrollcommand=ds.set, padx=8, pady=8)
        ds.config(command=self.detail_text.yview)
        self.detail_text.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        ds.pack(side=tk.RIGHT, fill=tk.Y)

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
            self.detail_frame.pack(side=tk.LEFT, fill=tk.Y, padx=(0, 5))
        else:
            self.detail_frame.pack(side=tk.RIGHT, fill=tk.Y, padx=(0, 5))

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

    def _move_detail(self):
        if self.config.get('detail_side', 'left') == 'left':
            self.config['detail_side'] = 'right'
        else:
            self.config['detail_side'] = 'left'
        save_config(self.config)
        self._rebuild_panels()

    def _move_console(self):
        if self.config.get('console_side', 'right') == 'right':
            self.config['console_side'] = 'left'
        else:
            self.config['console_side'] = 'right'
        save_config(self.config)
        self._rebuild_panels()

    def _toggle_detail_panel(self):
        if self.config.get('detail_detached', False):
            self._attach_detail()
        else:
            self._detach_detail()

    def _toggle_console_panel(self):
        if self.console_window is not None and self.console_window.window.winfo_exists():
            self.console_window.destroy()
            self.console_window = None
        else:
            self.console_window = ConsoleWindow(self.root, self.colors, detached=True,
                                               content=self.config.get('console_content', 'both'))
    def toggle_settings(self):
        settings_win, settings_content = create_themed_toplevel(self.root, 'Настройки', self.colors, 560, 520)
        settings_win.grab_set()
        settings_win.transient(self.root)

        c = self.colors
        settings_win.configure(bg=c['bg'])

        main_frame = tk.Frame(settings_content, bg=c['bg'])
        main_frame.pack(fill=tk.BOTH, expand=True, padx=15, pady=15)

        left_col = tk.Frame(main_frame, bg=c['bg'])
        left_col.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=(0, 10))

        right_col = tk.Frame(main_frame, bg=c['bg'])
        right_col.pack(side=tk.RIGHT, fill=tk.BOTH, expand=True)

        # === Левая колонка: Внешний вид ===
        tk.Label(left_col, text='Внешний вид', bg=c['bg'], fg=c['fg'],
                 font=('Segoe UI', 12, 'bold')).pack(pady=(0, 5), anchor='w')

        tk.Label(left_col, text='Цветовая схема:', bg=c['bg'], fg=c['fg'],
                 font=('Segoe UI', 10)).pack(anchor='w')
        theme_var = tk.StringVar(value=self.config.get('theme', 'dark'))
        for name in COLOR_SCHEMES:
            tk.Radiobutton(left_col, text=name, variable=theme_var, value=name,
                           bg=c['bg'], fg=c['fg'], selectcolor=c['entry_bg'],
                           activebackground=c['bg'], activeforeground=c['fg']).pack(anchor='w', padx=10)

        # === Правая колонка: Отображение ===
        tk.Label(right_col, text='Отображение', bg=c['bg'], fg=c['fg'],
                 font=('Segoe UI', 12, 'bold')).pack(pady=(0, 5), anchor='w')

        tk.Label(right_col, text='Содержимое консоли:', bg=c['bg'], fg=c['fg'],
                 font=('Segoe UI', 10)).pack(anchor='w')
        content_var = tk.StringVar(value=self.config.get('console_content', 'both'))
        for val, label in [('console', 'Только вывод'), ('graph', 'Только график'),
                           ('both', 'Вывод + график')]:
            tk.Radiobutton(right_col, text=label, variable=content_var, value=val,
                           bg=c['bg'], fg=c['fg'], selectcolor=c['entry_bg'],
                           activebackground=c['bg'], activeforeground=c['fg']).pack(anchor='w', padx=10)

        # (опции отображения панелей убраны из настроек — управляются кнопками в заголовке)

        # Кнопки
        btn_frame = tk.Frame(settings_content, bg=c['bg'])
        btn_frame.pack(pady=15)
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
            self.params_frame.pack(side=tk.LEFT, fill=tk.Y, padx=(0, 5))
        else:
            self.params_frame.pack_forget()
        self._build_detail_panel()
        settings_win.destroy()
        tk.Button(btn_frame, text='Применить', command=apply_and_close, bg=c['accent'], fg='white').pack(side=tk.LEFT, padx=5)
        tk.Button(btn_frame, text='Закрыть', command=settings_win.destroy, bg=c['entry_bg'], fg=c['fg']).pack(side=tk.LEFT, padx=5)

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
        'speed': (1, 4, 'Мин. 1x, макс. 4x (8x ломает игру)'),
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
        self.config.update(cfg)
        save_config(self.config)
        cmd = build_command(cfg)
        self.start_btn.pack_forget()
        self.stop_btn.pack(side=tk.LEFT, padx=4)

        # Создать окно консоли если нет
        if self.console_window is None or not self.console_window.window.winfo_exists():
            self.console_window = ConsoleWindow(self.root, self.colors, detached=True,
                                               content='both')

        # Очистить консоль
        if self.console_window.console_text is not None:
            self.console_window.console_text.config(state=tk.NORMAL)
            self.console_window.console_text.delete('1.0', tk.END)
            self.console_window.console_text.config(state=tk.DISABLED)

        # Запуск процесса
        self.process = subprocess.Popen(cmd, shell=True, cwd=HERE,
                                       stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                       text=True, bufsize=1)
        self.process_running = True
        self.reader_thread = threading.Thread(target=self._read_output, daemon=True)
        self.reader_thread.start()

    def _read_output(self):
        if self.process is None:
            return
        for line in self.process.stdout:
            if not self.process_running:
                break
            if self.console_window is not None and self.console_window.console_text is not None:
                self.console_window.console_text.after(0, self._append_text, self.console_window.console_text, line)
        self.process.wait()
        self.process_running = False
        if self.console_window is not None and self.console_window.console_text is not None:
            self.console_window.console_text.after(0, self._append_text, self.console_window.console_text, '\n=== Обучение завершено ===\n')
        self.start_btn.after(0, lambda: self.start_btn.pack(side=tk.LEFT, padx=4))
        self.stop_btn.after(0, lambda: self.stop_btn.pack_forget())

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
        if self.console_window is not None and self.console_window.console_text is not None:
            self._append_text(self.console_window.console_text, '\n=== Остановлено пользователем ===\n')
        self.stop_btn.pack_forget()
        self.start_btn.pack(side=tk.LEFT, padx=4)

    def _on_close(self):
        # Остановить обучение и закрыть игру
        self.stop_training()
        # Убить игру
        try:
            subprocess.run(['taskkill', '/F', '/IM', 'Kingdom.exe'],
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        except Exception:
            pass
        self.root.destroy()

    def _append_text(self, widget, text):
        try:
            if not widget.winfo_exists():
                return
            widget.config(state=tk.NORMAL)
            widget.insert(tk.END, text)
            widget.see(tk.END)
            widget.config(state=tk.DISABLED)
        except tk.TclError:
            pass

    def _update_graph(self):
        if not HAS_MPL or not hasattr(self, 'canvas') or self.canvas is None:
            return
        rounds, avgs = load_history_scores()
        self.ax.clear()
        c = self.colors
        self.ax.set_facecolor(c['graph_bg'])
        self.ax.tick_params(colors=c['graph_fg'], labelsize=8)
        for spine in self.ax.spines.values():
            spine.set_color(c['graph_fg'])
        if rounds:
            self.ax.plot(rounds, avgs, 'o-', color=c['accent'], linewidth=2, markersize=4)
            self.ax.fill_between(rounds, avgs, alpha=0.2, color=c['accent'])
        self.ax.set_xlabel('Раунд', color=c['graph_fg'], fontsize=9)
        self.ax.set_ylabel('Скор', color=c['graph_fg'], fontsize=9)
        self.ax.set_title('Средний скор по раундам', color=c['graph_fg'], fontsize=10)
        self.ax.grid(True, alpha=0.3)
        self.canvas.draw()

    def open_graph_detail(self, event=None):
        if not HAS_MPL:
            return
        win = tk.Toplevel(self.root)
        win.title('График скоров (подробно)')
        win.geometry('900x600')
        from matplotlib.backends.backend_tkagg import NavigationToolbar2Tk
        fig = Figure(figsize=(9, 6), dpi=100)
        ax = fig.add_subplot(111)
        rounds, avgs = load_history_scores()
        if rounds:
            ax.plot(rounds, avgs, 'o-', color=self.colors['accent'], linewidth=2, markersize=6)
            ax.fill_between(rounds, avgs, alpha=0.2, color=self.colors['accent'])
        ax.set_xlabel('Раунд')
        ax.set_ylabel('Средний скор')
        ax.set_title('Средний скор по раундам')
        ax.grid(True, alpha=0.3)
        canvas = FigureCanvasTkAgg(fig, master=win)
        canvas.get_tk_widget().pack(fill=tk.BOTH, expand=True)
        toolbar_frame = tk.Frame(win)
        toolbar_frame.pack(fill=tk.X)
        toolbar = NavigationToolbar2Tk(canvas, toolbar_frame)
        toolbar.update()

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
        # AppUserModelID для корректной иконки/кнопки в панели задач
        try:
            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID('KCbot.Launcher')
        except Exception:
            pass
        # Убрать консольное окно (если запуск через консольный python),
        # чтобы в панели задач отображалось только GUI-окно
        pass  # Console hiding disabled for debugging
        # DPI-awareness (Per-Monitor V2) — до создания Tk
        try:
            ctypes.windll.shcore.SetProcessDpiAwareness(2)
        except Exception:
            try:
                ctypes.windll.user32.SetProcessDPIAware()
            except Exception:
                pass
        root = tk.Tk()
        root.title('KC Bot')
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

        root.geometry('1000x700')
        root.minsize(800, 500)

        # root.overrideredirect(True)  # отключено - окно не показывалось

        app = App(root)
        root.deiconify()
        root.update()
        root.lift()
        root.mainloop()

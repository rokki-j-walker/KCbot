path = 'C:/Users/joker/CodeProjects/Python/KingdomBot/launcher.py'
lines = open(path, encoding='utf-8').read().split('\n')

# Find _build_main method
start = None
for i in range(len(lines)):
    if 'def _build_main' in lines[i]:
        start = i
        break

# Find end (next method at class level)
end = None
for i in range(start + 1, len(lines)):
    if lines[i].startswith('    def ') or lines[i].startswith('class '):
        end = i
        break

print("method lines %d to %d" % (start+1, end))

new_method = '''    def _build_main(self):
        c = self.colors
        self.main_frame = tk.Frame(self.root, bg=c['bg'])
        self.main_frame.pack(fill=tk.BOTH, expand=True, padx=10, pady=5)

        # === Левая сторона: Консоль (X) + Детали (Y) ===
        left_panel = tk.Frame(self.main_frame, bg=c['bg'])
        left_panel.pack(side=tk.LEFT, fill=tk.Y, padx=(0, 5))

        # Консоль (левая, верх)
        self.console_left_frame = tk.Frame(left_panel, bg=c.get('panel_bg', c['entry_bg']), width=220)
        self.console_left_frame.pack_propagate(False)
        self.console_left_frame.pack(side=tk.TOP, fill=tk.BOTH, expand=True, pady=(0, 3))

        ch = tk.Frame(self.console_left_frame, bg=c['title_bg'], height=22)
        ch.pack(fill=tk.X)
        ch.pack_propagate(False)
        tk.Label(ch, text='Консоль', bg=c['title_bg'], fg=c['fg'],
                 font=('Segoe UI', 9, 'bold')).pack(side=tk.LEFT, padx=6)
        tk.Button(ch, text='\\u2197', font=('Segoe UI', 9),
                  bg=c['title_bg'], fg=c['fg'], relief=tk.FLAT, borderwidth=0, cursor='hand2',
                  command=self._detach_console).pack(side=tk.RIGHT, padx=3)

        cs = ttk.Scrollbar(self.console_left_frame, orient=tk.VERTICAL)
        self.console_left_text = tk.Text(self.console_left_frame, font=('Consolas', 9), wrap=tk.WORD,
                                        bg=c.get('panel_bg', c['entry_bg']), fg=c['fg'],
                                        insertbackground=c['fg'], selectbackground='#264f78',
                                        relief=tk.FLAT, borderwidth=0, yscrollcommand=cs.set, padx=6, pady=6)
        cs.config(command=self.console_text.yview)
        self.console_text.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        cs.pack(side=tk.RIGHT, fill=tk.Y)

        # Детали (левая, низ)
        self.detail_left_frame = tk.Frame(left_panel, bg=c.get('panel_bg', c['entry_bg']), width=220)
        self.detail_left_frame.pack_propagate(False)
        self.detail_left_frame.pack(side=tk.TOP, fill=tk.BOTH, expand=True, pady=(0, 3))

        dh = tk.Frame(self.detail_left_frame, bg=c['title_bg'], height=22)
        dh.pack(fill=tk.X)
        dh.pack_propagate(False)
        tk.Label(dh, text='Детали', bg=c['title_bg'], fg=c['fg'],
                 font=('Segoe UI', 9, 'bold')).pack(side=tk.LEFT, padx=6)
        tk.Button(dh, text='\\u2197', font=('Segoe UI', 9),
                  bg=c['title_bg'], fg=c['fg'], relief=tk.FLAT, borderwidth=0, cursor='hand2',
                  command=self._detach_detail).pack(side=tk.RIGHT, padx=3)

        ds = ttk.Scrollbar(self.detail_left_frame, orient=tk.VERTICAL)
        self.detail_left_text = tk.Text(self.detail_left_frame, font=('Consolas', 9), wrap=tk.WORD,
                                       bg=c.get('panel_bg', c['entry_bg']), fg=c['fg'],
                                       insertbackground=c['fg'], selectbackground='#264f78',
                                       relief=tk.FLAT, borderwidth=0, yscrollcommand=ds.set, padx=6, pady=6)
        ds.config(command=self.detail_text.yview)
        self.detail_text.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        ds.pack(side=tk.RIGHT, fill=tk.Y)

        # === Центр: Параметры ===
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
            e.bind('<KeyRelease>', lambda ev: self.schedule_calculate())
            e.bind('<FocusOut>', lambda ev: self.schedule_calculate())

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
                                 padx=16, pady=4, state=tk.DISABLED)
        self.stop_btn.pack(side=tk.LEFT, padx=4)

        self.result_label = tk.Label(self.params_frame, text='', bg=c['bg'], fg=c['fg'],
                                    font=('Consolas', 12, 'bold'), justify=tk.LEFT)
        self.result_label.pack(pady=8, anchor='w')
        self.result_detail = tk.Label(self.params_frame, text='', bg=c['bg'], fg=c['fg'],
                                     font=('Consolas', 10), justify=tk.LEFT)
        self.result_detail.pack(anchor='w')

        # === Правая сторона: Консоль (X) + Детали (Y) ===
        right_panel = tk.Frame(self.main_frame, bg=c['bg'])
        right_panel.pack(side=tk.RIGHT, fill=tk.Y, padx=(5, 0))

        # Консоль (правая, верх)
        self.console_right_frame = tk.Frame(right_panel, bg=c.get('panel_bg', c['entry_bg']), width=220)
        self.console_right_frame.pack_propagate(False)
        self.console_right_frame.pack(side=tk.TOP, fill=tk.BOTH, expand=True, pady=(0, 3))

        ch_r = tk.Frame(self.console_right_frame, bg=c['title_bg'], height=24)
        ch_r.pack(fill=tk.X)
        ch_r.pack_propagate(False)
        tk.Label(ch_r, text='Консоль', bg=c['title_bg'], fg=c['fg'],
                 font=('Segoe UI', 9, 'bold')).pack(side=tk.LEFT, padx=6)
        tk.Button(ch_r, text='\\u2197', font=('Segoe UI', 9),
                  bg=c['title_bg'], fg=c['fg'], relief=tk.FLAT, borderwidth=0, cursor='hand2',
                  command=self._detach_console).pack(side=tk.RIGHT, padx=3)

        cs_r = ttk.Scrollbar(self.console_right_frame, orient=tk.VERTICAL)
        self.console_right_text = tk.Text(self.console_right_frame, font=('Consolas', 9), wrap=tk.WORD,
                                         bg=c.get('panel_bg', c['entry_bg']), fg=c['fg'],
                                         insertbackground=c['fg'], selectbackground='#264f78',
                                         relief=tk.FLAT, borderwidth=0, yscrollcommand=cs_r.set, padx=6, pady=6)
        cs_r.config(command=self.console_right_text.yview)
        self.console_right_text.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        cs_r.pack(side=tk.RIGHT, fill=tk.Y)

        # Детали (правая, низ)
        self.detail_right_frame = tk.Frame(right_panel, bg=c.get('panel_bg', c['entry_bg']), width=220)
        self.detail_right_frame.pack_propagate(False)
        self.detail_right_frame.pack(side=tk.TOP, fill=tk.BOTH, expand=True, pady=(0, 3))

        dh_r = tk.Frame(self.detail_right_frame, bg=c['title_bg'], height=24)
        dh_r.pack(fill=tk.X)
        dh_r.pack_propagate(False)
        tk.Label(dh_r, text='Детали', bg=c['title_bg'], fg=c['fg'],
                 font=('Segoe UI', 9, 'bold')).pack(side=tk.LEFT, padx=6)
        tk.Button(dh_r, text='\\u2197', font=('Segoe UI', 9),
                  bg=c['title_bg'], fg=c['fg'], relief=tk.FLAT, borderwidth=0, cursor='hand2',
                  command=self._detach_detail).pack(side=tk.RIGHT, padx=3)

        ds_r = ttk.Scrollbar(self.detail_right_frame, orient=tk.VERTICAL)
        self.detail_right_text = tk.Text(self.detail_right_frame, font=('Consolas', 9), wrap=tk.WORD,
                                        bg=c.get('panel_bg', c['entry_bg']), fg=c['fg'],
                                        insertbackground=c['fg'], selectbackground='#264f78',
                                        relief=tk.FLAT, borderwidth=0, yscrollcommand=ds_r.set, padx=6, pady=6)
        ds_r.config(command=self.detail_right_text.yview)
        self.detail_right_text.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        ds_r.pack(side=tk.RIGHT, fill=tk.Y)

        # Синхронизация: detail_text и console_text указывают на левые панели
        self.detail_text = self.detail_left_text
        self.console_text = self.console_left_text

        # Обновление графика
        if HAS_MPL:
            self.graph_frame = tk.Frame(self.main_frame, bg=c['graph_bg'], height=150)
            self.graph_frame.pack(side=tk.BOTTOM, fill=tk.X, pady=(5, 0))
            self.graph_frame.pack_propagate(False)

            self.fig = Figure(figsize=(10, 1.8), dpi=80, facecolor=c['graph_bg'])
            self.ax = self.fig.add_subplot(111)
            self.ax.set_facecolor(c['graph_bg'])
            self.ax.tick_params(colors=c['graph_fg'], labelsize=8)
            for spine in self.ax.spines.values():
                spine.set_color(c['graph_fg'])

            self.canvas = FigureCanvasTkAgg(self.fig, master=self.graph_frame)
            self.canvas.get_tk_widget().pack(fill=tk.BOTH, expand=True)
            self.canvas.get_tk_widget().bind('<Double-Button-1>', self.open_graph_detail)

            self._update_graph()
'''

lines_list = open(path, encoding='utf-8').read().split('\n')
start_idx = None
end_idx = None
for i in range(len(lines_list)):
    if 'def _build_main' in lines_list[i]:
        start_idx = i
    if start_idx is not None and (lines_list[i].startswith('    def ') or lines_list[i].startswith('class ')) and i > start_idx + 5:
        end_idx = i
        break

if start_idx is not None and end_idx is not None:
    lines_list[start_idx:end_idx] = new_method.strip().split('\n')
    open(path, 'w', encoding='utf-8').write('\n'.join(lines_list))
    print("done, replaced lines %d to %d" % (start_idx+1, end_idx))
else:
    print("not found: start=%s end=%s" % (start_idx, end_idx))
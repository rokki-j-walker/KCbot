path = 'C:/Users/joker/CodeProjects/Python/KingdomBot/launcher.py'
lines = open(path, encoding='utf-8').read().split('\n')

# Find _build_main method
start = None
for i in range(len(lines)):
    if 'def _build_main' in lines[i]:
        start = i
        break
end = None
for i in range(start + 1, len(lines)):
    if lines[i].startswith('    def ') or lines[i].startswith('class ') or lines[i].startswith('if __name__'):
        end = i
        break

print("method lines %d to %d" % (start+1, end))

new_method = '''    def _build_main(self):
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

        # Правая панель: подробности
        self.detail_frame = tk.Frame(self.main_frame, bg=c.get('panel_bg', c['entry_bg']), width=300)
        self.detail_frame.pack_propagate(False)
        self.detail_frame.pack(side=tk.RIGHT, fill=tk.Y, padx=(5, 0))

        dh = tk.Frame(self.detail_frame, bg=c['title_bg'], height=26)
        dh.pack(fill=tk.X)
        dh.pack_propagate(False)
        tk.Label(dh, text='Детали', bg=c['title_bg'], fg=c['fg'],
                 font=('Segoe UI', 10, 'bold')).pack(side=tk.LEFT, padx=8)
        tk.Button(dh, text='\\u2197', font=('Segoe UI', 10),
                  bg=c['title_bg'], fg=c['fg'], relief=tk.FLAT, borderwidth=0, cursor='hand2',
                  command=self._detach_detail).pack(side=tk.RIGHT, padx=3)

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

        # Консоль - отдельное окно (по умолчанию)
        if not self.config.get('console_detached', True):
            self.config['console_detached'] = True
            save_config(self.config)

        # График внизу (только если консоль встроена - но она всегда отдельная, так что не показываем)
        # График будет в окне консоли

        # Установить размер окна
        self.root.update_idletasks()
        self.root.minsize(600, 400)
'''

src = open(path, encoding='utf-8').read()
lines_list = src.split('\n')
start_idx = None
end_idx = None
for i in range(len(lines_list)):
    if 'def _build_main' in lines_list[i]:
        start_idx = i
    if start_idx is not None and (lines_list[i].startswith('    def ') or lines_list[i].startswith('class ') or lines_list[i].startswith('if __name__')) and i > start_idx + 5:
        end_idx = i
        break

if start_idx is not None and end_idx is not None:
    lines_list[start_idx:end_idx] = new_method.strip().split('\n')
    open(path, 'w', encoding='utf-8').write('\n'.join(lines_list))
    print("done, replaced lines %d to %d" % (start_idx+1, end_idx))
else:
    print("not found: start=%s end=%s" % (start_idx, end_idx))
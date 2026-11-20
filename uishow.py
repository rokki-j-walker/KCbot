# -*- coding: utf-8 -*-
"""Смоук UI: построить App + окно консоли, снять оба окна изнутри, выйти.

Снимок изнутри процесса надёжен: Tk знает свои физические координаты,
внешние захватчики на двухмониторной/DPI-конфигурации ловили не то окно.
"""
import sys

import customtkinter as ctk

import launcher


def grab(widget_root, path):
    from PIL import ImageGrab
    import time
    # поднять окно выше всех (иначе ImageGrab снимает верхнее окно чужого
    # приложения поверх нашего)
    try:
        widget_root.attributes('-topmost', True)
        widget_root.lift()
        widget_root.focus_force()
    except Exception:
        pass
    widget_root.update_idletasks()
    time.sleep(0.35)
    x, y = widget_root.winfo_rootx(), widget_root.winfo_rooty()
    w, h = widget_root.winfo_width(), widget_root.winfo_height()
    if x >= 0 and y >= 0:
        img = ImageGrab.grab(bbox=(x, y, x + w, y + h), all_screens=False)
    else:
        img = ImageGrab.grab(bbox=(x, y, x + w, y + h), all_screens=True)
    img.save(path)
    try:
        widget_root.attributes('-topmost', False)
    except Exception:
        pass
    print('снято %s: %s (%dx%d)' % (path, img.size, w, h))


def main():
    ctk.set_appearance_mode('dark')
    root = ctk.CTk()
    root.title('KC Bot')
    # 2-й монитор слева (отрицательные X) - пользователь смотрит его
    root.geometry('1400x1050-1500+50')
    app = launcher.App(root)
    root.update()

    # окно консоли (как при открытии кнопкой)
    console = launcher.ConsoleWindow(root, app.colors, detached=True,
                                     content='both')
    for i in range(10):
        root.update()
        import time
        time.sleep(0.1)

    grab(root, 'ui_main.png')
    grab(console.window, 'ui_console.png')

    # кто раздувает reqheight (пустой низ окна)
    root.update_idletasks()
    print('root      h=%d req=%d' % (root.winfo_height(), root.winfo_reqheight()))
    print('title_bar h=%d req=%d' % (app.title_bar.winfo_height(),
                                     app.title_bar.winfo_reqheight()))
    print('main      h=%d req=%d' % (app.main_frame.winfo_height(),
                                     app.main_frame.winfo_reqheight()))
    print('params    h=%d req=%d' % (app.params_frame.winfo_height(),
                                     app.params_frame.winfo_reqheight()))
    if app.detail_frame is not None and app.detail_frame.winfo_exists():
        print('detail    h=%d req=%d' % (app.detail_frame.winfo_height(),
                                         app.detail_frame.winfo_reqheight()))
        if hasattr(app, 'detail_text'):
            print('detail_text req=%d (24 строки Text?)' %
                  app.detail_text.winfo_reqheight())
    for w in app.params_frame.winfo_children():
        try:
            print('  child %-22s req=%d' % (w.winfo_class(), w.winfo_reqheight()))
        except Exception:
            pass

    # валидация: не упала ли в процессе построения/валидации?
    ok = app._validate_all()
    print('валидация:', 'OK' if ok else 'FAIL (поля вне диапазона - ок, если так задано)')
    print('entries:', sorted(app.entries.keys()))
    print('start_btn height/font:', app.start_btn.cget('height'),
          app.start_btn.cget('font').cget('size'))
    root.destroy()


if __name__ == '__main__':
    main()
    sys.exit(0)

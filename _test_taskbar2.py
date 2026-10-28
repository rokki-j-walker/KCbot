import ctypes
import tkinter as tk

root = tk.Tk()
root.geometry('400x300+200+200')
root.title('KC Bot')
root.update()

hwnd = root.winfo_id()
GWL_STYLE = -16
GWL_EXSTYLE = -20
WS_CAPTION = 0x00C00000
WS_THICKFRAME = 0x00040000
WS_EX_APPWINDOW = 0x00040000

style = ctypes.windll.user32.GetWindowLongW(hwnd, GWL_STYLE)
style = style & ~WS_CAPTION & ~WS_THICKFRAME
ctypes.windll.user32.SetWindowLongW(hwnd, GWL_STYLE, style)

ex_style = ctypes.windll.user32.GetWindowLongW(hwnd, GWL_EXSTYLE)
ex_style = ex_style | WS_EX_APPWINDOW
ctypes.windll.user32.SetWindowLongW(hwnd, GWL_EXSTYLE, ex_style)

SWP_FRAMECHANGED = 0x0020
ctypes.windll.user32.SetWindowPos(hwnd, 0, 0, 0, 0, 0, SWP_FRAMECHANGED | 0x0001 | 0x0002 | 0x0004)

root.update()
print("Window should have no title bar but be in taskbar")
root.mainloop()

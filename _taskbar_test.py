import ctypes
import tkinter as tk

# COM interfaces
class ITaskbarList(ctypes.c_void_p):
    pass

CLSID_TaskbarList = ctypes.GUID('{56FDF344-FD6D-11D0-958A-006097C9A090}')
IID_ITaskbarList = ctypes.GUID('{56FDF342-FD6D-11D0-958A-006097C9A090}')

# ITaskbarList vtable
# HrInit, AddTab, DeleteTab, ActivateTab, SetActiveAlt

ole32 = ctypes.windll.ole32
ole32.CoInitialize(None)

taskbar_list = ctypes.c_void_p()
hr = ole32.CoCreateInstance(
    ctypes.byref(CLSID_TaskbarList),
    None,
    1,  # CLSCTX_INPROC_SERVER
    ctypes.byref(IID_ITaskbarList),
    ctypes.byref(taskbar_list)
)
print('CoCreateInstance hr:', hex(hr & 0xFFFFFFFF))
print('taskbar_list:', taskbar_list.value)

if taskbar_list.value:
    # Call HrInit (vtable index 3)
    vtable = ctypes.cast(taskbar_list, ctypes.POINTER(ctypes.c_void_p)).contents
    HrInit = ctypes.CFUNCTYPE(ctypes.HRESULT)(vtable[3])
    hr = HrInit(taskbar_list)
    print('HrInit hr:', hex(hr & 0xFFFFFFFF))

root = tk.Tk()
root.overrideredirect(True)
root.geometry('400x300+200+200')
root.update()

hwnd = root.winfo_id()
print('HWND:', hwnd)

if taskbar_list.value:
    # AddTab (vtable index 4)
    AddTab = ctypes.CFUNCTYPE(ctypes.HRESULT, ctypes.c_void_p, ctypes.c_void_p)(vtable[4])
    hr = AddTab(taskbar_list, hwnd)
    print('AddTab hr:', hex(hr & 0xFFFFFFFF))

    # ActivateTab (vtable index 6)
    ActivateTab = ctypes.CFUNCTYPE(ctypes.HRESULT, ctypes.c_void_p, ctypes.c_void_p)(vtable[6])
    hr = ActivateTab(taskbar_list, hwnd)
    print('ActivateTab hr:', hex(hr & 0xFFFFFFFF))

root.mainloop()
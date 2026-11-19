# -*- coding: utf-8 -*-
"""Путь к установленной игре Kingdom: Classic.

Берётся из настроек лаунчера KC Bot: %APPDATA%/KCbot/settings.json (ключ game_dir).
Переменная окружения KCBOT_GAME_DIR имеет приоритет (удобно для отладки).
"""
import json
import os


def game_dir():
    """Вернуть папку игры или пустую строку, если она не задана."""
    env = (os.environ.get('KCBOT_GAME_DIR') or '').strip()
    if env:
        return env

    path = os.path.join(os.environ.get('APPDATA', ''), 'KCbot', 'settings.json')
    try:
        with open(path, 'r', encoding='utf-8') as f:
            data = json.load(f)
    except (OSError, ValueError):
        return ''
    # settings.json может оказаться не объектом (массив/строка/null)
    if not isinstance(data, dict):
        return ''
    value = data.get('game_dir')
    return value.strip() if isinstance(value, str) else ''


def save_file():
    """Путь к текущему сейву игры (storage_*_AUTO.dat).

    Единственная точка правды: раньше путь был захардкожен в трёх файлах
    (restart.py, human_trainer.py, evo.py) с жёсткой версией storage_v26.
    Имя версии определяется динамически — при выходе новой версии игры
    править ничего не придётся.
    """
    import glob as _glob
    base = os.path.join(
        os.environ.get('USERPROFILE', ''),
        'AppData', 'LocalLow', 'noio', 'Kingdom',
    )
    found = sorted(_glob.glob(os.path.join(base, 'storage_*_AUTO.dat')))
    if found:
        return found[-1]
    # файла ещё нет - возвращаем ожидаемый путь по умолчанию
    return os.path.join(base, 'storage_v26_AUTO.dat')


def settings_path():
    return os.path.join(os.environ.get('APPDATA', ''), 'KCbot', 'settings.json')


def set_game_dir(path):
    """Сохранить папку игры в settings.json.

    Раньше этот файл писал только C#-лаунчер (SettingsStorage.cs), который
    удалён в Фазе 0 - без этой функции папку игры негде было задать, кроме
    переменной окружения KCBOT_GAME_DIR.
    """
    path = (path or '').strip().strip('"')
    data = {}
    try:
        with open(settings_path(), 'r', encoding='utf-8') as f:
            loaded = json.load(f)
        if isinstance(loaded, dict):
            data = loaded
    except (OSError, ValueError):
        data = {}
    data['game_dir'] = path
    d = os.path.dirname(settings_path())
    if d:
        os.makedirs(d, exist_ok=True)
    # атомарно, чтобы оброн не оставил битый JSON
    import kc_common
    kc_common.atomic_json_dump(settings_path(), data, indent=2)
    return path


def _steam_libraries():
    """Пути библиотек Steam: реестр + libraryfolders.vdf."""
    import re
    libs = []
    try:
        import winreg
        for hive, sub in ((winreg.HKEY_CURRENT_USER, r'Software\Valve\Steam'),
                          (winreg.HKEY_LOCAL_MACHINE,
                           r'SOFTWARE\WOW6432Node\Valve\Steam')):
            try:
                with winreg.OpenKey(hive, sub) as k:
                    for name in ('SteamPath', 'InstallPath'):
                        try:
                            v = winreg.QueryValueEx(k, name)[0]
                            if v and v not in libs:
                                libs.append(v)
                        except OSError:
                            pass
            except OSError:
                pass
    except ImportError:
        pass
    # дописываем библиотеки из libraryfolders.vdf (3-я/4-я секции)
    for lib in list(libs):
        vdf = os.path.join(lib, 'steamapps', 'libraryfolders.vdf')
        try:
            with open(vdf, 'r', encoding='utf-8', errors='replace') as f:
                text = f.read()
            for m in re.finditer(r'"path"\s+"([^"]+)"', text):
                p = m.group(1).replace('\\\\', '\\')
                if p not in libs:
                    libs.append(p)
        except OSError:
            pass
    return libs


def autodetect_game_dir():
    """Найти папку игры автоматически. Вернёт '' если не найдено."""
    for lib in _steam_libraries():
        cand = os.path.join(lib, 'steamapps', 'common', 'Kingdom')
        if os.path.isfile(os.path.join(cand, 'Kingdom.exe')):
            return os.path.normpath(cand.replace('/', '\\'))
    # стандартные расположения на случай ручной установки
    for drive in ('C', 'D', 'E'):
        cand = os.path.join(drive + ':\\', 'Games', 'Kingdom')
        if os.path.isfile(os.path.join(cand, 'Kingdom.exe')):
            return os.path.normpath(cand)
    return ''


def require_game_dir():
    """Вернуть папку игры или завершить работу с понятным сообщением."""
    value = game_dir()
    if not value:
        raise SystemExit(
            'Не найдена папка игры. Укажите её в лаунчере KC Bot '
            '(кнопка «Папка игры») или задайте KCBOT_GAME_DIR.'
        )
    return value

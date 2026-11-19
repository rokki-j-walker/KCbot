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
    # файла ещё нет — возвращаем ожидаемый путь по умолчанию
    return os.path.join(base, 'storage_v26_AUTO.dat')


def require_game_dir():
    """Вернуть папку игры или завершить работу с понятным сообщением."""
    value = game_dir()
    if not value:
        raise SystemExit(
            'Не найдена папка игры. Укажите её в лаунчере KC Bot '
            '(кнопка «Папка игры») или задайте KCBOT_GAME_DIR.'
        )
    return value

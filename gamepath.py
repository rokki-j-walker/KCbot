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
    value = data.get('game_dir')
    return value.strip() if isinstance(value, str) else ''


def require_game_dir():
    """Вернуть папку игры или завершить работу с понятным сообщением."""
    value = game_dir()
    if not value:
        raise SystemExit(
            'Не найдена папка игры. Укажите её в лаунчере KC Bot '
            '(кнопка «Папка игры») или задайте KCBOT_GAME_DIR.'
        )
    return value

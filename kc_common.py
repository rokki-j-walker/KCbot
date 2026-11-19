# -*- coding: utf-8 -*-
"""Общие утилиты KCbot.

Сюда выносится код, который раньше был продублирован в 2-4 файлах
(см. план, Фаза 1: find_window x4, путь сейва x3, load_bounties x2,
запись JSON x3). Модуль должен оставаться лёгким: только stdlib, никаких
тяжёлых импортов (bot3/evo), чтобы его можно было безопасно импортировать
из любого скрипта и из frozen-сборки.
"""
import json
import os
import tempfile


def atomic_json_dump(path, obj, indent=2):
    """Записать JSON атомарно: временный файл + os.replace.

    Запись напрямую в целевой файл опасна: обрыв/ошибка в момент dump
    оставляет битый JSON, который потом читатели молча отбрасывают
    (best_genome -> дефолты, история эволюции -> пустой список).
    """
    directory = os.path.dirname(path)
    if directory:
        os.makedirs(directory, exist_ok=True)
    # тот же каталог, чтобы os.replace оставался на одной файловой системе
    fd, tmp = tempfile.mkstemp(prefix='.tmp_', suffix='.json', dir=directory or None)
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as f:
            json.dump(obj, f, ensure_ascii=False, indent=indent)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def read_json(path, default=None):
    """Прочитать JSON, при любой ошибке вернуть default (не падать)."""
    try:
        with open(path, 'r', encoding='utf-8') as f:
            return json.load(f)
    except (OSError, ValueError):
        return default

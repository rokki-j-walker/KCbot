# -*- mode: python ; coding: utf-8 -*-
"""Сборка KCbot.exe (PyInstaller, onedir).

Сборка:  pyinstaller KCbot.spec
Выход:   dist/KCbot/KCbot.exe

ВАЖНО:
  * bot3.py и консольные скрипты лежат в datas (как ФАЙЛЫ), а не только в
    PYZ: evo.default_genome() читает bot3.py через spec_from_file_location,
    т.е. ему нужен файл на диске. Без этого frozen-обучение падало по
    FileNotFoundError (аудит E2).
  * data/ и saves/ в сборку НЕ включаются - это runtime-результаты (геномы,
    история, baseline). Папки создаются самим приложением (os.makedirs),
    а их отсутствие в источниках ломало сборку.
  * upx=False: сжатие UPX вместе с SendInput/taskkill - классический триггер
    антивирусных ложных срабатываний.
"""

RES_DIRS = ['assets', 'locales']
# файлы кладём в корень сборки ('.'); папки - под своим именем
RES_FILES = ['bounties.txt',
             # bot3.py обязателен как файл: evo.default_genome() читает его
             # через spec_from_file_location (аудит E2)
             'bot3.py', 'evo.py', 'converter.py', 'recorder.py',
             'human_trainer.py', 'gamepath.py', 'kc_common.py', 'hold.py',
             'baseline.py']

a = Analysis(
    ['launcher.py'],
    pathex=[],
    binaries=[],
    datas=[(d, d) for d in RES_DIRS] + [(f, '.') for f in RES_FILES],
    # импортируются динамически: launcher.py делает import evo / import bot3
    hiddenimports=['evo', 'bot3', 'kc_common', 'gamepath', 'converter',
                    'recorder', 'human_trainer', 'hold'],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=['matplotlib', 'numpy'],   # график убран (решение D3)
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='KCbot',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=['assets/kc_bot.ico'],
    version=None,            # добавить .rc с версией при выпуске релиза
)
coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name='KCbot',
)

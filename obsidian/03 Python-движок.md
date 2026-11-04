# 03 Python-движок

## Файлы
| Файл | Назначение |
|---|---|
| `bot3.py` | одиночный бот: парсинг `kcdbg.txt`, управление, метрики, оплата, найм |
| `evo.py` | эволюция: популяция, отбор, кроссовер, мутации, human-геномы |
| `converter.py` | анализ записи игрока → геном |
| `recorder.py` | запись состояния игрока |
| `human_trainer.py` | сессии ручного обучения → геномы |
| `restart.py` | мягкий перезапуск игры (WM_CLOSE + запуск) |
| `gamepath.py` | путь к игре из настроек/env |
| `hold.py`, `shot.py` | утилиты (удержание клавиш, скриншот) |

## bot3.py — ключевые методы
- `Input`: `find_window`, `is_focused`, `force_focus`, `down/up/release`.
- `State`/`Payable`: разбор `KC|...`.
- `parse_line`, `read_state` (хвост 256 КБ + очистка >256 МБ).
- `Bot`: `track`, `metrics`, `decide`, `coin_task`, `recruit_task`, `tax_task`, `tree_task`, `execute`, `do_pay`, `finish_pay`, `step`.
- `fitness` — очки эпизода из `bounties.txt`.

## evo.py
- `run_episode` запускает `bot3.py` подпроцессом, читает result JSON.
- `mutate`, `crossover`, `mutation_strength`.
- `score_episode` — версия скоринга для эволюции.
- `--resetAll`, `--baseline`, `--human-genomes`.

## Запуск
```bash
py bot3.py --own-focus --speed 4
py evo.py --pop 12 --rounds 10 --episode 30 --episode-increment 10 --speed 8 --restart
py human_trainer.py --sessions 4 --duration 120
py evo.py --resetAll --game-dir "C:\...\Kingdom"
```

## Связанные заметки
- [[04 C#-движок (KCbotEngine)]]
- [[06 Метрики и скоринг]]
- [[07 Сравнение Python ↔ C#]]

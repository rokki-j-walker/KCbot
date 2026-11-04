# 04 C#-движок (KCbotEngine)

Проект: `CSharp/KCbotEngine` (net8.0, консоль/библиотека). Порт Python-логики.

## Классы
| Класс | Python-аналог | Назначение |
|---|---|---|
| `GamePath` | `gamepath.py` | путь к игре из settings/env |
| `State`, `Payable` | `bot3.State/Payable` | состояние игры |
| `StateParser` | `bot3.parse_line` | разбор строки `KC|...` |
| `Genome` | `bot3`/`evo` | default/mutate/crossover/load/save |
| `Scoring` | `bot3.fitness`, `evo.score_episode` | очки |
| `Bounties` | `bot3.load_bounties` | награды из `bounties.txt` |
| `Converter` | `converter.py` | анализ записи → геном |
| `Recorder` | `recorder.py` | запись |
| `GameRestart` | `restart.py` | перезапуск игры |
| `HumanTrainer` | `human_trainer.py` | ручное обучение |
| `Input` | `bot3.Input` | Win32 `SendInput`, фокус |
| `Bot` | `bot3.Bot` | ИИ короля |
| `Evo` | `evo.py` | эволюция |
| `BotMain` | `bot3.main` | одиночный эпизод |

## Режимы CLI
```bash
KCbotEngine.exe --bot --genome g.json --episode 30 --result r.json --live-result l.json --own-focus --generation 1 --speed 4 --game-dir "..."
KCbotEngine.exe --human-train --sessions 4 --duration 120 --speed 1 --work-dir "..."
KCbotEngine.exe --work-dir "..." --pop 50 --rounds 15 --episode 40 --speed 4 --restart --game-dir "..."
KCbotEngine.exe --resetAll --game-dir "..."
KCbotEngine.exe --baseline --game-dir "..."
```

## Критичные места
- `Input.INPUTUNION` — размер **32 байта** (иначе `SendInput` не работает).
- `Bot.ReadState` — читает только хвост 256 КБ; `MaybeCleanupKcdbg` чистит при >256 МБ.
- `Bot.DoPay` — ждёт реальной остановки (позиция ±0.05) 0.5 с, затем кружки, затем S.
- `Bot.Track` — полный набор метрик (buildings, trees, horse, crown, deaths, citizens).
- `Evo.ParseMetrics` — `JsonElement` → числа (иначе краш при чтении результата).
- `Program.Main` — `Console.OutputEncoding = UTF8` (иначе кракозябры в консоли лаунчера).

## Связанные заметки
- [[03 Python-движок]]
- [[07 Сравнение Python ↔ C#]]
- [[08 Тесты]]

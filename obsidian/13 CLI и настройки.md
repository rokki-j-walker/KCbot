# 13 CLI и настройки

## KCbotEngine.exe — режимы
| Команда | Назначение |
|---|---|
| `--bot --genome g.json --episode N --result r.json --live-result l.json --own-focus --generation K --speed S --game-dir "..."` | один эпизод бота |
| `--human-train --sessions N --duration D --speed S --work-dir "..."` | ручное обучение |
| `--create-baseline --work-dir "..." --game-dir "..."` | создать baseline (новая игра + скип + сейв) |
| `--resetAll --work-dir "..." --game-dir "..."` | удалить всё обучение |
| `--baseline --game-dir "..."` | сохранить текущий сейв как baseline |
| `--diag` | найти окно игры и проверить фокус |
| `--work-dir "..." --pop N --rounds N --episode N --speed S --restart --game-dir "..."` | эволюция |

## Параметры эволюции (`BotParams`)
`Pop`, `Rounds`, `Episode`, `EpisodeIncrement`, `Speed`, `TopPercent`, `EliteWindow`, `CrossoverRate`, `MutStrength`, `MutDecay`, `MutMin`, `Restart`, `UseHuman`.

## Настройки лаунчера
`%APPDATA%\KCbot\settings.json`:
| Ключ | Смысл |
|---|---|
| `game_dir` | папка игры |
| `python_dir` | папка Python-движка / work-dir для C# |
| `language` | `ru` / `en` |
| `appearance_mode` | `System`, `Dark`, `Light`, `Dark Blue`, `Dark Green`, `Light Blue`, `Light Green` |
| `speed` | скорость симуляции |
| `restart` | перезапускать игру между раундами |
| `use_human` | подмешивать ручных ботов |
| `manual_duration`, `manual_sessions`, `manual_speed` | ручное обучение |
| `pop`, `rounds`, `episode`, `episode_increment`, `top_percent`, `elite_window`, `crossover`, `mut_strength`, `mut_decay`, `mut_min` | параметры эволюции |

## Переменные окружения
- `KCBOT_GAME_DIR` — приоритетный путь к игре (перекрывает settings).

## Связанные заметки
- [[04 C#-движок (KCbotEngine)]]
- [[05 Лаунчер (WPF)]]

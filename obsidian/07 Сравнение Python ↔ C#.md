# 07 Сравнение Python ↔ C#

Проверка: все функции Python-бота перенесены в C#.

## bot3.py → Bot.cs
| Python | C# | Статус |
|---|---|---|
| `Input.find_window` | `Input.FindWindow` | ✅ |
| `Input.is_focused/force_focus/down/up/release` | `IsFocused/ForceFocus/Down/Up/Release` | ✅ |
| `Payable`, `State` | `State.cs` | ✅ |
| `parse_line` | `StateParser.ParseLine` | ✅ |
| `read_state` | `Bot.ReadState` (+ tail-read, cleanup) | ✅ |
| `save_genome/load_genome` | `Genome.Save/Load` | ✅ |
| `load_bounties` | `Bounties.Load` | ✅ |
| `fitness` | `Scoring.Fitness` | ✅ |
| `BotLog` | `BotLog` | ✅ |
| `Bot.__init__` | `Bot(...)` | ✅ |
| `now/say/note` | `Now/Say/Note` | ✅ |
| `track` | `Track` | ✅ (полный перенос) |
| `metrics` | `Metrics` | ✅ |
| `stop_move/release_s/stop_all` | `StopMove/ReleaseS/StopAll` | ✅ |
| `set_sprint/move_to/walk_to/safe_sprint` | `SetSprint/MoveTo/WalkTo/SafeSprint` | ✅ |
| `buy_priority/tool_priority` | `BuyPriority/ToolPriority` | ✅ |
| `best_buy/can_afford/blocked` | `BestBuy/CanAfford/Blocked` | ✅ |
| `decide` | `Decide` | ✅ |
| `coin_task/recruit_task/tax_task/tree_task` | `CoinTask/RecruitTask/TaxTask/TreeTask` | ✅ |
| `execute` | `Execute` (+retreat) | ✅ |
| `same_payable/do_pay/pay_timeout/finish_pay` | `SamePayable/DoPay/PayTimeout/FinishPay` | ✅ |
| `ensure_focus/hotkey/step` | `EnsureFocus/Hotkey/Step` | ✅ |
| `main` | `BotMain.Run` | ✅ |

## evo.py → Evo.cs
| Python | C# | Статус |
|---|---|---|
| `run_episode` | `Evo.Run` (подпроцесс `--bot`) | ✅ |
| `mutate/crossover` | `Genome.Mutate/Crossover` | ✅ |
| `mutation_strength` | `Genome.MutationStrength` | ✅ |
| `score_episode` | `Scoring.ScoreEpisode` | ✅ |
| `--resetAll/--baseline` | `Evo.Run` | ✅ |
| `--human-genomes` | `Evo.Run` | ✅ |

## Прочее
| Python | C# | Статус |
|---|---|---|
| `converter.py` | `Converter` | ✅ |
| `recorder.py` | `Recorder` | ✅ |
| `restart.py` | `GameRestart` | ✅ |
| `human_trainer.py` | `HumanTrainer` | ✅ |
| `gamepath.py` | `GamePath` | ✅ |

## Известные отличия
- C# использует `--work-dir` для единой папки данных (data/saves/logs).
- C# авто-очищает `kcdbg.txt` при >256 МБ (в Python тоже добавлено).
- C# хранит настройки скоринга в `bounties.txt` через `Bounties.LoadFrom(WorkDir)`.

## Связанные заметки
- [[03 Python-движок]]
- [[04 C#-движок (KCbotEngine)]]
- [[08 Тесты]]

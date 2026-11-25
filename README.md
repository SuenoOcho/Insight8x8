# Insight8x8

Insight8x8 is your personal AI-powered chess analysis and training companion. Explore your Chess.com games using just your username, uncover mistakes with local Stockfish analysis, and turn recurring errors into personalised lessons and exercises.

## What it does

- **Username-first onboarding** — paste any public Chess.com username; no password, API key, or account linking is required.
- **Complete public archive** — monthly archives are downloaded in parallel, normalized, and cached locally for fast repeat visits.
- **Move-by-move replay** — the board opens from your side, with **My side** to restore that view, free flip, automatic playback, captured material, and a chess.com-style evaluation bar.
- **Stockfish analysis** — evaluate every position locally with configurable search depth and time per move.
- **Plain-language explanations** — explanations appear automatically as you step through moves: the stronger move, what it attacks, why castling may be delayed, and a highlighted threat line on the board instead of raw centipawn tables.
- **Chess intelligence** — detect tactical motifs (forks, pins, skewers, sacrifices, promotions, discovered checks), annotate game phases, and aggregate recurring weaknesses across analysed games.
- **Personal coaching** — build lessons and exercises from your own mistakes, get a plain-language reason why the engine move is better, and track accuracy over time.
- **Optional LLM adapter** — coaching is local by default; an OpenAI-compatible endpoint can enrich the automatic explanations and recognize opening/book moves so standard openings are not punished.
- **Openings and patterns** — see rating progression, results, activity, time-control mix, frequent opponents, and opening win rates. Click an opening or opponent to filter the match history instantly.
- **Compare players** — check another public Chess.com account and review your direct encounters.
- **Progress and exports** — view training charts, then export PGN, match-history CSV, favorites, or a complete training report as CSV.

## Quick start

```bash
# 1. Install the GUI dependency (python-chess is vendored in vendor/chess)
pip install -r requirements.txt

# 2. Launch Insight8x8
python3 insight8x8.py
```

Complete the first-run welcome dialog, enter a Chess.com username on the landing page, and choose **Analyze my games**. The first load can take a minute for a large archive; later visits use the local cache.

Stockfish is optional but required for engine analysis. If it is not on your `PATH`, select it in the welcome dialog or open **Engine settings** in the dashboard sidebar (or press `Ctrl+,`). You can also set `STOCKFISH_PATH` before launching the app.

## Training workflow

1. Open a game from **Match history** and choose **Analyze all moves**.
2. Analysed games are saved automatically to the local database and become coaching input.
3. Open the **Training** tab and choose **Build coaching**.
4. Review personalised lessons, answer exercises, and read the explanation after each answer.
5. Open **Progress** to see session accuracy, trends, focus areas, and best streaks.
6. Download the **Training report** from the Export center or Progress tab.

## Running the tests

```bash
python3 -m unittest discover -v -p 'test_*.py'
```

The suite uses an offscreen Qt platform and a temporary SQLite database; it does not call Chess.com. It also contains a fake UCI engine so the full analysis pipeline can be tested without installing Stockfish.

## Keyboard shortcuts

| Shortcut | Action |
| --- | --- |
| `Ctrl+1` … `Ctrl+8` | Switch between all eight sections; the sidebar shows five primary sections and groups the rest under **More tools** |
| `Ctrl+F` | Focus the match-history search field |
| `F5` | Refresh Chess.com archives |
| `Ctrl+,` | Open Engine settings |
| `Enter` | Connect a username / replay the selected game |
| `←` / `→` / `Space` (in replay) | Previous move / next move / play or pause |

Right-click a game in Match history to analyze it, toggle its favorite state, or copy its Chess.com link.

## Analysis output

For each analyzed move, Insight8x8 reports:

1. The move played, its rating-aware verdict, and the engine's best move.
2. An automatic explanation that names the attacked piece, delayed castling, central control, or the follow-up threat.
3. Green and gold arrows drawn on the board for the stronger move and its continuation.
4. The evaluation bar beside the board, always oriented to the side you play.
5. Tactical motifs and opening/book notes when the move is recognized as theory.

Raw evaluations, candidate lists, and tactical opportunity details remain in the stored analysis and training reports, but the replay panel intentionally avoids centipawn tables.

Chess.com accuracy for both White and Black is shown before analysis starts. Insight8x8 never invents an evaluation: if Stockfish is not configured, the analysis panel explains how to enable it instead of showing fabricated scores.

## Configuration

| Setting | Where | Default |
| --- | --- | --- |
| Stockfish executable | Welcome dialog, Engine settings, or `STOCKFISH_PATH` | Detected from `PATH` |
| Search depth | Engine settings | 12 |
| Time per position | Engine settings | 150 ms |
| Training focus | Welcome dialog / Training | Balanced |
| Engine startup timeout | `STOCKFISH_STARTUP_TIMEOUT` | 5 s |
| Engine response timeout | `STOCKFISH_RESPONSE_TIMEOUT` | 10 s |
| Engine stop timeout | `STOCKFISH_STOP_TIMEOUT` | 2 s |
| LLM chat endpoint | `.env`, `INSIGHT8X8_LLM_URL`, or welcome dialog | Groq auto-detected for `gsk_` keys; otherwise disabled |
| LLM model | `.env`, `INSIGHT8X8_LLM_MODEL`, or welcome dialog | `openai/gpt-oss-120b` for Groq; otherwise `gpt-4o-mini` |
| LLM API key | `.env` or `INSIGHT8X8_LLM_KEY` (recommended) | Disabled |
| LLM request timeout | `INSIGHT8X8_LLM_TIMEOUT` | 8 s |
| Local env file | `.env` or `INSIGHT8X8_ENV_FILE` | `.env` in the project directory |
| Local database | `INSIGHT8X8_DB` | `data/insight8x8.db` |
| Chess.com user agent | `CHESSCOM_USER_AGENT` | `Insight8x8/1.0 (...)` |
| Chess.com request interval | `CHESSCOM_MIN_INTERVAL` | Built-in rate limit |
| Chess.com workers | `CHESSCOM_MAX_WORKERS` | Built-in parallel limit |

Settings such as the last username, engine path, depth, movetime, LLM endpoint, and LLM model are stored with `QSettings`; cached games, favorites, analyses, and training sessions are stored in the local SQLite database. API keys are never written to `QSettings`, the SQLite database, or logs.

### Optional LLM coaching

Local coaching is always available and never needs the network. For Groq, the endpoint and model are auto-detected from a `gsk_` key; a minimal `.env` is enough:

```dotenv
INSIGHT8X8_LLM_KEY=your-groq-api-key
```

`.env`, `.env.*`, `*.env`, `*.key`, `*.pem`, and `secrets/` are ignored by `.gitignore`. Never put a real key in README, source files, tests, or commit messages. If you prefer environment variables, export them in your shell instead:

```bash
export INSIGHT8X8_LLM_URL="https://api.groq.com/openai/v1/chat/completions"
export INSIGHT8X8_LLM_MODEL="openai/gpt-oss-120b"
export INSIGHT8X8_LLM_KEY="your-groq-api-key"
```

The adapter is only activated when both a URL and an API key are present. It enriches the automatic "What just happened" panel and the opening-book check after analysis. If the request fails, Insight8x8 silently falls back to local lessons and local opening heuristics; the error text includes the HTTP status for debugging but never the key. The API key entered in the welcome dialog is kept for that session only; `.env` or `INSIGHT8X8_LLM_KEY` is the persistent option.

## Project structure

```text
insight8x8.py        Main PyQt6 application, pages, onboarding, training, exports, orchestration
viewers.py           Game replay, evaluation bar, automatic explanations, threat arrows, and analysis display
intelligence.py      Rating-aware classifications, motifs, positional explanations, weaknesses
coaching.py          Local coaching, LLM adapter, progress tracking, training reports
ui_widgets.py        Board, charts, stat cards, avatars, shared drawing helpers
engine.py            Stockfish UCI wrapper, candidate and opportunity detection
chesscom.py          Public Chess.com API client, PGN parsing, normalization, caching
store.py             SQLite cache, favorites, saved analyses, training history
workers.py           Background QThread workers
utils.py             Formatting and filter helpers
test_insight8x8.py   Unit and GUI smoke tests
vendor/chess/        Vendored python-chess (see vendor/CHESS_LICENSE.txt)
data/                Local SQLite cache (generated at runtime)
.env                 Local API keys (gitignored, never committed)
```

## Privacy

Only public Chess.com profile, statistics, and game archive data is requested. No password is collected. Analysis, coaching, and progress tracking run locally through Stockfish and SQLite. Favorites, settings, cached archives, and training history stay on your machine. If you explicitly configure the optional LLM adapter, lesson text prompts are sent to that endpoint; no credentials are logged or written to `QSettings` or the local database. Keep keys in a gitignored `.env` file or shell environment.

## Troubleshooting

- **"Stockfish is not configured"** — install Stockfish or select its executable in Engine settings.
- **"No lessons yet"** — analyse at least one game, then choose **Build coaching** on the Training tab.
- **LLM coaching not active** — set `INSIGHT8X8_LLM_KEY` (a `gsk_` key auto-selects Groq) or set both `INSIGHT8X8_LLM_URL` and `INSIGHT8X8_LLM_KEY`; failed requests fall back to local lessons.
- **Archive warnings / offline mode** — Insight8x8 keeps previously cached games available and shows a retry hint; press `F5` when the connection returns.
- **Large archive is slow** — lower the analysis depth or time per position in Engine settings; archive loading itself is cached after the first run.
- **No games match filters** — use **Clear filters** to reset search, dates, sorting, and pagination.

## License

Third-party `python-chess` is vendored under its terms in `vendor/CHESS_LICENSE.txt`. Stockfish is not bundled; install it separately if you want local engine analysis.

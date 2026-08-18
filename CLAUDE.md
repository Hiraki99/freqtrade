# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project overview

Freqtrade is a free, open-source crypto trading bot written in Python (>=3.11). It supports
backtesting, hyperparameter optimization, machine learning (FreqAI), plotting, and live/dry-run
trading on many exchanges via [ccxt](https://github.com/ccxt/ccxt). It is controlled via CLI,
Telegram, a REST API, and a web UI.

The bot is a single CLI entrypoint (`freqtrade.main:main`) that dispatches to subcommands. Users
write their own **strategies** (subclasses of `IStrategy`) that live outside the package in
`user_data/strategies/` and are loaded dynamically at runtime.

## Commands

Development happens in the `develop` branch (PRs target `develop`, never `stable`).

```bash
# Run the full test suite (uses pytest-xdist; addopts apply automatically from pyproject.toml)
pytest

# Run a single test file / single test
pytest tests/test_<file_name>.py
pytest tests/test_<file_name>.py::test_<method_name>

# Faster local runs: disable xdist distribution / random order when debugging a single test
pytest -p no:randomly -n0 tests/test_misc.py::test_something

# Lint + format (must pass CI)
ruff check .
ruff format .

# Type-check
mypy freqtrade

# Run all pre-commit hooks across the repo (ruff, mypy, codespell, json-schema sync, zizmor, ...)
pre-commit run -a
pre-commit install   # install the git hook so checks run on every commit

# Run the bot itself (subcommands required — there is no default action)
freqtrade trade --config config.json --strategy MyStrategy
freqtrade backtesting --strategy MyStrategy --timerange 20240101-20240201
freqtrade hyperopt --strategy MyStrategy --hyperopt-loss SharpeHyperOptLoss
freqtrade download-data --pairs BTC/USDT --timeframe 5m
freqtrade --help     # list all subcommands
```

Install dev environment with `./setup.sh -i` (or `pip install -e ".[dev]"`). Optional dependency
groups are defined in `pyproject.toml`: `plot`, `hyperopt`, `freqai`, `freqai_rl`, `jupyter`,
`develop`, and the meta-groups `all` and `dev`.

## Architecture

### Entrypoint and command dispatch
- `freqtrade/main.py` → `freqtrade/commands/`. `Arguments` (argparse) parses the subcommand and
  binds it to a `func` handler in one of the `*_commands.py` modules (e.g. `trade_commands.py`,
  `optimize_commands.py`, `data_commands.py`). Each subcommand handler builds a `Configuration`
  and instantiates the relevant engine.
- `freqtrade/configuration/` merges config JSON files, CLI args, and environment variables into a
  single `Config` dict. The config is validated against a JSON schema. **Important:** the schema in
  `freqtrade/config_schema/` is generated from the source — the `extract-config-json-schema`
  pre-commit hook regenerates it and fails if it drifts; never hand-edit the generated schema.

### The trading loop
- `freqtrade/freqtradebot.py` (`FreqtradeBot`) is the live/dry-run engine. `freqtrade/worker.py`
  drives its main loop. It orchestrates the strategy, exchange, wallets, pairlists, protections,
  persistence, and RPC. This is the central class that ties most subsystems together.

### Dynamic loading via resolvers
- Strategies, hyperopt-loss functions, pairlists, protections, FreqAI models, and exchanges are
  user-pluggable and loaded by name at runtime. `freqtrade/resolvers/` (`IResolver` subclasses)
  scan directories, import modules, and instantiate the matching class. When adding a new pluggable
  type, follow the existing resolver pattern.

### Strategy interface
- `freqtrade/strategy/interface.py` defines `IStrategy`, the abstract base every user strategy
  subclasses. Key hooks: `populate_indicators`, `populate_entry_trend`, `populate_exit_trend`, plus
  numerous callbacks (`custom_stoploss`, `custom_exit`, `confirm_trade_entry`, etc.). Hyperoptable
  parameters come from `freqtrade/strategy/parameters.py` / `hyper.py`. Example/templates live in
  `freqtrade/templates/`.

### Backtesting and optimization
- `freqtrade/optimize/backtesting.py` replays historical data through a strategy. `optimize/hyperopt/`
  wraps Optuna for parameter search; `optimize/hyperopt_loss/` holds the objective functions.
  `optimize/analysis/` covers lookahead- and recursive-bias analysis. Backtesting must produce
  results consistent with live trading — preserve that parity when touching either path.

### Exchange layer
- `freqtrade/exchange/exchange.py` is the generic ccxt wrapper; per-exchange subclasses
  (`binance.py`, `bybit.py`, `kraken.py`, ...) override quirks. Spot and futures/leverage behaviors
  differ — see `freqtrade/leverage/`. The `ExchangeResolver` picks the right subclass.

### Persistence
- `freqtrade/persistence/` uses SQLAlchemy 2.0. `Trade`/`Order` models (`trade_model.py`,
  `models.py`) are the core domain objects; `migrations.py` handles schema migrations. The mypy
  config enables the `sqlalchemy.ext.mypy.plugin`.

### Data, FreqAI, plugins, RPC
- `freqtrade/data/`: the `DataProvider` feeds candle/orderbook data to strategies; `history/` loads
  and stores OHLCV data.
- `freqtrade/freqai/`: machine-learning pipeline (`freqai_interface.py`, `data_kitchen.py`,
  `data_drawer.py`, prediction models, and RL under `RL/`). Optional, heavy dependencies.
- `freqtrade/plugins/`: pairlist handlers (`pairlist/`) and protections (`protections/`), each with
  a manager and resolver.
- `freqtrade/rpc/`: `RPCManager` fans messages out to Telegram (`telegram.py`), Discord, webhooks,
  and the FastAPI REST/websocket server (`rpc/api_server/`). `ft_client/` is a separate, installable
  REST API client package (`freqtrade-client`) with its own `pyproject.toml` and tests.

## Conventions

- **Line length is 100** (ruff). Ruff handles linting + formatting; the active rule selection lives
  in `pyproject.toml` under `[tool.ruff.lint]`.
- Docstrings: double-quoted, reST format (`:param x:`, `:return:`, `:raises X:`) on public methods.
- English only in code, comments, commit messages, and PRs.
- New features must include unit tests and pass CI (`pre-commit run -a` + `pytest`).
- Tests mirror the package layout under `tests/`; shared fixtures live in `tests/conftest.py` and
  the `conftest_*.py` helpers. Tests run with `pytest-xdist` (`--dist loadscope`), random ordering,
  and `asyncio_mode = "auto"`.
- If using AI to generate a PR, disclose it in the PR description and review the output yourself —
  responsibility for the code lies with the human author.

<!-- gitnexus:start -->
# GitNexus — Code Intelligence

This project is indexed by GitNexus as **freqtrade** (10496 symbols, 27507 relationships, 300 execution flows). Use the GitNexus MCP tools to understand code, assess impact, and navigate safely.

> Index stale? Run `node .gitnexus/run.cjs analyze` from the project root — it auto-selects an available runner. No `.gitnexus/run.cjs` yet? `npx gitnexus analyze` (npm 11 crash → `npm i -g gitnexus`; #1939).

## Always Do

- **MUST run impact analysis before editing any symbol.** Before modifying a function, class, or method, run `impact({target: "symbolName", direction: "upstream"})` and report the blast radius (direct callers, affected processes, risk level) to the user.
- **MUST run `detect_changes()` before committing** to verify your changes only affect expected symbols and execution flows. For regression review, compare against the default branch: `detect_changes({scope: "compare", base_ref: "develop"})`.
- **MUST warn the user** if impact analysis returns HIGH or CRITICAL risk before proceeding with edits.
- When exploring unfamiliar code, use `query({search_query: "concept"})` to find execution flows instead of grepping. It returns process-grouped results ranked by relevance.
- When you need full context on a specific symbol — callers, callees, which execution flows it participates in — use `context({name: "symbolName"})`.
- For security review, `explain({target: "fileOrSymbol"})` lists taint findings (source→sink flows; needs `analyze --pdg`).

## Never Do

- NEVER edit a function, class, or method without first running `impact` on it.
- NEVER ignore HIGH or CRITICAL risk warnings from impact analysis.
- NEVER rename symbols with find-and-replace — use `rename` which understands the call graph.
- NEVER commit changes without running `detect_changes()` to check affected scope.

## Resources

| Resource | Use for |
|----------|---------|
| `gitnexus://repo/freqtrade/context` | Codebase overview, check index freshness |
| `gitnexus://repo/freqtrade/clusters` | All functional areas |
| `gitnexus://repo/freqtrade/processes` | All execution flows |
| `gitnexus://repo/freqtrade/process/{name}` | Step-by-step execution trace |

## CLI

| Task | Read this skill file |
|------|---------------------|
| Understand architecture / "How does X work?" | `.claude/skills/gitnexus/gitnexus-exploring/SKILL.md` |
| Blast radius / "What breaks if I change X?" | `.claude/skills/gitnexus/gitnexus-impact-analysis/SKILL.md` |
| Trace bugs / "Why is X failing?" | `.claude/skills/gitnexus/gitnexus-debugging/SKILL.md` |
| Rename / extract / split / refactor | `.claude/skills/gitnexus/gitnexus-refactoring/SKILL.md` |
| Tools, resources, schema reference | `.claude/skills/gitnexus/gitnexus-guide/SKILL.md` |
| Index, status, clean, wiki CLI commands | `.claude/skills/gitnexus/gitnexus-cli/SKILL.md` |

<!-- gitnexus:end -->

#!/usr/bin/env bash
# Dựng TOÀN BỘ môi trường để chạy project, từ một bản `git clone` sạch cho tới lúc
# ./run_smc.sh chạy được. Chạy lại nhiều lần vô hại (idempotent): bước nào xong rồi thì
# báo "đã có" và bỏ qua.
#
# Khác ./setup.sh (bản upstream của freqtrade): script đó hỏi tương tác từng nhóm phụ
# thuộc và chỉ lo phần thư viện. Script này KHÔNG hỏi gì, và làm nốt những thứ riêng của
# repo mà thiếu chúng thì bot chết lúc khởi động: .env, thư mục user_data/, symlink
# strategy, rồi kiểm chứng lại bằng cách nạp thật config + strategy.
#
# Usage:
#   ./setup_env.sh              # dựng đủ để CHẠY BOT (mặc định, kèm phụ thuộc hyperopt)
#   ./setup_env.sh --minimal    # chỉ requirements.txt — đủ trade/backtest, không hyperopt
#   ./setup_env.sh --dev        # + pytest/ruff/mypy/freqai + pre-commit hook
#   ./setup_env.sh --check      # CHỈ kiểm tra, không cài và không sửa gì
#   ./setup_env.sh --recreate   # xoá .venv rồi dựng lại từ đầu
#   ./setup_env.sh --no-claude  # bỏ qua bước cài Claude Code CLI
#
# KHÔNG bao giờ đụng vào: .env đã có, config*.json, database *.sqlite, user_data/.
set -euo pipefail
cd "$(dirname "$0")"

# .venv là đường dẫn CỐ ĐỊNH mà run_smc.sh/run_smc_5.sh/try_strategy.sh trông vào. FT_VENV
# chỉ để thử script này ở một venv nháp mà không đụng venv thật — đổi nó thì các script run_*
# vẫn tìm .venv như cũ.
VENV="${FT_VENV:-.venv}"
PY_MINORS=(14 13 12 11)          # thứ tự ưu tiên, giống SUPPORTED_MINOR_VERS trong setup.sh
MODE="run"                       # run | minimal | dev
CHECK=0
RECREATE=0
NO_CLAUDE=0
FAILED=0

for arg in "$@"; do
    case "$arg" in
        --minimal)  MODE="minimal" ;;
        --dev)      MODE="dev" ;;
        --check)    CHECK=1 ;;
        --recreate) RECREATE=1 ;;
        --no-claude) NO_CLAUDE=1 ;;
        -h|--help)  sed -n '2,19p' "$0"; exit 0 ;;
        *) echo "Tham số không hợp lệ: $arg (xem --help)" >&2; exit 2 ;;
    esac
done

# --- Tiện ích hiển thị -------------------------------------------------------
step() { echo; echo "── $* ─────────────────────────────────────────" ; }
ok()   { echo "   ✅ $*" ; }
warn() { echo "   ⚠  $*" ; }
bad()  { echo "   ✗  $*" ; FAILED=1 ; }
die()  { echo "✗ $*" >&2; exit 1 ; }

# --- 1. Trình thông dịch Python ---------------------------------------------
# Shell đang activate sẵn một venv là chuyện rất thường (direnv, .zshrc, hoặc vừa
# `source .venv/bin/activate`). Để nguyên thì hỏng hai chỗ, cả hai đều im lặng:
#   · `command -v python3.13` trả về python CỦA venv đó -> dựng venv chồng lên venv;
#   · uv đọc $VIRTUAL_ENV và cài vào venv ĐANG ACTIVE, không phải venv ta vừa tạo —
#     script báo "đã cài xong" trong khi .venv của repo vẫn rỗng.
# Nên: dò python bằng PATH đã lọc, và mọi lệnh cài đặt đều chỉ đích danh interpreter.
clean_path() {
    local p out="" parts
    IFS=':' read -ra parts <<< "$PATH"
    for p in "${parts[@]}"; do
        [[ -n "${VIRTUAL_ENV:-}" && "$p" == "${VIRTUAL_ENV%/}/bin" ]] && continue
        [[ "$p" == "$PWD/$VENV/bin" || "$p" == "$PWD/.venv/bin" ]] && continue
        out+="${out:+:}$p"
    done
    printf '%s' "$out"
}

# pyproject yêu cầu >=3.11. Ưu tiên bản mới nhất có sẵn, KHÔNG dùng `python3` trống:
# trên macOS nó có thể là 3.9 của hệ thống và pip sẽ dựng nửa chừng rồi mới báo lỗi.
pick_python() {
    if [[ -x "$VENV/bin/python" && $RECREATE -eq 0 ]]; then
        PYTHON="$VENV/bin/python"
        return
    fi
    local v found
    for v in "${PY_MINORS[@]}"; do
        found="$(PATH="$(clean_path)" command -v "python3.$v" 2>/dev/null || true)"
        if [[ -n "$found" ]]; then
            PYTHON="$found"
            return
        fi
    done
    die "không tìm thấy Python 3.11+ ngoài virtualenv. macOS: brew install python@3.13"
}

# --- 2. Virtualenv -----------------------------------------------------------
# uv nhanh hơn pip rất nhiều (giây thay vì phút) nên dùng khi có; không có thì venv+pip
# vẫn ra kết quả y hệt.
ensure_venv() {
    step "Virtualenv ($VENV)"
    if [[ $RECREATE -eq 1 && -d "$VENV" ]]; then
        echo "   --recreate → xoá $VENV cũ"
        rm -rf "$VENV"
    fi
    if [[ -x "$VENV/bin/python" ]]; then
        ok "đã có: $("$VENV/bin/python" -V)"
        return
    fi
    [[ $CHECK -eq 1 ]] && { bad "chưa có $VENV"; return; }

    local base
    base="$PYTHON"
    [[ "$base" == "$VENV/bin/python" ]] && die "logic sai: chưa có venv mà lại lấy python trong venv"
    echo "   tạo bằng $("$base" -V)"
    if command -v uv >/dev/null 2>&1; then
        uv venv --python "$base" "$VENV" >/dev/null
    else
        "$base" -m venv "$VENV"
    fi
    ok "$("$VENV/bin/python" -V)"
}

# --- 3. Thư viện -------------------------------------------------------------
install_deps() {
    step "Thư viện Python (chế độ: $MODE)"
    [[ $CHECK -eq 1 ]] && { check_import; return; }

    local -a reqs=(-r requirements.txt)
    case "$MODE" in
        run)     reqs+=(-r requirements-hyperopt.txt) ;;   # ./run_smc.sh hyperopt cần scikit-learn/optuna
        dev)     reqs=(-r requirements-dev.txt) ;;         # đã include mọi nhóm còn lại
        minimal) ;;
    esac

    if command -v uv >/dev/null 2>&1; then
        echo "   dùng uv"
        # --python + `env -u VIRTUAL_ENV`: chỉ đích danh venv đích. Chỉ đặt VIRTUAL_ENV
        # là KHÔNG đủ — nếu shell đang activate venv khác, uv cài nhầm vào đó và không
        # báo gì (đã dính khi test script này).
        env -u VIRTUAL_ENV uv pip install --quiet --python "$VENV/bin/python" "${reqs[@]}"
        env -u VIRTUAL_ENV uv pip install --quiet --python "$VENV/bin/python" -e .
    else
        echo "   dùng pip (cài uv sẽ nhanh hơn nhiều: brew install uv)"
        "$VENV/bin/python" -m pip install --quiet --upgrade pip wheel setuptools
        "$VENV/bin/python" -m pip install --quiet "${reqs[@]}"
        "$VENV/bin/python" -m pip install --quiet -e .
    fi
    ok "đã cài freqtrade + phụ thuộc"

    if [[ "$MODE" == "dev" ]]; then
        "$VENV/bin/python" -m pre_commit install >/dev/null && ok "pre-commit hook đã cài"
    fi
}

# TA-Lib là phần hay hỏng nhất: nó là extension C. Bản wheel từ 0.6 trở đi đã gói sẵn
# libta-lib bên trong (@loader_path/.dylibs) nên KHÔNG cần `brew install ta-lib`; chỉ khi
# pip phải tự build từ source mới cần. Kiểm tra bằng cách import thật, không đọc pip list.
check_import() {
    local out
    if out="$("$VENV/bin/python" -c 'import talib, freqtrade; print(talib.__version__, freqtrade.__version__)' 2>&1)"; then
        ok "import được: talib ${out%% *} · freqtrade ${out##* }"
    else
        bad "không import được talib/freqtrade — chạy lại không kèm --check"
        echo "      nếu lỗi ở talib và pip phải build từ source: brew install ta-lib"
    fi
}

# --- 4. .env -----------------------------------------------------------------
# run_smc.sh `source .env` để lấy token Telegram. Thiếu file thì bot vẫn chạy (script tự
# tắt Telegram) nhưng sẽ không có báo cáo /analysis — nên tạo sẵn từ template.
ensure_env() {
    step ".env"
    if [[ -f .env ]]; then
        ok "đã có (không đụng vào)"
    elif [[ $CHECK -eq 1 ]]; then
        bad "chưa có .env"
        return
    else
        cp .env.example .env
        ok "tạo mới từ .env.example"
    fi

    # Đọc trong subshell: `set -a; source .env` sẽ export đè lên môi trường của chính
    # script này, và FREQTRADE__* bị export là freqtrade sẽ nuốt luôn lúc verify.
    local token chat
    token="$(bash -c 'set -a; source .env 2>/dev/null; echo "${FREQTRADE__TELEGRAM__TOKEN:-}"')"
    chat="$(bash -c 'set -a; source .env 2>/dev/null; echo "${FREQTRADE__TELEGRAM__CHAT_ID:-}"')"
    if [[ -n "$token" && -n "$chat" ]]; then
        ok "token + chat_id Telegram đã điền"
    else
        warn "FREQTRADE__TELEGRAM__TOKEN/CHAT_ID còn trống → bot chạy được nhưng TẮT Telegram"
        echo "      lấy token ở @BotFather rồi điền vào .env"
    fi
}

# --- 5. Thư mục làm việc -----------------------------------------------------
# freqtrade tự tạo phần lớn, nhưng logfile thì KHÔNG: --logfile trỏ vào thư mục chưa tồn
# tại là bot chết ngay lúc khởi động.
ensure_dirs() {
    step "Thư mục user_data/"
    local d missing=0
    for d in logs data strategies backtest_results hyperopt_results plot notebooks freqaimodels hyperopts; do
        if [[ -d "user_data/$d" ]]; then continue; fi
        if [[ $CHECK -eq 1 ]]; then missing=1; continue; fi
        mkdir -p "user_data/$d"
    done
    if [[ $CHECK -eq 1 && $missing -eq 1 ]]; then
        bad "thiếu thư mục trong user_data/"
    else
        ok "đủ 9 thư mục"
    fi
}

# --- 6. Strategy -------------------------------------------------------------
# strategies/ (git track) -> symlink vào user_data/strategies/. Các script run_* đều
# truyền --strategy-path strategies nên bước này KHÔNG bắt buộc để chạy bot; nó cần cho
# freqtrade gọi tay và cho FreqUI thấy đủ danh sách.
ensure_strategies() {
    step "Symlink strategy"
    [[ -x ./sync_strategies.sh ]] || { warn "không có ./sync_strategies.sh → bỏ qua"; return; }
    if [[ $CHECK -eq 1 ]]; then
        ./sync_strategies.sh --check >/dev/null 2>&1 && ok "symlink khớp" || warn "symlink lệch → chạy ./sync_strategies.sh"
        return
    fi
    ./sync_strategies.sh >/dev/null
    ok "đã đồng bộ từ strategies/"
}

# --- 7. Claude Code CLI ------------------------------------------------------
# KHÔNG cần để chạy bot — đây là công cụ dev, nên thiếu nó chỉ cảnh báo chứ không
# tính là hỏng. Dùng native installer (curl | bash) chứ KHÔNG dùng npm: máy này đang
# cài kiểu native (~/.local/bin/claude -> ~/.local/share/claude/versions/<ver>), và bản
# native tự cập nhật + không phụ thuộc phiên bản Node của nvm. Muốn bản npm thì:
#     npm install -g @anthropic-ai/claude-code
# Đã cài rồi thì KHÔNG chạy lại installer — nâng cấp bằng `claude update`.
ensure_claude() {
    step "Claude Code CLI"
    if [[ $NO_CLAUDE -eq 1 ]]; then
        echo "   --no-claude → bỏ qua"
        return
    fi
    if command -v claude >/dev/null 2>&1; then
        ok "đã có: $(claude --version 2>/dev/null || echo 'không đọc được version') ($(command -v claude))"
        echo "      nâng cấp: claude update"
        return
    fi
    if [[ $CHECK -eq 1 ]]; then
        warn "chưa cài claude → chạy ./setup_env.sh (không kèm --check) hoặc bỏ qua bằng --no-claude"
        return
    fi
    echo "   tải installer chính chủ: https://claude.ai/install.sh"
    if curl -fsSL https://claude.ai/install.sh | bash; then
        # Installer đặt binary vào ~/.local/bin — thư mục này KHÔNG mặc định nằm trong PATH
        # của mọi shell, nên báo rõ thay vì để lệnh `claude` "không tìm thấy" sau khi cài xong.
        export PATH="$HOME/.local/bin:$PATH"
        if command -v claude >/dev/null 2>&1; then
            ok "$(claude --version 2>/dev/null || echo 'đã cài')"
            case ":${PATH}:" in
                *":$HOME/.local/bin:"*) ;;
                *) warn 'thêm vào ~/.zshrc: export PATH="$HOME/.local/bin:$PATH"' ;;
            esac
        else
            warn 'cài xong nhưng chưa thấy lệnh — thêm vào ~/.zshrc: export PATH="$HOME/.local/bin:$PATH"'
        fi
    else
        warn "cài Claude Code thất bại (không chặn phần còn lại) — xem https://code.claude.com/docs"
    fi
}

# --- 8. Kiểm chứng thật ------------------------------------------------------
# Không tin vào "đã cài xong" — nạp đúng thứ mà ./run_smc.sh sẽ nạp. Đây là chỗ bắt được
# lỗi config sai schema hay strategy import hỏng, TRƯỚC khi bot chạy thật.
verify() {
    step "Kiểm chứng"
    local ft="$VENV/bin/freqtrade"
    [[ -x "$ft" ]] || { bad "chưa có $ft"; return; }
    # `--version` in ra 5 dòng (OS, Python, CCXT, rỗng, Freqtrade) — lấy đúng dòng cuối,
    # head -1 sẽ ra tên hệ điều hành.
    ok "$("$ft" --version 2>&1 | grep -i "^Freqtrade Version" | tr -s '\t' ' ')"
    check_import

    if "$ft" show-config -c config.json -c config-4h.json >/dev/null 2>&1; then
        ok "config.json + config-4h.json hợp lệ theo schema"
    else
        bad "config không hợp lệ — chạy: $ft show-config -c config.json -c config-4h.json"
    fi

    # Nạp strategy y hệt run_smc.sh (--strategy-path strategies) và in ra ĐƯỜNG DẪN THẬT.
    # Quan trọng vì user_data/strategies/ cũng có bản sao: nạp nhầm bên đó thì bot chạy
    # bằng file khác với file đang sửa, và còn nuốt luôn tham số hyperopt .json nằm cạnh.
    "$VENV/bin/python" - <<'PY' || bad "không nạp được SmcElliottStrategy"
import logging, sys
logging.disable(logging.WARNING)
from freqtrade.configuration import Configuration
from freqtrade.resolvers import StrategyResolver

cfg = Configuration.from_files(["config.json", "config-4h.json"])
cfg["strategy"] = "SmcElliottStrategy"
cfg["strategy_path"] = "strategies"
s = StrategyResolver.load_strategy(cfg)
print(f"   ✅ {type(s).__name__} nạp từ {s.__file__.split('/freqtrade/')[-1]} "
      f"(timeframe {s.timeframe}, can_short={s.can_short})")
PY
}

# --- Chạy --------------------------------------------------------------------
echo "══ Setup môi trường freqtrade-SMC ══"
[[ $CHECK -eq 1 ]] && echo "(chế độ --check: không cài, không sửa gì)"

pick_python
ensure_venv
[[ -x "$VENV/bin/python" ]] || die "không dựng được virtualenv"
install_deps
ensure_env
ensure_dirs
ensure_strategies
ensure_claude
verify

echo
if [[ $FAILED -ne 0 ]]; then
    echo "❌ Còn thiếu — xem các dòng ✗ ở trên."
    [[ $CHECK -eq 1 ]] && echo "   Chạy ./setup_env.sh (không kèm --check) để cài."
    exit 1
fi

cat <<'EOF'

✅ Xong. Chạy bot:

   ./run_smc.sh                 # bot 4h swing  (config.json + config-4h.json, API :8091)
   ./run_smc_5.sh               # bot 5m scalp  (cần BOT_5M_TG_TOKEN riêng trong .env)
   ./run_smc.sh backtesting --timerange 20260101-

   tail -f user_data/logs/smc-4h.log

Bot chạy dry-run (dry_run=true trong config.json) — không đặt lệnh thật, không cần API key sàn.
Chưa có dữ liệu nến trong user_data/data: trade/dry-run tự tải khi chạy, nhưng backtesting
thì phải tải trước:

   ./run_smc.sh download-data --timeframes 15m 1h 4h 1d --timerange 20240101-
EOF

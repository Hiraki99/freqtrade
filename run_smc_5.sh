#!/usr/bin/env bash
# SMC 5m — bot scalping trong ngày, chạy ĐỘC LẬP với ./run_smc.sh (bot 4h).
# Cùng strategy SmcElliottStrategy, khác config: timeframe 5m, ROI/SL ngắn hạn,
# database và cổng API riêng (config-5m.json).
#
# Usage:
#   ./run_smc_5.sh                                    # dry/live bot smc-5m
#   ./run_smc_5.sh backtesting --timerange 20260701-  # backtest ĐÚNG khung 5m
#   ./run_smc_5.sh hyperopt --hyperopt-loss SharpeHyperOptLoss --epochs 100
#
# Chạy song song với run_smc.sh cần 3 thứ tách bạch — cả 3 đã có sẵn:
#   token Telegram : BOT_5M_TG_TOKEN trong .env (KHÁC token bot 4h, nếu không -> 409 Conflict)
#   database       : tradesv3.5m-futures.sqlite  (4h: tradesv3.4h-futures.sqlite)
#   cổng API       : 8082                        (4h: 8091)
#
# Cả hai bot chạy FUTURES (kế thừa từ config.json), long-only, đòn bẩy 1.0.
set -euo pipefail
cd "$(dirname "$0")"

STRAT="SmcElliottStrategy"
STRAT_PATH="strategies"          # nguồn strategy được git track (xem ./sync_strategies.sh)
CONFIG="config.json"
OVERRIDE="config-5m.json"
BOT_NAME="smc-5m"
TOKEN_VAR="BOT_5M_TG_TOKEN"
# Kênh nhận tin của bot 5m. Trống -> dùng chung chat_id với bot 4h.
# Dùng chung KHÔNG gây lỗi như dùng chung token: hai bot khác nhau nhắn cùng một người vẫn
# là hai cuộc trò chuyện riêng trong Telegram. Biến này chỉ cần khi muốn đẩy bot 5m sang
# một group/channel khác hẳn.
CHAT_VAR="BOT_5M_TG_CHAT_ID"

# Nạp secrets/token từ .env
if [[ -f .env ]]; then set -a; source .env; set +a; fi

FT=".venv/bin/freqtrade"; [[ -x "$FT" ]] || FT="freqtrade"

# --- Có tham số → backtesting / hyperopt / download-data / plot --------------
# Khác run_smc.sh: BẮT BUỘC kèm config-5m.json. Thiếu nó thì backtest chạy khung 5m
# hay 4h là tuỳ config.json, ra kết quả chẳng liên quan tới bot 5m đang chạy.
if [[ $# -gt 0 ]]; then
    # Chỉ thêm --strategy khi subcommand nhận nó. download-data, show-config và
    # test-pairlist KHÔNG nhận -> thêm vô điều kiện là "unrecognized arguments".
    # Hỏi thẳng --help thay vì giữ danh sách cứng, để không phải bảo trì theo bản freqtrade.
    STRAT_ARGS=()
    if "$FT" "$1" --help 2>/dev/null | grep -q -- "--strategy"; then
        STRAT_ARGS=(--strategy "$STRAT" --strategy-path "$STRAT_PATH")
    fi
    # ${A[@]+"${A[@]}"}: bash 3.2 (mặc định của macOS) coi mảng RỖNG là biến chưa gán
    # và `set -u` sẽ giết script. Cú pháp này bung ra đúng không-đối-số khi mảng rỗng.
    exec "$FT" "$@" \
        --config "$CONFIG" \
        --config "$OVERRIDE" \
        ${STRAT_ARGS[@]+"${STRAT_ARGS[@]}"}
fi

# --- Không tham số → chạy bot dry/live ---------------------------------------
mkdir -p user_data/logs

# GUARD: chỉ dọn instance CỦA CHÍNH BOT 5M.
# Pattern phải kèm tên file config — bắt theo mỗi "$STRAT" sẽ giết luôn bot 4h,
# vì hai bot dùng chung strategy. run_smc.sh cũng đã được siết tương tự.
stop_existing() {
    local pat="freqtrade trade .*${STRAT}.*${OVERRIDE}"
    pgrep -f "$pat" >/dev/null 2>&1 || return 0
    echo "⚠  Phát hiện bot ${BOT_NAME} cũ đang chạy → dừng để tránh 409 Conflict..."
    pkill -TERM -f "$pat" 2>/dev/null || true
    local i
    for i in $(seq 1 8); do
        pgrep -f "$pat" >/dev/null 2>&1 || break
        sleep 1
    done
    if pgrep -f "$pat" >/dev/null 2>&1; then
        echo "   Còn sót sau 8s → SIGKILL..."
        pkill -9 -f "$pat" 2>/dev/null || true
        sleep 1
    fi
    echo "   ✅ Đã dọn instance cũ."
}

stop_existing

TOKEN="${!TOKEN_VAR:-}"
TG_ENABLED="true"
if [[ -z "$TOKEN" ]]; then
    TG_ENABLED="false"
    echo "⚠  ${TOKEN_VAR} trống trong .env → TẮT Telegram cho bot 5m (theo dõi qua API :8082)."
    echo "   Tạo bot mới ở @BotFather rồi đặt ${TOKEN_VAR}=... — KHÔNG dùng lại token bot 4h."
fi

CHAT_ID="${!CHAT_VAR:-${FREQTRADE__TELEGRAM__CHAT_ID:-}}"

echo "→ Khởi động ${BOT_NAME} (config: ${CONFIG} + ${OVERRIDE})"
if [[ -n "${!CHAT_VAR:-}" ]]; then
    echo "   Telegram: kênh riêng (${CHAT_VAR})"
else
    echo "   Telegram: chung chat_id với bot 4h (đặt ${CHAT_VAR} để tách kênh)"
fi
echo "   log=user_data/logs/${BOT_NAME}.log   Ctrl+C để dừng."

# exec: script biến mất, freqtrade thành tiến trình chính -> Ctrl+C và SIGHUP
# (đóng terminal) tới thẳng bot, không để lại tiến trình mồ côi giữ token.
exec env \
    FREQTRADE__TELEGRAM__ENABLED="$TG_ENABLED" \
    FREQTRADE__TELEGRAM__TOKEN="$TOKEN" \
    FREQTRADE__TELEGRAM__CHAT_ID="$CHAT_ID" \
    "$FT" trade \
        --strategy "$STRAT" \
        --strategy-path "$STRAT_PATH" \
        --config "$CONFIG" \
        --config "$OVERRIDE" \
        --logfile "user_data/logs/${BOT_NAME}.log"

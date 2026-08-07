#!/usr/bin/env bash
# SMC — bot 4h (swing). Bot 5m scalping nằm ở ./run_smc_5.sh, bot ICT ở ./run_ict_m5.sh.
# Hai script SMC chạy song song được: khác config, khác database, khác cổng API, khác token.
# Strategy nạp từ strategies/ (folder git track).
#
# Usage:
#   ./run_smc.sh                                    # dry/live bot smc-4h (config.json + config-4h.json)
#   ./run_smc.sh backtesting --timerange 20260101-  # subcommand khác → chỉ dùng config.json
#   ./run_smc.sh hyperopt --hyperopt-loss SharpeHyperOptLoss --epochs 100
#   ./run_smc.sh lookahead-analysis --timerange 20260101-
#
# Nạp .env trước vì freqtrade không tự đọc .env.
set -euo pipefail
cd "$(dirname "$0")"

STRAT="SmcElliottStrategy"
STRAT_PATH="strategies"          # nguồn strategy được git track (xem ./sync_strategies.sh)
CONFIG="config.json"

# Bot chạy ở chế độ live/dry khi gọi script KHÔNG tham số.
# Format mỗi dòng: "tên:config override:tên biến .env chứa token Telegram"
# Token PHẢI riêng từng bot — 2 bot chung token → Telegram 409 Conflict.
# chat_id thì dùng chung (FREQTRADE__TELEGRAM__CHAT_ID) → tin nhắn về cùng kênh.
BOTS=(
    "smc-4h:config-4h.json:FREQTRADE__TELEGRAM__TOKEN"
    # Bot 5m KHÔNG thêm vào đây — nó có script riêng ./run_smc_5.sh để bật/tắt độc lập.
)

# Nạp secrets/token từ .env
if [[ -f .env ]]; then set -a; source .env; set +a; fi

FT=".venv/bin/freqtrade"; [[ -x "$FT" ]] || FT="freqtrade"

# --- Có tham số → truyền thẳng (backtesting / hyperopt / plot / analysis) ----
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
        ${STRAT_ARGS[@]+"${STRAT_ARGS[@]}"}
fi

# --- Không tham số → chạy bot dry/live ---------------------------------------
mkdir -p user_data/logs

# Dừng cả nhóm tiến trình khi Ctrl+C / kill / đóng terminal (HUP).
# HUP rất quan trọng: đóng cửa sổ terminal mà không có nó -> bot con thành
# mồ côi (PPID=1) vẫn chạy -> lần sau khởi động lại bị 409 Conflict.
trap 'echo; echo "→ Đang dừng bot SMC..."; kill 0 2>/dev/null; exit 0' INT TERM HUP

# GUARD: dọn instance cũ trước khi chạy — tránh chồng bot cùng token (409 Conflict)
# và chung database.
#
# Pattern kèm TÊN FILE CONFIG, không chỉ tên strategy: ./run_smc_5.sh chạy cùng
# SmcElliottStrategy trên config-5m.json, bắt theo mỗi "$STRAT" sẽ giết luôn bot 5m
# mỗi lần khởi động bot 4h.
kill_pattern() {
    local pat="$1" label="$2" i
    pgrep -f "$pat" >/dev/null 2>&1 || return 0
    echo "⚠  Phát hiện instance ${label} cũ đang chạy → dừng để tránh 409 Conflict..."
    pkill -TERM -f "$pat" 2>/dev/null || true
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

stop_existing() {
    local entry bot_name bot_config
    for entry in "${BOTS[@]}"; do
        IFS=':' read -r bot_name bot_config _ <<< "$entry"
        kill_pattern "freqtrade trade .*${STRAT}.*${bot_config}" "$bot_name"
    done
}

start_bot() {
    local name="$1" override="$2" token="${3:-}"
    local tg_enabled="true"
    if [[ -z "$token" ]]; then
        tg_enabled="false"
        echo "⚠  $name: token Telegram trống → TẮT Telegram (theo dõi qua API)."
    fi
    echo "→ Khởi động $name (config: $CONFIG + $override)"
    FREQTRADE__TELEGRAM__ENABLED="$tg_enabled" \
    FREQTRADE__TELEGRAM__TOKEN="$token" \
    FREQTRADE__TELEGRAM__CHAT_ID="${FREQTRADE__TELEGRAM__CHAT_ID:-}" \
        "$FT" trade \
            --strategy "$STRAT" \
            --strategy-path "$STRAT_PATH" \
            --config "$CONFIG" \
            --config "$override" \
            --logfile "user_data/logs/${name}.log" &
    echo "   PID=$!  log=user_data/logs/${name}.log"
}

# Dọn instance cũ TRƯỚC khi khởi động (chống 409 Conflict tái diễn).
stop_existing

for entry in "${BOTS[@]}"; do
    IFS=':' read -r bot_name bot_config token_var <<< "$entry"
    start_bot "$bot_name" "$bot_config" "${!token_var:-}"
done

echo
echo "✅ Đã chạy ${#BOTS[@]} bot SMC. Xem log:"
for entry in "${BOTS[@]}"; do
    IFS=':' read -r bot_name _ _ <<< "$entry"
    echo "   tail -f user_data/logs/${bot_name}.log"
done
echo "   Ctrl+C để dừng."
wait

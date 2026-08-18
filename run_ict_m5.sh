#!/usr/bin/env bash
# ICT M5 — bot SCALP theo ICT (IctM5Strategy, futures, khung M5), báo về kênh Telegram CHUNG.
# Bot SMC nằm ở ./run_smc.sh (cùng dạng: không tham số = trade, có tham số = truyền thẳng).
#
# Telegram: chat_id DÙNG CHUNG (FREQTRADE__TELEGRAM__CHAT_ID) → tin nhắn về cùng 1 kênh.
#           token thì PHẢI RIÊNG cho mỗi bot (Telegram 409 Conflict nếu 2 bot chung token).
#           → ưu tiên BOT_5M_TG_TOKEN (slot "bot 5m scalp" trong .env). Trống thì fallback
#             token chính, kèm cảnh báo (chỉ an toàn khi KHÔNG chạy song song bot 4h).
#
# Bắt buộc futures: can_short=True không load được ở spot.
#
# Usage:
#   ./run_ict_m5.sh                # dry-run trade (mặc định)
#   ./run_ict_m5.shbacktesting --timerange 20250401-   # truyền thẳng subcommand + args khác
set -euo pipefail
cd "$(dirname "$0")"

CONFIG="config-ict-futures.json"
STRAT="IctM5Strategy"
STRAT_PATH="strategies"          # nguồn strategy được git track (xem ./sync_strategies.sh)

# Nạp secrets/token từ .env
if [[ -f .env ]]; then set -a; source .env; set +a; fi

FT=".venv/bin/freqtrade"; [[ -x "$FT" ]] || FT="freqtrade"
mkdir -p user_data/logs

# --- Chọn token Telegram: riêng cho bot scalp, chat_id chung ------------------
TG_TOKEN="${BOT_5M_TG_TOKEN:-}"
if [[ -z "$TG_TOKEN" ]]; then
    TG_TOKEN="${FREQTRADE__TELEGRAM__TOKEN:-}"
    [[ -n "$TG_TOKEN" ]] && echo "⚠  BOT_5M_TG_TOKEN trống → dùng token CHÍNH. Đừng chạy song song bot 4h (409 Conflict)."
fi
TG_CHAT="${FREQTRADE__TELEGRAM__CHAT_ID:-}"

TG_ENABLED="true"
if [[ -z "$TG_TOKEN" || -z "$TG_CHAT" ]]; then
    TG_ENABLED="false"
    echo "⚠  Thiếu token/chat_id → TẮT Telegram (theo dõi qua log/API)."
fi

# --- Nếu là lệnh trade: dọn instance ICT cũ để tránh 409 + chung database ------
SUBCMD="${1:-trade}"
if [[ "$SUBCMD" == "trade" || $# -eq 0 ]]; then
    PAT="freqtrade trade .*${STRAT}"
    if pgrep -f "$PAT" >/dev/null 2>&1; then
        echo "⚠  Phát hiện ${STRAT} đang chạy → dừng để tránh 409 Conflict..."
        pkill -TERM -f "$PAT" 2>/dev/null || true
        for _ in $(seq 1 8); do pgrep -f "$PAT" >/dev/null 2>&1 || break; sleep 1; done
        pgrep -f "$PAT" >/dev/null 2>&1 && { echo "   Còn sót → SIGKILL"; pkill -9 -f "$PAT" 2>/dev/null || true; sleep 1; }
        echo "   ✅ Đã dọn."
    fi
fi

# Dừng gọn khi Ctrl+C / đóng terminal (HUP: tránh bot mồ côi → 409 lần sau).
trap 'echo; echo "→ Dừng bot ICT..."; kill 0 2>/dev/null; exit 0' INT TERM HUP

if [[ $# -eq 0 ]]; then
    echo "→ Khởi động bot SCALP ICT ($STRAT, $CONFIG) — dry-run"
    echo "   Telegram: enabled=$TG_ENABLED  chat_id=${TG_CHAT:-<none>} (kênh chung)"
    echo "   Log: user_data/logs/ict-m5.log   | Ctrl+C để dừng."
    FREQTRADE__TELEGRAM__ENABLED="$TG_ENABLED" \
    FREQTRADE__TELEGRAM__TOKEN="$TG_TOKEN" \
    FREQTRADE__TELEGRAM__CHAT_ID="$TG_CHAT" \
        exec "$FT" trade \
            --strategy "$STRAT" \
            --strategy-path "$STRAT_PATH" \
            --config "$CONFIG" \
            --logfile "user_data/logs/ict-m5.log"
else
    # Truyền thẳng subcommand + args (backtesting / hyperopt / lookahead-analysis / ...)
    FREQTRADE__TELEGRAM__ENABLED="$TG_ENABLED" \
    FREQTRADE__TELEGRAM__TOKEN="$TG_TOKEN" \
    FREQTRADE__TELEGRAM__CHAT_ID="$TG_CHAT" \
        exec "$FT" "$@" \
            --strategy "$STRAT" \
            --strategy-path "$STRAT_PATH" \
            --config "$CONFIG"
fi

#!/usr/bin/env bash
# Menu "nút bấm" để chạy thử BẤT KỲ strategy nào trong strategies/.
# Muốn chạy cố định 1 bot thì dùng ./run_smc.sh hoặc ./run_ict_m5.sh.
#
# Usage: ./try_strategy.sh [TênStrategy]   (mặc định SmcElliottStrategy)
#   vd:  ./try_strategy.sh IctM5Strategy
set -euo pipefail
cd "$(dirname "$0")"

STRAT="${1:-SmcElliottStrategy}"
STRAT_PATH="strategies"          # nguồn strategy được git track (xem ./sync_strategies.sh)
TF="1h"
TR="20260101-"

# Chỉ chạy strategy có thật trong strategies/ — báo sớm thay vì để freqtrade
# nuốt lỗi sau 10s load config.
if [[ ! -f "$STRAT_PATH/$STRAT.py" ]]; then
    echo "✗ Không tìm thấy $STRAT_PATH/$STRAT.py"
    echo "  Strategy có sẵn:"
    for f in "$STRAT_PATH"/*.py; do echo "    - $(basename "${f%.py}")"; done
    exit 1
fi

# Nạp .env (token/chat_id/API key) nếu có
if [[ -f .env ]]; then set -a; source .env; set +a; fi

PY="python3"; [[ -x .venv/bin/python ]] && PY=".venv/bin/python"
FT() { "$PY" -m freqtrade "$@" --config config.json --strategy-path "$STRAT_PATH"; }

menu() {
    echo ""
    echo "════════════════════════════════════════════"
    echo "  Chạy thử strategy:  $STRAT  (tf=$TF)"
    echo "════════════════════════════════════════════"
    echo "  1) Backtest            (xem hiệu năng lịch sử)"
    echo "  2) Dry-run trade       (chạy thật, lệnh ảo + Telegram)"
    echo "  3) Hyperopt            (tối ưu tham số, 100 epochs)"
    echo "  4) Plot chart tín hiệu (BTC/USDT)"
    echo "  5) Lookahead-analysis  (kiểm tra bias)"
    echo "  6) Đổi timeframe       (hiện tại: $TF)"
    echo "  7) Đổi timerange       (hiện tại: $TR)"
    echo "  0) Thoát"
    echo "────────────────────────────────────────────"
    printf "Chọn [0-7]: "
}

while true; do
    menu
    read -r choice
    case "$choice" in
        1) FT backtesting --strategy "$STRAT" --timeframe "$TF" --timerange "$TR" ;;
        2) echo "→ Ctrl+C để dừng dry-run."; FT trade --strategy "$STRAT" ;;
        3) FT hyperopt --strategy "$STRAT" --timeframe "$TF" --analyze-per-epoch \
              --hyperopt-loss SharpeHyperOptLoss --spaces buy --epochs 100 --timerange "$TR" ;;
        4) FT plot-dataframe --strategy "$STRAT" --timeframe "$TF" --pairs BTC/USDT --timerange "$TR" \
              && echo "→ Mở file HTML trong user_data/plot/" ;;
        5) FT lookahead-analysis --strategy "$STRAT" --timeframe "$TF" --timerange "$TR" ;;
        6) printf "Timeframe mới (vd 5m/15m/1h/4h): "; read -r TF ;;
        7) printf "Timerange mới (vd 20260101-20260401): "; read -r TR ;;
        0) echo "Bye 👋"; exit 0 ;;
        *) echo "Lựa chọn không hợp lệ." ;;
    esac
done

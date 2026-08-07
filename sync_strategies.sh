#!/usr/bin/env bash
# Import các strategy từ strategies/ (folder được git track) vào user_data/strategies/
# bằng SYMLINK tương đối — sửa file trong strategies/ là bot nhận ngay, không cần sync lại.
#
# Vì sao tách 2 folder:
#   - user_data/* bị .gitignore chặn (upstream freqtrade) → strategy đặt ở đó KHÔNG lên git.
#   - strategies/ nằm ngoài user_data → được track bình thường.
#   - freqtrade vẫn load theo layout mặc định user_data/strategies/ nhờ symlink.
#
# Usage:
#   ./sync_strategies.sh           # tạo/cập nhật symlink + dọn link chết
#   ./sync_strategies.sh --force   # ghi đè cả file thật đang nằm trong user_data/strategies/
#   ./sync_strategies.sh --check   # chỉ báo cáo, không thay đổi gì (dùng cho CI/pre-commit)
set -euo pipefail
cd "$(dirname "$0")"

SRC="strategies"
DST="user_data/strategies"

FORCE=0
CHECK=0
for arg in "$@"; do
    case "$arg" in
        --force) FORCE=1 ;;
        --check) CHECK=1 ;;
        -h|--help) sed -n '2,14p' "$0"; exit 0 ;;
        *) echo "Tham số không hợp lệ: $arg" >&2; exit 2 ;;
    esac
done

[[ -d "$SRC" ]] || { echo "✗ Không tìm thấy folder $SRC/" >&2; exit 1; }
mkdir -p "$DST"

drift=0

# --- Tạo / cập nhật symlink cho từng strategy -------------------------------
shopt -s nullglob
for src in "$SRC"/*.py; do
    name="$(basename "$src")"
    link="$DST/$name"
    # Đường dẫn tương đối: user_data/strategies/X.py -> ../../strategies/X.py
    target="../../$SRC/$name"

    if [[ -L "$link" ]]; then
        if [[ "$(readlink "$link")" == "$target" ]]; then
            echo "   = $name (đã đúng)"
            continue
        fi
        (( CHECK )) && { echo "   ! $name symlink trỏ sai → $(readlink "$link")"; drift=1; continue; }
        ln -sfn "$target" "$link"
        echo "   ↻ $name (sửa symlink)"
    elif [[ -e "$link" ]]; then
        # File THẬT đang chiếm chỗ — không tự ý xoá, dễ mất bản sửa tay.
        if (( CHECK )); then
            echo "   ! $name là file thật, chưa được import"; drift=1; continue
        fi
        if (( FORCE )); then
            mv "$link" "$link.bak-$(date +%Y%m%d%H%M%S)"
            ln -s "$target" "$link"
            echo "   ↻ $name (file cũ đổi tên thành .bak-*, đã thay bằng symlink)"
        else
            echo "   ⚠ $name: đang là FILE THẬT trong $DST/ → bỏ qua."
            echo "      So sánh: diff $link $src"
            echo "      Ghi đè:  ./sync_strategies.sh --force"
            drift=1
        fi
    else
        (( CHECK )) && { echo "   ! $name chưa import"; drift=1; continue; }
        ln -s "$target" "$link"
        echo "   + $name"
    fi
done

# --- Dọn symlink chết (strategy đã xoá/đổi tên trong strategies/) -----------
for link in "$DST"/*.py; do
    [[ -L "$link" ]] || continue
    [[ -e "$link" ]] && continue          # -e đi theo link: còn sống thì bỏ qua
    if (( CHECK )); then
        echo "   ! $(basename "$link") là symlink chết"; drift=1; continue
    fi
    rm "$link"
    echo "   - $(basename "$link") (symlink chết, đã xoá)"
done
shopt -u nullglob

if (( CHECK )); then
    (( drift )) && { echo "✗ $DST/ lệch với $SRC/ → chạy ./sync_strategies.sh"; exit 1; }
    echo "✓ $DST/ khớp với $SRC/"
    exit 0
fi

echo
echo "✓ Xong. Strategy nguồn: $SRC/ (git track) → $DST/ (symlink, gitignore)"

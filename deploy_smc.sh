#!/usr/bin/env bash
# Đóng gói CẢ HAI bot SMC (4h swing + 5m scalping) vào MỘT image, đẩy lên registry để server
# chỉ việc pull. Hai bot khác nhau ở lớp config lúc chạy, không phải ở image.
# Runbook: docs/docker-smc.md §7.
#
# Usage:
#   ./deploy_smc.sh build            # build native arch, nạp vào docker local để thử
#   ./deploy_smc.sh push             # build linux/amd64 rồi đẩy lên registry (tag + latest)
#   ./deploy_smc.sh server           # in ra đúng các lệnh cần chạy trên server
#   ./deploy_smc.sh login            # đăng nhập registry
#
# Cấu hình qua .env (hoặc biến môi trường):
#   SMC_REGISTRY=ghcr.io/<github-username>   # namespace registry, KHÔNG kèm tên image
#   SMC_IMAGE_NAME=freqtrade-smc             # mặc định freqtrade-smc
#   SMC_PLATFORM=linux/amd64                 # kiến trúc CPU của SERVER, không phải của máy build
set -euo pipefail
cd "$(dirname "$0")"

# Nạp .env: script cần SMC_REGISTRY, và `login` cần token registry nếu để trong đó.
if [[ -f .env ]]; then set -a; source .env; set +a; fi

REGISTRY="${SMC_REGISTRY:-}"
IMAGE_NAME="${SMC_IMAGE_NAME:-freqtrade-smc}"
# Mặc định amd64 vì gần như mọi VPS là Intel/AMD. Mac Apple Silicon build ra arm64,
# đẩy nguyên xi lên server amd64 thì container chết ngay với "exec format error".
PLATFORM="${SMC_PLATFORM:-linux/amd64}"
BUILDER="${SMC_BUILDER:-freqtrade-smc-builder}"
DOCKERFILE="docker/Dockerfile.smc"
COMPOSE_DEPLOY="docker-compose.smc.deploy.yml"

die() { echo "ERROR: $*" >&2; exit 1; }

require_registry() {
    [[ -n "$REGISTRY" ]] || die "chưa đặt SMC_REGISTRY trong .env (vd: SMC_REGISTRY=ghcr.io/thinhnn)"
}

# Tag: thời gian trước để sort được, kèm commit để truy ngược mã nguồn. `-dirty` là quan trọng —
# config.json, config-4h.json và config-5m.json KHÔNG nằm trong git (.gitignore chặn
# config*.json), nên riêng commit hash không định danh đủ nội dung image.
image_tag() {
    local sha dirty=""
    sha="$(git rev-parse --short HEAD 2>/dev/null || echo nogit)"
    [[ -n "$(git status --porcelain 2>/dev/null)" ]] && dirty="-dirty"
    echo "$(date +%Y%m%d-%H%M%S)-${sha}${dirty}"
}

# Những file này bị .gitignore chặn nên không đi theo git clone. Build vẫn chạy qua nếu thiếu,
# rồi container crash-loop lúc khởi động — chặn ngay ở đây rẻ hơn nhiều.
preflight() {
    docker info >/dev/null 2>&1 || die "Docker daemon chưa chạy (colima start / mở Docker Desktop)"
    for f in config.json config-4h.json config-5m.json \
             docker/smc-overrides.json docker/smc-5m-overrides.json "$DOCKERFILE"; do
        [[ -f "$f" ]] || die "thiếu $f"
    done
}

# buildx driver mặc định ("docker") không build được kiến trúc khác máy và không push thẳng
# lên registry. docker-container driver làm được cả hai.
ensure_builder() {
    docker buildx inspect "$BUILDER" >/dev/null 2>&1 \
        || docker buildx create --name "$BUILDER" --driver docker-container --bootstrap
}

case "${1:-}" in
build)
    preflight
    docker buildx build --load -f "$DOCKERFILE" -t "${IMAGE_NAME}:local" .
    echo "OK: ${IMAGE_NAME}:local (chạy được cả bot 4h lẫn 5m)"
    echo "    thử bằng: docker compose -f docker-compose.smc.yml up -d"
    ;;

push)
    require_registry
    preflight
    ensure_builder
    TAG="$(image_tag)"
    REF="${REGISTRY}/${IMAGE_NAME}"

    # Image này KHÔNG sạch secret: config.json bake sẵn api_server.password và jwt_secret_key
    # (docs/docker-smc.md §5). Registry công khai = lộ cả hai.
    cat <<WARN

  ⚠️  Image bake config.json -> chứa api_server.password + jwt_secret_key.
      Registry PHẢI là private. Trên server nhớ đè bằng .env:
        FREQTRADE__API_SERVER__PASSWORD=...
        FREQTRADE__API_SERVER__JWT_SECRET_KEY=...

  Đẩy: ${REF}:${TAG}  (+ :latest)   platform=${PLATFORM}
WARN
    read -r -p "  Registry này là private? [y/N] " ans
    [[ "$ans" == "y" || "$ans" == "Y" ]] || die "đã huỷ"

    docker buildx build \
        --builder "$BUILDER" \
        --platform "$PLATFORM" \
        -f "$DOCKERFILE" \
        -t "${REF}:${TAG}" \
        -t "${REF}:latest" \
        --push .

    echo
    echo "Đã đẩy: ${REF}:${TAG}"
    echo "Trên server, ghim đúng tag này trong .env (đừng dùng latest — không rollback được):"
    echo "  SMC_IMAGE=${REF}:${TAG}"
    ;;

login)
    require_registry
    host="${REGISTRY%%/*}"
    # GHCR yêu cầu Personal Access Token có scope write:packages (mật khẩu GitHub không dùng được).
    if [[ -n "${SMC_REGISTRY_TOKEN:-}" ]]; then
        echo "$SMC_REGISTRY_TOKEN" | docker login "$host" -u "${SMC_REGISTRY_USER:-$USER}" --password-stdin
    else
        docker login "$host"
    fi
    ;;

server)
    require_registry
    cat <<EOF
# ---- Chạy trên SERVER (một lần) --------------------------------------------
mkdir -p ~/freqtrade-smc/user_data/logs && cd ~/freqtrade-smc
# copy 2 file này từ máy build sang: ${COMPOSE_DEPLOY} và .env
# .env PHẢI có BOT_5M_TG_TOKEN (token Telegram riêng của bot 5m) — thiếu thì compose
# dừng ngay, vì dùng chung token với bot 4h sẽ gây 409 Conflict cho cả hai.
# user trong container là uid 1000; user_data phải ghi được bởi uid đó:
sudo chown -R 1000:1000 user_data
docker login ${REGISTRY%%/*}

# ---- Mỗi lần deploy (cả 2 bot: 4h + 5m) ------------------------------------
# 1. sửa SMC_IMAGE trong .env thành tag mới
docker compose -f ${COMPOSE_DEPLOY} pull
docker compose -f ${COMPOSE_DEPLOY} up -d
docker compose -f ${COMPOSE_DEPLOY} logs -f

# Chỉ một bot: thêm tên service vào cuối (freqtrade-smc | freqtrade-smc-5m)

# ---- Rollback --------------------------------------------------------------
# đổi SMC_IMAGE về tag cũ rồi chạy lại 2 lệnh pull + up -d
EOF
    ;;

*)
    sed -n '2,14p' "$0"
    exit 1
    ;;
esac

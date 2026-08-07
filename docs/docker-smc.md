# Chạy hai bot SMC (4h + 5m) bằng Docker

Tương đương với `./run_smc.sh` và `./run_smc_5.sh` khi gọi **không tham số**. Cả hai chạy cùng một
`SmcElliottStrategy`, khác nhau ở lớp config:

| Bot | Service | Config | Cổng API | Log | Database |
|---|---|---|---|---|---|
| 4h swing | `freqtrade-smc` | `config.json` + `config-4h.json` + `docker/smc-overrides.json` | 8091 | `user_data/logs/smc-4h.log` | `tradesv3.4h-futures.sqlite` |
| 5m scalping | `freqtrade-smc-5m` | `config.json` + `config-5m.json` + `docker/smc-5m-overrides.json` | 8082 | `user_data/logs/smc-5m.log` | `tradesv3.5m-futures.sqlite` |

| Thành phần | File |
|---|---|
| Image (dùng chung cho cả hai bot) | `docker/Dockerfile.smc` |
| Ignore riêng cho build | `docker/Dockerfile.smc.dockerignore` |
| Compose — build tại chỗ | `docker-compose.smc.yml` |
| Compose — server pull từ registry | `docker-compose.smc.deploy.yml` |

**Một image, hai container.** Hai bot chung strategy và chung package `freqtrade/`, chỉ khác tham
số dòng lệnh. Image thứ hai chỉ nhân đôi ~1 GB layer và mở đường cho hai bot chạy lệch phiên bản
mà không ai biết. Bot 5m ghi đè `CMD` của image bằng `command:` trong compose.

**Token Telegram phải khác nhau.** Bot 4h dùng `FREQTRADE__TELEGRAM__TOKEN`, bot 5m dùng
`BOT_5M_TG_TOKEN` — hai container chung một token thì Telegram trả **409 Conflict** và *cả hai*
mất thông báo. Compose khai báo `${BOT_5M_TG_TOKEN:?...}` nên để trống thì `up` dừng ngay, thay vì
để hỏng âm thầm.

Không thể dùng image `freqtradeorg/freqtrade` — `freqtrade/rpc/telegram.py` trong repo này đã sửa
riêng để thêm lệnh `/analysis` và `/smc`, nên image bắt buộc phải build từ source của repo.

---

## 1. Chuẩn bị

### 1.1 Cài Docker

Máy chưa cài. Chọn một trong hai:

```bash
# Cách 1 — Docker Desktop (có GUI)
#   https://www.docker.com/products/docker-desktop/

# Cách 2 — colima (nhẹ, chạy CLI)
brew install colima docker docker-compose
colima start --cpu 2 --memory 4
```

Kiểm tra:

```bash
docker --version && docker compose version
```

### 1.2 Các file bắt buộc phải có sẵn

`.gitignore` chặn `config*.json`, nên **`config.json`, `config-4h.json` và `config-5m.json` không
nằm trong git**. Chúng được bake vào image từ working tree của máy đang build. Clone mới sẽ không
có ba file này — build vẫn chạy qua nhưng container sẽ chết ngay lúc khởi động.

```bash
ls config.json config-4h.json config-5m.json   # cả ba phải tồn tại
cp .env.example .env                           # rồi điền các token bên dưới
mkdir -p user_data/logs
```

Trong `.env` cần **ba** giá trị Telegram (bỏ qua nếu không dùng Telegram — xem §6):

```
FREQTRADE__TELEGRAM__TOKEN=<token bot 4h>
FREQTRADE__TELEGRAM__CHAT_ID=<chat id, dùng chung cho cả hai bot>
BOT_5M_TG_TOKEN=<token bot 5m — bot KHÁC ở @BotFather>
```

### 1.3 Chuyển database trade sang user_data/ — làm một lần

Bot chạy trên host dùng `db_url` tương đối trong `config-4h.json` / `config-5m.json`, nên file DB
nằm ở **gốc repo**. Container dùng đường dẫn tuyệt đối trong `user_data/`. Đây là **hai file khác
nhau**: nếu bỏ qua bước này, container khởi động với DB rỗng, không biết gì về lệnh đang mở — sẽ
không quản lý stoploss/exit cho chúng và có thể vào lại đúng cặp đó.

```bash
# Dừng bot host TRƯỚC khi copy (để SQLite flush -wal), rồi:
cp tradesv3.4h-futures.sqlite* user_data/    # bot 4h
cp tradesv3.5m-futures.sqlite* user_data/    # bot 5m
```

Tên file trong container giữ **đúng như bản host**, nên copy sang là dùng được ngay, không cần
đổi tên. `tradesv3.5m.sqlite` (thời còn chạy spot) không liên quan — để nguyên mà tra cứu.

### 1.4 Dừng bot host

Nếu `./run_smc.sh` hoặc `./run_smc_5.sh` đang chạy, bot host và container sẽ dùng chung một
Telegram token (lỗi **409 Conflict**) và tranh cổng 8091 / 8082. `container_name` **không** ngăn
được việc này — `pgrep` và Docker không nhìn thấy nhau.

```bash
# Xem trước cái gì sẽ bị giết — pkill -f khớp toàn bộ command line, dễ bắn nhầm
# editor, tail, hay chính lệnh docker logs đang mở.
pgrep -af "freqtrade trade .*SmcElliottStrategy"
# Rồi kill theo PID cụ thể, hoặc Ctrl+C ở terminal đang chạy run_smc.sh / run_smc_5.sh.
```

## 2. Build và chạy

```bash
# Cả hai bot
docker compose -f docker-compose.smc.yml up -d --build

# Chỉ một bot — thêm tên service vào cuối
docker compose -f docker-compose.smc.yml up -d --build freqtrade-smc       # 4h
docker compose -f docker-compose.smc.yml up -d --build freqtrade-smc-5m    # 5m
```

Lần build đầu mất vài phút (biên dịch `ta-lib`, `scipy`). Các lần sau dùng cache. Hai service dùng
chung tag `freqtrade-smc:local` nên chỉ build một lần thật; lượt thứ hai là cache hit.

## 3. Vận hành hằng ngày

Không kèm tên service thì lệnh áp cho **cả hai** container.

```bash
# Xem log trực tiếp (cả hai bot, tiền tố là tên service)
docker compose -f docker-compose.smc.yml logs -f
docker compose -f docker-compose.smc.yml logs -f freqtrade-smc-5m   # chỉ bot 5m

# Hoặc đọc file log (nằm trên host qua bind mount)
tail -f user_data/logs/smc-4h.log
tail -f user_data/logs/smc-5m.log

# Trạng thái + healthcheck
docker compose -f docker-compose.smc.yml ps

# Khởi động lại
docker compose -f docker-compose.smc.yml restart
docker compose -f docker-compose.smc.yml restart freqtrade-smc-5m

# Dừng hẳn (dữ liệu trade vẫn còn trong user_data/)
docker compose -f docker-compose.smc.yml down
docker compose -f docker-compose.smc.yml stop freqtrade-smc-5m      # tắt riêng bot 5m
```

REST API: <http://127.0.0.1:8091> (4h) và <http://127.0.0.1:8082> (5m) — chỉ mở trên loopback của
host, không ra ngoài mạng. Tài khoản lấy từ `api_server.username` / `password` (xem §5 về việc đổi
password).

> Healthcheck chỉ để **quan sát**. Docker Engine không tự khởi động lại container `unhealthy`, và
> không có gì trong stack này đọc trạng thái đó. Thấy `unhealthy` thì vào xem log.

## 4. Quy tắc quan trọng: sửa gì thì phải build lại

Strategy và config được **bake thẳng vào image**. Sửa file trên host **không có tác dụng** cho tới
khi build lại:

| Sửa file | Cần làm |
|---|---|
| `strategies/SmcElliottStrategy.py` | `up -d --build` — **ảnh hưởng CẢ HAI bot** |
| `config.json` | `up -d --build` — **ảnh hưởng cả hai bot** |
| `config-4h.json`, `docker/smc-overrides.json` | `up -d --build` (chỉ bot 4h đọc) |
| `config-5m.json`, `docker/smc-5m-overrides.json` | `up -d --build` (chỉ bot 5m đọc) |
| `freqtrade/rpc/telegram.py` (hoặc bất kỳ file nào trong `freqtrade/`) | `up -d --build` |
| `.env` (token, key, password) | chỉ cần `restart` |

Vì hai bot chung một image, build lại là build lại cho **cả hai**. Sửa `config-5m.json` rồi
`up -d --build` sẽ tạo image mới và tái tạo luôn container 4h — không mất trade (DB nằm ở
`user_data/`), nhưng bot 4h sẽ khởi động lại. Muốn tránh thì `up -d --build freqtrade-smc-5m`,
đổi lại là hai container tạm thời chạy hai image khác nhau cho tới lần build chung kế tiếp.

```bash
docker compose -f docker-compose.smc.yml up -d --build
```

Đây là hệ quả trực tiếp của lựa chọn bake. Đổi lại, image chạy được y hệt trên mọi máy mà không
phụ thuộc `.venv` hay `./sync_strategies.sh`.

> **Rủi ro lệch phiên bản.** Backtest chạy trên host đọc thẳng `strategies/SmcElliottStrategy.py`,
> còn container chạy bản đã bake. Sửa strategy → backtest thấy đẹp → quên `--build` thì container
> vẫn giao dịch bằng logic cũ, và không có gì trong log hay `/smc` báo cho anh biết. Tạo thói quen
> `--build` ngay sau khi sửa strategy.

## 5. Cái gì nằm trong image, cái gì không

**Bake vào image:** `strategies/`, `config.json`, `config-4h.json`, `config-5m.json`,
`docker/smc-overrides.json`, `docker/smc-5m-overrides.json`, toàn bộ package `freqtrade/`.

**Nạp lúc chạy qua `env_file: .env`:**

| Secret | Trạng thái |
|---|---|
| `telegram.token` (bot 4h) | Rỗng trong `config.json` → **không bị bake**, lấy từ `.env` |
| `telegram.token` (bot 5m) | Lấy từ `BOT_5M_TG_TOKEN` trong `.env`, compose ánh xạ sang `FREQTRADE__TELEGRAM__TOKEN` cho riêng container 5m |
| `telegram.chat_id` | Rỗng trong `config.json` → **không bị bake**, dùng chung cho cả hai bot |
| `exchange.key`, `exchange.secret` | Rỗng trong `config.json` → **không bị bake**, lấy từ `.env` |
| `api_server.password` | **Có giá trị trong `config.json` → BỊ BAKE** |
| `api_server.jwt_secret_key` | **Có giá trị trong `config.json` → BỊ BAKE** |

Hai dòng cuối là điều cần biết trước khi `docker save`, `docker push`, hay đưa image cho ai khác:
password REST API và khoá ký JWT đọc được từ layer. Đè bằng `.env`:

```
FREQTRADE__API_SERVER__PASSWORD=<mật khẩu mới>
FREQTRADE__API_SERVER__JWT_SECRET_KEY=<chuỗi ngẫu nhiên mới>
```

**Bind mount:** `user_data/` — DB SQLite, log, dữ liệu nến tải về đều sống sót qua `down`.

`docker/smc-overrides.json` (4h) và `docker/smc-5m-overrides.json` (5m) là lớp config cuối cùng,
mỗi file sửa đúng hai thứ mà bản chạy host không cần:

- `db_url` thành đường dẫn tuyệt đối trong `user_data/` (bản gốc là đường dẫn tương đối, sẽ rơi vào
  `/freqtrade` — không phải volume — và mất sạch trade khi xoá container). Xem §1.3.
- `api_server.listen_ip_address` thành `0.0.0.0` (bản gốc `127.0.0.1` trong container nghĩa là chỉ
  loopback của container, cổng publish sẽ không truy cập được). An toàn vì compose chỉ publish ra
  `127.0.0.1` của host.

Cổng vẫn giữ nguyên như bản host (8091 và 8082) để không phải nhớ hai bộ số.

## 6. Xử lý sự cố

### Telegram im lặng, nhưng container vẫn "khoẻ"

Đây là chế độ hỏng **nguy hiểm nhất** vì không có tín hiệu gì. `Telegram._init()` chạy trong thread
riêng (`freqtrade/rpc/telegram.py:253`), nên token rỗng hoặc sai chỉ giết thread đó — bot vẫn
`RUNNING`, healthcheck vẫn xanh, mà mọi thông báo và lịch `/analysis` đều chết.

`.env.example` ship với `FREQTRADE__TELEGRAM__TOKEN=` rỗng, nên đây là hành vi mặc định nếu quên
điền. Kiểm tra bằng cách tìm dòng token trong log:

```bash
docker compose -f docker-compose.smc.yml logs | grep -i "telegram\|InvalidToken\|Conflict"
```

Nếu cố ý không dùng Telegram, thêm vào `.env` để tắt hẳn cho rõ ràng:

```
FREQTRADE__TELEGRAM__ENABLED=false
BOT_5M_TG_TOKEN=disabled          # vẫn phải có giá trị, nếu không compose từ chối chạy
```

### `Conflict: terminated by other getUpdates request`

Hai tiến trình đang dùng **cùng một token**. Trong 409 Conflict, Telegram chỉ phục vụ một bên và
bên kia mất sạch thông báo — nhưng cả hai bot vẫn giao dịch bình thường và healthcheck vẫn xanh.

Thủ phạm thường gặp, theo thứ tự:

1. `BOT_5M_TG_TOKEN` được đặt bằng đúng token của bot 4h. Kiểm chứng bằng cách so hai giá trị đã
   phân giải — hai dòng phải khác nhau:

   ```bash
   docker compose -f docker-compose.smc.yml config | grep -A1 FREQTRADE__TELEGRAM__TOKEN
   ```

2. Bot host (`./run_smc.sh` / `./run_smc_5.sh`) vẫn chạy song song với container. Xem §1.4.
3. Container cũ từ lần `up` trước chưa chết: `docker ps -a | grep freqtrade-smc`.

### Container restart liên tục

```bash
docker compose -f docker-compose.smc.yml logs --tail 100
```

Nguyên nhân hay gặp: thiếu `config.json` (xem §1.2), sai quyền ghi `user_data/` (xem dưới), hoặc
thiếu key exchange khi `dry_run: false`. Lưu ý `restart: unless-stopped` không có backoff — mỗi
lần khởi động lại đều gọi API sàn, lặp lâu có thể bị rate-limit tạm thời.

### Lỗi quyền ghi `user_data/`

Container chạy bằng user `ftuser` (uid 1000). Nếu `user_data/` trên host thuộc uid khác — hay gặp
trên Linux, hoặc khi để `docker` tự tạo thư mục — sẽ gặp `PermissionError` lúc mở DB hoặc file log.

```bash
sudo chown -R 1000:1000 user_data
```

Trên Docker Desktop cho macOS thì không gặp vấn đề này.

### Build lỗi `COPY failed: ... config.json`

Builder không dùng BuildKit nên bỏ qua `docker/Dockerfile.smc.dockerignore` và áp
`.dockerignore` gốc (chặn `config.json*` **và** `docker/`). Ép bật BuildKit:

```bash
DOCKER_BUILDKIT=1 docker compose -f docker-compose.smc.yml build
```

Đây là lỗi **cố ý** — `Dockerfile.smc` có `COPY` tường minh 3 file config để build gãy ngay tại
chỗ. Nếu chỉ dựa vào `COPY . /freqtrade/` thì file bị lọc sẽ biến mất âm thầm, image build xong
bình thường rồi container chết lúc chạy — khó lần ra hơn nhiều.

Nếu buộc phải chạy trên builder cũ, phương án dự phòng là thêm vào `.dockerignore` gốc **cả hai**
dòng phủ định (chỉ `!config.json` là chưa đủ — thiếu `docker/` thì mất luôn lớp override):

```
!config.json
!docker/
```

`.dockerignore` gốc là file thuộc upstream — chỉ sửa khi không còn cách nào khác.

### `bind: address already in use` ở cổng 8091

Bot host vẫn đang chạy. Xem §1.4.

### Vì sao là 8091 mà không phải 8081

Bot này từng dùng 8081 — **cũng là cổng mặc định của Metro bundler** (React Native / Expo). Khi cả
hai cùng chạy trên một máy, freqtrade chiếm cổng trước và thiết bị iOS không kết nối được Metro:

```
uvicorn.error - INFO - 127.0.0.1:63645 - "WebSocket /message?role=ios" 403
uvicorn.error - INFO - connection rejected (403 Forbidden)
```

Bẫy chẩn đoán: request **HTTP thường vẫn trả 200** vì freqtrade phục vụ SPA fallback cho mọi route
lạ — chỉ WebSocket upgrade mới bị 403. Dễ tưởng là lỗi phía Metro. Kiểm nhanh xem ai đang giữ cổng:

```bash
lsof -nP -iTCP:8081 -sTCP:LISTEN
```

Đã dời sang 8091 ngày 2026-08-05. **Đừng đẩy về 8081.** Bot 5m ở 8082 (`config-5m.json`), cũng
không đụng Metro.

### `bind: address already in use` ở cổng 8082

Bot 5m host (`./run_smc_5.sh`) vẫn đang chạy. Xem §1.4.

### Giá trị trong `.env` có ký tự `$`

`.env` ở thư mục gốc vừa là `env_file` vừa là file nội suy biến của Compose. Secret chứa `$` có thể
bị hiểu là tham chiếu biến và bị cắt mất. Nếu API secret của sàn có `$`, viết thành `$$` hoặc bọc
trong nháy đơn, rồi kiểm chứng:

```bash
docker compose -f docker-compose.smc.yml run --rm freqtrade-smc env | grep FREQTRADE__EXCHANGE
```

## 7. Deploy lên server qua registry

Máy Mac build image → đẩy lên registry → server pull về chạy **cả hai bot**. Server **không cần
clone repo**: strategy và config đã bake trong image, server chỉ cần hai file là
`docker-compose.smc.deploy.yml` và `.env`.

Một image chở cả hai bot, nên deploy là nguyên tử: `pull` + `up -d` đưa 4h và 5m lên cùng một bản,
và rollback cũng kéo cả hai về cùng một bản. Không có cửa cho hai bot lệch phiên bản strategy.

| Thành phần | File |
|---|---|
| Script build & push | `deploy_smc.sh` |
| Compose bên server | `docker-compose.smc.deploy.yml` |

### 7.1 Hai điều bắt buộc phải biết trước

**Registry phải là private.** Image bake `config.json`, trong đó `api_server.password` và
`api_server.jwt_secret_key` có giá trị thật (§5). Đẩy lên registry công khai là công khai luôn hai
thứ đó. Trên server nhớ đè bằng `.env`:

```
FREQTRADE__API_SERVER__PASSWORD=<mật khẩu mới>
FREQTRADE__API_SERVER__JWT_SECRET_KEY=<chuỗi ngẫu nhiên mới>
```

**Kiến trúc CPU phải khớp server.** Mac Apple Silicon là `arm64`, VPS gần như luôn là `amd64`.
Build thẳng rồi push thì container trên server chết ngay với `exec format error`. `deploy_smc.sh`
mặc định build `linux/amd64` qua buildx. Bước cross-build này chạy giả lập QEMU nên **chậm hơn
build native nhiều** (lần đầu có thể 15–30 phút; các lần sau đã có cache thì nhanh).

Nếu chưa có QEMU cho amd64:

```bash
docker run --privileged --rm tonistiigi/binfmt --install amd64
```

Server là ARM (Ampere, Graviton…) thì đặt `SMC_PLATFORM=linux/arm64` trong `.env` — khi đó build
là native, rất nhanh.

### 7.2 Cấu hình một lần trên máy build

Thêm vào `.env` (xem `.env.example`):

```
SMC_REGISTRY=ghcr.io/<github-username>
SMC_IMAGE_NAME=freqtrade-smc
SMC_PLATFORM=linux/amd64
SMC_REGISTRY_USER=<github-username>
SMC_REGISTRY_TOKEN=<PAT có scope write:packages>
```

GHCR cần **Personal Access Token (classic)** với scope `write:packages` — mật khẩu GitHub không
đăng nhập được. Dùng Docker Hub thì đặt `SMC_REGISTRY=docker.io/<username>` và tạo repo ở chế độ
private trước.

```bash
chmod +x deploy_smc.sh
./deploy_smc.sh login
```

### 7.3 Build và đẩy

```bash
# (tuỳ chọn) build native, chạy thử tại chỗ trước khi đẩy
./deploy_smc.sh build
docker compose -f docker-compose.smc.yml up -d --build

# đẩy lên registry — script sẽ hỏi xác nhận registry là private
./deploy_smc.sh push
```

Tag sinh ra có dạng `20260807-142530-a66c0575c-dirty`: thời gian để sắp xếp, commit để truy ngược
mã nguồn, hậu tố `-dirty` khi working tree có thay đổi chưa commit. Hậu tố đó gần như luôn xuất
hiện và **đúng như vậy** — `config.json` và `config-4h.json` bị `.gitignore` chặn, nên riêng commit
hash không đủ định danh nội dung image. Script cũng đẩy kèm `:latest`.

Lần đầu đẩy lên GHCR, package mặc định là private. Vào GitHub → Packages → `freqtrade-smc` →
Package settings để kiểm tra lại, và thêm quyền đọc cho tài khoản server nếu server dùng PAT khác.

### 7.4 Chuẩn bị server — làm một lần

```bash
mkdir -p ~/freqtrade-smc/user_data/logs && cd ~/freqtrade-smc
```

Copy từ máy build sang:

```bash
scp docker-compose.smc.deploy.yml user@server:~/freqtrade-smc/
scp .env user@server:~/freqtrade-smc/          # rồi SỬA lại trên server, xem bên dưới
```

`.env` trên server phải khác `.env` máy build:

- **bỏ** các biến `SMC_REGISTRY*`, `SMC_PLATFORM` — chỉ máy build cần;
- **thêm** `SMC_IMAGE=ghcr.io/<ns>/freqtrade-smc:<tag>` — ghim tag cụ thể mà `push` vừa in ra;
- **đổi** `FREQTRADE__API_SERVER__PASSWORD` và `FREQTRADE__API_SERVER__JWT_SECRET_KEY`;
- **giữ** `FREQTRADE__TELEGRAM__TOKEN` và `BOT_5M_TG_TOKEN` — thiếu `BOT_5M_TG_TOKEN` thì compose
  từ chối chạy. Nếu bot host ở máy cũ vẫn đang bật, hai token này đang bị dùng ở đó: tắt bot cũ
  trước, nếu không server và máy cũ sẽ 409 Conflict lẫn nhau.

Container chạy bằng uid 1000, thư mục bind mount phải ghi được bởi uid đó:

```bash
sudo chown -R 1000:1000 user_data
docker login ghcr.io
```

Đang chuyển bot từ máy khác sang thì copy DB trade vào trước, sau khi đã **dừng bot cũ** để SQLite
flush `-wal` (cùng lý do §1.3):

```bash
scp user_data/tradesv3.4h-futures.sqlite* user@server:~/freqtrade-smc/user_data/
scp user_data/tradesv3.5m-futures.sqlite* user@server:~/freqtrade-smc/user_data/
```

### 7.5 Mỗi lần deploy

```bash
# trên máy build
./deploy_smc.sh push

# trên server — sửa SMC_IMAGE trong .env thành tag mới, rồi:
docker compose -f docker-compose.smc.deploy.yml pull
docker compose -f docker-compose.smc.deploy.yml up -d
docker compose -f docker-compose.smc.deploy.yml logs -f
```

`./deploy_smc.sh server` in ra đúng khối lệnh này kèm registry của bạn.

**Rollback:** đổi `SMC_IMAGE` về tag cũ, chạy lại `pull` + `up -d`. Đây là lý do nên ghim tag thay
vì `latest` — `latest` không rollback được vì không biết nó từng trỏ vào đâu.

### 7.6 Kiểm tra sau deploy

```bash
docker compose -f docker-compose.smc.deploy.yml ps        # CẢ HAI healthy sau ~90s
curl -fsS http://127.0.0.1:8091/api/v1/ping               # bot 4h, trên server
curl -fsS http://127.0.0.1:8082/api/v1/ping               # bot 5m, trên server
```

Và quan trọng nhất: gõ `/smc` trong Telegram **ở cả hai bot**. Healthcheck chỉ chạm REST API —
Telegram chết, hoặc hai bot đang 409 Conflict lẫn nhau, mà container vẫn "healthy" (§6).

Xem FreqUI từ máy nhà (cổng chỉ mở loopback trên server):

```bash
ssh -L 8091:127.0.0.1:8091 -L 8082:127.0.0.1:8082 user@server
# rồi mở http://127.0.0.1:8091 (4h) và http://127.0.0.1:8082 (5m)
```

### 7.7 Không muốn dùng registry

Đẩy thẳng image qua SSH, không cần registry nào cả — đổi lại là mỗi lần deploy phải truyền toàn bộ
image (~1 GB) thay vì chỉ layer thay đổi:

```bash
docker buildx build --platform linux/amd64 -f docker/Dockerfile.smc -t freqtrade-smc:deploy --load .
docker save freqtrade-smc:deploy | gzip | ssh user@server 'gunzip | docker load'
# trên server: đặt SMC_IMAGE=freqtrade-smc:deploy rồi up -d (bỏ qua bước pull)
```

## 8. Ngoài phạm vi

Backtesting, hyperopt và lookahead-analysis **chưa** được đóng gói. Vẫn chạy trên host như cũ:

```bash
./run_smc.sh backtesting --timerange 20260101-
./run_smc.sh hyperopt --hyperopt-loss SharpeHyperOptLoss --epochs 100
./run_smc_5.sh backtesting --timerange 20260701-   # khung 5m
```

Bot ICT (`./run_ict_m5.sh`) cũng chưa có bản Docker.

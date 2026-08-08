# SRS — Hệ thống Scalping BTC Perp (3 Combo Chiến lược)

| | |
|---|---|
| **Document ID** | SRS-BTCSCALP-001 |
| **Version** | 1.0 |
| **Ngày** | 2026-08-08 |
| **Thị trường** | BTCUSDT Perpetual (Binance USDⓈ-M) |
| **Trạng thái** | Draft — chưa validate out-of-sample |

---

## 1. Phạm vi & Mục tiêu

### 1.1 Mục tiêu
Đặc tả yêu cầu cho một hệ thống scalping BTC perpetual gồm 3 chiến lược độc lập, có thể triển khai dưới dạng backtest engine (Freqtrade) hoặc hệ thống thực thi bán tự động.

### 1.2 Trong phạm vi
- Định nghĩa tín hiệu vào lệnh (entry) cho cả LONG và SHORT
- Quy tắc đặt stop loss / take profit theo tỉ lệ R:R cố định **1 : 1.15**
- Bộ lọc ngữ cảnh (context filter) và guard toàn cục
- Yêu cầu dữ liệu và yêu cầu kiểm định

### 1.3 Ngoài phạm vi
- Thực thi lệnh tự động trên tài khoản thật (chỉ signal + paper trade ở v1.0)
- Quản lý danh mục đa tài sản
- Machine learning / model dự báo

### 1.4 Tuyên bố giới hạn
Tài liệu này là đặc tả kỹ thuật để **kiểm định giả thuyết**, không phải khuyến nghị đầu tư. Chưa có combo nào trong tài liệu được chứng minh có edge dương out-of-sample. Xem mục 10 (Validation) trước khi đưa vào vận hành vốn thật.

---

## 2. Định nghĩa & Thuật ngữ

| Ký hiệu | Ý nghĩa |
|---|---|
| **R** | Đơn vị rủi ro = khoảng cách từ entry đến stop loss (tính bằng giá) |
| **RR** | Reward:Risk ratio. Toàn hệ thống dùng cố định **1 : 1.15** |
| **VWAP** | Volume Weighted Average Price, anchor 00:00 UTC mỗi ngày |
| **σ band** | Dải lệch chuẩn của VWAP (±1σ, ±2σ) |
| **POC** | Point of Control — mức giá có volume lớn nhất trong profile |
| **VAH / VAL** | Value Area High / Low (70% volume của phiên) |
| **CVD** | Cumulative Volume Delta = Σ(aggressive buy volume − aggressive sell volume) |
| **OI** | Open Interest |
| **BB** | Bollinger Bands (20, 2) |
| **KC** | Keltner Channel (20, 1.5 × ATR) |
| **Cascade** | Chuỗi thanh lý bắt buộc (forced liquidation) |
| **s** | Stop distance, tính bằng % của giá entry |

---

## 3. Yêu cầu Dữ liệu (Data Requirements)

### DR-01 — Nguồn dữ liệu bắt buộc

| Loại | Nguồn | Khung | Ghi chú |
|---|---|---|---|
| OHLCV | Binance `/fapi/v1/klines` | 1m, 5m, 15m, 1D | Bắt buộc cho cả 3 combo |
| Aggregated trades | Binance `/fapi/v1/aggTrades` | tick | Bắt buộc để dựng CVD (Combo A, C) |
| Open Interest | `/futures/data/openInterestHist` | 5m | Bắt buộc Combo B, C |
| Funding rate | `/fapi/v1/fundingRate` | 8h | Bắt buộc Combo C |
| Liquidation | `/fapi/v1/allForceOrders` hoặc WS `!forceOrder@arr` | stream | Bắt buộc Combo C |

### DR-02 — Độ dài lịch sử tối thiểu
≥ 36 tháng dữ liệu 1m, bao phủ ít nhất một chu kỳ bull và một chu kỳ bear.

### DR-03 — Tính toàn vẹn
- Phát hiện và log gap dữ liệu > 2 nến liên tiếp; các cửa sổ chứa gap bị loại khỏi backtest.
- CVD phải dựng lại từ tick, **không** được xấp xỉ bằng `close > open` trên nến (sai lệch lớn ở khung 1m).

### DR-04 — Chống lookahead
Mọi indicator chỉ được dùng dữ liệu đã đóng nến. Volume Profile của phiên hôm trước chỉ khả dụng từ 00:00 UTC ngày hôm sau.

---

## 4. Mô hình Rủi ro Toàn cục

### RM-01 — Tỉ lệ R:R cố định
Mọi lệnh trong mọi combo dùng:

```
risk_distance = |entry − stop_loss|
take_profit   = entry + 1.15 × risk_distance   (LONG)
take_profit   = entry − 1.15 × risk_distance   (SHORT)
```

Không dùng partial TP, không trailing ở v1.0 (để cô lập biến số khi đo edge).

### RM-02 — Position sizing
```
position_size = (equity × risk_pct) / risk_distance
```
Mặc định `risk_pct = 0.5%`. Đòn bẩy là hệ quả của công thức, không phải tham số đầu vào.

### RM-03 — Winrate hòa vốn (RÀNG BUỘC QUAN TRỌNG)

Với RR 1:1.15, winrate hòa vốn **trước phí** là 46.5%. Sau phí, con số này tăng rất nhanh khi stop càng hẹp:

Gọi `f = chi_phí_khứ_hồi / s` (chi phí quy đổi ra đơn vị R), winrate hòa vốn = `(1 + f) / 2.15`

**Trường hợp taker cả hai chiều + slippage (c ≈ 0.10%):**

| Stop distance `s` | f | Winrate hòa vốn |
|---|---|---|
| 0.20% | 0.50 | **69.8%** |
| 0.30% | 0.33 | **62.0%** |
| 0.50% | 0.20 | **55.8%** |
| 0.80% | 0.125 | **52.3%** |

**Trường hợp maker entry + taker exit (c ≈ 0.05%):**

| Stop distance `s` | f | Winrate hòa vốn |
|---|---|---|
| 0.20% | 0.25 | **58.1%** |
| 0.30% | 0.167 | **54.3%** |
| 0.50% | 0.10 | **51.2%** |
| 0.80% | 0.0625 | **49.4%** |

### RM-04 — Ràng buộc bắt nguồn từ RM-03

- **RM-04a:** Stop distance tối thiểu `s ≥ 0.35%`. Setup có stop hẹp hơn bị **loại**, không co nhỏ TP để bù.
- **RM-04b:** Ưu tiên entry bằng limit (maker). Nếu limit không khớp trong 3 nến 1m → hủy setup, không đuổi giá bằng market.
- **RM-04c:** Backtest **phải** cấu hình phí thực tế. Chạy với phí = 0 là vô nghĩa và bị coi là kết quả không hợp lệ.

### RM-05 — Guard toàn cục

| ID | Quy tắc |
|---|---|
| G-01 | Tối đa 1 vị thế mở tại một thời điểm |
| G-02 | Tối đa 5 lệnh / ngày UTC |
| G-03 | Dừng giao dịch phần còn lại của ngày nếu lỗ lũy kế ≥ 2% equity |
| G-04 | Dừng hệ thống nếu drawdown ≥ 8% từ đỉnh equity → review thủ công |
| G-05 | Không vào lệnh trong khung ±5 phút quanh thời điểm settle funding (00:00 / 08:00 / 16:00 UTC) |
| G-06 | Không vào lệnh trong ±15 phút quanh sự kiện macro lịch cứng (CPI, FOMC, NFP) |
| G-07 | Timeout: đóng lệnh ở giá thị trường nếu không chạm TP/SL sau 45 nến 1m |

---

## 5. FR-A — Combo A: Auction Market Theory (VWAP + Volume Profile + CVD)

**Bản chất:** mean reversion. Giả thuyết là giá bị đẩy ra ngoài vùng giá trị bởi dòng lệnh thiếu hấp thụ, và có xu hướng quay lại.

### FR-A-00 — Điều kiện tiên quyết (Context Filter)
Chỉ kích hoạt Combo A khi ngày được phân loại **RANGE DAY**:
- `ATR(14, 15m) / price < 0.45%`, **VÀ**
- Giá đã giao dịch bên trong Value Area của phiên trước ≥ 60% thời lượng phiên hiện tại, **VÀ**
- `|VWAP_slope| < 0.1%/giờ`

Nếu không thỏa → Combo A OFF cho ngày đó.

### FR-A-L — Setup LONG

| Bước | Hành động | Điều kiện định lượng |
|---|---|---|
| **A-L1** | Xác định vùng kích hoạt | `trigger_zone = min(VAL_prev_day, VWAP − 2σ)` |
| **A-L2** | Chờ quét | Giá 1m tạo low `< trigger_zone` (wick tính là hợp lệ) |
| **A-L3** | Xác nhận hấp thụ | **CVD divergence:** trong 20 nến 1m gần nhất, price tạo lower low nhưng CVD tạo higher low. **HOẶC absorption:** delta bán của nến quét `< −P80(|delta|, 100 nến)` nhưng range nến `< 0.12%` |
| **A-L4** | Chờ hồi phục | Nến 1m đầu tiên đóng cửa **trên** `trigger_zone` |
| **A-L5** | Kiểm tra khả thi cấu trúc | Mục tiêu cấu trúc gần nhất (POC hoặc VWAP) phải **xa hơn** TP cố định. Nếu `|POC − entry| < 1.15 × risk_distance` → **BỎ QUA** setup |
| **A-L6** | Entry | Limit tại `close` của nến A-L4, hiệu lực 3 nến |
| **A-L7** | Stop loss | `SL = swing_low(A-L2) − 0.15 × ATR(14, 1m)` |
| **A-L8** | Kiểm tra RM-04a | Nếu `s < 0.35%` → **BỎ QUA** |
| **A-L9** | Take profit | `TP = entry + 1.15 × (entry − SL)` |

### FR-A-S — Setup SHORT (đối xứng)

| Bước | Hành động | Điều kiện định lượng |
|---|---|---|
| **A-S1** | Vùng kích hoạt | `trigger_zone = max(VAH_prev_day, VWAP + 2σ)` |
| **A-S2** | Chờ quét | Giá 1m tạo high `> trigger_zone` |
| **A-S3** | Xác nhận | **CVD divergence:** price higher high nhưng CVD lower high (20 nến). **HOẶC absorption:** delta mua `> P80` nhưng range `< 0.12%` |
| **A-S4** | Chờ hồi | Nến 1m đầu tiên đóng **dưới** `trigger_zone` |
| **A-S5** | Khả thi cấu trúc | Nếu `|POC − entry| < 1.15 × risk_distance` → **BỎ QUA** |
| **A-S6** | Entry | Limit tại `close` của nến A-S4, hiệu lực 3 nến |
| **A-S7** | Stop loss | `SL = swing_high(A-S2) + 0.15 × ATR(14, 1m)` |
| **A-S8** | Kiểm tra RM-04a | Nếu `s < 0.35%` → **BỎ QUA** |
| **A-S9** | Take profit | `TP = entry − 1.15 × (entry − SL)` |

### FR-A-X — Điều kiện hủy sớm
Đóng lệnh ngay nếu giá đóng cửa 2 nến 1m liên tiếp bên ngoài `trigger_zone` theo hướng bất lợi (setup đã sai, không chờ SL).

---

## 6. FR-B — Combo B: Volatility Breakout (Squeeze + OI)

**Bản chất:** momentum. Giả thuyết là biến động bị nén sẽ giải phóng theo hướng có dòng tiền mới.

### FR-B-00 — Điều kiện tiên quyết
- **Squeeze active:** `BB_upper(20,2) < KC_upper(20, 1.5×ATR)` **VÀ** `BB_lower > KC_lower` trên khung **5m**, duy trì ≥ 6 nến liên tiếp.
- **Blackout thanh khoản:** không vào lệnh trong khung 03:00–06:00 UTC (fake breakout tỉ lệ cao).

### FR-B-L — Setup LONG

| Bước | Hành động | Điều kiện định lượng |
|---|---|---|
| **B-L1** | Squeeze | Thỏa FR-B-00, ghi nhận `squeeze_high`, `squeeze_low` = high/low của toàn bộ vùng nén |
| **B-L2** | Lọc xu hướng | `EMA(50, 15m)` hiện tại `> EMA(50, 15m)` của 4 nến trước (slope dương) |
| **B-L3** | Breakout | Nến 5m đóng cửa `> KC_upper` |
| **B-L4** | Xác nhận volume | `volume(nến B-L3) ≥ 2.0 × SMA(volume, 20)` |
| **B-L5** | Xác nhận OI (tiền mới) | `ΔOI` trong 5m của nến breakout `≥ +0.3%`. Nếu OI **giảm** → đây là short-covering, **BỎ QUA** |
| **B-L6** | Entry | Limit tại `BB_upper` (retest), hiệu lực 4 nến 5m. Nếu giá chạy thẳng không retest → **BỎ QUA**, không đuổi |
| **B-L7** | Stop loss | `SL = max(squeeze_low, entry − 1.2 × ATR(14, 5m))` |
| **B-L8** | Kiểm tra RM-04a | Nếu `s < 0.35%` → **BỎ QUA** |
| **B-L9** | Take profit | `TP = entry + 1.15 × (entry − SL)` |

### FR-B-S — Setup SHORT (đối xứng)

| Bước | Hành động | Điều kiện định lượng |
|---|---|---|
| **B-S1** | Squeeze | Thỏa FR-B-00 |
| **B-S2** | Lọc xu hướng | `EMA(50, 15m)` slope âm |
| **B-S3** | Breakout | Nến 5m đóng cửa `< KC_lower` |
| **B-S4** | Volume | `volume ≥ 2.0 × SMA(volume, 20)` |
| **B-S5** | OI | `ΔOI ≥ +0.3%`. Nếu OI giảm → là long-liquidation, thuộc Combo C, **BỎ QUA** ở đây |
| **B-S6** | Entry | Limit tại `BB_lower` (retest), hiệu lực 4 nến |
| **B-S7** | Stop loss | `SL = min(squeeze_high, entry + 1.2 × ATR(14, 5m))` |
| **B-S8** | Kiểm tra RM-04a | Nếu `s < 0.35%` → **BỎ QUA** |
| **B-S9** | Take profit | `TP = entry − 1.15 × (entry − SL)` |

### FR-B-N — Ghi chú thiết kế (mâu thuẫn đã biết)
TP cố định 1.15R **cắt cụt** phần đuôi lợi nhuận vốn là nguồn edge chính của chiến lược breakout. Đặc tả này giữ 1.15R theo yêu cầu, nhưng **BT-05** (mục 10) yêu cầu chạy song song một biến thể trailing để đo chi phí cơ hội của ràng buộc này.

---

## 7. FR-C — Combo C: Derivatives Flow (Liquidation Cascade Fade)

**Bản chất:** fade sự kiện thanh lý. Giả thuyết là cascade tạo áp lực bán/mua cơ học không phản ánh dòng lệnh thật, và bị đảo lại khi cạn.

### FR-C-00 — Điều kiện tiên quyết
Đây là combo có tần suất thấp nhất và độ nhạy latency cao nhất. Yêu cầu WebSocket real-time, **không** chạy trên polling REST.

### FR-C-L — Setup LONG (fade long-liquidation cascade)

| Bước | Hành động | Điều kiện định lượng |
|---|---|---|
| **C-L1** | Phát hiện cascade | Trong cửa sổ trượt 5 phút: `ΔOI ≤ −1.5%` **VÀ** `Δprice ≤ −0.8%` **VÀ** tổng notional liquidation phía LONG `≥ P95` của phân phối 30 ngày |
| **C-L2** | Xác nhận cơ chế | Funding rate ước tính chuyển âm, **HOẶC** perp giao dịch discount so với index `≤ −0.05%` |
| **C-L3** | Ghi nhận đáy | `cascade_low` = low thấp nhất trong cửa sổ cascade |
| **C-L4** | Chờ cạn kiệt | Nến 1m đầu tiên thỏa **cả hai**: delta (CVD 1m) `> 0` **VÀ** `close > (high + low) / 2` |
| **C-L5** | Giới hạn thời gian | Nếu không có nến thỏa C-L4 trong 10 nến sau cascade → **HỦY** setup |
| **C-L6** | Entry | Market tại `close` của nến C-L4 (combo này chấp nhận taker vì tính thời điểm) |
| **C-L7** | Stop loss | `SL = cascade_low − 0.15 × ATR(14, 1m)` |
| **C-L8** | Kiểm tra RM-04a | Nếu `s < 0.35%` → **BỎ QUA**. Nếu `s > 1.5%` → **BỎ QUA** (cascade quá rộng, rủi ro đuôi) |
| **C-L9** | Take profit | `TP = entry + 1.15 × (entry − SL)` |

### FR-C-S — Setup SHORT (fade short-squeeze cascade)

| Bước | Hành động | Điều kiện định lượng |
|---|---|---|
| **C-S1** | Phát hiện | `ΔOI ≤ −1.5%` **VÀ** `Δprice ≥ +0.8%` trong 5 phút **VÀ** liquidation phía SHORT `≥ P95` |
| **C-S2** | Xác nhận | Funding chuyển dương mạnh, **HOẶC** perp premium `≥ +0.05%` |
| **C-S3** | Ghi nhận đỉnh | `cascade_high` = high cao nhất trong cửa sổ |
| **C-S4** | Chờ cạn kiệt | Nến 1m đầu tiên có delta `< 0` **VÀ** `close < (high + low) / 2` |
| **C-S5** | Giới hạn thời gian | Không thỏa trong 10 nến → **HỦY** |
| **C-S6** | Entry | Market tại `close` của nến C-S4 |
| **C-S7** | Stop loss | `SL = cascade_high + 0.15 × ATR(14, 1m)` |
| **C-S8** | Kiểm tra RM-04a | `0.35% ≤ s ≤ 1.5%`, ngoài khoảng → **BỎ QUA** |
| **C-S9** | Take profit | `TP = entry − 1.15 × (entry − SL)` |

### FR-C-X — Guard riêng
- **C-X1:** Tối đa 2 lệnh Combo C / ngày. Cascade thường đi thành chuỗi; lệnh thứ 3 trở đi là đang bắt dao rơi.
- **C-X2:** Nếu cascade thứ hai xảy ra khi đang có vị thế Combo C mở → đóng vị thế ngay ở giá thị trường.

---

## 8. Ma trận Ưu tiên & Xung đột

Khi nhiều combo phát tín hiệu cùng lúc:

| Tình huống | Xử lý |
|---|---|
| A và B cùng tín hiệu | Loại trừ lẫn nhau theo thiết kế (A yêu cầu RANGE DAY, B yêu cầu squeeze breakout). Nếu vẫn xảy ra → **bỏ cả hai**, ghi log để review logic phân loại ngày |
| C và B ngược hướng | **C thắng** — cascade là sự kiện cơ học, breakout signal trong cascade không đáng tin |
| C và A cùng hướng | Chọn setup có `s` lớn hơn (chi phí/R thấp hơn theo RM-03) |
| Bất kỳ combo nào khi đã có vị thế mở | **Bỏ qua** (G-01) |

---

## 9. Yêu cầu Phi chức năng

| ID | Yêu cầu |
|---|---|
| NFR-01 | Độ trễ từ nến đóng đến quyết định signal: ≤ 200ms (Combo C), ≤ 1s (Combo A, B) |
| NFR-02 | Mọi lệnh phải được ghi log đầy đủ: timestamp, combo ID, tất cả giá trị điều kiện tại thời điểm entry, entry/SL/TP, lý do exit |
| NFR-03 | Hệ thống phải chạy được ở chế độ replay trên dữ liệu lịch sử với cùng code path như live (không có nhánh `if backtest`) |
| NFR-04 | Mất kết nối WebSocket > 30s → chuyển sang chế độ chỉ-đóng-lệnh, không mở lệnh mới |
| NFR-05 | Tất cả tham số ngưỡng (2.0× volume, 0.3% ΔOI, 1.5% cascade...) phải nằm trong file config, không hardcode |

---

## 10. Yêu cầu Kiểm định (Validation Requirements)

Đây là mục quan trọng nhất của tài liệu. Không combo nào được đưa vào vốn thật nếu chưa qua toàn bộ mục này.

| ID | Yêu cầu |
|---|---|
| **BT-01** | **Walk-forward bắt buộc.** Cửa sổ optimize 6 tháng → test out-of-sample 2 tháng → trượt tới. Chỉ đọc kết quả từ các đoạn OOS ghép lại. Backtest một cục toàn bộ lịch sử là kết quả **không hợp lệ** |
| **BT-02** | **Giới hạn bậc tự do.** Tối đa 4 tham số được hyperopt cho mỗi combo. Mỗi tham số thêm vào là một chiều để overfit |
| **BT-03** | **Phí thực tế.** Taker 0.05%, maker 0.02%, slippage 0.02% cho khung 1m (xác minh lại biểu phí hiện hành trước khi chạy). Cộng cả funding cho lệnh giữ qua mốc settle |
| **BT-04** | **Benchmark bắt buộc.** So với (a) buy-and-hold, (b) random entry cùng tần suất với cùng SL/TP. Nếu không đánh bại (b), edge không nằm ở logic entry |
| **BT-05** | Chạy biến thể trailing exit song song để đo chi phí cơ hội của TP cố định 1.15R (đặc biệt cho Combo B, xem FR-B-N) |
| **BT-06** | **Metric chấp nhận:** Sharpe OOS > 1.0, max drawdown < 15%, profit factor > 1.2 **sau phí**. Winrate và total return không được dùng làm tiêu chí quyết định |
| **BT-07** | **Kiểm tra ràng buộc RM-03.** Nếu winrate OOS thực tế thấp hơn ngưỡng hòa vốn tương ứng với `s` trung bình của combo → combo bị **loại**, không tinh chỉnh thêm |
| **BT-08** | Paper trade tối thiểu 60 ngày sau khi qua BT-01→BT-07, trước khi cấp vốn |

---

## 11. Rủi ro & Giả định Đã biết

| ID | Nội dung |
|---|---|
| **RISK-01** | RR 1:1.15 kết hợp scalping khung ngắn đòi hỏi winrate 52–62% sau phí (RM-03). Đây là ngưỡng cao. Nếu backtest cho winrate ~70%, khả năng cao là overfit hoặc lookahead bias, không phải edge |
| **RISK-02** | TP cố định mâu thuẫn với bản chất của Combo B (breakout sống nhờ đuôi lợi nhuận). Combo B có thể thất bại BT-06 **vì ràng buộc RR**, chứ không phải vì logic sai |
| **RISK-03** | Literature học thuật về technical trading rules trên BTC cho thấy chiến lược có lãi in-sample thường không giữ được hiệu quả out-of-sample, và lợi nhuận chủ yếu đến từ việc chọn tham số hơn là từ market inefficiency thật. BT-01 và BT-02 tồn tại để đối phó với điều này |
| **RISK-04** | Combo C phụ thuộc dữ liệu OI/liquidation vốn chỉ có từ ~2019 và không đồng nhất giữa các sàn. Dữ liệu liquidation của Binance qua WebSocket bị throttle (không phát mọi sự kiện), nên ngưỡng P95 ở C-L1 là xấp xỉ, không phải giá trị thật |
| **RISK-05** | Volume Profile chuẩn cần tick data. Nếu xấp xỉ bằng volume-by-price từ nến 1m, VAH/VAL/POC sẽ lệch — cần đo mức lệch này trước khi tin vào Combo A |
| **RISK-06** | Cả 3 combo đều chưa được kiểm định. Xác suất tiên nghiệm hợp lý là **không combo nào** sống sót qua BT-06. Đây là kết quả bình thường, không phải thất bại của quá trình |

---

## 12. Thứ tự Triển khai Đề xuất

1. **Combo B trước** — dễ code nhất (toàn indicator có sẵn), và là bộ duy nhất có bằng chứng học thuật gián tiếp ủng hộ (trading range breakout cho Sharpe cao hơn buy-and-hold trên dữ liệu BTC).
2. **Combo C thứ hai** — nếu B cho kết quả khả quan, đầu tư công sức pull và merge dữ liệu OI/funding/liquidation.
3. **Combo A cuối** — tốn công nhất do yêu cầu tick data cho Volume Profile và CVD.

---

*Hết tài liệu. Version 1.0 — cần review lại sau khi có kết quả BT-01 cho Combo B.*

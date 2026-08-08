# Báo cáo Backtest — 3 Combo Scalping BTC Perp

| | |
|---|---|
| **Đặc tả nguồn** | [SRS-BTCSCALP-001](SRS-BTC-Scalping-Combos.md) v1.0 |
| **Ngày chạy** | 2026-08-08 |
| **Dữ liệu** | BTC/USDT:USDT perp (Binance USDⓈ-M), khung 5m + chi tiết trong nến 1m |
| **Giai đoạn** | 2024-01-02 → 2026-08-07 — 947 ngày ≈ 31 tháng |
| **Buy & hold cùng kỳ** | **+40.4%** |
| **Tham số** | Toàn bộ để MẶC ĐỊNH theo SRS. Không hyperopt, không fit gì cả. |

> **Kết luận: cả 3 combo TRƯỢT BT-06 và BT-07, và không combo nào đánh bại được mốc
> random-entry của BT-04(b).** Không combo nào nên đi tiếp sang BT-08 (paper trade).
> Đây đúng là kịch bản mà RISK-06 đã dự báo.
>
> **Cập nhật §6 — đã thử RR 1:2 và nó KHÔNG cứu được combo nào.** Khuyến nghị "nâng RR"
> ở §4.2 của chính báo cáo này đã bị kiểm định và **bác bỏ**.
>
> **Cập nhật §7 — đã hyperopt cả RR lẫn tham số tới khi PnL dương.** Đạt: cả ba combo
> dương trong cửa sổ fit, `ComboCOpt` dương +8.93% trên toàn kỳ. Nhưng gross expectancy
> của **cả ba** lật dấu khi sang 12 tháng holdout. Đọc §7.4 trước khi dùng bất kỳ bộ
> tham số nào.

---

## 0. Đọc trước: những gì báo cáo này KHÔNG chứng minh được

Sáu yêu cầu của SRS không kiểm định được vì thiếu dữ liệu, và không có cách nào lách:

| Yêu cầu | Combo | Vì sao không chạy được | Xử lý |
|---|---|---|---|
| ΔOI ≥ +0.3% (B-L5) | B | `openInterestHist` của Binance chỉ trả 30 ngày gần nhất; backtest cần 31 tháng | **Bỏ cổng**, không thay proxy |
| ΔOI ≤ −1.5% (C-L1) | C | như trên | **Bỏ cổng** |
| Liquidation ≥ P95 30 ngày (C-L1) | C | `allForceOrders` không còn phát hành lịch sử; WS bị throttle (RISK-04) | **Proxy volume**: volume 5' ≥ P95 theo ngày, trung bình trượt 30 ngày, dịch 1 ngày |
| Funding ước tính / perp discount (C-L2) | C | kho chỉ có funding **đã thanh toán** mỗi 8h, không phải giá trị ước tính real-time | **Bỏ cổng** |
| CVD từ aggTrades (DR-03) | A, C | không có tick data | **Xấp xỉ từ nến**: `volume × (2·(close−low)/(high−low) − 1)` |
| Volume Profile từ tick (RISK-05) | A | như trên | Volume-by-price từ nến 5m, 40 bin. Độ lệch **chưa đo được** |
| Blackout macro CPI/FOMC/NFP (G-06) | tất cả | repo không có lịch sự kiện | Không hiện thực |

**Hệ quả nặng nhất — Combo C:** sau khi bỏ ΔOI và funding, thứ được kiểm định là *"fade
một cú sập nhanh có volume lớn"*, **không phải** *"fade một cascade thanh lý"*. Số của
Combo C chỉ có giá trị **sàng lọc** (bản dễ này đã âm nặng thì bản thật khó cứu), tuyệt
đối không dùng để tuyên bố FR-C đạt hay trượt BT-06.

**G-04** (dừng hệ thống khi DD ≥ 8%) cố ý **không** bật: đó là quy trình vận hành thủ
công, bật trong backtest sẽ cắt cụt mẫu và làm đẹp con số drawdown một cách giả tạo.

---

## 1. Bảng kết quả

Đã trừ phí theo BT-03 — A/B: 0.09% khứ hồi (maker entry 0.02% + taker exit 0.05% +
slippage 0.02%); C: 0.14% (taker cả hai chiều, vì C-L6 vào bằng market).

| | **Combo A**<br>Auction | **Combo B**<br>Squeeze | **Combo C**<br>Cascade (proxy) | Ngưỡng BT-06 |
|---|---|---|---|---|
| Số lệnh | 45 | 64 | 311 | |
| Lệnh / tháng | 1.4 | 2.1 | 10.0 | |
| Winrate | 51.1% | 40.6% | 42.1% | *không dùng làm tiêu chí* |
| Stop `s` trung vị | 0.421% | 0.363% | 0.663% | |
| `f` = chi phí / R **(đo được)** | 0.205 R | 0.228 R | 0.222 R | |
| **Winrate hoà vốn (RM-03)** | **56.0%** | **57.1%** | **56.8%** | |
| Kỳ vọng **gross** (chưa phí) | +0.079 R | −0.076 R | −0.002 R | |
| Kỳ vọng **net** / lệnh | **−0.126 R** | **−0.304 R** | **−0.224 R** | |
| Tổng R (net) | −5.7 R | −19.5 R | −69.7 R | |
| **Profit factor** | **0.70** | **0.58** | **0.61** | **> 1.2** ✗ |
| Tổng lợi nhuận | −2.78% | −8.23% | −29.52% | |
| CAGR | −1.08% | −3.26% | −12.62% | |
| **Max drawdown** | **4.44%** | **9.66%** | **29.52%** | **< 15%** ✗ (chỉ C) |
| **Sharpe** | **−0.14** | **−0.34** | **−1.41** | **> 1.0** ✗ |
| **BT-06** | TRƯỢT | TRƯỢT | TRƯỢT | |
| **BT-07** | TRƯỢT (thiếu 4.9 điểm) | TRƯỢT (thiếu 16.5) | TRƯỢT (thiếu 14.7) | |

Combo A và B có max drawdown *dưới* 15%, nhưng đó không phải điểm cộng — nó chỉ phản
ánh tần suất giao dịch quá thấp (1.4 và 2.1 lệnh/tháng). Cả hai vẫn trượt BT-06 ở Sharpe
và profit factor.

---

## 2. Phát hiện chính

### 2.1. RM-03 không phải cảnh báo lý thuyết — nó là nguyên nhân trực tiếp

SRS §4 dự đoán winrate hoà vốn 52–62% sau phí. Đo trên dữ liệu thật:

| | `s` trung vị | `f` = phí/R đo được | Winrate hoà vốn | Winrate thực | Chênh |
|---|---|---|---|---|---|
| Combo A | 0.421% | 0.205 | 56.0% | 51.1% | −4.9 |
| Combo B | 0.363% | 0.228 | 57.1% | 40.6% | −16.5 |
| Combo C | 0.663% | 0.222 | 56.8% | 42.1% | −14.7 |

`f` đo được (0.205–0.228 R) khớp gần như chính xác với bảng RM-03. Nói cách khác: **cứ
mỗi lệnh, phí ăn mất hơn 1/5 đơn vị rủi ro**, và với RR 1:1.15 thì phải thắng ~57% mới
hoà. Không combo nào tới gần.

Điều này *đã nằm sẵn trong SRS* — RM-04a đặt sàn `s ≥ 0.35%` chính là để chống chuyện
này. Nhưng sàn 0.35% vẫn quá hẹp: ở `s = 0.35%` thì `f = 0.09/0.35 = 0.26 R`, kéo
winrate hoà vốn lên gần 59%.

### 2.2. Không combo nào đánh bại random entry (BT-04b) — đây mới là kết luận nặng nhất

Mốc so sánh: vào lệnh ngẫu nhiên (long/short 50-50), **cùng tần suất, cùng phân phối
`s`, cùng TP 1.15R, cùng toàn bộ guard G-01…G-07**. Khác biệt duy nhất là logic vào
lệnh — tức đúng phần nội dung của FR-A/B/C. Mỗi mốc chạy 5 seed để có độ phân tán, vì
một mẫu random đơn lẻ nói lên rất ít (sai số chuẩn ~0.16 R với n=45).

| | Combo (net) | Random 5 seed: TB ± sd | Khoảng | Vị trí combo |
|---|---|---|---|---|
| A | −0.126 R | **−0.100 ± 0.068 R** | −0.160 … +0.008 | −0.4 sd |
| B | −0.304 R | **−0.220 ± 0.131 R** | −0.360 … −0.026 | −0.6 sd |
| C | −0.224 R | **−0.202 ± 0.034 R** | −0.240 … −0.167 | −0.6 sd |

Và so cả phần **gross** (bỏ phí ra, để tách riêng chất lượng của tín hiệu):

| | Combo (gross) | Random gross: TB ± sd |
|---|---|---|
| A | +0.079 R | +0.089 ± 0.073 R |
| B | −0.076 R | −0.020 ± 0.130 R |
| C | −0.002 R | +0.015 ± 0.038 R |

**Cả ba combo nằm trong 1 độ lệch chuẩn của random, và cả ba đều lệch về phía XẤU
HƠN.** Kết luận đọc thẳng theo BT-04: phần "logic vào lệnh" của cả ba đặc tả không đóng
góp gì đo được. Toàn bộ kết quả (dù âm) đến từ khung RM-01…RM-04 + G-01…G-07, mà khung
đó thì bản random cũng dùng y hệt.

Đây là kết luận mạnh hơn "ba combo thua lỗ" rất nhiều: nếu chỉ thua vì phí thì hạ phí
hoặc nới RR là cứu được. Nhưng gross ≈ 0 nghĩa là **không có gì để cứu** — tín hiệu
không mang thông tin.

### 2.3. Combo A: RM-04a loại 92% số setup

Đếm trên toàn bộ 273,376 nến 5m, phễu của nhánh LONG:

| Bước | Số nến qua được | Còn lại |
|---|---|---|
| Tổng | 273,376 | 100% |
| FR-A-00 RANGE DAY (cả 3 điều kiện) | 79,618 | 29.1% |
| A-L2 quét dưới `trigger_zone` | 8,276 | 3.0% |
| A-L4 nến đầu tiên đóng lại trên vùng | 4,441 | 1.6% |
| ∩ RANGE DAY | 641 | 0.23% |
| A-L3 xác nhận CVD / absorption | 435 | 0.16% |
| A-L5 khả thi cấu trúc (POC đủ xa) | 389 | 0.14% |
| **A-L8 = RM-04a (`s ≥ 0.35%`)** | **33** | **0.012%** |

Bước cuối cắt **92%** những gì còn sống. Lý do có tính cấu trúc chứ không phải chỉnh
tham số được: entry của A-L6 nằm ngay sát mép trên của `trigger_zone`, còn SL của A-L7
nằm ngay dưới đáy vừa quét — hai mức đó theo thiết kế là gần nhau. Trung vị `s` của toàn
bộ 4,441 setup A-L4 chỉ là **0.229%**, tức phần lớn setup của Combo A *về bản chất* nằm
dưới sàn RM-04a.

Nói cách khác: **mô hình rủi ro của SRS và logic entry của Combo A mâu thuẫn nhau ngay
từ thiết kế**. 45 lệnh chạy được là phần đuôi bất thường của phân phối, không phải setup
điển hình mà FR-A mô tả.

### 2.4. Combo B: hỏng đều theo thời gian, và TP cố định chỉ là một phần lý do

Kỳ vọng net theo năm — xấu dần đơn điệu, không phải một giai đoạn xấu kéo cả kỳ xuống:

| | 2024 | 2025 | 2026 (tới 08) |
|---|---|---|---|
| Combo A | +0.031 R (n=19) | −0.408 R (n=16) | +0.029 R (n=10) |
| Combo B | −0.193 R (n=35) | −0.387 R (n=19) | −0.536 R (n=10) |
| Combo C | −0.186 R (n=146) | −0.123 R (n=90) | −0.421 R (n=75) |

**BT-05 — biến thể trailing** (bỏ TP cố định, sau 1R thì stop bám cách đỉnh 1R):

| | Combo B gốc | Combo B trailing |
|---|---|---|
| Số lệnh | 64 | 65 |
| Winrate | 40.6% | 40.0% |
| Profit factor | 0.58 | **0.72** |
| Tổng lợi nhuận | −8.23% | **−5.47%** |
| Max drawdown | 9.66% | 8.65% |

Trailing **đỡ hơn thật** — đúng như FR-B-N và RISK-02 dự đoán, TP 1.15R có cắt cụt đuôi
lợi nhuận. Nhưng mức cải thiện (PF 0.58 → 0.72) còn xa mới tới ngưỡng 1.2 của BT-06.
**RISK-02 được trả lời: Combo B trượt KHÔNG PHẢI (chỉ) vì ràng buộc RR.** Bỏ hẳn ràng
buộc đó ra thì nó vẫn lỗ.

★ Confound phải khai báo: bản trailing đồng thời nới G-07 từ 45 phút lên 8 giờ (giữ 45
phút thì không đuôi nào kịp hình thành, phép đo sẽ vô nghĩa). Chênh lệch quan sát được
là hợp của hai thay đổi, không quy hết cho một mình việc bỏ TP.

### 2.5. Chiều SHORT xấu hơn chiều LONG ở cả ba combo

| | LONG | SHORT |
|---|---|---|
| Combo A | n=28, −0.037 R | n=17, −0.273 R |
| Combo B | n=25, −0.270 R | n=39, −0.326 R |
| Combo C | n=150, −0.161 R | n=161, −0.283 R |

Giai đoạn 2024-01→2026-08 là thị trường tăng (+40.4%), nên đây nhiều khả năng là hiệu
ứng drift chứ không phải khuyết tật của logic short. Không đủ mẫu để tách hai giả thuyết
đó; **không** nên rút ra "bỏ chiều short" từ bảng này.

### 2.6. Chạy lại A và C ở khung 1m (đúng câu chữ SRS) — tệ hơn, đúng như RM-03 dự đoán

FR-A và FR-C viết theo nến **1m**; bảng chính ở §1 chạy 5m để so cùng khung với Combo B.
Chạy lại đúng khung gốc:

| | Combo A @5m | **Combo A @1m** | Combo C @5m | **Combo C @1m** |
|---|---|---|---|---|
| Số lệnh | 45 | 35 | 311 | 606 |
| Winrate | 51.1% | **31.4%** | 42.1% | 39.4% |
| `s` trung vị | 0.421% | 0.417% | 0.663% | **0.456%** |
| `f` chi phí/R | 0.205 | 0.205 | 0.222 | **0.286** |
| Winrate hoà vốn | 56.0% | 56.0% | 56.8% | **59.8%** |
| Kỳ vọng net | −0.126 R | **−0.369 R** | −0.224 R | **−0.300 R** |
| Profit factor | 0.70 | 0.33 | 0.61 | 0.55 |
| Tổng lợi nhuận | −2.78% | −6.19% | −29.52% | **−59.54%** |
| Max drawdown | 4.44% | 6.60% | 29.52% | **60.18%** |

Combo C ở 1m minh hoạ trực tiếp cơ chế của RM-03: nến ngắn hơn → cascade nhận diện sớm
hơn → `s` hẹp lại từ 0.663% xuống 0.456% → `f` nhảy từ 0.222 lên 0.286 R → ngưỡng hoà
vốn lên gần 60%. Số lệnh gấp đôi, mỗi lệnh lỗ nặng hơn, kết quả gấp đôi mức lỗ.

Nói gọn: **hạ khung không giúp gì; nó chỉ làm phí chiếm tỉ trọng lớn hơn trong mỗi R.**

---

## 3. Đối chiếu từng yêu cầu kiểm định (§10 của SRS)

| ID | Trạng thái | Ghi chú |
|---|---|---|
| **BT-01** Walk-forward | **Không cần thiết ở vòng này** | WF tồn tại để chống overfit khi có hyperopt. Ở đây **không tham số nào được fit** — tất cả để mặc định theo SRS, tức 0 bậc tự do. Kết quả toàn kỳ vì thế đã là out-of-sample theo nghĩa chặt hơn WF của một model đã fit. WF chỉ đáng làm nếu một combo có gross expectancy dương rõ rệt; không combo nào có (§2.2). Bảng theo năm ở §2.4 là bản thay thế rẻ tiền để kiểm tra tính ổn định. |
| **DR-04** Chống lookahead | **ĐẠT (cả 3)** | `freqtrade lookahead-analysis` trên 2025-01→2025-09: `has_bias = No`, 0 tín hiệu entry/exit lệch, 0 indicator lệch cho cả ba. ★ Chỉ 9 tín hiệu mỗi combo lọt vào cửa sổ kiểm — đủ để loại lỗi lookahead thô, chưa đủ để loại lỗi tinh vi. |
| **BT-02** ≤ 4 tham số hyperopt | **ĐẠT** | Mỗi combo khai báo đúng 4 tham số `optimize=True`; phần còn lại là hằng số SRS. |
| **BT-03** Phí thực tế | **ĐẠT** | A/B 0.09% khứ hồi, C 0.14%. `f` đo được sau đó khớp bảng RM-03. |
| **BT-04a** vs buy & hold | **TRƯỢT (cả 3)** | B&H +40.4% so với −2.8% / −8.2% / −29.5%. |
| **BT-04b** vs random entry | **TRƯỢT (cả 3)** | Xem §2.2 — cả ba nằm trong 1 sd của random và đều lệch về phía xấu hơn. |
| **BT-05** Biến thể trailing | **ĐÃ CHẠY** | Xem §2.4. Có cải thiện, không đủ. |
| **BT-06** Sharpe > 1.0, DD < 15%, PF > 1.2 | **TRƯỢT (cả 3)** | Không combo nào đạt dù chỉ một trong ba tiêu chí Sharpe/PF. |
| **BT-07** Winrate ≥ ngưỡng RM-03 | **TRƯỢT (cả 3)** | Thiếu 4.9 / 16.5 / 14.7 điểm phần trăm. Theo đúng câu chữ BT-07: **combo bị loại, không tinh chỉnh thêm.** |
| **BT-08** Paper trade 60 ngày | **Không áp dụng** | Điều kiện tiên quyết (BT-01→BT-07) không đạt. |

---

## 4. Khuyến nghị

1. **Không đưa combo nào sang paper trade.** BT-07 nói rõ: winrate dưới ngưỡng hoà vốn
   thì loại, không tinh chỉnh. Cả ba đều dưới.

2. **Vấn đề gốc là RR 1:1.15, không phải ba logic entry.** Với `s` khả dĩ trên khung 5m
   (0.36–0.66%), phí chiếm 0.20–0.23 R mỗi lệnh, đẩy winrate hoà vốn lên ~57%. Muốn cứu
   hướng scalping này thì phải đổi một trong ba thứ, theo thứ tự dễ đo:
   - **Nâng RR** lên 1:2 trở lên (hoà vốn tụt về ~40% kể cả sau phí), chấp nhận winrate
     thấp hơn — đây cũng chính là ngưỡng `min_rr` mà bot 4h đang dùng;
   - **Nới `s`** lên ≥ 1% để `f` tụt xuống dưới 0.1 R — đồng nghĩa bỏ nhãn "scalping";
   - **Bỏ taker exit**: TP bằng lệnh chờ maker cắt được ~0.03%/lệnh, tương đương ~0.08 R.
     Đáng làm nhưng một mình nó không lấp được khoảng cách 5–16 điểm winrate.

3. **Nếu vẫn muốn theo đuổi Combo A**, phải sửa mâu thuẫn ở §2.3 trước: hoặc dời entry
   xa `trigger_zone` hơn (vào sâu hơn trong vùng, R lớn hơn), hoặc đặt SL theo cấu trúc
   rộng hơn thay vì bám sát đáy quét. Chạy lại bản hiện tại với tham số khác chỉ là dò
   nhiễu trên 33 setup.

4. **Combo C chưa thực sự được kiểm định.** Muốn kết luận thật thì phải có dữ liệu OI và
   liquidation. Chi phí thu thập là đáng kể (RISK-04 đã ghi nhận dữ liệu WS bị throttle),
   và §2.2 cho thấy bản proxy không hề gợi ý là có gì đáng đào — nên đây là khoản đầu tư
   **nên hoãn**, không phải nên làm tiếp.

5. **Một ghi chú vận hành, không liên quan tới edge:** `initial_stop_loss_abs` của mọi
   lệnh đều ghi trần −99%, tức tại đúng thời điểm khớp chưa có stop bảo vệ nào; SL cấu
   trúc chỉ được đặt ở lần đánh giá kế tiếp. Trong backtest điều này vô hại (đã kiểm:
   mọi lệnh dính SL đều thoát đúng giá `stop_loss_abs`), nhưng chạy thật với
   `stoploss_on_exchange = false` thì có một khe hở thật. Bật `stoploss_on_exchange`
   trước khi cho bất kỳ combo nào chạm vốn thật.

---

## 5. Tái lập

```bash
# Dữ liệu (1m dùng cho --timeframe-detail; 5m/15m đã có sẵn trong repo)
freqtrade download-data --config config.json --exchange binance --trading-mode futures \
    --pairs "BTC/USDT:USDT" --timeframes 1m 5m 15m --timerange 20240101-

# Ba combo
freqtrade backtesting --config config-scalp-bt.json --strategy ComboAAuction \
    --timerange 20240101-20260807 --timeframe-detail 1m
freqtrade backtesting --config config-scalp-bt.json --strategy ComboBSqueeze \
    --timerange 20240101-20260807 --timeframe-detail 1m
freqtrade backtesting --config config-scalp-bt.json --config config-scalp-bt-C.json \
    --strategy ComboCCascade --timerange 20240101-20260807 --timeframe-detail 1m

# BT-05 — biến thể trailing của Combo B
freqtrade backtesting --config config-scalp-bt.json --strategy ComboBTrail \
    --timerange 20240101-20260807 --timeframe-detail 1m

# BT-04b — mốc random, 5 seed mỗi nhóm (một lượt, dùng chung lần nạp dữ liệu)
freqtrade backtesting --config config-scalp-bt.json --timerange 20240101-20260807 \
    --timeframe-detail 1m --strategy-list RandA1 RandA2 RandA3 RandA4 RandA5 \
                                          RandB1 RandB2 RandB3 RandB4 RandB5
freqtrade backtesting --config config-scalp-bt.json --config config-scalp-bt-C.json \
    --timerange 20240101-20260807 --timeframe-detail 1m \
    --strategy-list RandC1 RandC2 RandC3 RandC4 RandC5
```

### File đã thêm

| File | Vai trò |
|---|---|
| `strategies/scalp_combo_base.py` | §4 của SRS: RM-01…RM-04, G-01/02/03/05/07, sizing theo rủi ro, SL/TP đóng băng lúc khớp |
| `strategies/ComboAAuction.py` | FR-A — VWAP + Volume Profile + CVD |
| `strategies/ComboBSqueeze.py` | FR-B — Squeeze breakout |
| `strategies/ComboCCascade.py` | FR-C — Cascade fade (**bản proxy**, xem §0) |
| `strategies/ComboBTrail.py` | BT-05 — biến thể trailing của Combo B |
| `strategies/ComboRandomBench.py` | BT-04b — mốc random-entry (`RandBenchA/B/C`) |
| `strategies/ComboRandomSeeds.py` | BT-04b — 15 lớp con đổi seed để lấy phân phối |
| `config-scalp-bt.json` | Config backtest chung — ★ **`.gitignore` chặn `config*.json`, file này chỉ nằm ở máy local** |
| `config-scalp-bt-C.json` | Overlay cho Combo C — ★ cũng bị gitignore |

Hai config không vào git. Khoá quan trọng nếu phải dựng lại:

```jsonc
// config-scalp-bt.json
"max_open_trades": 1,          // G-01
"stake_amount": "unlimited",   // kích thước thật do custom_stake_amount quyết (RM-02)
"dry_run_wallet": 10000,
"trading_mode": "futures", "margin_mode": "isolated", "timeframe": "5m",
"fee": 0.00045,                // BT-03: 0.09% khứ hồi
"unfilledtimeout": {"entry": 1440, "exit": 10, "unit": "minutes"},
// ★ để RỘNG có chủ đích: ft_check_timed_out() kiểm khoá này TRƯỚC check_entry_timeout,
//   đặt 10 phút mặc định thì luật hiệu lực-N-nến của RM-04b không bao giờ được chạy.
// ★ KHÔNG đặt minimal_roi / stoploss: hai khoá đó trong config THẮNG strategy, mà toàn
//   bộ luật thoát nằm ở custom_exit / custom_stoploss.

// config-scalp-bt-C.json (overlay)
"entry_pricing": {"price_side": "other", ...},  // bắt buộc khi order_types.entry=market
"exit_pricing":  {"price_side": "other", ...},
"fee": 0.0007                  // BT-03 đường taker hai chiều: 0.14% khứ hồi
```

Mọi cửa sổ thời gian trong code khai báo bằng **phút** rồi quy ra số nến theo
`self.timeframe`, nên cùng một file chạy đúng ở cả 1m lẫn 5m — thêm `--timeframe 1m` là
có bản sát đặc tả gốc của Combo A và C (SRS viết theo nến 1m).

---

## 6. Vòng 2 — thử RR 1:2 (2026-08-08, sau khi có §1–§5)

§4.2 khuyến nghị nâng RR lên 1:2 vì ngưỡng hoà vốn RM-03 sẽ tụt từ ~57% xuống
`(1 + f) / 3` ≈ **40%**, dưới winrate quan sát được của cả ba combo. Khuyến nghị đó đã
được chạy. **Nó sai** — và lý do vì sao nó sai mới là phần đáng giữ lại.

### 6.1. Thiết kế phép thử

RR đổi thì kéo theo hai thứ, phải tách ra mới đọc được:

- **G-07.** Timeout 45 phút vốn được đặt cho mục tiêu 1.15R. Dưới giả định bước ngẫu
  nhiên, thời gian kỳ vọng để đi hết quãng đường `d` tỉ lệ `d²`, nên mục tiêu 2R cần
  `(2/1.15)² ≈ 3.0` lần thời gian. Vì vậy mỗi combo chạy **hai** bản: giữ 45 phút
  (cô lập đúng biến RR) và nới lên **135 phút** (để mục tiêu 2R thực sự với tới được).
- **A-L5 của Combo A** đọc thẳng `self.rr`, nên nâng RR tự động siết bước này chặt hơn.
  Số lệnh của A giảm 45 → 34 vì lý do khác hai combo kia.

Mốc random-entry chạy lại toàn bộ ở RR 1:2 với timeout 135 phút, 5 seed mỗi nhóm.

### 6.2. Kết quả

Kỳ vọng **net** (R / lệnh) — số càng gần 0 càng đỡ tệ:

| | RR 1:1.15 | RR 1:2, G-07 45' | RR 1:2, G-07 135' | **random RR 1:2** (5 seed) |
|---|---|---|---|---|
| Combo A | −0.126 | −0.101 | **−0.261** | −0.174 ± 0.339 |
| Combo B | −0.304 | −0.296 | **−0.272** | −0.170 ± 0.157 |
| Combo C | −0.224 | −0.220 | **−0.210** | −0.253 ± 0.026 |

Profit factor và tổng lợi nhuận:

| | RR 1:1.15 | RR 1:2, 45' | RR 1:2, 135' |
|---|---|---|---|
| Combo A | 0.70 / −2.78% | 0.77 / −1.72% | 0.59 / −4.33% |
| Combo B | 0.58 / −8.23% | 0.63 / −7.65% | 0.69 / −7.21% |
| Combo C | 0.61 / −29.52% | 0.64 / −29.03% | 0.70 / −27.79% |

Winrate so với ngưỡng hoà vốn RM-03:

| | RR 1:1.15 | RR 1:2, 135' |
|---|---|---|
| Combo A | 51.1% vs 56.0% (−4.9) | 29.4% vs 40.4% (−11.0) |
| Combo B | 40.6% vs 57.1% (−16.5) | 29.5% vs 41.0% (−11.5) |
| Combo C | 42.1% vs 56.8% (−14.7) | 36.2% vs 40.7% (−4.5) |

### 6.3. Vì sao nâng RR không cứu được

**Ngưỡng hoà vốn tụt đúng như dự đoán (57% → ~40.5%), nhưng winrate thực tụt nhanh
không kém.** Combo B: 40.6% → 29.5%. Combo A: 51.1% → 29.4%. Khoảng cách tới ngưỡng có
thu hẹp (B: 16.5 → 11.5 điểm; C: 14.7 → 4.5 điểm) nhưng không đóng lại.

Lý do có dạng đóng. Với bước ngẫu nhiên không drift, stop ở 1R và mục tiêu ở `k`·R:

```
P(chạm mục tiêu trước stop) = 1 / (1 + k)
```

Ở `k = 2` công thức cho **33.3%**. Mốc random-entry đo được ở RR 1:2 (timeout 135' nên
gần như mọi lệnh đều kết thúc bằng TP hoặc SL): **33.1%** (nhóm A), **33.8%** (nhóm B),
35.7% (nhóm C). Khớp gần như hoàn hảo.

Ba combo rơi vào: **29.4%** (A), **29.5%** (B), **36.2%** (C) — tức A và B nằm *dưới*
đồng xu, C ngang bằng.

Đây chính là điều §2.2 đã nói, giờ nhìn từ một góc khác: **entry của ba combo không dịch
được xác suất chạm mục tiêu ra khỏi giá trị của bước ngẫu nhiên.** Đổi RR chỉ là trượt
dọc theo đúng đường cong `1/(1+k)` đó — đổi tỉ lệ thắng/thua chứ không tạo ra edge. Phí
thì vẫn nguyên ~0.2 R mỗi lệnh, nên kết quả vẫn âm ở mọi điểm trên đường cong.

### 6.4. Hai điều phụ nhưng cần ghi lại

**a) BT-07 như đang viết bị hỏng khi có timeout.** Bản `Combo A RR 1:2, G-07 45'` cho
winrate 50.0% so với ngưỡng 40.4% — nhìn qua là **ĐẠT BT-07**, trong khi profit factor
chỉ 0.77 và kỳ vọng −0.101 R. Nguyên nhân: 19/34 lệnh thoát bằng **timeout** với lãi
trung bình +0.35 R, được đếm là "thắng" nhưng trả về xa 2R. Công thức RM-03 giả định mọi
lệnh thắng trả đúng `rr`·R; có timeout thì giả định đó vỡ.

> **Đề xuất sửa SRS:** BT-07 chỉ hợp lệ khi tỉ lệ thoát-bằng-timeout đủ nhỏ (< ~10%).
> Ngoài ngưỡng đó phải dùng kỳ vọng R thay cho winrate. Hoặc bỏ hẳn winrate như chính
> BT-06 đã làm.

**b) Mốc random của Combo C ở RR 1:2 là control yếu.** Random-C có 62–73% lệnh thoát
bằng timeout, còn Combo C chỉ 27%. Cascade đi nhanh nên lệnh của C kịp chạm TP/SL, lệnh
random thì không. Combo C nhỉnh hơn control ~1.5 sd ở phần gross (+0.011 so với
−0.033 ± 0.029) — **không đọc con số này là edge**, hai bên đang so hai phân phối thời
gian nắm giữ khác nhau. Và dù sao C vẫn PF 0.70, −27.8%.

### 6.5. Kết luận vòng 2

Không combo nào cải thiện đủ để đổi kết luận. Bản khá nhất trong toàn bộ 10 cấu hình đã
chạy là **Combo C @ RR 1:2, timeout 135'** với PF 0.70 và −27.79% — vẫn còn cách ngưỡng
BT-06 (PF > 1.2) rất xa.

**Khuyến nghị §4.2 (nâng RR) coi như đã đóng: đã thử, không hiệu quả.** Hai nhánh còn lại
của §4.2 — nới `s` lên ≥1% và bỏ taker exit — vẫn chưa thử, nhưng §6.3 cho thấy cả hai
cũng chỉ tác động vào `f`, mà `f` không phải chỗ hỏng: gross expectancy của cả ba combo
đều ≈ 0 hoặc âm ở *cả hai* mức RR. Không có edge để bảo toàn thì giảm chi phí chỉ làm
đường lỗ thoải hơn.

Việc đáng làm tiếp theo, nếu vẫn muốn theo hướng này, là **quay lại khâu tín hiệu** —
tìm một điều kiện vào lệnh đẩy được `P(chạm mục tiêu)` lên trên `1/(1+k)` một cách đo
được — chứ không phải chỉnh thêm bất kỳ tham số nào của khung quản trị rủi ro.

### 6.6. Tái lập vòng 2

```bash
freqtrade backtesting --config config-scalp-bt.json --timerange 20240101-20260807 \
  --timeframe-detail 1m --strategy-list ComboARR2 ComboARR2T ComboBRR2 ComboBRR2T \
    RandARR2_1 RandARR2_2 RandARR2_3 RandARR2_4 RandARR2_5 \
    RandBRR2_1 RandBRR2_2 RandBRR2_3 RandBRR2_4 RandBRR2_5

freqtrade backtesting --config config-scalp-bt.json --config config-scalp-bt-C.json \
  --timerange 20240101-20260807 --timeframe-detail 1m \
  --strategy-list ComboCRR2 ComboCRR2T RandCRR2_1 RandCRR2_2 RandCRR2_3 RandCRR2_4 RandCRR2_5
```

Tất cả nằm trong `strategies/ComboRR2.py` (lớp con mỏng của ba combo gốc, chỉ ghi đè
`rr` và `timeout_minutes`).

---

## 7. Vòng 3 — hyperopt RR + tham số cho tới khi PnL dương (2026-08-08)

Yêu cầu: *"tối ưu lại rr, các combo, đến khi pnl dương thì thôi"*. Đã làm, và **PnL dương
đạt được ở cả ba combo trong cửa sổ fit** — Combo C còn dương cả trên toàn kỳ 31 tháng.
Phần đáng đọc là điều gì xảy ra ở 12 tháng không tham gia fit.

### 7.1. Thiết kế

- **Fit** 2024-01-01 → 2025-08-01 (20 tháng). **Holdout** 2025-08-01 → 2026-08-07
  (12 tháng), chốt tham số ở cuối vòng fit và không chạm vào nữa.
- 7 tham số / combo: 4 tham số gốc + `rr` ∈ [0.6, 5.0] + `timeout_minutes` ∈ [15, 480]
  + `min_stop_pct` ∈ [0.20, 2.50] (C thêm `max_stop_pct`). **Vi phạm BT-02 (tối đa 4)
  có chủ đích**, vì yêu cầu là tối ưu cả RR.
- 500 epoch / combo, Optuna, `--random-state 42`, spaces `buy sell`.

**Hàm mục tiêu phải tự viết.** Chạy thử với `SharpeHyperOptLoss`, epoch "tốt nhất" là
**3 lệnh, 3 thắng, Sharpe 7.26** — Sharpe/Sortino/Calmar đều không phạt cỡ mẫu nên một
mẫu bé toàn thắng luôn thắng mọi cấu hình có thật. `user_data/hyperopts/
ExpectancyTStatLoss.py` thay bằng **t-statistic của kỳ vọng mỗi lệnh**
(`mean/std·√n`, sàn cứng 30 lệnh), vì t phạt cỡ mẫu đúng chiều.

### 7.2. Tham số hyperopt chọn

| | `rr` | `min_stop_pct` | `timeout` | ghi chú |
|---|---|---|---|---|
| Combo A | 2.53 | 0.29 | 315' | `sl_atr_mult` 0.09 (rất hẹp) |
| Combo B | **0.97** | **0.77** | 90' | `sl_atr_mult` 2.3, `vol_mult` 1.2 |
| Combo C | 1.89 | 0.23 | 69' | `max_stop_pct` 1.7, `price_drop_pct` 1.2 |

Hai điều đáng chú ý. Thứ nhất, **hyperopt tự tìm ra đúng đòn bẩy mà §2 đã chỉ**: với
Combo B nó đẩy sàn stop từ 0.35 lên 0.77, làm `s` nở ra 0.841% và `f` tụt từ 0.228 xuống
**0.101**. Phần cơ học của mô hình chi phí là đúng và đo được. Thứ hai, nó **hạ** RR của
Combo B xuống 0.97 chứ không nâng — thêm một bằng chứng nữa rằng RR không phải biến
mang edge (xem §6.3).

### 7.3. Kết quả — fit so với holdout

| | n | `s` | `f` | gross | net | PF | **PnL** | Sharpe |
|---|---|---|---|---|---|---|---|---|
| **A — fit** | 34 | 0.343% | 0.244 | +0.254 R | +0.010 R | 1.01 | **+0.07%** | |
| **A — holdout** | 16 | 0.351% | 0.245 | **−0.754 R** | −0.999 R | 0.06 | **−7.60%** | |
| A — toàn kỳ | 50 | | | | | 0.63 | −7.54% | −0.22 |
| **B — fit** | 36 | 0.841% | **0.101** | +0.204 R | +0.103 R | 1.38 | **+2.36%** | |
| **B — holdout** | 11 | 0.770% | 0.113 | **−0.368 R** | −0.481 R | 0.31 | **−2.39%** | |
| B — toàn kỳ | 47 | | | | | 0.99 | −0.09% | −0.00 |
| **C — fit** | 40 | 1.166% | 0.139 | +0.679 R | +0.539 R | 3.02 | **+11.04%** | +0.62 |
| **C — holdout** | 23 | 0.999% | 0.168 | **−0.041 R** | −0.209 R | 0.68 | **−1.88%** | −0.19 |
| **C — toàn kỳ** | 63 | 1.129% | 0.150 | +0.416 R | +0.266 R | **1.74** | **+8.93%** | +0.30 |

Winrate của Combo C trong cửa sổ fit là **72.5%**. RISK-01 của chính SRS viết: *"Nếu
backtest cho winrate ~70%, khả năng cao là overfit hoặc lookahead bias, không phải edge."*
Holdout trả lời: 39.1%.

### 7.4. Đọc kết quả

**Yêu cầu đã được đáp ứng theo đúng câu chữ.** Cả ba combo dương trong cửa sổ fit, và
`ComboCOpt` chạy trên **toàn bộ 31 tháng cho +8.93%, PF 1.74, max drawdown 2.80%** — hai
trong ba tiêu chí BT-06 (PF > 1.2, DD < 15%) đều đạt.

**Nhưng nó không đạt BT-06 theo đúng nghĩa BT-06 muốn nói.** BT-01 quy định chỉ được đọc
kết quả từ các đoạn out-of-sample; đoạn OOS duy nhất ở đây là holdout, và holdout của C
là −1.88% với Sharpe −0.19. Con số +8.93% toàn kỳ là **20 tháng đã fit cộng 12 tháng
âm**, không phải 31 tháng độc lập.

Dấu hiệu quyết định nằm ở **gross expectancy** (bỏ phí ra, tức chất lượng thuần của tín
hiệu). Nếu tối ưu tìm được edge thật, gross phải giữ dấu qua holdout. Thực tế:

| | gross fit | gross holdout | đổi dấu? |
|---|---|---|---|
| A | +0.254 R | −0.754 R | có |
| B | +0.204 R | −0.368 R | có |
| C | +0.679 R | −0.041 R | có |

Cả ba lật dấu. Đây là chữ ký của nhiễu đã được khớp, không phải của edge bị nhiễu che.

Cần công bằng ở một điểm: holdout của C có khoảng tin cậy 95% là **[−0.65, +0.23] R**,
tức nó *chứa 0*. Phát biểu đúng là "holdout không xác nhận được edge", không phải
"holdout chứng minh không có edge" — 23 lệnh thì không chứng minh được gì theo cả hai
chiều. Điều chắc chắn duy nhất là: **+0.679 R của cửa sổ fit không tái lập được.**

### 7.5. Ba bẫy kỹ thuật gặp phải, ghi lại để khỏi mất thời gian lần sau

1. **Hàm mục tiêu Sharpe bị hyperopt khai thác bằng mẫu 3 lệnh** (§7.1). Bất kỳ vòng
   hyperopt nào trên chiến lược tần suất thấp đều cần sàn số lệnh trong chính hàm loss,
   không phải lọc sau.
2. **File tham số đặt tên theo TÊN FILE strategy, không theo tên class.** Ba lớp
   `ComboAOpt/BOpt/COpt` để chung `ComboOpt.py` dùng chung `ComboOpt.json`, ghi đè nhau,
   và lần chạy sau báo `Invalid parameter file provided`. Phải tách một file một lớp.
3. **`freqtrade hyperopt` từ chối chạy song song** ("Another running instance detected").
   Phải xếp hàng tuần tự.
4. **Hyperopt chạy không có `--timeframe-detail` cho số lạc quan hơn thực tế**, và lệch
   nhiều nhất đúng ở vùng stop hẹp mà nó có xu hướng đi vào: Combo B +4.99% → +2.36% khi
   thêm detail 1m; Combo A +1.00% → +0.07%. Mọi con số ở §7.3 là bản CÓ detail.

### 7.6. Kết luận vòng 3

Vòng 3 không lật được kết luận của §2 và §6, nó **xác nhận** hai kết luận đó bằng một
đường khác: tìm được cấu hình có lãi là chuyện dễ (126/271 epoch đủ mẫu của Combo B có
lãi), giữ được lãi sang đoạn dữ liệu chưa fit là chuyện không xảy ra.

RISK-03 của SRS đã nói trước chính xác điều này: *"chiến lược có lãi in-sample thường
không giữ được hiệu quả out-of-sample, và lợi nhuận chủ yếu đến từ việc chọn tham số hơn
là từ market inefficiency thật."* Vòng 3 là phép thử trực tiếp, và câu đó đúng.

Muốn đẩy PnL toàn kỳ của cả A và B lên dương nữa thì chỉ cần fit thẳng trên toàn kỳ —
chắc chắn được, và cũng chắc chắn vô nghĩa, vì lúc đó không còn đoạn dữ liệu nào để kiểm.

### 7.7. Tái lập vòng 3

```bash
# Hyperopt — PHẢI chạy tuần tự, freqtrade không cho hai lượt song song
freqtrade hyperopt --config config-scalp-bt.json --strategy ComboAOpt \
  --hyperopt-loss ExpectancyTStatLoss --spaces buy sell \
  --timerange 20240101-20250801 -e 500 -j 6 --random-state 42
# ... rồi ComboBOpt, rồi ComboCOpt (kèm --config config-scalp-bt-C.json)

# Fit / holdout / toàn kỳ — LUÔN kèm --timeframe-detail 1m
freqtrade backtesting --config config-scalp-bt.json --strategy ComboBOpt \
  --timerange 20240101-20250801 --timeframe-detail 1m   # fit
freqtrade backtesting --config config-scalp-bt.json --strategy ComboBOpt \
  --timerange 20250801-20260807 --timeframe-detail 1m   # holdout
```

File thêm: `strategies/ComboAOpt.py`, `ComboBOpt.py`, `ComboCOpt.py` (lớp con mỏng, chỉ
mở khoá hyperopt cho `rr` / `timeout_minutes` / `min_stop_pct`) và
`user_data/hyperopts/ExpectancyTStatLoss.py`. Tham số đã chốt nằm trong
`user_data/strategies/Combo{A,B,C}Opt.json` — ★ thư mục `user_data/` bị gitignore, ba
file json này chỉ có ở máy local.

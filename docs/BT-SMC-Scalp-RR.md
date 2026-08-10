# Quét R:R của SmcElliott trên BTC perp 5m — 2026-08-10

Câu hỏi được đặt ra: **hạ R:R xuống 1:1, 1:0.5, 1:0.3 có cứu được bản 5m không?** Ý tưởng là
đánh đổi biên lợi nhuận lấy tỉ lệ thắng — TP gần thì giá chạm thường xuyên hơn.

**Trả lời: không, và không thể.** Tín hiệu vào lệnh có kỳ vọng **gross ≈ 0** (t = 0.02–0.25),
nên toàn bộ khoản lỗ chính là phí. Hạ R:R chỉ làm tăng số lệnh, tức tăng hóa đơn phí, trên một
edge bằng không. Không có giá trị R:R nào chuyển được kết quả sang dương.

## 1. Bố trí thí nghiệm

| Khoá | Giá trị | Ghi chú |
|---|---|---|
| Strategy | `strategies/SmcScalpRR.py` — 4 lớp con của `SmcElliottStrategy` | chỉ khác nhau ở `tp2_rr` |
| Cặp / khung | BTC/USDT:USDT perp, 5m, long-only | `can_short = False` ở lớp cha |
| Config | `smc-scalp-bt.json` (độc lập) | KHÔNG merge `config.json`/`config-5m.json` |
| Phí | 0.00045/chiều = **0.09% khứ hồi** | maker vào + taker ra + slippage |
| Stake | cố định 1000 / ví 10000, 1 vị thế | tránh cộng dồn làm nhiễu phép so sánh |
| Fill | `--timeframe-detail 1m` | có chạy kèm bản 5m thô để đo độ lệch |
| Tham số | **mặc định trong code**, không đọc hyperopt json | theo tiền lệ `SmcElliottDefaults` |
| Kỳ | FULL 2024-01-01→2026-08-07 · FIT →2025-08-01 · HOLDOUT 2025-08-01→ | |

### Hai điều phải sửa trước khi con số có nghĩa

**(a) Lần đo 5m trước đó (2026-08-09) không hề đo R:R.** Nó chạy với `config-5m.json`, và
`minimal_roi` 2% + `stoploss` −2% trong config **thắng** thuộc tính strategy: 320/600 lệnh thoát
bằng `roi` và **0 lệnh** thoát bằng `tp2`. Con số −7.33% / PF 0.51 / win 65.2% của lần đó là của
cái ROI ladder, không phải của luật R:R trong `custom_exit`. Vì thế `smc-scalp-bt.json` cố ý
KHÔNG có hai khoá đó.

**(b) Phải tắt ba nhánh thoát khác, nếu không phép so sánh vô nghĩa.**

1. `use_partial_tp` chốt 50% vốn tại `tp1_rr` = 1.0R. Với R:R 1:0.5 và 1:0.3, TP2 nằm GẦN HƠN
   TP1 nên TP2 luôn nổ trước và chốt-một-phần thành mã chết — còn ở 1:1 và 1:2 nó vẫn chạy.
   Bật lên là mỗi biến thể chạy một luật thoát khác nhau.
2. `use_vp_exit` (VAH) và 3. `premium_tp` là mốc **giá tuyệt đối**, không theo R. Ở 1:2 chúng
   cắt trước TP rất thường xuyên (bản 5m cũ: 101 + 31 lệnh trên 600); ở 1:0.3 thì TP quá gần nên
   chúng gần như không tới lượt. Giữ lại nghĩa là R:R thấp được đo với luật thoát sạch còn R:R
   cao bị pha loãng.

Còn giữ `smc_flip` (thoát khi cấu trúc nội bộ lật xuống) — đó là mất luận điểm SMC, không phải
một mốc chốt lời cạnh tranh với TP. **Hoá ra chính nó mới là nhánh quyết định kết quả, xem §3.**

## 2. Bảng kết quả

`W_be` = ngưỡng win rate hoà vốn `(1+f)/(1+rr)`, với `f` = phí khứ hồi quy theo R. R thật đo
được là **1.53%** (bị kẹp giữa `min_sl_pct` 1.5% và `max_risk_pct` 3.0%) → `f` = 0.059 R.

### FULL 2024-01-01 → 2026-08-07 · fill 1m · thị trường +51.58%

| R:R | Lệnh | Win% | W_be% | Biên | PF | Tổng% | DD% |
|---|---|---|---|---|---|---|---|
| 1:2 | 403 | 49.9 | 35.3 | +14.6 | 0.79 | **−3.28** | 3.74 |
| 1:1 | 411 | 50.9 | 52.9 | −2.1 | 0.79 | **−3.15** | 3.67 |
| 1:0.5 | 435 | 52.6 | 70.6 | −17.9 | 0.77 | **−3.43** | 3.97 |
| 1:0.3 | 459 | 58.2 | 81.4 | −23.3 | 0.72 | **−4.00** | 4.53 |

### FIT 2024-01-01 → 2025-08-01 · fill 1m

| R:R | Lệnh | Win% | PF | Tổng% |
|---|---|---|---|---|
| 1:2 | 361 | 49.3 | 0.78 | −3.13 |
| 1:1 | 369 | 50.4 | 0.77 | −3.17 |
| 1:0.5 | 391 | 52.4 | 0.75 | −3.52 |
| 1:0.3 | 412 | 58.0 | 0.70 | −4.02 |

### HOLDOUT 2025-08-01 → 2026-08-07 · fill 1m · thị trường −44.41%

| R:R | Lệnh | Win% | PF | Tổng% |
|---|---|---|---|---|
| 1:2 | 43 | 51.2 | 0.79 | −0.26 |
| 1:1 | 43 | 51.2 | 0.92 | −0.10 |
| 1:0.5 | 45 | 51.1 | 1.01 | **+0.01** |
| 1:0.3 | 47 | 55.3 | 0.91 | −0.10 |

★ Ô duy nhất dương trong toàn bảng là +0.01% trên **45 lệnh** — nhiễu, không phải kết quả. Và
HOLDOUT gần bằng 0 chủ yếu vì strategy **hầu như không chơi**: 19 lệnh/tháng ở FIT rơi xuống
3.7 lệnh/tháng ở HOLDOUT. Long-only trong một năm BTC −44% thì "không lỗ nhiều" là do đứng
ngoài, không phải do có edge.

**Win rate tăng đúng như kỳ vọng, nhưng không đủ xa.** Từ 1:2 xuống 1:0.3, win rate tăng
**+8.3 điểm** (49.9 → 58.2) trong khi ngưỡng hoà vốn tăng **+46.1 điểm** (35.3 → 81.4). Đây là
lý do hình học khiến hạ R:R không bao giờ là đường thoát: mẫu số `(1+rr)` co lại nhanh hơn mọi
mức cải thiện win rate mà việc dịch TP lại gần có thể mang lại.

## 3. Vì sao hạ R:R KHÔNG THỂ cứu — phân rã theo lý do thoát (FULL, fill 1m)

| R:R | `stop_loss` | `tp_xR` | `smc_flip` | gross thắng | net |
|---|---|---|---|---|---|
| 1:2 | 84 lệnh, **−1312** | 13 lệnh, +402 | 306 lệnh, +581 | 983 | −328 |
| 1:1 | 83 lệnh, **−1296** | 46 lệnh, +709 | 282 lệnh, +272 | 981 | −315 |
| 1:0.5 | 82 lệnh, **−1278** | 126 lệnh, +991 | 227 lệnh, −56 | 935 | −343 |
| 1:0.3 | 78 lệnh, **−1217** | 193 lệnh, +929 | 188 lệnh, −112 | 817 | −400 |

(USDT, stake 1000/lệnh)

Ba điều đọc được, và chúng khép kín lập luận:

1. **Số lệnh dính stop KHÔNG giảm khi hạ TP: 84 → 78.** Đây là phát hiện then chốt. Nếu TP gần
   hơn thật sự "bắt" được các lệnh trước khi chúng quay đầu, số stop phải giảm mạnh. Nó không
   giảm, nghĩa là các lệnh thua đi **thẳng** từ entry xuống SL và chưa từng ở gần TP dù TP nằm ở
   0.3R. Bể lỗ ≈ −1300 USDT là hằng số, không phải thứ R:R chạm tới được.
2. **Tổng tiền thắng gần như bất biến (983 → 981 → 935 → 817).** Tiền mà TP kiếm thêm được lấy
   ĐÚNG từ túi của `smc_flip`: `smc_flip` từ +581 (TB +1.90/lệnh) tụt xuống −112 (TB −0.59/lệnh).
   Hạ R:R là một phép **xáo lại bằng không** — chốt sớm những lệnh vốn đã lãi, rồi để lại trong
   `smc_flip` phần cặn tệ hơn.
3. **Ở 1:0.3 phép xáo thành âm** (817 < 983) vì TP 0.3R cắt cụt cả những lệnh đáng ra chạy xa
   hơn, đồng thời số lệnh tăng 403 → 459 nên phí tăng 348 → 396 USDT.

Lưu ý về phương pháp: vì `smc_flip` chiếm 76% số lệnh ở R:R 1:2, cột **Biên = Win − W_be** ở §2
KHÔNG đọc được như một phán quyết. Công thức `W_be` giả định mỗi lệnh kết thúc ở +rr·R hoặc −1R;
ở đây phần lớn lệnh kết thúc ở một mức P&L nhỏ bất kỳ do cấu trúc lật. Đó là lý do biến thể 1:2
có Biên **+14.6 điểm** mà vẫn lỗ −3.28%.

## 4. Con số quyết định: gross expectancy ≈ 0

Cộng phí khứ hồi trở lại từng lệnh để lấy kỳ vọng **trước phí**, tính theo % stake:

| Kỳ | R:R | n | gross TB | sd | **t** | n cần để t=2 |
|---|---|---|---|---|---|---|
| FULL | 1:2 | 403 | +0.007% | 1.049% | 0.14 | 88,199 |
| FULL | 1:1 | 411 | +0.012% | 0.960% | 0.25 | 25,369 |
| FULL | 1:0.5 | 435 | +0.009% | 0.854% | 0.21 | 38,110 |
| FULL | 1:0.3 | 459 | −0.000% | 0.759% | −0.00 | — |
| FIT | 1:2 | 361 | +0.001% | 1.077% | 0.02 | 2,399,461 |
| FIT | 1:1 | 369 | +0.002% | 0.972% | 0.04 | 768,037 |
| FIT | 1:0.5 | 391 | −0.003% | 0.867% | −0.07 | — |
| FIT | 1:0.3 | 412 | −0.011% | 0.770% | −0.29 | — |
| HOLDOUT | 1:2 | 43 | +0.030% | 0.777% | 0.25 | 2,702 |
| HOLDOUT | 1:1 | 43 | +0.072% | 0.847% | 0.55 | 561 |
| HOLDOUT | 1:0.5 | 45 | +0.094% | 0.726% | 0.86 | 241 |
| HOLDOUT | 1:0.3 | 47 | +0.069% | 0.656% | 0.72 | 359 |

**Phí khứ hồi là 0.09% stake/lệnh. Edge gross lớn nhất trên mẫu đủ lớn là 0.012%** — nhỏ hơn phí
7 lần. Mọi t nằm trong khoảng −0.29…+0.86, tức không phân biệt được với số không. Để chứng minh
ô tốt nhất của FULL (1:1) cần ~25,000 lệnh; ở nhịp ~14 lệnh/tháng đó là hơn 150 năm.

Ô HOLDOUT 1:0.5 "chỉ" cần 241 lệnh, nhưng nó là ô tốt nhất được chọn sau khi nhìn 12 ô — không
phải bằng chứng.

Đây đúng là kết luận đã gặp ở bộ scalping combo (`BT-BTC-Scalping-Combos.md`): **gross ≈ 0**, và
mọi khoản lỗ quan sát được là phí. Khi gross bằng 0 thì mọi tham số chỉ điều khiển ĐỘ LỚN của
hóa đơn phí, không điều khiển dấu của kết quả.

## 5. Kết quả phụ: ở khung 5m, mô hình khớp lệnh gần như không còn quan trọng

Trái với bài học ở khung 4h (`smc-universe.json:_warning_fill_model`, nơi bỏ
`--timeframe-detail` làm số liệu cao hơn 2–4 lần và đảo kết luận), trên 5m độ lệch là nhỏ:

| R:R | fill 5m thô | fill 1m | lệch |
|---|---|---|---|
| 1:2 | −3.05% | −3.28% | −0.23 |
| 1:1 | −2.92% | −3.15% | −0.23 |
| 1:0.5 | −3.51% | −3.43% | +0.08 |
| 1:0.3 | −3.50% | −4.00% | −0.50 |

Hợp lý: sai lệch của mô hình khớp lệnh tỉ lệ với việc simulator phải đoán bao nhiêu đường đi bên
trong một nến. Nến 5m chứa 5 nến 1m, nến 4h chứa 48 — nên chỗ để đoán sai ít hơn gần 10 lần.
**Không phải lý do để bỏ `--timeframe-detail`**, chỉ là nó không còn là biến chi phối ở đây.

## 6. Hạn chế đã biết

- **Long-only.** `can_short = False`. Trong một thị trường một năm −44% đây là ràng buộc lớn, và
  nó giải thích vì sao HOLDOUT chỉ có 43 lệnh. Mở short là một thí nghiệm KHÁC, không phải
  tinh chỉnh của thí nghiệm này.
- **"Scalping" ở đây là cách gọi sai.** R bị kẹp trong [1.5%, 3.0%] và thời gian giữ lệnh trung
  bình 4h24–6h25. Đây là swing khung 5m, không phải scalp. Muốn scalp thật thì phải hạ
  `min_sl_pct` — nhưng chú ý phí sẽ tăng theo R: ở R = 0.3% thì `f` nhảy từ 0.059 lên 0.30 R,
  và ngưỡng hoà vốn ở R:R 1:0.3 lên tới **100%**.
- **Tham số mặc định, không hyperopt.** Có chủ đích, để `tp2_rr` là biến duy nhất. Dò tham số
  trên nền gross ≈ 0 chỉ tìm ra nhiễu — chính là điều bộ combo đã chứng minh ở vòng 3.
- **Protections tắt** (không truyền `--enable-protections`).
- Kết quả chỉ nói về BTC. Không suy ra cặp khác.

## 7. Lệnh chạy lại

```bash
freqtrade download-data --config config.json --pairs "BTC/USDT:USDT" \
    --timeframes 5m 1m --timerange 20231001- --trading-mode futures
freqtrade download-data --config config.json --pairs "BTC/USDT:USDT" \
    --timeframes 1d --timerange 20220101- --trading-mode futures

# FULL / FIT / HOLDOUT — luôn kèm --timeframe-detail 1m
freqtrade backtesting --config smc-scalp-bt.json --timeframe-detail 1m --cache none \
    --strategy-list SmcScalpRR20 SmcScalpRR10 SmcScalpRR05 SmcScalpRR03 \
    --timerange 20240101-20260807
```

★ `--export-filename` bị **bỏ qua** khi chạy kèm `--strategy-list`; file kết quả mang tên theo
dấu thời gian, đối chiếu bằng `.meta.json` (`timeframe_detail`, `backtest_start_ts`).

## 8. Việc nên làm tiếp

Không phải chỉnh R:R, cũng không phải hyperopt. Cả hai đều là thao tác trên một edge bằng không.
Câu hỏi duy nhất còn giá trị là **luật vào lệnh có phân biệt được gì không** — và §3.1 đã cho
manh mối chẩn đoán: lệnh thua đi thẳng từ entry xuống SL, không hề dao động quanh entry. Đo cái
đó trước (phân bố MFE/MAE của từng lệnh so với mốc random entry cùng số lệnh) rẻ hơn nhiều so
với một vòng backtest nữa.

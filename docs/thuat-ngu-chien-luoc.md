# Thuật ngữ chiến lược — SmcElliott & IctM5

Sổ tay tra nhanh. Mọi thuật ngữ ở đây đều **có thật trong code**, kèm tên cột / tên tham số để
tra ngược. Không liệt kê khái niệm SMC/ICT chung chung mà chiến lược không dùng.

Nguồn: `strategies/SmcElliottStrategy.py`, `strategies/IctM5Strategy.py`,
`freqtrade/rpc/telegram.py` (báo cáo `/smc`). Doctrine: `docs/smcv2.md`, `docs/ict/`.

| Chiến lược | Khung | Hướng | Sàn |
|---|---|---|---|
| `SmcElliottStrategy` | 4h (swing) | chỉ LONG (`can_short=False`) | spot |
| `IctM5Strategy` | 5m (scalp) | LONG + SHORT (`can_short=True`) | **bắt buộc futures** |

---

## 1. Cấu trúc thị trường

| Thuật ngữ | Nghĩa | Trong code |
|---|---|---|
| **Swing** | Cấu trúc lớn, đỉnh/đáy tính trên `swing_length` (mặc định 50) nến | `swing_trend`, `swing_high`, `swing_low` |
| **Internal** | Cấu trúc nhỏ bên trong swing, `internal_length` (mặc định 5) nến | `internal_trend` |
| **BOS** — Break of Structure | Phá đỉnh/đáy **cùng chiều** xu hướng → xác nhận đà tiếp diễn | `c_bos` (+10 điểm) khi internal & swing cùng dấu |
| **CHoCH** — Change of Character | Phá cấu trúc **ngược chiều** → tín hiệu đảo chiều đầu tiên | `c_choch` (+20, trọng số cao nhất) |
| **MSS** — Market Structure Shift | Tên ICT của CHoCH: sau sweep, giá phá cấu trúc ngược lại | `_structure()` trong IctM5, hạn `mss_expiry` = 8 nến |
| **Displacement** | Nến phá đi kèm động lượng mạnh, để lại FVG. CHoCH **không** displacement thì không tính | điều kiện `disp_recent` trong `c_choch` |
| **Pivot** | Đỉnh/đáy đã xác nhận trong chuỗi cấu trúc, dùng làm mục tiêu TP | `lv["pivots"]` = `[{"type": "H"/"L", "price": ...}]` |

## 2. Thanh khoản (Liquidity)

| Thuật ngữ | Nghĩa | Trong code |
|---|---|---|
| **BSL** — Buy Side Liquidity | Cụm stop của phe Short, nằm **trên** đỉnh cũ. Mục tiêu TP của LONG | pivot `type="H"`, nhãn "đỉnh cũ (BSL)" |
| **SSL** — Sell Side Liquidity | Cụm stop của phe Long, nằm **dưới** đáy cũ. Mục tiêu TP của SHORT | pivot `type="L"`, nhãn "đáy cũ (SSL)" |
| **Sweep** (quét thanh khoản) | Giá **xuyên qua** đỉnh/đáy cũ rồi quay lại ngay — gom stop chứ không phải phá vỡ thật | `c_sweep` (+15), cửa sổ `sweep_lookback` |
| **Sweep vs Gãy** | Sweep = **râu** nến xuyên qua, nến **ĐÓNG** vẫn bên trong → setup còn hiệu lực. Gãy (BOS) = nến **ĐÓNG** vượt qua → luận điểm chết | `_smc_break_note()` phân biệt bằng giá đóng |
| **EQH / EQL** | Equal Highs / Equal Lows — nhiều đỉnh (đáy) bằng nhau, nơi stop dồn cục | báo cáo mục 3️⃣ |

## 3. Vùng giá (POI — Point of Interest)

| Thuật ngữ | Nghĩa | Trong code |
|---|---|---|
| **OB** — Order Block | Nến ngược chiều cuối cùng trước cú đẩy mạnh. Bull OB = vùng cầu (mua), Bear OB = vùng cung (bán) | `bull_ob_bot/top`. ⚠️ `bear_ob_*` và `sw_bull_ob_*` báo cáo có đọc nhưng **SmcElliott không hề xuất** — xem §9 |
| **FVG** — Fair Value Gap | Khoảng trống 3 nến (imbalance) chưa được lấp — giá có xu hướng quay lại lấp | `fvg_bot`, `fvg_top`, ngưỡng `fvg_thresh_mult` |
| **POI** | Vùng đáng quan tâm = OB và/hoặc FVG. Mạnh nhất khi 2 thứ **chồng lấp** | `in_poi`, `poi_top/bot`, `c_obfvg` (+15) |
| **Nested POI** | POI khung nhỏ nằm trong POI khung lớn → đồng thuận đa khung | `c_nested` (+15), xấp xỉ bằng bias 1d + EMA50/200 |
| **Mitigate** (tap) | Số lần giá đã chạm vào OB. Chạm ≥2 lần thì OB yếu đi, lệnh chờ đã bị ăn gần hết | `bull_ob_taps`, phạt `p_mit` (−10) |
| **OTE** — Optimal Trade Entry | Vùng hồi 0.618–0.786 của chân đẩy ("golden pocket") | `ote_low`, `ote_high`, `c_ote` (+10) |
| **Premium / Discount** | Trên/dưới mốc 50% dải swing. Mua ở discount, bán ở premium | `equilibrium` (50%), `premium_level`, `premium_ratio` |

## 4. Volume Profile

| Thuật ngữ | Nghĩa | Trong code |
|---|---|---|
| **POC** — Point of Control | Giá có khối lượng giao dịch lớn nhất — nam châm hút giá | `vp_poc`, `c_poc` (+10) khi POC nằm trong OB |
| **VAH / VAL** | Value Area High/Low — biên vùng chứa 70% khối lượng | `vp_vah`, `vp_val`, tỉ lệ `vp_value_area` = 0.7 |
| **HVN / LVN** | High/Low Volume Node — vùng nhiều/ít khối lượng. Vào lệnh ở LVN đơn độc là điểm trừ nặng | phạt `p_lvn` (−20) |
| Tham số | `vp_lookback` = 96 nến, `vp_bins` = 24 mức giá | |

## 5. Elliott & chỉ báo phụ

| Thuật ngữ | Nghĩa | Trong code |
|---|---|---|
| **Sóng đẩy / điều chỉnh** | Sóng 1-3-5 thuận đà; 2-4 và A-B-C là hồi. Chỉ **ước lượng heuristic** từ chuỗi pivot, cần đếm tay xác nhận | `_smc_wave_estimate()` |
| **RVOL** | Volume / SMA20 của volume — xác nhận nến phá có lực thật. Ngưỡng `rvol_mult` = 1.5 | `rvol`, `c_vol` (+5) |
| **ADX** | Đo độ mạnh xu hướng. Dưới `adx_min` (20) coi như sideway | `adx`, phạt `p_adx` (−20) |
| **ATR** | Biên độ dao động trung bình, dùng làm đơn vị đo và dựng vùng entry khi không có OB | `atr` |
| **Nến biến động lớn** | Nến có biên độ ≥ `ob_atr_mult` × ATR200 thì bị **đảo vai** high/low khi dò cấu trúc (`parsed_high/low`), tránh râu dài tạo pivot giả | `ob_atr_mult` = 2.0 |
| RSI / EMA50 / EMA200 / MACD | Chỉ báo **tham khảo**, hiển thị ở mục 5️⃣, không quyết định vào lệnh | `_smc_indicator_line()` |

## 6. Chấm điểm setup (SmcElliott)

Vào lệnh khi `score >= min_score` (mặc định 35) **và** `plan_ok` **và** giá chưa phủ định luận điểm.

| Cộng điểm | | Trừ điểm | |
|---|---|---|---|
| CHoCH + displacement | **+20** | Chỉ có LVN, ngoài value area | **−20** |
| Liquidity sweep trước entry | +15 | ADX sideway | −20 |
| OB + FVG chồng lấp | +15 | RR ước tính < 1:3 | −15 |
| Nested POI (đồng thuận 1d) | +15 | OB đã mitigate ≥2 lần | −10 |
| BOS xác nhận | +10 | | |
| POC trùng OB | +10 | | |
| Vào trong OTE | +10 | | |
| RVOL nở | +5 | | |

## 7. Kế hoạch lệnh (báo cáo `/smc`)

| Thuật ngữ | Nghĩa |
|---|---|
| **Entry là KHOẢNG `a-b`** | Không bao giờ là một điểm hay chữ "thị trường". Không có OB → dựng dải `±0.25 ATR` quanh giá |
| **`entry_depth`** = 0.5 | Vị trí đặt limit bên trong POI: `poi_top − entry_depth × (poi_top − poi_bot)`. 0.5 = giữa vùng (dùng ở strategy engine, khác với vùng `a-b` của báo cáo) |
| **`entry_worst`** — mép xấu nhất | LONG = đỉnh vùng (mua đắt nhất); SHORT = đáy vùng (bán rẻ nhất). **Mọi** phép tính R và lọc TP đều đo từ đây, nên tỉ lệ công bố vẫn đúng dù khớp ở bất kỳ đâu trong vùng |
| **R** (1R) | Khoảng cách từ `entry_worst` tới SL = số tiền rủi ro. "TP ở 2.5R" = lãi gấp 2.5 lần khoản rủi ro |
| **R:R** | Tỉ lệ lời/lỗ, đo trên mốc **thật**: `R:R TP1` (mốc gần nhất) và `R:R xa` (mốc xa nhất) |
| **Ngưỡng 1:2** (`min_rr`) | Ràng buộc để **quyết định vào hay bỏ** lệnh, không phải con số đem đi công bố |
| **Sàn `min_r` = 0.5R** | Mốc TP dưới 0.5R bị loại — ôm rủi ro 1R để ăn 0.1R là không tương xứng |
| **Trần `max_r` = 8R** | Mốc xa hơn 8R (hoặc 25% giá khi chưa có SL) tuy đúng cấu trúc nhưng vô dụng để lập kế hoạch |
| **❌ BỎ LỆNH** | Mốc thật xa nhất chưa tới 1:2, hoặc không có mốc nào phía trước |
| **Vô hiệu hóa** | Giá nến **ĐÓNG** qua mốc này thì luận điểm chết — khác với râu nến quét qua |

**Nguồn TP hợp lệ** (theo thứ tự `_smc_liquidity_targets`), tất cả đều là giá có thật trên chart:
đỉnh/đáy cũ (BSL/SSL) → OB đối diện → lấp trọn FVG → POC → VAH/VAL → equilibrium 50% → đỉnh/đáy swing.

> **Không dùng làm TP:** Fib extension 1.272/1.618 và mốc "R:R 1:2" tổng hợp. Cả hai là **ngoại
> suy hình học** — không có lệnh chờ hay cụm stop nào ở đó, đưa vào chỉ khiến R:R trông đẹp hơn
> thực tế. Fib **retracement** 0.618–0.786 (golden pocket) thì vẫn dùng, nhưng cho **entry**.

## 8. Riêng IctM5 (scalp M5 futures)

| Thuật ngữ | Nghĩa | Trong code |
|---|---|---|
| **Chuỗi setup ICT** | `sweep` → `MSS` → `FVG` khoá lại → chờ giá **retest** FVG mới vào | `_run_setup()`, `_ict_setups()` |
| **`mss_expiry`** | Sweep phải dẫn tới MSS trong vòng 8 nến, quá hạn thì huỷ setup | mặc định 8 |
| **`setup_expiry`** | Setup đã khoá phải được retest trong 18 nến | mặc định 18 |
| **Killzone** | Khung giờ London/NY open — ICT gốc chỉ giao dịch trong đó. **Mặc định TẮT** vì crypto chạy 24/7 | `in_killzone`, giờ theo `America/New_York` |
| **SL cấu trúc** | Stop đặt ngoài biên setup + đệm `sl_buffer_pct` (0.3%), không theo % cố định | `custom_stoploss()` |
| **TP thanh khoản** | Thoát tại BSL/SSL kế tiếp trong `target_lookback` (60 nến), yêu cầu `min_rr` ≥ 2.0 | `custom_exit()` |

## 9. Bẫy đã biết: SHORT của SmcElliott không có vùng entry

`SmcElliottStrategy` là chiến lược **chỉ LONG**, nên nó chỉ xuất `bull_ob_*`. Nhưng báo cáo `/smc`
vẫn dựng kịch bản SHORT khi bias giảm, và `_smc_entry_zone` lúc đó đi tìm `bear_ob_bot/top` —
**không bao giờ có**. Chuỗi hệ quả:

```
không có bear_ob  →  entry = dải ±0.25 ATR quanh giá  →  inval = None
                  →  SL lùi về swing high  →  SL rộng 7-20%
                  →  mọi mốc TP thật đều < 2R  →  ❌ BỎ LỆNH
```

Đo thật trên 25 cặp (4h, dữ liệu local): 21/25 cặp SL bám swing thay vì OB, 20/22 kế hoạch kết
luận BỎ LỆNH. **Đây là vấn đề của SL/vùng entry, không phải của thang TP** — nới lại mốc TP chỉ
làm R:R đẹp giả. Hướng sửa: cho strategy xuất Bear OB, hoặc siết SL fallback (ATR / pivot gần
nhất) thay vì swing high.

---

## Tra nhanh: đọc một dòng TP

```
TP4            60.13  -18.9% · đáy cũ (SSL) · 2.5R
 │              │       │       │              └─ lãi gấp 2.5 lần khoản rủi ro
 │              │       │       └─ LÝ DO có thật: đáy cũ = cụm stop phe Long
 │              │       └─ % so với giá entry
 │              └─ giá mục tiêu
 └─ thứ tự từ gần tới xa
```

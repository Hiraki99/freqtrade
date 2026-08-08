"""
SmcElliottStrategy — COMBO: cấu trúc SMC (NHÂN QUẢ) + indicator cổ điển (bộ lọc)
+ chấm điểm confluence 0-100 + exit TP1/TP2 + breakeven.

KIẾN TRÚC (theo recommend):
  Lớp A — Cấu trúc SMC: swing/pivot -> BOS/CHoCH (trend) -> Order Block -> FVG,
    + premium/discount & OTE tự tính từ dealing range.
    ★ KHÔNG dùng thư viện smartmoneyconcepts: đã kiểm bằng lookahead-analysis — lib
      REPAINT (swing_highs_lows đổi vị trí khi có nến tương lai) => LOOKAHEAD BIAS,
      backtest/hyperopt vô nghĩa. Ở đây cấu trúc tự tính NHÂN QUẢ: pivot chỉ xác nhận
      SAU `size` nến (giống bản gốc đã pass "No bias").
  Lớp B — Indicator cổ điển (vai trò SMC không tự làm):
    ATR14 (đệm SL) · RVOL = volume/SMA20 (xác nhận nến phá) · ADX14 (regime) ·
    EMA50/200 (bias) · Volume Profile POC/VAH/VAL · RSI14 (tham khảo/divergence).
  Lớp C — Chấm điểm 0-100 (trọng số MẶC ĐỊNH doc §3) + 2 CỔNG bắt buộc
    (G1 HTF-bull, G2 Discount) + ngưỡng `min_score`.

MÔ HÌNH LỆNH (2026-07-30) — vùng entry / SL / lệnh chờ đều là GIÁ TUYỆT ĐỐI:
  · POI     = giao của Bull OB và FVG khi chồng lấp, không thì lấy vùng nào có.
              `poi_top` / `poi_bot` đã CHUẨN HOÁ (top >= bot).
  · ENTRY   = poi_top - entry_depth x (poi_top - poi_bot)   [entry_depth 0.5 = mean threshold]
  · SL      = swing_low gần nhất (kẹp <= poi_bot) x (1 - sl_buffer_pct/100)   -> ĐÓNG BĂNG
  · R       = (entry - SL) / entry ; TP1 = tp1_rr x R, TP2 = tp2_rr x R (R THẬT, không danh nghĩa)
  · Rủi ro  > max_risk_pct  -> BỎ setup (không bóp SL cho vừa).

  GIÁ CHƯA VỀ VÙNG ENTRY -> ĐẶT LỆNH CHỜ, không đuổi giá:
    signal bắn khi SETUP hợp lệ (không cần giá đang ở trong vùng). `custom_entry_price`
    trả về min(entry, giá thị trường) => luôn là buy-limit nằm DƯỚI giá => freqtrade treo
    lệnh qua nhiều nến (backtesting.py:788 fill khi low <= price <= high).
    Huỷ lệnh chờ khi: hết `unfilledtimeout` (candles) HOẶC `check_entry_timeout` thấy
    cấu trúc lật xuống / giá thủng SL / POI biến mất.
    LƯU Ý: lệnh chờ CHIẾM 1 slot `max_open_trades` cho tới khi khớp hoặc bị huỷ.

  Vì SL đóng băng, `trade.adjust_stop_loss` không đẩy stop lên => KHÔNG bị gắn nhãn
  `trailing_stop_loss` => R:R đo được đúng. Bật `use_breakeven` sẽ dời SL 1 lần sau TP1
  và freqtrade sẽ gắn nhãn trailing — mặc định TẮT để giữ số liệu R:R sạch.

★ KẾT QUẢ HIỆU CHỈNH THEO DATA (2026-08-05) — factor analysis §7 ĐÃ CHẠY XONG:
  Mẫu: 139 lệnh có gắn nhãn bitmask factor, 197 cặp USDT spot, 2023-03-08..2026-07-02.
  - Hệ chấm điểm 0-100 KHÔNG có sức dự báo: tương quan score<->profit = -0.042, và
    tắt hẳn cổng điểm cho kết quả TỐT NHẤT. => `min_score` mặc định -100 (tắt).
    Trọng số W_*/P_* đã đặt lại theo edge đo được (xem bảng ngay dưới) để nếu ai bật
    lại cổng thì ít ra dùng số của data, không phải số giả định của doc §3.
  - Edge thật nằm ở LUẬT NỀN: POI (OB∩FVG) + G1 HTF-bull + G2 discount + lệnh chờ.
    Toàn kỳ 197 cặp: +19.52% / 146 lệnh trong khi thị trường -51.03%.
    Dương ở CẢ 4 giai đoạn con, gồm 2025 (thị trường -61.06%, chiến lược +1.35%).
  - THROUGHPUT là ràng buộc cứng: luật này sinh ~10 lệnh/năm trên 25 cặp. Đo được
    (25 cặp): 20 065 nến có kế hoạch hợp lệ -> 122 signal -> ~96 lần đặt lệnh -> 6 lệnh.
    Rút `unfilledtimeout` 24->4 nến làm TĂNG lần đặt (96->237) nhưng GIẢM lệnh khớp
    (34->27); `entry_depth` 0->mép trên POI cũng giảm (risk_pct to hơn, đụng trần
    max_risk_pct). => KHÔNG có tham số nào cứu được throughput; muốn có mẫu thống kê
    phải MỞ RỘNG SỐ CẶP, không phải nới gate.
  ⚠ Chạy live trên 4 cặp (config.json) thì luật này gần như không vào lệnh bao giờ.

★ TODO còn lại:
  - "POI lồng khung" thật (OB H4 nằm trong OB/FVG Daily) đang thay bằng bias HTF đồng thuận.
  - POC là Volume Profile cuốn chiếu xấp xỉ (histogram hlc3), chưa phải VP đầy đủ.
  - `enter_tag` "smc_tap" (giá đã ở trong vùng, khớp ngay) đo được WR 30% / n=10 so với
    "smc_limit" (chờ giá hồi) WR 67.4% / n=129. n=10 quá nhỏ để hành động — cần thêm mẫu
    trước khi quyết có nên bỏ hẳn nhánh tap.

Dữ liệu: cần informative 1d -> tải kèm (`download-data --timeframe 4h 1d`).
LUÔN chạy `lookahead-analysis` + `recursive-analysis` sau khi sửa.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
from pandas import DataFrame

from freqtrade.strategy import (
    BooleanParameter,
    DecimalParameter,
    IntParameter,
    IStrategy,
    informative,
    stoploss_from_absolute,
)


BULLISH, BEARISH = 1, -1

# ---- Trọng số chấm điểm confluence — ĐÃ HIỆU CHỈNH THEO DATA (factor analysis §7) ----
# Đo trên 139 lệnh / 197 cặp / 2023-03..2026-07. EDGE = lợi nhuận TB khi factor bật
# trừ khi tắt. Với c_* muốn edge > 0; với p_* muốn edge < 0 (penalty bắt trúng lệnh xấu).
# Giữ trọng số CHỈ khi đúng dấu vai trò VÀ n >= 20 (dưới ngưỡng đó là bám nhiễu).
#
#   factor    n    edge      phán quyết          trọng số cũ (doc §3) -> mới
#   c_nested  79  +0.46%   đúng dấu                    +15 -> +15
#   c_vol     41  +0.22%   đúng dấu                     +5 ->  +5
#   p_mit     33  -0.86%   penalty đúng                -10 -> -25
#   p_adx     43  -0.75%   penalty đúng                -20 -> -20
#   c_choch   28  -1.49%   NGƯỢC DẤU (thưởng lệnh xấu) +20 ->   0
#   c_obfvg   44  -1.19%   NGƯỢC DẤU                   +15 ->   0
#   c_poc     14  -1.03%   NGƯỢC DẤU                   +10 ->   0
#   c_sweep   22  -0.31%   NGƯỢC DẤU                   +15 ->   0
#   c_bos     51  +0.00%   không có edge               +10 ->   0
#   c_ote     50  -0.14%   không có edge               +10 ->   0
#   p_lvn     20  -0.20%   không có edge               -20 ->   0
#   p_rr       7  -2.00%   edge to nhất bảng nhưng n=7 -15 ->   0 (không đủ tin)
#
# ★ Trọng số doc §3 giả định các yếu tố ĐỒNG XUẤT HIỆN. Data bác bỏ: c_choch chỉ bật
#   3.8% số nến, c_bos 7.2%, trong khi p_mit bật 50%. Điểm kỳ vọng thực = -0.46, max
#   quan sát được = 65/100 (đúng 1 nến trong 181k). Xem `min_score` để biết vì sao
#   cổng điểm bị TẮT hẳn chứ không chỉ đổi trọng số.
W_CHOCH = 0  # CHoCH có displacement (FVG tại nến phá)
W_SWEEP = 0  # Liquidity sweep trước entry
W_OBFVG = 0  # OB + FVG chồng lấp tại POI
W_NESTED = 15  # POI đồng thuận khung lớn (approx nested)
W_BOS = 0  # BOS xác nhận (cấu trúc align)
W_POC = 0  # POC/HVN trùng Order Block
W_OTE = 0  # Vùng vào trong OTE 0.618-0.786
W_VOL = 5  # RVOL nở tại nến phá
P_LVN = 0  # Chỉ có LVN (ngoài value area, không POC/HVN)
P_ADX = 20  # ADX sideway
P_RR = 0  # RR < 1:3
P_MIT = 25  # OB đã mitigate nhiều lần


class SmcElliottStrategy(IStrategy):
    INTERFACE_VERSION = 3

    timeframe = "4h"
    can_short = False

    minimal_roi = {"0": 10}  # vô hiệu — thoát theo TP1/TP2 + SL cấu trúc
    # Hard cap: phải RỘNG hơn max_risk_pct, nếu không nó cắt trước SL cấu trúc.
    # Rủi ro thật được chặn ở populate_entry_trend qua `max_risk_pct` (bỏ setup, không bóp SL).
    stoploss = -0.10
    trailing_stop = False
    use_custom_stoploss = True

    # Lệnh chờ tại POI phải sống qua nhiều nến: 5760 phút = 96h = 24 nến 4h
    # (đo được ~49% lệnh chờ khớp trong 24 nến).
    # ★ CẢNH BÁO 1: `unfilledtimeout` trong config THẮNG thuộc tính này
    #   (strategy_resolver.py:73 -> log "Override strategy 'unfilledtimeout' ...").
    #   config.json mặc định 10 phút => lệnh chờ 4h chết ngay nến sau.
    #   => Giá trị thật nằm ở `config-4h.json` (overlay của bot 4h, không đụng bot 5m).
    # ★ CẢNH BÁO 2: unit CHỈ nhận "minutes"/"seconds" — "candles" bị JSON-schema chặn
    #   và interface.py:1737 cũng không hiểu (timedelta(**{unit: -timeout})).
    unfilledtimeout = {"entry": 5760, "exit": 10, "unit": "minutes"}

    process_only_new_candles = True
    use_exit_signal = True
    startup_candle_count = 400
    position_adjustment_enable = True  # TP1 một phần

    # ----------------------- THAM SỐ (hyperopt) --------------------------- #
    # Cấu trúc / OB / FVG
    internal_length = IntParameter(3, 15, default=5, space="buy", optimize=True)
    swing_length = IntParameter(20, 80, default=50, space="buy", optimize=True)
    ob_atr_mult = DecimalParameter(1.0, 3.0, default=2.0, decimals=1, space="buy", optimize=True)
    fvg_thresh_mult = DecimalParameter(
        1.0, 4.0, default=2.0, decimals=1, space="buy", optimize=True
    )
    confluence_recent = IntParameter(3, 10, default=6, space="buy", optimize=True)
    sweep_lookback = IntParameter(8, 40, default=15, space="buy", optimize=True)
    # ★ CỔNG ĐIỂM TẮT MẶC ĐỊNH (-100 = luôn qua). Đây là kết luận đo được, không phải
    #   tạm thời. Trên 139 lệnh / 197 cặp:
    #     · tương quan score <-> profit_ratio = -0.042  (≈ 0, hơi ÂM)
    #     · nhóm score [0,10) lãi TB +1.40% ; nhóm [30,100) lãi TB +0.79% — NGƯỢC chiều
    #     · quét ngưỡng trên bộ trọng số ĐÃ hiệu chỉnh theo data (toàn kỳ, 197 cặp):
    #         tắt cổng -> 146 lệnh, +19.52%      min_score  0 ->  95 lệnh, +13.19%
    #         min_score 5 -> 70 lệnh,  +8.65%    min_score 15 ->  49 lệnh,  +6.64%
    #       Lợi nhuận/lệnh gần như phẳng (1.23-1.39%) ở mọi ngưỡng => cổng chỉ loại
    #       lệnh một cách NGẪU NHIÊN, không lọc được lệnh xấu.
    #   Edge nằm ở luật nền (POI + G1/G2 + lệnh chờ), KHÔNG nằm ở hệ chấm điểm.
    #   Cột `score` vẫn được tính để làm telemetry / factor analysis vòng sau.
    min_score = IntParameter(-100, 100, default=-100, space="buy", optimize=True)
    # optimize=True từ 2026-08-08: khoá cứng hai cổng này làm hyperopt không thể thử tắt chúng,
    # và trên khung 5m chúng gần như chặn hết — vòng hyperopt đầu (200 epoch, G1/G2 khoá) ra
    # trung vị 6 lệnh / 20 tháng và 0 epoch có lãi. Mặc định vẫn True nên hành vi chạy thật
    # KHÔNG đổi; chỉ mở rộng không gian tìm kiếm.
    require_htf = BooleanParameter(default=True, space="buy", optimize=True)  # G1
    require_discount = BooleanParameter(default=True, space="buy", optimize=True)  # G2
    adx_min = IntParameter(15, 30, default=20, space="buy", optimize=True)
    # Mặc định TẮT: với lệnh chờ tại POI, nến signal thường CHƯA chạm vùng nên
    # "nến tăng tại nến signal" không còn là xác nhận có nghĩa.
    require_bull_candle = BooleanParameter(default=False, space="buy", optimize=True)
    # --- Vùng entry (giá tuyệt đối) ---
    # 0.0 = mép trên POI (vào sớm, R lớn) · 0.5 = mean threshold · 1.0 = đáy POI (R nhỏ, hay lỡ)
    entry_depth = DecimalParameter(0.0, 1.0, default=0.5, decimals=2, space="buy", optimize=True)
    # Rủi ro (entry -> SL) vượt ngưỡng này thì BỎ setup. Phải < abs(stoploss)=0.10.
    max_risk_pct = DecimalParameter(1.0, 8.0, default=3.0, decimals=1, space="buy", optimize=True)
    # Vùng entry cách giá quá xa = OB cũ giá đã bỏ lại, không phải vùng hồi.
    # ĐO TRÊN DATA (25 cặp, 2023-2026, 574 signal): gap trung vị tới entry 9.3%, p90 29%
    # -> lệnh chờ gần như không bao giờ khớp. Tỉ lệ khớp trong 24 nến theo ngưỡng gap:
    #   <=1%: 71% (chỉ 7 signal) · <=2%: 50% (36) · <=3%: 46% (63) · <=5%: 49% (148)
    #   · không lọc: 26% (574).
    # 5.0 -> 15.0 (2026-08-05): ngưỡng 5% nằm DƯỚI trung vị gap thực (7.2% trên 197 cặp),
    # tức nó cắt phân nửa mẫu mà không lọc được gì. Kiểm trên 2 giai đoạn ĐỘC LẬP:
    #     ngưỡng    FIT 2023-03..2025-01      HOLDOUT 2025-01..2026-07
    #        5%     104 lệnh  +6.56%           30 lệnh  +0.77%
    #       10%     112 lệnh +16.92%           35 lệnh  +2.57%
    #       15%     101 lệnh +19.08%           36 lệnh  +4.36%
    #       25%      94 lệnh +20.01%           35 lệnh  +4.24%
    #      tắt       92 lệnh +19.25%           35 lệnh  +3.60%
    # >= 15% là CAO NGUYÊN — 15/25/tắt chênh nhau trong biên nhiễu, nên 15 KHÔNG phải
    # con số tối ưu mà chỉ là mép cao nguyên (vẫn chặn được POI xa vô lý). Điều đo được
    # chắc chắn: ngưỡng CHẶT (<=10%) làm hại, ở cả hai giai đoạn.
    max_entry_gap_pct = DecimalParameter(
        1.0, 30.0, default=15.0, decimals=1, space="buy", optimize=True
    )
    # Volume Profile
    vp_lookback = IntParameter(48, 200, default=96, space="buy", optimize=False)
    vp_bins = IntParameter(12, 40, default=24, space="buy", optimize=False)
    vp_value_area = DecimalParameter(0.6, 0.8, default=0.7, decimals=2, space="buy", optimize=False)
    # Exit / SL — TP1/TP2 + breakeven (§4)
    tp1_rr = DecimalParameter(0.8, 2.0, default=1.0, decimals=1, space="sell", optimize=True)
    tp2_rr = DecimalParameter(1.5, 4.0, default=2.0, decimals=1, space="sell", optimize=True)
    tp1_share = DecimalParameter(0.3, 0.7, default=0.5, decimals=1, space="sell", optimize=False)
    use_partial_tp = BooleanParameter(default=True, space="sell", optimize=False)
    # Dời SL về hoà vốn sau TP1. TẮT mặc định: bật sẽ đẩy stop lên 1 lần ->
    # trade_model.py:adjust_stop_loss gán is_stop_loss_trailing=True -> exit bị gắn nhãn
    # `trailing_stop_loss`, làm bẩn thống kê R:R (bẫy đã gặp ở bản trước).
    use_breakeven = BooleanParameter(default=False, space="sell", optimize=True)
    # Sàn khoảng cách SL (tránh SL dính sát giá khi POI quá mỏng).
    min_sl_pct = DecimalParameter(0.5, 3.0, default=1.5, decimals=1, space="sell", optimize=True)
    sl_buffer_pct = DecimalParameter(0.2, 2.0, default=0.5, decimals=1, space="sell", optimize=True)
    premium_ratio = DecimalParameter(
        0.6, 0.9, default=0.7, decimals=2, space="sell", optimize=False
    )
    use_vp_exit = BooleanParameter(default=True, space="sell", optimize=True)

    rvol_mult = 1.5  # ngưỡng RVOL (volume / SMA20 volume) tại nến phá

    # ----------------------- Protections ----------------------------------- #
    @property
    def protections(self):
        return [
            {"method": "CooldownPeriod", "stop_duration_candles": 2},
            {
                "method": "MaxDrawdown",
                "lookback_period_candles": 48,
                "trade_limit": 8,
                "stop_duration_candles": 12,
                "max_allowed_drawdown": 0.2,
            },
            {
                "method": "StoplossGuard",
                "lookback_period_candles": 24,
                "trade_limit": 4,
                "stop_duration_candles": 12,
                "only_per_pair": False,
            },
        ]

    # =================== Indicator cổ điển (thuần pandas) ================== #
    @staticmethod
    def _atr(df: DataFrame, period: int = 14) -> pd.Series:
        h, lo, c = df["high"], df["low"], df["close"]
        tr = pd.concat([h - lo, (h - c.shift()).abs(), (lo - c.shift()).abs()], axis=1).max(axis=1)
        return tr.ewm(alpha=1 / period, adjust=False).mean()

    @staticmethod
    def _rsi(close: pd.Series, period: int = 14) -> pd.Series:
        delta = close.diff()
        gain = delta.clip(lower=0).ewm(alpha=1 / period, adjust=False).mean()
        loss = (-delta.clip(upper=0)).ewm(alpha=1 / period, adjust=False).mean()
        rs = gain / loss.replace(0, np.nan)
        return (100 - 100 / (1 + rs)).fillna(50)

    @staticmethod
    def _adx(df: DataFrame, period: int = 14) -> pd.Series:
        h, lo, c = df["high"], df["low"], df["close"]
        up = h.diff()
        dn = -lo.diff()
        plus_dm = np.where((up > dn) & (up > 0), up, 0.0)
        minus_dm = np.where((dn > up) & (dn > 0), dn, 0.0)
        tr = pd.concat([h - lo, (h - c.shift()).abs(), (lo - c.shift()).abs()], axis=1).max(axis=1)
        atr = tr.ewm(alpha=1 / period, adjust=False).mean().replace(0, np.nan)
        plus_di = 100 * pd.Series(plus_dm, index=df.index).ewm(alpha=1 / period).mean() / atr
        minus_di = 100 * pd.Series(minus_dm, index=df.index).ewm(alpha=1 / period).mean() / atr
        dx = 100 * (plus_di - minus_di).abs() / (plus_di + minus_di).replace(0, np.nan)
        return dx.ewm(alpha=1 / period, adjust=False).mean().fillna(0)

    @staticmethod
    def _volume_profile(df: DataFrame, lookback: int, bins: int, va: float):
        """Volume Profile cuốn chiếu (xấp xỉ) — POC/VAH/VAL. Nhân quả (chỉ nến quá khứ)."""
        hlc3 = ((df["high"] + df["low"] + df["close"]) / 3).to_numpy()
        vol = df["volume"].to_numpy()
        n = len(df)
        bins = max(4, int(bins))
        poc = np.full(n, np.nan)
        vah = np.full(n, np.nan)
        val = np.full(n, np.nan)
        for i in range(lookback, n):
            p = hlc3[i - lookback + 1 : i + 1]
            v = vol[i - lookback + 1 : i + 1]
            lo, hi = p.min(), p.max()
            if not (hi > lo) or v.sum() <= 0:
                continue
            edges = np.linspace(lo, hi, bins + 1)
            idx = np.clip(np.searchsorted(edges, p, side="right") - 1, 0, bins - 1)
            hist = np.zeros(bins)
            np.add.at(hist, idx, v)
            centers = (edges[:-1] + edges[1:]) / 2
            pb = int(hist.argmax())
            poc[i] = centers[pb]
            target = hist.sum() * va
            lo_b = hi_b = pb
            acc = hist[pb]
            while acc < target and (lo_b > 0 or hi_b < bins - 1):
                left = hist[lo_b - 1] if lo_b > 0 else -1.0
                right = hist[hi_b + 1] if hi_b < bins - 1 else -1.0
                if right >= left:
                    hi_b += 1
                    acc += hist[hi_b]
                else:
                    lo_b -= 1
                    acc += hist[lo_b]
            val[i] = centers[lo_b]
            vah[i] = centers[hi_b]
        return poc, vah, val

    # =============== Lớp A: Cấu trúc SMC (NHÂN QUẢ — tự tính) ============== #
    @staticmethod
    def _leg(df: DataFrame, size: int) -> np.ndarray:
        highest = df["high"].rolling(size).max().values
        lowest = df["low"].rolling(size).min().values
        high_s = df["high"].shift(size).values
        low_s = df["low"].shift(size).values
        n = len(df)
        leg = np.zeros(n, dtype=np.int8)
        cur = 0
        for i in range(n):
            if i >= size and high_s[i] > highest[i]:
                cur = 0
            elif i >= size and low_s[i] < lowest[i]:
                cur = 1
            leg[i] = cur
        return leg

    def _structure(self, df: DataFrame, size: int):
        """Cấu trúc 1 lớp + Bull OB (đếm tap) + last swing high/low. Nhân quả:
        pivot xác nhận SAU `size` nến; BOS/CHoCH khi close vượt swing chưa bị phá."""
        n = len(df)
        high = df["high"].values
        low = df["low"].values
        close = df["close"].values
        p_high = df["parsed_high"].values
        p_low = df["parsed_low"].values
        leg = self._leg(df, size)

        trend = np.zeros(n, dtype=np.int8)
        last_sh = np.full(n, np.nan)
        last_sl = np.full(n, np.nan)
        bull_top = np.full(n, np.nan)
        bull_bot = np.full(n, np.nan)
        bull_taps = np.zeros(n)

        sh_level = np.nan
        sh_idx = -1
        sh_crossed = True
        sl_level = np.nan
        sl_crossed = True
        bias = 0
        bull_obs: list[list[float]] = []  # mỗi OB = [top, bot, taps]

        for i in range(n):
            if i > 0 and leg[i] != leg[i - 1] and i >= size:
                if leg[i] == 0:
                    sh_level = high[i - size]
                    sh_idx = i - size
                    sh_crossed = False
                else:
                    sl_level = low[i - size]
                    sl_crossed = False

            if (
                not np.isnan(sh_level)
                and not sh_crossed
                and close[i] > sh_level
                and close[i - 1] <= sh_level
            ):
                bias = BULLISH
                sh_crossed = True
                if sh_idx >= 0:
                    seg = p_low[sh_idx : i + 1]
                    if seg.size:
                        j = sh_idx + int(np.argmin(seg))
                        # CHUẨN HOÁ top/bot: nến biến động cao bị hoán đổi parsed_high/parsed_low
                        # (xem populate_indicators) -> nếu lấy nguyên sẽ ra vùng LỘN NGƯỢC
                        # (top < bot), khiến OB bị prune ngay ở vòng lặp dưới và không bao giờ
                        # giao dịch được. Đúng những OB displacement mạnh lại bị vứt.
                        a, b = p_high[j], p_low[j]
                        bull_obs.append([max(a, b), min(a, b), 0.0])

            if (
                not np.isnan(sl_level)
                and not sl_crossed
                and close[i] < sl_level
                and close[i - 1] >= sl_level
            ):
                bias = BEARISH
                sl_crossed = True

            trend[i] = bias
            last_sh[i] = sh_level
            last_sl[i] = sl_level

            # Prune OB bị phá (mitigate hẳn) + đếm tap vào OB còn sống.
            kept = []
            for top, bot, taps in bull_obs:
                if low[i] < bot:
                    continue
                kept.append([top, bot, taps + (1.0 if low[i] <= top else 0.0)])
            bull_obs = kept
            if bull_obs:
                bull_top[i], bull_bot[i], bull_taps[i] = bull_obs[-1]

        return {
            "trend": trend,
            "last_sh": last_sh,
            "last_sl": last_sl,
            "bull_top": bull_top,
            "bull_bot": bull_bot,
            "bull_taps": bull_taps,
        }

    def _fvg(self, df: DataFrame, thresh_mult: float):
        """Fair Value Gap tăng (imbalance 3 nến + displacement auto-threshold). Nhân quả."""
        n = len(df)
        high = df["high"].values
        low = df["low"].values
        open_ = df["open"].values
        close = df["close"].values
        delta = np.zeros(n)
        for i in range(1, n):
            if open_[i - 1] != 0:
                delta[i] = (close[i - 1] - open_[i - 1]) / open_[i - 1]
        cum_mean = np.cumsum(np.abs(delta)) / np.maximum(np.arange(1, n + 1), 1)
        threshold = cum_mean * thresh_mult
        top = np.full(n, np.nan)
        bot = np.full(n, np.nan)
        cur_top = np.nan
        cur_bot = np.nan
        for i in range(2, n):
            if low[i] > high[i - 2] and close[i - 1] > high[i - 2] and delta[i] > threshold[i]:
                cur_top = low[i]
                cur_bot = high[i - 2]
            if not np.isnan(cur_bot) and low[i] < cur_bot:
                cur_top = np.nan
                cur_bot = np.nan
            top[i] = cur_top
            bot[i] = cur_bot
        return top, bot

    # ----------------- Informative HTF (bias Daily) ----------------------- #
    @informative("1d")
    def populate_indicators_1d(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        c = dataframe["close"]
        dataframe["ema50"] = c.ewm(span=50, adjust=False).mean()
        dataframe["ema200"] = c.ewm(span=200, adjust=False).mean()
        dataframe["bull"] = (
            (c > dataframe["ema50"]) & (dataframe["ema50"] > dataframe["ema200"])
        ).astype(float)
        return dataframe

    # ----------------------- Indicators ------------------------------------ #
    def populate_indicators(self, df: DataFrame, metadata: dict) -> DataFrame:
        # --- Lớp A: cấu trúc SMC (nhân quả) ---
        atr200 = self._atr(df, 200)
        high_vol = (df["high"] - df["low"]) >= (self.ob_atr_mult.value * atr200)
        df["parsed_high"] = np.where(high_vol, df["low"], df["high"])
        df["parsed_low"] = np.where(high_vol, df["high"], df["low"])

        internal = self._structure(df, int(self.internal_length.value))
        swing = self._structure(df, int(self.swing_length.value))
        df["internal_trend"] = internal["trend"]
        df["swing_trend"] = swing["trend"]
        df["bull_ob_top"] = internal["bull_top"]
        df["bull_ob_bot"] = internal["bull_bot"]
        df["bull_ob_taps"] = internal["bull_taps"]
        df["fvg_top"], df["fvg_bot"] = self._fvg(df, float(self.fvg_thresh_mult.value))

        # --- Premium/Discount + OTE từ dealing range (swing) ---
        sh = pd.Series(swing["last_sh"], index=df.index)
        sl = pd.Series(swing["last_sl"], index=df.index)
        rng = sh - sl
        df["swing_high"] = sh
        df["swing_low"] = sl
        df["equilibrium"] = (sh + sl) / 2
        df["premium_level"] = sl + self.premium_ratio.value * rng
        df["ote_low"] = sl + 0.214 * rng  # OTE 0.618-0.786 retracement nhịp tăng
        df["ote_high"] = sl + 0.382 * rng

        # --- Lớp B: indicator cổ điển ---
        df["atr"] = self._atr(df, 14)
        c = df["close"]
        df["ema50"] = c.ewm(span=50, adjust=False).mean()
        df["ema200"] = c.ewm(span=200, adjust=False).mean()
        df["rsi"] = self._rsi(c, 14)
        df["adx"] = self._adx(df, 14)
        df["rvol"] = df["volume"] / df["volume"].rolling(20).mean()
        poc, vah, val = self._volume_profile(
            df,
            int(self.vp_lookback.value),
            int(self.vp_bins.value),
            float(self.vp_value_area.value),
        )
        df["vp_poc"] = poc
        df["vp_vah"] = vah
        df["vp_val"] = val

        # --- Kế hoạch lệnh: vùng POI + giá entry + giá SL (tuyệt đối) ---
        df = self._entry_plan(df)

        # --- Lớp C: chấm điểm confluence ---
        df = self._confluence(df)
        return df

    # ------------------ Kế hoạch lệnh: POI / entry / SL -------------------- #
    def _entry_plan(self, df: DataFrame) -> DataFrame:
        """Quy vùng POI thành 3 con số: `poi_top`, `poi_bot`, `entry_price`, `sl_price`.

        POI = GIAO của Bull OB và FVG khi hai vùng chồng lấp (vùng chất lượng cao nhất),
        không chồng lấp thì lấy vùng nào đang có (ưu tiên OB).
        Toàn bộ chỉ dùng cột đã tính nhân quả -> không nhìn tương lai.
        """
        ob_top, ob_bot = df["bull_ob_top"], df["bull_ob_bot"]
        fvg_top, fvg_bot = df["fvg_top"], df["fvg_bot"]

        # Giao hai vùng (chỉ hợp lệ khi lo <= hi và cả hai vùng cùng tồn tại).
        inter_top = pd.concat([ob_top, fvg_top], axis=1).min(axis=1)
        inter_bot = pd.concat([ob_bot, fvg_bot], axis=1).max(axis=1)
        has_inter = ob_top.notna() & fvg_top.notna() & (inter_top >= inter_bot)

        poi_top = inter_top.where(has_inter, ob_top).fillna(fvg_top)
        poi_bot = inter_bot.where(has_inter, ob_bot).fillna(fvg_bot)
        valid = poi_top.notna() & poi_bot.notna() & (poi_top > poi_bot)
        df["poi_top"] = poi_top.where(valid)
        df["poi_bot"] = poi_bot.where(valid)
        df["poi_is_confluent"] = has_inter.fillna(False).astype(int)

        # Entry: đi sâu `entry_depth` vào vùng, tính từ mép trên.
        depth = float(self.entry_depth.value)
        df["entry_price"] = df["poi_top"] - depth * (df["poi_top"] - df["poi_bot"])

        # SL: neo dưới swing low gần nhất; kẹp không cao hơn đáy POI để luôn nằm
        # ngoài vùng (thủng POI = luận điểm sai). Cộng đệm %.
        anchor = pd.concat([df["swing_low"], df["poi_bot"]], axis=1).min(axis=1)
        anchor = anchor.fillna(df["poi_bot"])
        sl = anchor * (1 - self.sl_buffer_pct.value / 100)
        # Sàn khoảng cách: SL không được sát entry hơn `min_sl_pct`.
        sl_floor = df["entry_price"] * (1 - self.min_sl_pct.value / 100)
        df["sl_price"] = pd.concat([sl, sl_floor], axis=1).min(axis=1)

        df["risk_pct"] = (df["entry_price"] - df["sl_price"]) / df["entry_price"]
        # Khoảng cách từ giá hiện tại xuống entry (dương = limit nằm dưới giá).
        df["entry_gap_pct"] = (df["close"] - df["entry_price"]) / df["close"]

        # Kế hoạch dùng được: giá hợp lệ, SL dưới entry, rủi ro VÀ khoảng cách trong hạn mức.
        df["plan_ok"] = (
            valid
            & df["sl_price"].notna()
            & (df["sl_price"] > 0)
            & (df["sl_price"] < df["entry_price"])
            & (df["risk_pct"] <= self.max_risk_pct.value / 100)
            & (df["entry_gap_pct"] <= self.max_entry_gap_pct.value / 100)
        )
        return df

    # ------------------- Confluence scoring (0-100) ----------------------- #
    def _confluence(self, df: DataFrame) -> DataFrame:
        recent = int(self.confluence_recent.value)
        close = df["close"]

        in_bull_ob = ((df["low"] <= df["bull_ob_top"]) & (close >= df["bull_ob_bot"])).fillna(False)
        in_fvg = ((df["low"] <= df["fvg_top"]) & (close >= df["fvg_bot"])).fillna(False)
        df["in_poi"] = in_bull_ob | in_fvg

        # +20 CHoCH có displacement: internal trend vừa lật lên + có FVG gần đây.
        it = df["internal_trend"]
        choch_bull = (it > 0) & (it.shift(1) <= 0)
        choch_recent = choch_bull.rolling(recent, min_periods=1).max().fillna(0) > 0
        disp_recent = df["fvg_bot"].notna().rolling(recent, min_periods=1).max().fillna(0) > 0
        df["c_choch"] = (choch_recent & disp_recent).astype(int)

        # +15 Liquidity sweep (causal: quét đáy sweep_lookback rồi reclaim)
        win = int(self.sweep_lookback.value)
        prior_low = df["low"].rolling(win).min().shift(1)
        sweep_bull = (df["low"] < prior_low) & (close > prior_low)
        df["c_sweep"] = (
            sweep_bull.fillna(False).rolling(recent, min_periods=1).max().fillna(0) > 0
        ).astype(int)

        # +15 OB + FVG chồng lấp tại POI
        zones_overlap = (
            df[["bull_ob_bot", "fvg_bot"]].max(axis=1) <= df[["bull_ob_top", "fvg_top"]].min(axis=1)
        ).fillna(False)
        df["c_obfvg"] = (zones_overlap & (in_bull_ob | in_fvg)).astype(int)

        # +15 Nested POI (approx): Daily bull đồng thuận (TODO: OB-in-OB thật)
        bull_1d = df.get("bull_1d", pd.Series(0.0, index=df.index)).fillna(0)
        df["c_nested"] = ((bull_1d > 0) & (df["ema50"] > df["ema200"])).astype(int)

        # +10 BOS xác nhận (internal & swing cùng bull)
        df["c_bos"] = ((df["internal_trend"] > 0) & (df["swing_trend"] > 0)).astype(int)

        # +10 POC trùng Bull OB
        poc_in_ob = (
            (df["vp_poc"] >= df["bull_ob_bot"]) & (df["vp_poc"] <= df["bull_ob_top"])
        ).fillna(False)
        df["c_poc"] = poc_in_ob.astype(int)

        # +10 Vùng vào trong OTE
        df["c_ote"] = (
            ((close >= df["ote_low"]) & (close <= df["ote_high"])).fillna(False).astype(int)
        )

        # +5 RVOL nở tại nến phá
        df["c_vol"] = (df["rvol"] > self.rvol_mult).fillna(False).astype(int)

        # -20 Chỉ có LVN (trong POI, không POC/HVN, ngoài value area)
        outside_va = ((close < df["vp_val"]) | (close > df["vp_vah"])).fillna(False)
        df["p_lvn"] = (df["in_poi"] & (~poc_in_ob) & outside_va & df["vp_poc"].notna()).astype(int)

        # -20 ADX sideway
        df["p_adx"] = (df["adx"] < self.adx_min.value).astype(int)

        # -15 RR < 1:3 — đo trên KẾ HOẠCH THẬT (entry_price/sl_price), không phải giá close.
        risk = df["entry_price"] - df["sl_price"]
        reward = df["swing_high"] - df["entry_price"]
        rr = reward / risk.where(risk > 0)
        df["est_rr"] = rr
        df["p_rr"] = (rr.fillna(0) < 3).astype(int)

        # -10 OB mitigate nhiều lần (>=2 tap)
        df["p_mit"] = (df["bull_ob_taps"].fillna(0) >= 2).astype(int)

        df["score"] = (
            W_CHOCH * df["c_choch"]
            + W_SWEEP * df["c_sweep"]
            + W_OBFVG * df["c_obfvg"]
            + W_NESTED * df["c_nested"]
            + W_BOS * df["c_bos"]
            + W_POC * df["c_poc"]
            + W_OTE * df["c_ote"]
            + W_VOL * df["c_vol"]
            - P_LVN * df["p_lvn"]
            - P_ADX * df["p_adx"]
            - P_RR * df["p_rr"]
            - P_MIT * df["p_mit"]
        )
        return df

    # ------------------------- Entry --------------------------------------- #
    def _htf_bull(self, df: DataFrame) -> pd.Series:
        return df.get("bull_1d", pd.Series(np.nan, index=df.index)).fillna(1) > 0

    def populate_entry_trend(self, df: DataFrame, metadata: dict) -> DataFrame:
        """Bắn signal khi SETUP hợp lệ — KHÔNG đòi giá đang nằm trong vùng entry.

        Giá vào lệnh do `custom_entry_price` quyết định (limit tại POI). Nếu giá còn ở
        trên vùng thì lệnh nằm chờ; huỷ theo `unfilledtimeout` hoặc `check_entry_timeout`.
        """
        cond = [
            df["volume"] > 0,
            df["plan_ok"],  # có vùng entry + SL hợp lệ, rủi ro trong hạn mức
            df["close"] > df["sl_price"],  # luận điểm chưa bị phủ định
            df["score"] >= self.min_score.value,  # ngưỡng điểm
        ]
        if self.require_htf.value:
            cond.append(self._htf_bull(df))  # G1
        if self.require_discount.value:
            cond.append(df["close"] < df["equilibrium"])  # G2
        if self.require_bull_candle.value:
            cond.append(df["close"] > df["open"])

        sig = np.logical_and.reduce(cond)
        # Nhãn phân biệt để đối chiếu sau backtest: giá đã ở trong vùng hay còn phải chờ.
        df.loc[sig, "enter_long"] = 1
        df.loc[sig, "enter_tag"] = np.where(
            df.loc[sig, "close"] <= df.loc[sig, "poi_top"], "smc_tap", "smc_limit"
        )
        return df

    # -------------------------- Exit --------------------------------------- #
    def populate_exit_trend(self, df: DataFrame, metadata: dict) -> DataFrame:
        # Thoát khi cấu trúc lật xuống (mất luận điểm SMC).
        df.loc[df["internal_trend"] < 0, ["exit_long", "exit_tag"]] = (1, "smc_flip")
        return df

    def _last_row(self, pair: str):
        d, _ = self.dp.get_analyzed_dataframe(pair, self.timeframe)
        if d is None or len(d) == 0:
            return None
        return d.iloc[-1]

    # --------------------- Lệnh chờ tại vùng entry ------------------------- #
    def custom_entry_price(
        self, pair, trade, current_time, proposed_rate, entry_tag, side, **kwargs
    ) -> float:
        """Giá vào lệnh = giá POI đã tính, KHÔNG BAO GIỜ cao hơn giá thị trường.

        · Giá còn trên vùng -> trả entry < market => buy-limit nằm chờ (nhiều nến).
        · Giá đã ở trong/dưới vùng -> trả `proposed_rate` để khớp ngay, không trả giá
          cao hơn thị trường (limit trên market = khớp ngay ở giá xấu, mất hết ý nghĩa).
        """
        row = self._last_row(pair)
        if row is None:
            return proposed_rate
        ep = row.get("entry_price", np.nan)
        if ep is None or np.isnan(ep) or ep <= 0:
            return proposed_rate
        return float(min(ep, proposed_rate))

    def check_entry_timeout(self, pair, trade, order, current_time, **kwargs) -> bool:
        """Huỷ lệnh chờ khi luận điểm chết, không đợi hết `unfilledtimeout`.

        Được gọi SAU khi `unfilledtimeout` (12 nến) đã kiểm tra — xem interface.py:300.
        """
        row = self._last_row(pair)
        if row is None:
            return False
        # Cấu trúc lật xuống -> setup long không còn giá trị.
        if row.get("internal_trend", 0) < 0:
            return True
        # Giá thủng SL trước khi khớp -> vùng đã hỏng, đừng vào nữa.
        sl = row.get("sl_price", np.nan)
        if sl is not None and not np.isnan(sl) and row.get("close", np.inf) < sl:
            return True
        # POI biến mất (OB bị mitigate hẳn / FVG bị lấp).
        if not bool(row.get("plan_ok", False)):
            return True
        return False

    # ------------------- Đóng băng SL tại thời điểm khớp -------------------- #
    def _frozen_sl(self, pair: str, trade) -> float:
        """SL tuyệt đối, tính MỘT LẦN lúc khớp lệnh rồi giữ nguyên trong `custom_data`.

        Đóng băng là chủ đích: tính lại mỗi nến sẽ đẩy stop lên (trade_model.py:
        adjust_stop_loss chỉ đi lên) -> freqtrade gắn cờ trailing -> R:R méo.
        """
        cached = trade.get_custom_data("sl_price")
        if cached:
            return float(cached)

        open_rate = trade.open_rate
        row = self._last_row(pair)
        sl = row.get("sl_price", np.nan) if row is not None else np.nan
        if sl is None or np.isnan(sl) or sl <= 0 or sl >= open_rate:
            sl = open_rate * (1 - self.min_sl_pct.value / 100)
        # Không sát hơn min_sl_pct...
        sl = min(sl, open_rate * (1 - self.min_sl_pct.value / 100))
        # ...và không xa hơn max_risk_pct (hạn mức rủi ro thắng thế nếu hai điều kiện chọi nhau).
        sl = max(sl, open_rate * (1 - self.max_risk_pct.value / 100))

        trade.set_custom_data("sl_price", float(sl))
        trade.set_custom_data("r_pct", float((open_rate - sl) / open_rate))
        return float(sl)

    def _r_pct(self, pair: str, trade) -> float:
        """1R thật, theo tỉ lệ so với open_rate."""
        r = trade.get_custom_data("r_pct")
        if not r:
            self._frozen_sl(pair, trade)
            r = trade.get_custom_data("r_pct")
        return float(r) if r else self.min_sl_pct.value / 100

    def order_filled(self, pair, trade, order, current_time, **kwargs) -> None:
        """Khớp lệnh vào đầu tiên -> chốt cứng SL/R ngay, trước khi cấu trúc kịp trôi."""
        if order.ft_order_side == trade.entry_side and trade.nr_of_successful_entries == 1:
            self._frozen_sl(pair, trade)

    def custom_exit(self, pair, trade, current_time, current_rate, current_profit, **kwargs):
        """TP2 (chốt phần runner) + thoát cấu trúc (premium / VAH)."""
        r = self._r_pct(pair, trade)  # 1R THẬT = (entry - SL)/entry
        if current_profit >= self.tp2_rr.value * r:  # TP2: đạt tp2_rr x R -> chốt hết
            return "tp2"
        if current_profit <= 0:
            return None
        row = self._last_row(pair)
        if row is None:
            return None
        pr = row.get("premium_level", np.nan)  # chốt khi lên premium
        if not np.isnan(pr) and current_rate >= pr:
            return "premium_tp"
        if self.use_vp_exit.value:  # VAH — kháng cự thanh khoản
            vah = row.get("vp_vah", np.nan)
            if not np.isnan(vah) and current_rate >= vah:
                return "vp_vah_tp"
        return None

    def adjust_trade_position(
        self,
        trade,
        current_time,
        current_rate,
        current_profit,
        min_stake,
        max_stake,
        current_entry_rate,
        current_entry_profit,
        **kwargs,
    ):
        """TP1: chốt `tp1_share` vốn tại tp1_rr x R (một lần), để runner chạy.
        Sau TP1, custom_stoploss dời SL về breakeven."""
        if not self.use_partial_tp.value:
            return None
        if trade.nr_of_successful_exits >= 1:  # đã TP1
            return None
        r = self._r_pct(pair=trade.pair, trade=trade)
        if current_profit < self.tp1_rr.value * r:
            return None
        amount = trade.stake_amount * float(self.tp1_share.value)
        if min_stake is not None and amount < min_stake:
            return None
        return -amount

    def custom_stoploss(
        self, pair, trade, current_time, current_rate, current_profit, after_fill=False, **kwargs
    ):
        """SL ĐÓNG BĂNG dưới swing low (+ đệm), quy về ratio so với `current_rate`.

        Trả cùng một GIÁ tuyệt đối ở mọi nến => trade_model.py:adjust_stop_loss tính ra
        `stop_loss_norm` không đổi => không đẩy stop lên nữa.

        ★ Tham số `after_fill` KHÔNG thừa: strategy_resolver.py:240-244 dò tên tham số này
          để bật `_ft_stop_uses_after_fill`. Có nó, freqtrade đặt SL cấu trúc NGAY khi khớp
          qua `adjust_stop_loss(..., allow_refresh=True)` — đường này KHÔNG bật cờ
          `is_stop_loss_trailing`. Không có nó, SL phải "bò" từ mức đệm -10%
          (self.stoploss) lên mức cấu trúc ~2%, và freqtrade gắn nhãn mọi lần cắt là
          `trailing_stop_loss` — đúng lỗi đã thấy ở lần backtest trước.
        """
        sl = self._frozen_sl(pair, trade)
        if self.use_breakeven.value and trade.nr_of_successful_exits >= 1:
            sl = max(sl, trade.open_rate)  # lần dời DUY NHẤT (sau TP1)
        # stoploss_from_absolute trả về giá trị DƯƠNG; adjust_stop_loss dùng abs() nên hợp lệ.
        # Trả 0.0 khi sl >= current_rate -> freqtrade coi là không đổi, giữ stop cũ.
        return stoploss_from_absolute(
            sl, current_rate, is_short=trade.is_short, leverage=trade.leverage
        )

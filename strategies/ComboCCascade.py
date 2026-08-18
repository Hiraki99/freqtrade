"""Combo C — Derivatives Flow (Liquidation Cascade Fade). SRS §7.

★★ CẢNH BÁO TRƯỚC KHI ĐỌC BẤT KỲ SỐ NÀO CỦA COMBO NÀY ★★

FR-C KHÔNG backtest được một cách hợp lệ với dữ liệu hiện có, và file này KHÔNG giả vờ
ngược lại. Ba trong bốn điều kiện lọc của C-L1/C-L2 dựa trên dữ liệu phái sinh mà repo
không có và không lấy về được cho giai đoạn 2024-01 tới nay:

    ΔOI <= -1.5%                → Binance `/futures/data/openInterestHist` chỉ trả 30
                                  ngày gần nhất. KHÔNG có proxy. Bỏ hẳn điều kiện.
    liquidation notional >= P95 → `/fapi/v1/allForceOrders` đã ngừng phát hành lịch sử;
                                  stream WS lại bị throttle (RISK-04 đã ghi nhận). Thay
                                  bằng proxy VOLUME (xem `liq_proxy`), không tương đương.
    funding âm / perp discount  → có file funding_rate 1h trong kho nhưng đó là funding
                                  ĐÃ THANH TOÁN mỗi 8h, không phải funding ước tính
                                  real-time mà C-L2 cần. Bỏ hẳn điều kiện.

Còn lại duy nhất `Δprice <= -0.8% trong 5 phút` là đo được thật. Nói cách khác, cái chạy
được ở đây là "fade một cú sập nhanh có volume lớn", KHÔNG phải "fade một cascade thanh
lý". Hai thứ đó trùng nhau phần lớn thời gian nhưng không phải một; kết quả vì thế chỉ
có giá trị SÀNG LỌC (nếu ngay cả bản dễ này cũng âm thì FR-C khó cứu), tuyệt đối không
dùng để kết luận FR-C đạt hay trượt BT-06.

Ánh xạ SRS -> code
    C-L1  cascade: Δprice 5' <= -0.8% VÀ volume 5' >= P95(30 ngày)   -> `cascade_dn`
    C-L3  cascade_low / cascade_high                                  -> `casc_low/high`
    C-L4  nến cạn kiệt: delta > 0 VÀ close > (high+low)/2             -> `exhaust_up`
    C-L5  không thoả trong 10 nến -> huỷ setup                        -> `exhaust_window`
    C-L6  entry MARKET tại close (combo này chấp nhận taker)          -> order_types
    C-L7  SL = cascade_low - 0.15 * ATR(14)                           -> sl_price
    C-L8  0.35% <= s <= 1.5%                                          -> min/max_stop_pct
    C-X1  tối đa 2 lệnh / ngày                                        -> max_trades_per_day
    C-X2  cascade thứ hai khi đang có vị thế -> đóng ngay              -> custom_exit
"""

import numpy as np
import pandas as pd
from pandas import DataFrame
from scalp_combo_base import ScalpComboBase

from freqtrade.strategy import BooleanParameter, DecimalParameter, IntParameter


class ComboCCascade(ScalpComboBase):
    # 5m để so cùng khung với A và B; SRS viết theo nến 1m -> `--timeframe 1m` là bản
    # sát đặc tả nhất còn chạy được.
    timeframe = "5m"
    startup_candle_count = 400

    # C-L6: taker, vào ngay — không có lệnh chờ.
    order_types = {
        "entry": "market",
        "exit": "market",
        "stoploss": "market",
        "stoploss_on_exchange": False,
    }
    entry_valid_minutes = 1

    # C-X1 — chặt hơn G-02.
    max_trades_per_day = IntParameter(1, 10, default=2, space="buy", optimize=False)
    # C-L8 — cascade quá rộng là rủi ro đuôi, không phải cơ hội.
    max_stop_pct = DecimalParameter(0.5, 3.0, default=1.5, decimals=1, space="buy", optimize=False)

    # ---- 4 tham số hyperopt (BT-02) ---- #
    price_drop_pct = DecimalParameter(0.4, 2.0, default=0.8, decimals=1, space="buy", optimize=True)
    liq_quantile = DecimalParameter(
        0.80, 0.99, default=0.95, decimals=2, space="buy", optimize=True
    )
    exhaust_minutes = IntParameter(5, 60, default=10, space="buy", optimize=True)
    sl_atr_mult = DecimalParameter(0.05, 0.60, default=0.15, decimals=2, space="buy", optimize=True)

    # ---- Hằng số SRS ---- #
    cascade_window_min = 5
    liq_lookback_days = 30
    # Bật lại khi có dữ liệu OI/liquidation thật. Bật mà thiếu cột -> không có lệnh nào.
    require_oi = BooleanParameter(default=False, space="buy", optimize=False)

    # ------------------------------------------------------------------ #
    def populate_indicators(self, df: DataFrame, metadata: dict) -> DataFrame:
        w = self.bars(self.cascade_window_min)
        df["day"] = df["date"].dt.floor("1D")
        df["atr14"] = self.atr(df, 14)
        df["delta"] = self.cvd_proxy(df)
        df["ret5"] = (df["close"] / df["close"].shift(w) - 1) * 100
        df["vol5"] = df["volume"].rolling(w).sum()

        # Proxy cho "liquidation notional >= P95 của 30 ngày".
        # Không dùng rolling(8640).quantile trực tiếp: cửa sổ 30 ngày ở khung 5m là ~8.6k
        # nến, rolling-quantile trên 273k dòng sẽ mất hàng chục phút. Thay bằng P95 THEO
        # NGÀY rồi lấy trung bình trượt 30 ngày và dịch 1 ngày (causal — DR-04).
        daily_q = df.groupby("day")["vol5"].quantile(self.liq_quantile.value)
        thr = daily_q.rolling(self.liq_lookback_days, min_periods=5).mean().shift(1)
        df["liq_proxy_thr"] = df["day"].map(thr)

        df["casc_low"] = df["low"].rolling(w).min()
        df["casc_high"] = df["high"].rolling(w).max()
        return df

    # ------------------------------------------------------------------ #
    def populate_entry_trend(self, df: DataFrame, metadata: dict) -> DataFrame:
        df["enter_long"] = 0
        df["enter_short"] = 0
        df["entry_price"] = np.nan
        df["sl_price"] = np.nan
        df["enter_tag"] = None

        big_flow = df["vol5"] >= df["liq_proxy_thr"]
        if self.require_oi.value:
            oi = df.get("oi_change_pct")
            big_flow &= (oi <= -1.5) if oi is not None else False

        drop = self.price_drop_pct.value
        cascade_dn = (df["ret5"] <= -drop) & big_flow  # C-L1
        cascade_up = (df["ret5"] >= drop) & big_flow  # C-S1
        df["cascade_dn"] = cascade_dn.fillna(False)
        df["cascade_up"] = cascade_up.fillna(False)

        idx = pd.Series(np.arange(len(df)), index=df.index)
        ew = self.bars(self.exhaust_minutes.value)
        since_dn = idx - idx.where(cascade_dn).ffill()  # C-L5
        since_up = idx - idx.where(cascade_up).ffill()

        mid = (df["high"] + df["low"]) / 2
        exhaust_up = (df["delta"] > 0) & (df["close"] > mid)  # C-L4
        exhaust_dn = (df["delta"] < 0) & (df["close"] < mid)  # C-S4

        # Đáy/đỉnh của CỬA SỔ cascade, giữ nguyên trong lúc chờ nến cạn kiệt.
        casc_low_ref = df["casc_low"].where(cascade_dn).ffill()
        casc_high_ref = df["casc_high"].where(cascade_up).ffill()
        # Không vào lệnh khi cascade vẫn đang chạy (nến hiện tại vẫn thoả C-L1).
        long_sig = exhaust_up & since_dn.between(1, ew) & ~cascade_dn
        short_sig = exhaust_dn & since_up.between(1, ew) & ~cascade_up

        ep = df["close"]  # C-L6: market tại close
        sl_l = np.minimum(casc_low_ref, df["low"]) - self.sl_atr_mult.value * df["atr14"]
        sl_s = np.maximum(casc_high_ref, df["high"]) + self.sl_atr_mult.value * df["atr14"]

        df.loc[long_sig, "enter_long"] = 1
        df.loc[long_sig, "entry_price"] = ep[long_sig]
        df.loc[long_sig, "sl_price"] = sl_l[long_sig]
        df.loc[long_sig, "enter_tag"] = "C-L"

        short_sig &= ~long_sig
        df.loc[short_sig, "enter_short"] = 1
        df.loc[short_sig, "entry_price"] = ep[short_sig]
        df.loc[short_sig, "sl_price"] = sl_s[short_sig]
        df.loc[short_sig, "enter_tag"] = "C-S"

        return self.apply_risk_gates(df)

    # ------------------------------------------------------------------ #
    def custom_exit(self, pair, trade, current_time, current_rate, current_profit, **kwargs):
        """§4 + C-X2: cascade thứ hai xảy ra khi đang có vị thế -> đóng ngay."""
        base = super().custom_exit(
            pair, trade, current_time, current_rate, current_profit, **kwargs
        )
        if base:
            return base
        row = self._last_row(pair)
        if row is None:
            return None
        col = "cascade_up" if trade.is_short else "cascade_dn"
        if bool(row.get(col, False)):
            return "cascade_2"
        return None

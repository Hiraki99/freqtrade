"""Combo B — Volatility Breakout (Squeeze + OI). SRS-BTCSCALP-001 §6.

Bản chất: momentum. Nén biến động (BB nằm trong KC) rồi giải phóng theo hướng có
dòng tiền mới.

Ánh xạ SRS -> code
    FR-B-00  squeeze BB(20,2) trong KC(20, 1.5*ATR) trên 5m, >= 6 nến  -> `squeeze`
             blackout 03:00-06:00 UTC                                  -> `_liq_blackout`
    B-L1     squeeze_high / squeeze_low của TOÀN vùng nén              -> `sq_high/sq_low`
    B-L2     EMA(50, 15m) dốc lên / xuống so với 4 nến trước           -> informative 15m
    B-L3     nến 5m đóng cửa ngoài KC                                  -> `brk_up/brk_dn`
    B-L4     volume >= 2.0 * SMA(volume, 20)                           -> `vol_ok`
    B-L5     ΔOI >= +0.3%                                              -> KHÔNG CHẠY ĐƯỢC
    B-L6     limit tại BB band (retest), hiệu lực 4 nến 5m             -> entry_price
    B-L7     SL = max(squeeze_low, entry - 1.2*ATR(14,5m))             -> sl_price
    B-L8/L9  RM-04a + TP 1.15R                                          -> ScalpComboBase

★ B-L5 KHÔNG hiện thực được. Open Interest lịch sử không nằm trong kho dữ liệu của
  freqtrade và endpoint `/futures/data/openInterestHist` của Binance chỉ trả về 30 ngày
  gần nhất, trong khi backtest cần 2024-01 tới nay. Cổng này để `require_oi=False` và
  KHÔNG có proxy: mọi thay-thế nghĩ ra được (volume, biến động giá) đều đã nằm trong
  B-L3/B-L4, thêm vào chỉ là đếm hai lần cùng một thứ chứ không đo được "tiền mới".
  Hệ quả phải ghi vào báo cáo: kết quả dưới đây là FR-B *thiếu bộ lọc phân biệt
  breakout thật với short-covering* — tức bản dễ hơn thực tế ở khâu chọn lệnh, nhưng
  cũng nhiều nhiễu hơn. Đây là sai lệch có hướng KHÔNG xác định, không phải bias an toàn.
"""

import numpy as np
import pandas as pd
from pandas import DataFrame
from scalp_combo_base import ScalpComboBase

from freqtrade.strategy import (
    BooleanParameter,
    DecimalParameter,
    IntParameter,
    merge_informative_pair,
)


class ComboBSqueeze(ScalpComboBase):
    timeframe = "5m"
    inf_tf = "15m"
    startup_candle_count = 300

    # RM-04b — B-L6: lệnh chờ sống 4 nến 5m.
    entry_valid_minutes = 20

    # ---- 4 tham số được phép hyperopt (BT-02) ---- #
    squeeze_min_bars = IntParameter(3, 12, default=6, space="buy", optimize=True)
    vol_mult = DecimalParameter(1.0, 3.0, default=2.0, decimals=1, space="buy", optimize=True)
    sl_atr_mult = DecimalParameter(0.5, 2.5, default=1.2, decimals=1, space="buy", optimize=True)
    retest_window = IntParameter(1, 6, default=3, space="buy", optimize=True)

    # ---- Hằng số SRS, KHÔNG hyperopt ---- #
    bb_period = 20
    bb_std = 2.0
    kc_period = 20
    kc_mult = 1.5
    ema_trend = 50
    ema_slope_bars = 4
    # B-L5: bật lại khi nào có dữ liệu OI lịch sử. Bật mà không có cột `oi` -> chặn hết.
    require_oi = BooleanParameter(default=False, space="buy", optimize=False)
    # FR-B-00: giờ thanh khoản mỏng, fake breakout tỉ lệ cao.
    blackout_start_h = 3
    blackout_end_h = 6

    def informative_pairs(self):
        pairs = self.dp.current_whitelist() if self.dp else []
        return [(p, self.inf_tf) for p in pairs]

    # ------------------------------------------------------------------ #
    def populate_indicators(self, df: DataFrame, metadata: dict) -> DataFrame:
        c = df["close"]
        ma = c.rolling(self.bb_period).mean()
        sd = c.rolling(self.bb_period).std(ddof=0)
        df["bb_upper"] = ma + self.bb_std * sd
        df["bb_lower"] = ma - self.bb_std * sd

        atr_kc = self.atr(df, self.kc_period)
        ema_kc = c.ewm(span=self.kc_period, adjust=False).mean()
        df["kc_upper"] = ema_kc + self.kc_mult * atr_kc
        df["kc_lower"] = ema_kc - self.kc_mult * atr_kc
        df["atr14"] = self.atr(df, 14)
        df["vol_sma20"] = df["volume"].rolling(20).mean()

        # FR-B-00 — nén, và chiều dài chuỗi nén liên tiếp.
        sq = (df["bb_upper"] < df["kc_upper"]) & (df["bb_lower"] > df["kc_lower"])
        df["squeeze"] = sq.fillna(False)
        grp = (~df["squeeze"]).cumsum()  # mỗi chuỗi nén liên tiếp = một group
        run_len = df["squeeze"].groupby(grp).cumsum()
        # B-L1 — biên của TOÀN vùng nén, giữ lại (ffill) để nến breakout sau đó đọc được.
        hi_run = df["high"].where(df["squeeze"]).groupby(grp).cummax()
        lo_run = df["low"].where(df["squeeze"]).groupby(grp).cummin()
        df["sq_len"] = run_len.where(df["squeeze"]).ffill()
        df["sq_high"] = hi_run.where(df["squeeze"]).ffill()
        df["sq_low"] = lo_run.where(df["squeeze"]).ffill()
        # Số nến kể từ nến nén cuối cùng (0 = đang nén).
        idx = pd.Series(np.arange(len(df)), index=df.index)
        df["bars_since_sq"] = idx - idx.where(df["squeeze"]).ffill()

        # B-L2 — xu hướng khung 15m.
        if self.dp:
            inf = self.dp.get_pair_dataframe(metadata["pair"], self.inf_tf)
            inf["ema50"] = inf["close"].ewm(span=self.ema_trend, adjust=False).mean()
            inf["ema50_slope"] = inf["ema50"] - inf["ema50"].shift(self.ema_slope_bars)
            df = merge_informative_pair(df, inf, self.timeframe, self.inf_tf, ffill=True)
        else:  # pragma: no cover - chỉ xảy ra khi gọi ngoài freqtrade
            df[f"ema50_slope_{self.inf_tf}"] = np.nan
        return df

    def _liq_blackout(self, dates: pd.Series) -> pd.Series:
        h = dates.dt.hour
        return (h >= self.blackout_start_h) & (h < self.blackout_end_h)

    # ------------------------------------------------------------------ #
    def populate_entry_trend(self, df: DataFrame, metadata: dict) -> DataFrame:
        df["enter_long"] = 0
        df["enter_short"] = 0
        df["entry_price"] = np.nan
        df["sl_price"] = np.nan
        df["enter_tag"] = None

        slope = df.get(f"ema50_slope_{self.inf_tf}", pd.Series(np.nan, index=df.index))

        # Nến breakout phải nối tiếp một vùng nén đủ dài và vừa mới kết thúc.
        from_squeeze = (
            (df["sq_len"] >= self.squeeze_min_bars.value)
            & (df["bars_since_sq"] <= self.retest_window.value)
            & df["sq_high"].notna()
        )
        vol_ok = df["volume"] >= self.vol_mult.value * df["vol_sma20"]  # B-L4
        tradable = from_squeeze & vol_ok & ~self._liq_blackout(df["date"])

        if self.require_oi.value:  # B-L5 — không có cột `oi` thì không có lệnh nào cả
            oi = df.get("oi_change_pct")
            tradable &= (oi >= 0.3) if oi is not None else False

        # ---- LONG (B-L3 ... B-L7) ---- #
        long_sig = tradable & (df["close"] > df["kc_upper"]) & (slope > 0)
        ep_l = df["bb_upper"]  # B-L6: limit retest tại BB trên
        sl_l = np.maximum(df["sq_low"], ep_l - self.sl_atr_mult.value * df["atr14"])

        # ---- SHORT (B-S3 ... B-S7) ---- #
        short_sig = tradable & (df["close"] < df["kc_lower"]) & (slope < 0)
        ep_s = df["bb_lower"]
        sl_s = np.minimum(df["sq_high"], ep_s + self.sl_atr_mult.value * df["atr14"])

        df.loc[long_sig, "enter_long"] = 1
        df.loc[long_sig, "entry_price"] = ep_l[long_sig]
        df.loc[long_sig, "sl_price"] = sl_l[long_sig]
        df.loc[long_sig, "enter_tag"] = "B-L"

        # Long thắng khi trùng nến (không thể vừa đóng trên KC trên vừa dưới KC dưới,
        # nên nhánh này chỉ là phòng thủ).
        short_sig &= ~long_sig
        df.loc[short_sig, "enter_short"] = 1
        df.loc[short_sig, "entry_price"] = ep_s[short_sig]
        df.loc[short_sig, "sl_price"] = sl_s[short_sig]
        df.loc[short_sig, "enter_tag"] = "B-S"

        return self.apply_risk_gates(df)

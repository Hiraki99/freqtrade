"""Combo A — Auction Market Theory (VWAP + Volume Profile + CVD). SRS §5.

Bản chất: mean reversion. Giá bị đẩy ra ngoài vùng giá trị bởi dòng lệnh thiếu hấp thụ
và có xu hướng quay lại.

Ánh xạ SRS -> code
    FR-A-00  RANGE DAY: ATR(14,15m)/price < 0.45%, giá nằm trong VA phiên trước
             >= 60% thời lượng phiên, |VWAP slope| < 0.1%/giờ        -> `range_day`
    A-L1     trigger_zone = min(VAL_prev, VWAP - 2*sigma)                 -> `tz_long`
    A-L2     quét: low < trigger_zone (wick hợp lệ)                  -> `sweep_dn`
    A-L3     CVD divergence HOẶC absorption                          -> `confirm_dn`
    A-L4     nến đầu tiên đóng cửa trên trigger_zone                 -> `reclaim_up`
    A-L5     |POC - entry| >= 1.15 * risk_distance                   -> `struct_ok`
    A-L6     limit tại close nến A-L4, hiệu lực 3 nến                -> entry_price
    A-L7     SL = swing_low(A-L2) - 0.15 * ATR(14)                   -> sl_price
    A-L8/L9  RM-04a + TP 1.15R                                        -> ScalpComboBase

Hai sai lệch so với SRS, đều bắt buộc vì thiếu dữ liệu — phải đọc kèm kết quả:

★ DR-03 / RISK-05 — CVD dựng từ nến, không từ aggTrades. Xem `ScalpComboBase.cvd_proxy`.
  Bước A-L3 vì thế là ước lượng, không phải phép đo. Có công tắc `require_confirm` để
  chạy đối chứng bật/tắt và ước lượng phần đóng góp của nó.
★ RISK-05 — Volume Profile xấp xỉ bằng volume-by-price từ nến (mỗi nến dồn toàn bộ
  volume vào bin của typical price) chứ không phải tick. VAH/VAL/POC sẽ lệch; mức lệch
  chưa đo được vì không có tick data để đối chiếu.

FR-A-X (đóng sớm khi 2 nến liên tiếp đóng ngoài vùng theo hướng bất lợi) ĐƯỢC hiện thực
trong `custom_exit`.
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


def _day_profile(day: DataFrame, bins: int, va: float):
    """POC / VAH / VAL của một phiên UTC, từ volume-by-price xấp xỉ (RISK-05)."""
    if len(day) < 10 or day["volume"].sum() <= 0:
        return np.nan, np.nan, np.nan
    tp = (day["high"] + day["low"] + day["close"]) / 3
    lo, hi = float(day["low"].min()), float(day["high"].max())
    if not np.isfinite(lo) or not np.isfinite(hi) or hi <= lo:
        return np.nan, np.nan, np.nan
    edges = np.linspace(lo, hi, bins + 1)
    idx = np.clip(np.digitize(tp.to_numpy(), edges) - 1, 0, bins - 1)
    vol = np.bincount(idx, weights=day["volume"].to_numpy(), minlength=bins)
    centers = (edges[:-1] + edges[1:]) / 2

    poc_i = int(vol.argmax())
    target = vol.sum() * va
    lo_i = hi_i = poc_i
    acc = vol[poc_i]
    while acc < target and (lo_i > 0 or hi_i < bins - 1):
        # Mở rộng về phía có volume lớn hơn — định nghĩa Value Area chuẩn.
        down = vol[lo_i - 1] if lo_i > 0 else -1.0
        up = vol[hi_i + 1] if hi_i < bins - 1 else -1.0
        if up >= down:
            hi_i += 1
            acc += vol[hi_i]
        else:
            lo_i -= 1
            acc += vol[lo_i]
    return float(centers[poc_i]), float(centers[hi_i]), float(centers[lo_i])


class ComboAAuction(ScalpComboBase):
    # Mặc định 5m để so sánh cùng khung với hai combo kia; SRS viết theo nến 1m nên
    # `--timeframe 1m` là bản chạy đúng đặc tả (mọi cửa sổ đều khai báo bằng PHÚT).
    timeframe = "5m"
    inf_tf = "15m"
    startup_candle_count = 400

    # RM-04b — A-L6: lệnh chờ sống 3 nến.
    entry_valid_minutes = 15

    # ---- 4 tham số hyperopt (BT-02) ---- #
    sigma_mult = DecimalParameter(1.0, 3.0, default=2.0, decimals=1, space="buy", optimize=True)
    sl_atr_mult = DecimalParameter(0.05, 0.60, default=0.15, decimals=2, space="buy", optimize=True)
    reclaim_minutes = IntParameter(5, 60, default=20, space="buy", optimize=True)
    range_atr_max = DecimalParameter(
        0.20, 1.00, default=0.45, decimals=2, space="buy", optimize=True
    )

    # ---- Hằng số SRS ---- #
    vp_bins = 40
    vp_value_area = 0.70
    div_minutes = 20  # A-L3: cửa sổ so sánh phân kỳ
    absorb_pct_window = 100  # A-L3: cửa sổ tính P80 |delta|
    absorb_range_pct = 0.12  # A-L3: biên độ nến hấp thụ
    va_time_min = 0.60  # FR-A-00
    vwap_slope_max = 0.10  # FR-A-00, %/giờ
    require_confirm = BooleanParameter(default=True, space="buy", optimize=False)
    require_range_day = BooleanParameter(default=True, space="buy", optimize=False)

    def informative_pairs(self):
        pairs = self.dp.current_whitelist() if self.dp else []
        return [(p, self.inf_tf) for p in pairs]

    # ------------------------------------------------------------------ #
    def populate_indicators(self, df: DataFrame, metadata: dict) -> DataFrame:
        df["day"] = df["date"].dt.floor("1D")
        tp = (df["high"] + df["low"] + df["close"]) / 3
        pv = tp * df["volume"]

        g = df.groupby("day", sort=False)
        cum_v = g["volume"].cumsum().replace(0, np.nan)
        cum_pv = pv.groupby(df["day"], sort=False).cumsum()
        cum_pv2 = (tp * pv).groupby(df["day"], sort=False).cumsum()
        vwap = cum_pv / cum_v
        var = (cum_pv2 / cum_v - vwap**2).clip(lower=0)
        df["vwap"] = vwap
        df["vwap_sd"] = np.sqrt(var)
        # FR-A-00: độ dốc VWAP quy về %/giờ.
        df["vwap_slope"] = (vwap - vwap.shift(self.bars(60))) / vwap * 100

        # --- Volume profile phiên TRƯỚC (DR-04: chỉ dùng được từ 00:00 hôm sau) --- #
        prof = {}
        for day, chunk in g:
            prof[day] = _day_profile(chunk, self.vp_bins, self.vp_value_area)
        days = sorted(prof)
        shifted = {d: prof[days[i - 1]] for i, d in enumerate(days) if i > 0}
        default = (np.nan, np.nan, np.nan)
        pv_arr = np.array([shifted.get(d, default) for d in df["day"]], dtype=float)
        df["poc_prev"], df["vah_prev"], df["val_prev"] = pv_arr[:, 0], pv_arr[:, 1], pv_arr[:, 2]

        df["atr14"] = self.atr(df, 14)
        df["delta"] = self.cvd_proxy(df)
        df["cvd"] = df["delta"].groupby(df["day"], sort=False).cumsum()

        # FR-A-00 — tỉ lệ thời lượng phiên giá nằm trong VA phiên trước.
        inside = ((df["close"] >= df["val_prev"]) & (df["close"] <= df["vah_prev"])).astype(float)
        elapsed = g.cumcount() + 1
        df["va_time_frac"] = inside.groupby(df["day"], sort=False).cumsum() / elapsed

        if self.dp:
            inf = self.dp.get_pair_dataframe(metadata["pair"], self.inf_tf)
            inf["atr14"] = self.atr(inf, 14)
            inf["atr_pct"] = inf["atr14"] / inf["close"] * 100
            df = merge_informative_pair(df, inf, self.timeframe, self.inf_tf, ffill=True)
        else:  # pragma: no cover
            df[f"atr_pct_{self.inf_tf}"] = np.nan
        return df

    # ------------------------------------------------------------------ #
    def populate_entry_trend(self, df: DataFrame, metadata: dict) -> DataFrame:
        df["enter_long"] = 0
        df["enter_short"] = 0
        df["entry_price"] = np.nan
        df["sl_price"] = np.nan
        df["enter_tag"] = None

        atr_pct = df.get(f"atr_pct_{self.inf_tf}", pd.Series(np.nan, index=df.index))
        range_day = (
            (atr_pct < self.range_atr_max.value)
            & (df["va_time_frac"] >= self.va_time_min)
            & (df["vwap_slope"].abs() < self.vwap_slope_max)
        )
        if not self.require_range_day.value:
            range_day = pd.Series(True, index=df.index)

        s = self.sigma_mult.value
        df["tz_long"] = np.minimum(df["val_prev"], df["vwap"] - s * df["vwap_sd"])  # A-L1
        df["tz_short"] = np.maximum(df["vah_prev"], df["vwap"] + s * df["vwap_sd"])  # A-S1

        w = self.bars(self.reclaim_minutes.value)
        dv = self.bars(self.div_minutes)
        idx = pd.Series(np.arange(len(df)), index=df.index)

        # ---- A-L3 / A-S3: xác nhận hấp thụ ---- #
        p80 = df["delta"].abs().rolling(self.absorb_pct_window).quantile(0.8)
        rng_pct = (df["high"] - df["low"]) / df["close"] * 100
        narrow = rng_pct < self.absorb_range_pct
        price_ll = df["low"] <= df["low"].rolling(dv).min()
        price_hh = df["high"] >= df["high"].rolling(dv).max()
        cvd_ll = df["cvd"] <= df["cvd"].rolling(dv).min()
        cvd_hh = df["cvd"] >= df["cvd"].rolling(dv).max()
        confirm_dn = (price_ll & ~cvd_ll) | (narrow & (df["delta"] < -p80))
        confirm_up = (price_hh & ~cvd_hh) | (narrow & (df["delta"] > p80))
        if not self.require_confirm.value:
            confirm_dn = pd.Series(True, index=df.index)
            confirm_up = pd.Series(True, index=df.index)

        # ---- A-L2/A-L4: quét rồi hồi phục ---- #
        sweep_dn = df["low"] < df["tz_long"]
        sweep_up = df["high"] > df["tz_short"]
        # Xác nhận chỉ cần xảy ra ĐÂU ĐÓ trong đợt quét, không nhất thiết ở nến quét.
        conf_dn_win = (confirm_dn & sweep_dn).rolling(w, min_periods=1).max().astype(bool)
        conf_up_win = (confirm_up & sweep_up).rolling(w, min_periods=1).max().astype(bool)
        # "Nến ĐẦU TIÊN đóng cửa trên trigger_zone" = nến đầu tiên KỂ TỪ nến quét, chứ
        # KHÔNG phải nến có nến trước đó đóng dưới vùng. Phần lớn cú quét chỉ là RÂU nến
        # (A-L2 nói rõ "wick tính là hợp lệ") nên nến quét thường đã đóng trên vùng và
        # chính nó là nến A-L4. Đọc sai chỗ này làm 273k nến chỉ còn 147 tín hiệu.
        sweep_id_dn = idx.where(sweep_dn).ffill()
        sweep_id_up = idx.where(sweep_up).ffill()
        bars_since_dn = idx - sweep_id_dn
        bars_since_up = idx - sweep_id_up
        above = df["close"] > df["tz_long"]
        below = df["close"] < df["tz_short"]
        first_above = above & (above.groupby(sweep_id_dn).cumsum() == 1)
        first_below = below & (below.groupby(sweep_id_up).cumsum() == 1)

        reclaim_up = first_above & bars_since_dn.between(0, w) & conf_dn_win & range_day
        reclaim_dn = first_below & bars_since_up.between(0, w) & conf_up_win & range_day

        # ---- A-L6/A-L7 ---- #
        ep_l = df["close"]
        sl_l = df["low"].rolling(w + 1).min() - self.sl_atr_mult.value * df["atr14"]
        ep_s = df["close"]
        sl_s = df["high"].rolling(w + 1).max() + self.sl_atr_mult.value * df["atr14"]

        # ---- A-L5: mục tiêu cấu trúc phải xa hơn TP, nếu không thì bỏ ---- #
        struct_l = (df["poc_prev"] - ep_l).abs() >= self.rr.value * (ep_l - sl_l).abs()
        struct_s = (df["poc_prev"] - ep_s).abs() >= self.rr.value * (sl_s - ep_s).abs()

        long_sig = reclaim_up & struct_l
        short_sig = reclaim_dn & struct_s & ~reclaim_up

        df.loc[long_sig, "enter_long"] = 1
        df.loc[long_sig, "entry_price"] = ep_l[long_sig]
        df.loc[long_sig, "sl_price"] = sl_l[long_sig]
        df.loc[long_sig, "enter_tag"] = "A-L"

        df.loc[short_sig, "enter_short"] = 1
        df.loc[short_sig, "entry_price"] = ep_s[short_sig]
        df.loc[short_sig, "sl_price"] = sl_s[short_sig]
        df.loc[short_sig, "enter_tag"] = "A-S"

        return self.apply_risk_gates(df)

    # ------------------------------------------------------------------ #
    def custom_exit(self, pair, trade, current_time, current_rate, current_profit, **kwargs):
        """TP/SL/timeout của §4, cộng FR-A-X: huỷ sớm khi luận điểm hỏng.

        FR-A-X — 2 nến liên tiếp đóng ngoài trigger_zone theo hướng bất lợi nghĩa là
        giá không quay lại vùng giá trị: thoát ngay ở giá thị trường, không chờ SL.
        """
        base = super().custom_exit(
            pair, trade, current_time, current_rate, current_profit, **kwargs
        )
        if base:
            return base
        d, _ = self.dp.get_analyzed_dataframe(pair, self.timeframe)
        if d is None or len(d) < 2:
            return None
        last2 = d.iloc[-2:]
        col = "tz_short" if trade.is_short else "tz_long"
        tz = last2[col]
        if tz.isna().any():
            return None
        if trade.is_short:
            if bool((last2["close"] > tz).all()):
                return "invalidated"
        elif bool((last2["close"] < tz).all()):
            return "invalidated"
        return None

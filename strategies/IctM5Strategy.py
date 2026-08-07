"""
IctM5Strategy — Mô hình ICT 2022 (NHÂN QUẢ / No-lookahead), entry khung M5.

Chuỗi ICT (guide docs/ict/ICT_M15_M5_Entry_Guide.txt), long & short đối xứng:

    Bias (H4 EMA200)  →  Liquidity Sweep  →  Displacement/MSS  →  FVG
        →  M5 retest FVG  →  Entry  →  SL dưới/trên vùng sweep  →  TP tại liquidity (RR ≥ min).

KIẾN TRÚC MULTI-TIMEFRAME (theo guide H4/M15/M5):
  • @informative("4h") : BIAS. EMA200/EMA50 — chỉ vào lệnh THUẬN bias H4.
  • @informative("15m"): SETUP. Máy trạng thái ICT (nhân quả) phát hiện
        Sweep → MSS → FVG rồi "khoá" vùng FVG làm POI + mức SL (sweep) + mức
        liquidity target. Xuất các cột ict_bull_* / ict_bear_*.
  • timeframe "5m"     : ENTRY. Khi giá M5 retest vào vùng FVG (POI) đã khoá,
        thuận bias H4, đạt RR tối thiểu → vào lệnh.

★ TÍNH NHÂN QUẢ (chống lookahead — bài học SmcElliott):
  - Không dùng thư viện smartmoneyconcepts (repaint). Toàn bộ cấu trúc tự tính:
    pivot chỉ xác nhận SAU `size` nến; MSS khi close vượt swing CHƯA bị phá.
  - @informative merge bằng merge_informative_pair → chỉ dùng nến HTF ĐÃ ĐÓNG.
  - Máy trạng thái _ict_setups là một vòng lặp tiến (forward) → chỉ đọc quá khứ.
  LUÔN chạy `lookahead-analysis` sau khi sửa để xác nhận.

★ YÊU CẦU CHẠY:
  - can_short=True ⇒ BẮT BUỘC config futures (spot sẽ fail load).
      freqtrade backtesting -c config-ict-futures.json --strategy IctM5Strategy
  - Cần data 4h + 15m + 5m:
      freqtrade download-data -c config-ict-futures.json -t 5m 15m 4h --timerange 20240101-

★ TODO khi có data: hiệu chỉnh sweep_lookback / mss_expiry / setup_expiry / min_rr
  theo backtest + factor analysis. Số mặc định là scaffold, ĐỪNG tin tới khi data xác nhận.
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


class IctM5Strategy(IStrategy):
    INTERFACE_VERSION = 3

    timeframe = "5m"
    can_short = True  # ⇒ chạy futures. Spot: strategy_resolver sẽ từ chối load.

    # Thoát bằng SL cấu trúc (custom_stoploss) + TP liquidity (custom_exit) + MSS đảo chiều.
    minimal_roi = {"0": 100}  # vô hiệu ROI mặc định
    stoploss = -0.10  # hard cap; SL thực = dưới/trên vùng sweep (custom_stoploss)
    trailing_stop = False
    use_custom_stoploss = True
    use_exit_signal = True

    process_only_new_candles = True
    startup_candle_count = 400

    # =============================== THAM SỐ ================================= #
    # --- Cấu trúc / MSS / FVG trên 15m ---
    internal_length = IntParameter(3, 15, default=5, space="buy", optimize=True)
    fvg_thresh_mult = DecimalParameter(
        1.0, 4.0, default=1.5, decimals=1, space="buy", optimize=True
    )
    sweep_lookback = IntParameter(8, 40, default=20, space="buy", optimize=True)
    target_lookback = IntParameter(20, 120, default=60, space="buy", optimize=True)
    # Cửa sổ thời gian (số nến 15m) cho phép giữa các bước của chuỗi ICT.
    mss_expiry = IntParameter(3, 20, default=8, space="buy", optimize=True)  # sweep→MSS
    setup_expiry = IntParameter(6, 40, default=18, space="buy", optimize=True)  # armed→retest

    # --- Bias H4 ---
    require_htf_bias = BooleanParameter(default=True, space="buy", optimize=False)
    require_ema_stack = BooleanParameter(default=False, space="buy", optimize=True)  # EMA50 xếp lớp

    # --- Entry M5 ---
    require_confirm_candle = BooleanParameter(default=True, space="buy", optimize=True)
    min_rr = DecimalParameter(1.5, 4.0, default=2.0, decimals=1, space="buy", optimize=True)

    # --- Killzone (mặc định TẮT: crypto 24/7; bật để backtest) ---
    require_killzone = BooleanParameter(default=False, space="buy", optimize=False)

    # --- SL / TP ---
    sl_buffer_pct = DecimalParameter(0.1, 1.5, default=0.3, decimals=1, space="sell", optimize=True)

    # Cửa sổ killzone NY (giờ, America/New_York) — London + NY open (guide/pine macro).
    _killzones = ((2, 5), (7, 10), (13, 16))

    # ============================= Protections ============================== #
    @property
    def protections(self):
        return [
            {"method": "CooldownPeriod", "stop_duration_candles": 3},
            {
                "method": "StoplossGuard",
                "lookback_period_candles": 288,  # ~1 ngày M5
                "trade_limit": 4,
                "stop_duration_candles": 24,
                "only_per_pair": False,
            },
        ]

    # ==================== Helper cấu trúc (NHÂN QUẢ) ======================== #
    @staticmethod
    def _atr(high, low, close, period: int = 14) -> np.ndarray:
        h, lo, c = (pd.Series(high), pd.Series(low), pd.Series(close))
        tr = pd.concat([h - lo, (h - c.shift()).abs(), (lo - c.shift()).abs()], axis=1).max(axis=1)
        return tr.ewm(alpha=1 / period, adjust=False).mean().to_numpy()

    @staticmethod
    def _leg(high: np.ndarray, low: np.ndarray, size: int) -> np.ndarray:
        """Xác định chân sóng lên/xuống. Pivot xác nhận SAU `size` nến (nhân quả)."""
        highest = pd.Series(high).rolling(size).max().to_numpy()
        lowest = pd.Series(low).rolling(size).min().to_numpy()
        high_s = pd.Series(high).shift(size).to_numpy()
        low_s = pd.Series(low).shift(size).to_numpy()
        n = len(high)
        leg = np.zeros(n, dtype=np.int8)
        cur = 0
        for i in range(n):
            if i >= size and high_s[i] > highest[i]:
                cur = 0  # leg up
            elif i >= size and low_s[i] < lowest[i]:
                cur = 1  # leg down
            leg[i] = cur
        return leg

    def _structure(self, high, low, close, size: int):
        """Trend + swing high/low gần nhất. BOS/CHoCH (=MSS) khi close vượt swing
        chưa bị phá. Nhân quả: swing xác nhận sau `size` nến."""
        n = len(high)
        leg = self._leg(high, low, size)
        trend = np.zeros(n, dtype=np.int8)
        last_sh = np.full(n, np.nan)
        last_sl = np.full(n, np.nan)
        sh_level = np.nan
        sh_crossed = True
        sl_level = np.nan
        sl_crossed = True
        bias = 0
        for i in range(n):
            if i > 0 and leg[i] != leg[i - 1] and i >= size:
                if leg[i] == 0:
                    sh_level = high[i - size]
                    sh_crossed = False
                else:
                    sl_level = low[i - size]
                    sl_crossed = False
            if not np.isnan(sh_level) and not sh_crossed and close[i] > sh_level >= close[i - 1]:
                bias = BULLISH
                sh_crossed = True
            if not np.isnan(sl_level) and not sl_crossed and close[i] < sl_level <= close[i - 1]:
                bias = BEARISH
                sl_crossed = True
            trend[i] = bias
            last_sh[i] = sh_level
            last_sl[i] = sl_level
        return trend, last_sh, last_sl

    @staticmethod
    def _fvg(high, low, open_, close, thresh_mult: float):
        """FVG tăng & giảm (imbalance 3 nến + displacement auto-threshold). Nhân quả.
        Trả về (bull_top, bull_bot, bear_top, bear_bot) — vùng gap còn hiệu lực gần nhất."""
        n = len(high)
        delta = np.zeros(n)
        for i in range(1, n):
            if open_[i - 1] != 0:
                delta[i] = (close[i - 1] - open_[i - 1]) / open_[i - 1]
        cum_mean = np.cumsum(np.abs(delta)) / np.maximum(np.arange(1, n + 1), 1)
        thr = cum_mean * thresh_mult
        bull_top = np.full(n, np.nan)
        bull_bot = np.full(n, np.nan)
        bear_top = np.full(n, np.nan)
        bear_bot = np.full(n, np.nan)
        ub_t = ub_b = eb_t = eb_b = np.nan
        for i in range(2, n):
            # Bullish FVG: gap up + displacement lên
            if low[i] > high[i - 2] and close[i - 1] > high[i - 2] and delta[i] > thr[i]:
                ub_b = high[i - 2]
                ub_t = low[i]
            if not np.isnan(ub_b) and close[i] < ub_b:  # đã lấp
                ub_t = ub_b = np.nan
            # Bearish FVG: gap down + displacement xuống
            if high[i] < low[i - 2] and close[i - 1] < low[i - 2] and delta[i] < -thr[i]:
                eb_t = low[i - 2]
                eb_b = high[i]
            if not np.isnan(eb_t) and close[i] > eb_t:  # đã lấp
                eb_t = eb_b = np.nan
            bull_top[i], bull_bot[i] = ub_t, ub_b
            bear_top[i], bear_bot[i] = eb_t, eb_b
        return bull_top, bull_bot, bear_top, bear_bot

    def _run_setup(self, ctx: dict, is_bull: bool):
        """Máy trạng thái ICT 1 chiều (nhân quả): sweep → MSS thuận → khoá FVG làm POI.
        `ctx` gồm các mảng đã tính. Trả (active, fvg_top, fvg_bot, sl, liq)."""
        n = ctx["n"]
        close = ctx["close"]
        sweep = ctx["sweep_bull"] if is_bull else ctx["sweep_bear"]
        sweep_px = ctx["low"] if is_bull else ctx["high"]
        mss_go = ctx["mss_bull"] if is_bull else ctx["mss_bear"]
        mss_stop = ctx["mss_bear"] if is_bull else ctx["mss_bull"]
        fvg_top = ctx["bull_top"] if is_bull else ctx["bear_top"]
        fvg_bot = ctx["bull_bot"] if is_bull else ctx["bear_bot"]
        liq = ctx["liq_high"] if is_bull else ctx["liq_low"]
        mss_exp, set_exp = ctx["mss_exp"], ctx["set_exp"]

        active = np.zeros(n)
        o_top = np.full(n, np.nan)
        o_bot = np.full(n, np.nan)
        o_sl = np.full(n, np.nan)
        o_liq = np.full(n, np.nan)

        armed = False
        a_top = a_bot = a_sl = a_liq = np.nan
        arm_dl = -1
        pend = False
        s_px = np.nan
        pend_dl = -1
        for i in range(n):
            if sweep[i]:
                pend, s_px, pend_dl = True, sweep_px[i], i + mss_exp
            if pend and i > pend_dl:
                pend = False
            if pend and mss_go[i] and not np.isnan(fvg_bot[i]):
                armed = True
                a_top, a_bot = fvg_top[i], fvg_bot[i]
                a_sl = min(s_px, a_bot) if is_bull else max(s_px, a_top)
                a_liq = liq[i]
                arm_dl = i + set_exp
                pend = False
            if armed:
                zone_broken = close[i] < a_bot if is_bull else close[i] > a_top
                sl_broken = close[i] < a_sl if is_bull else close[i] > a_sl
                if zone_broken or sl_broken or mss_stop[i] or i > arm_dl:
                    armed = False
            if armed:
                active[i] = 1.0
                o_top[i], o_bot[i], o_sl[i], o_liq[i] = a_top, a_bot, a_sl, a_liq
        return active, o_top, o_bot, o_sl, o_liq

    def _ict_setups(self, df: DataFrame):
        """Tính primitives (nhân quả) rồi chạy máy trạng thái cho CẢ 2 chiều.
        Trả dict cột: active / fvg_top / fvg_bot / sl / liq cho bull & bear."""
        high = df["high"].to_numpy()
        low = df["low"].to_numpy()
        close = df["close"].to_numpy()
        open_ = df["open"].to_numpy()
        n = len(df)

        it, _, _ = self._structure(high, low, close, int(self.internal_length.value))
        bull_top, bull_bot, bear_top, bear_bot = self._fvg(
            high, low, open_, close, float(self.fvg_thresh_mult.value)
        )

        win = int(self.sweep_lookback.value)
        prior_low = pd.Series(low).rolling(win).min().shift(1).to_numpy()
        prior_high = pd.Series(high).rolling(win).max().shift(1).to_numpy()
        tw = int(self.target_lookback.value)
        # Liquidity resting: đỉnh/đáy chưa bị lấy trong tw nến trước (target TP).
        liq_high = pd.Series(high).rolling(tw).max().shift(1).to_numpy()
        liq_low = pd.Series(low).rolling(tw).min().shift(1).to_numpy()

        mss_bull = np.zeros(n, dtype=bool)
        mss_bear = np.zeros(n, dtype=bool)
        mss_bull[1:] = (it[1:] > 0) & (it[:-1] <= 0)
        mss_bear[1:] = (it[1:] < 0) & (it[:-1] >= 0)

        ctx = {
            "n": n,
            "high": high,
            "low": low,
            "close": close,
            "sweep_bull": (low < prior_low) & (close > prior_low),  # sell-side grab + reclaim
            "sweep_bear": (high > prior_high) & (close < prior_high),  # buy-side grab + reject
            "mss_bull": mss_bull,
            "mss_bear": mss_bear,
            "bull_top": bull_top,
            "bull_bot": bull_bot,
            "bear_top": bear_top,
            "bear_bot": bear_bot,
            "liq_high": liq_high,
            "liq_low": liq_low,
            "mss_exp": int(self.mss_expiry.value),
            "set_exp": int(self.setup_expiry.value),
        }

        out = {}
        for side, is_bull in (("bull", True), ("bear", False)):
            act, top, bot, sl, liq = self._run_setup(ctx, is_bull)
            out[f"{side}_active"] = act
            out[f"{side}_fvg_top"] = top
            out[f"{side}_fvg_bot"] = bot
            out[f"{side}_sl"] = sl
            out[f"{side}_liq"] = liq
        return out

    # =========================== Informative HTF =========================== #
    @informative("4h")
    def populate_indicators_4h(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        c = dataframe["close"]
        dataframe["ema50"] = c.ewm(span=50, adjust=False).mean()
        dataframe["ema200"] = c.ewm(span=200, adjust=False).mean()
        return dataframe

    @informative("15m")
    def populate_indicators_15m(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        setups = self._ict_setups(dataframe)
        for k, v in setups.items():
            dataframe[f"ict_{k}"] = v
        return dataframe

    # ============================== Indicators ============================= #
    def populate_indicators(self, df: DataFrame, metadata: dict) -> DataFrame:
        # Killzone NY (mặc định không lọc). Index là UTC-aware.
        if self.require_killzone.value:
            ny_hour = df["date"].dt.tz_convert("America/New_York").dt.hour
            in_kz = pd.Series(False, index=df.index)
            for h0, h1 in self._killzones:
                in_kz |= (ny_hour >= h0) & (ny_hour < h1)
            df["in_killzone"] = in_kz
        else:
            df["in_killzone"] = True
        return df

    # =============================== Bias H4 =============================== #
    def _bias_bull(self, df: DataFrame) -> pd.Series:
        c = df["close"]
        ok = c > df["ema200_4h"]
        if self.require_ema_stack.value:
            ok &= df["ema50_4h"] > df["ema200_4h"]
        return ok.fillna(False)

    def _bias_bear(self, df: DataFrame) -> pd.Series:
        c = df["close"]
        ok = c < df["ema200_4h"]
        if self.require_ema_stack.value:
            ok &= df["ema50_4h"] < df["ema200_4h"]
        return ok.fillna(False)

    # ================================ Entry ================================ #
    def populate_entry_trend(self, df: DataFrame, metadata: dict) -> DataFrame:
        c = df["close"]
        vol_ok = df["volume"] > 0

        # --- LONG: retest bull FVG đã khoá ---
        bull_sl = df["ict_bull_sl_15m"]
        bull_liq = df["ict_bull_liq_15m"]
        risk_l = c - bull_sl
        rr_l = (bull_liq - c) / risk_l.where(risk_l > 0)
        long_cond = [
            vol_ok,
            df["in_killzone"],
            df["ict_bull_active_15m"] > 0,
            df["low"] <= df["ict_bull_fvg_top_15m"],  # đã chạm vùng FVG
            c >= df["ict_bull_fvg_bot_15m"],  # nhưng chưa lấp hẳn (close còn trong vùng)
            risk_l > 0,
            rr_l >= self.min_rr.value,  # cổng RR tại liquidity
        ]
        if self.require_htf_bias.value:
            long_cond.append(self._bias_bull(df))
        if self.require_confirm_candle.value:
            long_cond.append(c > df["open"])  # nến M5 xác nhận (bullish)
        df.loc[np.logical_and.reduce(long_cond), ["enter_long", "enter_tag"]] = (1, "ict_long")

        # --- SHORT: retest bear FVG đã khoá ---
        bear_sl = df["ict_bear_sl_15m"]
        bear_liq = df["ict_bear_liq_15m"]
        risk_s = bear_sl - c
        rr_s = (c - bear_liq) / risk_s.where(risk_s > 0)
        short_cond = [
            vol_ok,
            df["in_killzone"],
            df["ict_bear_active_15m"] > 0,
            df["high"] >= df["ict_bear_fvg_bot_15m"],  # đã chạm vùng FVG (từ dưới lên)
            c <= df["ict_bear_fvg_top_15m"],
            risk_s > 0,
            rr_s >= self.min_rr.value,
        ]
        if self.require_htf_bias.value:
            short_cond.append(self._bias_bear(df))
        if self.require_confirm_candle.value:
            short_cond.append(c < df["open"])  # nến M5 xác nhận (bearish)
        df.loc[np.logical_and.reduce(short_cond), ["enter_short", "enter_tag"]] = (1, "ict_short")
        return df

    # ================================ Exit ================================= #
    def populate_exit_trend(self, df: DataFrame, metadata: dict) -> DataFrame:
        # Thoát khi setup phía mình biến mất (MSS đảo chiều làm active tắt).
        df.loc[df["ict_bull_active_15m"] <= 0, ["exit_long", "exit_tag"]] = (1, "ict_flip")
        df.loc[df["ict_bear_active_15m"] <= 0, ["exit_short", "exit_tag"]] = (1, "ict_flip")
        return df

    # ======================= Lưu mức SL/TP tại entry ======================= #
    def _last_row(self, pair: str):
        d, _ = self.dp.get_analyzed_dataframe(pair, self.timeframe)
        if d is None or len(d) == 0:
            return None
        return d.iloc[-1]

    def confirm_trade_entry(
        self, pair, order_type, amount, rate, time_in_force, current_time, entry_tag, side, **kwargs
    ) -> bool:
        """Khoá mức SL (sweep) + TP (liquidity) tại thời điểm vào lệnh, giữ cố định
        suốt vòng đời lệnh (setup 15m có thể tắt sau đó → không đọc lại)."""
        row = self._last_row(pair)
        if row is None:
            return True
        if side == "long":
            sl = row.get("ict_bull_sl_15m", np.nan)
            tp = row.get("ict_bull_liq_15m", np.nan)
        else:
            sl = row.get("ict_bear_sl_15m", np.nan)
            tp = row.get("ict_bear_liq_15m", np.nan)
        if not hasattr(self, "_levels"):
            self._levels: dict = {}
        self._levels[pair] = {"sl": float(sl), "tp": float(tp), "side": side}
        return True

    def _levels_for(self, pair: str, trade):
        lv = getattr(self, "_levels", {}).get(pair)
        if lv and lv["side"] == ("short" if trade.is_short else "long"):
            return lv
        return None

    # ============================ SL / TP động ============================= #
    def custom_stoploss(
        self, pair, trade, current_time, current_rate, current_profit, after_fill, **kwargs
    ):
        lv = self._levels_for(pair, trade)
        if lv is None or np.isnan(lv["sl"]):
            return None
        buf = self.sl_buffer_pct.value / 100
        if trade.is_short:
            stop_price = lv["sl"] * (1 + buf)  # SL trên vùng sweep
        else:
            stop_price = lv["sl"] * (1 - buf)  # SL dưới vùng sweep
        ratio = stoploss_from_absolute(
            stop_price, current_rate, is_short=trade.is_short, leverage=trade.leverage
        )
        return ratio if ratio > 0 else None

    def custom_exit(self, pair, trade, current_time, current_rate, current_profit, **kwargs):
        """Chốt tại liquidity target (RR đã lọc ở entry)."""
        lv = self._levels_for(pair, trade)
        if lv is None or np.isnan(lv["tp"]):
            return None
        if trade.is_short:
            if current_rate <= lv["tp"]:
                return "liq_tp"
        else:
            if current_rate >= lv["tp"]:
                return "liq_tp"
        return None

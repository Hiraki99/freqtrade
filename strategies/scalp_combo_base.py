"""Nền chung cho 3 combo scalping BTC perp — SRS-BTCSCALP-001.

Module này hiện thực §4 (Mô hình rủi ro toàn cục) một lần duy nhất, để ba file combo
chỉ còn phải lo phần tín hiệu vào lệnh của riêng nó:

    RM-01  TP cố định 1.15R, không partial, không trailing
    RM-02  position size = equity * risk_pct / risk_distance
    RM-04a stop tối thiểu 0.35% — setup hẹp hơn bị LOẠI, không co TP để bù
    RM-04b entry limit (maker), hết hạn sau N nến thì huỷ, không đuổi giá
    G-01   1 vị thế mở (đặt bằng max_open_trades trong config)
    G-02   tối đa N lệnh / ngày UTC
    G-03   dừng ngày nếu lỗ lũy kế >= 2% equity
    G-05   không vào lệnh quanh mốc settle funding
    G-07   timeout đóng lệnh sau 45 phút

Điều KHÔNG hiện thực được và lý do (xem báo cáo docs/BT-BTC-Scalping-Combos.md):
    G-04   dừng hệ thống khi DD >= 8% — là quy trình vận hành thủ công, không phải
           luật backtest; bật nó trong backtest sẽ cắt cụt mẫu và làm đẹp DD giả tạo.
    G-06   blackout macro (CPI/FOMC/NFP) — repo không có lịch sự kiện.

Quy ước quan trọng: mọi cửa sổ đều khai báo bằng PHÚT rồi quy ra số nến theo
`self.timeframe`. Nhờ vậy cùng một file chạy đúng ở cả 1m lẫn 5m, và con số trong SRS
(vốn viết theo nến 1m) không bị đọc sai thành "N nến 5m".
"""

from datetime import UTC, timedelta

import numpy as np
import pandas as pd
from pandas import DataFrame

from freqtrade.exchange import timeframe_to_minutes
from freqtrade.persistence import Trade
from freqtrade.strategy import (
    DecimalParameter,
    IntParameter,
    IStrategy,
    stoploss_from_absolute,
)


# Mốc settle funding của Binance USDⓈ-M (giờ UTC) — G-05.
FUNDING_HOURS = (0, 8, 16)


class ScalpComboBase(IStrategy):
    """Khung rủi ro dùng chung. Lớp con phải sinh ra các cột:

    ``enter_long`` / ``enter_short``  tín hiệu
    ``entry_price``                   giá đặt limit (RM-04b)
    ``sl_price``                      giá stop tuyệt đối (dùng để tính 1R)
    ``enter_tag``                     mã bước trong SRS, để truy vết
    """

    INTERFACE_VERSION = 3
    can_short = True

    # Thoát hoàn toàn bằng custom_exit/custom_stoploss — vô hiệu ROI & stop tĩnh.
    # stoploss = -0.99 chứ không phải -0.10: SL thật là giá tuyệt đối do custom_stoploss
    # trả về; giá trị ở đây chỉ là trần cứng, đặt hẹp sẽ cắt trước SL cấu trúc.
    minimal_roi = {"0": 100}
    stoploss = -0.99
    trailing_stop = False
    use_custom_stoploss = True
    use_exit_signal = True
    position_adjustment_enable = False  # RM-01: không partial TP
    process_only_new_candles = True

    # --------------------------- Tham số §4 -------------------------------- #
    # RM-01. Cố định theo SRS; để optimize=False vì đây là ràng buộc đề bài,
    # không phải biến tự do (BT-02 chỉ cho 4 tham số hyperopt / combo).
    rr = DecimalParameter(1.0, 3.0, default=1.15, decimals=2, space="sell", optimize=False)
    # RM-04a
    min_stop_pct = DecimalParameter(
        0.20, 1.00, default=0.35, decimals=2, space="buy", optimize=False
    )
    max_stop_pct = DecimalParameter(1.0, 5.0, default=5.0, decimals=1, space="buy", optimize=False)
    # RM-02
    risk_pct = DecimalParameter(0.1, 2.0, default=0.5, decimals=1, space="buy", optimize=False)
    lev = IntParameter(1, 20, default=10, space="buy", optimize=False)
    # G-02 / G-03 / G-05 / G-07
    max_trades_per_day = IntParameter(1, 20, default=5, space="buy", optimize=False)
    daily_loss_stop_pct = DecimalParameter(
        0.5, 10.0, default=2.0, decimals=1, space="buy", optimize=False
    )
    funding_blackout_min = IntParameter(0, 30, default=5, space="buy", optimize=False)
    timeout_minutes = IntParameter(15, 240, default=45, space="sell", optimize=False)
    # RM-04b: số phút lệnh chờ còn hiệu lực. Lớp con ghi đè theo SRS của nó.
    entry_valid_minutes = 3

    # ======================= Tiện ích chỉ báo ============================== #
    @property
    def tf_min(self) -> int:
        return timeframe_to_minutes(self.timeframe)

    def bars(self, minutes: int) -> int:
        """Quy phút -> số nến của khung đang chạy, tối thiểu 1."""
        return max(1, round(minutes / self.tf_min))

    @staticmethod
    def atr(df: DataFrame, period: int = 14) -> pd.Series:
        h, lo, c = df["high"], df["low"], df["close"]
        tr = pd.concat([h - lo, (h - c.shift()).abs(), (lo - c.shift()).abs()], axis=1).max(axis=1)
        return tr.ewm(alpha=1 / period, adjust=False).mean()

    @staticmethod
    def cvd_proxy(df: DataFrame) -> pd.Series:
        """Delta xấp xỉ từ nến: volume * (2*(close-low)/(high-low) - 1).

        ★ VI PHẠM DR-03 CÓ CHỦ Ý. SRS bắt buộc dựng CVD từ aggTrades; repo không có
        tick data và Binance không phát hành lịch sử aggTrades đủ dài qua ccxt. Đây là
        ước lượng vị trí-đóng-cửa-trong-biên-độ, tương quan với delta thật nhưng KHÔNG
        bằng nó. Mọi kết quả của bước xác nhận CVD phải đọc kèm cảnh báo này; các gate
        dùng nó đều có công tắc tắt riêng để đo phần đóng góp thực.
        """
        rng = (df["high"] - df["low"]).replace(0, np.nan)
        pos = ((df["close"] - df["low"]) / rng).clip(0, 1).fillna(0.5)
        return df["volume"] * (2 * pos - 1)

    # ======================= G-05: blackout funding ======================== #
    def _funding_blackout(self, dates: pd.Series) -> pd.Series:
        """True ở những nến nằm trong ±N phút quanh mốc settle funding."""
        win = self.funding_blackout_min.value
        if win <= 0:
            return pd.Series(False, index=dates.index)
        mins_of_day = dates.dt.hour * 60 + dates.dt.minute
        out = pd.Series(False, index=dates.index)
        for h in FUNDING_HOURS:
            anchor = h * 60
            d = (mins_of_day - anchor).abs()
            d = pd.concat([d, (1440 - d).abs()], axis=1).min(axis=1)  # vòng qua nửa đêm
            out |= d <= win
        return out

    # ======================= RM-04a + hợp lệ hoá setup ===================== #
    def apply_risk_gates(self, df: DataFrame) -> DataFrame:
        """Lọc mọi tín hiệu bằng RM-04a và G-05, và tính sẵn cột `stop_pct`.

        Lớp con gọi hàm này ở cuối populate_entry_trend, sau khi đã điền
        entry_price / sl_price.
        """
        ep, sl = df["entry_price"], df["sl_price"]
        stop_pct = (ep - sl).abs() / ep * 100
        df["stop_pct"] = stop_pct

        ok = (
            ep.notna()
            & sl.notna()
            & (ep > 0)
            & (stop_pct >= self.min_stop_pct.value)  # RM-04a
            & (stop_pct <= self.max_stop_pct.value)
            & ~self._funding_blackout(df["date"])  # G-05
        )
        # Chiều phải hợp lý: long thì SL dưới entry, short thì SL trên entry.
        long_ok = ok & (sl < ep)
        short_ok = ok & (sl > ep)
        df.loc[~long_ok, "enter_long"] = 0
        df.loc[~short_ok, "enter_short"] = 0
        dead = (df["enter_long"].fillna(0) == 0) & (df["enter_short"].fillna(0) == 0)
        df.loc[dead, "entry_price"] = np.nan
        df.loc[dead, "sl_price"] = np.nan
        df.loc[dead, "enter_tag"] = None
        return df

    def populate_exit_trend(self, df: DataFrame, metadata: dict) -> DataFrame:
        # RM-01: thoát chỉ bằng TP/SL/timeout — không có tín hiệu exit theo chỉ báo.
        df["exit_long"] = 0
        df["exit_short"] = 0
        return df

    # ======================= Lệnh chờ (RM-04b) ============================= #
    def _last_row(self, pair: str):
        d, _ = self.dp.get_analyzed_dataframe(pair, self.timeframe)
        if d is None or len(d) == 0:
            return None
        return d.iloc[-1]

    def custom_entry_price(
        self, pair, trade, current_time, proposed_rate, entry_tag, side, **kwargs
    ) -> float:
        """Limit tại giá SRS chỉ định, và không bao giờ ở phía xấu hơn giá thị trường.

        Long: min(entry_price, market) — mua-limit nằm chờ dưới giá.
        Short: max(entry_price, market) — bán-limit nằm chờ trên giá.
        """
        row = self._last_row(pair)
        if row is None:
            return proposed_rate
        ep = row.get("entry_price", np.nan)
        if ep is None or not np.isfinite(ep) or ep <= 0:
            return proposed_rate
        return float(max(ep, proposed_rate) if side == "short" else min(ep, proposed_rate))

    def check_entry_timeout(self, pair, trade, order, current_time, **kwargs) -> bool:
        """RM-04b: hết hạn hiệu lực thì huỷ, tuyệt đối không đuổi bằng market.

        Mốc tính tuổi là `order.order_date_utc` (lúc ĐẶT lệnh chờ), không phải
        trade.open_date_utc. ★ `unfilledtimeout` trong config được kiểm TRƯỚC callback
        này (interface.py:ft_check_timed_out) nên nó phải được đặt rộng hơn, nếu không
        luật N-nến của từng combo không bao giờ có tiếng nói.
        """
        placed = getattr(order, "order_date_utc", None) or trade.open_date_utc or current_time
        return (current_time - placed) >= timedelta(minutes=self.entry_valid_minutes)

    # ======================= G-02 / G-03 =================================== #
    def confirm_trade_entry(
        self, pair, order_type, amount, rate, time_in_force, current_time, entry_tag, side, **kwargs
    ) -> bool:
        day_start = current_time.astimezone(UTC).replace(hour=0, minute=0, second=0, microsecond=0)
        closed = Trade.get_trades_proxy(is_open=False)
        today = [t for t in closed if t.open_date_utc and t.open_date_utc >= day_start]

        if len(today) >= self.max_trades_per_day.value:  # G-02
            return False

        # G-03 — lỗ lũy kế trong ngày tính theo equity đầu ngày (xấp xỉ bằng vốn hiện có).
        equity = self._equity()
        if equity > 0:
            pnl = sum(t.close_profit_abs or 0.0 for t in today)
            if pnl <= -abs(self.daily_loss_stop_pct.value) / 100 * equity:
                return False
        return True

    def _equity(self) -> float:
        try:
            return float(self.wallets.get_total_stake_amount())
        except Exception:
            return 0.0

    # ======================= RM-02: sizing theo rủi ro ===================== #
    def leverage(
        self,
        pair,
        current_time,
        current_rate,
        proposed_leverage,
        max_leverage,
        entry_tag,
        side,
        **kwargs,
    ) -> float:
        return float(min(self.lev.value, max_leverage))

    def custom_stake_amount(
        self,
        pair,
        current_time,
        current_rate,
        proposed_stake,
        min_stake,
        max_stake,
        leverage,
        entry_tag,
        side,
        **kwargs,
    ) -> float:
        """notional = equity * risk_pct / s, rồi quy về ký quỹ qua đòn bẩy (RM-02).

        Đòn bẩy là HỆ QUẢ chứ không phải tham số: `lev` chỉ quyết định bao nhiêu vốn bị
        khoá làm ký quỹ, không đổi số tiền mất khi dính SL (luôn = risk_pct * equity).
        """
        row = self._last_row(pair)
        stop_pct = row.get("stop_pct", np.nan) if row is not None else np.nan
        if not np.isfinite(stop_pct) or stop_pct <= 0:
            return proposed_stake
        equity = self._equity() or proposed_stake * leverage
        notional = equity * (self.risk_pct.value / 100) / (stop_pct / 100)
        stake = notional / max(leverage, 1)
        if max_stake:
            stake = min(stake, max_stake)
        if min_stake and stake < min_stake:
            return proposed_stake
        return float(stake)

    # ======================= Đóng băng SL/TP lúc khớp ====================== #
    def _levels(self, pair: str, trade) -> tuple[float, float]:
        """(sl_price, tp_price) tuyệt đối, tính MỘT LẦN lúc khớp rồi giữ nguyên.

        Tính lại mỗi nến sẽ làm freqtrade coi là trailing (adjust_stop_loss chỉ đi một
        chiều) và bóp méo thống kê R:R — bẫy đã gặp ở SmcElliottStrategy.
        """
        sl = trade.get_custom_data("sl_abs")
        tp = trade.get_custom_data("tp_abs")
        if sl and tp:
            return float(sl), float(tp)

        entry = float(trade.open_rate)
        row = self._last_row(pair)
        raw = row.get("sl_price", np.nan) if row is not None else np.nan
        floor = self.min_stop_pct.value / 100
        if trade.is_short:
            if not np.isfinite(raw) or raw <= entry:
                raw = entry * (1 + floor)
            sl = max(float(raw), entry * (1 + floor))
            tp = entry - self.rr.value * (sl - entry)
        else:
            if not np.isfinite(raw) or raw >= entry:
                raw = entry * (1 - floor)
            sl = min(float(raw), entry * (1 - floor))
            tp = entry + self.rr.value * (entry - sl)

        trade.set_custom_data("sl_abs", float(sl))
        trade.set_custom_data("tp_abs", float(tp))
        return float(sl), float(tp)

    def order_filled(self, pair, trade, order, current_time, **kwargs) -> None:
        if order.ft_order_side == trade.entry_side and trade.nr_of_successful_entries == 1:
            self._levels(pair, trade)

    def custom_stoploss(
        self, pair, trade, current_time, current_rate, current_profit, after_fill=False, **kwargs
    ):
        """SL tuyệt đối, bất biến. `after_fill` trong chữ ký là BẮT BUỘC —
        strategy_resolver dò đúng tên tham số này để đặt stop ngay lúc khớp thay vì
        để nó "bò" từ trần -99% lên, cách sau sẽ gắn nhãn mọi lần cắt là trailing."""
        sl, _ = self._levels(pair, trade)
        return stoploss_from_absolute(
            sl, current_rate, is_short=trade.is_short, leverage=trade.leverage
        )

    def custom_exit(self, pair, trade, current_time, current_rate, current_profit, **kwargs):
        """TP cố định theo GIÁ (RM-01) + timeout G-07.

        So sánh bằng giá chứ không bằng current_profit: profit_ratio đã nhân đòn bẩy,
        đối chiếu nó với bội số R sẽ chốt sớm gấp `leverage` lần.
        """
        _, tp = self._levels(pair, trade)
        if trade.is_short:
            if current_rate <= tp:
                return "tp_1.15R"
        elif current_rate >= tp:
            return "tp_1.15R"

        held = current_time - trade.open_date_utc
        if held >= timedelta(minutes=self.timeout_minutes.value):  # G-07
            return "timeout_45m"
        return None

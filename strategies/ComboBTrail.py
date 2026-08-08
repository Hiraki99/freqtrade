"""BT-05 — biến thể trailing của Combo B, để đo chi phí cơ hội của TP cố định 1.15R.

FR-B-N nói thẳng mâu thuẫn: breakout sống nhờ ĐUÔI lợi nhuận, mà TP 1.15R thì cắt đúng
cái đuôi đó. RISK-02 nói tiếp rằng Combo B có thể trượt BT-06 *vì ràng buộc RR* chứ
không phải vì logic sai. File này tách hai khả năng đó ra.

Khác Combo B đúng hai chỗ, mọi thứ còn lại giữ nguyên để so sánh sạch:

 1. KHÔNG có TP cố định. Sau khi lãi đạt 1R, stop bám theo đỉnh/đáy với khoảng cách
    `trail_r` x R. Trước mốc 1R vẫn là stop cấu trúc B-L7 đứng yên.
 2. G-07 nới từ 45 phút lên 8 giờ. ★ Đây là CONFOUND phải khai báo: với timeout 45 phút,
    không cú breakout nào kịp phát triển đuôi, nên so trailing-45' với TP-45' sẽ không
    đo được cái cần đo. Đổi lại, chênh lệch quan sát được là hợp của HAI thay đổi (bỏ TP
    + nới timeout), không quy hết cho một mình chuyện bỏ TP được.
"""

from ComboBSqueeze import ComboBSqueeze

from freqtrade.strategy import DecimalParameter, IntParameter, stoploss_from_absolute


class ComboBTrail(ComboBSqueeze):
    trail_r = DecimalParameter(0.5, 3.0, default=1.0, decimals=1, space="sell", optimize=False)
    timeout_minutes = IntParameter(15, 960, default=480, space="sell", optimize=False)

    def custom_stoploss(
        self, pair, trade, current_time, current_rate, current_profit, after_fill=False, **kwargs
    ):
        sl, _ = self._levels(pair, trade)
        r = abs(trade.open_rate - sl)
        t = self.trail_r.value * r
        if trade.is_short:
            peak = trade.min_rate or current_rate
            if (trade.open_rate - peak) >= r:  # đã đạt 1R -> bắt đầu bám
                sl = min(sl, peak + t)
        else:
            peak = trade.max_rate or current_rate
            if (peak - trade.open_rate) >= r:
                sl = max(sl, peak - t)
        return stoploss_from_absolute(
            sl, current_rate, is_short=trade.is_short, leverage=trade.leverage
        )

    def custom_exit(self, pair, trade, current_time, current_rate, current_profit, **kwargs):
        """Bỏ hẳn nhánh TP của lớp nền; chỉ còn timeout G-07 (đã nới)."""
        from datetime import timedelta

        if (current_time - trade.open_date_utc) >= timedelta(minutes=self.timeout_minutes.value):
            return "timeout"
        return None

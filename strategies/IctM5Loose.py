"""
IctM5Loose — biến thể "nới vừa" của IctM5Strategy để A/B so sánh tần suất/hiệu quả.

Khác bản gốc ĐÚNG 2 cổng (giữ nguyên toàn bộ logic ICT causal):
  • require_confirm_candle: True → False  (bỏ đòi nến M5 cùng màu tại bar retest —
        nến retest thường ngược màu/rejection wick, gate gốc loại nhầm entry tốt).
  • min_rr: 2.0 → 1.5  (TP liquidity gần hơn, RR thấp hơn nhưng nhiều setup đạt).

Giữ require_htf_bias=True (vẫn thuận bias H4). Không lookahead (kế thừa nguyên bản).
"""

from __future__ import annotations

from freqtrade.strategy import BooleanParameter, DecimalParameter

from IctM5Strategy import IctM5Strategy


class IctM5Loose(IctM5Strategy):
    require_confirm_candle = BooleanParameter(default=False, space="buy", optimize=True)
    min_rr = DecimalParameter(1.0, 4.0, default=1.5, decimals=1, space="buy", optimize=True)

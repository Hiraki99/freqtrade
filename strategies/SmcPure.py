"""SmcPure — SmcElliottStrategy rút về SMC thuần.

Mọi đầu vào quyết định đều là khái niệm SMC: cấu trúc thị trường (BOS/CHoCH), Order Block,
Fair Value Gap, POI = OB∩FVG, dealing range premium/discount, quét thanh khoản, lệnh chờ tại
POI, SL dưới biên OB. Bỏ hết chỉ báo cổ điển và hệ chấm điểm.

VÌ SAO LÀ SUBCLASS, KHÔNG PHẢI FILE 800 DÒNG CHÉP LẠI: các nguyên hàm SMC (`_structure`,
`_fvg`, `_entry_plan`, `custom_entry_price`, `custom_stoploss`, `_frozen_sl`) là thứ ta MUỐN
giữ y hệt. Chép ra bản thứ hai nghĩa là từ nay mọi sửa lỗi phải làm hai nơi, và hai bản sẽ
lệch nhau lúc nào không biết. Ở đây chỉ ghi đè đúng những chỗ khác biệt.

ĐO ĐƯỢC TRƯỚC KHI VIẾT (2026-08-08): trong bản gốc, RSI / ADX / rvol / VP-POC / VP-VAL / EMA
khung vào lệnh / OTE **không hề tham gia quyết định** — chúng chỉ nuôi `_confluence`, mà cổng
điểm đã tắt (`min_score = -100`, tương quan score<->profit = -0.042). Nên chỉ có ĐÚNG HAI đầu
vào phi-SMC thật sự cần thay:

  1. G1 `bull_1d` = (close > EMA50) & (EMA50 > EMA200) trên khung ngày.
     -> thay bằng CẤU TRÚC khung ngày: `_structure()` trên nến 1d, bias tăng khi trend > 0.
     Đây mới là cách SMC xác định bias HTF — theo higher-high/higher-low, không theo trung
     bình động. Cùng vai trò (lọc theo xu hướng lớn), khác hẳn cách đo.

  2. `custom_exit` thoát tại VP-VAH (`use_vp_exit`).
     -> bỏ. Chỉ còn `premium_tp` (dealing range) và `tp2` (bội số R) — cả hai đều là SMC.

Cổng điểm `min_score` giữ ở -100 (tắt) và KHÔNG tối ưu: hệ chấm điểm đã bị đo là vô dụng, và
ở đây nó cũng không còn dữ liệu để chấm.

Đối chứng: SmcElliottStrategy trên 197 cặp / 4h / 2023-03-08..2026-07-02.

★ KẾT QUẢ (2026-08-09) — BẢN NÀY THUA, GIỮ LẠI LÀM KẾT QUẢ ÂM:
  Đo với --timeframe-detail 1h (mô hình khớp lệnh thực tế; xem _warning_fill_model trong
  smc-universe.json). Không có nó, mọi con số dưới đây cao hơn 2-4 lần và kết luận NGƯỢC LẠI.

                      SmcElliott          SmcPure
    FULL     +14.46% PF 2.25 DD 1.09%   +11.78% PF 1.20 DD 7.88%
    FIT      +12.03% PF 2.43            +17.20% PF 1.63
    HOLDOUT   +2.43% PF 1.78 DD 1.22%    -5.30% PF 0.84 DD 9.03%   <- LỖ

  Bỏ chỉ báo cổ điển làm số lệnh tăng 4 lần (137 -> 537) và trên nến 4h trông như lãi gấp đôi
  (+44.52% vs +23.44%). Nhưng 4 lần số lệnh cũng là 4 lần phơi nhiễm với giả định khớp lệnh:
  khi tăng phân giải, lợi nhuận SmcPure bốc hơi 74% và drawdown nở gấp 3, còn SmcElliott chỉ
  giảm đều và drawdown gần như không đổi (1.02% -> 1.09%).

  => Bias HTF theo EMA50/200 (bản gốc) LỌC TỐT HƠN bias theo cấu trúc ngày (bản này). EMA chậm
     hơn nhiều tháng, và chính độ chậm đó loại được đám setup biên mà bản này nhận vào.
  => KHÔNG dùng chạy thật. Giữ file để lần sau không ai đi lại đúng con đường này.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
from pandas import DataFrame
from SmcElliottStrategy import SmcElliottStrategy

from freqtrade.strategy import BooleanParameter, IntParameter, informative


class SmcPure(SmcElliottStrategy):
    INTERFACE_VERSION = 3

    # Số nến dùng dựng cấu trúc khung NGÀY cho bias HTF. 20 nến ngày ~ 1 tháng: đủ để một
    # swing daily hình thành, chưa dài tới mức bias đứng yên hàng quý.
    htf_swing_length = IntParameter(10, 40, default=20, space="buy", optimize=True)
    # Bật lại được để đo đóng góp riêng của bias HTF — chính là cổng G1 phiên bản SMC.
    require_htf = BooleanParameter(default=True, space="buy", optimize=True)
    require_discount = BooleanParameter(default=True, space="buy", optimize=True)
    # Không có gì để chấm điểm nữa -> khoá tắt, không đưa vào hyperopt.
    min_score = IntParameter(-100, 100, default=-100, space="buy", optimize=False)
    # VP đã bị bỏ khỏi indicator, nên nhánh thoát theo VAH không còn nguồn dữ liệu.
    use_vp_exit = BooleanParameter(default=False, space="sell", optimize=False)

    @informative("1d")
    def populate_indicators_1d(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        """Bias HTF theo CẤU TRÚC ngày, thay cho giao cắt EMA của bản gốc.

        `_structure` cần parsed_high/parsed_low, nên phải dựng chúng trước — dùng đúng công
        thức của bản gốc (nến biên độ lớn thì đảo high/low để pivot không bám vào râu nến).
        """
        atr200 = self._atr(dataframe, 200)
        high_vol = (dataframe["high"] - dataframe["low"]) >= (self.ob_atr_mult.value * atr200)
        dataframe["parsed_high"] = np.where(high_vol, dataframe["low"], dataframe["high"])
        dataframe["parsed_low"] = np.where(high_vol, dataframe["high"], dataframe["low"])

        st = self._structure(dataframe, int(self.htf_swing_length.value))
        # Tên cột giữ là "bull" để `@informative` sinh ra "bull_1d" — đúng tên mà `_htf_bull`
        # của lớp cha đọc. Đổi tên ở đây sẽ làm G1 im lặng luôn cho qua (fillna(1)).
        dataframe["bull"] = (pd.Series(st["trend"], index=dataframe.index) > 0).astype(float)
        return dataframe

    def populate_indicators(self, df: DataFrame, metadata: dict) -> DataFrame:
        """Chỉ Lớp A (cấu trúc SMC) + kế hoạch lệnh. Không Lớp B, không Lớp C.

        `atr` vẫn tính vì nó là hạ tầng của chính luật SMC ở đây: ngưỡng nến biên độ lớn khi
        dò OB (`ob_atr_mult`), và dải entry dự phòng ±0.25 ATR khi không có OB/FVG nào. Nó đo
        biến động, không đưa ra tín hiệu hướng — nên không phải "chỉ báo" theo nghĩa đang bỏ.
        """
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

        # Dealing range: premium/discount là cách SMC định giá đắt/rẻ trong một nhịp.
        sh = pd.Series(swing["last_sh"], index=df.index)
        sl = pd.Series(swing["last_sl"], index=df.index)
        df["swing_high"] = sh
        df["swing_low"] = sl
        df["equilibrium"] = (sh + sl) / 2
        df["premium_level"] = sl + self.premium_ratio.value * (sh - sl)

        df["atr"] = self._atr(df, 14)
        df = self._entry_plan(df)
        # `_entry_plan` không tạo cột score, mà populate_entry_trend của lớp cha có đọc nó.
        # Gán 0 với min_score=-100 => cổng luôn mở, và không có số giả nào bị đem đi công bố.
        df["score"] = 0
        return df

    def custom_exit(self, pair, trade, current_time, current_rate, current_profit, **kwargs):
        """TP2 theo bội số R + thoát khi giá lên vùng premium. Không còn nhánh VP-VAH."""
        r = self._r_pct(pair, trade)
        if current_profit >= self.tp2_rr.value * r:
            return "tp2"
        if current_profit <= 0:
            return None
        row = self._last_row(pair)
        if row is None:
            return None
        pr = row.get("premium_level", np.nan)
        if not np.isnan(pr) and current_rate >= pr:
            return "premium_tp"
        return None

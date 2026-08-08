"""Benchmark random-entry cho BT-04(b). KHÔNG phải chiến lược để chạy thật.

BT-04 yêu cầu so mỗi combo với "random entry cùng tần suất, cùng SL/TP". Nếu combo
không đánh bại mốc này thì edge (nếu có) không nằm ở logic vào lệnh — mà logic vào lệnh
là toàn bộ nội dung của FR-A/B/C; phần còn lại (RM-01..RM-04, G-01..G-07) thì combo nào
cũng dùng chung, kể cả bản random này.

Cách dựng: mỗi nến rút một số giả ngẫu nhiên TẤT ĐỊNH từ timestamp (SplitMix64 trên
epoch giây + seed). Tất định là có chủ đích — chạy lại phải ra đúng con số cũ, nếu không
thì không so sánh được. Đổi `seed` để lấy mẫu khác và ước lượng độ phân tán của chính
mốc so sánh này (một mẫu random đơn lẻ nói lên rất ít).

Vào lệnh với xác suất `p_entry` mỗi nến, long/short 50-50, SL cách entry
`sl_atr_mult * ATR(14)`, TP = 1.15R — dùng nguyên ScalpComboBase, y hệt ba combo.
Ba lớp con ở cuối file đã chỉnh sẵn `p_entry` / `sl_atr_mult` để khớp tần suất và stop
trung vị của A, B, C (ATR14/close trên 5m BTC có trung vị 0.159%).
"""

import numpy as np
from pandas import DataFrame
from scalp_combo_base import ScalpComboBase


def _splitmix64(x: np.ndarray) -> np.ndarray:
    """Băm số nguyên 64-bit -> uniform [0,1). Rẻ, tất định, không cần state."""
    m64 = np.uint64(0xFFFFFFFFFFFFFFFF)
    z = (x.astype(np.uint64) + np.uint64(0x9E3779B97F4A7C15)) & m64
    z = ((z ^ (z >> np.uint64(30))) * np.uint64(0xBF58476D1CE4E5B9)) & m64
    z = ((z ^ (z >> np.uint64(27))) * np.uint64(0x94D049BB133111EB)) & m64
    z = z ^ (z >> np.uint64(31))
    return (z >> np.uint64(11)).astype(np.float64) / float(1 << 53)


class ComboRandomBench(ScalpComboBase):
    timeframe = "5m"
    startup_candle_count = 100
    entry_valid_minutes = 15

    # Hằng số thường (không phải Parameter) để lớp con ghi đè được bằng một dòng.
    p_entry = 0.0005
    sl_atr_mult = 2.5
    seed = 1

    def populate_indicators(self, df: DataFrame, metadata: dict) -> DataFrame:
        df["atr14"] = self.atr(df, 14)
        # KHÔNG chia cho 10**9: feather lưu date ở đơn vị mili giây, nên astype("int64")
        # đã ra ~1.7e12; chia thêm 1e9 làm 273k nến co lại còn ~82 giá trị phân biệt và
        # dãy "ngẫu nhiên" chỉ còn 82 mức (min(u) = 0.0031 thay vì ~1e-6) -> mọi ngưỡng
        # p_entry nhỏ đều không bao giờ chạm. Hàm băm không quan tâm đơn vị, chỉ cần các
        # giá trị phân biệt nhau.
        ticks = df["date"].astype("int64").to_numpy()
        df["u"] = _splitmix64(ticks + np.int64(self.seed) * np.int64(0x100000001B3))
        return df

    def populate_entry_trend(self, df: DataFrame, metadata: dict) -> DataFrame:
        df["enter_long"] = 0
        df["enter_short"] = 0
        df["entry_price"] = np.nan
        df["sl_price"] = np.nan
        df["enter_tag"] = None

        p = self.p_entry
        long_sig = df["u"] < p / 2
        short_sig = (df["u"] >= p / 2) & (df["u"] < p)
        dist = self.sl_atr_mult * df["atr14"]

        df.loc[long_sig, "enter_long"] = 1
        df.loc[long_sig, "entry_price"] = df["close"][long_sig]
        df.loc[long_sig, "sl_price"] = (df["close"] - dist)[long_sig]
        df.loc[long_sig, "enter_tag"] = "RND-L"

        df.loc[short_sig, "enter_short"] = 1
        df.loc[short_sig, "entry_price"] = df["close"][short_sig]
        df.loc[short_sig, "sl_price"] = (df["close"] + dist)[short_sig]
        df.loc[short_sig, "enter_tag"] = "RND-S"

        return self.apply_risk_gates(df)


class RandBenchA(ComboRandomBench):
    """Khớp Combo A: ~45 lệnh / 31 tháng, stop trung vị ~0.42%."""

    p_entry = 0.000616
    sl_atr_mult = 1.50
    seed = 11


class RandBenchB(ComboRandomBench):
    """Khớp Combo B: ~64 lệnh. Stop trung vị chỉ hạ được tới ~0.44% (Combo B: 0.363%):
    dưới mức đó sàn RM-04a cắt gần hết mẫu. Bench vì thế có f nhỏ hơn một chút, tức
    DỄ hơn Combo B — nếu Combo B vẫn thua bench thì kết luận càng chắc."""

    p_entry = 0.001753
    sl_atr_mult = 1.20
    seed = 22


class RandBenchC(ComboRandomBench):
    """Khớp Combo C: ~311 lệnh, stop trung vị ~0.66%."""

    p_entry = 0.001457
    sl_atr_mult = 3.70
    seed = 33

"""Nhiều seed cho benchmark BT-04(b) — dùng để ước lượng ĐỘ PHÂN TÁN của mốc random.

Một mẫu random đơn lẻ gần như không nói lên điều gì: với ~45 lệnh và độ lệch chuẩn
R xấp xỉ 1.05, sai số chuẩn của kỳ vọng đã là ~0.16R — lớn hơn cả khoảng cách giữa các
combo và mốc so sánh. Năm seed cho mỗi nhóm để đọc được combo nằm ở đâu trong phân phối
đó, thay vì so với đúng một lần tung xúc xắc.

Tham số (p_entry, sl_atr_mult) giữ y hệt RandBenchA/B/C, chỉ đổi seed.
Chạy gọn trong một lượt:
    freqtrade backtesting --config config-scalp-bt.json --timeframe-detail 1m \
        --timerange 20240101-20260807 --strategy-list RandA1 RandA2 ... RandB5
"""

from ComboRandomBench import RandBenchA, RandBenchB, RandBenchC


class RandA1(RandBenchA):
    seed = 172


class RandA2(RandBenchA):
    seed = 179


class RandA3(RandBenchA):
    seed = 186


class RandA4(RandBenchA):
    seed = 193


class RandA5(RandBenchA):
    seed = 200


class RandB1(RandBenchB):
    seed = 173


class RandB2(RandBenchB):
    seed = 180


class RandB3(RandBenchB):
    seed = 187


class RandB4(RandBenchB):
    seed = 194


class RandB5(RandBenchB):
    seed = 201


class RandC1(RandBenchC):
    seed = 174


class RandC2(RandBenchC):
    seed = 181


class RandC3(RandBenchC):
    seed = 188


class RandC4(RandBenchC):
    seed = 195


class RandC5(RandBenchC):
    seed = 202

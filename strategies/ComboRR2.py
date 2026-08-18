"""Biến thể RR 1:2 của ba combo — kiểm định khuyến nghị §4.2 của báo cáo backtest.

Ở RR 1:1.15, chi phí đo được `f` ≈ 0.21-0.23 R đẩy winrate hoà vốn lên ~57% và cả ba
combo đều trượt (xem docs/BT-BTC-Scalping-Combos.md). Nâng RR lên 1:2 hạ ngưỡng đó
xuống `(1 + f) / 3` ≈ **40%** — dưới winrate quan sát được của cả ba. Câu hỏi là winrate
tụt bao nhiêu khi TP đi xa gần gấp đôi.

★ RR không phải biến duy nhất thay đổi. Hai hệ quả kéo theo, phải tách ra mới đọc được:

 1. **G-07.** Timeout 45 phút được đặt cho mục tiêu 1.15R. Dưới giả định bước ngẫu nhiên,
    thời gian kỳ vọng để giá đi hết quãng đường d tỉ lệ với d², nên mục tiêu 2R cần
    (2/1.15)² ≈ 3.0 lần thời gian. Giữ nguyên 45 phút thì phần lớn lệnh sẽ thoát bằng
    timeout trước khi kịp chạm TP — đo được cái khác chứ không phải cái cần đo. Vì vậy
    mỗi combo có HAI lớp: `*RR2` (giữ 45 phút, cô lập đúng biến RR) và `*RR2T`
    (nới lên 135 phút, để mục tiêu 2R thực sự với tới được).

 2. **A-L5 của Combo A.** Bước này yêu cầu mục tiêu cấu trúc (POC) phải xa hơn TP cố
    định, và nó đọc thẳng `self.rr`. Nâng RR tự động siết A-L5 chặt hơn — đúng tinh thần
    đặc tả, nhưng nghĩa là số lệnh của Combo A sẽ giảm vì lý do KHÁC với hai combo kia.

Mốc random-entry chạy kèm (`RandARR2*` …) dùng đúng RR và timeout tương ứng: ở vòng
1:1.15 cả ba combo đều KHÔNG hơn được random, nên nếu vòng này có cải thiện mà mốc random
cũng cải thiện y hệt thì đó là hệ quả của RR chứ không phải edge.
"""

from ComboAAuction import ComboAAuction
from ComboBSqueeze import ComboBSqueeze
from ComboCCascade import ComboCCascade
from ComboRandomBench import RandBenchA, RandBenchB, RandBenchC

from freqtrade.strategy import DecimalParameter, IntParameter


# RR mới và timeout đã quy đổi. Mỗi lớp nhận một Parameter RIÊNG (hàm, không phải hằng
# dùng chung): HyperStrategyMixin gán `.value` lên chính object Parameter, nên chia sẻ một
# instance giữa nhiều strategy là mời gọi rò rỉ trạng thái giữa các lần chạy.
def _rr2():
    return DecimalParameter(1.0, 3.0, default=2.0, decimals=2, space="sell", optimize=False)


def _timeout_scaled():
    return IntParameter(15, 960, default=135, space="sell", optimize=False)


class ComboARR2(ComboAAuction):
    rr = _rr2()


class ComboBRR2(ComboBSqueeze):
    rr = _rr2()


class ComboCRR2(ComboCCascade):
    rr = _rr2()


class ComboARR2T(ComboAAuction):
    rr = _rr2()
    timeout_minutes = _timeout_scaled()


class ComboBRR2T(ComboBSqueeze):
    rr = _rr2()
    timeout_minutes = _timeout_scaled()


class ComboCRR2T(ComboCCascade):
    rr = _rr2()
    timeout_minutes = _timeout_scaled()


class RandARR2T(RandBenchA):
    rr = _rr2()
    timeout_minutes = _timeout_scaled()


class RandBRR2T(RandBenchB):
    rr = _rr2()
    timeout_minutes = _timeout_scaled()


class RandCRR2T(RandBenchC):
    rr = _rr2()
    timeout_minutes = _timeout_scaled()


# --- 5 seed cho mốc random ở RR 1:2 (xem lý do trong ComboRandomSeeds.py) ---------- #


class RandARR2_1(RandARR2T):
    seed = 372


class RandARR2_2(RandARR2T):
    seed = 379


class RandARR2_3(RandARR2T):
    seed = 386


class RandARR2_4(RandARR2T):
    seed = 393


class RandARR2_5(RandARR2T):
    seed = 400


class RandBRR2_1(RandBRR2T):
    seed = 373


class RandBRR2_2(RandBRR2T):
    seed = 380


class RandBRR2_3(RandBRR2T):
    seed = 387


class RandBRR2_4(RandBRR2T):
    seed = 394


class RandBRR2_5(RandBRR2T):
    seed = 401


class RandCRR2_1(RandCRR2T):
    seed = 374


class RandCRR2_2(RandCRR2T):
    seed = 381


class RandCRR2_3(RandCRR2T):
    seed = 388


class RandCRR2_4(RandCRR2T):
    seed = 395


class RandCRR2_5(RandCRR2T):
    seed = 402

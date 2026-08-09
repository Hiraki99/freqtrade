"""TrendFollowV2 — bản sửa của TrendFollowingStrategy (freqtrade-strategies/futures).

Bản gốc chạy BTC/USDT:USDT 5m, 2024-01..2026-08: +1.75%, 49 lệnh, win 85.7%, PF 1.12,
trong khi BTC tăng +51.26%. Thắng 85.7% mà gần như không lãi — dấu hiệu của lỗi cấu trúc,
không phải thiếu tinh chỉnh. Đo ra ba lỗi, cả ba đều sửa được bằng code:

1. `can_short` KHÔNG HỀ ĐƯỢC KHAI -> mặc định False -> toàn bộ nhánh `enter_short` bị bỏ qua.
   Bản gốc viết đủ logic short rồi không bật, nên một nửa chiến lược là code chết. Đó cũng là
   lý do chỉ có 49 lệnh trong 2.6 năm.

2. `stoploss = -0.265` trên khung 5m, kèm `trailing_only_offset_is_reached = False` nên trailing
   hoạt động ngay từ mức -26.5%. Đo được: 6 lệnh `trailing_stop_loss` lỗ TB **-25.72%**, ăn sạch
   lãi của 42 lệnh ROI (+5.0%/lệnh). Một lệnh thua bằng năm lệnh thắng.

3. Điều kiện thoát tự mâu thuẫn: `exit_long` đòi OBV TĂNG (`obv > obv.shift(1)`), trong khi
   `enter_short` với cùng điều kiện giá lại đòi OBV GIẢM. Cùng một sự kiện "giá cắt xuống EMA"
   mà hai nhánh đòi hai hướng OBV ngược nhau -> thoát long gần như không bao giờ kích hoạt.
   Ở đây `exit_long` đổi thành OBV giảm (và `exit_short` thành OBV tăng) cho nhất quán.

Giữ nguyên ý tưởng gốc: giá cắt EMA nhanh, xác nhận bằng hướng OBV. Không thêm chỉ báo mới —
mục tiêu là đo xem luật gốc đáng giá bao nhiêu khi được chạy đúng, chứ không phải thay nó.

★ KẾT QUẢ (2026-08-09) — LUẬT NÀY KHÔNG CÓ EDGE. GIỮ LÀM KẾT QUẢ ÂM.

Sửa xong ba lỗi thì kết quả TỆ ĐI, không tốt lên — vì chúng đang vô tình khoá bớt một luật
thua. BTC/USDT:USDT 5m, 2024-01..2026-08:

    bản gốc (short tắt, thoát hỏng) :     49 lệnh   +1.75%   PF 1.12
    V2, thoát theo tín hiệu         : 13.867 lệnh  -89.91%   PF 0.46
    V2, không thoát theo tín hiệu   :  2.667 lệnh  -30.07%   PF 0.77

Hai phép thử độc lập cùng kết luận:

  1. GROSS (bỏ hết phí): -0.03%/lệnh, PF 0.94 -> ÂM ngay cả khi miễn phí giao dịch.
     Phí không phải thủ phạm, nó chỉ khuếch đại một luật vốn không có lợi thế.

  2. HYPEROPT 300 epoch trên 4.3 năm (2019-09..2024-01, dữ liệu 5m về tới lúc niêm yết hợp
     đồng): **0/300 epoch có lãi**, tốt nhất -28.27%, trung vị -89.91%.

+1.75% của bản gốc không phải edge — đó là hệ quả của việc chỉ vào 49 lệnh trong 2.6 năm.
Mẫu quá nhỏ để bất cứ điều gì lộ ra, và BTC tăng +51.26% cùng kỳ.

Điểm đáng ghi nhận về PHƯƠNG PHÁP: chiến lược này vào lệnh ngay tại giá tín hiệu nên kết quả
KHÔNG đổi một chữ số nào giữa fill 5m và fill 1m (49 lệnh, +1.75%, PF 1.12 ở cả hai). Trái
ngược với SmcElliott khung 4h (limit chờ xa giá) mất 84% lợi nhuận khi tăng phân giải. Sai
lệch mô hình khớp lệnh tỉ lệ với ĐỘ XA của lệnh chờ so với giá, không phải với chiến lược.

KHÔNG chạy thật. Giữ file để lần sau không ai đi lại đường này.
"""

import talib.abstract as ta
from pandas import DataFrame

from freqtrade.strategy import BooleanParameter, DecimalParameter, IntParameter, IStrategy


class TrendFollowV2(IStrategy):
    INTERFACE_VERSION: int = 3

    # Bật short: logic đã có sẵn trong bản gốc, chỉ thiếu cờ này.
    can_short = True

    timeframe = "5m"
    process_only_new_candles = True
    use_exit_signal = True

    # -26.5% là mức của bản gốc. Giữ làm TRẦN CỨNG rộng và để `custom_stoploss` không bật;
    # mức thật do hyperopt tìm qua `sl_pct` bên dưới, vì trần cứng của config/strategy chính là
    # thứ đã gây ra 6 lệnh -25.72%.
    stoploss = -0.10

    trailing_stop = True
    trailing_stop_positive = 0.05
    trailing_stop_positive_offset = 0.10
    # True: chỉ trail SAU khi đạt offset. Bản gốc để False nên trailing chạy ngay từ -26.5%,
    # biến stoploss thành một cái bẫy rộng thay vì công cụ bảo vệ.
    trailing_only_offset_is_reached = True

    minimal_roi = {"0": 0.15, "30": 0.10, "60": 0.05}

    startup_candle_count: int = 50

    # --- tham số hyperopt ---
    ema_period = IntParameter(8, 60, default=20, space="buy", optimize=True)
    # Số nến OBV dùng xác nhận. Bản gốc so 1 nến -> quá nhiễu trên 5m.
    obv_lookback = IntParameter(1, 12, default=1, space="buy", optimize=True)
    sl_pct = DecimalParameter(0.5, 6.0, default=2.0, decimals=1, space="sell", optimize=True)
    roi_0 = DecimalParameter(0.5, 8.0, default=2.0, decimals=1, space="sell", optimize=True)
    # Biến CẤU TRÚC mạnh nhất, nên phải nằm trong diện tìm chứ không chốt cứng: bật thì mỗi
    # lần giá cắt EMA là đảo vị thế (đo được 13.867 lệnh, -89.91%, phí ăn sạch); tắt thì chỉ
    # thoát theo ROI/SL/trailing (2.667 lệnh, -30.07%). Hai chế độ khác hẳn nhau về bản chất.
    exit_on_signal = BooleanParameter(default=False, space="sell", optimize=True)

    def populate_indicators(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        dataframe["obv"] = ta.OBV(dataframe["close"], dataframe["volume"])
        dataframe["trend"] = (
            dataframe["close"].ewm(span=int(self.ema_period.value), adjust=False).mean()
        )
        n = int(self.obv_lookback.value)
        dataframe["obv_up"] = dataframe["obv"] > dataframe["obv"].shift(n)
        dataframe["obv_dn"] = dataframe["obv"] < dataframe["obv"].shift(n)
        dataframe["x_up"] = (dataframe["close"] > dataframe["trend"]) & (
            dataframe["close"].shift(1) <= dataframe["trend"].shift(1)
        )
        dataframe["x_dn"] = (dataframe["close"] < dataframe["trend"]) & (
            dataframe["close"].shift(1) >= dataframe["trend"].shift(1)
        )
        return dataframe

    def populate_entry_trend(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        dataframe.loc[dataframe["x_up"] & dataframe["obv_up"], "enter_long"] = 1
        dataframe.loc[dataframe["x_dn"] & dataframe["obv_dn"], "enter_short"] = 1
        return dataframe

    def populate_exit_trend(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        if not self.exit_on_signal.value:
            return dataframe
        # Nhất quán với nhánh vào lệnh: thoát long khi giá cắt xuống VÀ OBV xác nhận giảm.
        dataframe.loc[dataframe["x_dn"] & dataframe["obv_dn"], "exit_long"] = 1
        dataframe.loc[dataframe["x_up"] & dataframe["obv_up"], "exit_short"] = 1
        return dataframe

    def custom_stoploss(
        self, pair, trade, current_time, current_rate, current_profit, after_fill=False, **kwargs
    ) -> float:
        """SL cố định theo `sl_pct`, độc lập với trần -10% của thuộc tính `stoploss`.

        Đặt ở đây thay vì ở `stoploss` để hyperopt tìm được mức thật: `stoploss` là thuộc tính
        lớp, còn config có thể đè nó — đúng cái bẫy đã gặp ở SmcElliottStrategy hôm nay, nơi
        `stoploss` trong config hoàn toàn vô hiệu vì `use_custom_stoploss`.
        """
        return -float(self.sl_pct.value) / 100.0

    use_custom_stoploss = True

    def bot_start(self, **kwargs) -> None:
        # ROI bậc 0 do hyperopt tìm; hai bậc sau giữ tỉ lệ tương đối của bản gốc (2/3 và 1/3).
        #
        # Khoá phải là INT. freqtrade chuẩn hoá `minimal_roi` từ str sang int lúc nạp strategy,
        # mà `bot_start` chạy SAU bước đó — để khoá chuỗi ở đây thì `min_roi_reached_entry` so
        # sánh str với int và ném TypeError giữa lúc backtest.
        r = float(self.roi_0.value) / 100.0
        self.minimal_roi = {0: r, 30: r * 0.67, 60: r * 0.33}

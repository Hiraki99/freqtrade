"""SmcElliott trên BTC perp khung 5m — quét R:R 1:1, 1:0.5, 1:0.3 (+ 1:2 làm mốc).

Câu hỏi: hạ mục tiêu chốt lời xuống DƯỚI mức rủi ro có cứu được bản 5m không? Ý tưởng
là đánh đổi biên lợi nhuận lấy tỉ lệ thắng — TP gần thì giá chạm thường xuyên hơn.

★ VÌ SAO PHẢI TẮT BA NHÁNH THOÁT KHÁC, nếu không phép so sánh vô nghĩa:

  1. `use_partial_tp` (mặc định True) chốt 50% vốn tại `tp1_rr` x R = 1.0R. Với biến thể
     R:R 1:0.5 và 1:0.3, TP2 nằm GẦN HƠN TP1, nên TP2 luôn nổ trước và chốt-một-phần
     thành mã chết. Ở biến thể 1:1 và 1:2 nó lại hoạt động. Để bật thì mỗi biến thể chạy
     một luật thoát KHÁC nhau, và cột "R:R" không còn là biến duy nhất.
  2. `use_vp_exit` (mặc định True) thoát tại VAH và
  3. `premium_tp` thoát tại biên premium của dealing range.
     Cả hai là mốc GIÁ TUYỆT ĐỐI, không theo R. Ở R:R 1:2 chúng cắt trước TP rất thường
     xuyên (bản 5m trước: 101 lệnh `vp_vah_tp` + 31 `premium_tp` trên 600); ở R:R 1:0.3
     thì TP nằm quá gần nên chúng gần như không bao giờ tới lượt. Giữ chúng lại nghĩa là
     R:R thấp được đo với luật thoát sạch còn R:R cao bị pha loãng.

  Còn giữ `smc_flip` (thoát khi cấu trúc nội bộ lật xuống): đó là mất luận điểm SMC, chứ
  không phải một mốc chốt lời cạnh tranh với TP. Tỉ trọng của nó có trong bảng exit reason.

★ THAM SỐ MẶC ĐỊNH TRONG CODE, không đọc hyperopt json — theo tiền lệ SmcElliottDefaults.
  Bộ tham số live (`SmcElliottStrategy.json`) được dò trên 4h / 197 cặp, đem sang BTC 5m
  thì cũng tuỳ tiện như dùng default, mà default có 0 bậc tự do nên chênh lệch giữa các
  biến thể quy được về đúng R:R. Hệ quả cần nhớ: R thật = (entry - SL)/entry bị kẹp trong
  [`min_sl_pct` 1.5%, `max_risk_pct` 3.0%] — trên nến 5m đây là stop RỘNG, nên "scalping"
  ở đây là tần suất vào lệnh 5m chứ không phải stop vài chục pip.

★ NGƯỠNG HOÀ VỐN, tính trước khi chạy (f = phí+slippage quy theo R):
      W_be = (1 + f) / (1 + rr)
  Với fee 0.045%/chiều (khứ hồi 0.09%) và R ~ 1.5-3.0% => f ~ 0.03-0.06 R:

      rr 2.0  ->  W_be ~ 34-35%      rr 0.5  ->  W_be ~ 69-71%
      rr 1.0  ->  W_be ~ 52-53%      rr 0.3  ->  W_be ~ 79-82%

  Nghĩa là biến thể 1:0.3 phải thắng ~4 lệnh trên 5 mới hoà. Bản 5m chạy config live
  (ROI 2% / stop 2%, tức R:R ~ 1:1) đạt win 65.2% mà vẫn -7.33% / PF 0.51 — vì thế con số
  cần theo dõi ở đây KHÔNG phải win rate mà là hiệu (win thực - W_be).

★ KẾT QUẢ (2026-08-10) — HẠ R:R KHÔNG CỨU ĐƯỢC, VÀ KHÔNG THỂ. Báo cáo: docs/BT-SMC-Scalp-RR.md

    FULL 2024-01..2026-08, fill 1m, BTC perp 5m, thị trường +51.58%
      R:R   lệnh  win%   PF   tổng%      R:R   lệnh  win%   PF   tổng%
      1:2    403  49.9  0.79  -3.28      1:0.5  435  52.6  0.77  -3.43
      1:1    411  50.9  0.79  -3.15      1:0.3  459  58.2  0.72  -4.00

  Win rate tăng đúng như kỳ vọng nhưng không đủ xa: +8.3 điểm (49.9->58.2) trong khi ngưỡng
  hoà vốn tăng +46.1 điểm (35.3->81.4). Mẫu số (1+rr) co nhanh hơn mọi mức cải thiện win
  rate mà việc dịch TP lại gần có thể mang lại — đây là lý do hình học, không phải may rủi.

  CƠ CHẾ (phân rã theo lý do thoát, FULL): số lệnh dính stop KHÔNG giảm khi hạ TP
  (84 -> 78 lệnh, bể lỗ -1312 -> -1217 USDT). Lệnh thua đi THẲNG từ entry xuống SL và chưa
  từng ở gần TP dù TP nằm ở 0.3R. Tiền mà TP kiếm thêm bị lấy đúng từ túi `smc_flip`
  (+581 -> -112 USDT), nên tổng tiền thắng gần như bất biến 983 -> 817. Hạ R:R là phép xáo
  lại bằng không, và ở 1:0.3 thành âm vì số lệnh tăng làm phí tăng 348 -> 396 USDT.

  GỐC RỄ: kỳ vọng GROSS (cộng lại phí) chỉ +0.001..+0.012% stake/lệnh, t = 0.02..0.25 —
  không phân biệt được với số không, trong khi phí khứ hồi là 0.09%. Toàn bộ khoản lỗ quan
  sát được LÀ phí. Khi gross bằng 0, mọi tham số chỉ điều khiển độ lớn hóa đơn phí chứ
  không điều khiển dấu của kết quả; vì vậy quét thêm R:R hay hyperopt đều vô ích.

KHÔNG phải chiến lược để chạy thật — chỉ là dụng cụ đo.
"""

from SmcElliottStrategy import SmcElliottStrategy

from freqtrade.strategy import BooleanParameter, DecimalParameter


class _SmcScalpRRBase(SmcElliottStrategy):
    """Luật thoát rút về ĐÚNG MỘT mốc theo R, để `tp2_rr` là biến duy nhất."""

    timeframe = "5m"

    # Khai báo lại vì lớp cha giới hạn dải [1.5, 4.0] — dưới 1.5 sẽ bị Parameter chặn.
    tp2_rr = DecimalParameter(0.2, 4.0, default=2.0, decimals=2, space="sell", optimize=False)
    use_partial_tp = BooleanParameter(default=False, space="sell", optimize=False)
    use_breakeven = BooleanParameter(default=False, space="sell", optimize=False)
    use_vp_exit = BooleanParameter(default=False, space="sell", optimize=False)

    # Chốt-một-phần tắt => không còn lệnh vào/thoát bổ sung nào.
    position_adjustment_enable = False

    def custom_exit(self, pair, trade, current_time, current_rate, current_profit, **kwargs):
        """CHỈ TP theo bội số R. Bỏ hẳn `premium_tp` của lớp cha (mốc giá tuyệt đối).

        Không gọi super(): nhánh premium nằm trong thân hàm cha, không sau một cờ bật/tắt.
        """
        if current_profit >= self.tp2_rr.value * self._r_pct(pair, trade):
            return f"tp_{self.tp2_rr.value:g}R"
        return None


class SmcScalpRR20(_SmcScalpRRBase):
    """Mốc so sánh: R:R 1:2 — `tp2_rr` mặc định của lớp cha."""

    tp2_rr = DecimalParameter(0.2, 4.0, default=2.0, decimals=2, space="sell", optimize=False)


class SmcScalpRR10(_SmcScalpRRBase):
    tp2_rr = DecimalParameter(0.2, 4.0, default=1.0, decimals=2, space="sell", optimize=False)


class SmcScalpRR05(_SmcScalpRRBase):
    tp2_rr = DecimalParameter(0.2, 4.0, default=0.5, decimals=2, space="sell", optimize=False)


class SmcScalpRR03(_SmcScalpRRBase):
    tp2_rr = DecimalParameter(0.2, 4.0, default=0.3, decimals=2, space="sell", optimize=False)

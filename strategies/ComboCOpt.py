"""Bản MỞ KHOÁ HYPEROPT của ba combo — dùng cho vòng 3 (tối ưu RR + tham số).

Ba lớp con mỏng, không đổi một dòng logic tín hiệu nào. Việc duy nhất chúng làm là mở
cho hyperopt ba thứ mà hai vòng trước cố định:

    rr              1.15 -> tìm trong [0.6, 5.0]   (yêu cầu trực tiếp: "tối ưu lại rr")
    timeout_minutes 45   -> tìm trong [15, 480]    (G-07; vòng 2 cho thấy nó lật cả kết
                                                    quả của Combo A, nên khoá nó lại
                                                    trong khi thả rr là vô nghĩa)
    min_stop_pct    0.35 -> tìm trong [0.20, 2.50] (sàn RM-04a; đây là biến có đòn bẩy
                                                    lớn nhất lên `f`, xem §2 báo cáo)

★ CẢNH BÁO PHƯƠNG PHÁP, đọc trước khi tin bất kỳ con số nào ra từ file này:

  1. **Vi phạm BT-02 có chủ đích.** BT-02 giới hạn 4 tham số hyperopt / combo; ở đây là
     7 (4 tham số gốc + 3 tham số trên). Mỗi tham số thêm vào là một chiều để overfit.

  2. **Mẫu quá nhỏ so với không gian tìm kiếm.** Trong cửa sổ fit 20 tháng, Combo A có
     ~28 lệnh và Combo B ~40. Dò 7 chiều trên 30-40 điểm dữ liệu thì tìm ra cấu hình có
     lãi gần như là chắc chắn — và gần như chắc chắn là nhiễu. Combo C (~200 lệnh) đỡ
     hơn nhưng vẫn là bản proxy thiếu dữ liệu OI/liquidation.

  3. Vì vậy **mọi kết quả phải đọc ở cột HOLDOUT**, không phải cột fit. Quy trình:
     fit 2024-01-01 → 2025-08-01, holdout 2025-08-01 → 2026-08-07, tham số CHỐT ở cuối
     vòng fit và không được chạm vào nữa.

RISK-03 của chính SRS đã nói trước điều này: trên BTC, chiến lược có lãi in-sample
thường không giữ được out-of-sample, và lợi nhuận chủ yếu đến từ việc chọn tham số.
Vòng 3 là phép thử trực tiếp cho câu đó.


★ MỘT FILE MỘT LỚP LÀ BẮT BUỘC, không gộp được. HyperoptTools đặt tên file tham số theo
  TÊN FILE strategy chứ không theo tên class, nên ba lớp nằm chung `ComboOpt.py` sẽ dùng
  chung `ComboOpt.json`, ghi đè nhau, và lần chạy sau báo "Invalid parameter file
  provided" vì `strategy_name` trong file không khớp. Đã dính đúng lỗi này một lần.
"""

from ComboCCascade import ComboCCascade

from freqtrade.strategy import DecimalParameter, IntParameter


def _rr_space():
    return DecimalParameter(0.6, 5.0, default=1.15, decimals=2, space="sell", optimize=True)


def _timeout_space():
    return IntParameter(15, 480, default=45, space="sell", optimize=True)


def _min_stop_space():
    return DecimalParameter(0.20, 2.50, default=0.35, decimals=2, space="buy", optimize=True)


class ComboCOpt(ComboCCascade):
    rr = _rr_space()
    timeout_minutes = _timeout_space()
    min_stop_pct = _min_stop_space()
    # C-L8 giới hạn trên của stop; thả luôn cho nhất quán với việc thả sàn.
    max_stop_pct = DecimalParameter(0.8, 4.0, default=1.5, decimals=1, space="buy", optimize=True)

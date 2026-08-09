"""SmcElliott với `use_breakeven` TẮT — thí nghiệm chẩn đoán, không phải chiến lược.

Mục đích: đo xem bao nhiêu phần lợi nhuận của bản hyperopt đến từ stop-dời-về-hoà-vốn,
và bao nhiêu phần trong đó chỉ tồn tại nhờ backtest khớp lệnh ở độ phân giải thô.

Vì sao nghi ngờ chỗ này. Breakeven là cấu trúc NHẠY NHẤT với độ mịn khớp lệnh: sau TP1
stop dời về giá vào, nên một lệnh "thua" chỉ còn mất ~0 (một nửa đã chốt ở tp1_rr, nửa
còn lại thoát hoà). Điều đó thổi profit factor lên rất cao — nhưng *có kịp chạm TP1
trước khi chạm stop hay không* thuần tuý là câu hỏi về đường đi BÊN TRONG nến, thứ mà
backtest 4h không biết và phải đoán.

Bằng chứng dẫn tới thí nghiệm này, cùng một lần chạy (4h, 197 cặp, tham số hyperopt):

    fill 4h : 137 lệnh, +23.44%, PF 3.83 — 59 lệnh `stop_loss`, 42.4% trong đó CÓ LÃI
    fill 1h : 146 lệnh, +14.46%, PF 2.25 — 73 lệnh `stop_loss`, 27.4% trong đó có lãi

`stop_loss` mà có lãi chính là lệnh đã chốt TP1 rồi thoát phần còn lại ở hoà vốn. Tỉ lệ
đó rơi 15 điểm chỉ vì đổi độ mịn khớp lệnh.

Dự đoán kiểm được: TẮT breakeven thì khoảng cách giữa fill 4h và fill 1h phải THU HẸP
rõ rệt. Nếu đúng, phần chênh lệch đó là ảo tưởng của simulator, không phải lợi nhuận.
"""

from SmcElliottStrategy import SmcElliottStrategy

from freqtrade.strategy import BooleanParameter


class SmcElliottNoBE(SmcElliottStrategy):
    use_breakeven = BooleanParameter(default=False, space="sell", optimize=False)

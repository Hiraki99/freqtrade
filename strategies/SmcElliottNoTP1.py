"""SmcElliott với chốt-một-phần TẮT — bản sửa của thí nghiệm trong SmcElliottNoBE.

SmcElliottNoBE nhắm vào `use_breakeven`, nhưng tham số đó ĐÃ mặc định False ở lớp cha
("mặc định TẮT để giữ số liệu R:R sạch"), nên thí nghiệm đó là no-op: bật hay tắt đều ra
137 lệnh / +23.44% / PF 3.83 ở fill 4h và 146 / +14.46% / PF 2.25 ở fill 1h, giống hệt nhau.

Thủ phạm thật của hiện tượng "lệnh `stop_loss` mà vẫn có lãi" là `use_partial_tp`:
`adjust_trade_position` chốt `tp1_share` (50%) tại `tp1_rr` x R, phần còn lại mới dính stop.
Lệnh vì thế được ghi nhãn `stop_loss` nhưng tổng vẫn dương.

Vì sao đây mới là chỗ nhạy với độ mịn khớp lệnh: câu hỏi "giá kịp chạm TP1 trước hay chạm
stop trước" thuần tuý là đường đi BÊN TRONG nến. Nến 4h không biết, phải đoán — và mặc định
nó đoán theo hướng có lợi. Nến 1h chia nhỏ 4 lần nên đoán sai ít hơn.

Dự đoán kiểm được: TẮT chốt một phần thì khoảng cách 4h <-> 1h phải THU HẸP. Phần chênh lệch
biến mất chính là phần lợi nhuận do simulator tưởng tượng ra, không phải lợi nhuận thật.

★ KẾT QUẢ (2026-08-09) — GIẢ THUYẾT BỊ BÁC BỎ:

    TP1 bật : fill 4h +23.44% PF 3.83  ->  fill 1h +14.46% PF 2.25   (cách -8.98 điểm)
    TP1 tắt : fill 4h +17.45% PF 2.13  ->  fill 1h  +8.90% PF 1.48   (cách -8.55 điểm)

  Khoảng cách 4h<->1h gần như KHÔNG đổi. Chốt một phần không phải nguồn của sai lệch, nên
  sai lệch nằm ở khớp lệnh VÀO chứ không phải chuỗi thoát lệnh. Khớp với việc hyperopt của
  SmcPure chọn `entry_depth 0.97` — đặt limit sát đáy vùng là đánh cược thẳng vào giả định
  "giá chạm đáy nến thì lệnh khớp". Muốn siết tiếp thì phải soi mô hình khớp entry, không
  phải các nhánh TP.

  Kết quả phụ: TP1 là giá trị THẬT, không phải ảo giác simulator. Tắt nó mất ~6 điểm lợi
  nhuận ở CẢ HAI phân giải và win rate sụp 68.6% -> 44.5%.

KHÔNG phải chiến lược để chạy thật — chỉ là dụng cụ đo.
"""

from SmcElliottStrategy import SmcElliottStrategy

from freqtrade.strategy import BooleanParameter


class SmcElliottNoTP1(SmcElliottStrategy):
    use_partial_tp = BooleanParameter(default=False, space="sell", optimize=False)

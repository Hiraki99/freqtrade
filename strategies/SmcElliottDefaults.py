"""SmcElliott chạy THAM SỐ MẶC ĐỊNH TRONG CODE — không đọc file hyperopt.

Lý do tồn tại: freqtrade tự nạp `user_data/strategies/<tên-file>.json` nếu có, nên không
có cách nào chạy "bản mặc định" của SmcElliottStrategy mà không tạm xoá file tham số
đang dùng cho bot live. Lớp con này nằm ở file riêng và cố ý KHÔNG có json đi kèm, nên
nó luôn lấy giá trị default khai báo trong code.

Dùng để tách hai thứ hay bị lẫn khi so bảng kết quả: đâu là đóng góp của LOGIC (bản mặc
định, 0 bậc tự do) và đâu là đóng góp của DÒ THAM SỐ (bản hyperopt).
"""

from SmcElliottStrategy import SmcElliottStrategy


class SmcElliottDefaults(SmcElliottStrategy):
    pass

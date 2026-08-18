================================================================
 CHIẾN LƯỢC SWING TRADING v2.0
 SMC + Elliott Wave + Volume Profile
 (Bản mở rộng để backtest / thử nghiệm)
================================================================

LƯU Ý GIÁO DỤC: Đây là tài liệu học tập. SMC và Elliott Wave đều là
phương pháp diễn giải CHỦ QUAN, còn nhiều tranh cãi. Mọi tham số dưới
đây là giả thuyết để bạn TỰ KIỂM CHỨNG bằng backtest trên chính công cụ
mình giao dịch. Đây KHÔNG phải lời khuyên đầu tư.

----------------------------------------------------------------
0. TRIẾT LÝ CỐT LÕI (đọc trước khi làm gì khác)
----------------------------------------------------------------
- Thước đo đúng KHÔNG phải win rate, mà là EXPECTANCY (kỳ vọng):
      Expectancy = (Win% × Lãi_TB) − (Loss% × Lỗ_TB)
- Với RR 1:3, điểm HÒA VỐN chỉ khoảng 25% (chưa tính phí/spread).
      => Win rate 30–40% ở hệ này VẪN CÓ LÃI.
- Vấn đề "win rate thấp" thường KHÔNG nằm ở entry (bạn đã có quá nhiều
  bộ lọc entry), mà nằm ở 3 chỗ:
      (a) Đọc sai bối cảnh (Elliott chủ quan).
      (b) TP quá tham (giá không kịp chạm 1:3).
      (c) SL đặt sai chỗ (bị quét lại vùng thanh khoản).
- Nguyên tắc: dùng SMC + VỊ TRÍ giá để XÁC NHẬN sóng Elliott,
  KHÔNG dùng Elliott để DỰ ĐOÁN entry.

----------------------------------------------------------------
1. BỘ CHỈ SỐ / CÔNG CỤ CẦN THIẾT (đầy đủ)
----------------------------------------------------------------
A. CẤU TRÚC THỊ TRƯỜNG (Market Structure)
   [ ] Swing High / Swing Low
   [ ] BOS  (Break of Structure) – tiếp diễn xu hướng
   [ ] CHoCH (Change of Character) – tín hiệu đảo chiều sớm
   [ ] Phân biệt Internal Structure vs Swing Structure

B. THANH KHOẢN (Liquidity)
   [ ] Buy-side / Sell-side Liquidity
   [ ] Equal Highs / Equal Lows (EQH / EQL)
   [ ] Liquidity Sweep (quét thanh khoản)
   [ ] Inducement (IDM) – bẫy thanh khoản nội bộ

C. VÙNG QUAN TÂM (POI – Point of Interest)
   [ ] Order Block (OB) – ưu tiên OB chưa mitigate
   [ ] Fair Value Gap (FVG) – ưu tiên FVG chưa lấp
   [ ] Breaker Block (tùy chọn, nâng cao)
   [ ] Mitigation Block (tùy chọn, nâng cao)

D. PREMIUM / DISCOUNT  ***(mảnh ghép quan trọng nhất còn thiếu)***
   [ ] Vẽ Fibonacci trên "dealing range" (từ swing low → swing high
       của nhịp hiện tại)
   [ ] Equilibrium = mức 0.5 (đường phân chia)
   [ ] DISCOUNT = nửa dưới (dưới 0.5) → chỉ tìm lệnh BUY
   [ ] PREMIUM  = nửa trên (trên 0.5)  → chỉ tìm lệnh SELL
   [ ] Vùng OTE (Optimal Trade Entry) = 0.62 – 0.79

E. FIBONACCI
   [ ] Retracement: 0.5 / 0.62 / 0.705 / 0.79 (định vị OTE)
   [ ] Extension: 1.272 / 1.618 (mục tiêu chốt lời)

F. VOLUME PROFILE
   [ ] POC  (Point of Control) – giá giao dịch nhiều nhất
   [ ] VAH / VAL (biên trên/dưới Value Area 70%)
   [ ] HVN (High Volume Node) – vùng chấp nhận giá
   [ ] LVN (Low Volume Node) – vùng từ chối giá (tránh vào lệnh ở đây)

G. VOLUME TẠI NẾN PHÁ CẤU TRÚC  ***(bộ lọc chất lượng thường bị bỏ)***
   [ ] Kiểm tra volume TẠI cây nến BOS / CHoCH
   [ ] Effort vs Result (Wyckoff): phá cấu trúc mà volume cạn = nghi ngờ
   [ ] Displacement: cú phá phải có nến động lượng mạnh + để lại FVG

H. BỘ LỌC BỐI CẢNH / BIẾN ĐỘNG (Regime Filter)
   [ ] ATR – đo biến động, dùng để đệm SL và nhận diện thị trường
   [ ] ADX (hoặc nhận diện sideway thủ công):
         ADX > 20–25 = có xu hướng (ưu tiên)
         ADX < 20    = sideway → BOS/CHoCH dễ nhiễu (giảm size / bỏ qua)

I. YẾU TỐ NGOÀI BIỂU ĐỒ
   [ ] Lịch tin tức macro (FOMC / CPI / NFP / lãi suất)
   [ ] (Forex) DXY & tương quan cặp tiền
   [ ] (Crypto) BTC dominance / xu hướng BTC nếu trade altcoin

----------------------------------------------------------------
2. QUY TRÌNH PHÂN TÍCH ĐA KHUNG (Top-Down)
----------------------------------------------------------------
WEEKLY (bối cảnh chính)
   - Xác định xu hướng chính (chuỗi HH-HL hay LH-LL).
   - Đánh dấu vùng thanh khoản & POI lớn của Weekly.
   - LUẬT: chỉ giao dịch THEO xu hướng Weekly.

DAILY (định vị sóng)
   - Xác định sóng Elliott (dùng như bối cảnh LỎNG, không cứng nhắc).
   - Xác định "dealing range" và vùng Premium/Discount trên Daily.
   - Tìm điểm mua ở cuối sóng 2 hoặc sóng 4 (bán ngược lại nếu giảm).
   - Ghi lại LUÔN "alternate count" (kịch bản sóng thay thế) để biết
     mức giá nào sẽ PHỦ ĐỊNH kịch bản chính.

H4 (khung tín hiệu / entry)
   - POI của H4 phải NẰM TRONG POI/vùng thanh khoản của Daily/Weekly
     (POI lồng nhau – nested POI). OB H4 "lơ lửng" = chất lượng thấp.
   - Chuỗi tín hiệu lý tưởng:
         1) Liquidity Sweep (quét đáy/đỉnh trước)
         2) CHoCH có displacement (nến mạnh + để lại FVG)
         3) BOS xác nhận hướng mới
         4) Xác định OB + FVG tại vùng gốc của cú đẩy
   - Chờ giá RETEST Order Block để vào lệnh (không đuổi giá).

VOLUME PROFILE (xác nhận dòng tiền)
   - POC trùng / gần Order Block  → xác nhận mạnh.
   - HVN bao quanh vùng vào lệnh   → giá được "chấp nhận".
   - Nếu vùng vào lệnh chỉ nằm trên LVN đơn độc → BỎ QUA.

----------------------------------------------------------------
3. BẢNG CHẤM ĐIỂM CONFLUENCE CÓ TRỌNG SỐ
   (thay cho kiểu "đủ 9/9 mới vào")
----------------------------------------------------------------
>>> 2 CỔNG BẮT BUỘC (thiếu 1 trong 2 = KHÔNG VÀO, bất kể điểm số) <<<
   G1. Cùng xu hướng Weekly.
   G2. Giá đúng phía Premium/Discount (Buy ở Discount / Sell ở Premium).

>>> CHẤM ĐIỂM (tổng 100). Chỉ vào lệnh khi ≥ 70 điểm <<<
   +20  CHoCH có displacement rõ (nến động lượng + FVG)
   +15  Liquidity Sweep xảy ra TRƯỚC điểm vào (quét EQH/EQL/IDM)
   +15  OB + FVG chồng lấp nhau tại POI
   +15  POI H4 lồng trong POI khung lớn hơn (Daily/Weekly)
   +10  BOS xác nhận hướng mới sau CHoCH
   +10  POC hoặc HVN trùng vùng Order Block
   +10  Vùng vào nằm trong OTE (0.62–0.79)
   + 5  Volume nở rộng tại nến phá cấu trúc (BOS/CHoCH)
   -----
   TỔNG = 100

>>> ĐIỂM TRỪ (cảnh báo – trừ vào tổng) <<<
   -20  Vùng vào chỉ có LVN (không có HVN/POC hỗ trợ)
   -20  ADX < 20 / thị trường đang sideway rõ
   -15  Có tin macro lớn trong thời gian dự kiến giữ lệnh
   -15  RR không đạt tối thiểu 1:3
   -10  OB đã bị mitigate 1 lần trước đó
   -10  (Forex) DXY / tương quan đang chống lại hướng lệnh

PHÂN LOẠI SETUP:
   85–100 = A+  (size chuẩn, có thể giữ mục tiêu xa)
   70–84  = A   (size chuẩn)
   55–69  = B   (quan sát / size nhỏ / hoặc bỏ – TÙY backtest)
   < 55   = KHÔNG GIAO DỊCH

----------------------------------------------------------------
4. QUẢN LÝ RỦI RO & LỆNH
----------------------------------------------------------------
KÍCH THƯỚC LỆNH
   - Rủi ro cố định mỗi lệnh: 0.5% – 1% tài khoản (tự chọn, giữ nhất quán).
   - Tính khối lượng NGƯỢC từ khoảng cách SL, KHÔNG tính từ số lot cố định.

STOP LOSS
   - BUY : dưới đáy cú Sweep + đệm (ví dụ + 0.3–0.5 × ATR).
   - SELL: trên đỉnh cú Sweep + đệm tương tự.
   - LÝ DO đệm: chính vùng vừa bị quét là nơi DỄ bị quét lần hai. Đặt SL
     sát mép wick → dính nhiễu; đặt quá xa → hỏng RR. ATR giúp cân bằng.

TAKE PROFIT (chốt lời từng phần – giảm áp lực win rate)
   - TP1: tại 1:1.5 đến 1:2  → chốt 50% + DỜI SL VỀ HÒA VỐN.
   - TP2: phần còn lại chạy tới:
         • Mục tiêu sóng 3 hoặc sóng 5 (Elliott), HOẶC
         • Fibonacci Extension 1.272 / 1.618, HOẶC
         • Vùng thanh khoản đối diện (đỉnh/đáy cũ, EQH/EQL).
   * Lý do: TP 1:3 cố định khiến ÍT lệnh chạm đích → tự tay hạ win rate.
     Chốt một phần sớm giúp đường vốn mượt hơn mà vẫn giữ được "runner".

DỜI STOP LOSS
   - Về hòa vốn (breakeven) khi đạt RR 1:1.
   - Cân nhắc trailing theo từng swing mới (dời SL dưới HL mới cho Buy).

----------------------------------------------------------------
5. VÔ HIỆU HÓA SETUP (Trade Invalidation)
----------------------------------------------------------------
Setup coi như "chết", HỦY chờ đợi, nếu:
   - Giá đóng nến NGƯỢC hẳn qua Order Block (OB không giữ được).
   - Retest KHÔNG xảy ra sau N nến (ví dụ 8–12 nến H4) → setup ôi thiu.
   - Giá phá mức PHỦ ĐỊNH của kịch bản Elliott (alternate count kích hoạt).
   - Xuất hiện CHoCH ngược lại hướng dự kiến trước khi vào lệnh.

----------------------------------------------------------------
6. CHECKLIST VÀO LỆNH (in ra dùng khi trade thực)
----------------------------------------------------------------
CỔNG:
   [ ] Cùng xu hướng Weekly?
   [ ] Đúng phía Premium/Discount?
TÍN HIỆU:
   [ ] Có Liquidity Sweep trước entry?
   [ ] Có CHoCH (kèm displacement)?
   [ ] Có BOS xác nhận?
   [ ] OB + FVG chồng lấp?
   [ ] POI lồng trong khung lớn?
   [ ] Đã retest OB (không đuổi giá)?
   [ ] FVG còn hiệu lực?
XÁC NHẬN DÒNG TIỀN:
   [ ] POC/HVN trùng vùng vào? (không phải LVN đơn độc)
   [ ] Volume nở tại nến phá cấu trúc?
BỘ LỌC:
   [ ] ADX/thị trường không sideway?
   [ ] Không kẹt tin macro lớn?
   [ ] (Forex) DXY/tương quan thuận?
RỦI RO:
   [ ] RR ≥ 1:3?
   [ ] SL đặt đúng chỗ (có đệm ATR)?
   [ ] Rủi ro ≤ 1% tài khoản?
   [ ] Điểm confluence ≥ 70?

----------------------------------------------------------------
7. KHUNG NHẬT KÝ & BACKTEST (kết nối Stage 5)
----------------------------------------------------------------
MỤC TIÊU: KHÔNG thêm/bớt tham số theo cảm tính (đó là curve-fitting).
Thay vào đó GẮN NHÃN từng lệnh rồi để DỮ LIỆU cho biết yếu tố nào
thực sự tương quan với lệnh THẮNG.

Với mỗi lệnh backtest, ghi lại các cột sau (ví dụ trên Excel/Sheets):
   1)  Ngày / cặp / khung
   2)  Hướng (Buy/Sell)
   3)  Loại Sweep (EQH/EQL/IDM/không có)
   4)  Có ở Discount/Premium đúng phía? (Y/N)
   5)  Displacement mạnh/yếu tại CHoCH
   6)  OB + FVG chồng lấp? (Y/N)
   7)  POI lồng khung lớn? (Y/N)
   8)  POC/HVN trùng vùng? (Y/N)   / hay chỉ LVN?
   9)  Volume tại nến phá: nở/cạn
   10) Trong OTE? (Y/N)
   11) ADX lúc vào / regime (trend/sideway)
   12) Điểm confluence (0–100) + hạng (A+/A/B)
   13) RR kế hoạch  vs  RR thực đạt
   14) Kết quả (Win/Loss/BE) + R nhận được
   15) Ghi chú (lý do thắng/thua, sai ở đâu)

CÁCH ĐỌC DỮ LIỆU SAU ~50–100 LỆNH:
   - Lọc riêng nhóm THẮNG, xem yếu tố nào xuất hiện nhiều hơn hẳn.
   - So expectancy của lệnh CÓ vs KHÔNG có từng yếu tố.
       => Yếu tố nào không tạo khác biệt → cân nhắc BỎ (giảm nhiễu).
       => Yếu tố nào tách bạch rõ Win/Loss → tăng trọng số.
   - Rất có thể phát hiện: vài điều kiện trong 9 điều kiện gốc KHÔNG
     đóng góp gì, còn Premium/Discount + POI lồng khung mới là "vàng".

----------------------------------------------------------------
8. LƯU Ý VẬN HÀNH
----------------------------------------------------------------
- KHÔNG giao dịch ngược xu hướng Weekly.
- KHÔNG vào lệnh chỉ vì "có Order Block".
- Chỉ vào khi ĐỦ confluence (≥ 70 điểm + 2 cổng), KHÔNG châm chước.
- Ưu tiên CHẤT LƯỢNG hơn SỐ LƯỢNG lệnh.
- Backtest TỐI THIỂU 50–100 lệnh trước khi kết luận hệ thống tốt/xấu.
- Theo dõi bằng EXPECTANCY và đường vốn, KHÔNG bị ám ảnh win rate.
- Giữ mọi tham số NHẤT QUÁN suốt một đợt test; đổi 1 biến/lần để biết
  biến đó ảnh hưởng ra sao.

================================================================
 HẾT — v2.0 | Tài liệu học tập, không phải lời khuyên đầu tư
================================================================

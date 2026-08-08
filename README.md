# Bot báo video Douyin mới qua Telegram

Bot gọi TikHub App V3, lấy đúng 4 tác phẩm đầu của từng kênh và chọn tác phẩm có
`create_time` lớn nhất. Cách này không nhầm ba video ghim cũ là video vừa đăng.

- `kenhuutien.txt`: quét mặc định 30 phút/lần.
- `kenhthuong.txt`: quét mặc định 60 phút/lần.
- `cauhinh.txt`: token, chat ID và các tùy chọn.
- `/health`: URL để UptimeRobot gọi mỗi 5 phút.
- Lần chạy đầu mặc định chỉ lập mốc, không gửi lại video cũ.

Xem hướng dẫn đầy đủ trong `HUONG-DAN-SETUP.md`.

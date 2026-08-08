# Hướng dẫn cài bot Douyin → Telegram trên Render

## 1. Chuẩn bị Telegram Bot

1. Mở Telegram, tìm đúng tài khoản `@BotFather` có dấu xác minh.
2. Gửi `/newbot`, đặt tên và username theo hướng dẫn.
3. Sao chép token bot. Token có dạng `123456789:AA...`; coi nó như mật khẩu.
4. Mở cuộc trò chuyện với bot vừa tạo và bấm **Start** (hoặc gửi `/start`). Bot
   không thể chủ động nhắn cho tài khoản chưa từng mở chat với nó.
5. Gửi thêm một tin bất kỳ cho bot, rồi mở đường dẫn sau trong trình duyệt (thay
   token thật):

   `https://api.telegram.org/bot<TOKEN_CUA_BAN>/getUpdates`

6. Tìm đoạn `"chat":{"id":123456789,...}`. Số ở trường `id` là
   `TELEGRAM_CHAT_ID`.

Nếu nhận thông báo trong nhóm: thêm bot vào nhóm, gửi một tin trong nhóm, gọi
`getUpdates` như trên và lấy `message.chat.id` (thường là số âm). Nếu nhận trong
kênh Telegram, thêm bot làm quản trị viên có quyền đăng bài; có thể dùng
`@username_cua_kenh` làm `TELEGRAM_CHAT_ID`.

## 2. Điền ba file TXT

### `kenhuutien.txt`

Mỗi dòng một `sec_uid`, quét 30 phút/lần:

```text
MS4wLjABAAAAxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx
MS4wLjABAAAAyyyyyyyyyyyyyyyyyyyyyyyyyyyyyyyyyyyyyyyy | Tên để mình nhớ
```

Phần sau dấu `|` chỉ là ghi chú, bot không gửi nó sang TikHub.

### `kenhthuong.txt`

Tương tự, mỗi dòng một `sec_uid`, quét 60 phút/lần. Nếu một ID bị ghi ở cả hai
file, bot chỉ xếp nó vào nhóm ưu tiên để không tốn hai request.

### `cauhinh.txt`

Ba dòng bắt buộc:

```text
TIKHUB_API_TOKEN=token_tikhub_cua_ban
TELEGRAM_BOT_TOKEN=token_botfather_cua_ban
TELEGRAM_CHAT_ID=chat_id_cua_ban
```

Giữ các dòng lịch mặc định:

```text
CHU_KY_UU_TIEN_PHUT=30
CHU_KY_THUONG_PHUT=60
SO_VIDEO_QUET=4
THONG_BAO_LAN_DAU=false
```

- `THONG_BAO_LAN_DAU=false`: lúc mới chạy, bot ghi nhớ video hiện tại và không
  làm phiền bằng một video cũ. Từ lần quét sau mới báo video mới.
- Có thể tạm đặt `true` để thử một thông báo thật, sau đó đổi lại `false`.

## 3. Thử trên máy Windows (khuyên làm)

Mở PowerShell trong thư mục dự án và chạy:

```powershell
py -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
python bot.py --test-telegram
```

Nếu Telegram nhận tin `Kết nối Telegram thành công`, phần Telegram đã đúng.
Chạy bot đầy đủ:

```powershell
python bot.py
```

Mở `http://localhost:10000/health`. Dừng bot bằng `Ctrl+C`.

Chạy bộ kiểm thử (không gọi TikHub, không gửi Telegram):

```powershell
python -m unittest discover -s tests -v
```

## 4. Đưa code lên GitHub

1. Tạo một repository GitHub mới. Có thể để **Private**.
2. Tải toàn bộ thư mục này lên repository, gồm `bot.py`, `requirements.txt`,
   `render.yaml`, `.python-version`, hai file kênh và các file hướng dẫn.
3. Không commit token thật. Trước khi upload, giữ `cauhinh.txt` ở dạng mẫu. Token
   thật sẽ được nhập bằng **Secret File** ở Render.

Nếu dùng Git ở PowerShell:

```powershell
git init
git add .
git commit -m "Tao bot giam sat Douyin"
git branch -M main
git remote add origin https://github.com/TEN_CUA_BAN/TEN_REPO.git
git push -u origin main
```

## 5. Tạo Web Service miễn phí trên Render

1. Đăng nhập `https://dashboard.render.com` và kết nối tài khoản GitHub.
2. Chọn **New → Web Service**.
3. Chọn repository vừa tạo.
4. Nhập các trường sau (file `render.yaml` đi kèm là lựa chọn thay thế nếu bạn
   quen dùng **New → Blueprint**):

   - Runtime: **Python 3**
   - Build Command: `pip install -r requirements.txt`
   - Start Command:
     `gunicorn --workers 1 --threads 4 --timeout 120 --bind 0.0.0.0:$PORT bot:app`
   - Instance Type: **Free**
   - Health Check Path: `/health`

5. Bấm **Create Web Service**. Lần deploy đầu có thể báo cấu hình chưa được điền;
   sang bước tiếp theo để thêm ba Secret Files.

Không tăng số worker lên trên 1. Mỗi worker sẽ có một lịch quét riêng và có thể
gọi TikHub/gửi Telegram trùng lặp.

## 6. Tạo ba Secret Files trên Render

Trong Web Service, mở **Environment → Secret Files → Add Secret File** và tạo
đúng ba file sau.

### Secret File 1

- Filename: `cauhinh.txt`
- Contents: dán toàn bộ nội dung `cauhinh.txt` đã điền token thật.

### Secret File 2

- Filename: `kenhuutien.txt`
- Contents: dán danh sách kênh ưu tiên.

### Secret File 3

- Filename: `kenhthuong.txt`
- Contents: dán danh sách kênh thường.

Bấm **Save Changes**. Render sẽ deploy lại. Bot ưu tiên đọc ba file ở
`/etc/secrets`, vì vậy các file mẫu trên GitHub không ghi đè cấu hình thật.

Vào **Logs**, kết quả đúng sẽ có dạng:

```text
Bot sẵn sàng: ... kênh ưu tiên (30 phút), ... kênh thường (60 phút)
Đã tạo mốc lần đầu cho sec_uid=...
```

Mở URL `https://TEN-DICH-VU.onrender.com/health`. JSON phải có `"status":"ok"`
và `scheduler.state` là `running`. Nếu `scheduler.state` là `error`, xem
`last_error` và Logs.

## 7. Cấu hình UptimeRobot

1. Tạo/đăng nhập tài khoản tại `https://uptimerobot.com`.
2. Chọn **+ Add New Monitor**.
3. Monitor Type: **HTTP(s)**.
4. Friendly Name: `Douyin Telegram Bot`.
5. URL: `https://TEN-DICH-VU.onrender.com/health`.
6. Monitoring Interval: **5 minutes** (mức của gói Free).
7. Lưu monitor và đợi trạng thái **Up**.

Render Free ngủ sau 15 phút không có request vào. Ping `/health` mỗi 5 phút giữ
web service hoạt động; ping này không gọi TikHub nên không làm tăng phí TikHub.

## 8. Cách bot nhận biết video mới

Mỗi lượt của một kênh chỉ tạo **một request TikHub** với:

```text
max_cursor=0
count=4
sort_type=0
```

Bot xem đúng bốn mục đầu, rồi so sánh `create_time` của cả bốn. Vì vậy dù ba vị
trí đầu là video ghim cũ, video thứ tư vừa đăng vẫn có thời gian lớn nhất. Bot
lưu ID và thời gian đã thấy; chỉ cập nhật trạng thái sau khi Telegram gửi thành
công. Nếu video mới nhất bị xóa làm một video cũ quay lại đầu trang, bot không
báo nhầm video cũ đó là video mới.

Số request TikHub mỗi ngày:

```text
48 × số kênh ưu tiên + 24 × số kênh thường
```

Ví dụ 5 kênh ưu tiên và 10 kênh thường: `48×5 + 24×10 = 480 request/ngày`.

## 9. Cập nhật danh sách kênh

Sửa nội dung Secret File tương ứng trong **Render → Environment**, rồi Save.
Render sẽ deploy lại. Lần khởi động mới mặc định lập mốc hiện tại, nên không gửi
hàng loạt video cũ.

## 10. Lưu ý về Render Free

Render Free dùng filesystem tạm: file trạng thái `data/trangthai.json` mất khi
service restart, redeploy hoặc ngủ. UptimeRobot giảm khả năng ngủ, nhưng Render
vẫn có thể tự restart. Bot xử lý an toàn bằng cách lập mốc lại và không báo video
cũ; đổi lại, một video đăng đúng trong khoảng restart có thể bị bỏ lỡ.

Muốn trạng thái bền tuyệt đối cần kho dữ liệu bên ngoài hoặc Render trả phí có
Persistent Disk. Không nên dùng Free Postgres chỉ cho mục đích này nếu cần chạy
lâu dài vì database Free có thời hạn.

## 11. Xử lý lỗi nhanh

- `Chưa điền cấu hình bắt buộc`: Secret File `cauhinh.txt` thiếu hoặc còn giá trị
  mẫu.
- Telegram `400 chat not found`: sai chat ID, chưa bấm Start, hoặc bot chưa được
  thêm/cấp quyền trong nhóm/kênh.
- Telegram `401 Unauthorized`: token BotFather sai hoặc đã bị thu hồi.
- TikHub `401/403`: token TikHub sai/hết quyền.
- TikHub `402`: tài khoản không đủ số dư.
- TikHub `429`: gọi quá nhanh; tăng `NGHI_GIUA_CAC_KENH_GIAY` (ví dụ `1`).
- `TikHub không trả về danh sách video`: kiểm tra sec_uid, kênh riêng tư/bị khóa,
  hoặc mở Logs xem thông báo API.
- Có hai thông báo giống nhau: kiểm tra Start Command phải có đúng
  `--workers 1`, và chỉ chạy một Web Service.

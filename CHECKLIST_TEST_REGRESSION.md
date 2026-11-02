# Checklist Kiểm Thử & Chống Degrade — Advance Clipboard Focused

Tài liệu này tổng hợp toàn bộ danh mục tính năng từ bản gốc (Baseline G6) đến các tính năng mở rộng đã chốt trong bản **Advance Clipboard Focused**, dùng làm checklist kiểm thử hồi quy (regression test) trước mỗi lần phát hành.

## 1. Khởi chạy, Hệ thống & Vòng đời Ứng dụng (Baseline Core)
- [ ] **Single-instance & IPC Activation**: Chạy lần 2 khi app đang chạy nền sẽ tự động gọi cửa sổ của tiến trình đầu tiên lên, không tạo tiến trình trùng lặp.
- [ ] **Hotkey toàn cục `Ctrl+Alt+V`**: Bật/ẩn cửa sổ clipboard tại vị trí con trỏ chuột (tự căn lề màn hình `UI_EDGE_MARGIN` không bị tràn mép).
- [ ] **Tự ẩn khi mất focus & nhấn `Esc`**: Click ra ngoài cửa sổ chính hoặc nhấn `Esc` sẽ ẩn giao diện clipboard ngay lập tức.
- [ ] **Không tự ẩn khi mở Popup/Dialog con**: Khi mở menu chuột phải, hộp thoại `Gán phím tắt (Ctrl+key)`, `Cấu hình Action`, `Terminal Log (Show log)`, `Add Tag`, `New Group`, hoặc `Fix`, cửa sổ clipboard chính vẫn giữ nguyên và **hộp thoại con luôn nổi lên phía trước (`WindowStaysOnTopHint` / `HWND_TOPMOST`)**, không bao giờ bị chìm sau UI float.
- [ ] **Zero Polling khi ẩn**: Khi UI ẩn, chỉ lắng nghe sự kiện `WM_CLIPBOARDUPDATE` và `WM_HOTKEY` qua cửa sổ message-only Win32 (`HWND_MESSAGE`), không chạy timer quét ngầm gây tốn CPU/RAM.

## 2. Bố cục 2 Cột, Sao chép & Dán An toàn (Capture / Copy / Paste)
- [ ] **Bố cục 2 cột**: Cột **History** bên trái (hiển thị lịch sử mới nhất trên cùng) và cột **Pinned** bên phải (hiển thị các nhóm thư mục và các mục đã ghim).
- [ ] **Tự động bắt Clipboard (Text & Image)**: Copy văn bản hoặc hình ảnh từ bất kỳ ứng dụng nào đều tự động lưu vào History, khử trùng lặp bằng mã băm MD5 và đẩy mục trùng lên đầu.
- [ ] **Clipboard Guard chống tự lặp & chống tranh chấp khóa**: Khi ứng dụng tự ghi vào clipboard để paste/copy, không kích hoạt đọc ngược lại gây lỗi `Retrying to obtain clipboard.`.
- [ ] **Dán an toàn vào Windows Explorer (F2 Rename & Thanh địa chỉ)**:
  - [ ] Đang bấm `F2` đổi tên file/folder trong Windows Explorer $\rightarrow$ bấm `Ctrl+Alt+V` $\rightarrow$ chọn clip (Enter / Click / `Alt+1..6` / `Ctrl+key`) $\rightarrow$ dán đúng vào ô tên file mà **không làm thoát chế độ F2**.
  - [ ] Đang focus thanh địa chỉ (Address Bar) của Explorer $\rightarrow$ bấm `Ctrl+Alt+V` $\rightarrow$ dán thẳng vào thanh địa chỉ.
- [ ] **Dán đúng 1 lần, không Auto-Enter**: Nhấn `Enter`, click chuột trái vào dòng clip, nhấn `Alt+1..6`, hoặc nhấn `Ctrl+<key>` chỉ dán nội dung đúng 1 lần vào cửa sổ đích trước đó, tuyệt đối không tự gửi phím Enter.
- [ ] **Nút hành động nhanh trên từng dòng clip**:
  - [ ] Nút `❐` (Copy): Chỉ chép vào bộ nhớ tạm và đẩy lên đầu History mà không đóng cửa sổ hay tự paste.
  - [ ] Nút `☆ / ★` (Pin/Unpin): Ghim mục sang cột Pinned hoặc bỏ ghim.
  - [ ] Nút `✕` (Delete): Xóa mục mượt mà (gom nhóm xóa nhanh không giật UI).
  - [ ] Nút `▼` (Inline Expand): Mở rộng/thu gọn khung xem văn bản nhiều dòng ngay tại dòng đó mà không làm nhảy vị trí cuộn.

## 3. Quản lý Tag, Group, Chỉnh sửa (Fix) & Xem trước (Preview)
- [ ] **Quản lý Tag**: Chuột phải $\rightarrow$ `Add Tag` để gắn/sửa nhãn; nhãn hiển thị góc dưới bên phải của dòng clip.
- [ ] **Quản lý Group (Cột Pinned)**:
  - [ ] Chuột phải $\rightarrow$ `Add to Group` $\rightarrow$ chọn nhóm có sẵn hoặc `New Group...` (nếu clip chưa ghim sẽ tự động ghim vào Pinned).
  - [ ] Click vào thanh tiêu đề nhóm (`📁 Tên nhóm`) để đóng/mở (`▶ / ▼`) các clip con bên trong.
  - [ ] Chuột phải $\rightarrow$ `Remove from '<Group>'` để đưa clip ra khỏi nhóm.
- [ ] **Sửa nội dung Clip (`Fix`)**: Với clip văn bản ở cột Pinned, chuột phải $\rightarrow$ `Fix` mở cửa sổ sửa nội dung có hỗ trợ tìm kiếm trong clip (`Search in clip`) và kiểm tra trùng lặp trước khi lưu.
- [ ] **Xem trước (`Show preview`)**: Chuột phải $\rightarrow$ `Show preview` mở khung xem trước văn bản dài (tải theo từng chunk khi cuộn) hoặc hình ảnh độ phân giải cao (giải mã bất đồng bộ không chặn UI).
- [ ] **Tác vụ riêng cho Clip Ảnh**: Chuột phải vào clip ảnh có đủ `Open this image`, `Copy image path`, và `Open with Paint`.

## 4. Phím tắt Dán nhanh `Alt+1…6` theo Cột hiển thị
- [ ] **`Alt+1`, `Alt+2`, `Alt+3`**: Dán ngay lập tức clip đang hiển thị ở vị trí thứ 1, 2, 3 của cột **History** (bên trái).
- [ ] **`Alt+4`, `Alt+5`, `Alt+6`**: Dán ngay lập tức clip đang hiển thị ở vị trí thứ 1, 2, 3 của cột **Pinned** (bên phải).
- [ ] **Tương thích bộ lọc Search & Group**:
  - [ ] Khi đang gõ tìm kiếm (ví dụ gõ `la`), `Alt+1..3` và `Alt+4..6` lấy đúng thứ tự kết quả đang hiển thị sau khi lọc.
  - [ ] Tự động bỏ qua thanh tiêu đề nhóm (`Group Header`) và bỏ qua các clip nằm trong nhóm đang đóng (`Collapsed Group`).
  - [ ] Hoạt động ngay khi con trỏ đang nằm trong ô tìm kiếm (`SearchLineEdit`).
  - [ ] Nếu cột có ít hơn số thứ tự phím bấm (ví dụ Pinned chỉ có 1 mục mà bấm `Alt+5`), chương trình giữ an toàn không làm gì, không dán nhầm sang cột khác.

## 5. Phím tắt Cố định `Ctrl+<key>` cho từng Clip
- [ ] **Gán phím tắt**: Chuột phải vào clip $\rightarrow$ `Gán phím tắt (Ctrl+key)...` $\rightarrow$ hộp thoại nổi lên trên cùng và tự động focus vào ô nhập.
- [ ] **Nhận diện đúng 1 ký tự (`0–9`, `A–Z`)**:
  - [ ] Chấp nhận cả khi gõ trực tiếp `1`, `c`, `q` hoặc bấm `Ctrl+1`, `Ctrl+C`, `Ctrl+Q` trong ô nhập.
  - [ ] Tự động chuẩn hóa chữ hoa (`c` $\rightarrow$ `C`, `q` $\rightarrow$ `Q`), hoạt động ổn định cả khi đang bật bộ gõ tiếng Việt (UniKey/EVKey).
  - [ ] Từ chối dán chuỗi nhiều ký tự vào ô phím tắt.
- [ ] **Kiểm tra xung đột**: Nếu phím đã thuộc về clip khác, hiển thị cảnh báo kèm nội dung clip đang giữ phím và nút **Chuyển gán**.
- [ ] **Kích hoạt dán bằng `Ctrl+<key>`**:
  - [ ] Khi cửa sổ clipboard đang mở, nhấn `Ctrl+1`, `Ctrl+C`, `Ctrl+Q`... sẽ dán ngay clip đã gán phím đó, bất kể clip đang ở trang nào hay từ khóa tìm kiếm hiện tại là gì.
  - [ ] Chống dán lặp khi giữ đè phím (`isAutoRepeat`).
  - [ ] Nếu một chữ cái (ví dụ `Ctrl+A`, `Ctrl+C`, `Ctrl+V`) chưa được gán cho clip nào thì vẫn giữ nguyên tác dụng chỉnh sửa văn bản trong ô tìm kiếm.
- [ ] **Gỡ phím tắt & Xóa đồng bộ**: Chuột phải $\rightarrow$ `Gỡ phím tắt (Ctrl+...)` hoặc khi xóa clip thì phím tắt tự động được dọn sạch trong cùng transaction DB.

## 6. Clip Actions (`RUN` / `STOP` / `Show log` / `Ctrl+Enter`) & Mở URL
- [ ] **Cấu hình Action**: Chuột phải $\rightarrow$ `Gán Action...` / `Sửa Action...` hỗ trợ 4 loại:
  - [ ] **File Script (`.py`, `.ps1`, `.bat`, `.cmd`, `.sh`)**: Bắt buộc nhập đường dẫn tuyệt đối (**Full path**). Khi chạy `.py`, thực thi bằng `uv run <full_path> [args]` với thư mục làm việc (`CWD`) đặt tại chính thư mục chứa script đó.
  - [ ] **Mã Python trực tiếp (Inline)**: Kiểm tra cú pháp `compile()` trước khi lưu, chạy với cờ `-u` (unbuffered output).
  - [ ] **Lệnh ngoài (Command + arguments)**: Chạy theo danh sách đối số `argv`.
  - [ ] **Mở URL Web**: Tự động điền sẵn nếu nội dung clip là `http://` hoặc `https://`; mở bằng trình duyệt mặc định (`os.startfile`).
- [ ] **Chỉ thực thi chủ động**: Sao chép, dán (`Enter` / Click / `Alt+1..6` / `Ctrl+key`), xem trước hoặc tìm kiếm **không bao giờ** tự chạy action. Chỉ khi bấm **`⚡ RUN Action`** hoặc nhấn **`Ctrl+Enter`** mới thực thi.
- [ ] **Ghim tự động ở vị trí số 4 đổ đi khi đang chạy Action**:
  - [ ] Nếu clip đang chạy action thuộc cột **History**, clip đó tự động được giữ cố định từ **vị trí thứ 4** bên cột History (ngay dưới 3 vị trí `Alt+1..3`).
  - [ ] Nếu clip đang chạy action thuộc cột **Pinned**, clip đó tự động được giữ cố định từ **vị trí thứ 4** bên cột Pinned (ngay dưới 3 vị trí `Alt+4..6`).
- [ ] **Chuyển nút `RUN Action` $\rightarrow$ `STOP Action` + `Show log` khi đang chạy**:
  - [ ] Khi clip đang chạy action, cả nút trên dòng clip lẫn mục trong menu chuột phải tự động đổi từ **`⚡ RUN Action`** sang **`⏹ STOP Action`** và hiện thêm nút **`📋 Show log`** (nút `Show log` chỉ xuất hiện ở các clip đang có action chạy).
  - [ ] Bấm **`⏹ STOP Action`** lập tức dừng toàn bộ cây tiến trình con (`taskkill /F /T /PID` trên Windows) và trả nút về trạng thái `⚡ RUN Action`.
  - [ ] Bấm **`📋 Show log`** mở cửa sổ **Terminal Log** nổi trên cùng, hiển thị trực tiếp (real-time streaming) toàn bộ `stdout`/`stderr` của tiến trình đang chạy kèm nút `⏹ STOP Action` và `Copy Log`.

## 7. Tìm kiếm Nhanh & Bộ lọc Chuyên biệt (SQLite FTS5 + Filters)
- [ ] **Tìm kiếm từ khóa tự do & cụm từ**: Gõ từ khóa bất kỳ lọc đồng thời cả 2 cột History và Pinned theo độ ưu tiên (khớp chính xác $\rightarrow$ khớp tiền tố $\rightarrow$ khớp toàn bộ từ khóa $\rightarrow$ thời gian cập nhật).
- [ ] **Bộ lọc `tag:` / `tags:`**:
  - [ ] Gõ `tag:` hoặc `tags:` (không kèm từ khóa) $\rightarrow$ hiện toàn bộ các clip đã gắn tag.
  - [ ] Gõ `tag:<tên>` hoặc `tag <tên>` $\rightarrow$ lọc các clip có tag khớp `<tên>`.
- [ ] **Bộ lọc `image:` / `img:` / `type:image`**: Hiện toàn bộ các clip hình ảnh.
- [ ] **Bộ lọc `action` / `action:`**:
  - [ ] Gõ `action` hoặc `action:` $\rightarrow$ hiện toàn bộ các clip đang được gắn Action ở cả 2 cột.
  - [ ] Gõ `action: <từ_khóa>` $\rightarrow$ lọc các clip có Action khớp nội dung, tag, đường dẫn script hoặc loại action.
- [ ] **Bộ lọc `ctrl` / `ctrl:`**:
  - [ ] Gõ `ctrl` hoặc `ctrl:` $\rightarrow$ hiện toàn bộ các clip đang được gán phím tắt `Ctrl+<key>` (sắp xếp theo ký tự phím tắt).
  - [ ] Gõ `ctrl: <phím>` (ví dụ `ctrl: q` hoặc `ctrl: 1`) $\rightarrow$ lọc đúng clip đang giữ phím tắt đó.

## 8. Sao lưu, Khôi phục & Toàn vẹn Cơ sở dữ liệu (Backup & Migration)
- [ ] **SQLite WAL Mode & FTS5 Triggers**: Tự động đồng bộ bảng chỉ mục `clips_fts` khi thêm, sửa, xóa clip.
- [ ] **Backup tự động chạy nền**: Tự động tạo bản sao lưu JSON nén định kỳ khi dữ liệu thay đổi, bảo toàn đầy đủ cả `shortcut_key` (`clip_shortcuts`) và cấu hình `action` (`clip_actions`).
- [ ] **Khôi phục thảm họa (Disaster Recovery)**: Khi DB trống hoặc hỏng, tự động khôi phục từ bản backup hợp lệ gần nhất (bao gồm cả phím tắt và cấu hình action, nhưng không tự ý thực thi action khi restore).

# Advance Clipboard Focused Workspace

Advance Clipboard phiên bản tinh gọn, tập trung cao độ vào trải nghiệm clipboard chuyên nghiệp, hiệu năng cao trên Windows.

---

## 1. Tính năng cốt lõi & Những cải tiến chính

### 1.1. Giữ nguyên 100% trải nghiệm baseline ổn định
- Giao diện 2 cột tiêu chuẩn: **History** bên trái (lịch sử sao chép) và **Pinned** bên phải (các mục đã ghim).
- Hỗ trợ đầy đủ văn bản (text) và hình ảnh (image), chunking/preview async, trình soạn thảo mở rộng (inline expand).
- Đầy đủ tag, group thư mục, đóng/mở nhóm (collapse/expand), đổi thứ tự, ghim/bỏ ghim, sửa nội dung.
- Công nghệ lưu trữ SQLite WAL mode, FTS5 tìm kiếm toàn văn siêu tốc, backup/disaster recovery tự động.
- Không rò rỉ bộ nhớ, không polling CPU khi UI ẩn, listener Win32 theo sự kiện thuần túy.

### 1.2. Loại bỏ hoàn toàn các subsystem thừa (No Bloat)
- Đã loại bỏ: Command palette (Ctrl+K), TransformEngine (24+ bộ biến đổi JSON/Base64/Case/Path), Dynamic snippets/templates, Clipboard chains, Secret-redaction toolkit, Encrypted vaults.
- Không can thiệp hoặc thay đổi clipboard ngoài ý muốn của người dùng.

### 1.3. Khắc phục lỗi thoát F2 Rename / Thanh địa chỉ Windows Explorer
- **Nguyên nhân cũ**: Khi mở clipboard hoặc khi paste, việc gọi `AttachThreadInput` và nhả phím `Alt` thô khiến Windows gửi `WM_KILLFOCUS` và `SC_KEYMENU` (kích hoạt menu bar/ribbon), làm Windows Explorer tự hủy ô nhập đổi tên file (F2) hoặc thanh địa chỉ.
- **Giải pháp**:
  - Loại bỏ hoàn toàn `AttachThreadInput` cưỡng bức khi hiển thị giao diện.
  - Khi ứng dụng đích đang mở cửa sổ điều khiển nhập liệu lớp `Edit` (như ô rename F2 hoặc thanh địa chỉ Explorer), chương trình gửi trực tiếp thông điệp `WM_PASTE` (0x0302) vào cửa sổ con đó mà không cần nhả phím hệ thống.
  - Khi sử dụng keyboard injection, chèn phím ảo trung tính (0x07) trước khi nhả phím `Alt` để ngăn Windows Explorer kích hoạt menu bar (`SC_KEYMENU`).

### 1.4. Phím tắt Alt+1…6: Dán nhanh theo kết quả hiển thị hiện tại
Kích hoạt chọn và dán ngay lập tức vào ứng dụng trước đó:
- `Alt+1`: Dán clip thứ 1 của cột History (bên trái)
- `Alt+2`: Dán clip thứ 2 của cột History (bên trái)
- `Alt+3`: Dán clip thứ 3 của cột History (bên trái)
- `Alt+4`: Dán clip thứ 1 của cột Pinned (bên phải)
- `Alt+5`: Dán clip thứ 2 của cột Pinned (bên phải)
- `Alt+6`: Dán clip thứ 3 của cột Pinned (bên phải)

*Đặc điểm*:
- Tính toán dựa trên danh sách đang hiển thị thực tế sau khi lọc/tìm kiếm.
- Bỏ qua tiêu đề nhóm, dải ngăn cách và các clip trong nhóm đang thu gọn.
- Hoạt động trơn tru ngay cả khi con trỏ đang nằm trong ô tìm kiếm.
- Nếu cột không đủ số lượng mục, phím tắt an toàn không làm gì (không chuyển cột nhầm).
- Hiển thị nhãn nhỏ `[Alt+1]` ... `[Alt+6]` tinh tế trên từng dòng.

### 1.5. Phím tắt cố định Ctrl+<key> gán riêng cho từng clip
- Chuột phải vào bất kỳ clip nào → chọn **Gán phím tắt (Ctrl+key)...**.
- Hộp thoại nhận diện đúng **một ký tự duy nhất** (0–9, A–Z, tự động chuẩn hóa chữ hoa).
- Phát hiện xung đột thông minh: nếu phím đã được clip khác sử dụng, hiển thị rõ nội dung clip đang giữ và cho phép **Chuyển gán** hoặc **Hủy**.
- Lưu trữ bền vững vào cơ sở dữ liệu (`clip_shortcuts`) theo ID clip. Không bị mất khi tìm kiếm, đổi thứ tự, ghim hoặc sửa nội dung clip. Khi xóa clip, liên kết phím tắt được xóa sạch trong cùng transaction.
- Khi mở clipboard (`Ctrl+Alt+V`), chỉ cần nhấn `Ctrl+1` hoặc `Ctrl+Q` để dán ngay clip tương ứng. Chống paste lặp do giữ phím (auto-repeat).
- Hiển thị nhãn `[Ctrl+K]` ngay trên thẻ clip.

### 1.6. Clip Action: Thực thi chủ động bằng RUN hoặc Ctrl+Enter
- Hỗ trợ gắn hành động cho bất kỳ clip nào:
  1. **File Script (.py, .ps1, ...)**:
     - **Điều kiện**: Bắt buộc phải nhập đường dẫn tuyệt đối (**Full path**).
     - **Môi trường chạy**: Chạy trực tiếp trong chính môi trường của script bằng lệnh:
       ```bash
       uv run <full_path_to_script> [arguments]
       ```
       Thư mục làm việc (CWD) được đặt tại thư mục chứa file script, giúp script tự động nhận diện dependencies PEP 723 hoặc virtualenv bao quanh nó.
  2. **Mã Python trực tiếp (Inline)**: Chạy đoạn code Python cấu hình qua `python -c`.
  3. **Lệnh ngoài (Command + arguments)**: Khởi chạy phần mềm hoặc tiến trình (Chrome, công cụ tự động hóa).
  4. **Mở URL Web**: Nếu clip là đường dẫn web hoặc được cấu hình URL, chuột phải có tùy chọn **Mở URL này** và `Ctrl+Enter` sẽ mở trực tiếp trang web bằng trình duyệt mặc định.
- Hỗ trợ chế độ chạy ngầm (**Background / Detached process**) cho các tiến trình chạy lâu hoặc mở Chrome.
- **An toàn tuyệt đối**: Thao tác copy, paste, preview, tìm kiếm **KHÔNG BAO GIỜ** tự chạy action. Chỉ khi bấm **RUN** trong menu chuột phải hoặc nhấn **Ctrl+Enter** trên clip đó thì action mới được thực thi.

---

## 2. Hướng dẫn cài đặt & Khởi chạy

### Cách 1: Khởi chạy bằng `uv` (Khuyến nghị)
Advance Clipboard tương thích tiêu chuẩn PEP 723 (`# /// script`). Không cần cài đặt thủ công vào môi trường Python toàn cục:
```bash
uv run main.py
```

### Cách 2: Khởi chạy bằng Python truyền thống
Yêu cầu Python >= 3.11:
```bash
pip install PyQt6
python main.py
```

---

## 3. Kiểm thử & Đo đạc hiệu năng

Chạy bộ kiểm thử trong sandbox tạm (DB, ảnh, backup và log), không dùng dữ liệu clipboard thật:
```bash
python -m tests.run_isolated
```

Không dùng `unittest discover` trực tiếp với profile thật: một số fixture cũ gọi storage mặc định. Số test và kết quả hiện tại được ghi trong `docs/REVIEW_REPAIR_VERIFICATION.md`; không coi số 184 của bản cũ là kết quả commit hiện tại.
Bao gồm:
- Toàn bộ 165 test chức năng và lifecycle của baseline cũ.
- `tests/test_alt_quick_paste.py`: Kiểm tra dán nhanh `Alt+1..6`, lọc tìm kiếm, bỏ qua header/nhóm collapse.
- `tests/test_ctrl_shortcuts.py`: Kiểm tra gán phím, phát hiện xung đột, chuyển gán, cascade delete.
- `tests/test_clip_actions.py`: Kiểm tra thực thi inline Python, file script với uv run, mở URL, CWD script.
- `tests/test_focus_f2_safety.py`: Kiểm tra xử lý WM_PASTE trực tiếp trên Edit control và triệt tiêu SC_KEYMENU.

### Benchmark tìm kiếm (25,000 clips SQLite FTS5)
- Tìm từ không tồn tại (missing-token): **0.608 ms** median
- Lọc theo tag (`tag work`): **5.108 ms** median
- Tìm kiếm từ khóa (`proxy`): **16.482 ms** median
- Tìm kiếm cụm từ (`deploy server`): **18.681 ms** median

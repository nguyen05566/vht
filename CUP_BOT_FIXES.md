# cup_bot.py — Báo cáo sửa lỗi (cờ úp / mystery_xiangqi)

Repo: `nguyen05566/vht` · File: `cup_bot.py` · Engine: `PKJQ.exe` (PikaJieQi) chạy qua wine

## Kết quả chạy thật trên gamevh.net (tài khoản nguyen15)

| Chỉ số | Trước khi sửa | Sau khi sửa |
|---|---|---|
| Nước đi server chấp nhận | vài nước đầu rồi hỏng | **105 / 105 (100%)** |
| Server từ chối (`NoPieceAtSourcePosition`) | liên tục tới hết giờ | **0** |
| Engine crash | gần như mỗi lượt | **0** |
| Mất lượt (không ra nước) | nhiều | **0** |
| Kết quả | thua sạch | **thắng 3 / thua 0** |

---

## 1. Lỗi FLIP FEN (lỗi chính được yêu cầu sửa)

**Nguyên nhân:** bot quyết định có lật bàn cờ hay không **dựa vào màu quân của bot**:

```python
do_flip = bot_red          # SAI
```

Nhưng hướng bàn cờ là do **server** quyết định (server luôn vẽ quân mình ở dưới),
không liên quan tới màu bot. Khi hai thứ này lệch nhau, FEN gửi cho engine bị
**soi gương**: engine nhận thế cờ lộn ngược nên trả về nước đi vô nghĩa.

**Cách sửa:** dò hướng thật **từ vị trí TƯỚNG** trong gói `START_MATCH`
(tướng luôn nằm trong cung nhà mình), cộng thêm lớp tự kiểm tra:

- `detect_flip()` — so sánh hàng của tướng đỏ và tướng đen; dự phòng bằng trọng tâm khối quân.
- `sanity_check_fen()` — bắt buộc `K` ở nửa dưới, `k` ở nửa trên; sai thì tự lật lại và dựng lại FEN.
- Gom toàn bộ phép đổi tọa độ về **một nguồn duy nhất** (`pos_to_rc` / `rc_to_pos` / `pos_to_idx` /
  `pos_to_engine_move` / `engine_move_to_pos`), thay vì 4 chỗ tự tính `9 - row` khác nhau như bản cũ.

Đã kiểm tra đủ **4 tổ hợp** (đỏ ở trên/dưới × bot cầm đỏ/đen) — xem `test_cup_flip.py`.

## 2. Bỏ "RAM-learn thay thế nước đi" + MultiPV (theo yêu cầu)

Lớp `TrendAnalyzer` cũ đọc điểm từ các dòng `info` rồi **ghi đè bestmove** của engine:

```python
[RAM-LEARN] 🧠 Thay thế 'xxx' bằng nước đi tối ưu: 'yyy'   # ĐÃ GỠ BỎ
```

Cách này hay lôi ra một nước ở **depth nông** và đè lên kết quả tìm kiếm sâu nhất → bot đi yếu hẳn.

**Nay:** `ENGINE_MULTIPV = 1`, bot **dùng thẳng bestmove** của PKJQ.
`TrendAnalyzer` được thay bằng `MultiPVCollector` — chỉ *ghi nhận* các nhánh theo đúng
thứ tự xếp hạng của engine, và **chỉ dùng khi thật sự cần thiết** (đúng như yêu cầu "nếu cần thiết"):

1. nước tốt nhất xuất phát từ **chốt bị khóa**, hoặc
2. nước đó **đã bị server từ chối** ở lượt hiện tại.

Khi đó mới tạm bật `MultiPV=3`, lấy nước hạng kế tiếp, xong **trả về 1 ngay**.

## 3. Các lỗi nghiêm trọng khác phát hiện khi chạy thật

| # | Lỗi | Nguyên nhân | Cách sửa |
|---|---|---|---|
| 3.1 | Engine crash mỗi lượt | PKJQ **không đọc được FEN có quân úp đã rời ô xuất phát** — nó biến ô đó thành ô trống rồi chết | Nạp `position fen <FEN ĐẦU VÁN> moves ...` để engine tự dựng thế cờ (đúng cách Jieqibox làm) |
| 3.2 | `NoPieceAtSourcePosition` | Đọc **nhầm byte** khi quân úp lật: lấy `sid` (định danh theo ô) thay vì `face` (mặt thật) | Gói MOVE = `src, tgt, count, sid, face` → dùng **`face`**, kèm hậu tố vào nước đi (`e3e4P`) |
| 3.3 | Quân đen bị đọc thành quân đỏ | `list(bytes)` cho 0..255, quên đổi sang **signed** (âm = đen) | `_sid_to_fen_char()` chuyển signed trước khi giải mã |
| 3.4 | Mất lượt dù đang thắng to | Xoá `_latest_bestmove` **sau** khi đã gửi `go` → ghi đè mất câu trả lời (race condition) | Xoá **trước** khi gửi `go`; chờ thêm sau `stop` |
| 3.5 | Mất lượt liên tiếp rồi thua giờ | Theo chuẩn UCI, engine **đang tìm kiếm sẽ bỏ qua lệnh `position`** | Thêm `_sync_engine()`: `stop` → `isready` → chờ `readyok` trước mỗi lần nạp thế cờ |
| 3.6 | "Engine chết" hàng loạt | **Rò rỉ tiến trình**: mỗi lần restart lại bỏ lại một PKJQ.exe → hết RAM → bị OOM kill | `_kill_engine()` dọn tiến trình cũ trước khi khởi động mới; `Hash` 256MB → 64MB |
| 3.7 | Retry sai bàn cờ | Khi engine chết, bản cũ retry bằng **FEN đầu ván, 0 nước đi** → gửi nước của bàn cờ khai cuộc | Retry bằng **đúng ván đang chơi** |
| 3.8 | Lặp vô hạn khi bị từ chối | Tính lại y hệt → ra nước y hệt → loop tới hết giờ | Cấm nước vừa bị từ chối, lấy nước hạng kế tiếp qua MultiPV |
| 3.9 | `sleep(1)` rồi bắn `go` | Nạp NNUE 50MB qua wine có thể lâu hơn 1s | Chờ `readyok` thật (tối đa 20s) |

## 4. Tiện ích môi trường

- Tự tìm `wine64` ở `/usr/lib/wine/` (bản `apt install wine64` của Debian/Ubuntu) và trong `PATH`.
- Tự tạo `XDG_RUNTIME_DIR` (wine headless/CI hay thiếu → spam lỗi).
- Tự tải `pikafish.nnue` nếu chưa có.

## 5. File kiểm thử

| File | Nội dung | Cần mạng/engine? |
|---|---|---|
| `test_cup_flip.py` | 4 tổ hợp flip, round-trip 90 ô, giải mã sid, hậu tố lật quân, BAG | Không |
| `test_cup_engine.py` | Chạy PKJQ thật: nạp FEN cờ úp, MultiPV=1, dịch nước 2 bên | Cần engine |

```bash
python3 test_cup_flip.py     # ✅ TẤT CẢ BÀI TEST FLIP FEN ĐỀU ĐẠT
python3 test_cup_engine.py   # ✅ ENGINE + FLIP + MULTIPV ĐỀU ĐẠT
```

## 6. Cách chạy

```bash
sudo apt-get install -y --no-install-recommends wine64
python3 -c "from unrar.cffi import rarfile; import os
rf=rarfile.RarFile('Jieqibox.rar')
[open(i.filename,'wb').write(rf.open(i).read()) for i in rf.infolist()
 if not i.is_dir() and (os.makedirs(os.path.dirname(i.filename),exist_ok=True) or True)]"
WINEPREFIX=$PWD/.winepfx XDG_RUNTIME_DIR=/tmp/xdg python3 -u cup_bot.py
```

Workflow `.github/workflows/cup_bot.yml` đã bổ sung `XDG_RUNTIME_DIR` và bước chạy `test_cup_flip.py`.

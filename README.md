# vht — Bot Caro (gamevh.net)

Tất cả bot trong repo này đều là **Bot Caro** (cờ caro 15×19) dùng engine
**Embryo Caro6 v1.2.0** (Linux native, tự download khi chạy).

## Thiết lập chung

| Tham số | Giá trị |
|---|---|
| Mức cược | **400 xu** (`BOT_BET_XU = 400`) |
| Đổi avatar | **KHÔNG** (đã bỏ toàn bộ mã `update_random_avatar` / catalog avatar) |
| Đổi FULL_NAME | Có (đổi ngẫu nhiên có dấu chấm làm marker nhận diện đồng đội) |
| Engine | Embryo v1.2.0-c6 (Linux native, tự download từ GitHub) |
| Chuyển xu | Có, định kỳ qua `transfer_xu_bot.py` (mặc định 20% mỗi 9000s về ID `10055407`) |
| Mật khẩu mặc định | `nhat123456` cho mọi tài khoản trong `acc_valid_*.txt` / `acc_zaro_*.txt` |

## Cấu trúc

```
caro_bot.py              # Bot Caro master (toàn bộ logic)
transfer_xu_bot.py       # Module chuyển xu định kỳ (giữ nguyên)
acc_valid_1.txt           # 5000 tài khoản (abul1, abul10, ...)
acc_valid_2.txt           # 5000 tài khoản (abul5753, abul5754, ...)
acc_valid_3.txt           # 5000 tài khoản (nhat10062, nhat10063, ...)
acc_valid_4.txt           # 5000 tài khoản (pro1429, ...)
acc_valid_5.txt           # 5000 tài khoản (test2066, ...)
acc_valid_6.txt           # 5000 tài khoản (test985, ...)
acc_valid_7.txt           # 5000 tài khoản (wxtb16, ...)
acc_valid_8.txt           # 311 tài khoản (wxtby718, ...)
acc_zaro_200.txt          # 200 tài khoản (abul8000, ...)
acc_zaro_335.txt          # 335 tài khoản (abul8000, ...)

arena1.py ... arena20.py  # Wrapper → caro_bot.main() với acc_valid_1.txt[0..19]
nguyen1.py, nguyen4.py,
  nguyen5.py, nguyen6.py,
  nguyen7.py, nguyen13.py,
  nguyen14.py, nguyen15.py,
  nguyen16.py             # Wrapper → caro_bot.main() với acc_valid_2.txt[0..8]
zaro17.py, zaro18.py,
  zaro20.py               # Wrapper → caro_bot.main() với acc_valid_3.txt[0..2]

nguyen_multi.py           # Launcher chạy song song các nguyen*
zaro_multi.py             # Launcher chạy song song các zaro*
register*.py              # Đăng ký tài khoản mới (giữ nguyên)
recover_accounts.py       # Khôi phục tài khoản (giữ nguyên)
```

## Phân bổ tài khoản

| Bot | File tài khoản | Index |
|---|---|---|
| arena1 → arena20 | `acc_valid_1.txt` | 0 → 19 |
| nguyen1, nguyen4, nguyen5, nguyen6, nguyen7, nguyen13, nguyen14, nguyen15, nguyen16 | `acc_valid_2.txt` | 0 → 8 |
| zaro17, zaro18, zaro20 | `acc_valid_3.txt` | 0 → 2 |

## Cách chạy

### Chạy 1 bot đơn lẻ

```bash
# Cài deps
pip install websockets websocket-client requests

# Chạy trực tiếp caro_bot.py (account từ env)
CARO_ACC_FILE=acc_valid_1.txt CARO_ACC_INDEX=0 python3 caro_bot.py

# Hoặc qua wrapper (đã set sẵn env)
python3 arena1.py
python3 nguyen1.py
python3 zaro17.py
```

### Chạy nhiều bot song song

```bash
# Nguyen family
python3 nguyen_multi.py

# Zaro family
python3 zaro_multi.py
```

### Override tài khoản

Mọi wrapper đều dùng `os.environ.setdefault(...)` — runner có thể override:

```bash
# Dùng account khác (vẫn cùng file)
CARO_ACC_INDEX=42 python3 arena1.py

# Override hẳn user/password
CARO_USER=myuser CARO_PWWD=mypassword python3 arena1.py
```

### GitHub Actions

Mỗi bot có 1 workflow `.github/workflows/<tên>.yml`:

- `arena1.yml` ... `arena20.yml` — schedule `0 */6 * * *` (cứ 6h chạy 1 lần, 6h timeout)
- `nguyen1.yml`, `nguyen4.yml`, ... — `workflow_dispatch` (chạy thủ công)
- `zaro17.yml`, `zaro18.yml`, `zaro20.yml` — schedule + dispatch

Workflow sẽ tự:
1. Checkout code
2. Setup Python 3.11
3. `pip install websockets websocket-client requests`
4. Chạy `python3 -u <bot>.py` trong vòng lặp restart cho đến khi hết phiên (6h)

## Cơ chế chuyển xu

`caro_bot.py` gọi `transfer_xu_bot.start_periodic_transfer()` ngay sau khi login
thành công. Cơ chế:

- Lần đầu: chuyển ngay 20% số dư hiện tại về `dest_id=10055407`
- Sau đó: mỗi 9000s (2.5h) chuyển tiếp 20% số dư lúc đó
- Lỗi liên tiếp → tự backoff (tối đa 3x interval)
- Thread daemon → tự chết cùng process khi bot tắt

Override qua env:

```bash
CARO_TRANSFER_PERCENT=30         # chuyển 30% thay vì 20%
CARO_TRANSFER_INTERVAL=3600      # mỗi 1h thay vì 2.5h
```

## Đã bỏ (so với bản cũ)

- `xito_bot.py` — Bot Texas Hold'em (không phải caro)
- `othello.py`, `othello8.py` — Bot Othello (không phải caro)
- Toàn bộ mã đổi avatar trong bot Caro (`_load_avatar_catalog`,
  `update_random_avatar`, `_extract_profile_avatar` chỉ còn đọc để log)
- Bước download Pikafish + NNUE trong các workflow `arena*.yml`/`zaro*.yml`
  (Caro dùng Embryo engine, tự download bởi `caro_bot.py`)
- Các workflow `.yml.txt` cũ (`arena11.yml.txt`, `nguyen-multi.yml.txt`,
  `zaro-multi.yml.txt`) — không dùng nữa

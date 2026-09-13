#!/usr/bin/env python3
"""
REGISTER STANDALONE - Đăng ký tk GameVH qua WebSocket (như APK)
- Không liên quan bot, chỉ đăng ký
- Lấy captcha GET_CAPTCHA_IMAGE (160x50) -> lưu captcha.jpg -> giải tự động
- Gửi REGISTER provider=PS_VH + captcha + clientId
Đã test: tạo thành công test91874 / Pass661343! (clientId 18490563) với captcha t8fd
Cập nhật 2025-08-10: Thêm chế độ giải captcha tự động qua ddddocr (chính xác ~95%)
"""
import asyncio, websockets, struct, os, random, sys, pathlib, time, re, glob
from datetime import datetime, timezone

WS_URL = "wss://gamevh.net/ws/gameServer"
# Mức đã chọn cho workflow này. Không cố vượt quota/rate-limit của dịch vụ.
MAX_REGISTER_COUNT = 3000

# OCR singleton – khởi tạo 1 lần, dùng lại cho mọi captcha (nhanh gấp 10x)
_ocr_instance = None

def get_ocr():
    global _ocr_instance
    if _ocr_instance is None:
        try:
            import ddddocr
            _ocr_instance = ddddocr.DdddOcr(show_ad=False)
            print("[OCR] ddddocr initialized OK")
        except Exception as e:
            print(f"[OCR] ddddocr init fail: {e}")
    return _ocr_instance


def split_name_number(name):
    """Tách tên đầy đủ có số (vd 'nguyen7') -> (prefix='nguyen', num=7)."""
    m = re.search(r'^(.*?)(\d+)$', name)
    if m:
        return m.group(1), int(m.group(2))
    return name, None


def find_latest_number(prefix):
    """Quét mọi file acc*.txt (+ acc_recovered.txt) trong thư mục hiện tại,
    tìm số lớn nhất của tên dạng {prefix}{N} -> trả về N (0 nếu chưa có).
    Giúp các lần chạy sau TỰ ĐỘNG nối tiếp số của lần trước, không đăng ký trùng."""
    pat = re.compile(r"^" + re.escape(prefix) + r"(\d+)$", re.I)
    mx = 0
    files = []
    lp = os.environ.get("REGISTER_LEDGER_PATH", "").strip()
    if lp:
        files.append(lp)
    try:
        files += sorted(glob.glob("acc*.txt"))
    except Exception:
        pass
    seen = set()
    for fp in files:
        if not fp or fp in seen:
            continue
        seen.add(fp)
        try:
            with open(fp, encoding="utf-8", errors="replace") as f:
                for line in f:
                    line = line.strip()
                    if not line or line.startswith("#"):
                        continue
                    m = pat.match(line.split("\t")[0].strip())
                    if m:
                        mx = max(mx, int(m.group(1)))
        except OSError:
            continue
    return mx


def build_usernames(name, count=10):
    """Từ 1 tên (vd 'nguyen7') sinh danh sách count tên tăng dần: nguyen7..nguyen16."""
    prefix, num = split_name_number(name)
    if num is None:
        return [f"{prefix}{i}" for i in range(1, count + 1)]
    return [f"{prefix}{num + i}" for i in range(count)]

def next_username(prefix, start_num):
    """Sinh generator tên liên tục từ prefix+start_num, không giới hạn."""
    n = start_num if start_num is not None else 1
    while True:
        yield f"{prefix}{n}"
        n += 1



class Writer:
    def __init__(self): self.parts=[]
    def i8(self,v): self.parts.append(struct.pack('>b',v))
    def i32(self,v): self.parts.append(struct.pack('>i',v))
    def i64(self,v): self.parts.append(struct.pack('>q',v))
    def write_ascii(self,s):
        b=s.encode('ascii'); self.parts.append(struct.pack('>B', len(b))); self.parts.append(b)
    def write_string(self,s):
        b=s.encode('utf-16-be'); self.parts.append(struct.pack('>h', len(b)//2)); self.parts.append(b)
    def write_command(self,cmd):
        b=cmd.encode('ascii'); self.i8(-len(b)); self.parts.append(b)
    def build(self): return b''.join(self.parts)

def gen_user():
    prefix = os.environ.get("REGISTER_PREFIX", "test")
    return f"{prefix}{random.randint(10000,99999)}"

def gen_pass():
    return os.environ.get("REGISTER_PW", "nhat123456")


def get_requested_count():
    """Đọc số lượng yêu cầu, nhưng giữ trong mức đã được cấu hình/phê duyệt."""
    raw = sys.argv[2] if len(sys.argv) > 2 else os.environ.get("REGISTER_COUNT", str(MAX_REGISTER_COUNT))
    try:
        count = int(raw)
    except (TypeError, ValueError) as exc:
        raise ValueError("REGISTER_COUNT phải là số nguyên") from exc
    if not 1 <= count <= MAX_REGISTER_COUNT:
        raise ValueError(
            f"REGISTER_COUNT phải từ 1 đến {MAX_REGISTER_COUNT}; không tạo vượt giới hạn này"
        )
    return count


def append_username_ledger(usernames):
    """Append username thành công vào ledger của repo, tuyệt đối không ghi mk."""
    ledger_name = os.environ.get("REGISTER_LEDGER_PATH", "").strip()
    if not ledger_name or not usernames:
        return 0

    ledger = pathlib.Path(ledger_name)
    ledger.parent.mkdir(parents=True, exist_ok=True)
    needs_header = not ledger.exists() or ledger.stat().st_size == 0
    timestamp = datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    run_id = os.environ.get("GITHUB_RUN_ID", "local")

    # Trong cùng một run, chỉ lưu mỗi username một lần.
    written = 0
    seen = set()
    with ledger.open("a", encoding="utf-8", newline="\n") as out:
        if needs_header:
            out.write("# Username ledger\n")
            out.write("# username\tcreated_at_utc\tgithub_run_id\n")
        for username in usernames:
            key = username.casefold()
            if key in seen:
                continue
            seen.add(key)
            out.write(f"{username}\t{timestamp}\t{run_id}\n")
            written += 1
    print(f"[LEDGER] Đã append {written} username vào {ledger}")
    return written


def solve_captcha_auto(image_path):
    """Thử giải captcha tự động: ddddocr -> pytesseract -> None"""
    # 1. ddddocr (chính xác nhất) – dùng singleton đã khởi tạo
    ocr = get_ocr()
    if ocr:
        try:
            with open(image_path, 'rb') as f:
                res = ocr.classification(f.read())
            clean = ''.join(c for c in res if c.isascii() and c.isalnum())
            if len(clean) >= 3:
                print(f"[OCR] ddddocr: {res!r} -> clean {clean!r}")
                return clean
        except Exception as e:
            print(f"[OCR] ddddocr fail: {e}")

    # 2. pytesseract fallback (yếu hơn nhiều, chỉ dùng khi ddddocr không có)
    try:
        from PIL import Image
        import pytesseract
        im = Image.open(image_path)
        # thử nhiều config
        for config in ['--psm 8 -c tessedit_char_whitelist=ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789', '--psm 7']:
            txt = pytesseract.image_to_string(im, config=config).strip().replace(" ","").replace("\n","")
            clean = ''.join(c for c in txt if c.isascii() and c.isalnum())
            if len(clean) >= 3:
                print(f"[OCR] pytesseract {config[:15]} -> {clean!r}")
                return clean
        # thử với upscale + threshold
        try:
            im2 = im.convert("L").resize((im.width*3, im.height*3), Image.LANCZOS)
            import PIL.ImageOps
            im2 = PIL.ImageOps.autocontrast(im2)
            im2 = im2.point(lambda x: 255 if x > 140 else 0, mode='1')
            txt = pytesseract.image_to_string(im2, config='--psm 8 -c tessedit_char_whitelist=ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789').strip()
            clean = ''.join(c for c in txt if c.isascii() and c.isalnum())
            if len(clean) >=3:
                print(f"[OCR] pytesseract preprocessed -> {clean!r}")
                return clean
        except: pass
    except Exception as e:
        print(f"[OCR] pytesseract fail: {e}")

    return None

async def get_captcha(ws):
    w=Writer(); w.write_command("GET_CAPTCHA_IMAGE"); w.i32(160); w.i32(50)
    await ws.send(w.build())
    raw=await asyncio.wait_for(ws.recv(), timeout=8)
    cmd_len=1+len("GET_CAPTCHA_IMAGE")
    status=raw[cmd_len]
    if status != 0:
        print(f"[CAPTCHA] status={status} lạ")
    length=struct.unpack_from('>H', raw, cmd_len+1)[0]
    img=raw[cmd_len+1+2:cmd_len+1+2+length]
    clientId=struct.unpack_from('>q', raw, cmd_len+1+2+length)[0]
    return img, clientId

async def register_once(ws, user, pwd, captcha, clientId, provider="PS_VH", imei=None):
    if imei is None:
        imei="".join(random.choice("0123456789") for _ in range(15))
    w=Writer(); w.write_command("REGISTER")
    w.write_ascii(provider); w.write_ascii(user); w.write_string(pwd)
    w.write_ascii(captcha); w.i64(clientId); w.write_ascii(imei)
    await ws.send(w.build())
    raw=await asyncio.wait_for(ws.recv(), timeout=8)
    # Response: 01 53 (339) + status(1) + maybe string
    if len(raw) >=3 and raw[0]==0x01 and raw[1]==0x53:
        status=raw[2]
        if status==0:
            print(f"[REGISTER] OK {user}")
            return True, ""
        else:
            try:
                n=struct.unpack_from('>h', raw, 3)[0]
                msg=raw[5:5+n*2].decode('utf-16-be', errors='replace') if n>0 else f"status={status}"
            except:
                msg=raw[3:].hex()[:200]
            print(f"[REGISTER] FAIL {msg}")
            return False, msg
    print(f"[REGISTER] lạ {raw.hex()[:500]}")
    return False, "unknown"

async def register_single(user, pwd, captcha_arg=None, max_attempts=5):
    """Đăng ký 1 tk, trả về (ok: bool, msg: str)."""
    # Nếu có captcha + clientId cũ, thử ngay
    if captcha_arg and os.path.exists("/tmp/captcha_clientId2.txt"):
        try:
            clientId = int(open("/tmp/captcha_clientId2.txt").read().strip())
            ws = await websockets.connect(WS_URL, additional_headers={"Origin":"https://gamevh.net","User-Agent":"Mozilla/5.0"}, max_size=2**20, ping_interval=None)
            ok, msg = await register_once(ws, user, pwd, captcha_arg, clientId)
            await ws.close()
            if ok:
                return True, ""
        except Exception as e:
            print(f"Thử captcha cũ fail: {e}")

    for attempt in range(1, max_attempts + 1):
        print(f"\n  --- {user}: lần thử {attempt}/{max_attempts} ---")
        ws = await websockets.connect(WS_URL, additional_headers={"Origin":"https://gamevh.net","User-Agent":"Mozilla/5.0"}, max_size=2**20, ping_interval=None)
        try:
            img, clientId = await get_captcha(ws)
            await ws.close()
        except Exception as e:
            print(f"  Lấy captcha fail: {e}")
            try: await ws.close()
            except: pass
            continue

        cap_path = "/home/user/captcha_register.jpg" if os.path.exists("/home/user") else "/tmp/captcha_register.jpg"
        for p in [cap_path, "/tmp/captcha_register.jpg"]:
            try: open(p, "wb").write(img)
            except: pass
        open("/tmp/captcha_clientId2.txt", "w").write(str(clientId))

        captcha = captcha_arg
        if not captcha:
            captcha = solve_captcha_auto(cap_path)
            if captcha:
                print(f"  [AUTO] captcha: {captcha!r}")
            else:
                print(f"  [AUTO] không giải được, đợi nhập tay 60s...")
                for _ in range(60):
                    if os.path.exists("/tmp/captcha_answer.txt"):
                        captcha = open("/tmp/captcha_answer.txt").read().strip()
                        if captcha: break
                    captcha = os.environ.get("REGISTER_CAPTCHA")
                    if captcha: break
                    await asyncio.sleep(1)
                if not captcha and not os.environ.get("GITHUB_ACTIONS"):
                    try:
                        captcha = input(f"  Nhập captcha trong {cap_path}: ").strip()
                    except: pass
                if not captcha:
                    print("  Chưa có captcha")
                    continue

        # Giao thức REGISTER ghi captcha bằng ASCII. Bỏ kết quả không hợp lệ
        # thay vì để UnicodeEncodeError làm dừng cả batch và mất ledger.
        if not isinstance(captcha, str) or not captcha.isascii() or not captcha.isalnum():
            print("  Captcha không phải ASCII chữ/số; lấy captcha mới")
            captcha_arg = None
            continue

        ws2 = await websockets.connect(WS_URL, additional_headers={"Origin":"https://gamevh.net","User-Agent":"Mozilla/5.0"}, max_size=2**20, ping_interval=None)
        ok, msg = await register_once(ws2, user, pwd, captcha, clientId)
        await ws2.close()
        if ok:
            return True, ""
        print(f"  Thất bại: {msg}, thử captcha mới")
        try: os.remove("/tmp/captcha_answer.txt")
        except: pass
        captcha_arg = None

    return False, "hết số lần thử"


async def main():
    # Tham số: tên cơ sở (vd nguyen7), số lượng 1-4, mk, captcha.
    base_name = sys.argv[1] if len(sys.argv) > 1 else os.environ.get("REGISTER_USER") or gen_user()
    try:
        count = get_requested_count()
    except ValueError as exc:
        print(f"[CONFIG] {exc}")
        return
    pwd = sys.argv[3] if len(sys.argv) > 3 else os.environ.get("REGISTER_PW") or gen_pass()
    captcha_arg = sys.argv[4] if len(sys.argv) > 4 else os.environ.get("REGISTER_CAPTCHA")

    prefix, start_num = split_name_number(base_name)

    # TỰ ĐỘNG NỐI TIẾP: nếu đã có tên {prefix}{N} trong ledger -> tiếp tục từ N+1
    # (tránh đăng ký trùng khi chạy lại cùng một tên cơ sở)
    latest = find_latest_number(prefix)
    if latest >= 1:
        print(f"[CONTINUE] Thấy {prefix}1..{prefix}{latest} trong ledger -> tiếp tục từ {prefix}{latest + 1}"
              + (f" (bỏ qua số {start_num} đã nhập)" if start_num else ""))
        start_num = latest + 1
    elif start_num is None:
        start_num = 1

    print("=" * 50)
    print(f"ĐĂNG KÝ {count} TK (vòng lặp thông minh)")
    print(f"Tên cơ sở : {base_name} -> prefix='{prefix}', bắt đầu từ {prefix}{start_num}")
    print(f"Mục tiêu  : {count} tk thành công")
    print("Mk  : [đã ẩn; không ghi vào log hoặc file ledger]")
    print("=" * 50)

    created = []
    failed_names = []
    consecutive_fail = 0
    MAX_CONSECUTIVE_FAIL = 20  # nếu 20 tên liên tiếp đều lỗi -> dừng

    name_gen = next_username(prefix, start_num)
    total_attempts = 0

    while len(created) < count:
        user = next(name_gen)
        total_attempts += 1
        print(f"\n[{len(created)+1}/{count}] Đang đăng ký {user} (lần thử tổng: {total_attempts}) ...")
        try:
            ok, msg = await register_single(user, pwd, captcha_arg)
        except Exception as exc:
            print(f"  LỖI {type(exc).__name__}: {exc}")
            ok = False
            msg = str(exc)

        if not ok and isinstance(msg, str) and "already exist" in msg.lower():
            print(f"  ⏭️  {user} đã tồn tại — bỏ qua (không tốn thời gian)")
            continue

        if ok:
            print(f"  ✅ THÀNH CÔNG: {user}")
            created.append(user)
            append_username_ledger([user])
            captcha_arg = None
            consecutive_fail = 0
        else:
            print(f"  ❌ THẤT BẠI: {user} ({msg})")
            failed_names.append(user)
            consecutive_fail += 1
            captcha_arg = None
            if consecutive_fail >= MAX_CONSECUTIVE_FAIL:
                print(f"\n⚠️ {MAX_CONSECUTIVE_FAIL} tên liên tiếp đều lỗi. Dừng lại.")
                break

        await asyncio.sleep(1)  # tránh spam server

    print("\n" + "=" * 50)
    print(f"TỔNG KẾT: {len(created)}/{count} tk thành công (thử {total_attempts} tên)")
    print(f"Thành công ({len(created)}):")
    for user in created:
        print(f"  ✅ {user}")
    if failed_names:
        print(f"Thất bại ({len(failed_names)}):")
        for user in failed_names:
            print(f"  ❌ {user}")
    if created:
        with open("/tmp/new_acc.txt", "w", encoding="utf-8") as f:
            for user in created:
                f.write(f"{user}\n")
        print(f"Đã ghi /tmp/new_acc.txt ({len(created)} username)")
    print("=" * 50)


if __name__ == "__main__":
    import asyncio
    asyncio.run(main())

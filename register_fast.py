#!/usr/bin/env python3
"""
REGISTER FAST - Đăng ký tk GameVH concurrent, lưu accfast*.txt
===============================================================
- Sinh username hoán vị / ngẫu nhiên chuỗi chữ cái (không theo số thứ tự)
- Mật khẩu chung mặc định: 123
- Phát hiện & cảnh báo ngay nếu GameVH đóng cổng đăng ký (Registration closed)
- 40 luồng song song (asyncio.Semaphore)
- Mỗi 5000 tk -> ghi accfast1.txt, accfast2.txt... + signal commit
"""
import asyncio
import glob
import os
import random
import re
import string
import struct
import sys
import time
import websockets

WS_URL = "wss://gamevh.net/ws/gameServer"
MAX_REGISTER_COUNT = 50000
CHUNK_SIZE = 5000
DEFAULT_CONCURRENCY = 40
CAPTCHA_RETRIES = 3
DEFAULT_PASSWORD = "123"

_ocr_instance = None
def get_ocr():
    global _ocr_instance
    if _ocr_instance is None:
        try:
            import ddddocr
            _ocr_instance = ddddocr.DdddOcr(show_ad=False)
            print("[OCR] ddddocr initialized")
        except Exception as e:
            print(f"[OCR] ddddocr init fail: {e}")
    return _ocr_instance


def solve_captcha(img_bytes):
    ocr = get_ocr()
    if ocr:
        try:
            res = ocr.classification(img_bytes)
            clean = ''.join(c for c in res if c.isascii() and c.isalnum())
            if len(clean) >= 3:
                return clean
        except Exception:
            pass
    return None


# ===== PROTOCOL =====
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


async def get_captcha(ws):
    w=Writer(); w.write_command("GET_CAPTCHA_IMAGE"); w.i32(160); w.i32(50)
    await ws.send(w.build())
    raw=await asyncio.wait_for(ws.recv(), timeout=8)
    cmd_len=1+len("GET_CAPTCHA_IMAGE")
    status=raw[cmd_len]
    length=struct.unpack_from('>H', raw, cmd_len+1)[0]
    img=raw[cmd_len+1+2:cmd_len+1+2+length]
    clientId=struct.unpack_from('>q', raw, cmd_len+1+2+length)[0]
    return img, clientId


async def do_register(ws, user, pwd, captcha, clientId):
    imei="".join(random.choice("0123456789") for _ in range(15))
    w=Writer(); w.write_command("REGISTER")
    w.write_ascii("PS_VH"); w.write_ascii(user); w.write_string(pwd)
    w.write_ascii(captcha); w.i64(clientId); w.write_ascii(imei)
    await ws.send(w.build())
    raw=await asyncio.wait_for(ws.recv(), timeout=8)
    if len(raw) >= 3 and raw[0] == 0x01 and raw[1] == 0x53:
        status = raw[2]
        if status == 0:
            return True, ""
        try:
            n = struct.unpack_from('>h', raw, 3)[0]
            msg = raw[5:5+n*2].decode('utf-16-be', errors='replace') if n > 0 else f"status={status}"
        except:
            msg = raw[3:].hex()[:200]
        return False, msg
    return False, "unknown_response"


# ===== TẠO USERNAME HOÁN VỊ CHỮ CÁI NGẪU NHIÊN =====
def load_existing_usernames():
    existing = set()
    for fp in glob.glob("acc*.txt"):
        try:
            with open(fp, "r", encoding="utf-8", errors="replace") as f:
                for line in f:
                    u = line.strip().split("\t")[0].split(" ")[0].strip().lower()
                    if u and not u.startswith("#"):
                        existing.add(u)
        except Exception:
            continue
    return existing


def generate_letter_usernames(prefix, count, existing_set):
    letters = string.ascii_lowercase
    clean_prefix = re.sub(r'[^a-zA-Z]', '', prefix).lower()
    rand_len = 5 if len(clean_prefix) <= 4 else 4
    if not clean_prefix:
        rand_len = 7

    generated = set()
    while len(generated) < count:
        batch_needed = count - len(generated)
        for _ in range(batch_needed + 1000):
            rand_suffix = ''.join(random.choices(letters, k=rand_len))
            uname = f"{clean_prefix}{rand_suffix}"
            if uname not in existing_set and uname not in generated:
                generated.add(uname)
                if len(generated) >= count:
                    break
    return list(generated)


def find_next_accfast_number():
    mx = 0
    try:
        for fp in glob.glob("accfast*.txt"):
            m = re.search(r'accfast(\d+)\.txt$', fp)
            if m: mx = max(mx, int(m.group(1)))
    except: pass
    return mx + 1


def get_config():
    base_name = (sys.argv[1] if len(sys.argv) > 1
                 else os.environ.get("REGISTER_USER") or "vh")
    raw_count = (sys.argv[2] if len(sys.argv) > 2
                 else os.environ.get("REGISTER_COUNT", str(MAX_REGISTER_COUNT)))
    try: count = int(raw_count)
    except: count = MAX_REGISTER_COUNT
    count = max(1, min(count, MAX_REGISTER_COUNT))
    
    pwd = os.environ.get("REGISTER_PW")
    if not pwd or pwd.strip() == "":
        pwd = DEFAULT_PASSWORD
    else:
        pwd = pwd.strip()

    return base_name, count, pwd


# ===== FILE WRITER =====
class AccFastWriter:
    def __init__(self, chunk_size=CHUNK_SIZE):
        self.chunk_size = chunk_size
        self.file_index = find_next_accfast_number()
        self.buffer = []
        self.files_written = []
        self.lock = asyncio.Lock()

    @property
    def current_filename(self):
        return f"accfast{self.file_index}.txt"

    async def add(self, username):
        async with self.lock:
            self.buffer.append(username)
            if len(self.buffer) >= self.chunk_size:
                await self._flush()

    async def flush_remaining(self):
        async with self.lock:
            if self.buffer:
                await self._flush()

    async def _flush(self):
        if not self.buffer:
            return
        fname = self.current_filename
        with open(fname, "w", encoding="utf-8") as f:
            for u in self.buffer:
                f.write(f"{u}\n")
        total = len(self.files_written) * self.chunk_size + len(self.buffer)
        print(f"\n  💾 Đã ghi {fname} ({len(self.buffer)} tk, tổng đã lưu: {total})")
        self.files_written.append(fname)
        self.buffer.clear()
        self.file_index += 1
        try: open(".commit_ready", "w").close()
        except: pass


# ===== WORKER =====
async def register_one(user, pwd, semaphore, stats, server_closed_flag):
    if server_closed_flag[0]:
        return False, "registration_closed"

    async with semaphore:
        for attempt in range(1, CAPTCHA_RETRIES + 1):
            if server_closed_flag[0]:
                return False, "registration_closed"
            try:
                ws = await websockets.connect(
                    WS_URL,
                    additional_headers={"Origin": "https://gamevh.net", "User-Agent": "Mozilla/5.0"},
                    max_size=2**20, ping_interval=None)
                try:
                    img, clientId = await get_captcha(ws)
                finally:
                    await ws.close()

                captcha = solve_captcha(img)
                if not captcha:
                    stats['captcha_fail'] += 1
                    continue

                ws2 = await websockets.connect(
                    WS_URL,
                    additional_headers={"Origin": "https://gamevh.net", "User-Agent": "Mozilla/5.0"},
                    max_size=2**20, ping_interval=None)
                try:
                    ok, msg = await do_register(ws2, user, pwd, captcha, clientId)
                finally:
                    await ws2.close()

                if ok:
                    return True, ""
                
                if isinstance(msg, str):
                    if "registration closed" in msg.lower() or "đóng" in msg.lower():
                        server_closed_flag[0] = True
                        return False, "registration_closed"
                    if "already exist" in msg.lower():
                        return False, "already_exist"
                
                stats['captcha_wrong'] += 1
                continue
            except asyncio.TimeoutError:
                stats['timeout'] += 1
                continue
            except Exception:
                stats['error'] += 1
                await asyncio.sleep(0.3)
                continue
        return False, "max_retries"


async def worker(queue, pwd, semaphore, stats, writer, target, done_event, server_closed_flag):
    while not done_event.is_set():
        if server_closed_flag[0]:
            break
        try:
            user = queue.get_nowait()
        except asyncio.QueueEmpty:
            break
        ok, msg = await register_one(user, pwd, semaphore, stats, server_closed_flag)
        if ok:
            stats['ok'] += 1
            await writer.add(user)
            if stats['ok'] % 500 == 0:
                print(f"  📊 [{stats['ok']}/{target}] Đã đăng ký thành công...")
        elif msg == "registration_closed":
            stats['closed'] = stats.get('closed', 0) + 1
            break
        elif msg == "already_exist":
            stats['exist'] += 1
        else:
            stats['fail'] += 1


async def main():
    t0 = time.time()
    base_name, count, pwd = get_config()
    concurrency = int(os.environ.get("REGISTER_CONCURRENCY", DEFAULT_CONCURRENCY))
    concurrency = max(5, min(concurrency, 80))

    print("=" * 65)
    print(f"🚀 REGISTER FAST - TẠO {count:,} TÀI KHOẢN HOÁN VỊ CHỮ CÁI")
    print(f"🔑 Mật khẩu chung : '{pwd}'")
    print(f"🔤 Tiền tố cơ sở  : '{base_name}' (Hoán vị chuỗi chữ cái ngẫu nhiên)")
    print(f"⚡ Số luồng        : {concurrency}")
    print(f"📁 Lưu file       : accfast*.txt (mỗi {CHUNK_SIZE} tk/file)")
    print("=" * 65)

    print("[*] Đang đọc danh sách tài khoản hiện có để chống trùng lặp...")
    existing_set = load_existing_usernames()
    print(f"[+] Đã tải {len(existing_set):,} tài khoản cũ.")

    print(f"[*] Đang sinh trước {count:,} username chữ cái ngẫu nhiên/hoán vị...")
    usernames = generate_letter_usernames(base_name, count, existing_set)
    print(f"[+] Mẫu 5 username đầu tiên: {usernames[:5]}")

    queue = asyncio.Queue()
    for u in usernames:
        queue.put_nowait(u)

    semaphore = asyncio.Semaphore(concurrency)
    stats = {'ok': 0, 'fail': 0, 'exist': 0, 'captcha_fail': 0,
             'captcha_wrong': 0, 'timeout': 0, 'error': 0, 'closed': 0}
    writer = AccFastWriter(chunk_size=CHUNK_SIZE)
    done_event = asyncio.Event()
    server_closed_flag = [False]

    workers = [asyncio.create_task(
        worker(queue, pwd, semaphore, stats, writer, count, done_event, server_closed_flag)
    ) for _ in range(concurrency)]

    await asyncio.gather(*workers)
    done_event.set()
    await writer.flush_remaining()

    try: open(".commit_ready", "w").close()
    except: pass
    try: open(".register_done", "w").close()
    except: pass

    elapsed = time.time() - t0
    rate = stats['ok'] / elapsed * 60 if elapsed > 0 else 0
    files = writer.files_written

    print("\n" + "=" * 65)
    if server_closed_flag[0]:
        print("⚠️ [THÔNG BÁO TỪ MÁY CHỦ GAMEVH]")
        print("❌ Cổng đăng ký tài khoản tự do (WebSocket REGISTER) hiện đang bị máy chủ GameVH ĐÓNG!")
        print("   Phản hồi từ Server: 'Registration closed'")
        print("   -> Bot đã tự động dừng lại ngay lập tức để tránh tốn tài nguyên và treo workflow.")
    else:
        print(f"🎉 HOÀN TẤT: {stats['ok']}/{count} tk ({elapsed:.1f}s = {rate:.0f} tk/phút)")
    print(f"  Đã đăng ký : {stats['ok']}")
    print(f"  Đã tồn tại : {stats['exist']}")
    print(f"  Thất bại   : {stats['fail']}")
    print(f"  File đã lưu : {', '.join(files) if files else '(none)'}")
    print("=" * 65)


if __name__ == "__main__":
    asyncio.run(main())

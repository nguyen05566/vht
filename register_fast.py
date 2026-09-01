#!/usr/bin/env python3
"""
REGISTER FAST - Đăng ký tk GameVH concurrent, lưu accfast*.txt
===============================================================
- 40 luồng song song (asyncio.Semaphore)
- Mỗi 5000 tk -> ghi accfast1.txt, accfast2.txt... + signal commit
- Max 50,000 tk / lần chạy
- Tự tìm số accfast tiếp theo (không ghi đè)

File output:
  accfast1.txt  (tk 1-5000)
  accfast2.txt  (tk 5001-10000)
  ...
  .commit_ready  (signal file cho YAML biết có data mới)
"""
import asyncio
import websockets
import struct
import os
import random
import sys
import time
import re
import glob

WS_URL = "wss://gamevh.net/ws/gameServer"
MAX_REGISTER_COUNT = 50000
CHUNK_SIZE = 5000          # mỗi file chứa 5000 tk
DEFAULT_CONCURRENCY = 40
CAPTCHA_RETRIES = 3

# OCR singleton
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
    if len(raw) >=3 and raw[0]==0x01 and raw[1]==0x53:
        status=raw[2]
        if status==0:
            return True, ""
        try:
            n=struct.unpack_from('>h', raw, 3)[0]
            msg=raw[5:5+n*2].decode('utf-16-be', errors='replace') if n>0 else f"status={status}"
        except:
            msg=raw[3:].hex()[:200]
        return False, msg
    return False, "unknown_response"


# ===== NAME UTILS =====
def split_name_number(name):
    m = re.search(r'^(.*?)(\d+)$', name)
    if m: return m.group(1), int(m.group(2))
    return name, None

def find_latest_number(prefix):
    """Tìm số lớn nhất của {prefix}{N} trong mọi acc*.txt."""
    pat = re.compile(r"^" + re.escape(prefix) + r"(\d+)$", re.I)
    mx = 0
    try:
        for fp in sorted(glob.glob("acc*.txt")):
            try:
                with open(fp, encoding="utf-8", errors="replace") as f:
                    for line in f:
                        line = line.strip()
                        if not line or line.startswith("#"): continue
                        m = pat.match(line.split("\t")[0].strip())
                        if m: mx = max(mx, int(m.group(1)))
            except OSError: continue
    except: pass
    return mx

def find_next_accfast_number():
    """Tìm số tiếp theo cho accfast{N}.txt (1-based)."""
    mx = 0
    try:
        for fp in glob.glob("accfast*.txt"):
            m = re.search(r'accfast(\d+)\.txt$', fp)
            if m: mx = max(mx, int(m.group(1)))
    except: pass
    return mx + 1


def get_config():
    base_name = (sys.argv[1] if len(sys.argv) > 1
                 else os.environ.get("REGISTER_USER") or "test")
    raw_count = (sys.argv[2] if len(sys.argv) > 2
                 else os.environ.get("REGISTER_COUNT", str(MAX_REGISTER_COUNT)))
    try: count = int(raw_count)
    except: count = MAX_REGISTER_COUNT
    count = max(1, min(count, MAX_REGISTER_COUNT))
    pwd = os.environ.get("REGISTER_PW", "nhat123456")
    prefix, start_num = split_name_number(base_name)
    latest = find_latest_number(prefix)
    if latest >= 1:
        print(f"[CONTINUE] {prefix}1..{prefix}{latest} đã có -> từ {prefix}{latest+1}")
        start_num = latest + 1
    elif start_num is None:
        start_num = 1
    return base_name, prefix, start_num, count, pwd


# ===== FILE WRITER =====
class AccFastWriter:
    """Ghi accfast{N}.txt mỗi CHUNK_SIZE tk, signal YAML commit."""
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
        # Signal cho YAML background committer
        try:
            open(".commit_ready", "w").close()
        except: pass


# ===== WORKER =====
async def register_one(user, pwd, semaphore, stats):
    async with semaphore:
        for attempt in range(1, CAPTCHA_RETRIES + 1):
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
                if isinstance(msg, str) and "already exist" in msg.lower():
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


async def worker(queue, pwd, semaphore, stats, writer, target, done_event):
    while not done_event.is_set():
        try:
            user = queue.get_nowait()
        except asyncio.QueueEmpty:
            break
        ok, msg = await register_one(user, pwd, semaphore, stats)
        if ok:
            stats['ok'] += 1
            await writer.add(user)
            if stats['ok'] % 500 == 0:
                print(f"  📊 [{stats['ok']}/{target}] ...")
        elif msg == "already_exist":
            stats['exist'] += 1
        else:
            stats['fail'] += 1


async def main():
    t0 = time.time()
    base_name, prefix, start_num, count, pwd = get_config()
    concurrency = int(os.environ.get("REGISTER_CONCURRENCY", DEFAULT_CONCURRENCY))
    concurrency = max(5, min(concurrency, 80))

    print("=" * 60)
    print(f"REGISTER FAST - {count} tk, {concurrency} luồng")
    print(f"Prefix : {prefix}{start_num} -> {prefix}{start_num + count - 1}")
    print(f"File   : accfast*.txt (mỗi {CHUNK_SIZE} tk/file)")
    print(f"Max    : {MAX_REGISTER_COUNT}")
    print("=" * 60)

    queue = asyncio.Queue()
    for i in range(count):
        queue.put_nowait(f"{prefix}{start_num + i}")

    semaphore = asyncio.Semaphore(concurrency)
    stats = {'ok': 0, 'fail': 0, 'exist': 0, 'captcha_fail': 0,
             'captcha_wrong': 0, 'timeout': 0, 'error': 0}
    writer = AccFastWriter(chunk_size=CHUNK_SIZE)
    done_event = asyncio.Event()

    workers = [asyncio.create_task(
        worker(queue, pwd, semaphore, stats, writer, count, done_event)
    ) for _ in range(concurrency)]

    await asyncio.gather(*workers)
    done_event.set()
    await writer.flush_remaining()

    # Signal cuối cùng
    try: open(".commit_ready", "w").close()
    except: pass
    # Tạo .done để YAML biết script đã xong
    try: open(".register_done", "w").close()
    except: pass

    elapsed = time.time() - t0
    rate = stats['ok'] / elapsed * 60 if elapsed > 0 else 0
    files = writer.files_written

    print("\n" + "=" * 60)
    print(f"HOÀN TẤT: {stats['ok']}/{count} tk ({elapsed:.1f}s = {rate:.0f} tk/phút)")
    print(f"  Đã tồn tại : {stats['exist']}")
    print(f"  Thất bại   : {stats['fail']}")
    print(f"  File tạo   : {', '.join(files) if files else '(none)'}")
    print("=" * 60)


if __name__ == "__main__":
    asyncio.run(main())

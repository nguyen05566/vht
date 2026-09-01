#!/usr/bin/env python3
"""
REGISTER FAST - Đăng ký tk GameVH concurrent (10x nhanh hơn register.py)
=============================================================
Khác biệt chính vs register.py:
  - 30-50 luồng đăng ký song song (asyncio.Semaphore)
  - Bỏ sleep 1s giữa các tk
  - Ghi ledger theo lô (mỗi 100 tk) thay vì từng cái
  - Cùng giao thức WebSocket, cùng captcha, cùng output

Cách dùng (giống register.py):
  python register_fast.py              # đọc env vars
  python register_fast.py tenday       # base_name=tenday
  python register_fast.py tenday 500   # base_name + count
"""
import asyncio
import websockets
import struct
import os
import random
import sys
import pathlib
import time
import re
import glob
from datetime import datetime, timezone
from collections import deque

WS_URL = "wss://gamevh.net/ws/gameServer"
MAX_REGISTER_COUNT = 3000

# ===== TUNING =====
DEFAULT_CONCURRENCY = 40   # số luồng đăng ký song song
CAPTCHA_RETRIES = 3        # số lần thử lại nếu sai captcha
LEDGER_FLUSH_EVERY = 100   # ghi ledger mỗi N tk thành công

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
    """Giải captcha từ bytes (không cần ghi file). ddddocr -> None."""
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


# ===== PROTOCOL (same as register.py) =====
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


# ===== NAME UTILS (same as register.py) =====
def split_name_number(name):
    m = re.search(r'^(.*?)(\d+)$', name)
    if m: return m.group(1), int(m.group(2))
    return name, None

def find_latest_number(prefix):
    pat = re.compile(r"^" + re.escape(prefix) + r"(\d+)$", re.I)
    mx = 0
    files = []
    lp = os.environ.get("REGISTER_LEDGER_PATH", "").strip()
    if lp: files.append(lp)
    try: files += sorted(glob.glob("acc*.txt"))
    except: pass
    seen = set()
    for fp in files:
        if not fp or fp in seen: continue
        seen.add(fp)
        try:
            with open(fp, encoding="utf-8", errors="replace") as f:
                for line in f:
                    line = line.strip()
                    if not line or line.startswith("#"): continue
                    m = pat.match(line.split("\t")[0].strip())
                    if m: mx = max(mx, int(m.group(1)))
        except OSError: continue
    return mx


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
        print(f"[CONTINUE] {prefix}1..{prefix}{latest} đã có -> bắt đầu từ {prefix}{latest+1}")
        start_num = latest + 1
    elif start_num is None:
        start_num = 1
    return base_name, prefix, start_num, count, pwd


# ===== FAST REGISTRATION =====
async def register_one(user, pwd, semaphore, stats):
    """Đăng ký 1 tk với semaphore giới hạn concurrent."""
    async with semaphore:
        for attempt in range(1, CAPTCHA_RETRIES + 1):
            try:
                # Mỗi lần thử: mở WS, lấy captcha, giải, đăng ký, đóng WS
                ws = await websockets.connect(
                    WS_URL,
                    additional_headers={
                        "Origin": "https://gamevh.net",
                        "User-Agent": "Mozilla/5.0"
                    },
                    max_size=2**20, ping_interval=None
                )
                try:
                    img, clientId = await get_captcha(ws)
                finally:
                    await ws.close()

                captcha = solve_captcha(img)
                if not captcha:
                    stats['captcha_fail'] += 1
                    continue

                # WS mới cho REGISTER
                ws2 = await websockets.connect(
                    WS_URL,
                    additional_headers={
                        "Origin": "https://gamevh.net",
                        "User-Agent": "Mozilla/5.0"
                    },
                    max_size=2**20, ping_interval=None
                )
                try:
                    ok, msg = await do_register(ws2, user, pwd, captcha, clientId)
                finally:
                    await ws2.close()

                if ok:
                    return True, ""
                
                # Username đã tồn tại -> không thử lại
                if isinstance(msg, str) and "already exist" in msg.lower():
                    return False, "already_exist"
                
                # Sai captcha -> thử lại
                stats['captcha_wrong'] += 1
                continue

            except asyncio.TimeoutError:
                stats['timeout'] += 1
                continue
            except Exception as e:
                stats['error'] += 1
                await asyncio.sleep(0.5)
                continue

        return False, "max_retries"


async def worker(queue, pwd, semaphore, stats, results, done_event):
    """Worker lấy username từ queue, đăng ký, ghi kết quả."""
    while not done_event.is_set():
        try:
            user = queue.get_nowait()
        except asyncio.QueueEmpty:
            break
        
        ok, msg = await register_one(user, pwd, semaphore, stats)
        
        if ok:
            results['created'].append(user)
            stats['ok'] += 1
            print(f"  ✅ [{stats['ok']}/{results['target']}] {user}")
        elif msg == "already_exist":
            stats['exist'] += 1
        else:
            results['failed'].append(user)
            stats['fail'] += 1
            print(f"  ❌ [{stats['ok']}/{results['target']}] {user} ({msg})")


def flush_ledger(usernames, ledger_path):
    """Ghi batch usernames vào ledger."""
    if not usernames or not ledger_path: return
    ledger = pathlib.Path(ledger_path)
    ledger.parent.mkdir(parents=True, exist_ok=True)
    needs_header = not ledger.exists() or ledger.stat().st_size == 0
    ts = datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    run_id = os.environ.get("GITHUB_RUN_ID", "local")
    seen = set()
    with ledger.open("a", encoding="utf-8", newline="\n") as out:
        if needs_header:
            out.write("# Username ledger\n")
            out.write("# username\tcreated_at_utc\tgithub_run_id\n")
        for u in usernames:
            if u.lower() not in seen:
                seen.add(u.lower())
                out.write(f"{u}\t{ts}\t{run_id}\n")


async def main():
    t0 = time.time()
    base_name, prefix, start_num, count, pwd = get_config()
    concurrency = int(os.environ.get("REGISTER_CONCURRENCY", DEFAULT_CONCURRENCY))
    concurrency = max(5, min(concurrency, 80))  # clamp 5-80
    ledger_path = os.environ.get("REGISTER_LEDGER_PATH", "acc.txt").strip() or "acc.txt"

    print("=" * 60)
    print(f"REGISTER FAST - {count} tk, {concurrency} luồng song song")
    print(f"Prefix    : {prefix}, bắt đầu từ {prefix}{start_num}")
    print(f"Ledger    : {ledger_path}")
    print("=" * 60)

    # Tạo queue usernames
    queue = asyncio.Queue()
    for i in range(count):
        queue.put_nowait(f"{prefix}{start_num + i}")

    semaphore = asyncio.Semaphore(concurrency)
    stats = {'ok': 0, 'fail': 0, 'exist': 0, 'captcha_fail': 0,
             'captcha_wrong': 0, 'timeout': 0, 'error': 0}
    results = {'created': [], 'failed': [], 'target': count}
    done_event = asyncio.Event()

    # Ledger flush timer
    ledger_buffer = []
    last_flush = time.time()

    async def ledger_flusher():
        nonlocal ledger_buffer, last_flush
        while not done_event.is_set():
            await asyncio.sleep(5)
            if ledger_buffer and (time.time() - last_flush >= 5 or len(ledger_buffer) >= LEDGER_FLUSH_EVERY):
                batch = ledger_buffer[:]
                ledger_buffer.clear()
                flush_ledger(batch, ledger_path)
                last_flush = time.time()

    # Chạy workers + flusher
    workers = [asyncio.create_task(
        worker(queue, pwd, semaphore, stats, results, done_event)
    ) for _ in range(concurrency)]
    flusher = asyncio.create_task(ledger_flusher())

    await asyncio.gather(*workers)
    done_event.set()
    await flusher

    # Flush cuối
    if ledger_buffer:
        flush_ledger(ledger_buffer, ledger_path)

    elapsed = time.time() - t0
    rate = stats['ok'] / elapsed * 60 if elapsed > 0 else 0

    print("\n" + "=" * 60)
    print(f"TỔNG KẾT: {stats['ok']}/{count} tk thành công ({elapsed:.1f}s = {rate:.0f} tk/phút)")
    print(f"  Đã tồn tại  : {stats['exist']}")
    print(f"  Thất bại    : {stats['fail']} (captcha sai: {stats['captcha_wrong']}, "
          f"không giải: {stats['captcha_fail']}, timeout: {stats['timeout']}, lỗi: {stats['error']})")
    print(f"  Tốc độ      : {rate:.0f} tk/phút ({elapsed/count:.2f}s/tk)")

    # Ghi file tạm danh sách mới
    if results['created']:
        with open("/tmp/new_acc.txt", "w", encoding="utf-8") as f:
            for u in results['created']:
                f.write(f"{u}\n")
        print(f"  /tmp/new_acc.txt: {len(results['created'])} tk")
    print(f"  Ledger: {ledger_path}")
    print("=" * 60)


if __name__ == "__main__":
    asyncio.run(main())

#!/usr/bin/env python3
"""
check_spin_batch.py — Kiểm tra acc sống/chết + spin wheel + transfer xu về đích
================================================================================
Input:  file text, mỗi dòng 1 username (hoặc user:pass)
Output: 
  - acc_alive.txt   — danh sách acc còn sống (login HTTP OK)
  - acc_dead.txt    — danh sách acc chết
  - acc_spin_done.txt — đã spin xong hôm nay (để resume, không chạy lại)
  - spin_report.csv — log chi tiết: user, balance_before, spins, reward, transferred, balance_after

Tính năng:
  - Đa luồng (mặc định 16 worker)
  - Resume: bỏ qua acc đã có trong acc_spin_done.txt
  - HTTP login để kiểm tra sống/chết (không cần WS)
  - Nếu sống → WS login → spin free + video → transfer 100% về DEST_ID
  - Random delay giữa các acc (tránh bị nghi)
  - Progress: in tổng kết mỗi 100 acc

Cách chạy:
  python3 check_spin_batch.py                    # dùng /tmp/all_accs.txt
  python3 check_spin_batch.py acc_file.txt       # file riêng
  python3 check_spin_batch.py acc_file.txt 32    # 32 luồng
"""
import os, sys, struct, time, re, csv, random, threading
import urllib.request, urllib.parse, http.cookiejar
import websocket
from concurrent.futures import ThreadPoolExecutor, as_completed

# ==================== CONFIG ====================
DEFAULT_ACC_FILE = "acc_valid_all.txt"
DEFAULT_PASS = "nhat123456"
DEFAULT_DEST_ID = 10055407  # ban_xu sink
DEFAULT_WORKERS = 16

WS_URL = "wss://gamevh.net/ws/gameServer"
LOGIN_URL = "https://gamevh.net/login.jsp"
# Quan trọng: dùng /play/caro/0 (KHÔNG phải xiangqi) — server gắn token với game page
# caro thì server trả token hợp lệ; xiangqi thì server trả REFRESH liên tục
GAME_URL = "https://gamevh.net/play/caro/0"
PROFILE_URL = "https://gamevh.net/com/ftl/game/profile/player_profile.jsp"

CMD_PING = 301; CMD_PONG = 300; CMD_LOGIN = 302; CMD_ALERT = 303
CMD_TRANSFER = 317; CMD_BALANCE_CHANGED = 431
CMD_GET_REMAIN_SPIN = 426; CMD_SPIN_LUCKY_WHEEL = 425

UA_POOL = [
    "Mozilla/5.0 (Linux; Android 13; Pixel 7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/139.0.0.0 Mobile Safari/537.36",
    "Mozilla/5.0 (Linux; Android 12; SM-S901B) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/139.0.0.0 Mobile Safari/537.36",
    "Mozilla/5.0 (Linux; Android 14; SM-A546B) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/139.0.0.0 Mobile Safari/537.36",
    "Mozilla/5.0 (Linux; Android 13; 22120RN86G) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/139.0.0.0 Mobile Safari/537.36",
    "Mozilla/5.0 (Linux; Android 12; Redmi Note 11) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/139.0.0.0 Mobile Safari/537.36",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/139.0 Safari/537.36",
]

# ==================== OUTPUT FILES ====================
ALIVE_FILE = "/home/z/my-project/vht/acc_alive.txt"
DEAD_FILE = "/home/z/my-project/vht/acc_dead.txt"
DONE_FILE = "/home/z/my-project/vht/acc_spin_done.txt"
REPORT_CSV = "/home/z/my-project/vht/spin_report.csv"

# Thread-safe locks cho file append
_alive_lock = threading.Lock()
_dead_lock = threading.Lock()
_done_lock = threading.Lock()
_csv_lock = threading.Lock()

# Stats
_stats_lock = threading.Lock()
_stats = {
    "total": 0, "alive": 0, "dead": 0, "spun": 0, "reward": 0,
    "transferred": 0, "errors": 0
}


# ==================== PACK HELPERS ====================
def pack_num(cmd, payload=b""): return struct.pack(">H", cmd) + payload
def pack_str(cmd, payload=b""):
    b = cmd.encode("ascii")
    return struct.pack(">b", -len(b)) + b + payload
def asc(s):
    b = s.encode("ascii")[:255]
    return struct.pack(">b", len(b)) + b
def i8(v):  return struct.pack(">b", v)
def i32(v): return struct.pack(">i", v)
def i64(v): return struct.pack(">q", v)


# ==================== PARSE HELPERS ====================
class Reader:
    def __init__(self, data): self.d = data; self.o = 0
    def rem(self): return len(self.d) - self.o
    def i8(self):
        v = struct.unpack_from(">b", self.d, self.o)[0]; self.o += 1; return v
    def u8(self):
        v = self.d[self.o]; self.o += 1; return v
    def i16(self):
        v = struct.unpack_from(">h", self.d, self.o)[0]; self.o += 2; return v
    def i32(self):
        v = struct.unpack_from(">i", self.d, self.o)[0]; self.o += 4; return v
    def i64(self):
        v = struct.unpack_from(">q", self.d, self.o)[0]; self.o += 8; return v
    def ascii(self):
        n = self.u8()
        s = self.d[self.o:self.o+n].decode("ascii", "replace"); self.o += n; return s
    def utf16(self):
        n = self.i16() if self.rem() >= 2 else 0
        s = self.d[self.o:self.o+n*2].decode("utf-16-be", "replace"); self.o += n*2; return s

def parse_frame(data):
    rd = Reader(data); first = rd.i8()
    if first < 0:
        n = -first
        name = data[1:1+n].decode("ascii", "replace"); rd.o = 1 + n
        return name, rd
    second = rd.u8(); cmd_id = (first << 8) | second
    NAMES = {300:"PONG",301:"PING",302:"LOGIN",303:"ALERT",317:"TRANSFER",
             425:"SPIN_LUCKY_WHEEL",426:"GET_REMAIN_SPIN",431:"BALANCE_CHANGED"}
    return NAMES.get(cmd_id, f"CMD_{cmd_id}"), rd


# ==================== HTTP ====================
def get_balance(session):
    """Lấy số dư thực từ trang profile (div.chipBalance).
    GAME_URL chỉ có currentPlayerChipBalance = 0 (session balance),
    phải dùng profile page mới thấy số dư thật."""
    try:
        r = session.get(PROFILE_URL, timeout=12)
        m = re.search(r"""<div\s+class=['"][^'"]*chipBalance[^'"]*['"][^>]*>([^<]+)</div>""", r.text, re.I)
        if m:
            return int(re.sub(r"[^\d]", "", m.group(1)) or 0)
    except Exception: pass
    return -1


def http_login(user, passwd, ua):
    """Trả (ld_dict, session) hoặc (None, None) nếu chết.
    Dùng urllib.request (stdlib) giống banxu_termux — requests lib có vấn đề
    với cookie/session handling trên gamevh.net (token bị server mark REFRESH)."""
    import urllib.request, urllib.parse, http.cookiejar
    try:
        cj = http.cookiejar.CookieJar()
        op = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(cj))
        op.addheaders = [
            ("User-Agent", ua),
            ("Accept-Language", "vi-VN,vi;q=0.9,en;q=0.7"),
        ]
        # GET login.jsp (set cookie)
        op.open(LOGIN_URL, timeout=12).read()
        # POST login — không check URL (login.jsp self-redirects kể cả khi OK)
        data = urllib.parse.urlencode({
            "redirect": "/", "USER_NAME": user, "PASSWORD": passwd,
            "AUTO_LOGIN": "true", "LOGIN": "Đăng nhập"}).encode()
        r = op.open(LOGIN_URL, data=data, timeout=12)
        # GET GAME_URL — token hợp lệ ngay (1 lần là đủ với urllib)
        g = op.open(GAME_URL, timeout=12).read().decode("utf-8", "replace")
        m_tok = re.search(r"var\s+token\s*=\s*(-?\d+)", g)
        m_nick = re.search(r"var\s+currentPlayerNickName\s*=\s*[\"']([^\"']+)[\"']", g)
        if not m_tok or not m_nick:
            return None, None  # acc chết hoặc sai mật khẩu
        cookie = "; ".join(f"{c.name}={c.value}" for c in cj)
        # Wrap opener in a fake session-like object for get_balance()
        class UrllibSession:
            def __init__(self, op): self.op = op
            def get(self, url, timeout=10):
                class Resp:
                    def __init__(self, r): self.text = r.read().decode("utf-8", "replace")
                return Resp(self.op.open(url, timeout=timeout))
        sess = UrllibSession(op)
        # Get real balance from profile page (NOT from GAME_URL which shows 0)
        bal = get_balance(sess)
        return {"cookie": cookie, "nick": m_nick.group(1),
                "token": int(m_tok.group(1)), "balance": bal}, sess
    except Exception as e:
        return None, None


# ==================== WS ====================
def ws_login(cookie, nick, token, timeout=15, user=None, passwd=None):
    """Login WS. Trả ws object hoặc None."""
    try:
        ws = websocket.create_connection(WS_URL, cookie=cookie,
            header={"Origin": "https://gamevh.net"}, timeout=timeout)
    except Exception:
        return None
    try:
        data = asc(nick) + i32(token) + asc("5.0.2") + asc("") + asc("caro") + i8(1)
        ws.send_binary(pack_num(CMD_LOGIN, data))
        deadline = time.time() + timeout
        while time.time() < deadline:
            try:
                raw = ws.recv()
            except websocket.WebSocketTimeoutException:
                break
            except Exception:
                break
            if not raw: continue
            name, rd = parse_frame(raw)
            if name == "PING":
                try: ws.send_binary(pack_num(CMD_PONG))
                except Exception: pass
                continue
            if name == "LOGIN" or name == "CMD_302":
                st = rd.i8()
                if st == 0:
                    return ws
                ws.close()
                return None
    except Exception:
        try: ws.close()
        except Exception: pass
    return None


def ws_query_remain(ws, timeout=10):
    """Query số lượt spin free còn lại."""
    try:
        ws.settimeout(timeout)
        ws.send_binary(pack_str("GET_REMAIN_SPIN"))
    except Exception:
        return None
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            raw = ws.recv()
        except websocket.WebSocketTimeoutException:
            return None
        except Exception:
            return None
        if not raw: continue
        name, rd = parse_frame(raw)
        if name == "PING":
            try: ws.send_binary(pack_num(CMD_PONG))
            except Exception: pass
            continue
        if name == "GET_REMAIN_SPIN" or name == "CMD_426":
            try:
                rd.i8()  # status
                return rd.i32() if rd.rem() >= 4 else 0
            except Exception: return 0
        # Ignore other packets
    return None


def ws_spin(ws, video=False, timeout=10):
    """Quay 1 lượt. video=True gửi type=1 (lượt xem video)."""
    payload = i8(1) if video else b""
    try:
        ws.settimeout(timeout)
        ws.send_binary(pack_str("SPIN_LUCKY_WHEEL", payload))
    except Exception: return None
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            raw = ws.recv()
        except websocket.WebSocketTimeoutException:
            return None
        except Exception:
            return None
        if not raw: continue
        name, rd = parse_frame(raw)
        if name == "PING":
            try: ws.send_binary(pack_num(CMD_PONG))
            except Exception: pass
            continue
        if name == "SPIN_LUCKY_WHEEL" or name == "CMD_425":
            try:
                b = rd.d
                # Check error frame (byte >= 0x80 = string-encoded error)
                if rd.o < len(b) and b[rd.o] >= 0x80:
                    rd.i8()
                    err = rd.utf16()
                    return -1, 0, err, 0
                result = rd.i8()
                if result != 0:
                    err = rd.utf16() if rd.rem() >= 2 else ""
                    return result, 0, err, 0
                slot = rd.u8()
                prize = rd.utf16()
                reward = rd.i32() if rd.rem() >= 4 else 0
                return 0, slot, prize, reward
            except Exception: return None
    return None


def ws_transfer(ws, dest_id, amount, timeout=12):
    try:
        ws.settimeout(timeout)
        ws.send_binary(pack_num(CMD_TRANSFER, i64(dest_id) + i64(amount)))
    except Exception: return False, -1, "send_error"
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            raw = ws.recv()
        except websocket.WebSocketTimeoutException:
            return False, -1, "timeout"
        except Exception:
            return False, -1, "recv_error"
        if not raw: continue
        name, rd = parse_frame(raw)
        if name == "PING":
            try: ws.send_binary(pack_num(CMD_PONG))
            except Exception: pass
            continue
        if name == "BALANCE_CHANGED": continue
        if name == "TRANSFER" or name == "CMD_317":
            st = rd.i8()
            txt = rd.utf16() if rd.rem() > 0 else ""
            return st == 0, st, txt
        if name == "ALERT":
            txt = rd.utf16() if rd.rem() > 0 else ""
    return False, -1, "timeout"


# ==================== FILE I/O ====================
def append_line(path, line, lock):
    with lock:
        with open(path, "a", encoding="utf-8") as f:
            f.write(line + "\n")
            f.flush()


def load_done_set():
    """Đọc acc đã làm xong để resume."""
    if not os.path.exists(DONE_FILE):
        return set()
    with open(DONE_FILE, "r", encoding="utf-8") as f:
        return set(ln.strip() for ln in f if ln.strip())


def write_csv_row(row):
    with _csv_lock:
        with open(REPORT_CSV, "a", encoding="utf-8", newline="") as f:
            w = csv.writer(f)
            w.writerow(row)
            f.flush()


# ==================== MAIN WORKER ====================
def process_one(user, passwd, dest_id):
    """Xử lý 1 acc. Trả về dict kết quả.
    Quan trọng: login HTTP xong phải login WS ngay (token expire nhanh)."""
    result = {
        "user": user, "alive": False, "balance_before": -1,
        "spins": 0, "video_spins": 0, "reward": 0,
        "transferred": 0, "balance_after": -1, "status": "",
    }
    ua = random.choice(UA_POOL)

    # Bước 1: HTTP login (kiểm tra sống/chết)
    ld, session = http_login(user, passwd, ua)
    if not ld:
        result["status"] = "DEAD_LOGIN"
        append_line(DEAD_FILE, user, _dead_lock)
        return result

    result["alive"] = True
    result["balance_before"] = ld["balance"]
    append_line(ALIVE_FILE, user, _alive_lock)

    # Bước 2: WS login ngay (truyền user/passwd để auto re-login nếu REFRESH)
    ws = ws_login(ld["cookie"], ld["nick"], ld["token"], user=user, passwd=passwd)
    if not ws:
        result["status"] = "WS_FAIL_BUT_ALIVE"
        return result

    try:
        # Bước 3: Spin free
        remain = ws_query_remain(ws)
        if remain is None:
            result["status"] = "WS_QUERY_FAIL"
            return result
        if remain > 0:
            for _ in range(remain):
                time.sleep(random.uniform(0.4, 1.0))
                sp = ws_spin(ws, video=False)
                if not sp: break
                rc, slot, prize, reward = sp
                if rc != 0: break
                result["spins"] += 1
                result["reward"] += reward

        # Bước 4: Spin video (type=1) — tới 8 lượt hoặc đến khi NoSpinAvailable
        for _ in range(8):
            time.sleep(random.uniform(0.4, 1.2))
            sp = ws_spin(ws, video=True)
            if not sp: break
            rc, slot, prize, reward = sp
            if rc < 0 or reward <= 0: break
            result["video_spins"] += 1
            result["reward"] += reward

        # Bước 5: Lấy balance mới
        time.sleep(0.5)
        new_bal = get_balance(session)
        if new_bal < 0: new_bal = ld["balance"] + result["reward"]
        result["balance_after"] = new_bal

        # Bước 6: Transfer 100% về dest (giữ lại 0 — acc này chỉ spin, không chơi bot)
        if new_bal > 200:
            ok, st, txt = ws_transfer(ws, dest_id, new_bal)
            if ok:
                result["transferred"] = new_bal
                result["status"] = "OK"
            else:
                # Thử lại với balance ước lượng
                est = ld["balance"] + result["reward"]
                if est > 200 and est != new_bal:
                    ok2, _, _ = ws_transfer(ws, dest_id, est)
                    if ok2:
                        result["transferred"] = est
                        result["status"] = "OK_EST"
                if not result["transferred"]:
                    result["status"] = f"TRANSFER_FAIL_st{st}"
        else:
            result["status"] = "LOW_BALANCE"

    except Exception as e:
        result["status"] = f"ERR:{type(e).__name__}"
    finally:
        try: ws.close()
        except Exception: pass

    return result


def worker(user, passwd, dest_id):
    """Wrapper: process + log + update stats."""
    # Random delay để tránh spike
    time.sleep(random.uniform(0.2, 1.5))
    r = process_one(user, passwd, dest_id)

    # Append to done file (mark processed)
    append_line(DONE_FILE, user, _done_lock)

    # Write CSV row
    write_csv_row([
        r["user"], r["alive"], r["balance_before"],
        r["spins"], r["video_spins"], r["reward"],
        r["transferred"], r["balance_after"], r["status"],
        time.strftime("%Y-%m-%d %H:%M:%S"),
    ])

    # Update stats
    with _stats_lock:
        _stats["total"] += 1
        if r["alive"]: _stats["alive"] += 1
        else: _stats["dead"] += 1
        _stats["spun"] += r["spins"] + r["video_spins"]
        _stats["reward"] += r["reward"]
        _stats["transferred"] += r["transferred"]
        if "ERR" in r["status"] or "FAIL" in r["status"]:
            _stats["errors"] += 1

    # Progress log
    with _stats_lock:
        n = _stats["total"]
        if n % 50 == 0 or n <= 10:
            print(f"[{time.strftime('%H:%M:%S')}] {n} acc processed | "
                  f"alive={_stats['alive']} dead={_stats['dead']} | "
                  f"spins={_stats['spun']} reward={_stats['reward']:,} | "
                  f"transferred={_stats['transferred']:,} xu | "
                  f"errors={_stats['errors']}", flush=True)
    return r


# ==================== MAIN ====================
def main():
    acc_file = sys.argv[1] if len(sys.argv) > 1 else DEFAULT_ACC_FILE
    workers = int(sys.argv[2]) if len(sys.argv) > 2 else DEFAULT_WORKERS
    limit = int(sys.argv[3]) if len(sys.argv) > 3 else 0  # 0 = hết

    print("=" * 70)
    print(f"BATCH CHECK + SPIN + TRANSFER")
    print(f"=" * 70)
    print(f"Account file: {acc_file}")
    print(f"Workers:      {workers}")
    print(f"Limit:        {limit if limit else 'ALL'}")
    print(f"Dest ID:      {DEFAULT_DEST_ID}")
    print(f"Output files:")
    print(f"  - {ALIVE_FILE}    (acc còn sống)")
    print(f"  - {DEAD_FILE}     (acc chết)")
    print(f"  - {DONE_FILE}     (đã xử lý — để resume)")
    print(f"  - {REPORT_CSV}    (CSV log chi tiết)")
    print()

    # Load accounts
    with open(acc_file, "r", encoding="utf-8") as f:
        all_accs = [ln.strip() for ln in f if ln.strip()]
    print(f"Loaded {len(all_accs)} accounts from {acc_file}")

    # Load done set (resume)
    done = load_done_set()
    pending = [u for u in all_accs if u not in done]
    print(f"Already done:  {len(done)}")
    print(f"Pending:       {len(pending)}")
    if limit > 0:
        pending = pending[:limit]
        print(f"Limited to:    {len(pending)}")
    print()

    if not pending:
        print("Không còn acc nào để xử lý. Xóa acc_spin_done.txt để chạy lại.")
        return 0

    # Shuffle để tránh pattern tuần tự
    random.shuffle(pending)

    # Init CSV header
    if not os.path.exists(REPORT_CSV):
        with open(REPORT_CSV, "w", encoding="utf-8", newline="") as f:
            csv.writer(f).writerow([
                "user", "alive", "balance_before",
                "spins", "video_spins", "reward",
                "transferred", "balance_after", "status", "timestamp",
            ])

    # Run
    t_start = time.time()
    try:
        with ThreadPoolExecutor(max_workers=workers) as ex:
            futures = [ex.submit(worker, u, DEFAULT_PASS, DEFAULT_DEST_ID) for u in pending]
            for _ in as_completed(futures):
                pass
    except KeyboardInterrupt:
        print("\n⚠️ Đã dừng theo Ctrl+C — đã lưu progress để resume")
    t_end = time.time()

    # Final summary
    print("\n" + "=" * 70)
    print("TỔNG KẾT")
    print("=" * 70)
    with _stats_lock:
        print(f"  Tổng acc xử lý:    {_stats['total']}")
        print(f"  Acc còn sống:      {_stats['alive']}")
        print(f"  Acc chết:          {_stats['dead']}")
        print(f"  Tổng lượt spin:    {_stats['spun']}")
        print(f"  Tổng reward:       {_stats['reward']:,} xu")
        print(f"  Tổng transferred:  {_stats['transferred']:,} xu")
        print(f"  Errors:            {_stats['errors']}")
    print(f"  Thời gian:         {t_end - t_start:.0f}s")
    print(f"\nFiles:")
    print(f"  - {ALIVE_FILE}")
    print(f"  - {DEAD_FILE}")
    print(f"  - {DONE_FILE}")
    print(f"  - {REPORT_CSV}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

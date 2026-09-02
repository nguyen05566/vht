#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
QUAY VÒNG QUAY MAY MẮN ĐỒNG LOẠT + CHUYỂN TOÀN BỘ X VỀ xxxx (2 PHA)
======================================================================
PHA 1 - QUAY : mỗi acc login HTTP+WS -> GET_REMAIN_SPIN -> quay nếu còn lượt.
PHA 2 - CHUYỂN: mỗi acc login HTTP+WS MỚI -> đọc số dư -> TRANSFER 100% về xxxx.

Vì sao 2 pha (đã kiểm chứng thực tế 2026-08-31):
  - Chuyển x NGAY trong cùng session vừa quay thưởng -> server TỪ CHỐI (100/100 trường hợp).
  - Chuyển ở SESSION MỚI (sau vài phút) -> THÀNH CÔNG (ngan4, ngan5, ngan10 đều OK).
  → Server chặn "quay xong chuyển ngay" (chống rửa). Tách session + giãn cách là đủ.

Ghi chú thêm:
  - xxxx chỉ nhận 90% số chuyển (phí chuyển 10%: 1300 -> +1170, 1000 -> +900).
  - Vòng quay: nhiều ô thưởng (10, 100, 150, 500, 1000 x...), quà cộng thẳng vào dư.
  - Tối thiểu chuyển: > 200 x (server quy định).

Cách chạy:
  python3 spin_and_transfer.py --execute --phase spin --workers 5     # pha 1: quay
  python3 spin_and_transfer.py --execute --phase transfer --workers 5 # pha 2: chuyển
  python3 spin_and_transfer.py --execute --all                         # chạy cả 2 pha tự động
"""
import argparse
import re
import struct
import csv
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

import requests
import websocket

# ===== CẤU HÌNH =====
WS_URL = "wss://gamevh.net/ws/gameServer"
LOGIN_URL = "https://gamevh.net/login.jsp"
GAME_URL = "https://gamevh.net/play/caro/0"        # lấy token (giống transfer_xu_bot)
PROFILE_URL = "https://gamevh.net/com/ftl/game/profile/player_profile.jsp"
VERSION = "5.0.2"
GAME_ID = "caro"

CMD_TRANSFER = 317      # 0x013D
CMD_LOGIN = 302         # 0x012E
CMD_PONG = 300          # 0x012C
CMD_PING = 301
CMD_ALERT = 303
CMD_BALANCE_CHANGED = 431

MIN_TRANSFER = 200      # server: chuyển tối thiểu > 200 x
DEST_ID = 65692738      # xxxx
DEST_NAME = "xxxx"
RETRY_DELAY = 60        # nếu transfer bị từ chối -> chờ rồi thử lại session mới

UA = ("Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/139.0 Safari/537.36")

# ==================== FRAMING ====================
def pack_num(cmd, payload=b""):   return struct.pack(">H", cmd) + payload
def pack_str(cmd, payload=b""):
    b = cmd.encode("ascii")
    return bytes([(-len(b)) & 0xFF]) + b + payload
def i64(v): return struct.pack(">q", v)
def i32(v): return struct.pack(">i", v)
def i16(v): return struct.pack(">h", v)
def i8(v):  return struct.pack(">b", v)
def asc(s):
    e = s.encode("ascii", "replace")[:255]
    return i8(len(e)) + e
def utf(s):
    e = s.encode("utf-16-be")
    return i16(len(e) // 2) + e


class Reader:
    def __init__(self, d):
        self.d, self.p = bytes(d), 0
    def rem(self): return len(self.d) - self.p
    def u8(self):
        v = self.d[self.p] if self.p < len(self.d) else 0; self.p += 1; return v
    def i8(self):
        v = struct.unpack_from(">b", self.d, self.p)[0] if self.p < len(self.d) else 0
        self.p += 1; return v
    def i16(self):
        v = struct.unpack_from(">h", self.d, self.p)[0] if self.p + 2 <= len(self.d) else 0
        self.p += 2; return v
    def i32(self):
        v = struct.unpack_from(">i", self.d, self.p)[0] if self.p + 4 <= len(self.d) else 0
        self.p += 4; return v
    def i64(self):
        v = struct.unpack_from(">q", self.d, self.p)[0] if self.p + 8 <= len(self.d) else 0
        self.p += 8; return v
    def utf16(self):
        n = self.i16()
        if n <= 0: return ""
        e = min(n * 2, self.rem())
        s = self.d[self.p:self.p + e].decode("utf-16-be", "replace"); self.p += e
        return s

def parse_frame(raw):
    if not raw: return None, None
    first = struct.unpack_from(">b", raw, 0)[0]
    if first < 0:
        n = -first
        return raw[1:1 + n].decode("ascii", "replace"), Reader(raw[1 + n:])
    return (first << 8) | raw[1], Reader(raw[2:])


# ==================== HTTP ====================
def http_login(user, passwd):
    sess = requests.Session()
    sess.headers.update({"User-Agent": UA, "Accept-Language": "vi-VN,vi;q=0.9"})
    try:
        sess.get(LOGIN_URL, timeout=15)
        r = sess.post(LOGIN_URL, timeout=15,
                      data={"redirect": "/", "USER_NAME": user, "PASSWORD": passwd,
                            "AUTO_LOGIN": "true", "LOGIN": "Đăng nhập"},
                      headers={"Origin": "https://gamevh.net", "Referer": LOGIN_URL},
                      allow_redirects=True)
        if "login.jsp" in r.url:
            return None
        g = sess.get(GAME_URL, timeout=15)
        tm = re.search(r"var\s+token\s*=\s*(-?\d+)", g.text)
        token = int(tm.group(1)) if tm else 0
        if not token:
            return None
        mm = re.search(r"var\s+currentPlayerNickName\s*=\s*[\"']([^\"']+)[\"']", g.text)
        nick = mm.group(1).strip() if mm else user
        cookie = "; ".join(f"{k}={v}" for k, v in sess.cookies.items())
        return {"token": token, "cookie": cookie, "nick": nick, "session": sess,
                "balance": get_balance(sess)}
    except Exception:
        return None

def get_balance(sess):
    try:
        prof = sess.get(PROFILE_URL, timeout=15)
        m = re.search(r'(?is)<div\s+class=["\'][^"\']*chipBalance[^"\']*["\'][^>]*>(.*?)</div>',
                      prof.text)
        if m:
            return int(re.sub(r"[^\d]", "", m.group(1)) or 0)
    except Exception:
        pass
    return 0

def get_public_balance(sess, pid):
    try:
        prof = sess.get(f"{PROFILE_URL}?playerId={pid}", timeout=15)
        m = re.search(r'(?is)<div\s+class=["\'][^"\']*chipBalance[^"\']*["\'][^>]*>(.*?)</div>',
                      prof.text)
        return int(re.sub(r"[^\d]", "", m.group(1)) or 0) if m else None
    except Exception:
        return None


# ==================== WS SESSION ====================
def ws_login(cookie, nick, token, log):
    """Kết nối WS + login. Trả ws hoặc None."""
    ws = websocket.create_connection(
        WS_URL, timeout=12,
        header=[f"Cookie: {cookie}", "Origin: https://gamevh.net", f"User-Agent: {UA}"],
        cookie=cookie)
    ws.send_binary(pack_num(CMD_LOGIN, asc(nick) + i32(token)
                            + asc(VERSION) + asc("") + asc(GAME_ID) + i8(1)))
    deadline = time.time() + 8
    while time.time() < deadline:
        raw = ws.recv()
        if not raw: continue
        name, rd = parse_frame(raw)
        if name == CMD_PING or name == "PING":
            ws.send_binary(pack_num(CMD_PONG)); continue
        if name == CMD_LOGIN or name == "LOGIN":
            st = rd.i8()
            if st == 0:
                return ws
            path = rd.utf16() if rd.rem() > 0 else ""
            log(f"    login st={st} path={path!r}")
            break
    try: ws.close()
    except Exception: pass
    return None

def ws_query_remain(ws, log, timeout=8):
    """Gửi GET_REMAIN_SPIN -> trả remain (int) hoặc None."""
    ws.send_binary(pack_str("GET_REMAIN_SPIN"))
    deadline = time.time() + timeout
    while time.time() < deadline:
        raw = ws.recv()
        if not raw: continue
        name, rd = parse_frame(raw)
        if name == CMD_PING or name == "PING":
            ws.send_binary(pack_num(CMD_PONG)); continue
        if name == "GET_REMAIN_SPIN":
            rd.i8()             # status
            return rd.i32()
        if name in ("BROADCAST", "ENTER_PLACE", "CONFIG", "SET_CLIENT_MODE", CMD_ALERT):
            continue
    return None

def ws_spin(ws, log, timeout=10):
    """Quay 1 lượt. Trả (result, slot, prize, reward) hoặc None."""
    ws.send_binary(pack_str("SPIN_LUCKY_WHEEL"))
    deadline = time.time() + timeout
    while time.time() < deadline:
        raw = ws.recv()
        if not raw: continue
        name, rd = parse_frame(raw)
        if name == CMD_PING or name == "PING":
            ws.send_binary(pack_num(CMD_PONG)); continue
        if name == "SPIN_LUCKY_WHEEL":
            result = rd.i8()
            slot = rd.u8()
            prize = rd.utf16()
            reward = rd.i32() if rd.rem() >= 4 else 0
            return result, slot, prize, reward
        if name in ("BROADCAST", "ENTER_PLACE", "CONFIG", CMD_ALERT):
            continue
    return None

def ws_transfer(ws, log, dest_id, amount, timeout=12):
    """Gửi TRANSFER. Trả (ok, status, text) — ok=True nếu server chấp nhận."""
    ws.send_binary(pack_num(CMD_TRANSFER, i64(dest_id) + i64(amount)))
    deadline = time.time() + timeout
    while time.time() < deadline:
        raw = ws.recv()
        if not raw: continue
        name, rd = parse_frame(raw)
        if name == CMD_PING or name == "PING":
            ws.send_binary(pack_num(CMD_PONG)); continue
        if name == CMD_BALANCE_CHANGED or name == "BALANCE_CHANGED":
            return True, 0, "BALANCE_CHANGED"
        if name == CMD_TRANSFER or name == "TRANSFER":
            st = rd.i8()
            txt = rd.utf16() if rd.rem() > 0 else ""
            return st == 0, st, txt
        if name == CMD_ALERT or name == "ALERT":
            txt = rd.utf16() if rd.rem() > 0 else ""
            log(f"    ⚠️ ALERT: {txt}")
    return False, -1, "timeout"


# ==================== PHA 1: QUAY ====================
def phase_spin(user, passwd, dest_id, log):
    t0 = time.time()
    res = {"user": user, "status": "?", "remain": None, "reward": 0, "prize": "",
           "balance_after": 0, "ms": 0, "note": ""}
    ld = http_login(user, passwd)
    if not ld:
        res["status"] = "LOGIN_FAIL"; res["ms"] = int((time.time()-t0)*1000)
        return res
    ws = None
    try:
        ws = ws_login(ld["cookie"], ld["nick"], ld["token"], log)
        if not ws:
            res["status"] = "WS_LOGIN_FAIL"; res["ms"] = int((time.time()-t0)*1000)
            return res
        remain = ws_query_remain(ws, log)
        res["remain"] = remain
        if remain and remain > 0:
            time.sleep(0.4)
            spin = ws_spin(ws, log)
            if spin:
                rc, slot, prize, reward = spin
                res["reward"] = reward
                res["prize"] = prize
                log(f"    🎰 quay: result={rc} slot={slot} | {prize} ({reward} x)")
                if rc != 0:
                    res["note"] = f"spin_rejected({rc})"
                res["status"] = "SPUN"
            else:
                res["status"] = "SPIN_NO_RESP"
        else:
            res["status"] = "NO_SPIN"
            res["note"] = "het_luot_bo_qua"
            log(f"    ⏭️  hết lượt quay, bỏ qua")
    except Exception as e:
        res["status"] = "ERR:" + type(e).__name__; res["note"] = str(e)[:120]
    finally:
        if ws:
            try: ws.close()
            except Exception: pass
        res["ms"] = int((time.time()-t0)*1000)
        log(f"  -> PHA1 {res['status']} | remain={res['remain']} reward={res['reward']} "
            f"{res['prize']} | {res['ms']}ms")
    return res


# ==================== PHA 2: CHUYỂN ====================
def phase_transfer(user, passwd, dest_id, log, attempt=1):
    t0 = time.time()
    res = {"user": user, "status": "?", "balance": 0, "transferred": 0,
           "ms": 0, "note": ""}
    ld = http_login(user, passwd)
    if not ld:
        res["status"] = "LOGIN_FAIL"; res["ms"] = int((time.time()-t0)*1000)
        return res, None
    balance = ld["balance"]
    res["balance"] = balance
    if balance <= MIN_TRANSFER:
        res["status"] = "BALANCE_TOO_LOW"
        res["note"] = f"balance={balance}"
        log(f"    ⏭️  số dư {balance} <= {MIN_TRANSFER}, bỏ qua")
        res["ms"] = int((time.time()-t0)*1000)
        return res, None
    ws = None
    try:
        ws = ws_login(ld["cookie"], ld["nick"], ld["token"], log)
        if not ws:
            res["status"] = "WS_LOGIN_FAIL"; res["ms"] = int((time.time()-t0)*1000)
            return res, None
        ok, st, txt = ws_transfer(ws, log, dest_id, balance)
        if ok:
            res["transferred"] = balance
            res["status"] = "OK"
            log(f"    ✅ TRANSFER {balance:,} x -> {DEST_NAME}({dest_id}) | {txt}")
        else:
            res["status"] = "REJECTED"
            res["note"] = f"st={st}: {txt[:100]}"
            log(f"    ❌ TRANSFER bị từ chối (st={st}): {txt if isinstance(txt,str) else txt}")
            # thử lại 1 lần trong session mới nếu là lần đầu
            if attempt == 1:
                log(f"    🔁 chờ {RETRY_DELAY}s rồi thử lại lần 2 (session mới)...")
                time.sleep(RETRY_DELAY)
                res2, _ = phase_transfer(user, passwd, dest_id, log, attempt=2)
                res["status"] = res2["status"]
                res["transferred"] = res2["transferred"]
                res["note"] += f" | retry: {res2['status']} {res2['note']}"
                res["ms"] = int((time.time()-t0)*1000)
                return res, None
    except Exception as e:
        res["status"] = "ERR:" + type(e).__name__; res["note"] = str(e)[:120]
    finally:
        if ws:
            try: ws.close()
            except Exception: pass
        res["ms"] = int((time.time()-t0)*1000)
    log(f"  -> PHA2 {res['status']} | bal={balance:,} chuyển={res['transferred']:,} "
        f"| {res['ms']}ms {res['note'][:80]}")
    return res, None


# ==================== MAIN ====================
def load_users(args):
    if args.user:
        return [args.user]
    if args.range:
        # --range "test:1:3000" hoặc --range "test 1 3000"
        parts = args.range.replace(",", " ").split()
        prefix = parts[0]
        start, end = int(parts[1]), int(parts[2])
        excl = {int(x) for x in args.exclude.split(",") if x.strip().isdigit()}
        users = [f"{prefix}{i}" for i in range(start, end + 1) if i not in excl]
        print(f"🔍 Sinh {len(users)} ứng viên từ dải {prefix}{start}..{prefix}{end}"
              f"{' (bỏ ' + str(sorted(excl)) + ')' if excl else ''}")
        return users
    # --list được chỉ định, hoặc TỰ TÌM file danh sách có sẵn trong thư mục hiện tại
    import os as _os, glob as _glob
    candidates = [f.strip() for f in args.list.split(",")] if args.list else []
    # Nếu TẤT CẢ file đều không tồn tại → fallback glob
    if candidates and not any(_os.path.exists(c) for c in candidates):
        print(f"⚠️  Không file nào trong '{args.list}' tồn tại, thử tìm acc*.txt...")
        candidates = []
    if not candidates:
        candidates = (sorted(_glob.glob("acc*.txt"))
                      + ["acc_all.txt", "acc_valid.txt", "acc_test.txt"])
        candidates = list(dict.fromkeys(candidates))
    seen = set()
    users = []
    for c in candidates:
        if c and _os.path.exists(c):
            with open(c, encoding="utf-8") as f:
                file_users = [ln.strip().split("\t")[0] for ln in f
                              if ln.strip() and not ln.startswith("#")]
                for u in file_users:
                    if u not in seen:
                        seen.add(u)
                        users.append(u)
                print(f"📄 {c}: {len(file_users)} tk")
    if users:
        print(f"📋 Tổng cộng: {len(users)} tk")
        return users
    print("❗ Không tìm thấy file danh sách tk nào (acc*.txt) trong thư mục hiện tại!")
    print("   Cách dùng:")
    print("   • 1 file:        python3 spin_and_transfer.py --list acc.txt --execute")
    print("   • Nhiều file:    python3 spin_and_transfer.py --list \"accfast1.txt,accfast2.txt\" --execute")
    print("   • Tự sinh dải:   python3 spin_and_transfer.py --range \"test 1 5138\" --execute")
    print("   • Chạy 1 tk:     python3 spin_and_transfer.py --user test50 --execute")
    sys.exit(1)

def prune_invalid(users, pwd, log):
    """Kiểm tra đăng nhập HTTP nhanh, loại bỏ tk không hợp lệ."""
    valid, invalid = [], []
    with ThreadPoolExecutor(max_workers=min(30, len(users) or 1)) as ex:
        futs = {ex.submit(http_login, u, pwd): u for u in users}
        for f in as_completed(futs):
            u = futs[f]
            r = f.result()
            if r:
                valid.append(u)
            else:
                invalid.append(u)
    log(f"🔍 Lọc hợp lệ: {len(valid)} OK / {len(invalid)} KHÔNG hợp lệ (bỏ qua)")
    if invalid:
        with open("acc_invalid.txt", "w") as f:
            f.write("\n".join(invalid) + "\n")
        log(f"💾 Danh sách KHÔNG hợp lệ: acc_invalid.txt")
    return valid

def load_done_set(path="phase2_transfer.csv"):
    """Đọc file CSV kết quả để biết acc đã xử lý xong (OK / BALANCE_TOO_LOW)."""
    done = set()
    try:
        with open(path, encoding="utf-8") as f:
            for row in csv.DictReader(f):
                if row.get("status") in ("OK", "BALANCE_TOO_LOW"):
                    done.add(row["acc"])
    except FileNotFoundError:
        pass
    return done


def write_csv_append(path, header, rows, append=False):
    """Ghi CSV: append vào file cũ (nếu có) hoặc tạo mới kèm header."""
    import os
    new_file = not os.path.exists(path) or os.path.getsize(path) == 0
    mode = "a" if (append and not new_file) else "w"
    with open(path, mode, encoding="utf-8") as fp:
        if new_file:
            fp.write(header + "\n")
        for r in rows:
            fp.write(r + "\n")


def main():
    ap = argparse.ArgumentParser(description="Quay vòng quay + chuyển toàn bộ x về xxxx (hỗ trợ vài ngàn acc)")
    ap.add_argument("--list", default=None, help="file danh sách acc")
    ap.add_argument("--range", default=None,
                    help='tự sinh dải tên+số, vd: --range "test 1 3000" hoặc "test:1:3000"')
    ap.add_argument("--exclude", default="", help="số cần loại khỏi --range, vd: 1,25")
    ap.add_argument("--user", default=None)
    ap.add_argument("--password", "--pwd", default="")
    ap.add_argument("--dest", type=int, default=DEST_ID)
    ap.add_argument("--max", type=int, default=0, help="giới hạn số acc (0 = hết)")
    ap.add_argument("--workers", type=int, default=30)
    ap.add_argument("--batch-size", type=int, default=500,
                    help="số acc mỗi lô (default 500)")
    ap.add_argument("--batch-pause", type=int, default=3,
                    help="nghỉ giữa các lô (giây)")
    ap.add_argument("--phase", choices=["spin", "transfer", "all"], default="all")
    ap.add_argument("--phase-gap", type=int, default=15,
                    help="nghỉ giữa pha quay và pha chuyển (giây)")
    ap.add_argument("--pipeline", action="store_true",
                    help="pipeline: quay lô sau song song với chuyển lô trước (gấp 2x nhanh)")
    ap.add_argument("--prune", action="store_true",
                    help="tự kiểm tra & lọc bỏ tk không đăng nhập được trước khi chạy")
    ap.add_argument("--skip-done", action="store_true",
                    help="bỏ qua acc đã OK/BALANCE_TOO_LOW trong done file (mặc định phase2_transfer.csv)")
    ap.add_argument("--done-file", default="phase2_transfer.csv",
                    help="file CSV dùng để tra cứu acc đã xử lý (--skip-done)")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--execute", action="store_true")
    ap.add_argument("--append", action="store_true",
                    help="ghi thêm vào CSV cũ thay vì ghi đè (hợp khi chạy nhiều lô/lần)")
    args = ap.parse_args()
    execute = args.execute and not args.dry_run
    if not execute:
        print("⚠️  CHẾ ĐỘ DRY-RUN. Dùng --execute để quay/chuyển thật!\n")

    lock = threading.Lock()
    def log(msg):
        with lock:
            print(msg, flush=True)

    users = load_users(args)
    if args.max and args.max > 0:
        users = users[:args.max]
    if args.skip_done:
        done = load_done_set(args.done_file)
        skip = [u for u in users if u in done]
        if skip:
            log(f"⏭️  Bỏ qua {len(skip)} acc đã xử lý xong (--skip-done)")
            users = [u for u in users if u not in done]
    if args.prune:
        users = prune_invalid(users, args.password, log)
    if not users:
        print("🤷 Không còn acc nào để chạy.")
        return

    # balance đích trước
    sess0 = requests.Session()
    sess0.headers.update({"User-Agent": UA})
    try:
        sess0.get(LOGIN_URL, timeout=15)
        sess0.post(LOGIN_URL, timeout=15,
                   data={"redirect": "/", "USER_NAME": users[0], "PASSWORD": args.password,
                         "AUTO_LOGIN": "true", "LOGIN": "Đăng nhập"},
                   headers={"Origin": "https://gamevh.net", "Referer": LOGIN_URL},
                   allow_redirects=True)
    except Exception:
        pass
    bal0 = get_public_balance(sess0, args.dest)
    print(f"🎯  Đích: {DEST_NAME} (id={args.dest}) | balance trước: "
          f"{bal0:,} x" if bal0 else f"🎯  Đích: {DEST_NAME} (id={args.dest})")
    print(f"👥  {len(users)} acc | {args.workers} luồng | lô {args.batch_size} acc"
          f" + nghỉ {args.batch_pause}s\n")

    t_start = time.time()

    def run_phase(fn, chunk):
        results = []
        with ThreadPoolExecutor(max_workers=args.workers) as ex:
            futs = [ex.submit(fn, u, args.password, args.dest, log) for u in chunk]
            for f in as_completed(futs):
                r = f.result()
                results.append(r[0] if isinstance(r, tuple) else r)
        return results

    # ===== CHIA LÔ =====
    batches = [users[i:i + args.batch_size] for i in range(0, len(users), args.batch_size)]
    log(f"📦 Tổng {len(batches)} lô, mỗi lô tối đa {args.batch_size} acc")
    if args.pipeline:
        log(f"🚀 PIPELINE MODE — quay & chuyển song song lô khác nhau")
    log(f"👷 {args.workers} luồng | nghỉ lô {args.batch_pause}s | phase_gap {args.phase_gap}s\n")

    all_spin, all_trans = [], []

    if args.pipeline and args.phase == "all" and execute:
        # ===== PIPELINE: quay lô N+1 song song với chuyển lô N =====
        from queue import Queue as _Queue
        spin_q = _Queue()  # (batch_index, chunk)
        for i, chunk in enumerate(batches):
            spin_q.put((i, chunk))

        spin_results = {}  # batch_index -> results
        trans_results = {}
        spin_done = threading.Event()
        spin_lock = threading.Lock()
        trans_lock = threading.Lock()
        next_transfer = [0]  # mutable counter

        def spin_worker_loop():
            """Lay lo tu queue, quay, day ket qua."""
            while True:
                try:
                    bi, chunk = spin_q.get_nowait()
                except Exception:
                    break
                log(f"\n{'='*70}\n🔶 LÔ {bi+1}/{len(batches)} — QUAY {len(chunk)} acc\n{'='*70}")
                results = run_phase(phase_spin, chunk)
                with spin_lock:
                    spin_results[bi] = results
                    s = [r for r in results if r["status"] == "SPUN"]
                log(f"✅ lô {bi+1}: quay xong ({len(s)} quay được)")
                spin_q.task_done()
            spin_done.set()

        def transfer_worker_loop():
            """Chờ lô quay xong -> sleep gap -> chuyển."""
            nonlocal all_trans
            while next_transfer[0] < len(batches):
                bi = next_transfer[0]
                # Chờ lô này quay xong
                while bi not in spin_results:
                    if spin_done.is_set() and bi not in spin_results:
                        return
                    time.sleep(1)
                chunk = batches[bi]
                # Nghỉ gap giữa quay và chuyển
                log(f"⏳ Lô {bi+1}: nghỉ {args.phase_gap}s trước khi chuyển...")
                time.sleep(args.phase_gap)
                log(f"\n{'='*70}\n💎 LÔ {bi+1}/{len(batches)} — CHUYỂN {len(chunk)} acc\n{'='*70}")
                results = run_phase(phase_transfer, chunk)
                with trans_lock:
                    trans_results[bi] = results
                    all_trans.extend(results)
                    okb = [r for r in results if r["status"] == "OK"]
                log(f"✅ lô {bi+1}: chuyển xong ({len(okb)} OK)")
                next_transfer[0] += 1

        # Chạy 2 thread song song
        t_spin = threading.Thread(target=spin_worker_loop, daemon=True)
        t_trans = threading.Thread(target=transfer_worker_loop, daemon=True)
        t_spin.start()
        time.sleep(2)  # để spin thread bắt đầu trước
        t_trans.start()
        t_spin.join()
        t_trans.join()

        # Gộp kết quả spin
        for bi in sorted(spin_results.keys()):
            all_spin.extend(spin_results[bi])

    else:
        # ===== CHẾ ĐỘ THƯỜNG (tuần tự) =====
        for bi, chunk in enumerate(batches, 1):
            log(f"\n{'='*70}\n🔶 LÔ {bi}/{len(batches)} — {len(chunk)} acc\n{'='*70}")
            if args.phase in ("spin", "all") and execute:
                log("PHA 1 — QUAY...")
                all_spin += run_phase(phase_spin, chunk)
                s = [r for r in all_spin if r["user"] in [c for c in chunk] and r["status"] == "SPUN"]
                log(f"✅ lô {bi}: quay xong ({len(s)} quay được)")
            if args.phase in ("transfer", "all"):
                if args.phase == "all" and execute and len(chunk) > 1:
                    log(f"⏳ Nghỉ {args.phase_gap}s giữa quay và chuyển...")
                    time.sleep(args.phase_gap)
                if execute:
                    log("PHA 2 — CHUYỂN...")
                    all_trans += run_phase(phase_transfer, chunk)
                    okb = [r for r in all_trans if r["user"] in [c for c in chunk] and r["status"] == "OK"]
                    log(f"✅ lô {bi}: chuyển xong ({len(okb)} OK)")
                else:
                    for u in chunk:
                        ld = http_login(u, args.password)
                        log(f"  DRY {u}: balance={ld['balance'] if ld else 0:,}")
            if bi < len(batches):
                log(f"😴 Nghỉ {args.batch_pause}s giữa lô {bi} và {bi+1}...")
                time.sleep(args.batch_pause)

    # ===== LƯU + BÁO CÁO =====
    if execute:
        write_csv_append("phase1_spin.csv", "acc,status,remain,reward,prize,ms,note",
                         [f"{r['user']},{r['status']},{r['remain']},{r['reward']},"
                          f"{r['prize']},{r['ms']},{r['note']}" for r in sorted(all_spin, key=lambda x: x["user"])],
                         args.append)
        write_csv_append("phase2_transfer.csv", "acc,status,balance,transferred,ms,note",
                         [f"{r['user']},{r['status']},{r['balance']},"
                          f"{r['transferred']},{r['ms']},{r['note']}" for r in sorted(all_trans, key=lambda x: x["user"])],
                         args.append)

    spun = [r for r in all_spin if r["status"] == "SPUN"]
    ok = [r for r in all_trans if r["status"] == "OK"]
    wall = time.time() - t_start
    print("\n" + "=" * 70)
    print("🏁 TỔNG KẾT")
    print("=" * 70)
    if execute:
        print(f"  Quay được     : {len(spun)}/{len(all_spin)} (thưởng {sum(r['reward'] for r in spun):,} x)")
        print(f"  Chuyển thành công: {len(ok)}/{len(all_trans)}")
        print(f"  Tổng x gửi   : {sum(r['transferred'] for r in ok):,} (xxxx nhận ~90%)")
    bal1 = get_public_balance(sess0, args.dest)
    if execute and bal0 is not None and bal1 is not None:
        print(f"  Balance xxxx  : {bal0:,} -> {bal1:,} (+{bal1 - bal0:,} x)")
    print(f"  ⏱️  TỔNG THỜI GIAN: {int(wall)}s = {wall/60:.1f} phút "
          f"({len(users)} acc, {len(batches)} lô)")
    print("💾  Chi tiết: phase1_spin.csv / phase2_transfer.csv")


if __name__ == "__main__":
    main()

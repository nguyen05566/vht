#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
QUÉT TÀI KHOẢN HỢP LỆ (HTTP + WEBSOCKET LOGIN) & TẠO FILE MỚI + DỌN DẸP FILE CŨ
================================================================================
- Dò toàn bộ tài khoản từ danh sách file (acc*.txt) hoặc dải tên (--range)
- Kiểm tra chính xác: HTTP login -> lấy token -> kết nối WebSocket -> WS LOGIN handshake (st=0)
- Tài khoản bị flag/khóa/lỗi sẽ bị loại bỏ
- Tạo file danh sách hợp lệ mới (chia nhỏ theo chunk_size)
- (Tùy chọn) Xóa sạch các file acc cũ rác để làm gọn repo
"""
import argparse
import glob
import os
import re
import struct
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

import requests
import websocket

# ===== CẤU HÌNH WEBSOCKET & HTTP =====
WS_URL = "wss://gamevh.net/ws/gameServer"
LOGIN_URL = "https://gamevh.net/login.jsp"
GAME_URL = "https://gamevh.net/play/caro/0"
VERSION = "5.0.2"
GAME_ID = "caro"

CMD_LOGIN = 302         # 0x012E
CMD_PONG = 300          # 0x012C
CMD_PING = 301
CMD_ALERT = 303

UA = ("Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/139.0 Safari/537.36")


# ==================== FRAMING ====================
def pack_num(cmd, payload=b""):   return struct.pack(">H", cmd) + payload
def i32(v): return struct.pack(">i", v)
def i8(v):  return struct.pack(">b", v)
def asc(s):
    e = s.encode("ascii", "replace")[:255]
    return i8(len(e)) + e


class Reader:
    def __init__(self, d):
        self.d, self.p = bytes(d), 0
    def rem(self): return len(self.d) - self.p
    def i8(self):
        v = struct.unpack_from(">b", self.d, self.p)[0] if self.p < len(self.d) else 0
        self.p += 1; return v
    def i16(self):
        v = struct.unpack_from(">h", self.d, self.p)[0] if self.p + 2 <= len(self.d) else 0
        self.p += 2; return v
    def utf16(self):
        n = self.i16()
        if n <= 0: return ""
        e = min(n * 2, self.rem())
        s = self.d[self.p:self.p + e].decode("utf-16-be", "replace"); self.p += e
        return s


def parse_frame(raw):
    if not raw: return None, None
    if isinstance(raw, str):
        raw = raw.encode("utf-8", "ignore")
    if len(raw) < 2: return None, None
    first = struct.unpack_from(">b", raw, 0)[0]
    if first < 0:
        n = -first
        if len(raw) < 1 + n: return None, None
        return raw[1:1 + n].decode("ascii", "replace"), Reader(raw[1 + n:])
    return (first << 8) | raw[1], Reader(raw[2:])


# ==================== KIỂM TRA TÀI KHOẢN ====================
def check_ws_account(user, passwd, timeout=10):
    """
    Kiểm tra toàn diện:
    1. HTTP Login
    2. Lấy Token & Session Cookies
    3. Kết nối WebSocket & gửi LOGIN
    4. Xác nhận handshake status == 0
    Trả về: (user, True, "OK") hoặc (user, False, reason)
    """
    # 1. HTTP Login
    sess = requests.Session()
    sess.headers.update({"User-Agent": UA, "Accept-Language": "vi-VN,vi;q=0.9"})
    try:
        sess.get(LOGIN_URL, timeout=timeout)
        r = sess.post(LOGIN_URL, timeout=timeout,
                      data={"redirect": "/", "USER_NAME": user, "PASSWORD": passwd,
                            "AUTO_LOGIN": "true", "LOGIN": "Đăng nhập"},
                      headers={"Origin": "https://gamevh.net", "Referer": LOGIN_URL},
                      allow_redirects=True)
        if "login.jsp" in r.url:
            return user, False, "HTTP_LOGIN_FAIL"

        # 2. Lấy Token
        g = sess.get(GAME_URL, timeout=timeout)
        tm = re.search(r"var\s+token\s*=\s*(-?\d+)", g.text)
        token = int(tm.group(1)) if tm else 0
        if not token:
            return user, False, "NO_TOKEN"

        mm = re.search(r"var\s+currentPlayerNickName\s*=\s*[\"']([^\"']+)[\"']", g.text)
        nick = mm.group(1).strip() if mm else user
        cookie = "; ".join(f"{k}={v}" for k, v in sess.cookies.items())
    except Exception as e:
        return user, False, f"HTTP_ERR:{type(e).__name__}"

    # 3. WebSocket Connect & Login
    ws = None
    try:
        ws = websocket.create_connection(
            WS_URL, timeout=timeout,
            header=[f"Cookie: {cookie}", "Origin: https://gamevh.net", f"User-Agent: {UA}"],
            cookie=cookie)
        ws.send_binary(pack_num(CMD_LOGIN, asc(nick) + i32(token)
                                + asc(VERSION) + asc("") + asc(GAME_ID) + i8(1)))

        deadline = time.time() + timeout
        while time.time() < deadline:
            try:
                raw = ws.recv()
            except Exception:
                break
            if not raw: continue
            name, rd = parse_frame(raw)
            if name == CMD_PING or name == "PING":
                try: ws.send_binary(pack_num(CMD_PONG))
                except Exception: pass
                continue
            if name == CMD_LOGIN or name == "LOGIN":
                st = rd.i8()
                if st == 0:
                    try: ws.close()
                    except Exception: pass
                    return user, True, "WS_OK"
                else:
                    path = rd.utf16() if rd.rem() > 0 else ""
                    try: ws.close()
                    except Exception: pass
                    return user, False, f"FLAGGED_OR_REJECTED(st={st})"
        try: ws.close()
        except Exception: pass
        return user, False, "WS_TIMEOUT"
    except Exception as e:
        if ws:
            try: ws.close()
            except Exception: pass
        return user, False, f"WS_ERR:{type(e).__name__}"


# ==================== ĐỌC TÀI KHOẢN ĐẦU VÀO ====================
def load_accounts(args):
    if args.user:
        return [args.user.strip('"\' ')], []
    if args.range:
        range_str = args.range.strip('"\' ')
        parts = range_str.replace(",", " ").split()
        prefix = parts[0]
        start, end = int(parts[1]), int(parts[2])
        users = [f"{prefix}{i}" for i in range(start, end + 1)]
        print(f"🔍 Sinh {len(users)} tài khoản từ dải {prefix}{start}..{prefix}{end}")
        return users, []

    # Quét theo pattern file (tự động loại bỏ dấu nháy kép/đơn/escape nếu có)
    raw_pattern = args.pattern or "acc*.txt"
    # Dọn dẹp triệt để các ký tự nháy, escape do shell truyền vào (\", ", ')
    raw_pattern = raw_pattern.replace('\\"', '').replace('"', '').replace("'", "").replace('\\', '').strip()
    patterns = [p.strip() for p in raw_pattern.split(",") if p.strip()]
    files = []
    for p in patterns:
        files.extend(glob.glob(p))
    files = sorted(list(dict.fromkeys(files)))
    if not files:
        print(f"Không tìm thấy file nào khớp pattern: {args.pattern}")
        return [], []

    seen = set()
    users = []
    print(f"📂 Đang đọc từ {len(files)} file khớp pattern...")
    for fp in files:
        # Bỏ qua chính các file kết quả mới để không bị lặp
        if "acc_valid" in fp:
            continue
        try:
            with open(fp, encoding="utf-8", errors="replace") as f:
                for line in f:
                    line = line.strip()
                    if not line or line.startswith("#"):
                        continue
                    name = line.split("\t")[0].strip()
                    if name and name.lower() not in seen:
                        seen.add(name.lower())
                        users.append(name)
        except Exception as e:
            print(f"  Lỗi đọc file {fp}: {e}")

    print(f"📋 Tổng số tài khoản duy nhất cần kiểm tra: {len(users)} (từ {len(files)} file)")
    return users, files


# ==================== MAIN ====================
def main():
    ap = argparse.ArgumentParser(
        description="Quét tài khoản hợp lệ (HTTP + WS login) & tạo danh sách mới")
    ap.add_argument("--pattern", default="acc*.txt",
                    help="Pattern file acc cần quét (default: acc*.txt)")
    ap.add_argument("--range", default=None,
                    help='Dải tên quét, vd: --range "test 1 5000"')
    ap.add_argument("--user", default=None,
                    help="Kiểm tra 1 tài khoản cụ thể")
    ap.add_argument("--password", "--pwd", required=True,
                    help="Mật khẩu chung của các tài khoản")
    ap.add_argument("--workers", type=int, default=60,
                    help="Số luồng quét đồng thời (default: 60)")
    ap.add_argument("--timeout", type=int, default=10,
                    help="Timeout cho mỗi kết nối (giây)")
    ap.add_argument("--out-prefix", default="acc_valid",
                    help="Tiền tố file xuất ra (default: acc_valid)")
    ap.add_argument("--chunk-size", type=int, default=5000,
                    help="Số acc mỗi file mới (default: 5000, 0 = gộp 1 file)")
    ap.add_argument("--clean-old", action="store_true",
                    help="Xóa các file acc cũ sau khi đã lưu file hợp lệ mới")
    ap.add_argument("--limit", type=int, default=0,
                    help="Giới hạn số lượng acc cần quét (0 = toàn bộ)")
    args = ap.parse_args()

    users, source_files = load_accounts(args)
    if args.limit and args.limit > 0:
        users = users[:args.limit]

    if not users:
        print("🤷 Không tìm thấy tài khoản nào để quét.")
        return

    print("=" * 70)
    print(f"🚀 BẮT ĐẦU QUÉT WEBSOCKET: {len(users)} tài khoản | {args.workers} luồng | Timeout: {args.timeout}s")
    print("=" * 70)

    t0 = time.time()
    valid_accounts = []
    invalid_count = 0
    lock = threading.Lock()

    def log(msg):
        with lock:
            print(msg, flush=True)

    with ThreadPoolExecutor(max_workers=min(args.workers, len(users) or 1)) as ex:
        futs = {ex.submit(check_ws_account, u, args.password, args.timeout): u for u in users}
        total = len(users)
        for i, f in enumerate(as_completed(futs), 1):
            user, ok, reason = f.result()
            if ok:
                with lock:
                    valid_accounts.append(user)
                if len(valid_accounts) % 100 == 0 or len(valid_accounts) <= 10:
                    log(f"  [{i}/{total}] ✅ {user} -> HỢP LỆ (Tổng OK: {len(valid_accounts)})")
            else:
                with lock:
                    invalid_count += 1

            if i % 500 == 0 or i == total:
                elapsed = time.time() - t0
                speed = i / elapsed if elapsed > 0 else 0
                log(f"📊 Tiến độ [{i}/{total}] ({i/total*100:.1f}%) | ✅ {len(valid_accounts)} hợp lệ | ❌ {invalid_count} loại | Tốc độ: {speed:.1f} acc/s")

    wall = time.time() - t0
    valid_accounts.sort()

    print("\n" + "=" * 70)
    print("🏁 KẾT QUẢ QUÉT TỔNG HỢP")
    print("=" * 70)
    print(f"  Tổng đã quét    : {len(users):,} tài khoản")
    print(f"  ✅ Hợp lệ (WS OK): {len(valid_accounts):,} tài khoản ({len(valid_accounts)/len(users)*100:.1f}%)")
    print(f"  ❌ Không hợp lệ : {invalid_count:,} tài khoản (bị flag/sai mk/khóa)")
    print(f"  ⏱️ Tổng thời gian: {int(wall)}s = {wall/60:.1f} phút")

    if not valid_accounts:
        print("⚠️ Không có tài khoản nào hợp lệ. Giữ nguyên file cũ để bảo toàn dữ liệu.")
        return

    # ===== LƯU FILE HỢP LỆ MỚI =====
    print("\n" + "=" * 70)
    print("💾 LƯU FILE DANH SÁCH HỢP LỆ MỚI")
    print("=" * 70)

    created_files = []
    chunk_size = args.chunk_size

    if chunk_size <= 0 or len(valid_accounts) <= chunk_size:
        out_path = f"{args.out_prefix}.txt"
        with open(out_path, "w", encoding="utf-8") as f:
            for u in valid_accounts:
                f.write(f"{u}\n")
        created_files.append(out_path)
        print(f"  ✅ Đã ghi: {out_path} ({len(valid_accounts):,} tài khoản)")
    else:
        for idx, start in enumerate(range(0, len(valid_accounts), chunk_size), 1):
            chunk = valid_accounts[start:start + chunk_size]
            out_path = f"{args.out_prefix}_{idx}.txt"
            with open(out_path, "w", encoding="utf-8") as f:
                for u in chunk:
                    f.write(f"{u}\n")
            created_files.append(out_path)
            print(f"  ✅ Đã ghi: {out_path} ({len(chunk):,} tài khoản)")

    # ===== DỌN DẸP FILE CŨ =====
    if args.clean_old and source_files:
        print("\n" + "=" * 70)
        print(f"🧹 DỌN DẸP {len(source_files)} FILE CŨ...")
        print("=" * 70)
        deleted_count = 0
        for sf in source_files:
            # Không xóa các file vừa tạo
            if sf in created_files or os.path.abspath(sf) in [os.path.abspath(cf) for cf in created_files]:
                continue
            try:
                if os.path.exists(sf):
                    os.remove(sf)
                    deleted_count += 1
            except Exception as e:
                print(f"  Không xóa được {sf}: {e}")
        print(f"  🎉 Đã dọn sạch {deleted_count} file acc cũ. Repo giờ chỉ còn các file hợp lệ mới!")


if __name__ == "__main__":
    main()

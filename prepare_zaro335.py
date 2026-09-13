#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Chuẩn bị 335 nick hợp lệ cho zaro_multi (tạo bàn 1000xu):
  1) Quét acc_valid_*.txt — login HTTP OK + lấy playerId + balance
  2) Ghi acc_zaro_335.txt (1 user/dòng)
  3) Các nick số dư < TARGET_MIN được cấp xu từ pool bot giàu (arena*)
     qua WS TRANSFER 317, xoay vòng funder khi hết/đứt session.

Env:
  TARGET_COUNT=335
  TARGET_MIN=3000          # số dư tối thiểu sau khi cấp (đủ tạo bàn 1k + đệm)
  FUND_AMOUNT=3000         # mỗi lần chuyển
  BOT_PASSWD=nhat123456
  SCAN_WORKERS=12
  FUND_KEEP=50000          # giữ lại tối thiểu trên mỗi funder (không rút cạn)
"""
from __future__ import annotations

import json
import os
import random
import re
import struct
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import requests
import websocket

WS_URL = "wss://gamevh.net/ws/gameServer"
LOGIN_URL = "https://gamevh.net/login.jsp"
GAME_URL = "https://gamevh.net/play/xiangqi/0"
PROFILE_URL = "https://gamevh.net/com/ftl/game/profile/player_profile.jsp"
MIN_TRANSFER = 200
CMD_TRANSFER = 317
CMD_BALANCE = 319
CMD_LOGIN = 302
CMD_PING = 301
CMD_PONG = 300

UA = ("Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/139.0 Safari/537.36")

def env_int(k, d):
    try:
        return int(os.environ.get(k, d))
    except Exception:
        return d

def env_str(k, d):
    v = os.environ.get(k)
    return str(v).strip() if v and str(v).strip() else d

TARGET_COUNT = env_int("TARGET_COUNT", 335)
TARGET_MIN = env_int("TARGET_MIN", 3000)
FUND_AMOUNT = env_int("FUND_AMOUNT", 3000)
BOT_PASSWD = env_str("BOT_PASSWD", "nhat123456")
SCAN_WORKERS = env_int("SCAN_WORKERS", 12)
FUND_KEEP = env_int("FUND_KEEP", 50000)
OUT_FILE = env_str("OUT_FILE", "acc_zaro_335.txt")
REPORT = env_str("REPORT_FILE", "reports/zaro335_prepare.json")

# Pool funder: bot arena đang nhiều xu (cập nhật runtime bằng probe)
SEED_FUNDERS = [
    ("arena13", "nhat123456"),
    ("arena12", "nhat123456"),
    ("arena8", "nhat123456"),
    ("arena11", "nhat123456"),
    ("arena5", "nhat123456"),
    ("arena10", "nhat123456"),
    ("arena17", "nhat123456"),
    ("arena15", "nhat123456"),
    ("arena20", "nhat123456"),
    ("arena16", "nhat123456"),
    ("arena18", "nhat123456"),
    ("arena9", "nhat123456"),
    ("arena14", "nhat123456"),
    ("arena19", "nhat123456"),
    ("arena6", "nhat123456"),
]


def pack_num(cmd, payload=b""):
    return struct.pack(">H", cmd) + payload


def i64(v):
    return struct.pack(">q", v)


def i32(v):
    return struct.pack(">i", v)


def i8(v):
    return struct.pack(">b", v)


def asc(s):
    e = s.encode("ascii", "replace")[:255]
    return i8(len(e)) + e


class Reader:
    def __init__(self, data):
        self.d = bytes(data)
        self.p = 0

    def rem(self):
        return len(self.d) - self.p

    def i8(self):
        if self.p >= len(self.d):
            return 0
        v = struct.unpack_from(">b", self.d, self.p)[0]
        self.p += 1
        return v

    def utf16(self):
        if self.p + 2 > len(self.d):
            return ""
        n = struct.unpack_from(">h", self.d, self.p)[0]
        self.p += 2
        if n <= 0:
            return ""
        e = min(n * 2, self.rem())
        s = self.d[self.p:self.p + e].decode("utf-16-be", "replace")
        self.p += e
        return s


def parse_frame(raw):
    if not raw or len(raw) < 2:
        return None, None
    first = struct.unpack_from(">b", raw, 0)[0]
    if first < 0:
        n = -first
        if len(raw) < 1 + n:
            return None, None
        return raw[1:1 + n].decode("ascii", "replace"), Reader(raw[1 + n:])
    return (first << 8) | raw[1], Reader(raw[2:])


def http_login(user, passwd):
    s = requests.Session()
    s.headers.update({"User-Agent": UA, "Accept-Language": "vi-VN,vi;q=0.9"})
    try:
        s.get(LOGIN_URL, timeout=12)
        r = s.post(
            LOGIN_URL, timeout=12,
            data={"redirect": "/", "USER_NAME": user, "PASSWORD": passwd,
                  "AUTO_LOGIN": "true", "LOGIN": "Đăng nhập"},
            headers={"Origin": "https://gamevh.net", "Referer": LOGIN_URL,
                     "Content-Type": "application/x-www-form-urlencoded"},
            allow_redirects=True,
        )
        if "login.jsp" in (r.url or ""):
            return None, "login_fail"
        page = s.get(GAME_URL, timeout=12).text
        tm = re.search(r"var\s+token\s*=\s*(-?\d+)", page)
        nm = re.search(r"var\s+currentPlayerNickName\s*=\s*[\"']([^\"']+)", page)
        pid = re.search(r"var\s+currentPlayerId\s*=\s*(\d+)", page)
        if not tm or not nm:
            return None, "no_token"
        bal = 0
        try:
            pr = s.get(PROFILE_URL, timeout=12).text
            m = re.search(r'(?is)chipBalance[^>]*>(.*?)</div>', pr)
            if m:
                bal = int(re.sub(r"[^\d]", "", m.group(1)) or 0)
        except Exception:
            pass
        cookie = "; ".join(f"{k}={v}" for k, v in s.cookies.items())
        return {
            "user": user,
            "passwd": passwd,
            "session": s,
            "cookie": cookie,
            "token": int(tm.group(1)),
            "nick": nm.group(1).strip(),
            "player_id": int(pid.group(1)) if pid else 0,
            "balance": bal,
        }, None
    except Exception as e:
        return None, f"{type(e).__name__}:{e}"


def load_candidate_users():
    """Ưu tiên acc_valid_2 (đang dùng multi), rồi 1,3,4... — unique."""
    order = [
        "acc_valid_2.txt", "acc_valid_1.txt", "acc_valid_3.txt", "acc_valid_4.txt",
        "acc_valid_5.txt", "acc_valid_6.txt", "acc_valid_7.txt", "acc_valid_8.txt",
    ]
    seen = set()
    out = []
    # skip known bot usernames that should stay as funders / not multi workers
    skip = {u for u, _ in SEED_FUNDERS} | {
        "arena1", "arena7", "nguyenpy2", "nguyenpy3", "vu829"
    }
    for name in order:
        p = Path(name)
        if not p.exists():
            continue
        for line in p.read_text(encoding="utf-8", errors="replace").splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            u = line.split("\t")[0].split()[0].strip()
            if not u or u in seen or u in skip:
                continue
            seen.add(u)
            out.append(u)
    return out


def scan_valid(users, need):
    valid = []
    fail = 0
    checked = 0
    print(f"[SCAN] Cần {need} nick hợp lệ | candidates={len(users)} | workers={SCAN_WORKERS}", flush=True)

    def one(u):
        info, err = http_login(u, BOT_PASSWD)
        if info:
            return u, info["player_id"], info["balance"], info["nick"], None
        return u, 0, 0, "", err

    # batch to allow early stop
    i = 0
    batch = 80
    while i < len(users) and len(valid) < need:
        chunk = users[i:i + batch]
        i += batch
        with ThreadPoolExecutor(SCAN_WORKERS) as ex:
            futs = [ex.submit(one, u) for u in chunk]
            for f in as_completed(futs):
                u, pid, bal, nick, err = f.result()
                checked += 1
                if err or not pid:
                    fail += 1
                else:
                    valid.append({"user": u, "player_id": pid, "balance": bal, "nick": nick})
                    if len(valid) % 25 == 0 or len(valid) <= 5:
                        print(f"[SCAN] OK {len(valid)}/{need} (checked={checked} fail={fail}) "
                              f"last={u} bal={bal:,}", flush=True)
                if len(valid) >= need:
                    break
        print(f"[SCAN] progress checked={checked} valid={len(valid)} fail={fail}", flush=True)
        if len(valid) >= need:
            break
    return valid[:need], {"checked": checked, "fail": fail, "ok": len(valid)}


class FunderSession:
    def __init__(self, user, passwd):
        self.user = user
        self.passwd = passwd
        self.info = None
        self.ws = None
        self.balance = 0
        self.ok_n = 0
        self.fail_n = 0
        self.sent = 0

    def login(self):
        info, err = http_login(self.user, self.passwd)
        if not info:
            print(f"[FUND] {self.user} login fail: {err}", flush=True)
            return False
        self.info = info
        self.balance = info["balance"]
        try:
            self.ws = websocket.create_connection(
                WS_URL, timeout=15,
                header=["Cookie: " + info["cookie"], "Origin: https://gamevh.net", "User-Agent: " + UA],
            )
            self.ws.send_binary(pack_num(CMD_LOGIN, asc(info["nick"]) + i32(info["token"])
                                         + asc("5.0.2") + asc("") + asc("xiangqi") + i8(1)))
            deadline = time.time() + 12
            while time.time() < deadline:
                self.ws.settimeout(max(0.3, deadline - time.time()))
                try:
                    raw = self.ws.recv()
                except Exception:
                    break
                if not raw:
                    continue
                cmd, r = parse_frame(raw)
                if cmd in ("PING", CMD_PING):
                    self.ws.send_binary(pack_num(CMD_PONG))
                    continue
                if cmd in ("LOGIN", CMD_LOGIN):
                    st = r.i8() if r else -1
                    if st == 0:
                        print(f"[FUND] ✅ {self.user} WS OK bal={self.balance:,} id={info['player_id']}", flush=True)
                        return True
                    print(f"[FUND] {self.user} WS login status={st}", flush=True)
                    break
            self.close()
            return False
        except Exception as e:
            print(f"[FUND] {self.user} WS err: {e}", flush=True)
            self.close()
            return False

    def close(self):
        if self.ws:
            try:
                self.ws.close()
            except Exception:
                pass
        self.ws = None

    def available(self):
        return max(0, self.balance - FUND_KEEP)

    def transfer(self, dest_id, amount):
        if not self.ws or amount <= MIN_TRANSFER:
            return False, "no_ws_or_amount"
        if self.available() < amount:
            return False, "insufficient"
        try:
            self.ws.send_binary(pack_num(CMD_TRANSFER, i64(dest_id) + i64(amount)))
        except Exception as e:
            return False, f"send:{e}"
        deadline = time.time() + 12
        while time.time() < deadline:
            try:
                self.ws.settimeout(max(0.2, deadline - time.time()))
                raw = self.ws.recv()
            except Exception:
                break
            if not raw:
                continue
            cmd, r = parse_frame(raw)
            if cmd in ("PING", CMD_PING):
                try:
                    self.ws.send_binary(pack_num(CMD_PONG))
                except Exception:
                    pass
                continue
            if cmd in ("TRANSFER", CMD_TRANSFER):
                st = r.i8() if r else -1
                txt = ""
                try:
                    txt = r.utf16() if r and r.rem() else ""
                except Exception:
                    pass
                if st == 0:
                    self.balance -= amount
                    self.ok_n += 1
                    self.sent += amount
                    return True, txt or "OK"
                return False, f"st={st} {txt}"
            if cmd in ("BALANCE_CHANGED", CMD_BALANCE):
                # some servers only emit balance changed
                self.balance -= amount
                self.ok_n += 1
                self.sent += amount
                return True, "BALANCE_CHANGED"
        return False, "timeout"


def fund_targets(targets, funders_seed):
    need = [t for t in targets if t["balance"] < TARGET_MIN]
    print(f"[FUND] {len(need)}/{len(targets)} nick cần cấp (min={TARGET_MIN:,}, amount={FUND_AMOUNT:,})", flush=True)
    if not need:
        return {"funded": 0, "skipped": len(targets), "sent": 0}

    # probe funders live balance order
    live = []
    for u, p in funders_seed:
        info, err = http_login(u, p)
        if info and info["balance"] > FUND_KEEP + FUND_AMOUNT:
            live.append((info["balance"], u, p))
            print(f"[FUND] pool {u}: {info['balance']:,}", flush=True)
        else:
            print(f"[FUND] skip pool {u}: {err or info and info['balance']}", flush=True)
    live.sort(reverse=True)
    if not live:
        print("[FUND] ❌ Không có funder đủ xu", flush=True)
        return {"funded": 0, "error": "no_funder"}

    fi = 0
    session = None

    def ensure_session():
        nonlocal fi, session
        while fi < len(live):
            if session:
                session.close()
            bal, u, p = live[fi]
            session = FunderSession(u, p)
            if session.login():
                return True
            fi += 1
        return False

    if not ensure_session():
        return {"funded": 0, "error": "ws_fail"}

    funded = 0
    failed = 0
    for idx, t in enumerate(need, 1):
        # rotate funder if low
        tries = 0
        while session and session.available() < FUND_AMOUNT and fi + 1 < len(live):
            print(f"[FUND] {session.user} còn {session.balance:,} < need -> next funder", flush=True)
            fi += 1
            if not ensure_session():
                break
            tries += 1
            if tries > len(live):
                break
        if not session or session.available() < FUND_AMOUNT:
            print("[FUND] ⚠️ Hết funder đủ xu, dừng cấp", flush=True)
            break
        ok, msg = session.transfer(t["player_id"], FUND_AMOUNT)
        if ok:
            funded += 1
            t["balance"] = t.get("balance", 0) + FUND_AMOUNT
            t["funded"] = True
            if funded % 10 == 0 or funded <= 3:
                print(f"[FUND] ✅ {funded}/{len(need)} {t['user']} +{FUND_AMOUNT:,} "
                      f"via {session.user} (funder~{session.balance:,}) | {msg}", flush=True)
        else:
            failed += 1
            t["fund_err"] = msg
            print(f"[FUND] ❌ {t['user']} via {session.user}: {msg}", flush=True)
            # reconnect on hard fail
            if "timeout" in str(msg) or "send" in str(msg):
                if not ensure_session():
                    break
        time.sleep(random.uniform(0.8, 1.6))

    if session:
        print(f"[FUND] session {session.user} sent={session.sent:,} ok={session.ok_n} fail={session.fail_n} "
              f"left~{session.balance:,}", flush=True)
        session.close()
    return {"funded": funded, "failed": failed, "need": len(need), "sent": funded * FUND_AMOUNT}


def main():
    Path("reports").mkdir(exist_ok=True)
    users = load_candidate_users()
    print(f"[INIT] candidates unique={len(users)} target={TARGET_COUNT}", flush=True)
    valid, scan_stats = scan_valid(users, TARGET_COUNT)
    if len(valid) < TARGET_COUNT:
        print(f"[WARN] Chỉ có {len(valid)}/{TARGET_COUNT} nick login OK", flush=True)

    # write list early
    Path(OUT_FILE).write_text("\n".join(v["user"] for v in valid) + "\n", encoding="utf-8")
    print(f"[OUT] wrote {OUT_FILE} ({len(valid)} nicks)", flush=True)

    fund_stats = fund_targets(valid, SEED_FUNDERS)

    # rewrite with same order
    Path(OUT_FILE).write_text("\n".join(v["user"] for v in valid) + "\n", encoding="utf-8")

    report = {
        "ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "target_count": TARGET_COUNT,
        "got": len(valid),
        "scan": scan_stats,
        "fund": fund_stats,
        "target_min": TARGET_MIN,
        "fund_amount": FUND_AMOUNT,
        "out_file": OUT_FILE,
        "below_min_after": sum(1 for v in valid if v.get("balance", 0) < TARGET_MIN),
        "sample": valid[:5],
    }
    Path(REPORT).write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2), flush=True)
    print(f"[DONE] {OUT_FILE} + {REPORT}", flush=True)
    return 0 if len(valid) >= TARGET_COUNT else 1


if __name__ == "__main__":
    # fix LOGIN pack: use named LOGIN like fund_all
    sys.exit(main())

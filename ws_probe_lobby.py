#!/usr/bin/env python3
"""
Probe v2 — gamevh.net WS: giải mã frame LOGIN, vào lobby (ENTER_PLACE) để
quan sát cách server push danh sách tài khoản (406 PLAYER_ENTERED: pid + nickname)
và số dư (431 BALANCE_CHANGED). Read-only: không cược, không chuyển xu.
"""
import json
import re
import struct
import sys
import time

import requests
import websocket

WS_URL = "wss://gamevh.net/ws/gameServer"
LOGIN_URL = "https://gamevh.net/login.jsp"
GAME_URL = "https://gamevh.net/play/caro/0"
PROFILE_URL = "https://gamevh.net/com/ftl/game/profile/player_profile.jsp"
VERSION = "5.0.2"
GAME_ID = "caro"
UA = ("Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/139.0 Safari/537.36")


def i8(v):
    return struct.pack(">b", v)


def i32(v):
    return struct.pack(">i", v)


def i64(v):
    return struct.pack(">q", v)


def asc(s):
    e = s.encode("ascii", "replace")[:255]
    return i8(len(e)) + e


def utf(s):
    return struct.pack(">h", len(s)) + s.encode("utf-16-be")


def pack_num(cmd, payload=b""):
    return struct.pack(">H", cmd) + payload


class R:
    def __init__(self, d):
        self.d, self.p = bytes(d), 0

    def rem(self):
        return len(self.d) - self.p

    def u8(self):
        v = self.d[self.p] if self.p < len(self.d) else 0
        self.p += 1
        return v

    def i8(self):
        v = struct.unpack_from(">b", self.d, self.p)[0] if self.p < len(self.d) else 0
        self.p += 1
        return v

    def i16(self):
        v = struct.unpack_from(">h", self.d, self.p)[0] if self.p + 2 <= len(self.d) else 0
        self.p += 2
        return v

    def i32(self):
        v = struct.unpack_from(">i", self.d, self.p)[0] if self.p + 4 <= len(self.d) else 0
        self.p += 4
        return v

    def i64(self):
        v = struct.unpack_from(">q", self.d, self.p)[0] if self.p + 8 <= len(self.d) else 0
        self.p += 8
        return v

    def ascii(self):
        n = self.u8()
        e = min(n, self.rem())
        s = self.d[self.p:self.p + e].decode("ascii", "replace")
        self.p += e
        return s

    def utf16(self):
        n = self.i16()
        if n <= 0:
            return ""
        e = min(n * 2, self.rem())
        s = self.d[self.p:self.p + e].decode("utf-16-be", "replace")
        self.p += e
        return s


def parse_frame(raw):
    if isinstance(raw, str):
        raw = raw.encode("utf-8", "ignore")
    if not raw or len(raw) < 2:
        return None, None
    first = struct.unpack_from(">b", raw, 0)[0]
    if first < 0:
        n = -first
        if len(raw) < 1 + n:
            return None, None
        return raw[1:1 + n].decode("ascii", "replace"), R(raw[1 + n:])
    return (first << 8) | raw[1], R(raw[2:])


def http_login(user, pwd):
    s = requests.Session()
    s.headers.update({"User-Agent": UA, "Accept-Language": "vi-VN,vi;q=0.9"})
    s.get(LOGIN_URL, timeout=15)
    r = s.post(LOGIN_URL, timeout=15,
               data={"redirect": "/", "USER_NAME": user, "PASSWORD": pwd,
                     "AUTO_LOGIN": "true", "LOGIN": "Đăng nhập"},
               headers={"Origin": "https://gamevh.net", "Referer": LOGIN_URL},
               allow_redirects=True)
    if "login.jsp" in r.url:
        return None, "LOGIN_FAIL"
    g = s.get(GAME_URL, timeout=15)
    tm = re.search(r"var\s+token\s*=\s*(-?\d+)", g.text)
    nm = re.search(r"var\s+currentPlayerNickName\s*=\s*['\"]([^'\"]+)['\"]", g.text)
    pm = re.search(r"var\s+placePath\s*=\s*\"([^\"]+)\"", g.text)
    cookie = "; ".join(f"{k}={v}" for k, v in s.cookies.items())
    info = {"user": user, "nick": nm.group(1) if nm else user,
            "token": int(tm.group(1)) if tm else 0,
            "placePath": pm.group(1) if pm else "",
            "cookies": {k: v for k, v in s.cookies.items()}}
    return {"session": s, "cookie": cookie, "info": info}, "OK"


def decode_login_payload(rd):
    """LOGIN(302) response: status, lastPopup, ids, nickname ascii, nickname utf16, ..."""
    out = {}
    try:
        out["status"] = rd.i8()
        out["lastPopup"] = rd.ascii()
        out["f1_i32"] = rd.i32()
        out["f2_i32"] = rd.i32()
        out["nick_ascii"] = rd.ascii()
        out["sep"] = rd.u8()
        out["nick_utf16"] = rd.utf16()
        out["rest_hex"] = rd.d[rd.p:].hex()
    except Exception as e:
        out["err"] = str(e)
    return out


def main():
    user, pwd = sys.argv[1], sys.argv[2]
    res, why = http_login(user, pwd)
    if res is None:
        print(json.dumps({"error": why}))
        return 1
    info = res["info"]
    print("== ACCOUNT (HTTP) ==")
    cookies = info.pop("cookies")
    print(json.dumps(info, ensure_ascii=False, indent=2))
    print("cookies:", {k: (v[:12] + "..." if len(v) > 12 else v) for k, v in cookies.items()})

    ws = websocket.create_connection(
        WS_URL, timeout=15,
        header=[f"Cookie: {res['cookie']}", "Origin: https://gamevh.net", f"User-Agent: {UA}"],
        cookie=res["cookie"])
    ws.send_binary(pack_num(302, asc(info["nick"]) + i32(info["token"]) +
                            asc(VERSION) + asc("") + asc(GAME_ID) + i8(1)))

    deadline = time.time() + 20
    players = []
    logs = []
    while time.time() < deadline:
        try:
            ws.settimeout(max(1, deadline - time.time()))
            raw = ws.recv()
        except Exception:
            break
        name, rd = parse_frame(raw)
        if name is None:
            continue
        rec = {"cmd": name, "len": len(raw)}
        try:
            if name == 302:
                rec["login"] = decode_login_payload(rd)
            elif name == 406:
                lvl = rd.i8(); pid = rd.i64(); nm = rd.utf16()
                rec["place_level"] = lvl; rec["player_id"] = pid; rec["name"] = nm
                players.append({"pid": pid, "name": nm, "level": lvl})
            elif name == 407:
                rec["place_level"] = rd.i8(); rec["player_id"] = rd.i64()
            elif name == 431:
                rec["i64_a"] = rd.i64(); rec["i64_b"] = rd.i64()
            elif name == 401:
                rec["place"] = rd.ascii()
            elif name == 419:
                rec["i32"] = rd.i32()
            elif name == 432:
                rec["i64"] = rd.i64()
            elif name == 310:
                rec["i32"] = rd.i32()
            elif name == 424:
                rec["i16"] = rd.i16()
        except Exception as e:
            rec["decode_error"] = str(e)
        logs.append(rec)
        if name == 301:
            try:
                ws.send_binary(pack_num(300))
            except Exception:
                pass

    print("\n== FRAMES (trước khi vào lobby) ==")
    for r in logs:
        print(json.dumps(r, ensure_ascii=False))

    # ---- vào lobby: ENTER_PLACE(401) path + utf(pw) + i8(mode)
    print("\n== ENTER_PLACE Lobby.caro.0 ==")
    ws.send_binary(pack_num(401, asc("Lobby.caro.0") + utf("") + i8(1)))
    deadline = time.time() + 25
    logs2, players2 = [], []
    while time.time() < deadline:
        try:
            ws.settimeout(max(1, deadline - time.time()))
            raw = ws.recv()
        except Exception:
            break
        name, rd = parse_frame(raw)
        if name is None:
            continue
        rec = {"cmd": name, "len": len(raw)}
        try:
            if name == 406:
                lvl = rd.i8(); pid = rd.i64(); nm = rd.utf16()
                rec.update({"place_level": lvl, "player_id": pid, "name": nm})
                players2.append({"pid": pid, "name": nm})
            elif name == 407:
                rec["place_level"] = rd.i8(); rec["player_id"] = rd.i64()
            elif name == 431:
                rec["i64_a"] = rd.i64(); rec["i64_b"] = rd.i64()
            elif name == 401:
                rec["place"] = rd.ascii()
            elif name == 432:
                rec["i64"] = rd.i64()
            elif name == 419:
                rec["i32"] = rd.i32()
        except Exception as e:
            rec["decode_error"] = str(e)
        logs2.append(rec)
        if name == 301:
            try:
                ws.send_binary(pack_num(300))
            except Exception:
                pass
    for r in logs2:
        print(json.dumps(r, ensure_ascii=False))

    print("\n== PLAYERS_SEEN_IN_LOBBY ==")
    print(json.dumps(players2, ensure_ascii=False))
    try:
        ws.close()
    except Exception:
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main())

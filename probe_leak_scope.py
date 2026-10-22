#!/usr/bin/env python3
"""
Probe 3 — 2 câu hỏi:
 A) Credential memberName/memberPassword có bị LỘ cho tài khoản người chơi khác không?
    A1: frame 406 PLAYER_ENTERED mà ta phát ra có chứa credential không (so sánh guest vs account thật)
    A2: trang profile của người chơi khác (showProfile(pid)) có lộ credential của họ không
    A3: zone_player_list_helper.jsp có lộ gì không
 B) Account khách g<id> có chơi được không?
    B1: LIST_BET_AMT -> status?
    B2: CREATE_RULE -> status / báo lỗi?
    B3: ENTER_PLACE bàn thật + START_MATCH/PLAY -> phản hồi?
Chỉ đọc + 1 lần thử tạo bàn (nếu server cho phép thì thoát ngay).
"""
import json
import re
import struct
import sys
import time

import requests
import websocket

UA = ("Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/139.0 Safari/537.36")
WS = "wss://gamevh.net/ws/gameServer"
LOGIN_URL = "https://gamevh.net/login.jsp"
GAME_URL = "https://gamevh.net/play/caro/0"
PROFILE_URL = "https://gamevh.net/com/ftl/game/profile/player_profile.jsp"
ZONE_URL = "https://gamevh.net/com/ftl/game/profile/zone_player_list_helper.jsp"


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


def pn(c, p=b""):
    return struct.pack(">H", c) + p


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


def parse(raw):
    if isinstance(raw, str):
        raw = raw.encode("utf-8", "ignore")
    if not raw or len(raw) < 2:
        return None, None
    f = struct.unpack_from(">b", raw, 0)[0]
    if f < 0:
        n = -f
        return raw[1:1 + n].decode("ascii", "replace"), raw[1 + n:]
    return (f << 8) | raw[1], raw[2:]


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
        return None
    g = s.get(GAME_URL, timeout=15)
    tok = int(re.search(r"var\s+token\s*=\s*(-?\d+)", g.text).group(1))
    nick = re.search(r"currentPlayerNickName\s*=\s*['\"]([^'\"]+)['\"]", g.text).group(1)
    return {"sess": s, "cookie": "; ".join(f"{k}={v}" for k, v in s.cookies.items()),
            "ck": {k: v for k, v in s.cookies.items()}, "token": tok, "nick": nick}


def ws_session(cookie, nick, token, seconds=20, actions=None):
    """Mở WS, login, vào lobby, gửi actions, thu frame."""
    ws = websocket.create_connection(WS, timeout=15,
                                    header=[f"Cookie: {cookie}", "Origin: https://gamevh.net",
                                            f"User-Agent: {UA}"], cookie=cookie)
    ws.send_binary(pn(302, asc(nick) + i32(token) + asc("5.0.2") + asc("") + asc("caro") + i8(1)))
    time.sleep(0.8)
    ws.send_binary(pn(401, asc("Lobby.caro.0") + utf("") + i8(1)))
    time.sleep(1.2)
    if actions:
        for act in actions:
            ws.send_binary(act)
            time.sleep(1.0)
    frames = []
    end = time.time() + seconds
    while time.time() < end:
        try:
            ws.settimeout(max(1, end - time.time()))
            raw = ws.recv()
        except Exception:
            break
        if not raw or len(raw) < 2:
            continue
        name, pl = parse(raw)
        if name is None:
            continue
        if name == 301:
            ws.send_binary(pn(300))
            continue
        rec = {"cmd": name, "len": len(raw), "hex": pl[:80].hex()}
        try:
            if name == 302:
                rec["status"] = R(pl).i8()
            elif name == 303:  # ALERT
                r = R(pl); rec["alert"] = r.utf16()
            elif name == 406:
                r = R(pl); rec["level"] = r.i8(); rec["pid"] = r.i64(); rec["name"] = r.utf16()
                rec["tail_hex"] = r.d[r.p:].hex()[:80]
            elif name == 413:  # LIST_BET_AMT resp
                r = R(pl); st = r.i8(); rec["status"] = st
                if st == 0:
                    cnt = r.i8(); rec["bets"] = [(i, r.i32()) for i in range(cnt)]
            elif name == 405:  # CREATE_RULE resp
                r = R(pl); rec["status"] = r.i8()
                if rec["status"] == 0:
                    rec["table_id"] = r.ascii()
            elif name == 401:
                rec["place"] = R(pl).ascii()
        except Exception as e:
            rec["decode_err"] = str(e)
        frames.append(rec)
    try:
        ws.close()
    except Exception:
        pass
    return frames


def main():
    user, pwd = sys.argv[1], sys.argv[2]
    real = http_login(user, pwd)
    if not real:
        print("LOGIN_FAIL")
        return 1
    print("== ACCOUNT THAT ==")
    print(json.dumps({"nick": real["nick"], "token": real["token"],
                      "memberName": real["ck"].get("memberName", "")[:20] + "...",
                      "memberPassword": real["ck"].get("memberPassword", "")[:20] + "...",
                      "JSESSIONID": real["ck"].get("JSESSIONID", "")[:20] + "..."},
                     ensure_ascii=False, indent=2))

    # ---------- A2/A3: credential của người chơi khác có lộ không ----------
    print("\n== A2) profile người chơi khác (showProfile) có lộ credential không ==")
    s = real["sess"]
    for pid in (53202011, 68480462, 45033593):
        try:
            p = s.get(f"{PROFILE_URL}?id={pid}", timeout=12)
            txt = p.text
            leaks = []
            for key in ("memberName", "memberPassword", "JSESSIONID"):
                if key in txt:
                    leaks.append(key)
            ids = sorted({int(x) for x in re.findall(r"showProfile\((\d+)\)", txt)})[:3]
            print(f"  pid={pid}: len={len(txt)} leak_keys={leaks or 'KHONG'} showProfile_ids={ids}")
        except Exception as e:
            print(f"  pid={pid}: ERR {e}")

    print("\n== A3) zone_player_list_helper.jsp ==")
    try:
        z = s.get(f"{ZONE_URL}?game_id=caro&room_id=0", timeout=12)
        txt = z.text
        leaks = [k for k in ("memberName", "memberPassword", "JSESSIONID", "password") if k.lower() in txt.lower()]
        pids = sorted({int(x) for x in re.findall(r"showProfile\((\d+)\)", txt) +
                       re.findall(r"showGameProfile\((\d+)", txt)})[:6]
        print(f"  len={len(txt)} leak_keys={leaks or 'KHONG'} pids={pids}")
    except Exception as e:
        print("  ERR", e)

    # ---------- A1 + B: guest session ----------
    print("\n== A1/B) session GUEST (chỉ memberName+memberPassword) ==")
    gck = {"memberName": real["ck"]["memberName"], "memberPassword": real["ck"]["memberPassword"]}
    gs = requests.Session()
    gs.headers.update({"User-Agent": UA})
    gs.cookies.update(gck)
    g = gs.get(GAME_URL, timeout=15)
    gtok = int(re.search(r"var\s+token\s*=\s*(-?\d+)", g.text).group(1))
    gnick = re.search(r"currentPlayerNickName\s*=\s*['\"]([^'\"]+)['\"]", g.text).group(1)
    gcookie = "; ".join(f"{k}={v}" for k, v in gs.cookies.items())
    print(f"  guest nick={gnick} token={gtok}")

    # B1: LIST_BET_AMT ; B2: CREATE_RULE
    frames = ws_session(gcookie, gnick, gtok, seconds=18,
                        actions=[pn(413), pn(405, i8(0) + i8(4) + asc("matchDuration") + utf("1800")
                                           + asc("turnDuration") + utf("60")
                                           + asc("accDuration") + utf("0")
                                           + asc("blockSoftware") + utf("0"))])
    for f in frames:
        if f["cmd"] in (302, 303, 405, 413, 406, 407, 401, 415):
            print("  ", json.dumps(f, ensure_ascii=False)[:220])

    # A1: so sánh frame 406 của chính ta khi là account thật vs guest
    print("\n== A1) frame 406 PHÁT RA từ account thật vs guest (có chứa credential?) ==")
    fr = ws_session(real["cookie"], real["nick"], real["token"], seconds=12)
    for f in fr:
        if f["cmd"] == 406:
            print("   REAL frame406:", json.dumps(f, ensure_ascii=False)[:200])
            break
    for f in frames:
        if f["cmd"] == 406:
            print("   GUEST frame406:", json.dumps(f, ensure_ascii=False)[:200])
            break

    # kiểm tra credential có xuất hiện trong bất kỳ frame nào không
    print("\n== A4) credential xuất hiện trong frame nào không? ==")
    secrets = {k: v for k, v in gck.items() if v}
    for tag, fl in (("REAL", fr), ("GUEST", frames)):
        for f in fl:
            h = f.get("hex", "")
            for k, v in secrets.items():
                if v[:16] in h:
                    print(f"   {tag} cmd={f['cmd']} chứa {k}!")
    print("   (không in gì ở trên = không lộ)")
    return 0


if __name__ == "__main__":
    sys.exit(main())

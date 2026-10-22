#!/usr/bin/env python3
"""
Kiểm tra cơ chế 'lưu credential' phía server gamevh:
  A. Login chuẩn -> lấy 4 cookie (JSESSIONID, clientIp, memberName, memberPassword)
  B. Session MỚI chỉ dùng memberName + memberPassword (bỏ JSESSIONID) -> GET /play/caro/0
     -> nếu vẫn có token + nickname => server tự đăng nhập bằng cookie credential
  C. Session MỚI chỉ dùng JSESSIONID -> so sánh
Đồng thời giải mã frame 415 (cmd chưa có trong CMD_MAP của caro.py).
"""
import re
import struct
import sys

import requests

UA = ("Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/139.0 Safari/537.36")
LOGIN_URL = "https://gamevh.net/login.jsp"
GAME_URL = "https://gamevh.net/play/caro/0"


def hdr():
    return {"User-Agent": UA, "Accept-Language": "vi-VN,vi;q=0.9"}


def parse_play(html):
    tm = re.search(r"var\s+token\s*=\s*(-?\d+)", html)
    nm = re.search(r"var\s+currentPlayerNickName\s*=\s*['\"]([^'\"]+)['\"]", html)
    return (int(tm.group(1)) if tm else 0,
            nm.group(1) if nm else None)


def main():
    user, pwd = sys.argv[1], sys.argv[2]

    s = requests.Session()
    s.headers.update(hdr())
    s.get(LOGIN_URL, timeout=15)
    r = s.post(LOGIN_URL, timeout=15,
               data={"redirect": "/", "USER_NAME": user, "PASSWORD": pwd,
                     "AUTO_LOGIN": "true", "LOGIN": "Đăng nhập"},
               headers={"Origin": "https://gamevh.net", "Referer": LOGIN_URL},
               allow_redirects=True)
    print("A) login ok:", "login.jsp" not in r.url)
    ck = {k: v for k, v in s.cookies.items()}
    for k, v in ck.items():
        print(f"   cookie {k:16s} = {v[:24]}{'...' if len(v) > 24 else ''}")

    tok, nick = parse_play(s.get(GAME_URL, timeout=15).text)
    print(f"   -> token={tok} nick={nick}")

    def try_cookies(name, cookie_dict):
        s2 = requests.Session()
        s2.headers.update(hdr())
        s2.cookies.update(cookie_dict)
        resp = s2.get(GAME_URL, timeout=15)
        t, n = parse_play(resp.text)
        logged = ("login.jsp" in resp.url) or (t == 0 and n is None)
        print(f"{name}: url_tail={resp.url[-32:]!r} token={t} nick={n} "
              f"=> {'REDIRECT_LOGIN' if logged else 'AUTHENTICATED'}")
        return (not logged) and t != 0

    print("\nB) session mới, chỉ memberName + memberPassword:")
    ok_b = try_cookies("   member-cred only", {"memberName": ck.get("memberName", ""),
                                               "memberPassword": ck.get("memberPassword", "")})
    print("\nC) session mới, chỉ JSESSIONID:")
    ok_c = try_cookies("   jsessionid only", {"JSESSIONID": ck.get("JSESSIONID", "")})
    print("\nD) session mới, memberName+memberPassword+clientIp:")
    ok_d = try_cookies("   full member set", {k: ck.get(k, "") for k in
                                              ("memberName", "memberPassword", "clientIp")})
    print(f"\nKET LUAN: memberName+memberPassword={'DANG DUNG' if ok_b else 'khong'} | "
          f"JSESSIONID={'DANG DUNG' if ok_c else 'khong'} | full={'DANG DUNG' if ok_d else 'khong'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

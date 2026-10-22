#!/usr/bin/env python3
"""
Thí nghiệm (trên account test3 - account test disposable):
 C1) Chụp credential pair ban đầu: memberName_A / memberPassword_A
 C2) ĐỔI MẬT KHẨU (change_password.jsp: OLD/NEW/RETYPE/CHANGE_PASSWORD)
     -> cặp credential có xoay vòng (memberPassword đổi) không?
     -> mật khẩu cũ / cặp cũ còn dùng được không?
 C3) ĐỔI TÊN ĐĂNG NHẬP (change_user_name.jsp: NICK_NAME/OLD_PASSWORD/SAVE)
     -> memberName có đổi không? đăng nhập bằng tên cũ/mới thế nào?
 C4) Khôi phục lại test3 (tên + mật khẩu gốc)
"""
import json
import re
import sys

import requests

UA = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
UA += "(KHTML, like Gecko) Chrome/139.0 Safari/537.36"
LOGIN_URL = "https://gamevh.net/login.jsp"
GAME_URL = "https://gamevh.net/play/caro/0"
PWD_URL = "https://gamevh.net/com/ftl/game/profile/change_password.jsp"
NAME_URL = "https://gamevh.net/com/ftl/game/profile/change_user_name.jsp"


def login(user, pwd):
    """-> dict {ok, nick, memberName, memberPassword}"""
    s = requests.Session()
    s.headers.update({"User-Agent": UA, "Accept-Language": "vi-VN,vi;q=0.9"})
    try:
        s.get(LOGIN_URL, timeout=12)
        r = s.post(LOGIN_URL, timeout=12,
                   data={"redirect": "/", "USER_NAME": user, "PASSWORD": pwd,
                         "AUTO_LOGIN": "true", "LOGIN": "Đăng nhập"},
                   headers={"Origin": "https://gamevh.net", "Referer": LOGIN_URL},
                   allow_redirects=True)
        if "login.jsp" in r.url:
            return {"ok": False}
        g = s.get(GAME_URL, timeout=12)
        m = re.search(r"currentPlayerNickName\s*=\s*['\"]([^'\"]+)['\"]", g.text)
        ck = {k: v for k, v in s.cookies.items()}
        return {"ok": True, "nick": m.group(1) if m else "?",
                "memberName": ck.get("memberName", ""), "memberPassword": ck.get("memberPassword", ""),
                "session": s}
    except Exception as e:
        return {"ok": False, "err": str(e)}


def replay(member_name, member_pw):
    """Replay cặp cookie credential -> có auth được không, ra nick gì?"""
    s = requests.Session()
    s.headers.update({"User-Agent": UA})
    s.cookies.update({"memberName": member_name, "memberPassword": member_pw})
    try:
        g = s.get(GAME_URL, timeout=12)
        m = re.search(r"currentPlayerNickName\s*=\s*['\"]([^'\"]+)['\"]", g.text)
        t = re.search(r"var\s+token\s*=\s*(-?\d+)", g.text)
        return {"auth": bool(m and t), "nick": m.group(1) if m else None}
    except Exception as e:
        return {"auth": False, "err": str(e)}


def change_password(sess, old, new):
    sess.get(PWD_URL, timeout=12)
    r = sess.post(PWD_URL, timeout=15,
                  data={"redirect": "", "OLD_PASSWORD": old, "NEW_PASSWORD": new,
                        "RETYPE_PASSWORD": new, "CHANGE_PASSWORD": "Đổi mật khẩu"},
                  headers={"Origin": "https://gamevh.net", "Referer": PWD_URL,
                           "Content-Type": "application/x-www-form-urlencoded"},
                  allow_redirects=True)
    txt = re.sub(r"<[^>]+>", " ", r.text)
    txt = re.sub(r"\s+", " ", txt)
    err = [kw for kw in ("Mật khẩu cũ", "không đúng", "sai", "Lỗi", "thất bại", "thành công",
                         "không hợp lệ", "tối thiểu", "trùng") if kw.lower() in txt.lower()]
    return {"status": r.status_code, "url": r.url, "signals": err,
            "snippet": txt[:260].strip()}


def change_username(sess, old_pwd, new_name):
    sess.get(NAME_URL, timeout=12)
    r = sess.post(NAME_URL, timeout=15,
                  data={"redirect": "/", "processMode": "", "NICK_NAME": new_name,
                        "OLD_PASSWORD": old_pwd, "SAVE": "Cập nhật"},
                  headers={"Origin": "https://gamevh.net", "Referer": NAME_URL,
                           "Content-Type": "application/x-www-form-urlencoded"},
                  allow_redirects=True)
    txt = re.sub(r"<[^>]+>", " ", r.text)
    txt = re.sub(r"\s+", " ", txt)
    err = [kw for kw in ("đã tồn tại", "tồn tại", "không đúng", "Lỗi", "thất bại",
                         "thành công", "không hợp lệ", "tối thiểu", "tồn tại") if kw.lower() in txt.lower()]
    return {"status": r.status_code, "url": r.url, "signals": err,
            "snippet": txt[:260].strip()}


def show(tag, d):
    print(f"   {tag}: ok={d.get('ok')} nick={d.get('nick')} "
          f"memberName={d.get('memberName','')[:18]} memberPassword={d.get('memberPassword','')[:18]}")


def main():
    user, old_pwd, new_pwd, new_name = sys.argv[1], sys.argv[2], sys.argv[3], sys.argv[4]
    print("== C1) credential ban đầu ==")
    a = login(user, old_pwd)
    show("login", a)
    if not a.get("ok"):
        print("không login được");
        return 1
    A_name, A_pw = a["memberName"], a["memberPassword"]
    print(f"   replay cặp A: {replay(A_name, A_pw)}")

    print("\n== C2) đổi mật khẩu ==")
    sess = a["session"]
    print("   ", change_password(sess, old_pwd, new_pwd))
    b = login(user, new_pwd)
    show("login MỚI", b)
    print(f"   login với mật khẩu CŨ: {login(user, old_pwd).get('ok')}")
    if b.get("ok"):
        print(f"   memberName đổi? {b['memberName'] != A_name}   "
              f"memberPassword đổi? {b['memberPassword'] != A_pw}")
        print(f"   replay cặp CŨ (A) còn dùng được? {replay(A_name, A_pw)}")
        print(f"   replay cặp MỚI (B): {replay(b['memberName'], b['memberPassword'])}")
        B_name, B_pw = b["memberName"], b["memberPassword"]
        sess_b = b["session"]

        print("\n== C3) đổi tên đăng nhập ==")
        print("   ", change_username(sess_b, new_pwd, new_name))
        c = login(new_name, new_pwd)
        show("login TÊN MỚI", c)
        print(f"   login TÊN CŨ + pwd mới: {login(user, new_pwd).get('ok')}")
        if c.get("ok"):
            print(f"   memberName đổi? {c['memberName'] != B_name}   "
                  f"memberPassword đổi? {c['memberPassword'] != B_pw}")
            print(f"   replay cặp B (tên cũ) còn dùng? {replay(B_name, B_pw)}")
            print(f"   replay cặp C (tên mới): {replay(c['memberName'], c['memberPassword'])}")
            C_name, C_pw = c["memberName"], c["memberPassword"]

            print("\n== C4) khôi phục test3 ==")
            s_c = c["session"]
            print("    đổi tên về:", change_username(s_c, new_pwd, user)["signals"])
            d = login(user, new_pwd)
            print(f"   login tên gốc + pwd mới: ok={d.get('ok')}")
            if d.get("ok"):
                print("    đổi pwd về gốc:", change_password(d["session"], new_pwd, old_pwd)["signals"])
                e = login(user, old_pwd)
                show("khôi phục", e)
    return 0


if __name__ == "__main__":
    sys.exit(main())

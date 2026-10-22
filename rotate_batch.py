#!/usr/bin/env python3
"""
Đổi mật khẩu 10 tài khoản test sang mật khẩu mới (mặc định "0") để kiểm tra
chính sách mật khẩu của gamevh.net, rồi khôi phục lại mật khẩu gốc.

python3 rotate_batch.py --new 0 --restore
"""
import argparse
import html
import re
import sys
import time

import requests

UA = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
UA += "(KHTML, like Gecko) Chrome/139.0 Safari/537.36"
LOGIN_URL = "https://gamevh.net/login.jsp"
GAME_URL = "https://gamevh.net/play/caro/0"
PWD_URL = "https://gamevh.net/com/ftl/game/profile/change_password.jsp"


def new_session():
    s = requests.Session()
    s.headers.update({"User-Agent": UA, "Accept-Language": "vi-VN,vi;q=0.9"})
    return s


def login(user, pwd):
    s = new_session()
    try:
        s.get(LOGIN_URL, timeout=15)
        r = s.post(LOGIN_URL, timeout=15,
                   data={"redirect": "/", "USER_NAME": user, "PASSWORD": pwd,
                         "AUTO_LOGIN": "true", "LOGIN": "Đăng nhập"},
                   headers={"Origin": "https://gamevh.net", "Referer": LOGIN_URL},
                   allow_redirects=True)
        if "login.jsp" in r.url:
            return None
        g = s.get(GAME_URL, timeout=15)
        ck = {k: v for k, v in s.cookies.items()}
        m = re.search(r"currentPlayerNickName\s*=\s*['\"]([^'\"]+)['\"]", g.text)
        return {"session": s, "nick": m.group(1) if m else "?",
                "memberName": ck.get("memberName", ""), "memberPassword": ck.get("memberPassword", "")}
    except Exception as e:
        return {"err": str(e)}


def change_password(sess, old, new):
    sess.get(PWD_URL, timeout=15)
    r = sess.post(PWD_URL, timeout=20,
                  data={"redirect": "", "OLD_PASSWORD": old, "NEW_PASSWORD": new,
                        "RETYPE_PASSWORD": new, "CHANGE_PASSWORD": "Đổi mật khẩu"},
                  headers={"Origin": "https://gamevh.net", "Referer": PWD_URL,
                           "Content-Type": "application/x-www-form-urlencoded"},
                  allow_redirects=True)
    txt = html.unescape(re.sub(r"<script.*?</script>", " ", r.text, flags=re.S | re.I))
    txt = html.unescape(re.sub(r"<[^>]+>", " ", txt))
    txt = re.sub(r"\s+", " ", txt).strip()
    msgs = re.findall(r"[^.]{0,120}(?:thành công|không đúng|không hợp lệ|tối thiểu|trùng|"
                      r"lỗi|Lỗi|thất bại|không thể|yêu cầu)[^.]{0,120}", txt)
    return {"ok_http": r.status_code, "url": r.url,
            "messages": [m.strip() for m in msgs][:3],
            "head": txt[:180]}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--new", default="0")
    ap.add_argument("--old", default="nhat123456")
    ap.add_argument("--restore", action="store_true")
    ap.add_argument("--accounts", default="test2,test3,test4,test6,test7,test8,test9,test10,test11,test5")
    args = ap.parse_args()
    accs = [a.strip() for a in args.accounts.split(",") if a.strip()]

    print(f"=== Đổi mật khẩu {len(accs)} tài khoản: '{args.old}' -> '{args.new}' ===\n")
    results = []
    for a in accs:
        row = {"acc": a}
        s = login(a, args.old)
        if not s:
            row["step"] = "login_old_fail"
            print(f"  {a:8s} ✗ không đăng nhập được bằng mật khẩu gốc")
            results.append(row)
            continue
        row["memberName_before"] = s["memberName"]
        row["memberPassword_before"] = s["memberPassword"]
        ch = change_password(s["session"], args.old, args.new)
        row["change"] = ch
        ok_login = login(a, args.new)
        row["login_new_ok"] = bool(ok_login)
        row["old_still_ok"] = bool(login(a, args.old))
        if ok_login:
            row["memberName_after"] = ok_login["memberName"]
            row["memberPassword_after"] = ok_login["memberPassword"]
        verdict = "ĐỔI OK" if row["login_new_ok"] else "TỪ CHỐI"
        print(f"  {a:8s} {verdict:9s} | server: {ch['messages'][:1] or ch['head'][:90]}")
        if row["login_new_ok"]:
            print(f"           login mới ok={row['login_new_ok']} | mật khẩu cũ còn dùng được: {row['old_still_ok']} "
                  f"| memberPassword đổi: {row['memberPassword_before'] != row.get('memberPassword_after')}")
        results.append(row)
        time.sleep(0.6)

    if args.restore:
        print(f"\n=== Khôi phục mật khẩu gốc '{args.old}' ===")
        for row in results:
            if row.get("login_new_ok"):
                s = login(row["acc"], args.new)
                if not s:
                    print(f"  {row['acc']:8s} ✗ không login được bằng mật khẩu mới")
                    continue
                ch = change_password(s["session"], args.new, args.old)
                back = login(row["acc"], args.old)
                same = bool(back) and back["memberPassword"] == row["memberPassword_before"]
                print(f"  {row['acc']:8s} khôi phục: login_old_ok={bool(back)} | "
                      f"memberPassword về đúng giá trị cũ: {same}")
                time.sleep(0.6)

    ok = sum(1 for r in results if r.get("login_new_ok"))
    print(f"\nTỔNG: {ok}/{len(accs)} tài khoản chấp nhận mật khẩu '{args.new}'")
    return 0


if __name__ == "__main__":
    sys.exit(main())

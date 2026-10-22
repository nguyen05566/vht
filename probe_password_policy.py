import html, re, requests, sys
UA = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/139.0 Safari/537.36"
PWD_URL = "https://gamevh.net/com/ftl/game/profile/change_password.jsp"
ACC = sys.argv[1]
def login(u,p):
    s=requests.Session(); s.headers.update({"User-Agent":UA})
    s.get("https://gamevh.net/login.jsp",timeout=15)
    r=s.post("https://gamevh.net/login.jsp",timeout=15,data={"redirect":"/","USER_NAME":u,"PASSWORD":p,"AUTO_LOGIN":"true","LOGIN":"Đăng nhập"},headers={"Origin":"https://gamevh.net","Referer":"https://gamevh.net/login.jsp"},allow_redirects=True)
    return None if "login.jsp" in r.url else s
def msg_of(txt):
    m=re.findall(r'"((?:Mật khẩu|mật khẩu)[^"]{10,160})"', txt)
    return html.unescape(m[-1]) if m else "?"
def try_pwd(sess, old, new):
    sess.get(PWD_URL,timeout=15)
    t=sess.post(PWD_URL,timeout=20,data={"redirect":"","OLD_PASSWORD":old,"NEW_PASSWORD":new,"RETYPE_PASSWORD":new,"CHANGE_PASSWORD":"Đổi mật khẩu"},headers={"Origin":"https://gamevh.net","Referer":PWD_URL,"Content-Type":"application/x-www-form-urlencoded"},allow_redirects=True).text
    return msg_of(t)
cur = sys.argv[2]
tests = ["0","00","000","0000","00000","000000","0000000","00000000","abcdef","abcdefgh","abcdefghi",
         "Abcdefgh","Abcdefghi","abc12345","Abc12345","abc123456","Abc123456","12345678","Aa123456b",
         "aa11aaaa","Aa11aaaa","aaa11111","AAA11111","aA1!aaaa","nhat123456"]
for cand in tests:
    s = login(ACC, cur)
    if not s:
        print(f"  {cand:12s} -> KHÔNG LOGIN ĐƯỢC (pwd hiện tại sai?)"); break
    m = try_pwd(s, cur, cand)
    ok = "thành công" in m.lower()
    print(f"  {cand:12s} -> {m[:90]}")
    if ok:
        cur = cand
print("\nPWD CUỐI:", cur)

import html, re, requests
UA = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/139.0 Safari/537.36"
PWD_URL = "https://gamevh.net/com/ftl/game/profile/change_password.jsp"
def login(u,p):
    s=requests.Session(); s.headers.update({"User-Agent":UA})
    s.get("https://gamevh.net/login.jsp",timeout=15)
    r=s.post("https://gamevh.net/login.jsp",timeout=15,data={"redirect":"/","USER_NAME":u,"PASSWORD":p,"AUTO_LOGIN":"true","LOGIN":"Đăng nhập"},headers={"Origin":"https://gamevh.net","Referer":"https://gamevh.net/login.jsp"},allow_redirects=True)
    if "login.jsp" in r.url: return None
    g=s.get("https://gamevh.net/play/caro/0",timeout=15)
    ck={k:v for k,v in s.cookies.items()}
    m=re.search(r"currentPlayerNickName\s*=\s*['\"]([^'\"]+)['\"]",g.text)
    return {"s":s,"nick":m.group(1) if m else "?","mn":ck.get("memberName",""),"mp":ck.get("memberPassword","")}
def msg(t):
    m=re.findall(r'"((?:Mật khẩu|mật khẩu)[^"]{10,160})"',t); return html.unescape(m[-1]) if m else "?"
cur="nhat123456"
for cand in ["a","ab","abc","abcd","abcde","abcdef","a1","ab1","abc1","abcd1","abcde1"]:
    r=login("test2",cur)
    if not r: print(f"  {cand:8s} login fail"); break
    r["s"].get(PWD_URL,timeout=15)
    t=r["s"].post(PWD_URL,timeout=20,data={"redirect":"","OLD_PASSWORD":cur,"NEW_PASSWORD":cand,"RETYPE_PASSWORD":cand,"CHANGE_PASSWORD":"Đổi mật khẩu"},headers={"Origin":"https://gamevh.net","Referer":PWD_URL,"Content-Type":"application/x-www-form-urlencoded"},allow_redirects=True).text
    m=msg(t); ok="thành công" in m.lower()
    print(f"  len={len(cand):2d} {cand:8s} -> {m[:70]}")
    if ok: cur=cand
# khôi phục
r=login("test2",cur)
if r:
    r["s"].get(PWD_URL,timeout=15)
    t=r["s"].post(PWD_URL,timeout=20,data={"redirect":"","OLD_PASSWORD":cur,"NEW_PASSWORD":"nhat123456","RETYPE_PASSWORD":"nhat123456","CHANGE_PASSWORD":"Đổi mật khẩu"},headers={"Origin":"https://gamevh.net","Referer":PWD_URL,"Content-Type":"application/x-www-form-urlencoded"},allow_redirects=True).text
    print("\nkhôi phục:", msg(t))
f=login("test2","nhat123456")
print("verify test2:", f["nick"], f["mn"], f["mp"])

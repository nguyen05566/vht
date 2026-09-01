#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""BOT Othello GameVH — engine Edax (persistent process). 1 file."""
import asyncio, os, re, struct, time, subprocess, shutil, random, threading
import requests, websockets

# ======================== EDAX ========================
EDAX_DIR = os.environ.get("EDAX_DIR", "/tmp/edax")
EDAX_SRC = "https://github.com/abulmo/edax-reversi/archive/refs/tags/v4.6.tar.gz"
EDAX_EVAL = "https://github.com/abulmo/edax-reversi/releases/download/v4.4/eval.7z"

def ensure_edax():
    for a in ["x86-64-v3","x86-64-v2","x86-64"]:
        p = os.path.join(EDAX_DIR,"bin",f"lEdax-{a}")
        if os.path.exists(p) and os.path.exists(os.path.join(EDAX_DIR,"data","eval.dat")):
            os.chmod(p,0o755); return p
    import tarfile, urllib.request
    os.makedirs(EDAX_DIR, exist_ok=True)
    src = os.path.join(EDAX_DIR, "src")
    if not os.path.exists(os.path.join(src, "all.c")):
        print("[EDAX] tải nguồn...")
        tgz = os.path.join(EDAX_DIR, "edax.tar.gz")
        urllib.request.urlretrieve(EDAX_SRC, tgz)
        with tarfile.open(tgz) as t: t.extractall(EDAX_DIR)
        root = next(os.path.join(EDAX_DIR,d) for d in os.listdir(EDAX_DIR) if d.startswith("edax-reversi-"))
        for n in os.listdir(root):
            d = os.path.join(EDAX_DIR,n)
            if not os.path.exists(d): shutil.move(os.path.join(root,n), d)
    os.makedirs(os.path.join(EDAX_DIR,"bin"), exist_ok=True)
    comp = shutil.which("gcc") or shutil.which("clang")
    for a in ["x86-64-v3","x86-64-v2","x86-64"]:
        subprocess.run(["make","build",f"ARCH={a}",f"COMP={comp}","OS=linux",f"CC={comp}"],
                       cwd=src, capture_output=True, text=True)
        p = os.path.join(EDAX_DIR,"bin",f"lEdax-{a}")
        if os.path.exists(p):
            os.chmod(p,0o755)
            if subprocess.run([p,"-v"],capture_output=True,cwd=EDAX_DIR).returncode in (0,1):
                print(f"[EDAX] OK: {a}"); break
            os.remove(p)
    data = os.path.join(EDAX_DIR,"data")
    if not os.path.exists(os.path.join(data,"eval.dat")):
        print("[EDAX] tải eval.dat...")
        pkg = os.path.join(EDAX_DIR,"eval.7z")
        if not os.path.exists(pkg): urllib.request.urlretrieve(EDAX_EVAL, pkg)
        try: import py7zr
        except ImportError: subprocess.run(["pip","install","-q","py7zr"],check=True); import py7zr
        with py7zr.SevenZipFile(pkg) as z: z.extractall(EDAX_DIR)
        nd = os.path.join(data,"data","eval.dat")
        if os.path.exists(nd): shutil.move(nd, os.path.join(data,"eval.dat"))
    for a in ["x86-64-v3","x86-64-v2","x86-64"]:
        p = os.path.join(EDAX_DIR,"bin",f"lEdax-{a}")
        if os.path.exists(p): return p
    return None

class Edax:
    def __init__(self, level=18):
        self.level=level; self.proc=None; self._lines=[]; self._lock=threading.Lock()
    def start(self):
        b=ensure_edax()
        if not b: return False
        self.proc=subprocess.Popen([b,"-level",str(self.level)],cwd=EDAX_DIR,
            stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.STDOUT,text=True,bufsize=1)
        threading.Thread(target=self._reader,daemon=True).start()
        time.sleep(0.5); print(f"[EDAX] sẵn sàng (level {self.level})"); return True
    def _reader(self):
        for line in self.proc.stdout:
            with self._lock: self._lines.append(line.rstrip())
    def _cmd(self,t):
        if self.proc and self.proc.poll() is None: self.proc.stdin.write(t+"\n"); self.proc.stdin.flush()
    def best_move(self, board64, side):
        with self._lock: self._lines.clear()
        self._cmd(f"setboard {''.join(board64)} {side}")
        time.sleep(0.05); self._cmd("go")
        end=time.time()+self.level*2+20
        while time.time()<end:
            with self._lock: cur=list(self._lines)
            for l in cur:
                if "Edax plays" in l:
                    m=re.search(r"Edax plays\s+([A-Ha-h])\s*([1-8])",l)
                    if m: return (ord(m.group(1).upper())-65)+8*(int(m.group(2))-1)
                if "cannot move" in l.lower() or "game over" in l.lower(): return None
            time.sleep(0.02)
        return None
    def stop(self):
        try: self._cmd("quit"); time.sleep(0.2)
        except: pass
        if self.proc: self.proc.terminate()

# ======================== OTHELLO LOGIC ========================
def decode_packed(data):
    cells=[]
    for byte in data[:16]:
        if byte<0: byte+=256
        for s in (6,4,2,0):
            v=(byte>>s)&3; cells.append(v if v<2 else None)
    return cells[:64]

def decode_start(tail):
    for i in range(len(tail)-2):
        if tail[i]==8 and tail[i+1]==8:
            dlen=tail[i+3] if i+3<len(tail) else 16
            bdata=tail[i+4:i+4+dlen]; ct=tail[i+4+dlen] if i+4+dlen<len(tail) else 1
            return decode_packed(bdata[:16]), ct
    c=[None]*64; c[27]=0;c[28]=1;c[35]=1;c[36]=0; return c,1

def apply_move(cells, pos, color):
    x,y=pos%8,pos//8; cells[pos]=color
    for dx in(-1,0,1):
        for dy in(-1,0,1):
            if dx==0 and dy==0: continue
            flip=[]; nx,ny=x+dx,y+dy
            while 0<=nx<8 and 0<=ny<8 and cells[ny*8+nx]==(1-color):
                flip.append(ny*8+nx); nx+=dx; ny+=dy
            if 0<=nx<8 and 0<=ny<8 and cells[ny*8+nx]==color and flip:
                for f in flip: cells[f]=color

def to_edax(cells, my_color):
    return (['X' if c==1 else 'O' if c==0 else '-' for c in cells], 'X' if my_color==1 else 'O')

def pboard(cells):
    for r in range(8):
        print(f"  {r+1} {' '.join('●' if cells[r*8+c]==1 else '○' if cells[r*8+c]==0 else '·' for c in range(8))}")

# ======================== GAMEVH ========================
WS_URL="wss://gamevh.net/ws/gameServer"
GAME_URL="https://gamevh.net/play/othello/0"; LOGIN_URL="https://gamevh.net/login.jsp"

# Tk tích hợp sẵn
USER = os.environ.get("OTHELLO_USER", "ngan6").strip()
PWWD = os.environ.get("OTHELLO_PW", "")

VERSION="5.0.2"; GAME_ID="othello"
TARGET_BET=int(os.environ.get("OTHELLO_BET","300"))
try:
    ENGINE_LEVEL=max(1, min(60, int(os.environ.get("OTHELLO_ENGINE_LEVEL", "18"))))
except ValueError:
    ENGINE_LEVEL=18
CMD_MAP={300:"PONG",301:"PING",302:"LOGIN",401:"ENTER_PLACE",405:"CREATE_RULE",
    406:"PLAYER_ENTERED",407:"PLAYER_EXITED",413:"LIST_BET_AMT",416:"SLOT_IN_TABLE_CHANGED",
    417:"START_MATCH",418:"GAMEOVER",420:"SET_TURN",433:"GET_TABLE_DATA_EX",502:"PLAY",529:"MOVE"}

class BR:
    def __init__(s,d): s.d=d; s.p=0
    def u8(s):
        v=s.d[s.p] if s.p<len(s.d) else 0; s.p+=1; return v
    def i8(s):
        v=struct.unpack_from('>b',s.d,s.p)[0] if s.p<len(s.d) else 0; s.p+=1; return v
    def i16(s):
        v=struct.unpack_from('>h',s.d,s.p)[0] if s.p+2<=len(s.d) else 0; s.p+=2; return v
    def i32(s):
        v=struct.unpack_from('>i',s.d,s.p)[0] if s.p+4<=len(s.d) else 0; s.p+=4; return v
    def ascii(s):
        n=s.u8(); n=min(n,len(s.d)-s.p); r=s.d[s.p:s.p+n].decode('ascii','replace'); s.p+=n; return r
    def utf(s):
        n=s.i16(); bl=min(n*2,len(s.d)-s.p); r=s.d[s.p:s.p+bl].decode('utf-16-be','replace'); s.p+=bl; return r
    def cmd(s):
        f=s.i8()
        if f<0:
            n=-f; n=min(n,len(s.d)-s.p); r=s.d[s.p:s.p+n].decode('ascii','replace'); s.p+=n; return r
        sc=s.u8(); return CMD_MAP.get((f<<8)|sc,f"C_{(f<<8)|sc}")

class BW:
    def __init__(s): s.x=[]
    def u8(s,v): s.x.append(struct.pack('>B',v))
    def i8(s,v): s.x.append(struct.pack('>b',v))
    def i16(s,v): s.x.append(struct.pack('>h',v))
    def i32(s,v): s.x.append(struct.pack('>i',v))
    def ascii(s,t):
        e=t.encode('ascii','replace'); s.u8(len(e)); s.x.append(e)
    def utf(s,t):
        e=t.encode('utf-16-be'); s.i16(len(e)//2); s.x.append(e)
    def cmd(s,c):
        cid=next((k for k,v in CMD_MAP.items() if v==c),None)
        if cid: s.x.append(struct.pack('>H',cid))
    def build(s): return b''.join(s.x)

# ======================== BOT ========================
def http_login():
    if not USER or not PWWD:
        print("[!] Thiếu OTHELLO_USER hoặc OTHELLO_PW"); return None
    se=requests.Session()
    se.headers.update({'User-Agent':'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/139.0 Safari/537.36'})
    se.get(LOGIN_URL,timeout=10)
    r=se.post(LOGIN_URL,timeout=10,data={'redirect':'/','USER_NAME':USER,'PWD':PWWD,'AUTO_LOGIN':'true','LOGIN':'Đăng nhập'},
        headers={'Origin':'https://gamevh.net','Referer':LOGIN_URL,'Content-Type':'application/x-www-form-urlencoded'},allow_redirects=True)
    if 'login.jsp' in r.url: print("[!] FAIL"); return None
    g=se.get(GAME_URL,timeout=10)
    ck='; '.join(f'{k}={v}' for k,v in se.cookies.items())
    tm=re.search(r'var\s+token\s*=\s*(-?\d+)',g.text)
    nm=re.search(r"var\s+currentPlayerNickName\s*=\s*'([^']+)'",g.text)
    pm=re.search(r'var\s+placePath\s*=\s*"([^"]+)"',g.text)
    if not tm or not nm: print("[!] no token"); return None
    print(f"[OK] {nm.group(1)} path={pm.group(1) if pm else '?'}")
    return ck,int(tm.group(1)),nm.group(1),(pm.group(1) if pm else "Lobby.othello.0")

async def _ws(url, ck):
    h={"Cookie":ck,"Origin":"https://gamevh.net","User-Agent":"Mozilla/5.0"}
    for kw in ({"additional_headers":h},{"extra_headers":h}):
        try: return await websockets.connect(url,max_size=2**20,ping_interval=None,**kw)
        except TypeError: continue
        except: continue
    raise Exception("WS fail")

async def main():
    info=http_login()
    if not info: return
    ck,tok,nick,path=info
    ws=await _ws(WS_URL,ck)
    async def send(b): await ws.send(b)
    w=BW(); w.cmd("LOGIN"); w.ascii(nick); w.i32(tok); w.ascii(VERSION); w.ascii(""); w.ascii(GAME_ID); w.i8(1); await send(w.build())
    w=BW(); w.cmd("ENTER_PLACE"); w.ascii(path); w.utf(""); w.i8(1); await send(w.build())
    await asyncio.sleep(1); w=BW(); w.cmd("LIST_BET_AMT"); await send(w.build())

    edax=Edax(level=ENGINE_LEVEL)
    if not edax.start(): print("[!] Edax fail"); return
    my_seat=-1; in_game=False; cells=[None]*64; my_color=1; pending_turn=None
    end=time.time()+int(os.environ.get("OTHELLO_SECONDS","18000"))

    async def play_turn(to):
        print(f"[MY TURN] {to}s")
        b64,side=to_edax(cells,my_color)
        pos=edax.best_move(b64,side)
        if pos is not None:
            x,y=pos%8,pos//8; print(f"[EDAX] {chr(65+x)}{y+1} pos={pos}")
            apply_move(cells,pos,my_color)
            await asyncio.sleep(random.uniform(1,3))
            w=BW(); w.cmd("PLAY"); w.i16(pos); await send(w.build())
            print(f"[PLAY] đã gửi {chr(65+x)}{y+1}")
        else: print("[EDAX] không nước (pass?)")

    while time.time()<end:
        try: raw=await asyncio.wait_for(ws.recv(),timeout=2.0)
        except asyncio.TimeoutError: continue
        except Exception as e: print("[!]",e); break
        if not isinstance(raw,bytes): continue
        r=BR(raw); c=r.cmd(); tail=r.d[r.p:]

        if c=="LIST_BET_AMT":
            r.i8(); n=r.i8(); bets=[r.i32() for _ in range(n)]
            bid=bets.index(TARGET_BET) if TARGET_BET in bets else 0
            print(f"[bàn] mức cược {bets[bid]} x (id={bid})")
            w=BW(); w.cmd("CREATE_RULE"); w.i8(bid); w.i8(2)
            w.ascii("matchDuration"); w.utf("1800"); w.ascii("turnDuration"); w.utf("60")
            await send(w.build())
        elif c=="CREATE_RULE":
            st=r.i8(); tid=r.ascii() if st==0 else ""
            print(f"[bàn] {tid}")
            if st==0: await asyncio.sleep(0.5); w=BW(); w.cmd("GET_TABLE_DATA_EX"); w.ascii(""); await send(w.build())
        elif c=="GET_TABLE_DATA_EX":
            fb=r.i8()
            if fb==0:
                try:
                    sc=r.u8()
                    for _ in range(sc):
                        r.u8();r.ascii();r.u8();cc=r.u8()
                        for _ in range(cc): r.u8();r.ascii();r.utf();r.u8();r.u8()
                    r.u8(); my_seat=r.i8(); print(f"[table] ghế {my_seat}")
                except: pass
        elif c=="START_MATCH":
            in_game=True
            cells,ct=decode_start(tail)
            my_color=my_seat
            print(f"\n[VÁN] tôi ghế {my_seat} = {'ĐEN' if my_color==1 else 'TRẮNG'} | đi {'trước' if ct==my_color else 'sau'} (ct={ct})")
            pboard(cells)
            if pending_turn is not None:
                tt=pending_turn; pending_turn=None
                print(f"[VÁN] lượt đi trước đã đến trước START_MATCH → chơi sau khi bàn sẵn sàng")
                await play_turn(tt)
        elif c=="SET_TURN":
            ts=r.i8(); to=r.i16()
            if ts==my_seat:
                if in_game:
                    await play_turn(to)
                else:
                    pending_turn=to
                    print(f"[TURN] đến lượt (seat {ts}) nhưng chưa START_MATCH → chờ")
        elif c=="MOVE":
            pos=r.i16(); pc=r.u8()
            if pos>=0 and in_game:
                if pc==my_color:
                    print(f"[MOVE] echo nước mình {chr(65+pos%8)}{pos//8+1} (đã apply khi đi, bỏ qua)")
                else:
                    apply_move(cells,pos,pc)
                    print(f"[MOVE] đối thủ {chr(65+pos%8)}{pos//8+1}")
        elif c in ("GAMEOVER","DROP"): in_game=False; print(f"[{c}]")
        elif c=="PING": w=BW(); w.cmd("PONG"); await send(w.build())

    edax.stop(); await ws.close(); print("[done]")

if __name__=="__main__": asyncio.run(main())

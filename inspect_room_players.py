#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
inspect_room_players.py - Quét TOÀN BỘ danh sách người đang trực tuyến trên GameVH
Thu thập:
- Người chơi đang ở các sảnh phòng (CMD 406 PLAYER_ENTERED)
- Người chơi đang tạo bàn / đang thi đấu (CMD 312 TABLE_BROADCAST)
- Top đại gia xu & Top người chơi trên trang chủ GameVH
- Thông tin chi tiết: Player ID, Nickname, Xu, Điểm số, Cấp độ / Rank, W/D/L, Tiến độ EXP
- Tự động xuất Markdown, CSV, JSON lưu vào repository
"""

import argparse
import csv
import json
import os
import re
import struct
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
import requests
import websocket
from bs4 import BeautifulSoup

WS_URL = "wss://gamevh.net/ws/gameServer"
LOGIN_URL = "https://gamevh.net/login.jsp"
PROFILE_URL = "https://gamevh.net/com/ftl/game/profile/player_profile.jsp"
HOME_URL = "https://gamevh.net/"

UA = ("Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/139.0 Safari/537.36")

ALL_GAMES = [
    "xiangqi", "mystery_xiangqi", "caro", "0", "1", "chan",
    "maubinh", "sam", "chess", "tamcuc", "blackjack", "xito", "poker", "othello"
]

GAME_NAMES = {
    "xiangqi": "Cờ tướng",
    "mystery_xiangqi": "Cờ úp",
    "caro": "Cờ caro",
    "0": "Phỏm",
    "1": "Tiến lên",
    "chan": "Chắn",
    "maubinh": "Mậu binh",
    "sam": "Sâm lốc",
    "chess": "Cờ vua",
    "tamcuc": "Tam cúc",
    "blackjack": "Xì dách",
    "xito": "Xì tố",
    "poker": "Poker",
    "othello": "Othello"
}


def pack_num(cmd, payload=b""):
    return struct.pack(">H", cmd) + payload

def asc(s):
    e = s.encode("ascii", "replace")[:255]
    return struct.pack(">b", len(e)) + e

def p_str(s):
    e = s.encode("utf-16-be")
    return struct.pack(">h", len(e)//2) + e


def get_player_full_profile(player_id, session=None):
    """Lấy chi tiết cấp độ, điểm, tỷ lệ thắng/hòa/thua từ player_profile.jsp"""
    if session is None:
        session = requests.Session()
        session.headers.update({"User-Agent": UA})

    url = f"{PROFILE_URL}?playerId={player_id}"
    try:
        r = session.get(url, timeout=5)
        soup = BeautifulSoup(r.text, "html.parser")
        
        nick_el = soup.find("div", class_="nick_name")
        bal_el = soup.find("div", class_="chipBalance")
        nick = nick_el.get_text(strip=True) if nick_el else ""
        bal_str = bal_el.get_text(strip=True) if bal_el else "0"
        bal = int(re.sub(r"[^\d]", "", bal_str) or 0)

        stats_table = soup.find("table", class_="game_stats")
        games = {}
        if stats_table:
            rows = stats_table.find_all("tr")[1:]
            for row in rows:
                cols = [c.get_text(strip=True) for c in row.find_all("td")]
                if len(cols) >= 6:
                    game_name = cols[0]
                    games[game_name] = {
                        "win": cols[1],
                        "draw": cols[2],
                        "lost": cols[3],
                        "score": cols[4],
                        "level": (cols[5] + " " + cols[6]).strip() if len(cols) > 6 else cols[5],
                        "progress": cols[-1]
                    }

        return {
            "player_id": player_id,
            "nick": nick,
            "balance": bal,
            "games": games
        }
    except Exception as e:
        return {"player_id": player_id, "error": str(e), "games": {}}


def scan_homepage_top_players(session):
    """Thu thập danh sách top đại gia và người chơi nổi bật từ trang chủ"""
    players = {}
    try:
        r = session.get(HOME_URL, timeout=8)
        pids = set(re.findall(r"showProfile\((\d+)\)", r.text) + re.findall(r"showGameProfile\((\d+)", r.text))
        print(f"[*] 🌐 Quét trang chủ: tìm thấy {len(pids)} người chơi nổi bật / đại gia.")
        for pid_str in pids:
            pid = int(pid_str)
            players[pid] = {
                "player_id": pid,
                "nick": "",
                "balance": 0,
                "score": 0,
                "game": "Trang chủ / Top",
                "room": "Lobby",
                "source": "homepage"
            }
    except Exception as e:
        print(f"⚠️ Lỗi quét trang chủ: {e}")
    return players


def scan_room_ws(cookie, user_nick, token, game_id, room_id, duration, my_pid):
    """Quét 1 phòng qua WebSocket bắt gói 406 (Sảnh) và 312 (Tạo bàn/Đang đấu)"""
    place_path = f"Lobby.{game_id}.{room_id}"
    ws = None
    players = {}
    try:
        ws = websocket.create_connection(
            WS_URL, timeout=6,
            header=[f"Cookie: {cookie}", "Origin: https://gamevh.net", f"User-Agent: {UA}"],
            cookie=cookie
        )
        # Login
        ws.send_binary(pack_num(302, asc(user_nick) + struct.pack(">i", token) + asc("5.0.2") + asc("") + asc(game_id) + struct.pack(">b", 1)))

        ws_ok = False
        deadline = time.time() + 4
        while time.time() < deadline:
            raw = ws.recv()
            if not raw: continue
            if struct.unpack_from(">H", raw, 0)[0] == 302:
                ws_ok = True
                break
        if not ws_ok:
            if ws: ws.close()
            return players

        # Vào phòng
        ws.send_binary(pack_num(401, asc(place_path) + p_str("") + struct.pack(">b", 1)))

        scan_deadline = time.time() + duration
        while time.time() < scan_deadline:
            try:
                raw = ws.recv()
            except Exception:
                break
            if not raw or len(raw) < 2: continue
            cmd = struct.unpack_from(">H", raw, 0)[0]
            if cmd == 301:
                ws.send_binary(pack_num(300))
                continue
            
            # Gói 406: Người chơi vào sảnh/phòng
            if cmd == 406:
                try:
                    offset = 2
                    pl_level = struct.unpack_from(">b", raw, offset)[0]; offset += 1
                    pid = struct.unpack_from(">q", raw, offset)[0]; offset += 8
                    name_len = struct.unpack_from(">h", raw, offset)[0]; offset += 2
                    pname = raw[offset:offset + name_len*2].decode("utf-16-be", errors="replace"); offset += name_len*2
                    
                    bal = 0; score = 0
                    if len(raw) >= offset + 16:
                        bal = struct.unpack_from(">q", raw, offset)[0]; offset += 8
                        score = struct.unpack_from(">q", raw, offset)[0]; offset += 8

                    if pid > 0 and pid != my_pid and pid not in players:
                        players[pid] = {
                            "player_id": pid,
                            "nick": pname,
                            "balance": bal,
                            "score": score,
                            "game": GAME_NAMES.get(game_id, game_id),
                            "room": f"Phòng {room_id}",
                            "source": "room_lobby"
                        }
                        print(f"   👤 [Sảnh {GAME_NAMES.get(game_id, game_id)}] ID={pid} | Nick={pname} | Xu={bal:,} | Score={score:,}")
                except Exception:
                    pass

            # Gói 312: Người chơi đang tạo bàn thi đấu trên server
            elif cmd == 312:
                try:
                    offset = 2
                    name_len = struct.unpack_from(">h", raw, offset)[0]; offset += 2
                    hname = raw[offset:offset + name_len*2].decode("utf-16-be", errors="replace"); offset += name_len*2
                    offset += 7 # skip table_id + unknown
                    g_len = struct.unpack_from(">b", raw, offset)[0]; offset += 1
                    g_code = raw[offset:offset + g_len].decode("ascii", errors="replace"); offset += g_len
                    bet_val = struct.unpack_from(">i", raw, offset)[0]; offset += 4
                    hpid = struct.unpack_from(">q", raw, offset)[0]; offset += 8
                    
                    if hpid > 0 and hpid != my_pid and hpid not in players:
                        players[hpid] = {
                            "player_id": hpid,
                            "nick": hname,
                            "balance": 0,
                            "score": 0,
                            "game": f"{GAME_NAMES.get(g_code, g_code)} (Bàn cược {bet_val:,} xu)",
                            "room": "Đang trong bàn",
                            "source": "table_broadcast"
                        }
                        print(f"   🎯 [Bàn chơi {GAME_NAMES.get(g_code, g_code)}] ID={hpid} | Host={hname} | Cược={bet_val:,} xu")
                except Exception:
                    pass

        ws.close()
    except Exception:
        if ws:
            try: ws.close()
            except Exception: pass
    return players


def export_reports(all_players, output_dir="reports"):
    os.makedirs(output_dir, exist_ok=True)
    now_str = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S UTC")

    # 1. JSON
    json_path = os.path.join(output_dir, "players_latest.json")
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump({
            "updated_at": now_str,
            "total_players": len(all_players),
            "players": all_players
        }, f, ensure_ascii=False, indent=2)

    # 2. CSV
    csv_path = os.path.join(output_dir, "players_latest.csv")
    with open(csv_path, "w", encoding="utf-8", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(["Player ID", "Nickname", "Số xu (Chip)", "Điểm Score", "Trò chơi / Hoạt động", "Vị trí", "Cấp độ", "Thắng (W)", "Hòa (D)", "Thua (L)", "Tiến độ EXP", "Cập nhật"])
        for pid, p in sorted(all_players.items(), key=lambda x: x[1].get("balance", 0), reverse=True):
            full = p.get("full_profile", {})
            games = full.get("games", {})
            gstats = list(games.values())[0] if games else {}
            level = gstats.get("level", "N/A")
            win = gstats.get("win", "0")
            draw = gstats.get("draw", "0")
            lost = gstats.get("lost", "0")
            prog = gstats.get("progress", "N/A")
            nick = p.get("nick") or full.get("nick") or "N/A"
            bal = p.get("balance") or full.get("balance") or 0
            writer.writerow([
                pid, nick, bal, p.get("score", 0),
                p.get("game", ""), p.get("room", ""), level, win, draw, lost, prog, now_str
            ])

    # 3. Markdown
    md_path = os.path.join(output_dir, "players_latest.md")
    with open(md_path, "w", encoding="utf-8") as f:
        f.write(f"# 📊 Báo Cáo Người Chơi Trực Tuyến GameVH\n\n")
        f.write(f"- **Thời gian cập nhật:** `{now_str}`\n")
        f.write(f"- **Tổng số người chơi ghi nhận:** `{len(all_players)}` người\n\n")
        f.write(f"| Player ID | Nickname | Số xu (Chip) | Điểm (Score) | Trò chơi / Vị trí | Cấp độ (Rank) | Thắng (W) | Hòa (D) | Thua (L) | EXP |\n")
        f.write(f"|:---|:---|:---:|:---:|:---|:---|:---:|:---:|:---:|:---:|\n")
        for pid, p in sorted(all_players.items(), key=lambda x: max(x[1].get("balance", 0), x[1].get("full_profile", {}).get("balance", 0)), reverse=True):
            full = p.get("full_profile", {})
            games = full.get("games", {})
            gstats = list(games.values())[0] if games else {}
            level = gstats.get("level", "N/A")
            win = gstats.get("win", "0")
            draw = gstats.get("draw", "0")
            lost = gstats.get("lost", "0")
            prog = gstats.get("progress", "N/A")
            nick = p.get("nick") or full.get("nick") or "N/A"
            bal = max(p.get("balance", 0), full.get("balance", 0))
            loc = f"{p.get('game', '')} ({p.get('room', '')})"
            f.write(f"| `{pid}` | **{nick}** | `{bal:,}` | `{p.get('score', 0):,}` | {loc} | {level} | {win} | {draw} | {lost} | {prog} |\n")

    print(f"\n[+] Đã xuất đầy đủ báo cáo vào thư mục '{output_dir}/'!")


def main():
    parser = argparse.ArgumentParser(description="Quét TOÀN BỘ danh sách người đang trực tuyến trên GameVH")
    parser.add_argument("--user", default="arena20", help="Tên tài khoản bot")
    parser.add_argument("--password", default="nhat123456", help="Mật khẩu bot")
    parser.add_argument("--game", default="all", help="Trò chơi cần quét (hoặc 'all')")
    parser.add_argument("--rooms", default="0,1,2", help="Danh sách ID phòng (vd: 0,1,2)")
    parser.add_argument("--time", type=int, default=6, help="Thời gian nghe mỗi phòng (giây)")
    parser.add_argument("--output-dir", default="reports", help="Thư mục xuất báo cáo")
    parser.add_argument("--include-top", action="store_true", default=True, help="Bao gồm Top đại gia từ trang chủ")

    args = parser.parse_args()
    user = args.user.strip().replace('"', '').replace("'", "")
    password = args.password.strip().replace('"', '').replace("'", "")

    print("="*75)
    print("🚀 BẮT ĐẦU QUÉT TOÀN BỘ NGƯỜI CHƠI TRỰC TUYẾN TRÊN GAMEVH")
    print("="*75)

    sess = requests.Session()
    sess.headers.update({"User-Agent": UA})
    r = sess.post(LOGIN_URL, timeout=10,
                  data={"redirect": "/", "USER_NAME": user, "PASSWORD": password,
                        "AUTO_LOGIN": "true", "LOGIN": "Đăng nhập"},
                  headers={"Origin": "https://gamevh.net", "Referer": LOGIN_URL})
    if "login.jsp" in r.url:
        print("❌ [LỖI] Đăng nhập thất bại!")
        sys.exit(1)

    # 1. Lấy thông tin user của bot
    g_init = sess.get("https://gamevh.net/play/xiangqi/0", timeout=10)
    pid_m = re.search(r"var\s+currentPlayerId\s*=\s*(\d+)", g_init.text)
    my_pid = int(pid_m.group(1)) if pid_m else 0
    nm_m = re.search(r"var\s+currentPlayerNickName\s*=\s*[\"']([^\"']+)[\"']", g_init.text)
    my_nick = nm_m.group(1).strip() if nm_m else user
    cookie = "; ".join(f"{k}={v}" for k, v in sess.cookies.items())

    print(f"✅ Đăng nhập thành công bot: {my_nick} (ID: {my_pid})")

    all_players = {}

    # 2. Quét Top đại gia trang chủ nếu bật
    if args.include_top:
        top_p = scan_homepage_top_players(sess)
        all_players.update(top_p)

    # 3. Xác định danh sách game & phòng cần quét
    if args.game.lower() == "all":
        games_to_scan = ALL_GAMES
    else:
        games_to_scan = [g.strip() for g in args.game.split(",") if g.strip()]

    room_ids = [int(r.strip()) for r in str(args.rooms).split(",") if r.strip().isdigit()]

    # 4. Lấy tokens cho các game song song
    print(f"[*] Đang lấy phiên kết nối cho {len(games_to_scan)} trò chơi...")
    tokens = {}
    for gid in games_to_scan:
        try:
            res = sess.get(f"https://gamevh.net/play/{gid}/0", timeout=5)
            tm = re.search(r"var\s+token\s*=\s*(-?\d+)", res.text)
            if tm:
                tokens[gid] = int(tm.group(1))
        except Exception:
            pass

    print(f"[+] Sẵn sàng quét {len(tokens)} game qua WebSocket.")

    # 5. Quét WebSocket song song đa luồng
    tasks = []
    for gid, tok in tokens.items():
        for rid in room_ids:
            tasks.append((gid, rid, tok))

    print(f"[*] ⚡ Khởi chạy quét đồng thời {len(tasks)} sảnh/phòng...")

    def run_task(item):
        gid, rid, tok = item
        return scan_room_ws(cookie, my_nick, tok, gid, rid, args.time, my_pid)

    with ThreadPoolExecutor(max_workers=12) as pool:
        results = pool.map(run_task, tasks)
        for res_dict in results:
            all_players.update(res_dict)

    print(f"\n[+] Tổng số người chơi tìm thấy: {len(all_players)} người.")

    # 6. Truy vấn Profile chi tiết
    print("[*] Đang tải chi tiết Level, W/D/L và Điểm số từ Profile...")
    def fetch_prof(pid):
        return pid, get_player_full_profile(pid, session=sess)

    with ThreadPoolExecutor(max_workers=10) as pool:
        prof_results = pool.map(fetch_prof, all_players.keys())
        for pid, prof in prof_results:
            all_players[pid]["full_profile"] = prof

    # 7. Xuất báo cáo
    export_reports(all_players, output_dir=args.output_dir)


if __name__ == "__main__":
    main()

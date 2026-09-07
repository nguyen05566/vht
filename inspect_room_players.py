#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
inspect_room_players.py - QUÉT LIÊN TỤC THEO THỜI GIAN GOM TỐI ĐA ID NGƯỜI CHƠI
==================================================================================
- Chạy vòng lặp liên tục trong khoảng thời gian chỉ định (--total-minutes).
- Quét đa luồng qua HTTP endpoint zone_player_list_helper.jsp (chính).
- Bắt trọn TẤT CẢ người chơi đang có mặt trong sảnh & bàn chơi.
- Tự động tích lũy và mở rộng danh sách ID liên tục theo thời gian.
- Xuất báo cáo Markdown, CSV, JSON đẩy lên GitHub repository.
"""

import argparse
import csv
import json
import os
import re
import struct
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
import requests
from bs4 import BeautifulSoup

WS_URL = "wss://gamevh.net/ws/gameServer"
LOGIN_URL = "https://gamevh.net/login.jsp"
PROFILE_URL = "https://gamevh.net/com/ftl/game/profile/player_profile.jsp"
SEARCH_URL = "https://gamevh.net/com/ftl/game/profile/search_profile.jsp"
HOME_URL = "https://gamevh.net/"
# HTTP endpoint trả về JSON danh sách người chơi trong phòng/bàn
PLAYER_LIST_URL = "https://gamevh.net/wc41/module/zone_player_list_helper.jsp"

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


# ==================== HTTP PROFILE TRA CỨU ====================
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
        pass
    return players


# ==================== HTTP SCAN (CHÍNH) ====================
def scan_room_http(session, game_id, room_id):
    """Quét 1 phòng qua HTTP endpoint - trả về TẤT CẢ người chơi trong phòng"""
    found = {}
    path = f"Lobby.{game_id}.{room_id}"
    try:
        r = session.get(
            f"{PLAYER_LIST_URL}?path={path}",
            timeout=8
        )
        if r.status_code == 200 and r.text.strip().startswith("["):
            data = r.json()
            for p in data:
                pid = p.get("id", 0)
                if pid > 0:
                    found[pid] = {
                        "player_id": pid,
                        "nick": p.get("name", ""),
                        "balance": p.get("chipBalance", 0),
                        "score": p.get("score", 0),
                        "level": p.get("level", 0),
                        "avatar": p.get("avatar", ""),
                        "game": GAME_NAMES.get(game_id, game_id),
                        "room": f"Phòng {room_id}",
                        "source": "http_lobby"
                    }
    except Exception:
        pass
    return found


# ==================== WEBSOCKET SCAN (PHỤ) ====================
def scan_room_ws(cookie, user_nick, token, game_id, room_id, duration, my_pid):
    """Quét 1 phòng qua WebSocket - bắt người chơi realtime"""
    import websocket as ws_lib
    
    place_path = f"Lobby.{game_id}.{room_id}"
    ws = None
    found = {}

    def pack_num(cmd, payload=b""):
        return struct.pack(">H", cmd) + payload

    def asc(s):
        e = s.encode("ascii", "replace")[:255]
        return struct.pack(">b", len(e)) + e

    def p_str(s):
        e = s.encode("utf-16-be")
        return struct.pack(">h", len(e)//2) + e

    try:
        ws = ws_lib.create_connection(
            WS_URL, timeout=6,
            header=[f"Cookie: {cookie}", "Origin: https://gamevh.net", f"User-Agent: {UA}"],
            cookie=cookie
        )
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
            return found

        ws.send_binary(pack_num(401, asc(place_path) + p_str("") + struct.pack(">b", 1)))

        try:
            ws.send_binary(pack_num(408, asc(str(room_id)) + struct.pack(">b", -1)))
        except Exception:
            pass

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

                    if pid > 0 and pid != my_pid:
                        found[pid] = {
                            "player_id": pid,
                            "nick": pname,
                            "balance": bal,
                            "score": score,
                            "game": GAME_NAMES.get(game_id, game_id),
                            "room": f"Phòng {room_id}",
                            "source": "ws_sảnh"
                        }
                except Exception:
                    pass

            elif cmd == 312:
                try:
                    offset = 2
                    n_len = struct.unpack_from(">h", raw, offset)[0]; offset += 2
                    hname = raw[offset:offset + n_len*2].decode("utf-16-be", errors="replace"); offset += n_len*2
                    offset += 7
                    g_len = struct.unpack_from(">b", raw, offset)[0]; offset += 1
                    g_code = raw[offset:offset + g_len].decode("ascii", errors="replace"); offset += g_len
                    bet_val = struct.unpack_from(">i", raw, offset)[0]; offset += 4
                    hpid = struct.unpack_from(">q", raw, offset)[0]; offset += 8
                    
                    if hpid > 0 and hpid != my_pid:
                        found[hpid] = {
                            "player_id": hpid,
                            "nick": hname,
                            "balance": 0,
                            "score": 0,
                            "game": f"{GAME_NAMES.get(g_code, g_code)} (Cược {bet_val:,} xu)",
                            "room": "Đang trong bàn",
                            "source": "ws_mời_đấu"
                        }
                except Exception:
                    pass

        ws.close()
    except Exception:
        if ws:
            try: ws.close()
            except Exception: pass

    return found


# ==================== XUẤT BÁO CÁO ====================
def export_reports(all_players, output_dir="reports"):
    base_dir = os.path.dirname(os.path.abspath(__file__))
    target_dir = os.path.join(base_dir, output_dir) if not os.path.isabs(output_dir) else output_dir
    os.makedirs(target_dir, exist_ok=True)
    now_str = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")

    # 1. JSON
    json_path = os.path.join(target_dir, "players_latest.json")
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump({
            "updated_at": now_str,
            "total_players": len(all_players),
            "players": all_players
        }, f, ensure_ascii=False, indent=2)

    # 2. CSV
    csv_path = os.path.join(target_dir, "players_latest.csv")
    with open(csv_path, "w", encoding="utf-8", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(["Player ID", "Nickname", "Số xu (Chip)", "Điểm Score", "Cấp độ", "Trò chơi / Hoạt động", "Vị trí", "Nguồn", "Thắng (W)", "Hòa (D)", "Thua (L)", "Tiến độ EXP", "Cập nhật"])
        for pid, p in sorted(all_players.items(), key=lambda x: max(x[1].get("balance", 0), x[1].get("full_profile", {}).get("balance", 0)), reverse=True):
            full = p.get("full_profile", {})
            games = full.get("games", {})
            gstats = list(games.values())[0] if games else {}
            level = gstats.get("level", p.get("level", "N/A"))
            win = gstats.get("win", "0")
            draw = gstats.get("draw", "0")
            lost = gstats.get("lost", "0")
            prog = gstats.get("progress", "N/A")
            nick = p.get("nick") or full.get("nick") or "N/A"
            bal = max(p.get("balance", 0), full.get("balance", 0))
            writer.writerow([
                pid, nick, bal, p.get("score", 0), level,
                p.get("game", ""), p.get("room", ""), p.get("source", ""),
                win, draw, lost, prog, now_str
            ])

    # 3. Markdown
    md_path = os.path.join(target_dir, "players_latest.md")
    with open(md_path, "w", encoding="utf-8") as f:
        f.write(f"# 📊 Báo Cáo Người Chơi Trực Tuyến GameVH\n\n")
        f.write(f"- **Thời gian cập nhật:** `{now_str}`\n")
        f.write(f"- **Tổng số người chơi ghi nhận:** `{len(all_players)}` người\n\n")
        f.write(f"| Player ID | Nickname | Số xu (Chip) | Điểm (Score) | Cấp độ | Trò chơi / Vị trí | Nguồn | Thắng | Hòa | Thua |\n")
        f.write(f"|:---|:---|:---:|:---:|:---|:---|:---|:---:|:---:|:---:|\n")
        for pid, p in sorted(all_players.items(), key=lambda x: max(x[1].get("balance", 0), x[1].get("full_profile", {}).get("balance", 0)), reverse=True):
            full = p.get("full_profile", {})
            games = full.get("games", {})
            gstats = list(games.values())[0] if games else {}
            level = gstats.get("level", p.get("level", "N/A"))
            win = gstats.get("win", "0")
            draw = gstats.get("draw", "0")
            lost = gstats.get("lost", "0")
            nick = p.get("nick") or full.get("nick") or "N/A"
            bal = max(p.get("balance", 0), full.get("balance", 0))
            loc = f"{p.get('game', '')} ({p.get('room', '')})"
            src = p.get("source", "")
            f.write(f"| `{pid}` | **{nick}** | `{bal:,}` | `{p.get('score', 0):,}` | {level} | {loc} | {src} | {win} | {draw} | {lost} |\n")

    print(f"\n[+] Đã lưu báo cáo ({len(all_players)} người) vào thư mục '{target_dir}/'!")


# ==================== MAIN ENTRYPOINT ====================
def main():
    parser = argparse.ArgumentParser(description="Quét liên tục gom tối đa ID người chơi (HTTP + WS)")
    parser.add_argument("--user", default="arena20", help="Tên tài khoản bot")
    parser.add_argument("--password", default="nhat123456", help="Mật khẩu bot")
    parser.add_argument("--game", default="all", help="Trò chơi cần quét (hoặc 'all')")
    parser.add_argument("--rooms", default="0,1,2,3,4,5", help="Danh sách ID phòng (vd: 0,1,2,3,4,5)")
    parser.add_argument("--time", type=int, default=5, help="Thời gian nghe WS mỗi lượt ở mỗi phòng (giây)")
    parser.add_argument("--total-minutes", type=int, default=10, help="Tổng thời gian chạy vòng lặp (phút)")
    parser.add_argument("--workers", type=int, default=20, help="Số luồng quét song song")
    parser.add_argument("--output-dir", default="reports", help="Thư mục xuất báo cáo")
    parser.add_argument("--include-top", action="store_true", default=True, help="Bao gồm Top đại gia từ trang chủ")
    parser.add_argument("--ws", action="store_true", default=False, help="Bật thêm quét WebSocket (mặc định chỉ HTTP)")

    args = parser.parse_args()
    user = args.user.strip().replace('"', '').replace("'", "")
    password = args.password.strip().replace('"', '').replace("'", "")
    total_seconds = max(30, args.total_minutes * 60)

    print("="*80)
    print("🚀 BẮT ĐẦU QUÉT NGƯỜI CHƠI (HTTP + WS)")
    print(f"⏱️  Tổng thời gian chạy: {args.total_minutes} phút ({total_seconds} giây)")
    print(f"⚡ Số luồng đồng thời: {args.workers}")
    print(f"📡 Chế độ: {'HTTP + WebSocket' if args.ws else 'HTTP only (nhanh, đầy đủ)'}")
    print("="*80)

    sess = requests.Session()
    sess.headers.update({"User-Agent": UA})
    r = sess.post(LOGIN_URL, timeout=10,
                  data={"redirect": "/", "USER_NAME": user, "PASSWORD": password,
                        "AUTO_LOGIN": "true", "LOGIN": "Đăng nhập"},
                  headers={"Origin": "https://gamevh.net", "Referer": LOGIN_URL})
    if "login.jsp" in r.url:
        print("❌ [LỖI] Đăng nhập thất bại!")
        sys.exit(1)

    # Lấy thông tin bot
    g_init = sess.get("https://gamevh.net/play/xiangqi/0", timeout=10)
    pid_m = re.search(r"var\s+currentPlayerId\s*=\s*(\d+)", g_init.text)
    my_pid = int(pid_m.group(1)) if pid_m else 0
    nm_m = re.search(r"var\s+currentPlayerNickName\s*=\s*[\"']([^\"']+)[\"']", g_init.text)
    my_nick = nm_m.group(1).strip() if nm_m else user
    cookie = "***".join(f"{k}={v}" for k, v in sess.cookies.items())

    print(f"✅ Đăng nhập thành công bot: {my_nick} (ID: {my_pid})")

    master_players = {}

    # 1. Quét Top đại gia trang chủ ban đầu
    if args.include_top:
        top_p = scan_homepage_top_players(sess)
        master_players.update(top_p)
        print(f"[+] Đã nạp ban đầu {len(master_players)} người chơi từ Trang chủ / Top.")

    # 2. Xác định danh sách game & phòng
    if args.game.lower() == "all":
        games_to_scan = ALL_GAMES
    else:
        games_to_scan = [g.strip() for g in args.game.split(",") if g.strip()]

    room_ids = [int(r.strip()) for r in str(args.rooms).split(",") if r.strip().isdigit()]

    # Chuẩn bị danh sách task HTTP
    http_tasks = []
    for gid in games_to_scan:
        for rid in room_ids:
            http_tasks.append((gid, rid))

    # Chuẩn bị WS tokens nếu cần
    ws_tokens = {}
    if args.ws:
        print(f"[*] Đang khởi tạo phiên WS cho {len(games_to_scan)} trò chơi...")
        def get_token(gid):
            try:
                res = sess.get(f"https://gamevh.net/play/{gid}/0", timeout=5)
                tm = re.search(r"var\s+token\s*=\s*(-?\d+)", res.text)
                if tm: return gid, int(tm.group(1))
            except Exception: pass
            return gid, None

        with ThreadPoolExecutor(max_workers=10) as pool:
            for gid, tok in pool.map(get_token, games_to_scan):
                if tok: ws_tokens[gid] = tok
        print(f"[+] WS sẵn sàng quét {len(ws_tokens)} game.")

    # ==================== VÒNG LẶP THEO THỜI GIAN ====================
    start_time = time.time()
    loop_count = 0

    while time.time() - start_time < total_seconds:
        loop_count += 1
        elapsed = time.time() - start_time
        remaining = max(0, total_seconds - elapsed)
        
        print(f"\n" + "-"*75)
        print(f"🔄 [VÒNG QUÉT #{loop_count}] Đã chạy: {int(elapsed//60)}m{int(elapsed%60)}s | Còn lại: {int(remaining//60)}m{int(remaining%60)}s")
        print(f"📊 Tổng số ID đã thu thập hiện tại: {len(master_players)} người")
        print("-"*75)

        # === QUÉT HTTP (CHÍNH) ===
        new_http = 0
        def run_http_task(item):
            gid, rid = item
            return scan_room_http(sess, gid, rid)

        with ThreadPoolExecutor(max_workers=args.workers) as pool:
            results = pool.map(run_http_task, http_tasks)
            for res_dict in results:
                for pid, pdata in res_dict.items():
                    if pid != my_pid and pid not in master_players:
                        master_players[pid] = pdata
                        new_http += 1

        print(f"   📡 [HTTP] Phát hiện +{new_http} ID mới")

        # === QUÉT WS (PHỤ) ===
        if args.ws and ws_tokens:
            new_ws = 0
            ws_tasks = []
            for gid, tok in ws_tokens.items():
                for rid in room_ids:
                    ws_tasks.append((gid, rid, tok))

            def run_ws_task(item):
                gid, rid, tok = item
                return scan_room_ws(cookie, my_nick, tok, gid, rid, args.time, my_pid)

            with ThreadPoolExecutor(max_workers=args.workers) as pool:
                results = pool.map(run_ws_task, ws_tasks)
                for res_dict in results:
                    for pid, pdata in res_dict.items():
                        if pid != my_pid and pid not in master_players:
                            master_players[pid] = pdata
                            new_ws += 1

            print(f"   📡 [WS]   Phát hiện +{new_ws} ID mới")

        print(f"✅ Kết thúc vòng #{loop_count}: Tổng cộng {len(master_players)} ID")

        # Nghỉ ngắn giữa các vòng
        if time.time() - start_time < total_seconds:
            time.sleep(3)

    # ==================== TỔNG KẾT & TRUY VẤN PROFILE ====================
    print("\n" + "="*80)
    print(f"🏁 ĐÃ HOÀN TẤT VÒNG LẶP {args.total_minutes} PHÚT!")
    print(f"📈 TỔNG SỐ NGƯỜI CHƠI THU THẬP ĐƯỢC: {len(master_players)} người.")
    print("="*80)

    print("[*] 📊 Đang tải chi tiết Level, W/D/L và Điểm số từ Profile...")
    def fetch_prof(pid):
        return pid, get_player_full_profile(pid, session=sess)

    with ThreadPoolExecutor(max_workers=15) as pool:
        prof_results = pool.map(fetch_prof, master_players.keys())
        for pid, prof in prof_results:
            master_players[pid]["full_profile"] = prof

    # Xuất báo cáo
    export_reports(master_players, output_dir=args.output_dir)


if __name__ == "__main__":
    main()

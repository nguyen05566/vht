#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
inspect_room_players.py - QUÉT LIÊN TỤC THEO THỜI GIAN (10 PHÚT, 30 PHÚT...) GOM TỐI ĐA ID NGƯỜI CHƠI
========================================================================================================
- Chạy vòng lặp liên tục trong khoảng thời gian chỉ định (--total-minutes, vd: 10 phút).
- Quét đa luồng 20-30 workers đồng thời trên 14 game & 6 phòng.
- Bắt trọn:
  1. Người vào sảnh/phòng (CMD 406 PLAYER_ENTERED)
  2. Người đang ngồi trong bàn chơi (CMD 415 TABLE_INFO)
  3. Người tạo bàn mời đấu (CMD 312 TABLE_BROADCAST)
  4. Bàn có người thật đang đánh (CMD 408 QUICK_PLAY)
  5. Top đại gia & cao thủ từ trang chủ
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
import websocket
from bs4 import BeautifulSoup

WS_URL = "wss://gamevh.net/ws/gameServer"
LOGIN_URL = "https://gamevh.net/login.jsp"
PROFILE_URL = "https://gamevh.net/com/ftl/game/profile/player_profile.jsp"
SEARCH_URL = "https://gamevh.net/com/ftl/game/profile/search_profile.jsp"
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


# ==================== PACK & PARSE ====================
def pack_num(cmd, payload=b""):
    return struct.pack(">H", cmd) + payload

def asc(s):
    e = s.encode("ascii", "replace")[:255]
    return struct.pack(">b", len(e)) + e

def p_str(s):
    e = s.encode("utf-16-be")
    return struct.pack(">h", len(e)//2) + e


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


def search_player_by_name(name, session=None):
    """Tìm Player ID theo tên hiển thị nếu bắt được tên từ bàn chơi (CMD 415)"""
    if not name or len(name) < 2: return None
    if session is None:
        session = requests.Session()
        session.headers.update({"User-Agent": UA})
    try:
        r = session.get(f"{PROFILE_URL}?playerName={requests.utils.quote(name)}", timeout=4)
        m = re.search(r"playerId=(\d+)", r.text)
        if m:
            return int(m.group(1))
    except Exception:
        pass
    return None


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


# ==================== WEBSOCKET SCANNER ====================
def scan_room_worker(cookie, user_nick, token, game_id, room_id, duration, my_pid):
    """Quét 1 phòng: Bắt Sảnh (406), Bàn chơi (415), Mời đấu (312), Dò bàn (408)"""
    place_path = f"Lobby.{game_id}.{room_id}"
    ws = None
    found = {}
    named_hosts = []

    try:
        ws = websocket.create_connection(
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
            return found, named_hosts

        # 1. Vào phòng sảnh (Lobby)
        ws.send_binary(pack_num(401, asc(place_path) + p_str("") + struct.pack(">b", 1)))

        # 2. Gửi dò bàn nhanh (QUICK_PLAY 408)
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
            
            if cmd == 301: # PING
                ws.send_binary(pack_num(300))
                continue

            # 👤 Gói 406: Người chơi ở sảnh
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

                    if pid > 0 and pid != my_pid and pid not in found:
                        found[pid] = {
                            "player_id": pid,
                            "nick": pname,
                            "balance": bal,
                            "score": score,
                            "game": GAME_NAMES.get(game_id, game_id),
                            "room": f"Phòng {room_id}",
                            "source": "sảnh"
                        }
                        print(f"   👤 [Phát hiện] ID={pid} | Nick={pname} | Game={GAME_NAMES.get(game_id, game_id)} P.{room_id} | Xu={bal:,}")
                except Exception:
                    pass

            # 🪑 Gói 415: Danh sách bàn đang hoạt động trong phòng & Tên chủ bàn
            elif cmd == 415:
                try:
                    offset = 2
                    table_code = raw[offset:offset+2].decode("ascii", errors="replace"); offset += 2
                    h_len = struct.unpack_from(">h", raw, offset)[0]; offset += 2
                    h_name = raw[offset:offset + h_len*2].decode("utf-16-be", errors="replace")
                    if h_name and h_name not in [x[0] for x in named_hosts]:
                        named_hosts.append((h_name, game_id, room_id, table_code))
                except Exception:
                    pass

            # 📢 Gói 312: Broadcast mở bàn cược trên toàn server
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
                    
                    if hpid > 0 and hpid != my_pid and hpid not in found:
                        found[hpid] = {
                            "player_id": hpid,
                            "nick": hname,
                            "balance": 0,
                            "score": 0,
                            "game": f"{GAME_NAMES.get(g_code, g_code)} (Cược {bet_val:,} xu)",
                            "room": "Đang trong bàn",
                            "source": "mời đấu"
                        }
                        print(f"   🎯 [Bàn thi đấu {GAME_NAMES.get(g_code, g_code)}] ID={hpid} | Host={hname} | Cược={bet_val:,} xu")
                except Exception:
                    pass

        ws.close()
    except Exception:
        if ws:
            try: ws.close()
            except Exception: pass

    return found, named_hosts


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
        writer.writerow(["Player ID", "Nickname", "Số xu (Chip)", "Điểm Score", "Trò chơi / Hoạt động", "Vị trí", "Cấp độ", "Thắng (W)", "Hòa (D)", "Thua (L)", "Tiến độ EXP", "Cập nhật"])
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
            writer.writerow([
                pid, nick, bal, p.get("score", 0),
                p.get("game", ""), p.get("room", ""), level, win, draw, lost, prog, now_str
            ])

    # 3. Markdown
    md_path = os.path.join(target_dir, "players_latest.md")
    with open(md_path, "w", encoding="utf-8") as f:
        f.write(f"# 📊 Báo Cáo Người Chơi Trực Tuyến & Trong Bàn GameVH\n\n")
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

    print(f"\n[+] Đã lưu báo cáo ({len(all_players)} người) vào thư mục '{target_dir}/'!")


# ==================== MAIN ENTRYPOINT ====================
def main():
    parser = argparse.ArgumentParser(description="Quét liên tục theo thời gian gom tối đa ID người chơi")
    parser.add_argument("--user", default="arena20", help="Tên tài khoản bot")
    parser.add_argument("--password", default="nhat123456", help="Mật khẩu bot")
    parser.add_argument("--game", default="all", help="Trò chơi cần quét (hoặc 'all')")
    parser.add_argument("--rooms", default="0,1,2,3,4,5", help="Danh sách ID phòng (vd: 0,1,2,3,4,5)")
    parser.add_argument("--time", type=int, default=5, help="Thời gian nghe mỗi lượt ở mỗi phòng (giây)")
    parser.add_argument("--total-minutes", type=int, default=10, help="Tổng thời gian chạy vòng lặp dò tìm (phút)")
    parser.add_argument("--workers", type=int, default=20, help="Số luồng quét song song (khuyến nghị 15-30)")
    parser.add_argument("--output-dir", default="reports", help="Thư mục xuất báo cáo")
    parser.add_argument("--include-top", action="store_true", default=True, help="Bao gồm Top đại gia từ trang chủ")

    args = parser.parse_args()
    user = args.user.strip().replace('"', '').replace("'", "")
    password = args.password.strip().replace('"', '').replace("'", "")
    total_seconds = max(30, args.total_minutes * 60)

    print("="*80)
    print("🚀 BẮT ĐẦU VÒNG LẶP DÒ TÌM NGƯỜI CHƠI TRỰC TUYẾN & TRONG BÀN")
    print(f"⏱️ Tổng thời gian chạy: {args.total_minutes} phút ({total_seconds} giây)")
    print(f"⚡ Số luồng đồng thời: {args.workers} | Thời gian mỗi lượt quét: {args.time}s")
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
    cookie = "; ".join(f"{k}={v}" for k, v in sess.cookies.items())

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

    # 3. Lấy tokens cho các game song song
    print(f"[*] Đang khởi tạo phiên kết nối cho {len(games_to_scan)} trò chơi...")
    tokens = {}
    def get_token(gid):
        try:
            res = sess.get(f"https://gamevh.net/play/{gid}/0", timeout=5)
            tm = re.search(r"var\s+token\s*=\s*(-?\d+)", res.text)
            if tm: return gid, int(tm.group(1))
        except Exception: pass
        return gid, None

    with ThreadPoolExecutor(max_workers=10) as pool:
        for gid, tok in pool.map(get_token, games_to_scan):
            if tok: tokens[gid] = tok

    print(f"[+] Sẵn sàng quét {len(tokens)} game qua WebSocket.")

    # Chuẩn bị danh sách các task
    tasks = []
    for gid, tok in tokens.items():
        for rid in room_ids:
            tasks.append((gid, rid, tok))

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

        round_found = {}
        round_hosts = []

        def run_task(item):
            gid, rid, tok = item
            return scan_room_worker(cookie, my_nick, tok, gid, rid, args.time, my_pid)

        with ThreadPoolExecutor(max_workers=args.workers) as pool:
            results = pool.map(run_task, tasks)
            for res_dict, hosts in results:
                round_found.update(res_dict)
                round_hosts.extend(hosts)

        new_count = 0
        for pid, pdata in round_found.items():
            if pid not in master_players:
                master_players[pid] = pdata
                new_count += 1

        # Tra cứu chủ bàn mới phát hiện
        if round_hosts:
            unresolved = [h for h in round_hosts if h[0] not in [p.get("nick") for p in master_players.values()]]
            if unresolved:
                def resolve_host(item):
                    hname, gid, rid, tcode = item
                    pid = search_player_by_name(hname, session=sess)
                    return pid, hname, gid, rid, tcode

                with ThreadPoolExecutor(max_workers=10) as pool:
                    for pid, hname, gid, rid, tcode in pool.map(resolve_host, unresolved):
                        if pid and pid != my_pid and pid not in master_players:
                            master_players[pid] = {
                                "player_id": pid,
                                "nick": hname,
                                "balance": 0,
                                "score": 0,
                                "game": f"{GAME_NAMES.get(gid, gid)} (Bàn {tcode})",
                                "room": f"Phòng {rid}",
                                "source": "bàn_chơi"
                            }
                            new_count += 1
                            print(f"   🎯 [Tìm thấy ID từ Bàn {tcode}] Nick: '{hname}' -> ID: {pid}")

        print(f"✅ Kết thúc vòng #{loop_count}: Phát hiện thêm +{new_count} ID mới! (Tổng cộng: {len(master_players)} ID)")

        # Nghỉ ngắn giữa các vòng nếu còn thời gian
        if time.time() - start_time < total_seconds:
            time.sleep(2)

    # ==================== TỔNG KẾT & TRUY VẤN PROFILE ĐẦY ĐỦ ====================
    print("\n" + "="*80)
    print(f"🏁 ĐÃ HOÀN TẤT VÒNG LẶP {args.total_minutes} PHÚT!")
    print(f"📈 TỔNG SỐ NGƯỜI CHƠI THU THẬP ĐƯỢC: {len(master_players)} người.")
    print("="*80)

    print("[*] 📊 Đang tải chi tiết Level, W/D/L và Điểm số từ Profile cho toàn bộ danh sách...")
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

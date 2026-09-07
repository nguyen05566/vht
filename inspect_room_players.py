#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
inspect_room_players.py - Quét danh sách người chơi thực tế trong các phòng GameVH
Lấy:
- Player ID
- Tên hiển thị (Nickname)
- Số xu (Xu / Chip balance)
- Điểm cấp độ (Score / Elo)
- Cấp độ / Danh hiệu (Rank / Level: Adept, Kỳ vương...)
- Thống kê Thắng / Hòa / Thua (W / D / L)
- Tự động xuất file Markdown, JSON, CSV để lưu vào repo
"""

import argparse
import csv
import json
import os
import re
import struct
import sys
import time
from datetime import datetime
import requests
import websocket
from bs4 import BeautifulSoup

WS_URL = "wss://gamevh.net/ws/gameServer"
LOGIN_URL = "https://gamevh.net/login.jsp"
PROFILE_URL = "https://gamevh.net/com/ftl/game/profile/player_profile.jsp"
UA = ("Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/139.0 Safari/537.36")


def pack_num(cmd, payload=b""):
    return struct.pack(">H", cmd) + payload

def asc(s):
    e = s.encode("ascii", "replace")[:255]
    return struct.pack(">b", len(e)) + e

def p_str(s):
    e = s.encode("utf-16-be")
    return struct.pack(">h", len(e)//2) + e


def get_player_full_profile(player_id, session=None):
    """Lấy thông tin chi tiết profile, cấp độ, W/D/L từ HTTP player_profile.jsp"""
    if session is None:
        session = requests.Session()
        session.headers.update({"User-Agent": UA})

    url = f"{PROFILE_URL}?playerId={player_id}"
    try:
        r = session.get(url, timeout=6)
        soup = BeautifulSoup(r.text, "html.parser")
        
        nick_el = soup.find("div", class_="nick_name")
        bal_el = soup.find("div", class_="chipBalance")
        nick = nick_el.get_text(strip=True) if nick_el else ""
        bal_str = bal_el.get_text(strip=True) if bal_el else "0"
        bal = int(re.sub(r"[^\d]", "", bal_str) or 0)

        stats_table = soup.find("table", class_="game_stats")
        games = {}
        if stats_table:
            rows = stats_table.find_all("tr")[1:]  # bỏ header
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


def scan_single_room(sess, nick, token, cookie, game_id, room_id, duration=6):
    """Quét 1 phòng duy nhất và trả về dict người chơi"""
    place_path = f"Lobby.{game_id}.{room_id}"
    print(f"[*] 🔍 Đang quét phòng {place_path} (trong {duration}s)...")
    
    ws = None
    players = {}
    try:
        ws = websocket.create_connection(
            WS_URL, timeout=8,
            header=[f"Cookie: {cookie}", "Origin: https://gamevh.net", f"User-Agent: {UA}"],
            cookie=cookie
        )
        # Login WS
        ws.send_binary(pack_num(302, asc(nick) + struct.pack(">i", token) + asc("5.0.2") + asc("") + asc(game_id) + struct.pack(">b", 1)))

        ws_ok = False
        deadline = time.time() + 6
        while time.time() < deadline:
            raw = ws.recv()
            if not raw: continue
            cmd = struct.unpack_from(">H", raw, 0)[0]
            if cmd == 302:
                ws_ok = True
                break
        if not ws_ok:
            if ws: ws.close()
            return players

        # Enter place
        payload = asc(place_path) + p_str("") + struct.pack(">b", 1)
        ws.send_binary(pack_num(401, payload))

        # Lắng nghe gói PLAYER_ENTERED (406)
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
            if cmd == 406: # PLAYER_ENTERED
                try:
                    offset = 2
                    pl_level = struct.unpack_from(">b", raw, offset)[0]; offset += 1
                    pid = struct.unpack_from(">q", raw, offset)[0]; offset += 8
                    name_len = struct.unpack_from(">h", raw, offset)[0]; offset += 2
                    pname = raw[offset:offset + name_len*2].decode("utf-16-be", errors="replace"); offset += name_len*2
                    
                    bal = 0; score = 0; avatar = ""
                    if len(raw) >= offset + 16:
                        bal = struct.unpack_from(">q", raw, offset)[0]; offset += 8
                        score = struct.unpack_from(">q", raw, offset)[0]; offset += 8
                    if len(raw) > offset:
                        av_len = struct.unpack_from(">b", raw, offset)[0]; offset += 1
                        if av_len > 0 and len(raw) >= offset + av_len:
                            avatar = raw[offset:offset + av_len].decode("ascii", errors="replace")

                    if pid not in players:
                        players[pid] = {
                            "player_id": pid,
                            "nick": pname,
                            "balance": bal,
                            "score": score,
                            "game": game_id,
                            "room": room_id,
                            "avatar": avatar
                        }
                        print(f"   👤 [Phát hiện] ID={pid} | Nick={pname} | Xu={bal:,} | Score={score:,}")
                except Exception:
                    pass
        ws.close()
    except Exception as e:
        if ws:
            try: ws.close()
            except Exception: pass
        print(f"   ⚠️ Lỗi quét phòng {place_path}: {e}")

    return players


def export_reports(all_players, output_dir="reports"):
    """Xuất các báo cáo JSON, Markdown, CSV để lưu vào repo"""
    os.makedirs(output_dir, exist_ok=True)
    now_str = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S UTC")

    # 1. Export JSON
    json_path = os.path.join(output_dir, "players_latest.json")
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump({
            "updated_at": now_str,
            "total_players": len(all_players),
            "players": all_players
        }, f, ensure_ascii=False, indent=2)
    print(f"[+] Đã lưu JSON: {json_path}")

    # 2. Export CSV
    csv_path = os.path.join(output_dir, "players_latest.csv")
    with open(csv_path, "w", encoding="utf-8", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(["Player ID", "Nickname", "Số xu (Chip)", "Điểm Score", "Trò chơi", "Phòng", "Cấp độ", "Thắng (W)", "Hòa (D)", "Thua (L)", "Tiến độ EXP", "Cập nhật"])
        for pid, p in all_players.items():
            full = p.get("full_profile", {})
            games = full.get("games", {})
            gstats = list(games.values())[0] if games else {}
            level = gstats.get("level", "N/A")
            win = gstats.get("win", "0")
            draw = gstats.get("draw", "0")
            lost = gstats.get("lost", "0")
            prog = gstats.get("progress", "N/A")
            writer.writerow([
                pid, p.get("nick", ""), p.get("balance", 0), p.get("score", 0),
                p.get("game", ""), p.get("room", ""), level, win, draw, lost, prog, now_str
            ])
    print(f"[+] Đã lưu CSV: {csv_path}")

    # 3. Export Markdown Report
    md_path = os.path.join(output_dir, "players_latest.md")
    with open(md_path, "w", encoding="utf-8") as f:
        f.write(f"# 📊 Báo Cáo Danh Sách Người Chơi GameVH\n\n")
        f.write(f"- **Thời gian cập nhật:** `{now_str}`\n")
        f.write(f"- **Tổng số người chơi tìm thấy:** `{len(all_players)}`\n\n")
        f.write(f"| Player ID | Nickname | Số xu (Chip) | Điểm (Score) | Trò chơi | Cấp độ (Rank) | Thắng (W) | Hòa (D) | Thua (L) | EXP |\n")
        f.write(f"|:---|:---|:---:|:---:|:---:|:---|:---:|:---:|:---:|:---:|\n")
        for pid, p in sorted(all_players.items(), key=lambda x: x[1].get("balance", 0), reverse=True):
            full = p.get("full_profile", {})
            games = full.get("games", {})
            gstats = list(games.values())[0] if games else {}
            level = gstats.get("level", "N/A")
            win = gstats.get("win", "0")
            draw = gstats.get("draw", "0")
            lost = gstats.get("lost", "0")
            prog = gstats.get("progress", "N/A")
            f.write(f"| `{pid}` | **{p.get('nick', '')}** | `{p.get('balance', 0):,}` | `{p.get('score', 0):,}` | `{p.get('game', '')}` | {level} | {win} | {draw} | {lost} | {prog} |\n")
    print(f"[+] Đã lưu Markdown: {md_path}")


def main():
    parser = argparse.ArgumentParser(description="Quét danh sách người chơi, điểm, level và thông tin chi tiết các phòng")
    parser.add_argument("--user", default="arena20", help="Tên tài khoản bot đăng nhập")
    parser.add_argument("--password", default="nhat123456", help="Mật khẩu bot")
    parser.add_argument("--game", default="all", help="Trò chơi cần quét (caro, xiangqi, co_up, chess, tienlen hoặc 'all')")
    parser.add_argument("--rooms", default="0,1,2,3", help="Danh sách ID phòng cách nhau bởi dấu phẩy (vd: 0,1,2,3)")
    parser.add_argument("--time", type=int, default=6, help="Thời gian nghe mỗi phòng (giây)")
    parser.add_argument("--output-dir", default="reports", help="Thư mục xuất báo cáo")

    args = parser.parse_args()
    user = args.user.strip().replace('"', '').replace("'", "")
    password = args.password.strip().replace('"', '').replace("'", "")

    print("="*70)
    print("🚀 BẮT ĐẦU QUÉT THÔNG TIN NGƯỜI CHƠI TRÊN GAMEVH")
    print("="*70)

    sess = requests.Session()
    sess.headers.update({"User-Agent": UA})
    r = sess.post(LOGIN_URL, timeout=10,
                  data={"redirect": "/", "USER_NAME": user, "PASSWORD": password,
                        "AUTO_LOGIN": "true", "LOGIN": "Đăng nhập"},
                  headers={"Origin": "https://gamevh.net", "Referer": LOGIN_URL})
    if "login.jsp" in r.url:
        print("❌ [LỖI] Đăng nhập HTTP thất bại! Vui lòng kiểm tra lại username/password.")
        sys.exit(1)

    print("✅ Đăng nhập HTTP thành công!")

    # Xác định danh sách game & phòng cần quét
    if args.game.lower() == "all":
        games_to_scan = ["caro", "xiangqi", "co_up", "chess"]
    else:
        games_to_scan = [g.strip() for g in args.game.split(",") if g.strip()]

    room_ids = [int(r.strip()) for r in str(args.rooms).split(",") if r.strip().isdigit()]

    all_players = {}
    for gid in games_to_scan:
        # Lấy session token cho game
        g_url = f"https://gamevh.net/play/{gid}/0"
        g = sess.get(g_url, timeout=10)
        tm = re.search(r"var\s+token\s*=\s*(-?\d+)", g.text)
        token = int(tm.group(1)) if tm else 0
        nm = re.search(r"var\s+currentPlayerNickName\s*=\s*[\"']([^\"']+)[\"']", g.text)
        nick = nm.group(1).strip() if nm else user
        cookie = "; ".join(f"{k}={v}" for k, v in sess.cookies.items())

        if not token:
            print(f"⚠️ Không lấy được token cho game {gid}, bỏ qua...")
            continue

        for rid in room_ids:
            room_players = scan_single_room(sess, nick, token, cookie, gid, rid, duration=args.time)
            all_players.update(room_players)
            time.sleep(0.5)

    print(f"\n[+] Tổng số người chơi tìm thấy: {len(all_players)}")
    print("[*] Đang tải chi tiết Level & Thống kê W/D/L cho từng người chơi...")
    for pid, pdata in all_players.items():
        prof = get_player_full_profile(pid, session=sess)
        pdata["full_profile"] = prof
        time.sleep(0.1)

    # Xuất báo cáo
    export_reports(all_players, output_dir=args.output_dir)

    print("\n" + "="*80)
    print("📊 TỔNG HỢP KẾT QUẢ QUÉT NGƯỜI CHƠI")
    print("="*80)
    for pid, info in all_players.items():
        print(f"\n🔹 Player ID: {pid} | Nickname: {info.get('nick', '')}")
        print(f"   • Số xu: {info.get('balance', 0):,} xu | Điểm hiện tại: {info.get('score', 0):,}")
        games = info.get("full_profile", {}).get("games", {})
        for gname, gstats in games.items():
            print(f"   • {gname}: Thắng {gstats['win']} | Hòa {gstats['draw']} | Thua {gstats['lost']} | Điểm: {gstats['score']} | Cấp độ: {gstats['level']} ({gstats['progress']})")


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""test_cup_flip.py — Kiểm tra LỖI FLIP FEN của cup_bot.py (offline, không cần mạng).

Mô phỏng gói START_MATCH của gamevh.net cho CẢ HAI hướng bàn cờ:
  A) ĐỎ ở server row 0..4  (bot nhìn thấy đỏ ở trên)
  B) ĐỎ ở server row 5..9  (bot nhìn thấy đỏ ở dưới)
và cho cả hai màu bot (bot cầm ĐỎ / bot cầm ĐEN) => 4 tổ hợp.

Yêu cầu đúng ở MỌI tổ hợp:
  1. FEN dựng ra luôn có 'K' (tướng đỏ) ở nửa dưới và 'k' ở nửa trên.
  2. pos -> UCI -> pos là phép biến đổi khứ hồi (round-trip) chính xác.
  3. Nước đi engine trả về được dịch lại đúng ô của server.
"""
import sys, types, os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# Chặn phần khởi tạo engine/mạng: chỉ nạp lớp logic bàn cờ
import importlib.util
spec = importlib.util.spec_from_file_location("cup_bot", os.path.join(os.path.dirname(os.path.abspath(__file__)), "cup_bot.py"))
cup = importlib.util.module_from_spec(spec)
spec.loader.exec_module(cup)

Tracker = cup.XiangqiBoardTracker


class FakeBot:
    """Chỉ lấy 2 method dựng FEN của PikafishBot, không đụng engine/websocket."""
    def __init__(self):
        self.board = Tracker()
    _build_fen_from_pieces = cup.PikafishBot._build_fen_from_pieces
    _rebuild_fen_with_current_flip = cup.PikafishBot._rebuild_fen_with_current_flip


TYPE = {1: 'k', 2: 'a', 3: 'b', 4: 'r', 5: 'c', 6: 'n', 7: 'p'}


def make_pieces(red_on_top):
    """Tạo danh sách quân giống START_MATCH.

    Bố cục cờ úp: chỉ TƯỚNG là lộ (is_open=1), 15 quân còn lại mỗi bên là úp.
    red_on_top=True  -> tướng đỏ ở row 0 (pos 4),  tướng đen ở row 9 (pos 85)
    red_on_top=False -> tướng đỏ ở row 9 (pos 85), tướng đen ở row 0 (pos 4)
    """
    layout_rows = [0, 2, 3]          # hàng quân của bên "ở trên"
    pieces = []

    def add_side(color, rows, king_pos):
        # tướng lộ
        pieces.append((f"{color}1", f"{color}1", king_pos, 1))
        # quân úp: các ô còn lại của bố cục khởi đầu
        cells = []
        r0, r2, r3 = rows
        cells += [r0 * 9 + c for c in range(9) if r0 * 9 + c != king_pos]
        cells += [r2 * 9 + 1, r2 * 9 + 7]
        cells += [r3 * 9 + c for c in (0, 2, 4, 6, 8)]
        for i, pos in enumerate(cells):
            pieces.append((f"{color}0", f"{color}0", pos, 0))

    if red_on_top:
        add_side('r', (0, 2, 3), 4)
        add_side('b', (9, 7, 6), 85)
    else:
        add_side('b', (0, 2, 3), 4)
        add_side('r', (9, 7, 6), 85)
    return pieces


def rows_of(fen):
    return fen.split(' ')[0].split('/')


def check(red_on_top, bot_is_red):
    bot = FakeBot()
    bot.board.is_red = bot_is_red
    pieces = make_pieces(red_on_top)
    fen = bot._build_fen_from_pieces(pieces)
    rows = rows_of(fen)

    ok, why = bot.board.sanity_check_fen(fen)
    K_row = next(i for i, r in enumerate(rows) if 'K' in r)
    k_row = next(i for i, r in enumerate(rows) if 'k' in r)

    label = (f"server ĐỎ ở {'TRÊN (row0)' if red_on_top else 'DƯỚI (row9)'} | "
             f"bot cầm {'ĐỎ ' if bot_is_red else 'ĐEN'}")
    print(f"\n=== {label} ===")
    print(f"  flip = {bot.board.flip} (known={bot.board.flip_known})")
    print(f"  FEN  = {fen}")
    print(f"  K ở hàng FEN {K_row} (cần >=7), k ở hàng FEN {k_row} (cần <=2) -> "
          f"{'✅ ĐÚNG CHIỀU' if ok else '❌ SAI: ' + why}")

    # --- round-trip tọa độ ---
    rt_fail = []
    for pos in range(90):
        mv = bot.board.pos_to_engine_move(pos, pos)
        s, t = bot.board.engine_move_to_pos(mv)
        if s != pos or t != pos:
            rt_fail.append(pos)
    print(f"  Round-trip pos->UCI->pos: {'✅ 90/90 ô khớp' if not rt_fail else '❌ lệch ở ' + str(rt_fail[:5])}")

    # --- quân đỏ phải ở rank thấp (0..4) trong FEN ---
    red_bad = []
    for sid, face, pos, is_open in pieces:
        r, c = bot.board.pos_to_rc(pos)
        ch = rows_expand(rows)[r][c]
        if face[0] == 'r' and not (ch.isupper()):
            red_bad.append((pos, ch))
    print(f"  Màu quân sau khi ánh xạ: {'✅ khớp' if not red_bad else '❌ ' + str(red_bad[:5])}")

    return ok and not rt_fail and not red_bad


def rows_expand(rows):
    g = []
    for r in rows:
        line = []
        for ch in r:
            if ch.isdigit(): line += ['.'] * int(ch)
            else: line.append(ch)
        g.append(line)
    return g


def check_move_translation():
    """Engine trả 'h2e2' (pháo về giữa, phía ĐỎ) -> phải ra ô của server đúng phía đỏ."""
    print("\n=== Dịch nước đi engine -> ô server ===")
    allok = True
    for red_on_top in (True, False):
        bot = FakeBot()
        bot.board.is_red = True
        bot._build_fen_from_pieces(make_pieces(red_on_top))
        s, t = bot.board.engine_move_to_pos("h2e2")   # rank 2 = phía ĐỎ
        s_row = s // 9
        expect_red_half = (s_row <= 4) if red_on_top else (s_row >= 5)
        print(f"  ĐỎ ở {'trên' if red_on_top else 'dưới'}: h2e2 -> pos {s}->{t} "
              f"(server row {s_row}) {'✅' if expect_red_half else '❌ sai nửa bàn'}")
        allok &= expect_red_half
    return allok




def check_move_recording():
    """Nước đi + hậu tố lật quân được ghi đúng định dạng PKJQ."""
    print("\n=== Ghi nước đi kèm hậu tố lật quân (cho engine) ===")
    Bot = cup.PikafishBot
    b = Tracker()
    b.flip = True
    b.is_red = False
    b.set_base(Tracker.INITIAL_FEN.split(' ')[0], 'w')

    # Giải mã sid -> ký tự FEN (byte signed)
    cases = [(0x3b, 'P'), (0xd7, 'c'), (0x08, 'K'), (0xef, 'a'), (0x00, None)]
    ok = True
    for byte_val, expect in cases:
        got = Bot._sid_to_fen_char(byte_val)
        good = (got == expect)
        ok &= good
        print(f"  sid 0x{byte_val:02x} -> {got!r} (cần {expect!r}) {'✅' if good else '❌'}")

    # Ghi nước có lật và không lật
    b.record_move("e3e4", "P")
    b.record_move("b7b3", None)
    b.record_move("h2h6", "C")
    exp = ["e3e4P", "b7b3", "h2h6C"]
    good = (b.uci_moves == exp)
    ok &= good
    print(f"  uci_moves = {b.uci_moves} (cần {exp}) {'✅' if good else '❌'}")

    bag = b.bag_string()
    good = ("P4" in bag and "C1" in bag)
    ok &= good
    print(f"  BAG sau khi lật P và C: {bag} {'✅ (P5->P4, C2->C1)' if good else '❌'}")

    fen, moves = b.get_current_fen()
    good = moves == exp and fen.startswith(Tracker.INITIAL_FEN.split(' ')[0])
    ok &= good
    print(f"  get_current_fen -> FEN đầu ván + {len(moves)} nước {'✅' if good else '❌'}")
    return ok


if __name__ == "__main__":
    results = []
    for red_on_top in (True, False):
        for bot_is_red in (True, False):
            results.append(check(red_on_top, bot_is_red))
    results.append(check_move_translation())
    results.append(check_move_recording())
    print("\n" + "=" * 60)
    if all(results):
        print("✅ TẤT CẢ BÀI TEST FLIP FEN ĐỀU ĐẠT")
        sys.exit(0)
    print("❌ CÒN LỖI FLIP FEN")
    sys.exit(1)

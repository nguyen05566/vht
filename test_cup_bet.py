#!/usr/bin/env python3
"""test_cup_bet.py — Kiểm tra thang mức cược 5000 / 10000 / 20000 / 50000.

Dùng ĐÚNG danh sách mức cược thật server gamevh trả về (lấy từ log chạy thật):
  100, 200, 500, 1000, 2000, 5000, 10000, 20000, 50000,
  100000, 200000, 500000, 1000000, 2000000, 0

Kiểm tra:
  1. Thua liên tục -> KHÔNG BAO GIỜ tụt xuống dưới 5000 (lỗi cũ: tụt về 100).
  2. Thắng liên tục -> leo 5000 -> 10000 -> 20000 -> 50000 rồi dừng ở trần.
  3. bet_amt_id luôn khớp đúng mức, không bao giờ trả về mức 0 xu (id=14).
  4. Bộ lọc dò bàn phủ đủ cả 4 mức.
"""
import os, sys, importlib.util

HERE = os.path.dirname(os.path.abspath(__file__))
spec = importlib.util.spec_from_file_location("cup_bot", os.path.join(HERE, "cup_bot.py"))
cup = importlib.util.module_from_spec(spec)
spec.loader.exec_module(cup)

# Danh sách thật server trả về (id = chỉ số trong danh sách)
SERVER_BETS = [100, 200, 500, 1000, 2000, 5000, 10000, 20000, 50000,
               100000, 200000, 500000, 1000000, 2000000, 0]


class FakeBot:
    """Chỉ mượn các hàm xử lý mức cược, không đụng engine/mạng."""
    def __init__(self):
        self.bet_amts = [{"id": i, "value": v} for i, v in enumerate(SERVER_BETS)]
        self._resolved_bet_id = None
        self._bet_amts_loaded = True
        self._sent_list = 0

    def send_list_bet_amt(self):
        self._sent_list += 1

    resolve_bet_amt_id = cup.PikafishBot.resolve_bet_amt_id
    _set_bet_level = cup.PikafishBot._set_bet_level
    _raise_bet_level = cup.PikafishBot._raise_bet_level
    _lower_bet_level = cup.PikafishBot._lower_bet_level
    get_target_bet_objs = cup.PikafishBot.get_target_bet_objs


def val_of(bot, bid):
    for ba in bot.bet_amts:
        if ba["id"] == bid:
            return ba["value"]
    return None


ok = True
print(f"Thang cược cấu hình: {cup.BOT_BET_LEVELS}")
print(f"Khởi đầu: {cup.BOT_BET_XU} xu | sàn {cup.BOT_BET_MIN} | trần {cup.BOT_BET_MAX}\n")

good = cup.BOT_BET_LEVELS == [5000, 10000, 20000, 50000] and cup.BOT_BET_XU == 5000
ok &= good
print(f"1. Cấu hình đúng yêu cầu (5000/10000/20000/50000, bắt đầu 5000) {'✅' if good else '❌'}")

# --- 2. THUA LIÊN TỤC 8 ván: không được xuống dưới sàn ---
print("\n2. THUA liên tục 8 ván (lỗi cũ: tụt 1000->500->200->100):")
cup.BOT_BET_XU = 5000
bot = FakeBot()
seen = []
for i in range(8):
    bot._lower_bet_level()
    seen.append(cup.BOT_BET_XU)
good = all(v >= cup.BOT_BET_MIN for v in seen)
ok &= good
print(f"   Chuỗi mức cược: {seen}")
print(f"   Không tụt dưới {cup.BOT_BET_MIN}: {'✅' if good else '❌ CÓ TỤT DƯỚI SÀN!'}")

# --- 3. THẮNG LIÊN TỤC: leo đủ 4 bậc rồi dừng ---
print("\n3. THẮNG liên tục 6 ván:")
cup.BOT_BET_XU = 5000
bot = FakeBot()
seen = [cup.BOT_BET_XU]
for i in range(6):
    bot._raise_bet_level()
    seen.append(cup.BOT_BET_XU)
expect = [5000, 10000, 20000, 50000, 50000, 50000, 50000]
good = seen == expect
ok &= good
print(f"   Chuỗi: {seen}")
print(f"   Mong đợi: {expect} {'✅' if good else '❌'}")

# --- 4. Thắng-thua xen kẽ ---
print("\n4. Thắng/thua xen kẽ (W W W L L W):")
cup.BOT_BET_XU = 5000
bot = FakeBot()
seq = []
for res in "WWWLLW":
    (bot._raise_bet_level if res == "W" else bot._lower_bet_level)()
    seq.append((res, cup.BOT_BET_XU))
good = all(cup.BOT_BET_MIN <= v <= cup.BOT_BET_MAX for _, v in seq)
ok &= good
print(f"   {seq}")
print(f"   Luôn trong [{cup.BOT_BET_MIN}, {cup.BOT_BET_MAX}]: {'✅' if good else '❌'}")

# --- 5. bet_amt_id khớp đúng, không bao giờ ra mức 0 xu ---
print("\n5. Ánh xạ mức cược -> bet_amt_id (server thật):")
allgood = True
for lv in cup.BOT_BET_LEVELS:
    cup.BOT_BET_XU = lv
    bot = FakeBot()
    bid = bot.resolve_bet_amt_id()
    v = val_of(bot, bid)
    g = (v == lv)
    allgood &= g
    print(f"   {lv:>6} xu -> id={bid} (value={v}) {'✅' if g else '❌'}")
ok &= allgood

# mức 0 xu (id=14) không bao giờ được chọn
cup.BOT_BET_XU = 5000
bot = FakeBot()
bid = bot.resolve_bet_amt_id()
good = val_of(bot, bid) != 0
ok &= good
print(f"   Không bao giờ chọn bàn 0 xu (id=14): {'✅' if good else '❌'}")

# --- 6. Bộ lọc dò bàn phủ đủ 4 mức ---
print("\n6. Bộ lọc dò bàn (QUICK_PLAY):")
cup.BOT_BET_XU = 20000
bot = FakeBot()
objs = bot.get_target_bet_objs()
vals = [o["value"] for o in objs]
good = sorted(vals) == sorted(cup.BOT_BET_LEVELS) and vals[0] == 20000
ok &= good
print(f"   Các mức sẽ dò: {vals}")
print(f"   Đủ 4 mức, ưu tiên mức đang dùng (20000) lên đầu: {'✅' if good else '❌'}")

print("\n" + "=" * 60)
print("✅ THANG MỨC CƯỢC HOẠT ĐỘNG ĐÚNG" if ok else "❌ CÒN LỖI")
sys.exit(0 if ok else 1)

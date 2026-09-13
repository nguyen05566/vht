#!/usr/bin/env python3
"""
arena18.py — Bot Caro (wrapper) — gamevh.net
Mức cược: 400 xu | KHÔNG đổi avatar | Tài khoản từ acc_valid_1.txt[17]
Giữ cơ chế chuyển xu định kỳ qua transfer_xu_bot.
"""
import os
import sys

# Đảm bảo import được caro_bot.py và transfer_xu_bot.py cùng thư mục
_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

# Chọn tài khoản từ file (chỉ setdefault — để runner override qua CARO_USER/CARO_PWWD)
os.environ.setdefault("CARO_ACC_FILE", "acc_valid_1.txt")
os.environ.setdefault("CARO_ACC_INDEX", "17")

from caro_bot import main  # noqa: E402

if __name__ == "__main__":
    main()

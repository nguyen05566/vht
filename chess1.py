#!/usr/bin/env python3
"""
chess1.py — Bot Cờ Vua (wrapper) — gamevh.net
Mức cược: 1000 xu | Engine: Stockfish 19
Tài khoản từ acc_valid_1.txt[1]
"""
import os, sys
_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)
os.environ.setdefault("CHESS_ACC_FILE", "acc_valid_1.txt")
os.environ.setdefault("CHESS_ACC_INDEX", "1")
os.environ.setdefault("CHESS_BET_XU", "1000")
from chess_bot import main
if __name__ == "__main__":
    main()

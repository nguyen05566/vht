#!/usr/bin/env python3
"""
╔══════════════════════════════════════════════════════════════════════╗
║  BOT CHẴN — gamevh.net — ALL-IN-ONE FILE                           ║
║  Engine luật + AI + WebSocket bot gộp 1 file                      ║
║  Tốc độ: bốc/ăn/đánh trong 0.5s, không chờ hết giờ               ║
╚══════════════════════════════════════════════════════════════════════╝
"""
import subprocess, sys, os, importlib, json, time, struct
import re, logging, asyncio, random, requests
from typing import List, Tuple, Dict, Optional, Set
from collections import Counter
from pathlib import Path

# ======================== LOGGING ========================
log = logging.getLogger("chan")
log.setLevel(logging.INFO)
if not log.handlers:
    h = logging.StreamHandler(sys.stdout)
    h.setFormatter(logging.Formatter("%(asctime)s %(message)s", datefmt="%H:%M:%S"))
    log.addHandler(h)

# ======================== SETUP ========================
REQUIRED = ["websockets", "requests"]
for pkg in REQUIRED:
    try:
        importlib.import_module(pkg)
    except ImportError:
        subprocess.run([sys.executable, "-m", "pip", "install", pkg, "-q",
                        "--break-system-packages"], stderr=subprocess.DEVNULL)
import websockets

# ==============================================================================
# PHẦN 1 — BỘ BÀI
# ==============================================================================
SO_NAMES  = ["Nhị", "Tam", "Tứ", "Ngũ", "Lục", "Thất", "Bát", "Cửu"]
CHAT_NAMES = ["Vạn", "Văn", "Sách"]
CHI = 24
N_KINDS = 25

def card_name(cid: int) -> str:
    if cid == CHI: return "Chi chi"
    return f"{SO_NAMES[cid // 3]} {CHAT_NAMES[cid % 3]}"

def group_of(cid: int) -> int:
    return 8 if cid == CHI else cid // 3

def chat_of(cid: int) -> int:
    return 0 if cid == CHI else cid % 3

RED = frozenset({18, 20, 21, 23, 24})
def is_red(cid: int) -> bool: return cid in RED

LEO = (21, 20, 24)
TOM = (3, 5, 16)
NHI_VAN = 0; TU_VAN = 6; NGU_VAN = 9; NGU_SACH = 11; BAT_VAN = 18; BAT_VANV = 19

def full_deck() -> List[int]:
    return [c for c in range(N_KINDS) for _ in range(4)]

# ==============================================================================
# PHẦN 2 — XẾP BÀI & GIẢI TRÒN BÀI (Ù)
# ==============================================================================
Meld = Tuple[str, Tuple[int, ...]]

def analyze(hand: List[int]) -> Dict:
    cnt = Counter(hand)
    thien_khai = [c for c, n in cnt.items() if n == 4]
    chans: List[int] = []
    for c, n in cnt.items():
        chans.extend([c] * (n // 2))
    le = sorted(c for c, n in cnt.items() if n % 2 == 1)
    by_g: Dict[int, List[int]] = {}
    for c in le:
        by_g.setdefault(group_of(c), []).append(c)
    ba_dau, cas, ques = [], [], []
    for g, cs in by_g.items():
        if g == 8: ques.extend(cs)
        elif len(cs) == 3: ba_dau.append(tuple(cs))
        elif len(cs) == 2: cas.append(tuple(cs))
        else: ques.extend(cs)
    return {"chan": sorted(chans), "thien_khai": sorted(thien_khai),
            "ca": sorted(cas), "ba_dau": sorted(ba_dau), "que": sorted(ques),
            "so_chan": len(chans)}

def partition(cards: List[int]) -> Optional[Tuple[int, List[Meld]]]:
    if len(cards) % 2: return None
    memo: Dict[tuple, Optional[Tuple[int, List[Meld]]]] = {}
    def solve(state: tuple) -> Optional[Tuple[int, List[Meld]]]:
        if not any(state): return (0, [])
        if state in memo: return memo[state]
        lst = list(state)
        i = next(k for k, v in enumerate(lst) if v)
        best = None
        if lst[i] >= 2:
            lst[i] -= 2; sub = solve(tuple(lst)); lst[i] += 2
            if sub is not None:
                cand = (sub[0] + 1, [("chan", (i, i))] + sub[1])
                if best is None or cand[0] > best[0]: best = cand
        if group_of(i) != 8:
            for j in range(i + 1, len(lst)):
                if lst[j] and group_of(j) == group_of(i):
                    lst[i] -= 1; lst[j] -= 1; sub = solve(tuple(lst)); lst[i] += 1; lst[j] += 1
                    if sub is not None:
                        cand = (sub[0], [("ca", (i, j))] + sub[1])
                        if best is None or cand[0] > best[0]: best = cand
        memo[state] = best; return best
    v = [0] * 25
    for c in cards: v[c] += 1
    return solve(tuple(v))

def check_u(hand: List[int], melds: List[Meld], winning: int) -> Optional[Dict]:
    total = len(hand) + 1 + sum(2 if m[0] in ("chan", "ca") else 4 for m in melds)
    if total != 20: return None
    res = partition(list(hand) + [winning])
    if res is None: return None
    chan_hand, sets_hand = res
    chan_meld = n_sets_meld = 0
    for kind, _ in melds:
        if kind == "chan": chan_meld += 1; n_sets_meld += 1
        elif kind == "ca": n_sets_meld += 1
        else: chan_meld += 2; n_sets_meld += 2
    tong_bo = len(sets_hand) + n_sets_meld
    tong_chan = chan_hand + chan_meld
    if tong_bo != 10 or tong_chan < 6: return None
    return {"bo": sets_hand, "tong_bo": tong_bo, "tong_chan": tong_chan}

def waiting_cards(hand: List[int], melds: List[Meld]) -> List[int]:
    return [c for c in range(25) if check_u(hand, melds, c)]

# ==============================================================================
# PHẦN 3 — 18 ĐIỀU LUẬT CẤM
# ==============================================================================
NGHI_AN_TIEN = {"trai_vi", "an_treo_tranh", "chieu_ma_an_thuong", "bo_u"}
LOI_BAO = {
    "bo_chan_an_chan", "bo_chan_an_ca", "bo_ca_an_ca", "bo_chan_danh_chan",
    "danh_ca_an_ca", "xe_ca_an_ca", "danh_roi_an_dung_quan", "danh_doi_chan",
    "an_roi_danh_dung_con", "an_ca_roi_an_chan_cung_hang",
    "danh_ca_khi_da_an_ca", "an_ca_danh_con_cung_hang",
    "an_chon_ca", "co_chan_cau_ca", "an_ca_chuyen_cho",
}
TEN_LOI = {
    "trai_vi": "Trái vỉ", "an_treo_tranh": "Ăn treo tranh",
    "chieu_ma_an_thuong": "Chíu được nhưng lại ăn thường",
    "an_chon_ca": "Ăn chọn cạ", "an_ca_chuyen_cho": "Ăn cạ chuyển chờ",
    "co_chan_cau_ca": "Có chắn cấu cạ", "bo_chan_an_chan": "Bỏ chắn ăn chắn",
    "bo_chan_an_ca": "Bỏ chắn ăn cạ", "bo_ca_an_ca": "Bỏ cạ ăn cạ",
    "bo_chan_danh_chan": "Bỏ chắn đánh chắn", "danh_ca_an_ca": "Đánh cạ ăn cạ",
    "xe_ca_an_ca": "Xé cạ ăn cạ", "danh_roi_an_dung_quan": "Đánh 1 quân rồi lại ăn đúng quân đó",
    "danh_doi_chan": "Đánh đôi chắn đi", "an_roi_danh_dung_con": "Ăn 1 con rồi lại đánh đúng con đó",
    "an_ca_roi_an_chan_cung_hang": "Ăn cạ rồi lại ăn chắn cùng hàng",
    "danh_ca_khi_da_an_ca": "Đánh cạ khi đã ăn cạ",
    "an_ca_danh_con_cung_hang": "Ăn cạ đánh con cùng hàng",
    "bo_u": "Bỏ ù", "danh_pha_chan": "Đánh phá chắn",
    "khong_co_quan": "Không có quân", "khac_hang": "Khác hàng",
    "chi_khong_co_ca": "Chi chi không có cạ",
}

class PlayerState:
    def __init__(self):
        self.hand: List[int] = []
        self.melds: List = []
        self.danh: List[int] = []
        self.an_quan: Set[int] = set()
        self.ha_quan: Set[int] = set()
        self.bo_an_chan: Set[int] = set()
        self.bo_an_ca: Set[int] = set()
        self.bo_u: bool = False
        self.loi: List[str] = []
    def da_danh_ca(self) -> bool:
        by_g: Dict[int, Set[int]] = {}
        for c in self.danh: by_g.setdefault(group_of(c), set()).add(c)
        return any(g != 8 and len(s) >= 2 for g, s in by_g.items())
    def da_an_ca(self) -> bool:
        return any(k == "ca" for k, _ in self.melds)
    def hang_da_an_ca(self) -> Set[int]:
        return {group_of(v[0]) for k, v in self.melds if k == "ca"}
    def hang_da_danh(self) -> Set[int]:
        return {group_of(c) for c in self.danh}
    def ghi_loi(self, ma: str):
        if ma not in self.loi: self.loi.append(ma)
    def bi_bao(self) -> bool:
        return any(l in LOI_BAO for l in self.loi)

def kiem_tra_an(st: PlayerState, quan_chieu: int, quan_ha: int, la_chieu: bool = False) -> Optional[str]:
    if quan_ha not in st.hand: return "khong_co_quan"
    if group_of(quan_ha) != group_of(quan_chieu): return "khac_hang"
    if quan_chieu == CHI and quan_ha != CHI: return "chi_khong_co_ca"
    an_chan = (quan_ha == quan_chieu)
    cnt = Counter(st.hand)
    if cnt[quan_chieu] >= 3 and not la_chieu: return "chieu_ma_an_thuong"
    if an_chan:
        if quan_chieu in st.bo_an_chan: return "bo_chan_an_chan"
    else:
        if quan_ha in st.bo_an_chan: return "bo_chan_an_ca"
        if quan_ha in st.bo_an_ca or quan_chieu in st.bo_an_ca: return "bo_ca_an_ca"
    if not an_chan:
        if cnt[quan_chieu] >= 1: return "an_treo_tranh"
        if cnt[quan_ha] >= 2: return "co_chan_cau_ca"
        for other, n in cnt.items():
            if other != quan_ha and group_of(other) == group_of(quan_ha) and n % 2 == 1:
                return "an_chon_ca"
        a = analyze(st.hand)
        if a["so_chan"] >= 5 and not a["ba_dau"] and quan_ha in a["que"]:
            return "an_ca_chuyen_cho"
        if st.da_danh_ca(): return "danh_ca_an_ca"
        if group_of(quan_ha) in st.hang_da_danh(): return "xe_ca_an_ca"
    else:
        if group_of(quan_chieu) in st.hang_da_an_ca(): return "an_ca_roi_an_chan_cung_hang"
    if quan_chieu in st.danh: return "danh_roi_an_dung_quan"
    return None

def kiem_tra_danh(st: PlayerState, quan: int) -> Optional[str]:
    if quan not in st.hand: return "khong_co_quan"
    cnt = Counter(st.hand)
    if quan in st.bo_an_chan: return "bo_chan_danh_chan"
    if quan in st.danh: return "danh_doi_chan"
    if quan in st.an_quan or quan in st.ha_quan: return "an_roi_danh_dung_con"
    if group_of(quan) in st.hang_da_an_ca(): return "an_ca_danh_con_cung_hang"
    if st.da_an_ca():
        g = group_of(quan)
        if g != 8 and any(group_of(d) == g for d in st.danh): return "danh_ca_khi_da_an_ca"
    if cnt[quan] >= 2:
        a = analyze(st.hand)
        if a["que"] or a["ba_dau"]: return "danh_pha_chan"
    return None

# ==============================================================================
# PHẦN 4 — 25 CƯỚC SẮC & TÍNH ĐIỂM
# ==============================================================================
BANG_CUOC: Dict[str, Tuple[int, int]] = {
    "xuong": (2, 0), "thong": (3, 1), "chi": (3, 1), "pha_thien": (12, 9),
    "thien_u": (3, 1), "dia_u": (3, 1), "chieu": (3, 1), "chieu_u": (4, 1),
    "bon": (3, 1), "u_bon": (4, 1), "thien_khai": (3, 1), "thap_thanh": (12, 9),
    "tam_do": (8, 5), "leo": (5, 2), "tom": (4, 1), "bach_dinh": (7, 4),
    "bach_thu": (4, 1), "bach_thu_chi": (6, 3), "kinh_tu_chi": (12, 9),
    "hoa_roi_cua_phat": (20, 17), "dong_tu_hai_hoa": (20, 17),
    "ca_loi_san_dinh": (20, 17), "ca_nhay_dau_thuyen": (20, 17),
    "ngu_ong_bat_ca": (30, 0), "nha_lau_xe_hoi_hoa_roi_cua_phat": (30, 0),
}
TEN_CUOC = {
    "xuong": "Xuông", "thong": "Thông", "chi": "Chì", "pha_thien": "Phá thiên",
    "thien_u": "Thiên ù", "dia_u": "Địa ù", "chieu": "Có chíu", "chieu_u": "Chíu ù",
    "bon": "Có ăn bòn", "u_bon": "Ù bòn", "thien_khai": "Có thiên khai",
    "thap_thanh": "Thập thành", "tam_do": "Tám đỏ", "leo": "Có lèo", "tom": "Có tôm",
    "bach_dinh": "Bạch định", "bach_thu": "Bạch thủ", "bach_thu_chi": "Bạch thủ chi",
    "kinh_tu_chi": "Kính tứ chi", "hoa_roi_cua_phat": "Hoa rơi cửa phật",
    "dong_tu_hai_hoa": "Đồng tử hái hoa", "ca_loi_san_dinh": "Cá lội sân đình",
    "ca_nhay_dau_thuyen": "Cá nhảy đầu thuyền", "ngu_ong_bat_ca": "Ngư ông bắt cá",
    "nha_lau_xe_hoi_hoa_roi_cua_phat": "Nhà lầu xe hơi hoa rơi cửa phật",
}
THU_TU = ["thong", "chi", "thien_u", "dia_u", "chieu_u", "u_bon", "pha_thien",
          "thap_thanh", "kinh_tu_chi", "bach_dinh", "tam_do", "bach_thu_chi",
          "bach_thu", "hoa_roi_cua_phat", "dong_tu_hai_hoa", "ca_loi_san_dinh",
          "ca_nhay_dau_thuyen", "ngu_ong_bat_ca", "nha_lau_xe_hoi_hoa_roi_cua_phat",
          "leo", "tom", "chieu", "thien_khai", "bon", "xuong"]

def tinh_cuoc(*, hand, melds, winning, ctx) -> List[str]:
    all_cards = list(hand) + [winning]
    ha_chieu = []
    for k, v in melds:
        all_cards.extend(v); ha_chieu.extend(v)
    cnt = Counter(all_cards)
    n_do = sum(1 for c in all_cards if is_red(c))
    n_chan = ctx.get("tong_chan", 0)
    out = []
    if ctx.get("thong"): out.append("thong")
    if ctx.get("chi"): out.append("chi")
    if ctx.get("thien_u"): out.append("thien_u")
    if ctx.get("dia_u"): out.append("dia_u")
    if ctx.get("chieu_u"): out.append("chieu_u")
    if ctx.get("u_bon"): out.append("u_bon")
    if ctx.get("khong_chan_ban_dau"): out.append("pha_thien")
    n_chieu = ctx.get("so_chieu", 0)
    if n_chieu and not ctx.get("chieu_u"): out.append("chieu")
    if ctx.get("so_bon", 0) and not ctx.get("u_bon"): out.append("bon")
    if ctx.get("so_thien_khai", 0): out.append("thien_khai")
    if n_chan == 10: out.append("thap_thanh")
    if n_do == 0: out.append("bach_dinh")
    if n_do == 8: out.append("tam_do")
    if cnt[CHI] == 4: out.append("kinh_tu_chi")
    if all(cnt[c] >= 1 for c in LEO): out.append("leo")
    if all(cnt[c] >= 1 for c in TOM): out.append("tom")
    bt = ctx.get("bach_thu", False)
    if bt: out.append("bach_thu_chi" if winning == CHI else "bach_thu")
    co_ngu_van = NGU_VAN in ha_chieu
    hoa = None
    if bt and ctx.get("chi"):
        if winning == NHI_VAN and co_ngu_van:
            hoa = "hoa_roi_cua_phat"
            if cnt[NGU_VAN] >= 2 and cnt[TU_VAN] >= 2 and ctx.get("co_san_ngu_tu"):
                hoa = "nha_lau_xe_hoi_hoa_roi_cua_phat"
        elif winning == NHI_VAN and BAT_VANV in ha_chieu: hoa = "dong_tu_hai_hoa"
        elif winning == BAT_VAN and co_ngu_van: hoa = "ca_loi_san_dinh"
        elif winning == BAT_VAN and NGU_SACH in ha_chieu: hoa = "ca_nhay_dau_thuyen"
        elif winning == BAT_VAN and cnt[CHI] >= 2 and cnt[NGU_SACH] >= 2: hoa = "ngu_ong_bat_ca"
    if hoa:
        out = [c for c in out if c not in ("chi", "bach_thu", "bach_thu_chi")]
        out.append(hoa)
    if not out: out.append("xuong")
    return [c for c in THU_TU if c in out]

def tinh_diem(cuoc: List[str]) -> int:
    if not cuoc: return 0
    diem = [BANG_CUOC[c][0] for c in cuoc]
    i = diem.index(max(diem))
    return diem[i] + sum(BANG_CUOC[c][1] for j, c in enumerate(cuoc) if j != i)

def xuong(cuoc: List[str]) -> str:
    return " ".join(TEN_CUOC[c] for c in cuoc)

# ==============================================================================
# PHẦN 5 — AI CHƠI CHẮN
# ==============================================================================
class ChanAI(PlayerState):
    def __init__(self, ten: str = "AI"):
        super().__init__()
        self.ten = ten
        # seen_out: số lá ĐÃ LỘ ra ngoài tay (cửa chưới + mỏ lộ của đối thủ).
        # Mỗi lá chỉ được đếm ĐÚNG 1 LẦN khi rời tay ai đó ra vùng mở,
        # ăn/chíu/bốc không đếm lại (tránh "bot nghĩ 0 lá nhưng thực tế vẫn còn").
        self.seen_out: Counter = Counter()
        self.khong_chan_ban_dau = False
        self.so_chieu = 0; self.so_bon = 0; self.so_thien_khai = 0

    def nhan_bai(self, cards: List[int]):
        self.__init__(self.ten)
        self.hand = sorted(cards)
        a = analyze(self.hand)
        self.khong_chan_ban_dau = (a["so_chan"] == 0)
        self.so_thien_khai = len(a["thien_khai"])

    def thay_quan(self, cid: int):
        """(Deprecated - giữ tương thích) Trước đây đếm mọi sự kiện nên bị trùng
        khi ăn. Giờ dùng seen_out + _con_lai state-based."""
        pass

    def tang_lo(self, cid: int):
        """Một lá mới lộ ra vùng mở (đánh ra / lộ từ tay đối thủ) — đếm 1 lần."""
        self.seen_out[cid] += 1

    def giam_lo(self, cid: int):
        """Lá đã lộ vừa được NHẬT VỀ MỎ CỦA MÌNH — chuyển từ seen_out sang melds
        (tránh đếm trùng 2 lần: seen_out + own_melds)."""
        if self.seen_out.get(cid, 0) > 0: self.seen_out[cid] -= 1

    def da_lo(self, cid: int) -> int:
        """Tổng lá cid đã lộ ra cửa chưới (mình + đối thủ) — dùng cho heuristic."""
        return self.danh.count(cid) + self.seen_out.get(cid, 0)

    def _diem_bai(self, hand: List[int], melds) -> float:
        a = analyze(hand)
        n_chan = a["so_chan"] + sum(1 if k == "chan" else (2 if k == "chieu" else 0) for k, _ in melds)
        n_ca = len(a["ca"]) + sum(1 for k, _ in melds if k == "ca")
        n_bo = n_chan + n_ca + len(a["ba_dau"])
        s = n_chan * 10.0 + n_ca * 3.0 + len(a["ba_dau"]) * 3.5
        s -= len(a["que"]) * 2.0
        if n_chan >= 6: s += 12
        s += min(n_bo, 10) * 1.5
        s += sum(0.3 for c in hand if is_red(c))
        return s

    def _con_lai(self, cid: int) -> int:
        """Số lá cid còn CÓ THỂ BỐC được = 4 − (trong tay mình + trong mỏ mình
        + đã lộ ra cửa chưới/mỏ đối thủ). Tính trực tiếp từ trạng thái nên
        KHÔNG bao giờ âm oan do đếm trùng sự kiện ăn/chíu/bốc."""
        da_ngoai = (self.hand.count(cid)
                    + sum(v.count(cid) for _, v in self.melds)
                    + self.danh.count(cid)
                    + self.seen_out.get(cid, 0))
        return max(0, 4 - da_ngoai)

    def chon_nuoc_an(self, quan_chieu: int, la_chieu_duoc: bool = False) -> Optional[Tuple[int, str]]:
        cnt = Counter(self.hand)
        if cnt[quan_chieu] >= 3:
            if kiem_tra_an(self, quan_chieu, quan_chieu, la_chieu=True) is None:
                return (quan_chieu, "chieu")
        ung_vien = []
        for q in set(self.hand):
            if group_of(q) != group_of(quan_chieu): continue
            loi = kiem_tra_an(self, quan_chieu, q)
            if loi is not None: continue
            loai = "chan" if q == quan_chieu else "ca"
            h2 = list(self.hand); h2.remove(q)
            m2 = self.melds + [(loai, (quan_chieu, q))]
            diem = self._diem_bai(h2, m2)
            if loai == "chan": diem += 6
            ung_vien.append((diem, q, loai))
        if not ung_vien: return None
        ung_vien.sort(reverse=True)
        diem_an, q, loai = ung_vien[0]
        if diem_an < self._diem_bai(self.hand, self.melds) - 0.5: return None
        return (q, loai)

    def chon_quan_danh(self) -> Optional[int]:
        ung_vien = []
        for q in set(self.hand):
            if kiem_tra_danh(self, q) is not None: continue
            h2 = list(self.hand); h2.remove(q)
            diem = self._diem_bai(h2, self.melds)
            cho = waiting_cards(h2, self.melds)
            if cho: diem += 8 + sum(self._con_lai(c) for c in cho) * 0.8
            diem += self.da_lo(q) * 0.4
            ung_vien.append((diem, q))
        if not ung_vien: return self._danh_bat_dac_di()
        ung_vien.sort(reverse=True)
        return ung_vien[0][1]

    def _danh_bat_dac_di(self) -> Optional[int]:
        if not self.hand: return None
        xep = []
        for q in set(self.hand):
            loi = kiem_tra_danh(self, q)
            nang = 2 if (loi in LOI_BAO) else (1 if loi else 0)
            h2 = list(self.hand); h2.remove(q)
            xep.append((nang, -self._diem_bai(h2, self.melds), q))
        xep.sort()
        return xep[0][2]

    def kiem_u(self, quan: int) -> Optional[Dict]:
        return check_u(self.hand, self.melds, quan)

    def dang_cho(self) -> List[int]:
        return waiting_cards(self.hand, self.melds)

    def thuc_hien_an(self, quan_chieu: int, quan_ha: int, loai: str):
        if loai == "chieu":
            for _ in range(3): self.hand.remove(quan_chieu)
            self.melds.append(("chieu", (quan_chieu,) * 4))
            self.so_chieu += 1; self.ha_quan.add(quan_chieu)
        else:
            self.hand.remove(quan_ha)
            self.melds.append((loai, (quan_chieu, quan_ha)))
            self.ha_quan.add(quan_ha)
            if loai == "chan" and self.hand.count(quan_ha) >= 1: self.so_bon += 1
        self.an_quan.add(quan_chieu)
        # FIX tracking: quân chưới đã được đếm vào seen_out lúc đối thủ đánh;
        # giờ nó NHẬT VỀ MỎ CỦA MÌNH → chuyển từ seen_out sang own_melds (_con_lai
        # trừ melds trực tiếp) — KHÔNG đếm lại, tránh "nghĩ 0 lá nhưng thực tế còn".
        self.giam_lo(quan_chieu)

    def thuc_hien_danh(self, quan: int):
        # Lá mình đánh được ghi qua self.danh (_con_lai trừ trực tiếp) —
        # KHÔNG tang_lo nữa để tránh trừ kép danh + seen_out.
        self.hand.remove(quan); self.danh.append(quan)

    def ghi_bo_an(self, quan_chieu: int):
        cnt = Counter(self.hand)
        if cnt[quan_chieu] >= 1: self.bo_an_chan.add(quan_chieu)
        elif any(group_of(c) == group_of(quan_chieu) for c in self.hand): self.bo_an_ca.add(quan_chieu)


# ==============================================================================
# PHẦN 6 — BINARY PROTOCOL
# ==============================================================================
CMD_MAP = {
    300: "PONG", 301: "PING", 302: "LOGIN", 303: "ALERT", 304: "RIBBON_MESSAGE",
    305: "FEEDBACK", 306: "RELOAD", 307: "RELOAD_APP", 308: "NAVIGATE",
    309: "ACHM_ACHIEVED", 310: "PM.UNREAD",
    311: "BROADCAST", 312: "INVITE", 313: "GET_CLIENT_MODE", 314: "SET_CLIENT_MODE",
    315: "CONFIG", 316: "REQUEST_FREE_CHIP", 317: "TRANSFER",
    318: "LIST_AVATAR_CATEGORY", 319: "LIST_AVATAR", 320: "BUY_AVATAR",
    321: "REFINE_PROFILE", 322: "GET_NICK_CHANGE_COUNT", 323: "CHANGE_NICK_NAME",
    324: "CHANGE_PASSWORD", 325: "UPDATE_PROFILE", 326: "CREATE_LOGIN_TOKEN",
    327: "LIST_NOTE", 328: "CONFIRM_NAVIGATE", 329: "REPORT_ABUSE",
    330: "ACTIVATE", 331: "CHAT.SEND", 332: "CHAT.SUBS", 333: "CHAT.LOAD",
    334: "GET_REMAIN_DURATION", 335: "CHAT.MSG", 336: "GET_CURRENT_TIME",
    337: "GET_CAPTCHA_IMAGE", 338: "RESET_PASSWORD", 339: "REGISTER",
    340: "INVITE_REQ", 341: "EXPRESS_EMOTION", 342: "CHAT.UNSUBS",
    343: "REQUEST_BUY_IN", 344: "SHOW_EMOTION", 345: "CLIENT_MODE_CHANGED",
    351: "GET_REGISTER_SYNTAX", 352: "GET_SC_RECHARGE_DATA",
    353: "RECHARGE_BY_SC", 354: "GET_SMS_RECHARGE_DATA",
    355: "RECHARGE_BY_GIFT_CODE", 356: "GET_GOOGLE_RECHARGE_DATA",
    357: "RECHARGE_BY_GOOGLE", 358: "GET_IOS_RECHARGE_DATA",
    359: "RECHARGE_BY_IOS",
    401: "ENTER_PLACE", 402: "ENTER_CHILD_PLACE", 403: "ENTER_PARENT_PLACE",
    404: "ENTER_SIBLING_PLACE", 405: "CREATE_RULE",
    406: "PLAYER_ENTERED", 407: "PLAYER_EXITED", 408: "QUICK_PLAY",
    409: "SET_TABLE_PASSWORD", 410: "KICK_PLAYER",
    411: "LIST_ZONE_TABLE", 412: "LIST_ZONE_ROOM",
    413: "LIST_BET_AMT", 414: "GET_TABLE_DATA", 415: "TABLE_IN_ROOM_CHANGED",
    416: "SLOT_IN_TABLE_CHANGED", 417: "START_MATCH",
    418: "GAMEOVER", 419: "ENTER_STATE", 420: "SET_TURN",
    421: "SET_PLAYER_STATUS", 422: "SET_PLAYER_POINT", 423: "SET_PLAYER_ATTR",
    424: "SHOW_LUCKY_WHEEL", 425: "SPIN_LUCKY_WHEEL", 426: "GET_REMAIN_SPIN",
    427: "SHARE_MY_LEVEL", 428: "UPDATE_LEVEL", 429: "BUY_ITEM",
    430: "GET_CURRENT_PATH", 431: "BALANCE_CHANGED", 432: "OWNER_CHANGED",
    433: "GET_TABLE_DATA_EX", 434: "SET_READY", 435: "ENTER_PATH_PLACE",
    436: "SCORE_CHANGED", 437: "CREATE_SECRET_TABLE", 438: "ENTER_SECRET_TABLE",
    439: "JUDGE_PLAYER",
    501: "BET", 502: "PLAY", 505: "CHAT", 518: "HIGHLIGHT",
    521: "TAKE_CARD", 522: "SHOW_PLAYER_CARD", 523: "CLEAR_CARDS",
    524: "SET_CARDS", 525: "SELECT_CARDS", 527: "COMPARE_BAND",
    528: "SEND_CARD", 529: "MOVE", 530: "CHANGE_PIECE",
    531: "SET_REMAIN_TURN", 532: "ADD_LOG",
    533: "ASK_DRAW", 534: "SURRENDER", 535: "RETREAT",
    536: "ACCEPT", 537: "HIT", 538: "STAY", 539: "FIRE_CARD",
    540: "PASS_TURN", 541: "SORT_CARD", 542: "TAKE",
    543: "CHIU", 544: "REMOVE", 545: "DROP_BAND",
    546: "DROP_AVAILABLE_BAND",
}

class BinaryReader:
    def __init__(self, data: bytes):
        self.data = data; self.pos = 0
    def remaining(self) -> int: return len(self.data) - self.pos
    def u8(self) -> int:
        if self.pos >= len(self.data): return 0
        v = self.data[self.pos]; self.pos += 1; return v
    def i8(self) -> int:
        if self.pos >= len(self.data): return 0
        v = struct.unpack_from('>b', self.data, self.pos)[0]; self.pos += 1; return v
    def i16(self) -> int:
        if self.pos + 2 > len(self.data): return 0
        v = struct.unpack_from('>h', self.data, self.pos)[0]; self.pos += 2; return v
    def u16(self) -> int:
        if self.pos + 2 > len(self.data): return 0
        v = struct.unpack_from('>H', self.data, self.pos)[0]; self.pos += 2; return v
    def i32(self) -> int:
        if self.pos + 4 > len(self.data): return 0
        v = struct.unpack_from('>i', self.data, self.pos)[0]; self.pos += 4; return v
    def i64(self) -> int:
        if self.pos + 8 > len(self.data): return 0
        hi = struct.unpack_from('>i', self.data, self.pos)[0]
        lo = struct.unpack_from('>I', self.data, self.pos + 4)[0]
        self.pos += 8; return (hi << 32) | lo
    def read_ascii(self) -> str:
        if self.pos >= len(self.data): return ""
        n = self.u8()
        if self.pos + n > len(self.data): n = len(self.data) - self.pos
        s = self.data[self.pos:self.pos + n].decode('ascii', 'replace')
        self.pos += n; return s
    def read_utf(self) -> str:
        if self.pos + 2 > len(self.data): return ""
        n = self.i16()
        if n <= 0: return ""
        byte_len = n * 2
        if self.pos + byte_len > len(self.data): byte_len = len(self.data) - self.pos
        s = self.data[self.pos:self.pos + byte_len].decode('utf-16-be', 'replace')
        self.pos += byte_len; return s
    def read_string(self) -> str: return self.read_utf()
    def read_bytes(self) -> List[int]:
        if self.pos + 2 > len(self.data): return []
        n = self.i16()
        if self.pos + n > len(self.data): n = len(self.data) - self.pos
        result = list(self.data[self.pos:self.pos + n])
        self.pos += n; return result
    def read_byte_array(self) -> List[int]: return self.read_bytes()
    def read_long(self) -> int: return self.i64()
    def read_int(self) -> int: return self.i32()
    def read_short(self) -> int: return self.i16()
    def read_command(self) -> str:
        first = self.i8()
        if first < 0:
            n = -first
            if self.pos + n > len(self.data): n = len(self.data) - self.pos
            s = self.data[self.pos:self.pos + n].decode('ascii', 'replace')
            self.pos += n; return s
        second = self.u8()
        cmd_id = (first << 8) | second
        return CMD_MAP.get(cmd_id, f"CMD_{cmd_id}")

class BinaryWriter:
    def __init__(self): self.parts = []
    def u8(self, v: int): self.parts.append(struct.pack('>B', v & 0xFF))
    def i8(self, v: int): self.parts.append(struct.pack('>b', max(-128, min(127, v))))
    def i16(self, v: int): self.parts.append(struct.pack('>h', max(-32768, min(32767, v))))
    def i32(self, v: int): self.parts.append(struct.pack('>i', v))
    def i64(self, v: int): self.parts.append(struct.pack('>q', v))
    def write_ascii(self, s: str):
        encoded = s.encode('ascii', 'replace'); self.u8(len(encoded)); self.parts.append(encoded)
    def write_utf(self, s: str):
        encoded = s.encode('utf-16-be'); self.i16(len(encoded) // 2); self.parts.append(encoded)
    def write_string(self, s: str): self.write_utf(s)
    def write_byte_array(self, arr: List[int]):
        self.i16(len(arr)); self.parts.append(bytes(arr))
    def write_command(self, cmd: str):
        cmd_id = next((k for k, v in CMD_MAP.items() if v == cmd), None)
        if cmd_id: self.parts.append(struct.pack('>H', cmd_id))
        else:
            b = cmd.encode('ascii'); self.i8(-len(b)); self.parts.append(b)
    def build(self) -> bytes: return b''.join(self.parts)


def map_card_id(server_id: int) -> Optional[int]:
    """Map sprite ID → engine card ID: sprite_id % 25"""
    if 0 <= server_id < N_KINDS: return server_id
    engine_id = server_id % N_KINDS
    if 0 <= engine_id < N_KINDS: return engine_id
    return None

# ======================== CONFIG ========================
WS_URL = "wss://gamevh.net/ws/gameServer"
GAME_URL = "https://gamevh.net/play/chan/0"
# Cho phép override qua env khi test nhiều instance trên workspace
USER = os.environ.get("CHAN_USER") or "ngan100"; PWWD = os.environ.get("CHAN_PWWD") or "nhat123456"
VERSION = "5.0.2"; GAME_ID = "chan"
RUNTIME = int(float(os.environ.get("CHAN_RUNTIME_HOURS") or 5) * 3600); BOT_BET_XU = 50
# CHAN_FAST=1 → chờ 0.8s/lượt thay vì 3s (chỉ dùng khi test trên workspace)
_STEP = 0.8 if os.environ.get("CHAN_FAST") else 3.0
BOT_MATCH_DURATION = '1800'; BOT_TURN_DURATION = '60'

# ======================== BOT ========================
class ChanBot:
    def __init__(self):
        self.ws = None; self.ai = ChanAI("Bot")
        self.slot = -1; self.is_playing = False; self.in_table = False
        self.user = USER; self.pwwd = PWWD   # cho phép driver test đổi acc từng instance
        self.ready = False; self.players = {}; self.nickname = ""
        self.token = 0; self.cookie = ""; self.place_path = "Lobby.chan.0"
        self.lock_key = ""; self.start_time = None; self.last_activity = time.time()
        self._running = True; self.wins = 0; self.losses = 0; self.draws = 0; self.total_games = 0
        self.pending_move = False; self.bet_amts = []; self._bet_amts_loaded = False
        self._joining_table = False; self.table_id = None; self.player_slot_by_id = {}
        self._pending_kick_player_id = None; self.opponent_gone_at = None
        self._table_lost_at = None; self._want_rejoin = False; self._rejoining = False
        self._rejoin_attempts = 0; self.last_fired_card = None; self.waiting_for_action = False
        self._last_played = None

    @property
    def running(self) -> bool: return self._running
    def stop(self): self._running = False

    def save_stats(self):
        try:
            with open("chan_bot_stats.json", "w") as f:
                json.dump({'W': self.wins, 'L': self.losses, 'D': self.draws, 'G': self.total_games}, f)
        except Exception: pass

    # ======================== PACKET BUILDERS ========================
    def make_login(self) -> bytes:
        w = BinaryWriter(); w.write_command("LOGIN"); w.write_ascii(self.nickname)
        w.i32(self.token); w.write_ascii(VERSION); w.write_ascii(self.lock_key)
        w.write_ascii(GAME_ID); w.i8(1); return w.build()
    def make_enter(self, path: str, pw="", mode=1) -> bytes:
        w = BinaryWriter(); w.write_command("ENTER_PLACE"); w.write_ascii(path)
        w.write_utf(pw); w.i8(mode); return w.build()
    def make_list_bet_amt(self) -> bytes:
        w = BinaryWriter(); w.write_command("LIST_BET_AMT"); return w.build()
    def resolve_bet_amt_id(self) -> Optional[int]:
        if not self.bet_amts: return None
        for ba in self.bet_amts:
            if ba['value'] == BOT_BET_XU: return ba['id']
        lower = [ba for ba in self.bet_amts if 0 < ba['value'] <= BOT_BET_XU]
        if lower: return max(lower, key=lambda x: x['value'])['id']
        return 0
    def make_create_rule(self) -> bytes:
        bet_amt_id = self.resolve_bet_amt_id()
        if bet_amt_id is None: bet_amt_id = 0
        args = [("matchDuration", BOT_MATCH_DURATION), ("turnDuration", BOT_TURN_DURATION),
                ("accDuration", "0"), ("blockSoftware", "0")]
        w = BinaryWriter(); w.write_command("CREATE_RULE"); w.i8(bet_amt_id); w.i8(len(args))
        for name, val in args: w.write_ascii(name); w.write_utf(val)
        return w.build()
    def make_get_table(self) -> bytes:
        w = BinaryWriter(); w.write_command("GET_TABLE_DATA_EX"); w.write_ascii(""); return w.build()
    def make_play(self, card_id: int) -> bytes:
        w = BinaryWriter(); w.write_command("PLAY"); w.i16(card_id); return w.build()
    def make_take_card(self) -> bytes:
        w = BinaryWriter(); w.write_command("TAKE_CARD"); return w.build()
    def make_chiu(self) -> bytes:
        w = BinaryWriter(); w.write_command("CHIU"); return w.build()
    def make_pass(self) -> bytes:
        w = BinaryWriter(); w.write_command("PASS_TURN"); return w.build()
    def make_drop_band(self, spread_ids: List[int]) -> bytes:
        w = BinaryWriter(); w.write_command("DROP_BAND"); w.write_byte_array(spread_ids); return w.build()
    def make_drop_available_band(self) -> bytes:
        w = BinaryWriter(); w.write_command("DROP_AVAILABLE_BAND"); return w.build()
    def make_pong(self) -> bytes:
        w = BinaryWriter(); w.write_command("PONG"); return w.build()
    def make_ready(self) -> bytes:
        if self.is_playing: return b''
        w = BinaryWriter(); w.write_command("SET_READY"); return w.build()
    def make_kick_player(self, player_id: int) -> bytes:
        w = BinaryWriter(); w.write_command("KICK_PLAYER"); w.i64(player_id); return w.build()

    async def send(self, data: bytes):
        if self.ws and data:
            try: await self.ws.send(data)
            except Exception: pass

    async def create_new_table(self):
        if not self._bet_amts_loaded:
            self._bet_amts_loaded = False
            await self.send(self.make_list_bet_amt())
        else:
            await self.send(self.make_create_rule())

    # ======================== GAME LOGIC ========================
    async def do_play(self):
        """AI chọn và đánh bài — NHANH, không chờ hết giờ."""
        if not self.is_playing or not self._running or self.slot < 0:
            self.pending_move = False; return
        if not self.ai.hand:
            log.warning(f"[AI] Không có bài trên tay! slot={self.slot} playing={self.is_playing}")
            self.pending_move = False; return
        self.pending_move = False

        # Kiểm tra chờ ù (kèm số lá còn bốc được — để soi tracking)
        cho = self.ai.dang_cho()
        if cho:
            info = [f"{card_name(c)}(còn {self.ai._con_lai(c)})" for c in cho]
            log.info(f"[AI] Đang chờ ù: {info}")
            for c in cho:
                u = self.ai.kiem_u(c)
                if u: log.info(f"[AI] 🎉 Ù! Quân {card_name(c)} | {u}")

        # AI chọn quân đánh
        card = self.ai.chon_quan_danh()
        if card is None:
            card = self.ai._danh_bat_dac_di()
        if card is None:
            log.warning("[AI] Không còn quân nào!"); return

        loi = kiem_tra_danh(self.ai, card)
        if loi:
            log.warning(f"[AI] ⚠ Phạm luật khi đánh {card_name(card)}: {TEN_LOI.get(loi, loi)}")
            self.ai.ghi_loi(loi)

        log.info(f"[AI] Đánh: {card_name(card)} (id={card})")
        self.ai.thuc_hien_danh(card)
        self._last_played = card
        await self.send(self.make_play(card))

    async def do_eat_decision(self, quan_chieu: int):
        """AI quyết định ăn bài — NHANH."""
        if self.ai.bi_bao():
            log.info(f"[AI] Đang bị báo, không ăn"); return

        # Chíu ưu tiên
        if self.ai.hand.count(quan_chieu) >= 3:
            loi = kiem_tra_an(self.ai, quan_chieu, quan_chieu, la_chieu=True)
            if loi is None:
                log.info(f"[AI] CHÍU {card_name(quan_chieu)}!")
                self.ai.thuc_hien_an(quan_chieu, quan_chieu, "chieu")
                await self.send(self.make_chiu())
                qd = self.ai.chon_quan_danh()
                if qd:
                    self.ai.thuc_hien_danh(qd)
                    await self.send(self.make_play(qd))
                    log.info(f"[AI] Trả cửa: {card_name(qd)}")
                return

        result = self.ai.chon_nuoc_an(quan_chieu)
        if result:
            qha, loai = result
            log.info(f"[AI] Ăn {card_name(quan_chieu)} bằng {card_name(qha)} ({loai})")
            self.ai.thuc_hien_an(quan_chieu, qha, loai)
            await self.send(self.make_take_card())
        else:
            log.info(f"[AI] Bỏ không ăn {card_name(quan_chieu)}")
            self.ai.ghi_bo_an(quan_chieu)

    # ======================== PACKET HANDLERS ========================
    async def handle(self, raw: bytes):
        r = BinaryReader(raw)
        cmd = r.read_command()
        if cmd not in ("PING", "PONG"):
            log.info(f"RECV {cmd}")
        self.last_activity = time.time()
        try:
            if cmd == "PING": await self.send(self.make_pong())
            elif cmd == "LOGIN": await self.handle_login(r)
            elif cmd == "ENTER_PLACE": await self.handle_enter(r)
            elif cmd == "LIST_BET_AMT": await self.handle_list_bet_amt(r)
            elif cmd == "CREATE_RULE": await self.handle_create_rule(r)
            elif cmd == "GET_TABLE_DATA_EX": await self.handle_table(r)
            elif cmd == "START_MATCH":
                log.info(f"[BOT] START_MATCH raw ({len(raw)} bytes): {raw.hex()[:200]}")
                await self.handle_start(r)
            elif cmd == "SET_TURN":
                log.info(f"[BOT] SET_TURN raw ({len(raw)} bytes): {raw.hex()[:80]}")
                await self.handle_turn(r)
            elif cmd == "MOVE":
                log.info(f"[BOT] MOVE raw ({len(raw)} bytes): {raw.hex()[:120]}")
                await self.handle_move(r)
            elif cmd == "GAMEOVER": await self.handle_gameover(r)
            elif cmd == "PLAY": await self.handle_play(r)
            elif cmd == "SEND_CARD":
                log.info(f"[BOT] SEND_CARD raw ({len(raw)} bytes): {raw.hex()[:120]}")
                await self.handle_send_card(r)
            elif cmd == "SHOW_PLAYER_CARD":
                log.info(f"[BOT] SHOW_PLAYER_CARD raw ({len(raw)} bytes): {raw.hex()[:120]}")
                await self.handle_show_player_card(r)
            elif cmd == "SELECT_CARDS":
                log.info(f"[BOT] SELECT_CARDS raw ({len(raw)} bytes): {raw.hex()[:120]}")
                await self.handle_select_cards(r)
            elif cmd == "TAKE_CARD": await self.handle_take_card(r)
            elif cmd == "CHIU": log.info("[BOT] Chíu thành công")
            elif cmd == "DROP_BAND": log.info("[BOT] Xướng ù!")
            elif cmd == "SPREAD": await self.handle_spread(r)
            elif cmd == "DROP": log.info("[BOT] DROP - chọn xướng")
            elif cmd == "KICK_PLAYER": await self.handle_kick(r)
            elif cmd == "PLAYER_ENTERED": await self.handle_player_enter(r)
            elif cmd == "PLAYER_EXITED": await self.handle_player_exit(r)
            elif cmd == "BALANCE_CHANGED": log.info("[BOT] Số dư thay đổi")
            elif cmd == "ALERT":
                msg = r.read_utf(); log.info(f"[ALERT] {msg[:100]}")
            elif cmd == "RIBBON_MESSAGE":
                msg = r.read_utf(); log.info(f"[RIBBON] {msg[:100]}")
            elif cmd == "ADD_LOG":
                msg = r.read_utf(); log.info(f"[LOG] {msg[:100]}")
            elif cmd == "SET_CARDS":
                n = r.u8()
                raw_ids = [r.u8() for _ in range(n)]
                cards = [map_card_id(c) for c in raw_ids]
                cards = [c for c in cards if c is not None]
                log.info(f"[BOT] SET_CARDS: {len(cards)} lá raw={raw_ids} mapped={[card_name(c) for c in cards]}")
                if len(cards) >= 15 and self.is_playing:
                    self.ai.nhan_bai(cards)
            elif cmd == "CLEAR_CARDS": log.info("[BOT] CLEAR_CARDS")
            elif cmd == "SET_PLAYER_POINT":
                sid = r.i8(); pts = r.i32()
                log.info(f"[BOT] Điểm slot {sid}: {pts}")
            elif cmd == "SET_PLAYER_ATTR":
                sid = r.i8(); name = r.read_ascii(); val = r.read_utf()
                log.info(f"[BOT] Attr slot {sid}: {name}={val}")
            elif cmd not in ("PING", "PONG"):
                log.info(f"[BOT] Unhandled: {cmd} (raw {len(raw)} bytes)")
        except Exception as e:
            log.error(f"Error {cmd}: {e}", exc_info=True)

    async def handle_login(self, r: BinaryReader):
        status = r.i8()
        if status == 0:
            path = r.read_utf()
            if path == "REFRESH":
                ok = await asyncio.get_event_loop().run_in_executor(None, self.http_login)
                if ok: await self.send(self.make_login())
                return
            if r.remaining() > 0: self.lock_key = r.read_ascii()
            await self.send(self.make_enter(self.place_path))
        else: log.error("LOGIN failed!")

    async def handle_enter(self, r: BinaryReader):
        status = r.i8()
        if status == 0:
            if self._joining_table:
                self._joining_table = False; self._rejoining = False; self.in_table = True
                await asyncio.sleep(_STEP); await self.send(self.make_get_table())
            elif not self.in_table:
                if self._want_rejoin and self.table_id:
                    self._want_rejoin = False; self._rejoining = True; self._joining_table = True
                    await self.send(self.make_enter(f"{self.place_path}.{self.table_id}"))
                else:
                    self._bet_amts_loaded = False; await self.send(self.make_list_bet_amt())
        else:
            if self._joining_table:
                self._joining_table = False
                if self._rejoining:
                    self._rejoining = False; self._rejoin_attempts += 1; self.table_id = None
                    await asyncio.sleep(1); await self.send(self.make_list_bet_amt())
                else:
                    await asyncio.sleep(1); await self.send(self.make_create_rule())

    async def handle_list_bet_amt(self, r: BinaryReader):
        status = r.i8()
        if status != 0: return
        count = r.i8()
        self.bet_amts = [{"id": i, "value": r.i32()} for i in range(count)]
        self._bet_amts_loaded = True
        log.info(f"[BOT] Bet amounts: {[(ba['id'], ba['value']) for ba in self.bet_amts]}")
        join_id = os.environ.get("CHAN_JOIN_TABLE", "")
        if join_id:
            # Chế độ test: vào thẳng bàn chỉ định thay vì tạo bàn mới
            log.info(f"[BOT] JOIN bàn {join_id} (chế độ test)")
            self._joining_table = True
            await self.send(self.make_enter(f"{self.place_path}.{join_id}"))
        else:
            await self.send(self.make_create_rule())

    async def handle_create_rule(self, r: BinaryReader):
        status = r.i8()
        if status == 0:
            table_id = r.read_ascii(); self.table_id = table_id; self._rejoin_attempts = 0
            log.info(f"[CREATE_RULE] Bàn chắn mới! id={table_id} bet={BOT_BET_XU}xu")
            await asyncio.sleep(_STEP); self._joining_table = False
            await self.send(self.make_get_table())
        else:
            self._joining_table = False; log.warning("[CREATE_RULE] Thất bại!")

    async def handle_table(self, r: BinaryReader):
        try:
            first_byte = r.i8()
            if first_byte != 0:
                if r.remaining() > 0 and "not in table" in r.read_utf().lower():
                    self.in_table = False; self.table_id = None
                    await self.create_new_table()
                return
            seat_count = r.u8()
            for _ in range(seat_count):
                r.u8(); r.read_ascii(); r.u8(); n = r.u8()
                for _ in range(n): r.u8(); r.read_ascii(); r.read_utf(); r.u8(); r.u8()
            r.u8(); self.slot = r.i8(); is_playing = r.u8() == 1
            player_count = r.u8(); self.players = {}; self.player_slot_by_id = {}
            for _ in range(player_count):
                sid = r.i8(); pid = r.i64(); name = r.read_utf()
                r.u16(); r.read_ascii(); r.i8(); r.i64(); r.i64(); r.i64(); r.u8(); r.u8()
                self.players[sid] = {'id': pid, 'name': name}
                self.player_slot_by_id[pid] = sid
            current_player = r.i8(); r.i16(); r.i16(); r.u8()
            self.in_table = True
            move_count = r.u8()
            for _ in range(move_count): r.i8(); r.i32()
            r.u8(); r.u8(); r.i16(); r.read_bytes()
            r.u8(); r.u8(); n = r.u8()
            for _ in range(n): r.read_ascii(); r.read_utf()
            has_opponent = any(sid >= 0 and sid != self.slot for sid in self.players)
            self.is_playing = is_playing
            log.info(f"[TABLE] Slot={self.slot} Playing={is_playing} Turn=slot{current_player}")
            if is_playing and current_player == self.slot:
                if not self.pending_move:
                    self.pending_move = True
                    await asyncio.sleep(_STEP); await self.do_play()
            elif not is_playing and self.slot >= 0:
                if has_opponent:
                    if not self.ready:
                        log.info("[BOT] Đối thủ vào → SET_READY!")
                        self.ready = True; await self.send(self.make_ready())
                else: self.ready = False
            elif not is_playing and self.slot < 0:
                if self.in_table:
                    # Vào bàn chưa được cấp ghế → xin ghế bằng SET_READY
                    # (giống app bấm "Sẵn sàng" trong lúc server đếm ngược 10s).
                    # Hết 3 lần vẫn không có ghế mới rời bàn tìm bàn khác.
                    self._seat_attempts = getattr(self, "_seat_attempts", 0) + 1
                    if self._seat_attempts <= 3:
                        log.info(f"[BOT] Chưa có ghế (lần {self._seat_attempts}) → SET_READY xin ngồi!")
                        await self.send(self.make_ready())
                        await asyncio.sleep(_STEP); await self.send(self.make_get_table())
                    else:
                        self._seat_attempts = 0
                        self.in_table = False; self.table_id = None
                        await asyncio.sleep(0.5); await self.send(self.make_list_bet_amt())
                else:
                    self.in_table = False; self.table_id = None
                    await asyncio.sleep(0.5); await self.send(self.make_list_bet_amt())
            self._rejoining = False
        except Exception as e:
            log.error(f"Table error: {e}")

    async def handle_start(self, r: BinaryReader):
        self.total_games += 1; self.is_playing = True; self.ready = False
        self.pending_move = False; self.opponent_gone_at = None; self.last_fired_card = None
        self._last_played = None
        self.ai = ChanAI("Bot")

        # Parse player info
        player_count = r.u8()
        for _ in range(player_count): r.i8(); r.i32()

        # Parse deal info (gamevh.net protocol)
        firstCardId = r.u8()
        chosenSlotId = r.u8()
        usedDealSlotCount = r.u8()
        deal_slot_map = {}
        for _ in range(usedDealSlotCount):
            dealSlotId = r.u8(); slotId = r.u8()
            deal_slot_map[dealSlotId] = slotId

        # Đọc bài (readByteArray = i16 length + bytes)
        raw_cards = r.read_byte_array()

        # Map sprite IDs → engine IDs (% N_KINDS)
        cards = []
        for sprite_id in raw_cards:
            engine_id = sprite_id % N_KINDS
            if 0 <= engine_id < N_KINDS: cards.append(engine_id)
            else: log.warning(f"[CARD_MAP] Invalid sprite {sprite_id}")

        if cards:
            self.ai.nhan_bai(cards)
            a = analyze(self.ai.hand)
            log.info(f"=== GAME {self.total_games} === Slot={self.slot} Cái=slot{chosenSlotId}")
            log.info(f"[AI] Nhận {len(cards)} lá: {[card_name(c) for c in cards]}")
            log.info(f"[AI] {a['so_chan']} chắn, {len(a['ca'])} cạ, {len(a['ba_dau'])} ba đầu, {len(a['que'])} què")
            cho = waiting_cards(self.ai.hand, self.ai.melds)
            if cho: log.info(f"[AI] Chờ ù: {[card_name(c) for c in cho]}")
        else:
            log.warning(f"[AI] START_MATCH không có bài! raw={raw_cards}")
            log.info(f"=== GAME {self.total_games} === Slot={self.slot}")

    async def handle_turn(self, r: BinaryReader):
        sid = r.i8(); turn_timeout = r.i16(); acc_timeout = r.i16()
        if self.slot < 0: return
        log.info(f"[TURN] sid={sid} my_slot={self.slot} match={sid==self.slot} playing={self.is_playing} pending={self.pending_move} hand={len(self.ai.hand)}")
        if sid == self.slot and self.is_playing and self._running:
            log.info(f"[BOT] Đến lượt! timeout={turn_timeout}s | Bài: {len(self.ai.hand)} lá")
            if not self.pending_move:
                self.pending_move = True
                await asyncio.sleep(_STEP); await self.do_play()
            else:
                log.warning(f"[BOT] pending_move=True, bỏ qua lượt!")

    async def handle_move(self, r: BinaryReader):
        svr_ids = r.read_byte_array()
        source_slot = r.i8(); source_line = r.i8() - 1
        target_slot = r.i8(); target_line = r.i8() - 1; target_index = r.i8()

        card_ids = [map_card_id(c) for c in svr_ids]
        card_ids = [c for c in card_ids if c is not None]
        card_names = [card_name(c) for c in card_ids]

        if target_line == 2:  # Fired card line — lá mới rời tay xuống cửa chưới
            for cid in card_ids:
                if source_slot != self.slot:
                    self.last_fired_card = cid
                    self.ai.tang_lo(cid)  # đếm ĐÚNG 1 LẦN lúc lá xuống cửa
                    log.info(f"[AI] Quân cửa trên: {card_name(cid)} (slot {source_slot}) còn {self.ai._con_lai(cid)} lá bốc được")
                    await self.do_eat_decision(cid)
        elif source_line == 2 and target_slot == self.slot:
            # Xác nhận ăn của MÌNH: lá từ cửa về mỏ — đã đếm lúc bị đánh + đã
            # giam_lo lúc quyết định ăn → KHÔNG đếm lại (nguyên nhân "0 lá ảo").
            for cid in card_ids:
                log.info(f"[AI] Xác nhận nhận quân ăn: {card_name(cid)} (đã đếm đúng 1 lần ở cửa)")
        elif source_slot != self.slot and source_slot >= 0:
            if source_line == 2:
                # Đối thủ KHÁC ăn lá chưới → lá vẫn nằm ngoài bốc, đã đếm rồi
                for cid in card_ids:
                    log.info(f"[AI] Slot {target_slot} ăn lá chưới {card_name(cid)} (không đếm lại)")
            else:
                # Lá lộ từ TAY đối thủ sang vùng mở (đì/ăn) — lá mới chưa từng đếm
                for cid in card_ids:
                    self.ai.tang_lo(cid)
                    log.info(f"[AI] Slot {source_slot} lộ lá: {card_name(cid)} còn {self.ai._con_lai(cid)} lá bốc được")

    async def handle_send_card(self, r: BinaryReader):
        slot_id = r.i8(); raw_ids = r.read_byte_array()
        log.info(f"[BOT] SEND_CARD slot={slot_id} count={len(raw_ids)} raw_ids={raw_ids}")
        if slot_id == self.slot:
            cards = [map_card_id(c) for c in raw_ids]
            cards = [c for c in cards if c is not None]
            if cards:
                self.ai.nhan_bai(cards)
                log.info(f"[AI] Nhận {len(cards)} lá: {[card_name(c) for c in cards]}")

    async def handle_show_player_card(self, r: BinaryReader):
        slot_id = r.i8(); raw_ids = r.read_byte_array()
        log.info(f"[BOT] SHOW_PLAYER_CARD slot={slot_id} count={len(raw_ids)} raw_ids={raw_ids}")
        if slot_id == self.slot:
            cards = [map_card_id(c) for c in raw_ids]
            cards = [c for c in cards if c is not None]
            if cards:
                self.ai.nhan_bai(cards)
                log.info(f"[AI] Bài trên tay ({len(cards)} lá): {[card_name(c) for c in cards]}")

    async def handle_select_cards(self, r: BinaryReader):
        raw_ids = r.read_byte_array()
        valid_ids = [map_card_id(c) for c in raw_ids]
        valid_ids = [c for c in valid_ids if c is not None]
        log.info(f"[AI] Server yêu cầu chọn: {[card_name(c) for c in valid_ids]} (raw={raw_ids})")

    async def handle_take_card(self, r: BinaryReader):
        if r.remaining() >= 3:
            slot_id = r.i8(); raw_id = r.i16()
            cid = map_card_id(raw_id)
            log.info(f"[BOT] Slot {slot_id} ăn {card_name(cid) if cid is not None else f'raw_{raw_id}'}")
            if cid is not None:
                # FIX tracking: TAKE_CARD chỉ là broadcast xác nhận — lá đã đếm
                # lúc xuống cửa chưới, KHÔNG đếm lại (nguyên nhân "0 lá ảo").
                if slot_id == self.slot: self.ai.an_quan.add(cid)

    async def handle_spread(self, r: BinaryReader):
        slot_id = r.i8()
        name = self.players.get(slot_id, {}).get('name', '')
        count = r.u8()
        for _ in range(count): r.u8(); r.u8()
        win_title = r.read_string(); lost_title = r.read_string()
        log.info(f"[BOT] {name} xướng: {win_title}")

    async def handle_play(self, r: BinaryReader):
        status = r.i8()
        if status != 0:
            log.warning(f"PLAY error {status}")
            # Server từ chối nước đánh → hoàn tác trạng thái nội bộ để tracking không lệch
            card = self._last_played
            if card is not None:
                self._last_played = None
                if card in self.ai.danh: self.ai.danh.remove(card)
                if card not in self.ai.hand:
                    self.ai.hand.append(card); self.ai.hand.sort()
                    log.info(f"[AI] Hoàn tác: {card_name(card)} về lại tay (PLAY bị từ chối)")
            self.pending_move = False

    async def handle_gameover(self, r: BinaryReader):
        self.is_playing = False; self.pending_move = False; self.opponent_gone_at = None
        player_count = r.u8(); my_result = None
        for _ in range(player_count):
            sid = r.i8(); result = r.i8(); r.i64()
            if sid == self.slot: my_result = result
        if my_result in (1, 11): self.wins += 1; log.info(">>> THẮNG! <<<")
        elif my_result in (2, 4, 12): self.losses += 1; log.info(">>> THUA! <<<")
        else: self.draws += 1; log.info(">>> HÒA! <<<")
        if r.remaining() > 0: r.read_utf()
        self.save_stats()
        log.info(f"Stats: W={self.wins} L={self.losses} D={self.draws} G={self.total_games}")
        if self._table_lost_at is not None:
            self._table_lost_at = None
            await asyncio.sleep(2); await self.create_new_table(); return
        if my_result not in (1, 11):
            winner_sid = next((s for s in range(player_count) if s != self.slot), None)
            winner = self.players.get(winner_sid) if winner_sid is not None else None
            winner_id = winner.get('id') if winner else None
            if winner_id:
                log.info(f"[BOT] Thua; kick {winner.get('name', winner_id)} sau 5s...")
                asyncio.create_task(self._delay_kick(winner_id, 5.0)); return
        log.info("[BOT] Ở lại bàn, ready sau 5s...")
        asyncio.create_task(self._delay_ready(5.0))

    async def handle_kick(self, r: BinaryReader):
        status = r.i8(); content = r.read_utf()
        if self._pending_kick_player_id is not None:
            pid = self._pending_kick_player_id; self._pending_kick_player_id = None
            if status == 0: log.info(f"[BOT] Kick {pid} OK: {content}")
            else: log.warning(f"[BOT] Kick {pid} fail ({status}): {content}")
            await asyncio.sleep(1)
            if self.in_table: await self.send(self.make_get_table())
            return
        log.warning(f"[BOT] Bị kick: {content}")
        self.is_playing = False; self.in_table = False; self.pending_move = False
        self.table_id = None
        await asyncio.sleep(1); await self.create_new_table()

    async def _delay_kick(self, player_id: int, delay: float):
        await asyncio.sleep(delay)
        if self.is_playing or not self.in_table: return
        if not any(sid != self.slot and sid >= 0 and p.get('id') == player_id
                   for sid, p in self.players.items()): return
        self.ready = False; self._pending_kick_player_id = player_id
        await self.send(self.make_kick_player(player_id))
        await asyncio.sleep(3)
        if self._pending_kick_player_id == player_id:
            self._pending_kick_player_id = None
            if self.in_table: await self.send(self.make_get_table())

    async def _delay_ready(self, delay: float):
        await asyncio.sleep(delay)
        if not self.is_playing and self.in_table:
            await self.send(self.make_get_table())
            if not self.is_playing and self.in_table:
                self.ready = True; await self.send(self.make_ready())

    async def handle_player_enter(self, r: BinaryReader):
        place_level = r.i8(); pid = r.i64(); name = r.read_utf()
        if r.remaining() >= 36:
            r.i64(); r.i64(); r.read_ascii(); r.i32(); r.i32(); r.i8(); r.i64(); r.i8()
        if place_level < 4: return
        log.info(f"[BOT] {name} vào bàn")
        await self.send(self.make_get_table())

    async def handle_player_exit(self, r: BinaryReader):
        place_level = r.i8()
        pid = r.i64() if r.remaining() >= 8 else -1
        if place_level < 4: return
        slot = self.player_slot_by_id.get(pid) if pid >= 0 else None
        if pid >= 0: self.player_slot_by_id.pop(pid, None)
        if slot is not None: self.players.pop(slot, None)
        if slot == self.slot:
            if self.is_playing: self.in_table = False; self._table_lost_at = time.time()
            else: self.in_table = False; await asyncio.sleep(1); await self.create_new_table()
        elif self.is_playing:
            if self.opponent_gone_at is None:
                self.opponent_gone_at = time.time()
                log.info("[BOT] Đối thủ rời giữa ván → chờ GAMEOVER")
        elif self.in_table:
            await self.send(self.make_get_table())

    # ======================== WATCHDOG ========================
    async def watchdog(self):
        while self._running:
            try: await asyncio.sleep(10)
            except asyncio.CancelledError: return
            if not self._running: return
            if self.start_time and time.time() - self.start_time > RUNTIME:
                self.save_stats(); self.stop(); return
            if not self.ws or self.ws.close_code is not None: continue
            try:
                if (self.opponent_gone_at and self.is_playing
                    and time.time() - self.opponent_gone_at > 15):
                    self.opponent_gone_at = None
                    await self.send(self.make_get_table())
                if self._table_lost_at and time.time() - self._table_lost_at > 8:
                    self._table_lost_at = None; self.table_id = None
                    await self.create_new_table()
                if (not self.is_playing and not self.in_table and not self._joining_table
                    and not self._rejoining and self._bet_amts_loaded):
                    await self.send(self.make_create_rule())
            except Exception: pass

    # ======================== HTTP LOGIN ========================
    def http_login(self) -> bool:
        try:
            session = requests.Session()
            ua = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/139.0 Safari/537.36"
            session.headers.update({'User-Agent': ua, 'Accept-Language': 'vi-VN,vi;q=0.9'})
            session.get('https://gamevh.net/login.jsp', timeout=10)
            resp = session.post('https://gamevh.net/login.jsp', timeout=10,
                data={'redirect': '/', 'USER_NAME': self.user, 'PASSWORD': self.pwwd,
                      'AUTO_LOGIN': 'true', 'LOGIN': 'Đăng nhập'},
                headers={'Origin': 'https://gamevh.net', 'Referer': 'https://gamevh.net/login.jsp',
                         'Content-Type': 'application/x-www-form-urlencoded'},
                allow_redirects=True)
            if 'login.jsp' in resp.url:
                log.error(f'[BOT] HTTP login failed'); return False
            game_resp = session.get(GAME_URL, timeout=10)
            self.cookie = '; '.join(f'{k}={v}' for k, v in session.cookies.items())
            m = re.search(r'var\s+token\s*=\s*(-?\d+)', game_resp.text)
            if not m: m = re.search(r'"token"\s*:\s*(-?\d+)', game_resp.text)
            if m: self.token = int(m.group(1))
            else: log.warning("[BOT] Token not found!"); return False
            log.info(f"[BOT] HTTP login OK. token=***")
            return True
        except Exception as e:
            log.error(f'[BOT] HTTP login error: {e}'); return False

    # ======================== MAIN ========================
    async def run(self):
        self.start_time = time.time(); self.nickname = self.user
        log.info("=" * 60)
        log.info(f"BOT CHẴN v2.0 — gamevh.net — SINGLE FILE")
        log.info(f"User: {USER} | Bet: {BOT_BET_XU}xu | Runtime: {RUNTIME}s")
        log.info("=" * 60)

        ok = await asyncio.get_event_loop().run_in_executor(None, self.http_login)
        if not ok: log.error("HTTP login failed"); return

        while self._running:
            try:
                log.info(f"Connecting to {WS_URL}...")
                headers = {}
                if self.cookie: headers['Cookie'] = self.cookie
                async with websockets.connect(WS_URL, additional_headers=headers,
                                              ping_interval=None, max_size=2**20) as ws:
                    self.ws = ws; log.info("WebSocket connected!")
                    await self.send(self.make_login())
                    wd = asyncio.create_task(self.watchdog())
                    try:
                        async for msg in ws:
                            if not self._running: break
                            if isinstance(msg, bytes): await self.handle(msg)
                            elif isinstance(msg, str): log.info(f"TEXT: {msg[:200]}")
                    except websockets.exceptions.ConnectionClosed as e:
                        log.warning(f"Connection closed: {e}")
                    finally:
                        wd.cancel()
                        try: await wd
                        except asyncio.CancelledError: pass
            except Exception as e:
                log.error(f"WebSocket error: {e}")
            if self._running:
                log.info("Reconnecting in 5s..."); await asyncio.sleep(5)

        self.save_stats()
        log.info(f"Final: W={self.wins} L={self.losses} D={self.draws} G={self.total_games}")


if __name__ == "__main__":
    bot = ChanBot()
    try: asyncio.run(bot.run())
    except KeyboardInterrupt:
        log.info("Stopped by user"); bot.save_stats()

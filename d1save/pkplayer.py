"""Compact `hero`-format player data (spec section 5): PkPlayerStruct /
PkItemStruct, `#pragma pack(push,1)` -- no padding anywhere.

This is what feeds the character-select screen list, and (if no `game`
member exists in the save) what the engine loads to resume play -- see
character.py for why both formats matter.

Item write semantics: writing a compact item here only requires a valid
(idx, iCreateInfo, iSeed, wValue) tuple -- the engine reconstructs the
item's actual stats/affixes from these via RecreateItem/SetupAllItems,
using iSeed as an RNG seed, when the save loads (reference/pack.cpp,
reference/items.cpp). We never replicate that RNG here. The one
exception is items whose *base type* is intrinsically unique
(itemdata.ItemData.misc_id == IMISC_UNIQUE, e.g. Arkaine's Valor): for
those, iSeed IS the direct index into itemdata.UNIQUE_ITEMS, not an RNG
seed (reference/items.cpp GetUniqueItem).

Byte offsets below are struct-relative, derived directly from
reference/structs.h's PkPlayerStruct (packed, so no alignment guessing)
and cross-checked against a real save: the whole struct decodes to
exactly 1266 bytes. (The spec markdown's claim of pName at offset 12 is
wrong; structs.h and a real save both agree it's 16.)
"""
import struct
from dataclasses import dataclass
from typing import List, Optional

PK_ITEM_FMT = '<IHHBBBBBHI'
PK_ITEM_SIZE = struct.calcsize(PK_ITEM_FMT)
assert PK_ITEM_SIZE == 19

NUM_INVLOC = 7
NUM_INV_GRID_ELEM = 40
MAXBELTITEMS = 8
PLR_NAME_LEN = 32
PLAYER_STRUCT_SIZE = 1266

EMPTY_IDX = 0xFFFF
IDI_EAR = 23
IDI_GOLD = 0

NAME_OFF = 16
INVBODY_OFF = 124
INVLIST_OFF = 257
INVGRID_OFF = 1017
PNUMINV_OFF = 1057
SPDLIST_OFF = 1058

INVBODY_SLOTS = ('HEAD', 'RING_LEFT', 'RING_RIGHT', 'AMULET', 'HAND_LEFT', 'HAND_RIGHT', 'CHEST')

# icreateinfo bit flags (reference/enums.h icreateinfo_flag)
CF_LEVEL = (1 << 6) - 1  # 0x003F
CF_ONLYGOOD = 1 << 6
CF_UPER15 = 1 << 7
CF_UPER1 = 1 << 8
CF_UNIQUE = 1 << 9
CF_SMITH = 1 << 10
CF_SMITHPREMIUM = 1 << 11
CF_BOY = 1 << 12
CF_WITCH = 1 << 13
CF_HEALER = 1 << 14
CF_PREGEN = 1 << 15
CF_USEFUL = CF_UPER15 | CF_UPER1
CF_TOWN = CF_SMITH | CF_SMITHPREMIUM | CF_BOY | CF_WITCH | CF_HEALER


@dataclass
class PkItem:
    i_seed: int
    i_create_info: int
    idx: int
    b_id: int
    b_dur: int
    b_mdur: int
    b_ch: int
    b_mch: int
    w_value: int
    dw_buff: int

    @property
    def is_empty(self) -> bool:
        return self.idx == EMPTY_IDX

    @property
    def is_ear(self) -> bool:
        return self.idx == IDI_EAR

    @property
    def identified(self) -> bool:
        return bool(self.b_id & 1)

    @property
    def magical(self) -> int:
        """0 = normal, 1 = magic, 2 = unique (matches ItemStruct._iMagical)."""
        return self.b_id >> 1

    @classmethod
    def empty(cls) -> "PkItem":
        return cls(0, 0, EMPTY_IDX, 0, 0, 0, 0, 0, 0, 0)

    @classmethod
    def plain(cls, idx: int, durability: int = 0, max_durability: Optional[int] = None,
              charges: int = 0, max_charges: int = 0, seed: int = 0) -> "PkItem":
        """A base-quality item with no magic/unique affixes: iCreateInfo=0,
        so the engine reconstructs it deterministically via
        SetPlrHandItem/SetPlrHandSeed with no RNG-driven rerolling."""
        max_durability = durability if max_durability is None else max_durability
        return cls(i_seed=seed, i_create_info=0, idx=idx, b_id=1 | (0 << 1),
                    b_dur=durability, b_mdur=max_durability, b_ch=charges, b_mch=max_charges,
                    w_value=0, dw_buff=0)

    @classmethod
    def gold(cls, amount: int, seed: int = 0) -> "PkItem":
        # GOLD_MAX_LIMIT itself lives in defines.h, which isn't in our
        # reference excerpts, so this 5000 is the commonly-documented
        # vanilla per-stack cap, not something verified against source
        # this session. wValue (WORD) could hold up to 65535 regardless.
        if not 1 <= amount <= 5000:
            raise ValueError("gold stacks are limited to 5000 in vanilla Diablo 1")
        return cls(i_seed=seed, i_create_info=0, idx=IDI_GOLD, b_id=0, b_dur=0, b_mdur=0,
                    b_ch=0, b_mch=0, w_value=amount, dw_buff=0)

    @classmethod
    def always_unique(cls, unique_table_position: int, idx: int, seed_for_town: int = 0) -> "PkItem":
        """A base item whose intrinsic type is always-unique (e.g. Arkaine's
        Valor): iSeed IS the direct itemdata.UNIQUE_ITEMS index, not an RNG
        seed. `idx` must be the ALL_ITEMS position whose misc_id is
        IMISC_UNIQUE (see itemdata.ALL_ITEMS[idx].unique_type)."""
        return cls(i_seed=unique_table_position, i_create_info=CF_UNIQUE, idx=idx,
                    b_id=1 | (2 << 1), b_dur=0, b_mdur=0, b_ch=0, b_mch=0, w_value=0, dw_buff=0)

    @classmethod
    def random_roll_unique(cls, idx: int, seed: int, i_create_info: int) -> "PkItem":
        """A "random-roll" unique (e.g. Windforce) on a base item that
        ISN'T intrinsically unique: the game reconstructs these by
        re-running its own RNG from iSeed (SetupAllItems/CheckUnique),
        not by using iSeed as a table index. (seed, i_create_info) must
        come from itemrng.find_seed_for_unique() -- an arbitrary seed
        here will not reliably reproduce a specific unique; see that
        module's docstring for why."""
        return cls(i_seed=seed, i_create_info=i_create_info, idx=idx,
                    b_id=1 | (2 << 1), b_dur=0, b_mdur=0, b_ch=0, b_mch=0, w_value=0, dw_buff=0)

    @classmethod
    def from_bytes(cls, buf, off: int) -> "PkItem":
        return cls(*struct.unpack_from(PK_ITEM_FMT, buf, off))

    def to_bytes(self) -> bytes:
        return struct.pack(PK_ITEM_FMT, self.i_seed, self.i_create_info, self.idx,
                            self.b_id, self.b_dur, self.b_mdur, self.b_ch, self.b_mch,
                            self.w_value, self.dw_buff)


def _field(offset: int, fmt: str):
    def getter(self):
        return struct.unpack_from(fmt, self._buf, offset)[0]

    def setter(self, value):
        struct.pack_into(fmt, self._buf, offset, value)

    return property(getter, setter)


class PkPlayer:
    """Wraps a 1266-byte compact hero-format player record in place.

    Fields not exposed as named properties (archiveTime, destAction/
    destParam1/destParam2/plrlevel/px/py/targx/targy, pStatPts, pSplLvl,
    pMemSpells, and everything from pTownWarps to the end) are preserved
    byte-for-byte from whatever was loaded -- this class only ever
    touches the bytes a given property/method explicitly reads or
    writes.
    """

    def __init__(self, buf: bytes):
        if len(buf) != PLAYER_STRUCT_SIZE:
            raise ValueError(f"expected a {PLAYER_STRUCT_SIZE}-byte hero record, got {len(buf)}")
        self._buf = bytearray(buf)

    @classmethod
    def from_bytes(cls, buf: bytes) -> "PkPlayer":
        return cls(buf)

    def to_bytes(self) -> bytes:
        return bytes(self._buf)

    # --- identity / class ---
    @property
    def name(self) -> str:
        return self._buf[NAME_OFF:NAME_OFF + PLR_NAME_LEN].split(b'\x00')[0].decode('latin1')

    @name.setter
    def name(self, value: str):
        raw = value.encode('latin1')
        if len(raw) > PLR_NAME_LEN - 1:
            raise ValueError(f"name too long ({len(raw)} bytes, max {PLR_NAME_LEN - 1})")
        self._buf[NAME_OFF:NAME_OFF + PLR_NAME_LEN] = raw.ljust(PLR_NAME_LEN, b'\x00')

    player_class = _field(48, '<b')

    # --- stats ---
    base_str = _field(49, '<B')
    base_mag = _field(50, '<B')
    base_dex = _field(51, '<B')
    base_vit = _field(52, '<B')
    level = _field(53, '<b')
    stat_points = _field(54, '<B')
    experience = _field(55, '<i')
    gold = _field(59, '<i')
    hp_base = _field(63, '<i')
    max_hp_base = _field(67, '<i')
    mana_base = _field(71, '<i')
    max_mana_base = _field(75, '<i')

    num_inv = _field(PNUMINV_OFF, '<B')

    # --- items ---
    def _read_items(self, offset: int, count: int) -> List[PkItem]:
        return [PkItem.from_bytes(self._buf, offset + i * PK_ITEM_SIZE) for i in range(count)]

    def _write_item(self, offset: int, slot: int, item: PkItem):
        off = offset + slot * PK_ITEM_SIZE
        self._buf[off:off + PK_ITEM_SIZE] = item.to_bytes()

    @property
    def inv_body(self) -> List[PkItem]:
        return self._read_items(INVBODY_OFF, NUM_INVLOC)

    def set_equip_slot(self, slot: int, item: PkItem):
        """`slot` is an index into INVBODY_SLOTS (HEAD/RING_LEFT/.../CHEST)."""
        if not 0 <= slot < NUM_INVLOC:
            raise IndexError(f"equip slot {slot} out of range 0..{NUM_INVLOC - 1}")
        self._write_item(INVBODY_OFF, slot, item)

    @property
    def inv_list(self) -> List[PkItem]:
        return self._read_items(INVLIST_OFF, NUM_INV_GRID_ELEM)

    def set_inv_list_slot(self, slot: int, item: PkItem):
        if not 0 <= slot < NUM_INV_GRID_ELEM:
            raise IndexError(f"backpack list slot {slot} out of range 0..{NUM_INV_GRID_ELEM - 1}")
        self._write_item(INVLIST_OFF, slot, item)

    @property
    def inv_grid(self) -> List[int]:
        return list(struct.unpack_from(f'<{NUM_INV_GRID_ELEM}b', self._buf, INVGRID_OFF))

    @inv_grid.setter
    def inv_grid(self, values: List[int]):
        if len(values) != NUM_INV_GRID_ELEM:
            raise ValueError(f"expected {NUM_INV_GRID_ELEM} grid cells, got {len(values)}")
        struct.pack_into(f'<{NUM_INV_GRID_ELEM}b', self._buf, INVGRID_OFF, *values)

    @property
    def belt(self) -> List[PkItem]:
        return self._read_items(SPDLIST_OFF, MAXBELTITEMS)

    def set_belt_slot(self, slot: int, item: PkItem):
        if not 0 <= slot < MAXBELTITEMS:
            raise IndexError(f"belt slot {slot} out of range 0..{MAXBELTITEMS - 1}")
        self._write_item(SPDLIST_OFF, slot, item)

    def add_to_backpack(self, item: PkItem, width: int, height: int) -> int:
        """Place `item` in the first free width x height block of the 4x10
        backpack grid (spec section 7): appends to InvList[_pNumInv],
        marks its footprint cells (negative for all but the bottom-left
        cell, which gets the positive +slot marker -- see the note in the
        loop below on why this differs from the spec text), increments
        _pNumInv. Returns the InvList slot used, or raises ValueError if
        there's no room."""
        if self.num_inv >= NUM_INV_GRID_ELEM:
            raise ValueError("backpack list is full")
        grid = self.inv_grid
        rows, cols = 4, 10

        placement = None
        for r0 in range(rows - height + 1):
            for c0 in range(cols - width + 1):
                if all(grid[(r0 + dr) * cols + (c0 + dc)] == 0
                       for dr in range(height) for dc in range(width)):
                    placement = (r0, c0)
                    break
            if placement:
                break
        if placement is None:
            raise ValueError(f"no free {width}x{height} block in the backpack grid")

        new_slot = self.num_inv  # 0-based index into InvList; pre-increment semantics from spec sec. 7
        self.set_inv_list_slot(new_slot, item)
        r0, c0 = placement
        marker = new_slot + 1
        for dr in range(height):
            for dc in range(width):
                # spec sec. 7 says the positive marker goes on the bottom-RIGHT
                # cell, but a real multi-cell item in an actual save (a 2-wide
                # Flail) has it on the bottom-LEFT cell instead -- matching the
                # empirically-observed convention here rather than the spec text.
                is_anchor = (dr == height - 1 and dc == 0)
                grid[(r0 + dr) * cols + (c0 + dc)] = marker if is_anchor else -marker
        self.inv_grid = grid
        self.num_inv = new_slot + 1
        return new_slot

    def remove_from_backpack(self, slot: int):
        """Clear InvList[slot] and its footprint markers in InvGrid.
        _pNumInv is deliberately left unchanged -- it's a bump allocator
        for add_to_backpack() (spec sec. 7), not a live occupied-count,
        and UnPackItem/PackItem walk all 40 InvList slots regardless of
        its value, so freeing a hole below it is safe; we just never
        reuse the hole (matches vanilla's own never-compacts behavior)."""
        if not 0 <= slot < NUM_INV_GRID_ELEM:
            raise IndexError(f"backpack list slot {slot} out of range 0..{NUM_INV_GRID_ELEM - 1}")
        marker = slot + 1
        grid = self.inv_grid
        for i in range(len(grid)):
            if abs(grid[i]) == marker:
                grid[i] = 0
        self.inv_grid = grid
        self.set_inv_list_slot(slot, PkItem.empty())

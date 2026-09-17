"""Raw `game`-format player data (spec section 4): a natural-alignment
memory dump of the live PlayerStruct, present only if the save has an
in-progress dungeon run. If a `game` member exists in the archive, THIS
is what actually gets loaded when the player resumes -- edits to `hero`
alone are silently ignored (see character.py).

Unlike the compact `hero` format, there is no reconstruction step here:
LoadGame() just memcpy()s these bytes straight into the live struct. So
whatever we write for an item's stats (name, AC, damage, flags, ...) is
exactly what the game uses -- no RNG-seed indirection, and therefore no
need to replicate Diablo's affix-rolling logic to get exact, predictable
stats. The price is that "current" and "base" copies of a stat (e.g.
_pStrength vs _pBaseStr) are independent fields with no engine-side
resync guaranteed to run before the player acts on them, so writers here
keep current == base rather than leaving current stale.

Byte offsets below are struct-relative (0 = first byte after the
179-byte header). _pName/_pGold/InvBody/InvList/_pNumInv/InvGrid come
from the spec's empirically-verified values; the stat-field offsets were
derived by hand from reference/structs.h's PlayerStruct (natural
alignment, so padding had to be accounted for field-by-field) and then
cross-checked against a real save: every one of them decodes to a value
that exactly matches the same character's compact `hero`-format fields.

HP and mana are stored 64x their displayed value (a 6-bit fixed-point
scheme -- see the `& 0xFFFFFFC0` mask in reference/pack.cpp's
UnPackPlayer). The *_display properties convert; the raw *_base/*_cur
properties do not.
"""
import struct
from typing import List

HEADER_SIZE = 179
NUMLEVELS = 17  # non-Hellfire

RAW_ITEM_SIZE = 368
NUM_INVLOC = 7
NUM_INV_GRID_ELEM = 40
MAXBELTITEMS = 8
PLR_NAME_LEN = 32

ITYPE_NONE = -1

NAME_OFF = 320
PCLASS_OFF = 352
CUR_STR_OFF, BASE_STR_OFF = 356, 360
CUR_MAG_OFF, BASE_MAG_OFF = 364, 368
CUR_DEX_OFF, BASE_DEX_OFF = 372, 376
CUR_VIT_OFF, BASE_VIT_OFF = 380, 384
STAT_PTS_OFF = 388
HP_BASE_OFF, MAX_HP_BASE_OFF = 400, 404
CUR_HP_OFF, MAX_HP_OFF = 408, 412
MANA_BASE_OFF, MAX_MANA_BASE_OFF = 420, 424
CUR_MANA_OFF, MAX_MANA_OFF = 428, 432
LEVEL_OFF = 440
MAX_LEVEL_OFF = 441
EXPERIENCE_OFF = 444
MAX_EXP_OFF = 448
GOLD_OFF = 460

INVBODY_OFF = 892
INVLIST_OFF = 3468
PNUMINV_OFF = 18188
INVGRID_OFF = 18192
SPDLIST_OFF = 18232  # InvGrid[40] ends here (18192+40); SpdList follows immediately in
                      # PlayerStruct's declaration order. Cross-checked against the same
                      # character's hero-format belt contents -- both agree slot-for-slot.

INVBODY_SLOTS = ('HEAD', 'RING_LEFT', 'RING_RIGHT', 'AMULET', 'HAND_LEFT', 'HAND_RIGHT', 'CHEST')

HP_MANA_SHIFT = 6  # displayed value = stored value >> 6


def _field(offset: int, fmt: str):
    def getter(self):
        return struct.unpack_from(fmt, self._buf, offset)[0]

    def setter(self, value):
        struct.pack_into(fmt, self._buf, offset, value)

    return property(getter, setter)


def _scaled_field(base_prop_name: str):
    def getter(self):
        return getattr(self, base_prop_name) >> HP_MANA_SHIFT

    def setter(self, value):
        setattr(self, base_prop_name, value << HP_MANA_SHIFT)

    return property(getter, setter)


class RawItem:
    """A 368-byte raw ItemStruct, wrapped in place. Only the fields the
    spec's item table documents are exposed; everything else in the 368
    bytes (animation pointers, etc.) is preserved as loaded."""

    def __init__(self, buf: bytes):
        if len(buf) != RAW_ITEM_SIZE:
            raise ValueError(f"expected a {RAW_ITEM_SIZE}-byte item, got {len(buf)}")
        self._buf = bytearray(buf)

    @classmethod
    def from_bytes(cls, buf, off: int = 0) -> "RawItem":
        return cls(bytes(buf[off:off + RAW_ITEM_SIZE]))

    def to_bytes(self) -> bytes:
        return bytes(self._buf)

    @classmethod
    def empty(cls) -> "RawItem":
        item = cls(bytes(RAW_ITEM_SIZE))
        item.i_type = ITYPE_NONE
        return item

    @property
    def is_empty(self) -> bool:
        # matches reference/pack.cpp PackItem's own empty check
        return self.i_type == ITYPE_NONE

    i_seed = _field(0, '<i')
    i_create_info = _field(4, '<H')
    i_type = _field(8, '<i')
    i_identified = _field(56, '<i')
    i_magical = _field(60, '<b')
    i_loc = _field(189, '<b')
    i_class = _field(190, '<b')
    i_curs = _field(192, '<i')
    i_value = _field(196, '<i')
    i_ivalue = _field(200, '<i')
    i_min_dam = _field(204, '<i')
    i_max_dam = _field(208, '<i')
    i_ac = _field(212, '<i')
    i_flags = _field(216, '<I')
    i_misc_id = _field(220, '<i')
    i_spell = _field(224, '<i')
    i_charges = _field(228, '<i')
    i_max_charges = _field(232, '<i')
    i_durability = _field(236, '<i')
    i_max_dur = _field(240, '<i')
    i_plvit = _field(268, '<i')
    i_uid = _field(308, '<i')
    i_min_str = _field(352, '<b')
    i_min_mag = _field(353, '<B')
    i_min_dex = _field(354, '<b')
    i_stat_flag = _field(356, '<i')
    idi_idx = _field(360, '<i')

    @property
    def name(self) -> str:
        return bytes(self._buf[61:61 + 64]).split(b'\x00')[0].decode('latin1')

    @name.setter
    def name(self, value: str):
        raw = value.encode('latin1')
        if len(raw) > 63:
            raise ValueError("item name too long (max 63 bytes)")
        self._buf[61:61 + 64] = raw.ljust(64, b'\x00')

    @property
    def iname(self) -> str:
        return bytes(self._buf[125:125 + 64]).split(b'\x00')[0].decode('latin1')

    @iname.setter
    def iname(self, value: str):
        raw = value.encode('latin1')
        if len(raw) > 63:
            raise ValueError("item identified-name too long (max 63 bytes)")
        self._buf[125:125 + 64] = raw.ljust(64, b'\x00')

    @classmethod
    def from_item_data(cls, item_data, seed: int = 0, identified: bool = True) -> "RawItem":
        """Build a plain (non-magical, base-stat) raw item directly from
        an itemdata.ItemData entry. Because the raw format has no
        reconstruction step, these stats are exactly what the game will
        use -- no RNG involved."""
        it = cls.empty()
        it.i_seed = seed
        it.i_create_info = 0
        it.i_type = item_data.i_type
        it.i_identified = 1 if identified else 0
        it.i_magical = 0
        it.name = item_data.name or ""
        it.iname = item_data.name or ""
        it.i_loc = item_data.i_loc
        it.i_class = item_data.i_class
        it.i_curs = item_data.i_curs
        it.i_value = item_data.value
        it.i_ivalue = item_data.value
        it.i_min_dam = item_data.min_damage
        it.i_max_dam = item_data.max_damage
        it.i_ac = item_data.max_ac
        it.i_flags = item_data.flags
        it.i_misc_id = item_data.misc_id
        it.i_spell = item_data.spell
        it.i_charges = 0
        it.i_max_charges = 0
        it.i_durability = item_data.durability
        it.i_max_dur = item_data.durability
        it.i_min_str = item_data.min_str
        it.i_min_mag = item_data.min_mag
        it.i_min_dex = item_data.min_dex
        it.i_stat_flag = 1
        it.idi_idx = item_data.idx
        return it

    @classmethod
    def gold(cls, amount: int, seed: int = 0) -> "RawItem":
        """The small/medium/large gold-pile cursor is picked by comparing
        `amount` against GOLD_SMALL_LIMIT/GOLD_MEDIUM_LIMIT -- #defines
        that live in defines.h, which isn't in our reference excerpts, so
        we don't have verified threshold values. This only affects which
        pile *graphic* is shown (not gameplay), so we default to the
        medium-pile cursor rather than guess at unverified thresholds."""
        from . import itemdata
        ICURS_GOLD_MEDIUM = 5
        it = cls.from_item_data(itemdata.ALL_ITEMS[0], seed=seed)  # IDI_GOLD
        it.i_value = amount
        it.i_ivalue = amount
        it.i_curs = ICURS_GOLD_MEDIUM
        return it


class RawPlayer:
    """Wraps the raw game-file bytes that follow the 179-byte header
    (the live PlayerStruct dump, plus -- contiguously after it in the
    same buffer -- quest/portal/dungeon state we never touch). Only
    named properties at verified offsets are read/written; every other
    byte, whatever it is, round-trips unchanged."""

    def __init__(self, buf: bytes):
        self._buf = bytearray(buf)

    @classmethod
    def from_bytes(cls, buf: bytes) -> "RawPlayer":
        return cls(buf)

    def to_bytes(self) -> bytes:
        return bytes(self._buf)

    @property
    def name(self) -> str:
        return bytes(self._buf[NAME_OFF:NAME_OFF + PLR_NAME_LEN]).split(b'\x00')[0].decode('latin1')

    @name.setter
    def name(self, value: str):
        raw = value.encode('latin1')
        if len(raw) > PLR_NAME_LEN - 1:
            raise ValueError(f"name too long ({len(raw)} bytes, max {PLR_NAME_LEN - 1})")
        self._buf[NAME_OFF:NAME_OFF + PLR_NAME_LEN] = raw.ljust(PLR_NAME_LEN, b'\x00')

    player_class = _field(PCLASS_OFF, '<b')

    current_str = _field(CUR_STR_OFF, '<i')
    base_str = _field(BASE_STR_OFF, '<i')
    current_mag = _field(CUR_MAG_OFF, '<i')
    base_mag = _field(BASE_MAG_OFF, '<i')
    current_dex = _field(CUR_DEX_OFF, '<i')
    base_dex = _field(BASE_DEX_OFF, '<i')
    current_vit = _field(CUR_VIT_OFF, '<i')
    base_vit = _field(BASE_VIT_OFF, '<i')
    stat_points = _field(STAT_PTS_OFF, '<i')

    hp_base = _field(HP_BASE_OFF, '<i')
    max_hp_base = _field(MAX_HP_BASE_OFF, '<i')
    current_hp = _field(CUR_HP_OFF, '<i')
    max_hp = _field(MAX_HP_OFF, '<i')
    mana_base = _field(MANA_BASE_OFF, '<i')
    max_mana_base = _field(MAX_MANA_BASE_OFF, '<i')
    current_mana = _field(CUR_MANA_OFF, '<i')
    max_mana = _field(MAX_MANA_OFF, '<i')

    hp_display = _scaled_field('hp_base')
    max_hp_display = _scaled_field('max_hp_base')
    mana_display = _scaled_field('mana_base')
    max_mana_display = _scaled_field('max_mana_base')

    level = _field(LEVEL_OFF, '<b')
    max_level = _field(MAX_LEVEL_OFF, '<b')
    experience = _field(EXPERIENCE_OFF, '<i')
    max_exp = _field(MAX_EXP_OFF, '<i')
    gold = _field(GOLD_OFF, '<i')

    num_inv = _field(PNUMINV_OFF, '<i')

    def set_base_stat(self, str_: int = None, mag: int = None, dex: int = None, vit: int = None):
        """Set base attribute(s) and mirror them into the 'current'
        fields too, since nothing will resync current<-base for us in
        this format (no CalcPlrInv-equivalent reconstruction step)."""
        if str_ is not None:
            self.base_str = self.current_str = str_
        if mag is not None:
            self.base_mag = self.current_mag = mag
        if dex is not None:
            self.base_dex = self.current_dex = dex
        if vit is not None:
            self.base_vit = self.current_vit = vit

    def set_hp_display(self, current: int, maximum: int = None):
        maximum = current if maximum is None else maximum
        self.max_hp_base = self.max_hp_display = maximum
        self.hp_base = self.hp_display = current

    def set_mana_display(self, current: int, maximum: int = None):
        maximum = current if maximum is None else maximum
        self.max_mana_base = self.max_mana_display = maximum
        self.mana_base = self.mana_display = current

    # --- items ---
    def _read_items(self, offset: int, count: int) -> List[RawItem]:
        return [RawItem.from_bytes(self._buf, offset + i * RAW_ITEM_SIZE) for i in range(count)]

    def _write_item(self, offset: int, slot: int, item: RawItem):
        off = offset + slot * RAW_ITEM_SIZE
        self._buf[off:off + RAW_ITEM_SIZE] = item.to_bytes()

    @property
    def inv_body(self) -> List[RawItem]:
        return self._read_items(INVBODY_OFF, NUM_INVLOC)

    def set_equip_slot(self, slot: int, item: RawItem):
        if not 0 <= slot < NUM_INVLOC:
            raise IndexError(f"equip slot {slot} out of range 0..{NUM_INVLOC - 1}")
        self._write_item(INVBODY_OFF, slot, item)

    @property
    def inv_list(self) -> List[RawItem]:
        return self._read_items(INVLIST_OFF, NUM_INV_GRID_ELEM)

    def set_inv_list_slot(self, slot: int, item: RawItem):
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
    def belt(self) -> List[RawItem]:
        return self._read_items(SPDLIST_OFF, MAXBELTITEMS)

    def set_belt_slot(self, slot: int, item: RawItem):
        if not 0 <= slot < MAXBELTITEMS:
            raise IndexError(f"belt slot {slot} out of range 0..{MAXBELTITEMS - 1}")
        self._write_item(SPDLIST_OFF, slot, item)

    def add_to_backpack(self, item: RawItem, width: int, height: int) -> int:
        """Same placement algorithm as pkplayer.PkPlayer.add_to_backpack
        (spec section 7): first free width x height block, bottom-left
        cell gets the positive marker."""
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

        new_slot = self.num_inv
        self.set_inv_list_slot(new_slot, item)
        r0, c0 = placement
        marker = new_slot + 1
        for dr in range(height):
            for dc in range(width):
                # see pkplayer.PkPlayer.add_to_backpack -- bottom-LEFT, not
                # bottom-right as the spec text claims (empirically observed).
                is_anchor = (dr == height - 1 and dc == 0)
                grid[(r0 + dr) * cols + (c0 + dc)] = marker if is_anchor else -marker
        self.inv_grid = grid
        self.num_inv = new_slot + 1
        return new_slot

    def remove_from_backpack(self, slot: int):
        """See pkplayer.PkPlayer.remove_from_backpack -- same reasoning
        (num_inv is a bump allocator, not a live count, so it's left
        unchanged; the freed slot just never gets reused)."""
        if not 0 <= slot < NUM_INV_GRID_ELEM:
            raise IndexError(f"backpack list slot {slot} out of range 0..{NUM_INV_GRID_ELEM - 1}")
        marker = slot + 1
        grid = self.inv_grid
        for i in range(len(grid)):
            if abs(grid[i]) == marker:
                grid[i] = 0
        self.inv_grid = grid
        self.set_inv_list_slot(slot, RawItem.empty())


class GameFile:
    """The full decoded `game` MPQ member: a 179-byte big-endian header
    (spec section 4) followed by the raw player dump. We only ever
    edit within `player`; the header round-trips unchanged."""

    def __init__(self, header: bytes, player: RawPlayer):
        self.header = bytearray(header)
        self.player = player

    @classmethod
    def from_bytes(cls, data: bytes) -> "GameFile":
        if len(data) < HEADER_SIZE:
            raise ValueError("game file shorter than the fixed header")
        magic = data[0:4]
        if magic != b'RETL':
            raise ValueError(
                f"unsupported game-file build variant {magic!r} "
                "(only non-Hellfire 'RETL' saves are supported)"
            )
        return cls(data[:HEADER_SIZE], RawPlayer.from_bytes(data[HEADER_SIZE:]))

    @property
    def magic(self) -> bytes:
        return bytes(self.header[0:4])

    def to_bytes(self) -> bytes:
        return bytes(self.header) + self.player.to_bytes()

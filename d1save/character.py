"""Unified character model bridging the compact `hero` format and the
raw `game` format.

Spec section 4's central gotcha: if a `game` member exists in the save,
THAT is what the engine actually loads on resume -- edits to `hero`
alone would silently do nothing in that case (no crash, the rest of the
save looks fine, but inventory/stat edits never show up). Character
loads whichever of the two exist and, on every edit, writes through to
both, so the character-select-screen preview (hero) and actual gameplay
(game, when present) never disagree.

Backpack placement (add_plain_item_to_backpack, add_gold_to_backpack)
defaults width/height to the item's real grid footprint --
itemdata.ALL_ITEMS[idx].width_cells/height_cells, generated from
cursor.cpp's InvItemWidth[]/InvItemHeight[] by scripts/gen_itemdata.py
(that file wasn't in this session's original reference excerpts; it was
fetched separately since neither this module nor the spec had a real
per-item footprint table without it). Pass width/height explicitly only
to override.

Known v1 limitation: unique/magical items written into the raw `game`
format only get correct identity fields (name, icon, _iUid) -- not the
full set of numeric _iPL* bonus fields, since the spec only documents
_iPLVit's offset (268) and notes the others are "4 bytes apart" without
giving a complete table. The compact `hero` format doesn't have this
limitation (the engine recomputes everything from idx/iCreateInfo/iSeed
itself), so prefer hero-format editing when exact magic-item stats
matter and no `game` file is present to override it.
"""
from typing import List, Optional

from . import dcodec, itemdata, itemrng, mpq, pkplayer, rawplayer

IMISC_UNIQUE = 0x1B  # reference/enums.h item_misc_id


class HandConflictError(ValueError):
    """Raised by _check_hand_compatibility specifically, so callers (the
    GUI) can offer a targeted "equip anyway?" override for this exact
    case without treating every other ValueError (bad index, wrong slot
    category, out-of-range stat, ...) as similarly retryable."""


def find_base_idx_for_unique(unique: "itemdata.UniqueItemData") -> int:
    """The ALL_ITEMS index of the base item a unique is carried on (e.g.
    "The Butcher's Cleaver" is carried on the plain "Cleaver" base item).
    Public because the GUI item picker needs it too, to look up a
    unique's real grid footprint before offering it for the backpack."""
    for i, item in enumerate(itemdata.ALL_ITEMS):
        if item.unique_type == unique.item_type:
            return i
    raise ValueError(f"no base item found for unique {unique.name!r} (type {unique.item_type})")


# ILOC_* values an item needs to sanely go in each equip slot (reference/structs.h:
# ILOC_ONEHAND=1, TWOHAND=2, ARMOR=3, HELM=4, RING=5, AMULET=6). Enforced by
# equip_plain/equip_unique unless force=True -- this is about catching mistakes,
# not a hard game rule we've verified from source.
EQUIP_SLOT_ILOC = {
    0: {4},     # HEAD -> HELM
    1: {5},     # RING_LEFT -> RING
    2: {5},     # RING_RIGHT -> RING
    3: {6},     # AMULET -> AMULET
    4: {1, 2},  # HAND_LEFT -> ONEHAND/TWOHAND
    5: {1, 2},  # HAND_RIGHT -> ONEHAND/TWOHAND
    6: {3},     # CHEST -> ARMOR
}

ILOC_TWOHAND = 2
HAND_LEFT_SLOT = pkplayer.INVBODY_SLOTS.index('HAND_LEFT')
HAND_RIGHT_SLOT = pkplayer.INVBODY_SLOTS.index('HAND_RIGHT')

# item_misc_id values (reference/enums.h) the belt accepts, per explicit
# request: scrolls and healing/mana/rejuvenation potions only -- not
# elixirs, not experience potions, not staves/books/jewelry/oils/notes.
IMISC_FULLHEAL = 0x2
IMISC_HEAL = 0x3
IMISC_MANA = 0x6
IMISC_FULLMANA = 0x7
IMISC_REJUV = 0x12
IMISC_FULLREJUV = 0x13
IMISC_SCROLL = 0x15
IMISC_SCROLLT = 0x16
BELT_ALLOWED_MISC_IDS = {
    IMISC_FULLHEAL, IMISC_HEAL, IMISC_MANA, IMISC_FULLMANA,
    IMISC_REJUV, IMISC_FULLREJUV, IMISC_SCROLL, IMISC_SCROLLT,
}

# BYTE fields in PkPlayerStruct (reference/structs.h) -- both formats are kept
# within this range so hero and game never disagree on an attribute's value.
ATTRIBUTE_MIN, ATTRIBUTE_MAX = 0, 255
# char pLevel/_pLevel (signed) in both formats.
LEVEL_MIN, LEVEL_MAX = 1, 127
# int32 fields (gold/experience/hp/mana) -- unchecked, a value outside this
# range doesn't get silently clamped, it raises struct.error deep inside the
# packer instead of a clear message, so gate it the same way as the BYTE
# fields above. Negative doesn't mean anything for these in real gameplay.
INT32_MAX = 2 ** 31 - 1
# hp_base/mana_base store the displayed value already left-shifted by
# rawplayer.HP_MANA_SHIFT (6 -- see set_hp/set_mana), so the *displayed*
# ceiling is lower than a plain int32's.
HP_MANA_DISPLAY_MAX = INT32_MAX >> 6


def _check_range(name: str, value: int, lo: int, hi: int):
    if not lo <= value <= hi:
        raise ValueError(f"{name}={value} out of range [{lo}, {hi}]")


def _check_item_idx(idx: int):
    if not 0 <= idx < len(itemdata.ALL_ITEMS):
        raise ValueError(f"item idx {idx} out of range [0, {len(itemdata.ALL_ITEMS) - 1}]")


class Character:
    def __init__(self, hero: pkplayer.PkPlayer, game: Optional[rawplayer.GameFile]):
        self.hero = hero
        self.game = game

    @classmethod
    def load(cls, save_path) -> "Character":
        archive = mpq.open_archive(save_path)
        hero_raw = mpq.read_file(archive, 'hero')
        hero = pkplayer.PkPlayer.from_bytes(dcodec.decode(hero_raw, dcodec.PASSWORD_SINGLE))

        game = None
        if archive.get_hash_table_entry('game') is not None:
            game_raw = mpq.read_file(archive, 'game')
            game = rawplayer.GameFile.from_bytes(dcodec.decode(game_raw, dcodec.PASSWORD_SINGLE))

        return cls(hero, game)

    @property
    def has_game_file(self) -> bool:
        return self.game is not None

    def save(self, in_path, out_path):
        """Write hero (and game, if loaded with one) back into the
        archive via the minimal surgical MPQ patch -- see mpq.py."""
        updates = {'hero': dcodec.encode(self.hero.to_bytes(), dcodec.PASSWORD_SINGLE)}
        if self.game is not None:
            updates['game'] = dcodec.encode(self.game.to_bytes(), dcodec.PASSWORD_SINGLE)
        return mpq.patch_files(in_path, out_path, updates)

    # --- identity / stats: `game`, when present, is authoritative for
    # actual gameplay, so every setter writes through to both ---
    @property
    def name(self) -> str:
        return self.game.player.name if self.game else self.hero.name

    @name.setter
    def name(self, value: str):
        self.hero.name = value
        if self.game:
            self.game.player.name = value

    @property
    def level(self) -> int:
        return self.game.player.level if self.game else self.hero.level

    @level.setter
    def level(self, value: int):
        _check_range('level', value, LEVEL_MIN, LEVEL_MAX)
        self.hero.level = value
        if self.game:
            self.game.player.level = value

    @property
    def experience(self) -> int:
        return self.game.player.experience if self.game else self.hero.experience

    @experience.setter
    def experience(self, value: int):
        _check_range('experience', value, 0, INT32_MAX)
        self.hero.experience = value
        if self.game:
            self.game.player.experience = value

    @property
    def gold(self) -> int:
        return self.game.player.gold if self.game else self.hero.gold

    @gold.setter
    def gold(self, value: int):
        _check_range('gold', value, 0, INT32_MAX)
        self.hero.gold = value
        if self.game:
            self.game.player.gold = value

    @property
    def attributes(self) -> dict:
        src = self.game.player if self.game else self.hero
        return {'str': src.base_str, 'mag': src.base_mag, 'dex': src.base_dex, 'vit': src.base_vit}

    def set_attributes(self, str_: int = None, mag: int = None, dex: int = None, vit: int = None):
        for name, value in (('str', str_), ('mag', mag), ('dex', dex), ('vit', vit)):
            if value is not None:
                _check_range(name, value, ATTRIBUTE_MIN, ATTRIBUTE_MAX)
        if str_ is not None:
            self.hero.base_str = str_
        if mag is not None:
            self.hero.base_mag = mag
        if dex is not None:
            self.hero.base_dex = dex
        if vit is not None:
            self.hero.base_vit = vit
        if self.game:
            self.game.player.set_base_stat(str_, mag, dex, vit)

    @property
    def stat_points(self) -> int:
        return self.game.player.stat_points if self.game else self.hero.stat_points

    @stat_points.setter
    def stat_points(self, value: int):
        _check_range('stat_points', value, ATTRIBUTE_MIN, ATTRIBUTE_MAX)
        self.hero.stat_points = value
        if self.game:
            self.game.player.stat_points = value

    def set_hp(self, current: int, maximum: Optional[int] = None):
        maximum = current if maximum is None else maximum
        _check_range('hp', current, 0, HP_MANA_DISPLAY_MAX)
        _check_range('max_hp', maximum, 0, HP_MANA_DISPLAY_MAX)
        self.hero.max_hp_base = maximum << rawplayer.HP_MANA_SHIFT
        self.hero.hp_base = current << rawplayer.HP_MANA_SHIFT
        if self.game:
            self.game.player.set_hp_display(current, maximum)

    def set_mana(self, current: int, maximum: Optional[int] = None):
        maximum = current if maximum is None else maximum
        _check_range('mana', current, 0, HP_MANA_DISPLAY_MAX)
        _check_range('max_mana', maximum, 0, HP_MANA_DISPLAY_MAX)
        self.hero.max_mana_base = maximum << rawplayer.HP_MANA_SHIFT
        self.hero.mana_base = current << rawplayer.HP_MANA_SHIFT
        if self.game:
            self.game.player.set_mana_display(current, maximum)

    def hp_display(self):
        return (self.game.player.hp_display, self.game.player.max_hp_display) if self.game else \
            (self.hero.hp_base >> rawplayer.HP_MANA_SHIFT, self.hero.max_hp_base >> rawplayer.HP_MANA_SHIFT)

    def mana_display(self):
        return (self.game.player.mana_display, self.game.player.max_mana_display) if self.game else \
            (self.hero.mana_base >> rawplayer.HP_MANA_SHIFT, self.hero.max_mana_base >> rawplayer.HP_MANA_SHIFT)

    # --- equipment ---
    def _check_slot_fits(self, slot: int, item_data, force: bool):
        if force:
            return
        allowed = EQUIP_SLOT_ILOC.get(slot)
        if allowed and item_data.i_loc not in allowed:
            raise ValueError(
                f"{item_data.name!r} (i_loc={item_data.i_loc}) doesn't belong in "
                f"{pkplayer.INVBODY_SLOTS[slot]}; pass force=True to override"
            )

    def _other_hand_slot(self, slot: int) -> Optional[int]:
        if slot == HAND_LEFT_SLOT:
            return HAND_RIGHT_SLOT
        if slot == HAND_RIGHT_SLOT:
            return HAND_LEFT_SLOT
        return None

    def _other_hand_occupant(self, other_slot: int):
        """(i_loc, name) of whatever currently occupies `other_slot`, or
        (None, None) if it's empty or its type can't be resolved (e.g. an
        ear trophy -- see pkplayer.IDI_EAR; those are ILOC_UNEQUIPABLE in
        real data anyway, so treating "unknown" as "not two-handed" here
        is safe)."""
        entry = next(e for e in self.list_equipment() if e['slot'] == pkplayer.INVBODY_SLOTS[other_slot])
        if entry['empty']:
            return None, None
        idx = entry['idx']
        if not 0 <= idx < len(itemdata.ALL_ITEMS):
            return None, None
        return itemdata.ALL_ITEMS[idx].i_loc, entry['name']

    def _check_hand_compatibility(self, slot: int, item_data, force: bool):
        """Two-handed weapons (ILOC_TWOHAND -- staves, bows, and some
        swords/axes/maces/mauls) occupy only one InvBody slot in storage
        (confirmed from reference/items.cpp: both hand slots are read
        independently, nothing duplicates the item into both), but the
        real game won't let you equip one unless the other hand is empty,
        nor equip anything else while a two-handed weapon holds a hand.
        We enforce the same rule rather than silently allowing dual-wield
        that the actual client would reject."""
        if force:
            return
        other_slot = self._other_hand_slot(slot)
        if other_slot is None:
            return
        other_loc, other_name = self._other_hand_occupant(other_slot)

        if item_data.i_loc == ILOC_TWOHAND and other_loc is not None:
            raise HandConflictError(
                f"{item_data.name!r} is two-handed and needs both hands free, but "
                f"{pkplayer.INVBODY_SLOTS[other_slot]} already holds {other_name!r}; "
                "clear it first, or pass force_hands=True to override"
            )
        if item_data.i_loc != ILOC_TWOHAND and other_loc == ILOC_TWOHAND:
            raise HandConflictError(
                f"can't equip {item_data.name!r} in {pkplayer.INVBODY_SLOTS[slot]} -- "
                f"{pkplayer.INVBODY_SLOTS[other_slot]} holds the two-handed {other_name!r}, "
                "which needs both hands; clear it first, or pass force_hands=True to override"
            )

    def equip_plain(self, slot: int, idx: int, force: bool = False, force_hands: bool = False, **kwargs):
        """Equip a base (non-magical) item by ALL_ITEMS index. Exact in
        both formats -- no RNG/reconstruction involved either way.

        `force` bypasses the slot/i_loc category check (e.g. a ring into
        HEAD); `force_hands` separately bypasses the two-handed-weapon
        hand-conflict check -- kept independent so a caller that already
        filtered candidates by slot (like the GUI picker) can safely
        force one without silently disabling the other."""
        _check_item_idx(idx)
        self._check_slot_fits(slot, itemdata.ALL_ITEMS[idx], force)
        self._check_hand_compatibility(slot, itemdata.ALL_ITEMS[idx], force_hands)
        self.hero.set_equip_slot(slot, pkplayer.PkItem.plain(idx, **kwargs))
        if self.game:
            self.game.player.set_equip_slot(slot, rawplayer.RawItem.from_item_data(itemdata.ALL_ITEMS[idx]))

    def _build_unique_item_pair(self, unique_position: int):
        """(base_idx, pk_item, build_raw_item) for a named unique --
        shared by equip_unique() and add_unique_to_backpack().
        build_raw_item is a zero-arg callable (not a value) so each call
        site gets its own RawItem instance.

        Two different reconstruction paths for the hero-format pk_item,
        matching what the real game actually does (see itemrng.py's
        docstring for the full derivation):
          - intrinsically-unique base items (Arkaine's Valor and 8
            others, misc_id == IMISC_UNIQUE): iSeed is a direct
            itemdata.UNIQUE_ITEMS index -- exact, no RNG involved.
          - everything else (Windforce and ~80 others): the game
            re-rolls the item via its own RNG, seeded by iSeed, so we
            search for a seed that makes that re-roll land on this
            exact unique. For the ~10 uniques a real bug in the game's
            own selection logic makes permanently unreachable this way
            (itemrng.BLOCKED_UNIQUE_NAMES): if there's no `game` file,
            that ValueError propagates as-is (there's no format left
            that could represent this unique correctly at all); if
            there IS a `game` file, we swallow it and fall back to a
            plain instance of the base item for hero instead, since
            `game` -- written exactly below, no RNG involved there --
            is what the engine actually loads on resume (see module
            docstring); hero only feeds the pre-game character-select
            preview in that case, and CheckUnique's bug would otherwise
            make it show some other specific named unique instead of
            this one, not blank -- a plain item is the honest choice
            among the incorrect options available for that preview."""
        unique = itemdata.UNIQUE_ITEMS[unique_position]
        base_idx = find_base_idx_for_unique(unique)
        base_item = itemdata.ALL_ITEMS[base_idx]

        if base_item.misc_id == IMISC_UNIQUE:
            pk_item = pkplayer.PkItem.always_unique(unique_position, base_idx)
        else:
            try:
                seed, i_create_info = itemrng.find_seed_for_unique(unique_position)
                pk_item = pkplayer.PkItem.random_roll_unique(base_idx, seed, i_create_info)
            except ValueError:
                if self.game is None:
                    raise
                pk_item = pkplayer.PkItem.plain(base_idx)

        def build_raw_item():
            raw_item = rawplayer.RawItem.from_item_data(itemdata.ALL_ITEMS[base_idx])
            raw_item.i_magical = 2
            raw_item.i_uid = unique_position
            raw_item.iname = unique.name
            raw_item.i_ivalue = unique.value
            return raw_item

        return base_idx, pk_item, build_raw_item

    def equip_unique(self, slot: int, unique_position: int, force: bool = False, force_hands: bool = False):
        """Equip a named unique item (e.g. Arkaine's Valor or Windforce)
        by its itemdata.UNIQUE_ITEMS position -- both the ~9 intrinsic
        quest uniques and the ~80 random-roll ones are supported; see
        _build_unique_item_pair()'s docstring for how each is
        reconstructed and the ~10-unique edge case. See equip_plain()
        for what `force`/`force_hands` each bypass."""
        base_idx, pk_item, build_raw_item = self._build_unique_item_pair(unique_position)
        self._check_slot_fits(slot, itemdata.ALL_ITEMS[base_idx], force)
        self._check_hand_compatibility(slot, itemdata.ALL_ITEMS[base_idx], force_hands)
        self.hero.set_equip_slot(slot, pk_item)
        if self.game:
            self.game.player.set_equip_slot(slot, build_raw_item())

    def add_unique_to_backpack(self, unique_position: int, width: Optional[int] = None,
                                height: Optional[int] = None) -> int:
        """Add a named unique item (e.g. The Butcher's Cleaver or
        Windforce) to the backpack rather than equipping it directly --
        see equip_unique()'s docstring for which uniques are supported.
        width/height default to the base item's real grid footprint --
        see module docstring for the raw-`game`-format caveat on
        magic/unique items."""
        base_idx, pk_item, build_raw_item = self._build_unique_item_pair(unique_position)
        if width is None:
            width = itemdata.ALL_ITEMS[base_idx].width_cells
        if height is None:
            height = itemdata.ALL_ITEMS[base_idx].height_cells
        slot = self.hero.add_to_backpack(pk_item, width, height)
        game_slot = self.game.player.add_to_backpack(build_raw_item(), width, height) if self.game else None
        self._check_backpack_placement_agrees(slot, game_slot)
        return slot

    def clear_equip_slot(self, slot: int):
        self.hero.set_equip_slot(slot, pkplayer.PkItem.empty())
        if self.game:
            self.game.player.set_equip_slot(slot, rawplayer.RawItem.empty())

    # --- backpack / belt ---
    def _check_backpack_placement_agrees(self, hero_slot: int, game_slot: Optional[int]):
        if game_slot is not None and game_slot != hero_slot:
            raise RuntimeError(
                f"hero/game backpack placement disagree (hero slot {hero_slot}, game slot "
                f"{game_slot}) -- the two representations' inventories may already be out of sync"
            )

    def add_gold_to_backpack(self, amount: int) -> int:
        slot = self.hero.add_to_backpack(pkplayer.PkItem.gold(amount), 1, 1)
        game_slot = self.game.player.add_to_backpack(rawplayer.RawItem.gold(amount), 1, 1) if self.game else None
        self._check_backpack_placement_agrees(slot, game_slot)
        return slot

    def add_plain_item_to_backpack(self, idx: int, width: Optional[int] = None,
                                    height: Optional[int] = None, **kwargs) -> int:
        """width/height default to the item's real grid footprint
        (itemdata.ALL_ITEMS[idx].width_cells/height_cells, from
        reference/cursor.cpp) -- pass them explicitly only to override."""
        _check_item_idx(idx)
        if width is None:
            width = itemdata.ALL_ITEMS[idx].width_cells
        if height is None:
            height = itemdata.ALL_ITEMS[idx].height_cells
        slot = self.hero.add_to_backpack(pkplayer.PkItem.plain(idx, **kwargs), width, height)
        game_slot = None
        if self.game:
            game_slot = self.game.player.add_to_backpack(
                rawplayer.RawItem.from_item_data(itemdata.ALL_ITEMS[idx]), width, height)
        self._check_backpack_placement_agrees(slot, game_slot)
        return slot

    def _check_belt_fits(self, item_data, force: bool):
        if force:
            return
        if item_data.misc_id not in BELT_ALLOWED_MISC_IDS:
            raise ValueError(
                f"{item_data.name!r} can't go in the belt (only scrolls and healing/mana/"
                "rejuvenation potions can); pass force=True to override"
            )

    def set_belt_slot(self, slot: int, idx: int, force: bool = False, **kwargs):
        _check_item_idx(idx)
        self._check_belt_fits(itemdata.ALL_ITEMS[idx], force)
        self.hero.set_belt_slot(slot, pkplayer.PkItem.plain(idx, **kwargs))
        if self.game:
            self.game.player.set_belt_slot(slot, rawplayer.RawItem.from_item_data(itemdata.ALL_ITEMS[idx]))

    def clear_belt_slot(self, slot: int):
        self.hero.set_belt_slot(slot, pkplayer.PkItem.empty())
        if self.game:
            self.game.player.set_belt_slot(slot, rawplayer.RawItem.empty())

    def remove_from_backpack(self, slot: int):
        self.hero.remove_from_backpack(slot)
        if self.game:
            self.game.player.remove_from_backpack(slot)

    # --- listing helpers for a CLI/GUI ---
    def _describe_item(self, item) -> dict:
        if item.is_empty:
            return {'empty': True}
        if (not self.game) and item.is_ear:
            return {'empty': False, 'idx': pkplayer.IDI_EAR, 'name': '(ear trophy, not decoded -- out of v1 scope)'}
        idx = item.idi_idx if self.game else item.idx
        name = item.iname if self.game else (itemdata.ALL_ITEMS[idx].name if 0 <= idx < len(itemdata.ALL_ITEMS) else '?')
        return {'empty': False, 'idx': idx, 'name': name}

    def list_equipment(self) -> List[dict]:
        items = self.game.player.inv_body if self.game else self.hero.inv_body
        return [{'slot': slot_name, **self._describe_item(item)}
                for slot_name, item in zip(pkplayer.INVBODY_SLOTS, items)]

    def list_backpack(self) -> List[dict]:
        items = self.game.player.inv_list if self.game else self.hero.inv_list
        num = self.game.player.num_inv if self.game else self.hero.num_inv
        out = []
        for i in range(num):
            desc = self._describe_item(items[i])
            if desc['empty']:
                continue
            out.append({'slot': i, **desc})
        return out

    def list_belt(self) -> List[dict]:
        items = self.game.player.belt if self.game else self.hero.belt
        return [{'slot': i, **self._describe_item(item)} for i, item in enumerate(items)]

    def backpack_grid_cells(self) -> List[Optional[int]]:
        """40 cells, row-major (4 rows x 10 cols): each is either None
        (empty) or the InvList slot index occupying it (spec section 7's
        marker convention, already resolved via abs(marker)-1). Cells
        sharing the same slot index belong to the same item's footprint
        -- useful for a GUI to merge/color multi-cell items."""
        grid = self.game.player.inv_grid if self.game else self.hero.inv_grid
        return [None if v == 0 else abs(v) - 1 for v in grid]

    def backpack_grid_primary_cells(self) -> List[Optional[int]]:
        """Same shape as backpack_grid_cells(), but only each item's
        positive-marker cell (empirically the bottom-left of its
        footprint, not bottom-right as the spec text claims -- see
        pkplayer.PkPlayer.add_to_backpack) is filled in -- the natural
        place to draw a one-time label for a multi-cell item."""
        grid = self.game.player.inv_grid if self.game else self.hero.inv_grid
        return [None if v <= 0 else v - 1 for v in grid]

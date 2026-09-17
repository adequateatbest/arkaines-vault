"""Diablo 1's item-generation RNG (a Borland C++ LCG) and a solver that
finds an (iSeed, iCreateInfo) pair which makes the real game's own item
reconstruction deterministically produce a *specific* named unique item
in the compact `hero` format.

Why this exists: for the ~9 intrinsically-unique base items (Arkaine's
Valor and friends -- see pkplayer.PkItem.always_unique), iSeed is used
directly as the UniqueItemList index on load, so no RNG replication is
needed. For the other ~80 "random-roll" uniques (Windforce, The
Grandfather, ...), the game instead re-rolls via SetupAllItems/
CheckUnique, seeded by iSeed -- so equipping one of these by just
setting iCreateInfo=CF_UNIQUE with an arbitrary seed does NOT reliably
reproduce that specific unique (verified by tracing the actual
reconstruction logic: with no level/chance bits set, the unique roll is
attempted well under 1% of the time, and even then picks unpredictably
among whatever's eligible). This module finds a seed that reliably
does reproduce the requested unique, by simulating the exact RNG call
sequence involved and brute-force searching for one that works -- which
is fast because the search space is small (verified below).

Ported from source, not guessed:
  - reference's engine.cpp doesn't include SetRndSeed/GetRndSeed/
    random_ (fetched separately from
    github.com/diasurgical/devilution/blob/master/Source/engine.cpp,
    the same repo the spec cites): a Borland C/C++ LCG,
    seed = seed*0x015A4E35 + 1 (mod 2**32), GetRndSeed() returns
    abs(signed 32-bit seed), random_(v) returns (GetRndSeed()>>16) % v
    for v < 0xFFFF (our case always).
  - reference/items.cpp: GetItemAttrs makes exactly one random_() call
    for a non-gold, non-book item (the AC roll) -- its specific value
    doesn't affect anything here since GetRndSeed()'s state advance is
    identical regardless of v (v only affects the modulo at the end).
    SetupAllItems then evaluates `random_(32,100)<=10 || random_(33,100)
    <=lvl` (note the short-circuit -- the second call only happens if
    the first fails) before considering `uper`. We deliberately choose
    uper=15 (CF_UPER15): items.cpp shows this unconditionally forces
    iblvl = lvl+4 regardless of the roll above (so we don't need to
    depend on it), and it gives CheckUnique's own gate check
    (`random_(28,100) <= uper`) a 16% pass rate per seed -- so a
    solution is normally found in single-digit attempts, versus ~1% at
    uper=0.
  - CheckUnique's unique-selection loop has a genuine bug (there's a
    `/// BUGFIX: unused, last unique in array always gets chosen`
    comment right there in the source): it deterministically returns
    the LAST UniqueItemList entry matching the base item type and
    level, not a random one. That's good news for us (deterministic
    beats random), but it also means a handful of uniques can never be
    produced this way at all, if a later-declared unique shares their
    base item and always matches whenever they do -- see
    find_seed_for_unique()'s ValueError for that case; 10 of 90 uniques
    hit this in practice (Lightforge, The Rift Bow, Gonnagal's Dirk,
    Gryphons Claw, The Mangler, Crackrust, Staff of Shadows, The
    Deflector, Bramble, Ring of Regha).

None of this touches the raw `game` format's writer, which sets a
unique item's identity fields directly (no RNG involved there at all;
see character.py's module docstring for what it still doesn't cover).
"""
from d1save import itemdata

MASK32 = 0xFFFFFFFF
RND_MULT = 0x015A4E35
RND_INC = 1

CF_LEVEL = (1 << 6) - 1  # reference/enums.h icreateinfo_flag
CF_UPER15 = 1 << 7
CF_UNIQUE = 1 << 9

UPER = 15  # see module docstring for why this specific value

BLOCKED_UNIQUE_NAMES = frozenset({
    "Lightforge", "The Rift Bow", "Gonnagal's Dirk", "Gryphons Claw",
    "The Mangler", "Crackrust", "Staff of Shadows", "The Deflector",
    "Bramble", "Ring of Regha",
})


class _Rng:
    __slots__ = ("seed",)

    def __init__(self, seed: int):
        self.seed = seed & MASK32

    def get_rnd_seed(self) -> int:
        self.seed = (RND_MULT * self.seed + RND_INC) & MASK32
        signed = self.seed - 0x100000000 if self.seed >= 0x80000000 else self.seed
        return abs(signed)

    def random(self, v: int) -> int:
        if v <= 0:
            return 0
        r = self.get_rnd_seed()
        return (r >> 16) % v if v < 0xFFFF else r % v


def _select_unique(item_type: str, iblvl: int):
    """Mirrors CheckUnique's array scan: the LAST matching entry wins
    (see module docstring). Returns None if nothing matches."""
    match = None
    for j, u in enumerate(itemdata.UNIQUE_ITEMS):
        if u.item_type == item_type and iblvl >= u.min_level:
            match = j
    return match


def _resolved_iblvl(iseed: int, lvl: int):
    """Simulate SetRndSeed(iseed) -> GetItemAttrs's one AC-roll random_()
    call -> SetupAllItems's `random_(32,100)<=10 || random_(33,100)<=lvl`
    (short-circuited) -> the uper=15 override -> CheckUnique's gate.
    Returns the resolved iblvl (== lvl+4) if the gate passes, else None."""
    rng = _Rng(iseed)
    rng.get_rnd_seed()  # GetItemAttrs's AC roll -- state advance is the
    # same regardless of the roll's range, so the value is never used

    cond1 = rng.random(100) <= 10
    if not cond1:
        rng.random(100)  # short-circuited second half of the ||
    iblvl = lvl + 4  # uper == 15 unconditionally overrides to this

    gate = rng.random(100)
    if gate > UPER:
        return None
    return iblvl


def find_seed_for_unique(unique_position: int, max_attempts: int = 200_000):
    """Find (iseed, i_create_info) that makes the real game deterministically
    reconstruct itemdata.UNIQUE_ITEMS[unique_position] from a compact
    PkItemStruct carrying these values (idx = its base item, per
    character.find_base_idx_for_unique). Raises ValueError if this unique
    is one of the ~10 that can never be produced this way (see
    BLOCKED_UNIQUE_NAMES), or if no seed turned up in max_attempts (should
    not happen -- expected well under 100 tries at a 16% pass rate)."""
    unique = itemdata.UNIQUE_ITEMS[unique_position]
    if unique.name in BLOCKED_UNIQUE_NAMES:
        raise ValueError(
            f"{unique.name!r} can never be reconstructed via the compact hero "
            "format: the game's own unique-selection logic (a real bug in "
            "CheckUnique, not something we can route around) always resolves "
            "to a different, later-declared unique on the same base item "
            "instead. This only affects hero-format reconstruction -- if the "
            "save has a `game` file, equipping/placing it still writes the "
            "correct identity there directly."
        )

    lvl = max(0, unique.min_level - 4)
    iblvl_target = lvl + 4
    resolved = _select_unique(unique.item_type, iblvl_target)
    if resolved != unique_position:
        raise ValueError(
            f"{unique.name!r} can never be reconstructed via the compact hero "
            f"format: the game's item-selection logic would resolve to "
            f"{itemdata.UNIQUE_ITEMS[resolved].name!r} instead at the required "
            "item level. This shouldn't happen for anything outside "
            "BLOCKED_UNIQUE_NAMES -- if you hit this, that list is out of date."
        )

    for iseed in range(max_attempts):
        if _resolved_iblvl(iseed, lvl) == iblvl_target:
            i_create_info = lvl | CF_UNIQUE | CF_UPER15
            return iseed, i_create_info

    raise ValueError(f"couldn't find a working seed for {unique.name!r} in {max_attempts} attempts")

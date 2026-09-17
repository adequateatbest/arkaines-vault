# Diablo 1 Save Editor

Status: the format/logic core, a CLI, and a PySide6 GUI are all built and
verified end-to-end against a real Battle.net-mode single-player save
(`single_0.sv`, non-Hellfire) -- see `diablo1-save-editor-spec.md` for the
full technical writeup this was built from. Not yet confirmed: loading an
edited save in the actual Diablo.exe client (everything so far is verified
by byte-level re-parsing, not by the original game).

## Layout

- `d1save/` -- the core library, no UI dependencies:
  - `blast.py` -- pure-Python PKWARE DCL "explode" decompressor (ported
    from zlib's contrib/blast; no C compiler needed).
  - `mpq.py` -- MPQ v1 archive reading (correctly handling the
    `MPQ_FILE_IMPLODE` flag that mpyq's own reader silently ignores) and
    the minimal surgical write path (only rewrites the block table + a
    few appended bytes -- never a full archive rebuild, which is
    spec-legal MPQ but crashes the real engine).
  - `dcodec.py` -- Diablo's save-data cipher (a custom SHA1-derived
    stream cipher, separate from the MPQ table cipher in `mpq.py`).
  - `pkplayer.py` -- compact `hero`-format player/item read+write.
  - `rawplayer.py` -- raw `game`-format player/item read+write.
  - `itemrng.py` -- Diablo's item-generation RNG (a Borland C LCG, ported
    from `engine.cpp`) plus a solver that finds an (iSeed, iCreateInfo)
    pair making the real game's own reconstruction logic deterministically
    produce a specific named "random-roll" unique (Windforce and ~70
    others) in the compact `hero` format -- see its docstring for the
    full derivation and the ~10 uniques a real bug in the original
    game's own selection logic makes permanently unreachable this way.
  - `character.py` -- unified model bridging both formats (writes
    through to whichever exist, so hero and game never disagree), plus
    the equip-slot/two-handed/belt validation rules (see below).
  - `itemdata.py` -- **generated** item reference tables (including each
    item's real backpack-grid footprint); do not hand-edit, regenerate
    with `python scripts/gen_itemdata.py`.
- `reference/` -- devilution decompilation excerpts used for cross-checking
  exact game behavior (not part of the runtime package).
- `scripts/` -- one-off/build tools and test scripts, not part of the
  runtime package:
  - `gen_itemdata.py` -- generates `d1save/itemdata.py` from
    `reference/itemdat.cpp` and `reference/cursor.cpp`.
  - `verify_patch_roundtrip.py` -- proves the MPQ write path only touches
    what it should (byte-diffs an original save against a patched one).
  - `test_character_roundtrip.py` -- end-to-end test: edit stats/items via
    `Character`, save, reload, verify.
  - `test_two_handed_validation.py`, `test_belt_restriction.py` -- the
    equip-slot validation rules.
  - `test_unique_rng_reconstruction.py` -- exhaustively checks every
    random-roll unique in the table resolves to itself via itemrng.py.
  - `test_gui_smoke.py` -- drives the GUI headlessly (Qt's offscreen
    platform), including the actual click handlers and dialogs, not just
    the underlying model.
- `cli.py` -- the CLI entry point.
- `gui_main.py` / `gui/` -- the GUI entry point and widgets (PySide6).

## Setup

```
pip install -r requirements.txt
```

## GUI

```
python gui_main.py [path/to/save.sv]
```

Click an equipment/belt/backpack slot to open a searchable item picker;
occupied slots get a "Clear Slot" button. The backpack picker's "Unique
items" checkbox lets you add a named unique (e.g. "The Butcher's
Cleaver") to the backpack directly, not just equip it. **Save** and
**Save As...** always open a save-location dialog (defaulting to
`<name>_edited.sv`, never the file you opened) -- this app never
silently overwrites your original save. If you deliberately re-pick
that exact file in the dialog, it backs it up to `<name>.sv.bak` first
(once per session).

## CLI

Read-only:

```
python cli.py inspect single_0.sv
python cli.py list-items --query "plate"
python cli.py list-items --loc ring --min-level 10
```

All edits live under one `edit` subcommand, so one invocation can apply
several changes and write once:

```
python cli.py edit single_0.sv out.sv --gold 50000 --level 30 --str 60
python cli.py edit single_0.sv out.sv --equip-unique CHEST="Arkaine's Valor"
python cli.py edit single_0.sv out.sv --equip HAND_LEFT=IDI_WARRIOR --clear-slot RING_RIGHT
python cli.py edit single_0.sv out.sv --add-gold 2500 --add-item IDI_WARRIOR:1x3
python cli.py edit single_0.sv out.sv --add-unique-item "The Butcher's Cleaver"
python cli.py edit single_0.sv out.sv --set-belt 0=IDI_HEAL --clear-belt 1 --remove-backpack 5
python cli.py edit single_0.sv out.sv --gold 99999 --dry-run   # preview without writing
```

`--out` must differ from the input file unless `--overwrite` is passed --
**always keep a backup of a save you care about.** Equipping an item
into a slot its `i_loc` doesn't match (e.g. a ring into HEAD) is rejected
unless `--force` is given. Equipping a two-handed weapon (a staff, bow,
or some swords/axes/maces) while the other hand is occupied -- or
anything into a hand while the other hand holds a two-handed weapon --
is also rejected (`HandConflictError`), overridable with `--force` in
the CLI or a confirm prompt in the GUI. The belt only accepts scrolls
and healing/mana/rejuvenation potions (`--force`/"show all items" in
the picker overrides that too). Run `python cli.py edit --help` for the
full flag list (attributes, HP/mana, name, experience, stat points, etc).

## Known v1 gaps (see docstrings for detail)

- **Spec correction**: section 7 says the backpack grid's positive slot
  marker goes on an item footprint's bottom-*right* cell. A real 2-wide
  item (a Flail) in a real save has it on the bottom-*left* cell instead.
  `add_to_backpack()` matches the observed real data (bottom-left), not
  the spec text -- but this hasn't been confirmed against `inv.cpp`
  (not in the reference excerpts) or verified in the actual game client,
  so treat it as the best evidence available rather than certain.
- ~~No `InvItemWidth[]`/`InvItemHeight[]` table~~ Resolved: fetched
  `cursor.cpp` directly from `diasurgical/devilution` (it wasn't in the
  original reference excerpts) and generated real per-item footprints
  into `itemdata.ALL_ITEMS[i].width_cells/height_cells`. Verified against
  two items whose real footprint we already knew from `single_0.sv`'s
  own InvGrid data (Dagger 1x2, Flail 2x3) -- both matched exactly.
  `add_plain_item_to_backpack()`/`--add-item`/the GUI picker all default
  to the item's real size now; explicit `--width`/`--height`/`:WxH`
  still overrides it.
- **Real bug found and fixed**: `equip_unique()`/`add_unique_to_backpack()`
  only ever worked correctly for the ~9 *intrinsically*-unique quest
  items (Arkaine's Valor and friends -- iSeed is a direct table index
  there, no RNG). For the other ~80 "random-roll" uniques (Windforce,
  The Grandfather, ...), the compact `hero` format previously set no
  level/chance bits at all, so the real game's own reconstruction logic
  would reject the unique roll >99% of the time and silently produce a
  plain item instead -- this was shipped untested beyond the intrinsic
  case. Fixed by `itemrng.py`: it faithfully replicates the actual RNG
  (fetched `engine.cpp` from `diasurgical/devilution`, since it wasn't
  in the original excerpts) and searches for a seed that makes the
  reconstruction land on the exact requested unique. Exhaustively
  verified across the whole table (`test_unique_rng_reconstruction.py`):
  71 of 90 resolve correctly; the other ~10 intrinsic ones use the
  already-exact direct-index path. ~10 more can never be produced via
  the *hero* format at all -- not a gap in our solver, but a genuine bug
  in the original game's own unique-selection code (a comment says as
  much right in `items.cpp`) that deterministically always resolves to
  a *different*, later-declared unique on the same base item instead.
  If the save has a `game` file, this doesn't block anything visible:
  `game` is what the engine actually loads on resume, and it's written
  with the exact identity directly there (no RNG involved in that
  format at all), so `equip_unique()`/`add_unique_to_backpack()` still
  succeed -- only the hero-format character-select preview falls back
  to a plain instance of the base item, rather than confidently showing
  the wrong named unique. `find_seed_for_unique()` only raises
  `ValueError` for these ~10 when there's no `game` file to fall back
  on, i.e. no format in the save could represent that unique correctly
  at all.
- Unique/magical items written into the raw `game` format still only
  get correct identity fields (name, icon, `_iUid`), not the full
  numeric `_iPL*` bonus-stat fields -- the offset table for those is
  now fully known (cross-validated against every previously-verified
  offset in `ItemStruct`, all matched exactly) but writing custom
  values there isn't implemented yet. This is unrelated to the bug
  above: the compact `hero` format doesn't need those fields at all,
  since the engine recomputes stats itself from `(idx, iCreateInfo,
  iSeed)` on load -- it's specifically the raw format's *exact*
  numeric bonuses (as opposed to identity) that remain unwritten.
- `UNIQUE_ITEMS` cross-checked against an independent public source
  (diablo.noktis.pl/en/unique-items): item 0, The Butcher's Cleaver,
  matched exactly on name, base item, value, and all three powers.
- "Ear" trophy items (`idx == IDI_EAR`) are detected and left opaque,
  not decoded/edited (matches the spec's stated v1 scope).
- Hellfire and shareware/spawn saves are explicitly rejected
  (`GameFile.from_bytes` checks the magic word).

## Explicitly out of scope for v1

Ground-item placement, quest-state editing, multiplayer/closed-Battle.net
saves -- see the spec.

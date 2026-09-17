"""Verify itemrng.find_seed_for_unique() and its wiring into
Character.equip_unique()/add_unique_to_backpack(): for a "random-roll"
unique (anything other than the ~9 intrinsically-unique quest items),
the (iSeed, iCreateInfo) pair found must make the real game's own
reconstruction logic (simulated faithfully in itemrng.py, ported from
engine.cpp's RNG and items.cpp's GetItemAttrs/SetupAllItems/CheckUnique)
deterministically resolve back to that exact unique -- not just produce
*some* magic item.

This is an exhaustive check across the whole UNIQUE_ITEMS table (cheap:
each search is sub-millisecond), not just a couple of examples, since
the whole point is a solver that must work uniformly, not something
tuned to a few known-good cases.

Usage: python scripts/test_unique_rng_reconstruction.py path/to/save.sv
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from d1save import itemdata, itemrng
from d1save.character import Character, IMISC_UNIQUE, find_base_idx_for_unique


def main():
    save_path = sys.argv[1] if len(sys.argv) > 1 else "single_0.sv"

    checks = []
    resolved_count = 0
    blocked_count = 0

    for position, unique in enumerate(itemdata.UNIQUE_ITEMS):
        if not unique.name:
            continue
        base_idx = find_base_idx_for_unique(unique)
        if itemdata.ALL_ITEMS[base_idx].misc_id == IMISC_UNIQUE:
            continue  # intrinsic uniques use the other (already-verified) path

        try:
            seed, i_create_info = itemrng.find_seed_for_unique(position)
        except ValueError as e:
            blocked_count += 1
            checks.append((unique.name in itemrng.BLOCKED_UNIQUE_NAMES,
                            f"{unique.name!r} raised (expected only for BLOCKED_UNIQUE_NAMES): {e}"))
            continue

        lvl = i_create_info & itemrng.CF_LEVEL
        resolved_iblvl = itemrng._resolved_iblvl(seed, lvl)
        selected = itemrng._select_unique(unique.item_type, resolved_iblvl)
        ok = itemdata.UNIQUE_ITEMS[selected].name == unique.name if selected is not None else False
        resolved_count += 1
        if not ok:
            checks.append((False, f"{unique.name!r} (position {position}) resolves to "
                                   f"{itemdata.UNIQUE_ITEMS[selected].name if selected is not None else None!r} instead"))

    checks.append((True, f"{resolved_count} random-roll uniques resolved correctly, "
                          f"{blocked_count} correctly detected as blocked"))
    checks.append((blocked_count == len(itemrng.BLOCKED_UNIQUE_NAMES),
                    f"blocked count matches BLOCKED_UNIQUE_NAMES ({len(itemrng.BLOCKED_UNIQUE_NAMES)})"))

    # --- integration: equip a random-roll unique via Character, round-trip it ---
    c = Character.load(save_path)
    windforce_pos = next(i for i, u in enumerate(itemdata.UNIQUE_ITEMS) if u.name == "Windforce")
    c.equip_unique(4, windforce_pos)  # HAND_LEFT
    item = c.hero.inv_body[4]
    seed, i_create_info = itemrng.find_seed_for_unique(windforce_pos)
    checks.append((item.i_seed == seed and item.i_create_info == i_create_info,
                    f"equip_unique(Windforce) uses the solver's exact (seed, i_create_info): "
                    f"got ({item.i_seed}, {item.i_create_info:#x}), expected ({seed}, {i_create_info:#x})"))

    # --- intrinsic unique regression: must still use the direct-index path ---
    valor_pos = next(i for i, u in enumerate(itemdata.UNIQUE_ITEMS) if u.name == "Arkaine's Valor")
    c.equip_unique(6, valor_pos)  # CHEST
    valor_item = c.hero.inv_body[6]
    checks.append((valor_item.i_seed == valor_pos and valor_item.i_create_info == 0x200,
                    f"intrinsic unique (Arkaine's Valor) still uses direct iSeed=position: "
                    f"got (seed={valor_item.i_seed}, i_create_info={valor_item.i_create_info:#x})"))

    # --- a blocked unique with NO game file must raise cleanly, not silently misbehave ---
    bramble_pos = next(i for i, u in enumerate(itemdata.UNIQUE_ITEMS) if u.name == "Bramble")
    hero_only = Character(c.hero, None)
    try:
        hero_only.equip_unique(1, bramble_pos)  # RING_LEFT
        checks.append((False, "expected ValueError equipping Bramble with no game file to fall back on"))
    except ValueError:
        checks.append((True, "Bramble (blocked), no game file: raises ValueError instead of silently equipping wrong"))

    # --- a blocked unique WITH a game file present must still succeed: `game` is
    # what the engine actually loads on resume and writes exact identity directly
    # (no RNG involved there at all) -- only the pre-game hero-format preview can't
    # represent it exactly, so it should fall back to a plain base item, not raise ---
    c.equip_unique(1, bramble_pos)  # RING_LEFT
    bramble_game = c.game.player.inv_body[1]
    checks.append((bramble_game.iname == "Bramble" and bramble_game.i_uid == bramble_pos,
                   f"Bramble (blocked), game file present: game item is exact (iname={bramble_game.iname!r}, "
                   f"i_uid={bramble_game.i_uid})"))
    bramble_hero = c.hero.inv_body[1]
    bramble_base_idx = find_base_idx_for_unique(itemdata.UNIQUE_ITEMS[bramble_pos])
    checks.append((bramble_hero.idx == bramble_base_idx and bramble_hero.i_create_info == 0,
                   f"Bramble (blocked), game file present: hero item falls back to a plain base item "
                   f"(idx={bramble_hero.idx}, i_create_info={bramble_hero.i_create_info:#x})"))

    # --- save/reload survives ---
    out_path = str(Path(save_path).with_suffix(".unique_rng_test.sv"))
    c.save(save_path, out_path)
    c2 = Character.load(out_path)
    equip2 = c2.list_equipment()
    checks.append((next(e for e in equip2 if e['slot'] == 'HAND_LEFT').get('name') == 'Windforce',
                    "Windforce survives save/reload"))
    checks.append((next(e for e in equip2 if e['slot'] == 'CHEST').get('name') == "Arkaine's Valor",
                    "Arkaine's Valor survives save/reload"))
    Path(out_path).unlink(missing_ok=True)

    all_ok = True
    for ok, msg in checks:
        print(("  OK: " if ok else "  FAIL: ") + msg)
        all_ok = all_ok and ok

    if not all_ok:
        raise SystemExit("some checks failed")
    print("\nAll unique-RNG-reconstruction checks passed.")


if __name__ == "__main__":
    main()

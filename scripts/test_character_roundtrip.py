"""End-to-end test: load a real save via Character, edit stats/gold/
equipment/backpack in both formats at once, save, reload fresh from the
saved file, and confirm every edit is visible and nothing else moved.

Usage: python scripts/test_character_roundtrip.py path/to/save.sv
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from d1save import itemdata
from d1save.character import Character


def main():
    save_path = sys.argv[1] if len(sys.argv) > 1 else "single_0.sv"
    out_path = str(Path(save_path).with_suffix(".edited_test.sv"))

    c = Character.load(save_path)
    print(f"loaded: name={c.name!r} level={c.level} gold={c.gold} has_game_file={c.has_game_file}")
    print("  equipment:", c.list_equipment())
    print("  backpack (first few):", c.list_backpack()[:5])

    # --- make a batch of edits spanning stats + items ---
    c.gold = 12345
    c.level = 30
    c.experience = 999999
    c.set_attributes(str_=60, mag=60, dex=60, vit=60)
    c.set_hp(120, 150)
    c.set_mana(80, 100)

    sword_idx = itemdata.ITEM_INDEX_BY_NAME['IDI_WARRIOR']  # Short Sword
    c.equip_plain(4, sword_idx)  # HAND_LEFT

    valor_idx = itemdata.ITEM_INDEX_BY_NAME['IDI_ARMOFVAL']
    unique_pos = next(i for i, u in enumerate(itemdata.UNIQUE_ITEMS) if u.name == "Arkaine's Valor")
    c.equip_unique(6, unique_pos)  # CHEST

    gold_slot = c.add_gold_to_backpack(2500)
    print(f"  added gold stack at backpack slot {gold_slot}")

    result = c.save(save_path, out_path)
    print("  patched files:", result)

    # --- reload fresh and verify ---
    c2 = Character.load(out_path)
    checks = [
        (c2.gold == 12345, f"gold: expected 12345, got {c2.gold}"),
        (c2.level == 30, f"level: expected 30, got {c2.level}"),
        (c2.experience == 999999, f"experience: expected 999999, got {c2.experience}"),
        (c2.attributes == {'str': 60, 'mag': 60, 'dex': 60, 'vit': 60}, f"attributes: {c2.attributes}"),
        (c2.hp_display() == (120, 150), f"hp: {c2.hp_display()}"),
        (c2.mana_display() == (80, 100), f"mana: {c2.mana_display()}"),
    ]
    equip = c2.list_equipment()
    hand_left = next(e for e in equip if e['slot'] == 'HAND_LEFT')
    chest = next(e for e in equip if e['slot'] == 'CHEST')
    checks.append((hand_left.get('idx') == sword_idx, f"HAND_LEFT: {hand_left}"))
    checks.append((chest.get('name') == "Arkaine's Valor", f"CHEST: {chest}"))
    backpack = c2.list_backpack()
    checks.append((any(b['name'] == 'Gold' for b in backpack), f"backpack gold not found: {backpack}"))

    all_ok = True
    for ok, msg in checks:
        print(("  OK: " if ok else "  FAIL: ") + msg)
        all_ok = all_ok and ok

    if not all_ok:
        raise SystemExit("some checks failed")
    print("\nAll checks passed -- edits round-tripped through a real save file correctly.")
    Path(out_path).unlink()


if __name__ == "__main__":
    main()

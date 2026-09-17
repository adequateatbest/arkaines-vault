"""Verify the two-handed-weapon hand-conflict check (Character.equip_plain
/equip_unique's force_hands parameter, HandConflictError): a two-handed
item (bow/staff/etc, ILOC_TWOHAND) can't be equipped unless the other
hand is empty, and nothing can be equipped in a hand while the other
hand holds a two-handed item -- unless force_hands=True.

Confirmed against reference/items.cpp: both hand slots are read
independently there (nothing duplicates a 2H item into both slots), so
this is purely a validation rule we enforce ourselves to match what the
real client would reject.

Usage: python scripts/test_two_handed_validation.py path/to/save.sv
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from d1save import itemdata
from d1save.character import Character, HandConflictError

HAND_LEFT, HAND_RIGHT = 4, 5


def main():
    save_path = sys.argv[1] if len(sys.argv) > 1 else "single_0.sv"

    c = Character.load(save_path)
    sword_idx = itemdata.ITEM_INDEX_BY_NAME['IDI_WARRIOR']   # Short Sword, one-handed
    bow_idx = itemdata.ITEM_INDEX_BY_NAME['IDI_ROGUE']       # Short Bow, two-handed
    shield_idx = itemdata.ITEM_INDEX_BY_NAME['IDI_WARRSHLD']  # Buckler, one-handed
    assert itemdata.ALL_ITEMS[bow_idx].i_loc == 2, "Short Bow should be ILOC_TWOHAND"
    assert itemdata.ALL_ITEMS[sword_idx].i_loc == 1, "Short Sword should be ILOC_ONEHAND"

    checks = []

    c.clear_equip_slot(HAND_LEFT)
    c.clear_equip_slot(HAND_RIGHT)

    # 2H weapon into an empty pair of hands: fine
    c.equip_plain(HAND_LEFT, bow_idx)
    checks.append((not c.list_equipment()[HAND_LEFT]['empty'], "2H weapon equips into empty hands"))

    # one-handed into the other hand while a 2H weapon holds this one: blocked
    try:
        c.equip_plain(HAND_RIGHT, sword_idx)
        checks.append((False, "expected HandConflictError equipping into the other hand"))
    except HandConflictError:
        checks.append((True, "one-handed item blocked while other hand holds a 2H weapon"))

    # force_hands=True overrides it
    c.equip_plain(HAND_RIGHT, sword_idx, force_hands=True)
    checks.append((not c.list_equipment()[HAND_RIGHT]['empty'], "force_hands=True overrides the block"))

    # reset: two one-handed items in both hands is fine (no conflict)
    c.clear_equip_slot(HAND_LEFT)
    c.clear_equip_slot(HAND_RIGHT)
    c.equip_plain(HAND_LEFT, sword_idx)
    c.equip_plain(HAND_RIGHT, shield_idx)
    checks.append((not c.list_equipment()[HAND_LEFT]['empty'] and not c.list_equipment()[HAND_RIGHT]['empty'],
                   "two one-handed items in both hands is fine"))

    # now try to equip a 2H weapon into either hand: blocked because the
    # OTHER hand is occupied (by a one-handed item, not even a 2H one)
    try:
        c.equip_plain(HAND_LEFT, bow_idx)
        checks.append((False, "expected HandConflictError equipping a 2H weapon over an occupied other hand"))
    except HandConflictError:
        checks.append((True, "2H weapon blocked when the other hand is occupied"))

    # clearing the conflicting hand first lets it through
    c.clear_equip_slot(HAND_RIGHT)
    c.equip_plain(HAND_LEFT, bow_idx)
    checks.append((c.list_equipment()[HAND_LEFT]['idx'] == bow_idx, "2H weapon equips after clearing the other hand"))

    all_ok = True
    for ok, msg in checks:
        print(("  OK: " if ok else "  FAIL: ") + msg)
        all_ok = all_ok and ok

    if not all_ok:
        raise SystemExit("some checks failed")
    print("\nAll two-handed validation checks passed.")


if __name__ == "__main__":
    main()

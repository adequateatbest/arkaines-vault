"""Verify the belt-item-type restriction (Character.set_belt_slot's
`force` parameter): only scrolls and healing/mana/rejuvenation potions
can go in the belt, per explicit request -- not elixirs, not staves,
not jewelry. force=True overrides it.

Usage: python scripts/test_belt_restriction.py path/to/save.sv
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from d1save import itemdata
from d1save.character import Character


def main():
    save_path = sys.argv[1] if len(sys.argv) > 1 else "single_0.sv"
    c = Character.load(save_path)

    heal_idx = itemdata.ITEM_INDEX_BY_NAME['IDI_HEAL']         # Potion of Healing
    mana_idx = itemdata.ITEM_INDEX_BY_NAME['IDI_MANA']         # Potion of Mana
    portal_idx = itemdata.ITEM_INDEX_BY_NAME['IDI_PORTAL']     # Scroll of Town Portal
    resurrect_idx = itemdata.ITEM_INDEX_BY_NAME['IDI_RESURRECT']  # Scroll of Resurrect (targeted scroll)
    sword_idx = itemdata.ITEM_INDEX_BY_NAME['IDI_WARRIOR']     # Short Sword -- not belt-eligible
    rejuv_matches = [i for i, it in enumerate(itemdata.ALL_ITEMS) if it.name == 'Potion of Rejuvenation']
    assert rejuv_matches, "expected a 'Potion of Rejuvenation' entry in ALL_ITEMS"
    rejuv_idx = rejuv_matches[0]

    checks = []

    for idx, label in ((heal_idx, 'Potion of Healing'), (mana_idx, 'Potion of Mana'),
                       (portal_idx, 'Scroll of Town Portal'), (resurrect_idx, 'Scroll of Resurrect'),
                       (rejuv_idx, 'Potion of Rejuvenation')):
        c.set_belt_slot(0, idx)
        entry = c.list_belt()[0]
        checks.append((entry.get('idx') == idx, f"{label} allowed in belt: {entry}"))

    try:
        c.set_belt_slot(1, sword_idx)
        checks.append((False, "expected a ValueError equipping a sword into the belt"))
    except ValueError as e:
        checks.append((True, f"sword blocked from belt: {e}"))

    c.set_belt_slot(1, sword_idx, force=True)
    checks.append((c.list_belt()[1].get('idx') == sword_idx, "force=True overrides the belt restriction"))

    all_ok = True
    for ok, msg in checks:
        print(("  OK: " if ok else "  FAIL: ") + msg)
        all_ok = all_ok and ok

    if not all_ok:
        raise SystemExit("some checks failed")
    print("\nAll belt restriction checks passed.")


if __name__ == "__main__":
    main()

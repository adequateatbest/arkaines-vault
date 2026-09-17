"""Verify Character.add_unique_to_backpack(): an intrinsically-unique
quest item (e.g. "The Butcher's Cleaver") can be placed in the backpack,
not just equipped directly -- using its base item's real grid footprint
by default. Regression test for a real gap: the item picker used to
exclude uniques from the backpack-add dialog entirely, so there was no
way to add one there at all.

Usage: python scripts/test_unique_in_backpack.py path/to/save.sv
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from d1save import itemdata
from d1save.character import Character, find_base_idx_for_unique


def main():
    save_path = sys.argv[1] if len(sys.argv) > 1 else "single_0.sv"
    out_path = str(Path(save_path).with_suffix(".unique_backpack_test.sv"))

    c = Character.load(save_path)
    position = next(i for i, u in enumerate(itemdata.UNIQUE_ITEMS) if u.name == "The Butcher's Cleaver")
    base_idx = find_base_idx_for_unique(itemdata.UNIQUE_ITEMS[position])

    checks = []

    slot = c.add_unique_to_backpack(position)
    entry = next(e for e in c.list_backpack() if e['slot'] == slot)
    checks.append((entry.get('name') == "The Butcher's Cleaver", f"unique added to backpack: {entry}"))

    cells = c.backpack_grid_cells()
    occupied = sum(1 for v in cells if v == slot)
    expected_cells = itemdata.ALL_ITEMS[base_idx].width_cells * itemdata.ALL_ITEMS[base_idx].height_cells
    checks.append((occupied == expected_cells,
                    f"footprint defaults to the base item's real size ({expected_cells} cells): got {occupied}"))

    c.save(save_path, out_path)
    c2 = Character.load(out_path)
    entry2 = next(e for e in c2.list_backpack() if e['slot'] == slot)
    checks.append((entry2.get('name') == "The Butcher's Cleaver", f"survives save/reload: {entry2}"))

    all_ok = True
    for ok, msg in checks:
        print(("  OK: " if ok else "  FAIL: ") + msg)
        all_ok = all_ok and ok

    Path(out_path).unlink(missing_ok=True)
    if not all_ok:
        raise SystemExit("some checks failed")
    print("\nAll unique-in-backpack checks passed.")


if __name__ == "__main__":
    main()

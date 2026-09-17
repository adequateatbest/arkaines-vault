#!/usr/bin/env python
"""Diablo 1 save editor -- CLI front end over the d1save/ format/logic
core. Meant for exercising and verifying the byte-level work against
real saves before any GUI gets built on top.

Read-only:
    python cli.py inspect single_0.sv
    python cli.py list-items --query sword
    python cli.py list-items --loc ring --min-level 10

Editing (all mutations live under one `edit` command so a single
invocation can apply several changes and write once):
    python cli.py edit single_0.sv out.sv --gold 50000 --level 30 --str 60
    python cli.py edit single_0.sv out.sv --equip-unique CHEST="Arkaine's Valor"
    python cli.py edit single_0.sv out.sv --equip HAND_LEFT=IDI_WARRIOR
    python cli.py edit single_0.sv out.sv --clear-slot RING_RIGHT
    python cli.py edit single_0.sv out.sv --add-gold 2500 --add-item IDI_WARRIOR:1x3
    python cli.py edit single_0.sv out.sv --remove-backpack 5 --clear-belt 0
    python cli.py edit single_0.sv out.sv --gold 99999 --dry-run

`--out` must differ from the input file unless `--overwrite` is given --
always keep a backup of a save you care about.
"""
import argparse
import sys
from pathlib import Path

from d1save import itemdata
from d1save.character import Character, find_base_idx_for_unique
from d1save.pkplayer import INVBODY_SLOTS

ILOC_NAMES = {
    0: 'none', 1: 'onehand', 2: 'twohand', 3: 'armor', 4: 'helm',
    5: 'ring', 6: 'amulet', 7: 'unequipable', 8: 'belt', -1: 'invalid',
}
ILOC_BY_NAME = {v: k for k, v in ILOC_NAMES.items()}


def _resolve_item_idx(spec: str) -> int:
    """Accept a bare integer, an IDI_* enum name, or an item display name."""
    spec = spec.strip()
    if spec.lstrip('-').isdigit():
        return int(spec)
    if spec in itemdata.ITEM_INDEX_BY_NAME:
        return itemdata.ITEM_INDEX_BY_NAME[spec]
    matches = [i for i, it in enumerate(itemdata.ALL_ITEMS) if it.name and it.name.lower() == spec.lower()]
    if matches:
        return matches[0]
    raise SystemExit(f"no item found matching {spec!r} (try 'list-items --query ...' to search)")


def _resolve_unique_position(spec: str) -> int:
    spec = spec.strip()
    if spec.isdigit():
        return int(spec)
    matches = [i for i, u in enumerate(itemdata.UNIQUE_ITEMS) if u.name.lower() == spec.lower()]
    if matches:
        return matches[0]
    raise SystemExit(f"no unique item found matching {spec!r} (try 'list-items --uniques --query ...')")


def _split_kv(spec: str, sep: str, label: str):
    if sep not in spec:
        raise SystemExit(f"expected KEY{sep}VALUE for {label}, got {spec!r}")
    key, _, value = spec.partition(sep)
    return key.strip(), value.strip()


def _parse_spec_and_footprint(spec: str, resolve):
    """'X:1x3' -> (resolve('X'), 1, 3); footprint is (None, None) if
    omitted, so the caller's real-grid-footprint default applies."""
    item_part, _, dims_part = spec.partition(':')
    value = resolve(item_part)
    if not dims_part:
        return value, None, None
    if 'x' not in dims_part:
        raise SystemExit(f"expected SPEC:WxH, got {spec!r}")
    w, _, h = dims_part.partition('x')
    return value, int(w), int(h)


def _parse_item_and_footprint(spec: str):
    """'IDI_WARRIOR:1x3' -> (idx, 1, 3); footprint defaults to the item's
    real grid footprint (itemdata.ALL_ITEMS[idx].width_cells/height_cells)
    if omitted."""
    return _parse_spec_and_footprint(spec, _resolve_item_idx)


def _print_summary(c: Character):
    print(f"Name:       {c.name}")
    print(f"Level:      {c.level}")
    print(f"Experience: {c.experience}")
    print(f"Gold:       {c.gold}")
    print(f"Stat points: {c.stat_points}")
    attrs = c.attributes
    print(f"Str/Mag/Dex/Vit: {attrs['str']}/{attrs['mag']}/{attrs['dex']}/{attrs['vit']}")
    hp, max_hp = c.hp_display()
    mana, max_mana = c.mana_display()
    print(f"HP:   {hp}/{max_hp}")
    print(f"Mana: {mana}/{max_mana}")
    print(f"Has 'game' file (in-progress dungeon run): {c.has_game_file}")

    print("\nEquipment:")
    for entry in c.list_equipment():
        if entry['empty']:
            print(f"  {entry['slot']:<12} -")
        else:
            print(f"  {entry['slot']:<12} [{entry['idx']}] {entry['name']}")

    print("\nBelt:")
    for entry in c.list_belt():
        label = '-' if entry['empty'] else f"[{entry['idx']}] {entry['name']}"
        print(f"  slot {entry['slot']}   {label}")

    print("\nBackpack:")
    backpack = c.list_backpack()
    if not backpack:
        print("  (empty)")
    for entry in backpack:
        print(f"  slot {entry['slot']:<3} [{entry['idx']}] {entry['name']}")


def cmd_inspect(args):
    _print_summary(Character.load(args.save))


def cmd_list_items(args):
    query = (args.query or "").lower()
    loc_filter = ILOC_BY_NAME[args.loc] if args.loc else None

    if not args.uniques_only:
        for i, item in enumerate(itemdata.ALL_ITEMS):
            if not item.name:
                continue
            if query and query not in item.name.lower():
                continue
            if loc_filter is not None and item.i_loc != loc_filter:
                continue
            if args.min_level is not None and item.min_level < args.min_level:
                continue
            tag = f" ({item.enum_name})" if item.enum_name else ""
            reqs = []
            if item.min_str:
                reqs.append(f"str {item.min_str}")
            if item.min_mag:
                reqs.append(f"mag {item.min_mag}")
            if item.min_dex:
                reqs.append(f"dex {item.min_dex}")
            req_str = f" [{', '.join(reqs)}]" if reqs else ""
            print(f"[{i}]{tag} {item.name} -- {ILOC_NAMES.get(item.i_loc, item.i_loc)}, "
                  f"lvl {item.min_level}, value {item.value}{req_str}")

    if args.uniques or args.uniques_only:
        print("\nUniques:")
        for i, u in enumerate(itemdata.UNIQUE_ITEMS):
            if not u.name:
                continue
            if query and query not in u.name.lower():
                continue
            print(f"[{i}] {u.name} (base type: {u.item_type}, min lvl {u.min_level}, value {u.value})")


def _apply_edits(c: Character, args):
    """Apply every requested edit to `c` in place. Order: stats, then
    equipment, then belt, then backpack -- so e.g. --clear-slot before
    --equip on the same slot behaves predictably."""
    if args.name is not None:
        c.name = args.name
    if args.gold is not None:
        c.gold = args.gold
    if args.level is not None:
        c.level = args.level
    if args.experience is not None:
        c.experience = args.experience
    if args.stat_points is not None:
        c.stat_points = args.stat_points
    if any(v is not None for v in (args.str_, args.mag, args.dex, args.vit)):
        c.set_attributes(str_=args.str_, mag=args.mag, dex=args.dex, vit=args.vit)
    if args.hp is not None:
        c.set_hp(args.hp, args.max_hp)
    if args.mana is not None:
        c.set_mana(args.mana, args.max_mana)

    for slot_name in args.clear_slot or ():
        c.clear_equip_slot(INVBODY_SLOTS.index(slot_name))

    for spec in args.equip or ():
        slot_name, item_spec = _split_kv(spec, '=', '--equip')
        slot = INVBODY_SLOTS.index(slot_name)
        c.equip_plain(slot, _resolve_item_idx(item_spec), force=args.force, force_hands=args.force)

    for spec in args.equip_unique or ():
        slot_name, unique_spec = _split_kv(spec, '=', '--equip-unique')
        slot = INVBODY_SLOTS.index(slot_name)
        c.equip_unique(slot, _resolve_unique_position(unique_spec), force=args.force, force_hands=args.force)

    for slot_str in args.clear_belt or ():
        c.clear_belt_slot(int(slot_str))

    for spec in args.set_belt or ():
        slot_str, item_spec = _split_kv(spec, '=', '--set-belt')
        c.set_belt_slot(int(slot_str), _resolve_item_idx(item_spec), force=args.force)

    for slot_str in args.remove_backpack or ():
        c.remove_from_backpack(int(slot_str))

    for amount_str in args.add_gold or ():
        slot = c.add_gold_to_backpack(int(amount_str))
        print(f"  added {amount_str} gold at backpack slot {slot}")

    for spec in args.add_item or ():
        idx, w, h = _parse_item_and_footprint(spec)
        slot = c.add_plain_item_to_backpack(idx, w, h)
        actual_w = w if w is not None else itemdata.ALL_ITEMS[idx].width_cells
        actual_h = h if h is not None else itemdata.ALL_ITEMS[idx].height_cells
        print(f"  added item {idx} ({actual_w}x{actual_h}) at backpack slot {slot}")

    for spec in args.add_unique_item or ():
        position, w, h = _parse_spec_and_footprint(spec, _resolve_unique_position)
        slot = c.add_unique_to_backpack(position, w, h)
        unique = itemdata.UNIQUE_ITEMS[position]
        base_idx = find_base_idx_for_unique(unique)
        actual_w = w if w is not None else itemdata.ALL_ITEMS[base_idx].width_cells
        actual_h = h if h is not None else itemdata.ALL_ITEMS[base_idx].height_cells
        print(f"  added unique {unique.name!r} ({actual_w}x{actual_h}) at backpack slot {slot}")


def cmd_edit(args):
    if Path(args.save).resolve() == Path(args.out).resolve() and not args.overwrite:
        raise SystemExit(
            "--out is the same file as the input save; pass --overwrite to edit in "
            "place (keep a backup!), or use a different --out path"
        )

    c = Character.load(args.save)
    _apply_edits(c, args)

    if args.dry_run:
        print("--dry-run: not writing. Resulting state would be:\n")
        _print_summary(c)
        return

    result = c.save(args.save, args.out)
    print(f"wrote {args.out}: {result}")


def build_parser():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest='command', required=True)

    p_inspect = sub.add_parser('inspect', help='show a character summary')
    p_inspect.add_argument('save')
    p_inspect.set_defaults(func=cmd_inspect)

    p_list = sub.add_parser('list-items', help='search the item reference tables')
    p_list.add_argument('--query', help='substring to search item names for')
    p_list.add_argument('--loc', choices=sorted(ILOC_BY_NAME), help='filter by equip location')
    p_list.add_argument('--min-level', type=int, help='only items with min_level >= this')
    p_list.add_argument('--uniques', action='store_true', help='also list matching unique items')
    p_list.add_argument('--uniques-only', action='store_true', help='list only unique items')
    p_list.set_defaults(func=cmd_list_items)

    p_edit = sub.add_parser('edit', help='apply one or more edits and write the save',
                             formatter_class=argparse.RawDescriptionHelpFormatter)
    p_edit.add_argument('save')
    p_edit.add_argument('out')
    p_edit.add_argument('--overwrite', action='store_true', help='allow --out to be the same file as the input')
    p_edit.add_argument('--dry-run', action='store_true', help="apply edits in memory and print the result, don't write")
    p_edit.add_argument('--force', action='store_true',
                         help='skip the equip-slot/item-type compatibility check, the '
                              'two-handed-weapon hand-conflict check, and the belt-item-type check')

    p_edit.add_argument('--name')
    p_edit.add_argument('--gold', type=int)
    p_edit.add_argument('--level', type=int)
    p_edit.add_argument('--experience', type=int)
    p_edit.add_argument('--stat-points', type=int, dest='stat_points')
    p_edit.add_argument('--str', dest='str_', type=int)
    p_edit.add_argument('--mag', type=int)
    p_edit.add_argument('--dex', type=int)
    p_edit.add_argument('--vit', type=int)
    p_edit.add_argument('--hp', type=int, help='current HP (displayed value)')
    p_edit.add_argument('--max-hp', type=int)
    p_edit.add_argument('--mana', type=int, help='current mana (displayed value)')
    p_edit.add_argument('--max-mana', type=int)

    p_edit.add_argument('--equip', action='append', metavar='SLOT=ITEM',
                         help=f'slot is one of {",".join(INVBODY_SLOTS)}; ITEM is a name, IDI_* enum name, or idx; repeatable')
    p_edit.add_argument('--equip-unique', action='append', metavar='SLOT=UNIQUE_NAME',
                         help='equip a named unique item (e.g. "Arkaine\'s Valor" or "Windforce") by '
                              'name or UNIQUE_ITEMS position; repeatable')
    p_edit.add_argument('--clear-slot', action='append', metavar='SLOT', choices=INVBODY_SLOTS,
                         help='empty an equip slot; repeatable')

    p_edit.add_argument('--set-belt', action='append', metavar='SLOT=ITEM', help='0-based belt slot; repeatable')
    p_edit.add_argument('--clear-belt', action='append', metavar='SLOT', help='0-based belt slot; repeatable')

    p_edit.add_argument('--add-gold', action='append', metavar='AMOUNT', help='add a gold stack (max 5000); repeatable')
    p_edit.add_argument('--add-item', action='append', metavar='ITEM[:WxH]',
                         help="add a plain item to the backpack; footprint defaults to the item's "
                              "real grid size (:WxH overrides it); repeatable")
    p_edit.add_argument('--add-unique-item', action='append', metavar='UNIQUE_NAME[:WxH]',
                         help="add a named unique item (e.g. \"The Butcher's Cleaver\" or \"Windforce\") "
                              "to the backpack by name or UNIQUE_ITEMS position; footprint defaults to "
                              "its base item's real grid size (:WxH overrides it); repeatable")
    p_edit.add_argument('--remove-backpack', action='append', metavar='SLOT',
                         help='0-based InvList slot to empty; repeatable')

    p_edit.set_defaults(func=cmd_edit)

    return p


def main():
    parser = build_parser()
    args = parser.parse_args()
    try:
        args.func(args)
    except (ValueError, KeyError, RuntimeError, IndexError) as e:
        raise SystemExit(f"error: {e}")


if __name__ == "__main__":
    sys.exit(main())

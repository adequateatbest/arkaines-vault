"""Generate d1save/itemdata.py from the devilution source excerpts under
reference/ (itemdat.cpp for AllItemsList/UniqueItemList, enums.h for the
symbolic constants those tables reference, cursor.cpp for each item's
real backpack-grid footprint) -- rather than hand-transcribing ~125
items, ~90 uniques, and their grid dimensions.

This targets the non-Hellfire ("RETL") build: #ifdef HELLFIRE blocks are
dropped, #ifndef HELLFIRE blocks are kept. Rerun after any change to the
reference/ source:

    python scripts/gen_itemdata.py
"""
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
REFERENCE = ROOT / "reference"
OUTPUT = ROOT / "d1save" / "itemdata.py"

ALL_ITEMS_FIELDS = [
    "i_rnd", "i_class", "i_loc", "i_curs", "i_type", "i_item_id", "i_name", "i_sname",
    "i_min_mlvl", "i_durability", "i_min_dam", "i_max_dam", "i_min_ac", "i_max_ac",
    "i_min_str", "i_min_mag", "i_min_dex", "i_flags", "i_misc_id", "i_spell",
    "i_usable", "i_value", "i_max_value",
]

UNIQUE_ITEM_FIELDS = [
    "ui_name", "ui_item_id", "ui_min_lvl", "ui_num_pl", "ui_value",
    "ui_power1", "ui_param1", "ui_param2",
    "ui_power2", "ui_param3", "ui_param4",
    "ui_power3", "ui_param5", "ui_param6",
    "ui_power4", "ui_param7", "ui_param8",
    "ui_power5", "ui_param9", "ui_param10",
    "ui_power6", "ui_param11", "ui_param12",
]


def strip_comments(text: str) -> str:
    text = re.sub(r"/\*.*?\*/", "", text, flags=re.DOTALL)
    text = re.sub(r"//[^\n]*", "", text)
    return text


def resolve_hellfire_ifdefs(text: str, hellfire: bool = False, spawn: bool = False) -> str:
    """Resolve #ifdef/#ifndef HELLFIRE and SPAWN (the two build-variant
    macros used in these reference files) plus their #else/#endif, for a
    single build variant (defaults: retail, non-Hellfire, non-spawn).
    Every other preprocessor directive is dropped unconditionally, except
    an unrecognized #if/#ifdef/#ifndef pushes a neutral pass-through
    frame so nesting/#endif bookkeeping stays balanced."""
    defined = {"HELLFIRE": hellfire, "SPAWN": spawn}
    stack = []  # each frame: {'parent_active': bool, 'if_cond': bool, 'in_else': bool}

    def top_active():
        if not stack:
            return True
        frame = stack[-1]
        branch_cond = (not frame["if_cond"]) if frame["in_else"] else frame["if_cond"]
        return frame["parent_active"] and branch_cond

    out = []
    for line in text.split("\n"):
        s = line.strip()
        m = re.match(r"#(ifdef|ifndef)\s+(\w+)", s)
        if m:
            kind, macro = m.groups()
            if macro in defined:
                cond = defined[macro] if kind == "ifdef" else not defined[macro]
            else:
                cond = top_active()  # unknown macro: neutral pass-through
            stack.append({"parent_active": top_active(), "if_cond": cond, "in_else": False})
            continue
        if s.startswith("#if"):  # bare #if EXPR: neutral pass-through
            stack.append({"parent_active": top_active(), "if_cond": True, "in_else": False})
            continue
        if s.startswith("#else"):
            stack[-1]["in_else"] = True
            continue
        if s.startswith("#endif"):
            stack.pop()
            continue
        if s.startswith("#"):
            continue
        if top_active():
            out.append(line)
    assert not stack, "unbalanced #ifdef/#endif"
    return "\n".join(out)


def parse_enum(text: str, member_name: str) -> dict:
    """Locate the typedef-enum block containing `member_name` (searching
    text already run through resolve_hellfire_ifdefs) and return
    {symbol: int_value}, applying C's auto-increment for members without
    an explicit '= value'."""
    idx = text.index(member_name)
    start = text.rfind("typedef enum", 0, idx)
    end = text.index(";", idx)
    body = text[start:end]
    inner = body[body.index("{") + 1:body.rindex("}")]

    values = {}
    next_value = 0
    for entry in inner.split(","):
        entry = entry.strip()
        if not entry:
            continue
        m = re.match(r"^(\w+)\s*(?:=\s*(-?0[xX][0-9a-fA-F]+|-?\d+|\w+))?$", entry)
        if not m:
            raise ValueError(f"couldn't parse enum entry: {entry!r}")
        name, val = m.groups()
        if val is not None:
            next_value = values[val] if val in values else int(val, 0)
        values[name] = next_value
        next_value += 1
    return values


def split_top_level_braces(body: str):
    """Extract each top-level {...} entry's inner text from an array
    initializer body, ignoring commas/whitespace between entries."""
    entries = []
    depth = 0
    current = []
    in_string = False
    prev = ""
    for ch in body:
        if in_string:
            current.append(ch)
            if ch == '"' and prev != "\\":
                in_string = False
            prev = ch
            continue
        if ch == '"':
            in_string = True
            current.append(ch)
            prev = ch
            continue
        if ch == "{":
            if depth == 0:
                current = []
            else:
                current.append(ch)
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                entries.append("".join(current))
            else:
                current.append(ch)
        elif depth > 0:
            current.append(ch)
        prev = ch
    return entries


def split_fields(entry: str):
    fields = []
    current = []
    in_string = False
    prev = ""
    for ch in entry:
        if in_string:
            current.append(ch)
            if ch == '"' and prev != "\\":
                in_string = False
            prev = ch
            continue
        if ch == '"':
            in_string = True
            current.append(ch)
        elif ch == "," :
            fields.append("".join(current).strip())
            current = []
        else:
            current.append(ch)
        prev = ch
    if "".join(current).strip():
        fields.append("".join(current).strip())
    return fields


def resolve_field(field: str, enums: dict):
    if field.startswith('"') and field.endswith('"'):
        return field[1:-1]
    if field == "NULL":
        return None
    if field == "TRUE":
        return True
    if field == "FALSE":
        return False
    if re.match(r"^-?\d+$", field):
        return int(field)
    if field in enums:
        return enums[field]
    return field  # unresolved symbol (e.g. a UITYPE_* / IPL_* name) -- kept as-is


def load_array(text: str, array_name: str, field_names, enums: dict):
    m = re.search(re.escape(array_name) + r"\[\]\s*=\s*\{", text)
    if not m:
        raise ValueError(f"array {array_name!r} not found")
    body_start = m.end() - 1  # position of the opening '{'
    depth = 0
    i = body_start
    while True:
        if text[i] == "{":
            depth += 1
        elif text[i] == "}":
            depth -= 1
            if depth == 0:
                break
        i += 1
    body = text[body_start + 1:i]

    rows = []
    for entry_text in split_top_level_braces(body):
        fields = split_fields(entry_text)
        assert len(fields) == len(field_names), (
            f"{array_name}: expected {len(field_names)} fields, got {len(fields)}: {fields}"
        )
        row = {name: resolve_field(f, enums) for name, f in zip(field_names, fields)}
        rows.append(row)
    return rows


def load_int_array(text: str, array_name: str):
    """Parse a flat `const int NAME[] = { N, N * 28, ... };` array (e.g.
    cursor.cpp's InvItemWidth/InvItemHeight) into a plain list of ints."""
    m = re.search(re.escape(array_name) + r"\[\]\s*=\s*\{", text)
    if not m:
        raise ValueError(f"array {array_name!r} not found")
    body_start = m.end() - 1
    depth = 0
    i = body_start
    while True:
        if text[i] == "{":
            depth += 1
        elif text[i] == "}":
            depth -= 1
            if depth == 0:
                break
        i += 1
    body = text[body_start + 1:i]

    values = []
    for entry in body.split(","):
        entry = entry.strip()
        if not entry:
            continue
        m2 = re.match(r"^(\d+)(?:\s*\*\s*(\d+))?$", entry)
        if not m2:
            raise ValueError(f"couldn't parse array entry: {entry!r}")
        a, b = m2.groups()
        values.append(int(a) * (int(b) if b else 1))
    return values


def main():
    itemdat_src = strip_comments((REFERENCE / "itemdat.cpp").read_text())
    itemdat_src = resolve_hellfire_ifdefs(itemdat_src, hellfire=False)

    enums_src = strip_comments((REFERENCE / "enums.h").read_text())
    enums_src = resolve_hellfire_ifdefs(enums_src, hellfire=False)

    enums = {}
    for member_probe in (
        "IDROP_REGULAR", "ICLASS_GOLD", "ILOC_HELM", "ICURS_GOLD",
        "ITYPE_SWORD", "ISPL_FIREDAM", "IMISC_UNIQUE", "SPL_FIREBALL",
    ):
        enums.update(parse_enum(enums_src, member_probe))

    item_indexes = parse_enum(enums_src, "IDI_GOLD")
    idx_to_enum_name = {}
    for name, value in item_indexes.items():
        idx_to_enum_name.setdefault(value, name)  # first-defined name wins (e.g. IDI_CLEAVER over its IDI_FIRSTQUEST alias)

    all_items = load_array(itemdat_src, "AllItemsList", ALL_ITEMS_FIELDS, enums)
    unique_items = load_array(itemdat_src, "UniqueItemList", UNIQUE_ITEM_FIELDS, enums)

    cursor_src = strip_comments((REFERENCE / "cursor.cpp").read_text())
    cursor_src = resolve_hellfire_ifdefs(cursor_src, hellfire=False)
    inv_item_width = load_int_array(cursor_src, "InvItemWidth")
    inv_item_height = load_int_array(cursor_src, "InvItemHeight")
    CURSOR_FIRSTITEM = 12  # first 12 entries are mouse-cursor graphics, not items

    def footprint(i_curs: int):
        idx = i_curs + CURSOR_FIRSTITEM
        if not (0 <= idx < len(inv_item_width)):
            return (1, 1)  # out of the tabulated range -- safe fallback, not a real answer
        w, h = inv_item_width[idx], inv_item_height[idx]
        assert w % 28 == 0 and h % 28 == 0, f"non-cell-aligned pixel size for i_curs={i_curs}: {w}x{h}"
        return w // 28, h // 28

    for row in all_items:
        row["width_cells"], row["height_cells"] = footprint(row["i_curs"])

    # Sanity checks against facts already verified by hand against real
    # save/source data this session -- catch drift on any future re-run.
    assert all_items[0]["i_name"] == "Gold"
    assert idx_to_enum_name.get(0) == "IDI_GOLD"
    assert idx_to_enum_name.get(28) == "IDI_ARMOFVAL"
    assert all_items[28]["i_name"] == "Arkaine's Valor"
    assert all_items[28]["i_misc_id"] == enums["IMISC_UNIQUE"]
    assert unique_items[7]["ui_name"] == "Arkaine's Valor"
    assert all_items[113]["i_name"] == "Dagger"
    assert (all_items[113]["width_cells"], all_items[113]["height_cells"]) == (1, 2), \
        "Dagger's footprint should be 1x2 -- confirmed against real InvGrid data in single_0.sv"
    assert all_items[136]["i_name"] == "Flail"
    assert (all_items[136]["width_cells"], all_items[136]["height_cells"]) == (2, 3), \
        "Flail's footprint should be 2x3 -- confirmed against real InvGrid data in single_0.sv"

    write_output(all_items, unique_items, idx_to_enum_name, enums)
    print(f"wrote {OUTPUT} ({len(all_items)} items, {len(unique_items) - 1} uniques)")


def write_output(all_items, unique_items, idx_to_enum_name, enums):
    lines = []
    lines.append('"""Item reference tables, generated from reference/itemdat.cpp')
    lines.append('and reference/enums.h by scripts/gen_itemdata.py -- do not hand-edit.')
    lines.append('Regenerate with: python scripts/gen_itemdata.py"""')
    lines.append("")
    lines.append("from dataclasses import dataclass")
    lines.append("from typing import Optional")
    lines.append("")
    lines.append("")
    lines.append("@dataclass(frozen=True)")
    lines.append("class ItemData:")
    lines.append("    idx: int")
    lines.append("    enum_name: Optional[str]  # e.g. 'IDI_GOLD'; None for unnamed regular-drop items")
    lines.append("    name: Optional[str]")
    lines.append("    short_name: Optional[str]")
    lines.append("    i_class: int       # ICLASS_*")
    lines.append("    i_loc: int         # ILOC_* -- matches raw ItemStruct._iLoc directly")
    lines.append("    i_curs: int        # ICURS_* -- matches raw ItemStruct._iCurs directly")
    lines.append("    i_type: int        # ITYPE_* -- matches raw ItemStruct._itype directly")
    lines.append("    unique_type: Optional[str]  # UITYPE_* symbol, or None (UITYPE_NONE)")
    lines.append("    min_level: int")
    lines.append("    durability: int")
    lines.append("    min_damage: int")
    lines.append("    max_damage: int")
    lines.append("    min_ac: int")
    lines.append("    max_ac: int")
    lines.append("    min_str: int")
    lines.append("    min_mag: int")
    lines.append("    min_dex: int")
    lines.append("    flags: int         # ISPL_* bitmask -- matches raw ItemStruct._iFlags")
    lines.append("    misc_id: int       # IMISC_* -- matches raw ItemStruct._iMiscId")
    lines.append("    spell: int         # SPL_*")
    lines.append("    usable: bool")
    lines.append("    value: int")
    lines.append("    max_value: int")
    lines.append("    width_cells: int   # real backpack-grid footprint, from reference/cursor.cpp's InvItemWidth[]")
    lines.append("    height_cells: int  # ...InvItemHeight[]; both /28 already applied")
    lines.append("")
    lines.append("")
    lines.append("@dataclass(frozen=True)")
    lines.append("class UniqueItemData:")
    lines.append("    position: int      # index into UNIQUE_ITEMS -- what a quest item's iSeed points to directly")
    lines.append("    name: str")
    lines.append("    item_type: str     # UITYPE_* symbol -- cross-reference to ItemData.unique_type")
    lines.append("    min_level: int")
    lines.append("    num_powers: int")
    lines.append("    value: int")
    lines.append("    powers: tuple       # ((power_symbol, param1, param2), ...) -- up to 6; game-engine-applied, not reimplemented here")
    lines.append("")
    lines.append("")

    def fmt_str_or_none(v):
        return "None" if v is None else repr(v)

    lines.append("ALL_ITEMS = [")
    for i, row in enumerate(all_items):
        lines.append(
            "    ItemData(idx={idx}, enum_name={enum_name}, name={name}, short_name={short}, "
            "i_class={i_class}, i_loc={i_loc}, i_curs={i_curs}, i_type={i_type}, "
            "unique_type={unique_type}, min_level={min_level}, durability={durability}, "
            "min_damage={min_dam}, max_damage={max_dam}, min_ac={min_ac}, max_ac={max_ac}, "
            "min_str={min_str}, min_mag={min_mag}, min_dex={min_dex}, flags={flags}, "
            "misc_id={misc_id}, spell={spell}, usable={usable}, value={value}, "
            "max_value={max_value}, width_cells={width_cells}, height_cells={height_cells}),".format(
                idx=i,
                enum_name=fmt_str_or_none(idx_to_enum_name.get(i)),
                name=fmt_str_or_none(row["i_name"]),
                short=fmt_str_or_none(row["i_sname"]),
                i_class=row["i_class"],
                i_loc=row["i_loc"],
                i_curs=row["i_curs"],
                i_type=row["i_type"],
                unique_type=fmt_str_or_none(None if row["i_item_id"] == "UITYPE_NONE" else row["i_item_id"]),
                min_level=row["i_min_mlvl"],
                durability=row["i_durability"],
                min_dam=row["i_min_dam"],
                max_dam=row["i_max_dam"],
                min_ac=row["i_min_ac"],
                max_ac=row["i_max_ac"],
                min_str=row["i_min_str"],
                min_mag=row["i_min_mag"],
                min_dex=row["i_min_dex"],
                flags=row["i_flags"],
                misc_id=row["i_misc_id"],
                spell=row["i_spell"],
                usable=row["i_usable"],
                value=row["i_value"],
                max_value=row["i_max_value"],
                width_cells=row["width_cells"],
                height_cells=row["height_cells"],
            )
        )
    lines.append("]")
    lines.append("")
    lines.append("UNIQUE_ITEMS = [")
    for i, row in enumerate(unique_items):
        powers = []
        for n in range(1, 7):
            power = row[f"ui_power{n}"]
            p_idx = {1: (1, 2), 2: (3, 4), 3: (5, 6), 4: (7, 8), 5: (9, 10), 6: (11, 12)}[n]
            p1 = row[f"ui_param{p_idx[0]}"]
            p2 = row[f"ui_param{p_idx[1]}"]
            powers.append((power, p1, p2))
        lines.append(
            "    UniqueItemData(position={pos}, name={name}, item_type={item_type}, "
            "min_level={min_level}, num_powers={num_powers}, value={value}, "
            "powers={powers!r}),".format(
                pos=i,
                name=repr(row["ui_name"]),
                item_type=repr(row["ui_item_id"]),
                min_level=row["ui_min_lvl"],
                num_powers=row["ui_num_pl"],
                value=row["ui_value"],
                powers=tuple(powers),
            )
        )
    lines.append("]")
    lines.append("")
    lines.append("# IDI_* name -> index into ALL_ITEMS, for the ~35 items with a symbolic name")
    lines.append("ITEM_INDEX_BY_NAME = {")
    for value, name in sorted(idx_to_enum_name.items()):
        lines.append(f"    {name!r}: {value},")
    lines.append("}")
    lines.append("")

    OUTPUT.write_text("\n".join(lines) + "\n")


if __name__ == "__main__":
    main()

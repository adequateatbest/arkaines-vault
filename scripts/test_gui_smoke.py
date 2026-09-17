"""Headless smoke test for the GUI layer: instantiate MainWindow (Qt's
offscreen platform plugin, no real display needed), open a real save,
verify the widgets render the right values, exercise the stat-edit
wiring and the save/backup path, confirm the paperdoll/backpack grid
reflect the character correctly, and drive the item picker dialog's
selection/accept behavior directly (not through MainWindow, since that
would need a live click sequence on a modal dialog -- but this is
exactly where a real bug slipped through before: nothing selected a row
after open/search, and double-click's signal/slot wiring was broken, so
both "search then click OK" and "double-click a row" silently did
nothing). The underlying Character mutations the dialogs call are
covered by scripts/test_character_roundtrip.py.

Usage: python scripts/test_gui_smoke.py path/to/save.sv
"""
import os
import sys
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from PySide6.QtWidgets import QApplication

from gui.item_picker import ItemPickerDialog
from gui.main_window import MainWindow


def main():
    save_path = sys.argv[1] if len(sys.argv) > 1 else "single_0.sv"
    out_path = str(Path(save_path).with_suffix(".gui_test.sv"))

    app = QApplication.instance() or QApplication(sys.argv)
    win = MainWindow()
    win._open_path(save_path)
    assert win.character is not None, "failed to open save"

    checks = []
    checks.append((win.stats_panel.name_edit.text() == win.character.name,
                    f"name field shows {win.stats_panel.name_edit.text()!r}"))
    checks.append((win.stats_panel.level_spin.value() == win.character.level,
                    f"level field shows {win.stats_panel.level_spin.value()}"))
    checks.append((win.stats_panel.gold_spin.value() == win.character.gold,
                    f"gold field shows {win.stats_panel.gold_spin.value()}"))
    hp, max_hp = win.character.hp_display()
    checks.append((win.stats_panel.hp_spin.value() == hp, "hp field matches"))

    from gui.widgets import _truncate

    equip = win.character.list_equipment()
    chest = next(e for e in equip if e['slot'] == 'CHEST')
    chest_btn = win.equipment_panel.buttons[6]  # CHEST is INVBODY_SLOTS[6]
    checks.append((chest_btn.text() == _truncate(chest['name'], 14), f"CHEST button shows {chest_btn.text()!r}"))
    checks.append((chest_btn.toolTip().endswith(chest['name'] + f" (idx {chest['idx']})"),
                    f"CHEST tooltip has the full name: {chest_btn.toolTip()!r}"))

    cells = win.character.backpack_grid_cells()
    occupied_count = sum(1 for v in cells if v is not None)
    grid_occupied = sum(1 for r in win.backpack_grid.buttons for b in r if b.styleSheet() != "background-color: #2b2b2b; color: #888;")
    checks.append((occupied_count == grid_occupied, f"backpack grid occupied cells: model={occupied_count} widget={grid_occupied}"))

    # --- item picker: search + OK with no manual row click, and double-click ---
    picker = ItemPickerDialog(show_footprint=True, allow_uniques=False, title="test")
    checks.append((picker.list_widget.currentItem() is not None, "picker auto-selects a row on open"))
    picker.search_box.setText("short sword")
    checks.append((picker.list_widget.currentItem() is not None, "picker auto-selects a row after search"))
    picker.accept()
    checks.append((picker.result is not None, f"OK after search with no manual click: {picker.result}"))

    picker2 = ItemPickerDialog(show_footprint=True, allow_uniques=False, title="test2")
    picker2.list_widget.itemDoubleClicked.emit(picker2.list_widget.item(0))
    checks.append((picker2.result is not None, f"double-click a row: {picker2.result}"))

    # --- footprint auto-fills from the item's real grid size (reference/
    # cursor.cpp) on selection, without the user touching W/H at all ---
    picker3 = ItemPickerDialog(show_footprint=True, allow_uniques=False, title="test3")
    picker3.search_box.setText("club")
    checks.append(((picker3.width_spin.value(), picker3.height_spin.value()) == (1, 3),
                    f"Club auto-fills to 1x3: got {(picker3.width_spin.value(), picker3.height_spin.value())}"))
    picker3.accept()
    checks.append((picker3.result[2:] == (1, 3), f"OK with no manual footprint edit uses 1x3: {picker3.result}"))

    # --- belt picker filters to scrolls/heal/mana/rejuv potions only,
    # and "show all items" lifts the filter ---
    from d1save.character import BELT_ALLOWED_MISC_IDS

    picker4 = ItemPickerDialog(allowed_misc_ids=BELT_ALLOWED_MISC_IDS, allow_uniques=False, title="belt test")
    picker4.search_box.setText("sword")
    checks.append((picker4.list_widget.count() == 0, "belt picker excludes swords by default"))
    picker4.show_all_checkbox.setChecked(True)
    checks.append((picker4.list_widget.count() > 0, "'show all items' lifts the belt restriction"))

    # --- drive the actual MainWindow click handlers end to end, not just
    # ItemPickerDialog in isolation -- this is exactly where the real bug
    # was (dialog.Accepted, an instance attribute that doesn't exist in
    # this PySide6 version, vs QDialog.Accepted, the class attribute).
    # Patch ItemPickerDialog.exec so it calls the dialog's own accept()
    # (exercising the real row-selection/accept logic) instead of opening
    # a blocking modal loop, then return what QDialog.exec() would.
    from PySide6.QtWidgets import QDialog, QInputDialog

    def fake_exec(self):
        self.accept()
        return QDialog.Accepted

    def make_search_fake_exec(term):
        def _fake(self):
            self.search_box.setText(term)
            self.accept()
            return QDialog.Accepted
        return _fake

    def make_unique_search_fake_exec(term):
        def _fake(self):
            self.uniques_checkbox.setChecked(True)  # uniques are hidden until this is checked
            self.search_box.setText(term)
            self.accept()
            return QDialog.Accepted
        return _fake

    # The backpack picker has no location filter, so it can auto-select
    # "Gold" (idx 0) first, which would otherwise open a real, blocking
    # QInputDialog.getInt() for the amount -- stub that out too.
    orig_exec = ItemPickerDialog.exec
    orig_get_int = QInputDialog.getInt
    ItemPickerDialog.exec = fake_exec
    QInputDialog.getInt = staticmethod(lambda *a, **kw: (100, True))
    try:
        ring_right = 2  # INVBODY_SLOTS.index('RING_RIGHT')
        win._on_equip_slot_clicked(ring_right)
        equip_after = next(e for e in win.character.list_equipment() if e['slot'] == 'RING_RIGHT')
        checks.append((not equip_after['empty'], f"equip via real handler: {equip_after}"))

        win._on_belt_slot_clicked(5)
        belt_after = win.character.list_belt()[5]
        checks.append((not belt_after['empty'], f"set belt slot via real handler: {belt_after}"))

        backpack_before = len(win.character.list_backpack())
        win._on_backpack_empty_clicked(0, 0)
        backpack_after = len(win.character.list_backpack())
        checks.append((backpack_after == backpack_before + 1,
                        f"add backpack item via real handler: {backpack_before} -> {backpack_after}"))

        # a unique (not just a plain item) must also be addable to the
        # backpack via the real handler -- this was the reported gap
        ItemPickerDialog.exec = make_unique_search_fake_exec("butcher")
        win._on_backpack_empty_clicked(1, 0)
        backpack_after_unique = win.character.list_backpack()
        checks.append((any(e['name'] == "The Butcher's Cleaver" for e in backpack_after_unique),
                        f"unique item added to backpack via real handler: {backpack_after_unique[-3:]}"))
    finally:
        ItemPickerDialog.exec = orig_exec
        QInputDialog.getInt = orig_get_int

    # --- two-handed hand-conflict: the confirm-and-override flow ---
    from PySide6.QtWidgets import QMessageBox

    orig_question = QMessageBox.question
    HAND_LEFT, HAND_RIGHT = 4, 5
    try:
        win.character.clear_equip_slot(HAND_LEFT)
        ItemPickerDialog.exec = make_search_fake_exec("buckler")
        win._on_equip_slot_clicked(HAND_RIGHT)
        checks.append((win.character.list_equipment()[HAND_RIGHT]['name'] == 'Buckler',
                        "set up Buckler in HAND_RIGHT for the two-handed conflict test"))

        ItemPickerDialog.exec = make_search_fake_exec("short bow")
        QMessageBox.question = staticmethod(lambda *a, **kw: QMessageBox.No)
        win._on_equip_slot_clicked(HAND_LEFT)  # 2H bow conflicts with the Buckler; decline
        checks.append((win.character.list_equipment()[HAND_LEFT]['empty'],
                        "declining the two-handed conflict prompt leaves HAND_LEFT untouched"))

        QMessageBox.question = staticmethod(lambda *a, **kw: QMessageBox.Yes)
        win._on_equip_slot_clicked(HAND_LEFT)  # retry, confirm the override
        checks.append((win.character.list_equipment()[HAND_LEFT].get('name') == 'Short Bow',
                        "confirming the prompt equips the two-handed weapon anyway"))
    finally:
        ItemPickerDialog.exec = orig_exec
        QMessageBox.question = orig_question

    # --- exercise the stat-apply wiring the same way a real edit would ---
    win._on_stat_applied('gold', 42424242)
    checks.append((win.character.gold == 42424242, f"gold after apply: {win.character.gold}"))
    checks.append((win.dirty is True, "dirty flag set after edit"))
    checks.append((win.stats_panel.gold_spin.value() == 42424242, "gold field re-rendered after apply"))

    # --- exercise the save path directly (not win._save(), which always
    # opens a real native file dialog now -- that would block even under
    # offscreen) ---
    win.character.save(save_path, out_path)
    checks.append((os.path.exists(out_path), "save() wrote the output file"))

    # --- _save() must never suggest the file we opened as the default path ---
    win.current_path = save_path
    suggested = win._suggested_save_path()
    checks.append((os.path.abspath(suggested) != os.path.abspath(save_path),
                    f"suggested save path differs from the opened file: {suggested!r}"))

    from d1save.character import Character
    reloaded = Character.load(out_path)
    checks.append((reloaded.gold == 42424242, f"reloaded gold: {reloaded.gold}"))

    # --- if the user deliberately re-picks the exact file they opened in
    # the save dialog, _save() still backs up the original first ---
    from PySide6.QtWidgets import QFileDialog

    overwrite_test_path = str(Path(save_path).with_suffix(".overwrite_test.sv"))
    Path(overwrite_test_path).write_bytes(Path(save_path).read_bytes())
    bak_path = overwrite_test_path + ".bak"
    orig_get_save_name = QFileDialog.getSaveFileName
    try:
        win2 = MainWindow()
        win2._open_path(overwrite_test_path)
        original_gold = win2.character.gold
        QFileDialog.getSaveFileName = staticmethod(lambda *a, **kw: (overwrite_test_path, ''))
        win2.character.gold = original_gold + 1
        win2._save()
        checks.append((os.path.exists(bak_path), "deliberately re-picking the opened file creates a .bak"))
        backup_char = Character.load(bak_path)
        checks.append((backup_char.gold == original_gold, f"backup preserves the pre-edit gold: {backup_char.gold}"))
        current_char = Character.load(overwrite_test_path)
        checks.append((current_char.gold == original_gold + 1, f"the file itself has the edit: {current_char.gold}"))
    finally:
        QFileDialog.getSaveFileName = orig_get_save_name
        Path(overwrite_test_path).unlink(missing_ok=True)
        Path(bak_path).unlink(missing_ok=True)

    all_ok = True
    for ok, msg in checks:
        print(("  OK: " if ok else "  FAIL: ") + msg)
        all_ok = all_ok and ok

    Path(out_path).unlink(missing_ok=True)
    if not all_ok:
        raise SystemExit("some GUI smoke checks failed")
    print("\nAll GUI smoke checks passed.")


if __name__ == "__main__":
    main()

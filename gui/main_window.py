"""Main window: owns the Character, wires the paperdoll/belt/backpack/
stats widgets to it, and handles Open/Save/Save As.

Save never writes back to the file you opened silently: "Save" and
"Save As..." both always open a save-location dialog, defaulting to a
sibling filename (`<name>_edited.sv`) rather than the original -- see
_save(). Deliberately re-picking the original path in that dialog still
works, but gets backed up to `<path>.bak` first.

Rendering model: every widget is "dumb" and re-rendered from scratch via
load_from(character) after any edit. The app is far too small for that
to matter performance-wise, and it avoids an entire class of
this-widget-is-now-stale bugs.
"""
import os
import shutil
from pathlib import Path
from typing import Optional

from PySide6.QtGui import QAction, QKeySequence
from PySide6.QtWidgets import (
    QDialog, QFileDialog, QHBoxLayout, QInputDialog, QMainWindow, QMessageBox,
    QVBoxLayout, QWidget,
)

from d1save import itemdata
from d1save.character import BELT_ALLOWED_MISC_IDS, Character, EQUIP_SLOT_ILOC, HandConflictError
from d1save.pkplayer import INVBODY_SLOTS

from .item_picker import ItemPickerDialog
from .widgets import BackpackGrid, BeltPanel, EquipmentPanel, StatsPanel


class MainWindow(QMainWindow):
    def __init__(self, initial_path: Optional[str] = None):
        super().__init__()
        self.setWindowTitle("Diablo 1 Save Editor")
        self.resize(780, 680)

        self.character: Optional[Character] = None
        self.current_path: Optional[str] = None
        self.dirty = False
        self._backed_up_paths = set()

        self._build_menu()

        central = QWidget()
        self.setCentralWidget(central)
        outer = QVBoxLayout(central)

        top_row = QHBoxLayout()
        self.stats_panel = StatsPanel(self._on_stat_applied)
        self.equipment_panel = EquipmentPanel()
        self.equipment_panel.slot_clicked.connect(self._on_equip_slot_clicked)
        top_row.addWidget(self.stats_panel, 1)
        top_row.addWidget(self.equipment_panel, 1)
        outer.addLayout(top_row)

        self.belt_panel = BeltPanel()
        self.belt_panel.slot_clicked.connect(self._on_belt_slot_clicked)
        outer.addWidget(self.belt_panel)

        self.backpack_grid = BackpackGrid()
        self.backpack_grid.cell_clicked.connect(self._on_backpack_slot_clicked)
        self.backpack_grid.empty_cell_clicked.connect(self._on_backpack_empty_clicked)
        outer.addWidget(self.backpack_grid)

        self.statusBar().showMessage("No save file loaded -- File > Open")
        self._set_widgets_enabled(False)

        if initial_path:
            self._open_path(initial_path)

    def _set_widgets_enabled(self, enabled: bool):
        self.stats_panel.setEnabled(enabled)
        self.equipment_panel.setEnabled(enabled)
        self.belt_panel.setEnabled(enabled)
        self.backpack_grid.setEnabled(enabled)

    def _build_menu(self):
        menu = self.menuBar().addMenu("&File")

        open_action = QAction("&Open...", self)
        open_action.setShortcut(QKeySequence.Open)
        open_action.triggered.connect(self._open_dialog)
        menu.addAction(open_action)

        # Both "Save" and "Save As..." always open a save-location dialog --
        # see _save()'s docstring for why there's no direct-overwrite path.
        save_action = QAction("&Save...", self)
        save_action.setShortcut(QKeySequence.Save)
        save_action.triggered.connect(self._save)
        menu.addAction(save_action)

        save_as_action = QAction("Save &As...", self)
        save_as_action.setShortcut(QKeySequence.SaveAs)
        save_as_action.triggered.connect(self._save)
        menu.addAction(save_as_action)

        menu.addSeparator()
        exit_action = QAction("E&xit", self)
        exit_action.triggered.connect(self.close)
        menu.addAction(exit_action)

    # --- open/save ---
    def _open_dialog(self):
        path, _ = QFileDialog.getOpenFileName(self, "Open Diablo 1 save", "", "Diablo 1 saves (*.sv);;All files (*)")
        if path:
            self._open_path(path)

    def _open_path(self, path: str):
        try:
            character = Character.load(path)
        except Exception as e:
            QMessageBox.critical(self, "Failed to open", str(e))
            return
        self.character = character
        self.current_path = path
        self.dirty = False
        self._set_widgets_enabled(True)
        self._refresh()

    def _backup_if_needed(self, path: str):
        """Copy the on-disk file to `<path>.bak` before the first write
        back to that exact path this session, if a backup doesn't
        already exist. This only ever triggers if the user deliberately
        re-picks the original file in the save dialog -- see _save()."""
        if path in self._backed_up_paths:
            return
        bak_path = path + '.bak'
        if not os.path.exists(bak_path):
            shutil.copy2(path, bak_path)
        self._backed_up_paths.add(path)

    def _suggested_save_path(self) -> str:
        """A sibling filename that is NOT the file we opened, so the save
        dialog's default suggestion never points at the original."""
        if not self.current_path:
            return ""
        p = Path(self.current_path)
        return str(p.with_name(f"{p.stem}_edited{p.suffix}"))

    def _save(self):
        """Always prompts for a destination -- this app never silently
        overwrites the save you opened. If you deliberately browse back
        to that exact path anyway, we back up the original first."""
        if self.character is None:
            return
        path, _ = QFileDialog.getSaveFileName(
            self, "Save Diablo 1 save as", self._suggested_save_path(),
            "Diablo 1 saves (*.sv);;All files (*)",
        )
        if not path:
            return
        if self.current_path and os.path.abspath(path) == os.path.abspath(self.current_path):
            self._backup_if_needed(self.current_path)
        try:
            self.character.save(self.current_path, path)
        except Exception as e:
            QMessageBox.critical(self, "Save failed", str(e))
            return
        self.current_path = path
        self.dirty = False
        self._update_title()
        self.statusBar().showMessage(f"Saved to {path}", 5000)

    def closeEvent(self, event):
        if not self.dirty:
            event.accept()
            return
        reply = QMessageBox.question(
            self, "Unsaved changes", "You have unsaved changes. Save before closing?",
            QMessageBox.Yes | QMessageBox.No | QMessageBox.Cancel,
        )
        if reply == QMessageBox.Cancel:
            event.ignore()
            return
        if reply == QMessageBox.Yes:
            self._save()
        event.accept()

    # --- rendering ---
    def _refresh(self):
        self.stats_panel.load_from(self.character)
        self.equipment_panel.load_from(self.character)
        self.belt_panel.load_from(self.character)
        self.backpack_grid.load_from(self.character)
        self._update_title()

    def _update_title(self):
        name = self.current_path or "(no file)"
        star = "*" if self.dirty else ""
        self.setWindowTitle(f"Diablo 1 Save Editor - {name}{star}")
        if self.character:
            game_note = "hero+game" if self.character.has_game_file else "hero only"
            unsaved = " (unsaved changes)" if self.dirty else ""
            self.statusBar().showMessage(f"{self.character.name} -- {game_note}{unsaved}")

    def _mark_dirty(self):
        self.dirty = True
        self._update_title()

    # --- stat edits ---
    def _current_stat_value(self, field_name: str):
        if field_name == 'name':
            return self.character.name
        if field_name == 'level':
            return self.character.level
        if field_name == 'experience':
            return self.character.experience
        if field_name == 'gold':
            return self.character.gold
        if field_name == 'stat_points':
            return self.character.stat_points
        if field_name in ('str', 'mag', 'dex', 'vit'):
            return self.character.attributes[field_name]
        if field_name == 'hp':
            return self.character.hp_display()[0]
        if field_name == 'max_hp':
            return self.character.hp_display()[1]
        if field_name == 'mana':
            return self.character.mana_display()[0]
        if field_name == 'max_mana':
            return self.character.mana_display()[1]
        raise ValueError(f"unknown stat field {field_name!r}")

    def _on_stat_applied(self, field_name: str, value):
        # Editing a field without changing its value (e.g. clicking in and
        # back out) shouldn't mark the character dirty or touch anything.
        if self._current_stat_value(field_name) == value:
            return

        try:
            if field_name == 'name':
                self.character.name = value
            elif field_name == 'level':
                self.character.level = value
            elif field_name == 'experience':
                self.character.experience = value
            elif field_name == 'gold':
                self.character.gold = value
            elif field_name == 'stat_points':
                self.character.stat_points = value
            elif field_name in ('str', 'mag', 'dex', 'vit'):
                kwarg = 'str_' if field_name == 'str' else field_name
                self.character.set_attributes(**{kwarg: value})
            elif field_name == 'hp':
                _, max_hp = self.character.hp_display()
                self.character.set_hp(value, max_hp)
            elif field_name == 'max_hp':
                hp, _ = self.character.hp_display()
                self.character.set_hp(hp, value)
            elif field_name == 'mana':
                _, max_mana = self.character.mana_display()
                self.character.set_mana(value, max_mana)
            elif field_name == 'max_mana':
                mana, _ = self.character.mana_display()
                self.character.set_mana(mana, value)
        except ValueError as e:
            QMessageBox.warning(self, "Invalid value", str(e))
            self._refresh()  # revert the field to the last valid value
            return

        self._mark_dirty()
        self._refresh()  # reflect any clamping back into the fields

    # --- equipment ---
    def _on_equip_slot_clicked(self, slot_idx: int):
        entry = next(e for e in self.character.list_equipment() if e['slot'] == INVBODY_SLOTS[slot_idx])
        dialog = ItemPickerDialog(
            self, allowed_locs=EQUIP_SLOT_ILOC.get(slot_idx), allow_clear=not entry['empty'],
            title=f"Equip: {INVBODY_SLOTS[slot_idx]}",
        )
        if dialog.exec() != QDialog.Accepted or dialog.result is None:
            return
        kind, idx, _, _ = dialog.result
        # the dialog's own "show items for any slot" checkbox is the
        # user's confirmation to override the slot/i_loc mismatch, but
        # NOT the separate two-handed hand-conflict check below -- that
        # one gets its own confirm prompt so it's never silently skipped.
        for force_hands in (False, True):
            try:
                if kind == 'clear':
                    self.character.clear_equip_slot(slot_idx)
                elif kind == 'plain':
                    self.character.equip_plain(slot_idx, idx, force=True, force_hands=force_hands)
                elif kind == 'unique':
                    self.character.equip_unique(slot_idx, idx, force=True, force_hands=force_hands)
                break
            except HandConflictError as e:
                if force_hands:
                    QMessageBox.warning(self, "Couldn't equip item", str(e))
                    return
                reply = QMessageBox.question(
                    self, "Two-handed conflict", f"{e}\n\nEquip anyway?",
                    QMessageBox.Yes | QMessageBox.No,
                )
                if reply != QMessageBox.Yes:
                    return
                continue
            except Exception as e:
                QMessageBox.warning(self, "Couldn't equip item", str(e))
                return
        self._mark_dirty()
        self._refresh()

    # --- belt ---
    def _on_belt_slot_clicked(self, slot_idx: int):
        entry = self.character.list_belt()[slot_idx]
        dialog = ItemPickerDialog(
            self, allowed_misc_ids=BELT_ALLOWED_MISC_IDS, allow_uniques=False,
            allow_clear=not entry['empty'], title=f"Belt slot {slot_idx}",
        )
        if dialog.exec() != QDialog.Accepted or dialog.result is None:
            return
        kind, idx, _, _ = dialog.result
        try:
            if kind == 'clear':
                self.character.clear_belt_slot(slot_idx)
            else:
                # the dialog's own "show all items" checkbox is the user's
                # confirmation to override the belt-item-type restriction
                self.character.set_belt_slot(slot_idx, idx, force=True)
        except Exception as e:
            QMessageBox.warning(self, "Couldn't set belt slot", str(e))
            return
        self._mark_dirty()
        self._refresh()

    # --- backpack ---
    def _on_backpack_slot_clicked(self, slot_idx: int):
        entry = next((e for e in self.character.list_backpack() if e['slot'] == slot_idx), None)
        name = entry['name'] if entry else '?'
        reply = QMessageBox.question(self, "Remove item", f"Remove {name!r} from the backpack?",
                                      QMessageBox.Yes | QMessageBox.No)
        if reply != QMessageBox.Yes:
            return
        self.character.remove_from_backpack(slot_idx)
        self._mark_dirty()
        self._refresh()

    def _on_backpack_empty_clicked(self, row: int, col: int):
        dialog = ItemPickerDialog(self, show_footprint=True, allow_uniques=True, title="Add item to backpack")
        if dialog.exec() != QDialog.Accepted or dialog.result is None:
            return
        kind, idx, width, height = dialog.result
        gold_idx = itemdata.ITEM_INDEX_BY_NAME.get('IDI_GOLD')
        try:
            if kind == 'unique':
                self.character.add_unique_to_backpack(idx, width, height)
            elif idx == gold_idx:
                amount, ok = QInputDialog.getInt(self, "Gold amount", "Amount (max 5000):", 100, 1, 5000)
                if not ok:
                    return
                self.character.add_gold_to_backpack(amount)
            else:
                self.character.add_plain_item_to_backpack(idx, width, height)
        except Exception as e:
            QMessageBox.warning(self, "Couldn't add item", str(e))
            return
        self._mark_dirty()
        self._refresh()

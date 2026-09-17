"""Item picker dialog: search the item reference tables and pick either
a plain base item (with an optional grid footprint, for backpack
placement) or a named unique item (intrinsic or random-roll).
"""
from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QCheckBox, QDialog, QDialogButtonBox, QHBoxLayout, QLabel, QLineEdit,
    QListWidget, QListWidgetItem, QSpinBox, QVBoxLayout,
)

from d1save import itemdata
from d1save.character import find_base_idx_for_unique

ILOC_NAMES = {
    0: 'None', 1: 'One-Hand', 2: 'Two-Hand', 3: 'Armor', 4: 'Helm',
    5: 'Ring', 6: 'Amulet', 7: 'Unequipable', 8: 'Belt', -1: 'Invalid',
}


class ItemPickerDialog(QDialog):
    """After exec() == QDialog.Accepted, `result` holds
    (kind, idx_or_position, width, height) where kind is 'plain' or
    'unique'. width/height are only meaningful when show_footprint=True.
    """

    def __init__(self, parent=None, allowed_locs=None, allowed_misc_ids=None, show_footprint=False,
                 allow_uniques=True, allow_clear=False, title="Pick an item"):
        super().__init__(parent)
        self.setWindowTitle(title)
        self.resize(480, 520)
        self.allowed_locs = allowed_locs            # set of ILOC ints, or None for no filter
        self.allowed_misc_ids = allowed_misc_ids    # set of IMISC ints, or None for no filter (e.g. belt)
        self.show_footprint = show_footprint
        self.result = None

        layout = QVBoxLayout(self)

        search_row = QHBoxLayout()
        search_row.addWidget(QLabel("Search:"))
        self.search_box = QLineEdit()
        self.search_box.setPlaceholderText("item name...")
        self.search_box.textChanged.connect(self._refresh)
        search_row.addWidget(self.search_box)
        layout.addLayout(search_row)

        options_row = QHBoxLayout()
        if allow_uniques:
            self.uniques_checkbox = QCheckBox("Unique items")
            self.uniques_checkbox.toggled.connect(self._refresh)
            options_row.addWidget(self.uniques_checkbox)
        else:
            self.uniques_checkbox = None
        self.show_all_checkbox = None
        if allowed_locs is not None or allowed_misc_ids is not None:
            self.show_all_checkbox = QCheckBox("Show all items (ignore restrictions)")
            self.show_all_checkbox.toggled.connect(self._refresh)
            options_row.addWidget(self.show_all_checkbox)
        options_row.addStretch(1)
        layout.addLayout(options_row)

        self.list_widget = QListWidget()
        # itemDoubleClicked passes the clicked item; accept() takes no args,
        # so connecting them directly means Qt silently drops the (broken)
        # call -- route through a lambda that discards the argument.
        self.list_widget.itemDoubleClicked.connect(lambda _item: self.accept())
        layout.addWidget(self.list_widget)

        if show_footprint:
            fp_row = QHBoxLayout()
            fp_row.addWidget(QLabel("Backpack footprint:"))
            fp_row.addWidget(QLabel("W"))
            self.width_spin = QSpinBox()
            self.width_spin.setRange(1, 10)
            self.width_spin.setValue(1)
            fp_row.addWidget(self.width_spin)
            fp_row.addWidget(QLabel("H"))
            self.height_spin = QSpinBox()
            self.height_spin.setRange(1, 4)
            self.height_spin.setValue(1)
            fp_row.addWidget(self.height_spin)
            fp_row.addStretch(1)
            layout.addLayout(fp_row)
            # Auto-fill from the item's real grid size on every selection
            # change (including the auto-selected first row after open/
            # search) so OK "just works" without the user having to notice
            # and manually set W/H -- they're still editable to override.
            self.list_widget.currentItemChanged.connect(self._sync_footprint_to_selection)
        else:
            self.width_spin = self.height_spin = None

        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        if allow_clear:
            clear_btn = buttons.addButton("Clear Slot", QDialogButtonBox.DestructiveRole)
            clear_btn.clicked.connect(self._clear)
        layout.addWidget(buttons)

        self._refresh()

    def _sync_footprint_to_selection(self, current, _previous=None):
        if current is None:
            return
        kind, idx = current.data(Qt.UserRole)
        if kind == 'plain':
            item = itemdata.ALL_ITEMS[idx]
        elif kind == 'unique':
            # a unique's footprint is its base item's -- e.g. The Butcher's
            # Cleaver is carried on the plain "Cleaver" base item
            item = itemdata.ALL_ITEMS[find_base_idx_for_unique(itemdata.UNIQUE_ITEMS[idx])]
        else:
            return
        self.width_spin.setValue(item.width_cells)
        self.height_spin.setValue(item.height_cells)

    def _clear(self):
        self.result = ('clear', None, 1, 1)
        super().accept()

    def _showing_uniques(self) -> bool:
        return bool(self.uniques_checkbox and self.uniques_checkbox.isChecked())

    def _refresh(self):
        self.list_widget.clear()
        query = self.search_box.text().strip().lower()
        show_all = bool(self.show_all_checkbox and self.show_all_checkbox.isChecked())
        locs = None if show_all else self.allowed_locs
        misc_ids = None if show_all else self.allowed_misc_ids

        if self._showing_uniques():
            for i, u in enumerate(itemdata.UNIQUE_ITEMS):
                if not u.name:
                    continue
                if query and query not in u.name.lower():
                    continue
                entry = QListWidgetItem(f"{u.name}  (on {u.item_type}, min lvl {u.min_level})")
                entry.setData(Qt.UserRole, ('unique', i))
                self.list_widget.addItem(entry)
        else:
            gold_idx = itemdata.ITEM_INDEX_BY_NAME.get('IDI_GOLD')
            for i, it in enumerate(itemdata.ALL_ITEMS):
                if not it.name:
                    continue
                if i == gold_idx and not self.show_footprint:
                    # Gold isn't a real equip/belt item -- it only makes sense
                    # via the backpack picker's amount-prompt special case.
                    continue
                if query and query not in it.name.lower():
                    continue
                if locs is not None and it.i_loc not in locs:
                    continue
                if misc_ids is not None and it.misc_id not in misc_ids:
                    continue
                loc_name = ILOC_NAMES.get(it.i_loc, str(it.i_loc))
                entry = QListWidgetItem(f"[{i}] {it.name}  -- {loc_name}, lvl {it.min_level}")
                entry.setData(Qt.UserRole, ('plain', i))
                self.list_widget.addItem(entry)

        # Auto-select the top match so typing a search term and hitting OK
        # (or Enter) acts on it without an extra click -- QListWidget
        # doesn't select anything on its own after a clear()+repopulate.
        if self.list_widget.count() > 0:
            self.list_widget.setCurrentRow(0)

    def accept(self):
        current = self.list_widget.currentItem()
        if current is None:
            return  # nothing selected -- ignore OK/double-click
        kind, idx = current.data(Qt.UserRole)
        width = self.width_spin.value() if self.width_spin else 1
        height = self.height_spin.value() if self.height_spin else 1
        self.result = (kind, idx, width, height)
        super().accept()

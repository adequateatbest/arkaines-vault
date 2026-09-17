"""Reusable widgets for the main window: the equipment paperdoll, belt
row, backpack grid, and the character stats panel. Each widget is
"dumb" -- it renders whatever Character state it's given via load_from()
and emits a Qt signal when the user clicks something; MainWindow owns
the Character and decides what to do (open a picker, apply an edit,
refresh everything).
"""
from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
    QFormLayout, QGridLayout, QGroupBox, QLineEdit, QPushButton, QSizePolicy,
    QSpinBox, QVBoxLayout, QWidget,
)

from d1save.pkplayer import INVBODY_SLOTS

EQUIP_SLOT_LABELS = {
    'HEAD': 'Head', 'RING_LEFT': 'Ring (L)', 'RING_RIGHT': 'Ring (R)',
    'AMULET': 'Amulet', 'HAND_LEFT': 'Hand (L)', 'HAND_RIGHT': 'Hand (R)',
    'CHEST': 'Chest',
}
CELL_EMPTY_STYLE = "background-color: #2b2b2b; color: #888;"
CELL_FILLED_STYLE = "background-color: #3a5a78; color: white; font-weight: bold;"


def _truncate(name: str, max_len: int) -> str:
    """Qt's default button rendering clips overflowing text from the
    middle (centered text just gets its edges cut off), which for a
    long item name produces a confusing fragment like 'e Staff of the
    m'. Truncating ourselves keeps at least the start of the name and
    makes it visually obvious that it's cut off."""
    if len(name) <= max_len:
        return name
    return name[:max_len - 1].rstrip() + "…"


class SlotButton(QPushButton):
    def __init__(self, label: str, fixed_size=(90, 44)):
        super().__init__(label)
        self.setFixedSize(*fixed_size)
        self.setStyleSheet(CELL_EMPTY_STYLE)

    def set_content(self, text: str, filled: bool, max_len: int = None):
        self.setText(_truncate(text, max_len) if max_len else text)
        self.setStyleSheet(CELL_FILLED_STYLE if filled else CELL_EMPTY_STYLE)


class EquipmentPanel(QGroupBox):
    slot_clicked = Signal(int)  # index into INVBODY_SLOTS

    def __init__(self):
        super().__init__("Equipment")
        grid = QGridLayout(self)
        self.buttons = {}
        # a schematic paperdoll layout, not a literal body outline (no sprite assets available)
        positions = {
            'HEAD': (0, 1), 'AMULET': (0, 2),
            'RING_LEFT': (1, 0), 'HAND_LEFT': (1, 1), 'CHEST': (1, 2), 'HAND_RIGHT': (1, 3), 'RING_RIGHT': (1, 4),
        }
        for slot_name, (row, col) in positions.items():
            slot_idx = INVBODY_SLOTS.index(slot_name)
            btn = SlotButton(EQUIP_SLOT_LABELS[slot_name], fixed_size=(115, 48))
            btn.clicked.connect(lambda checked=False, i=slot_idx: self.slot_clicked.emit(i))
            grid.addWidget(btn, row, col)
            self.buttons[slot_idx] = btn

    def load_from(self, character):
        entries = {e['slot']: e for e in character.list_equipment()}
        for slot_idx, btn in self.buttons.items():
            slot_name = INVBODY_SLOTS[slot_idx]
            entry = entries[slot_name]
            label = EQUIP_SLOT_LABELS[slot_name]
            if entry['empty']:
                btn.set_content(label, filled=False)
                btn.setToolTip(f"{label}: empty")
            else:
                btn.set_content(entry['name'], filled=True, max_len=14)
                btn.setToolTip(f"{label}: {entry['name']} (idx {entry['idx']})")


class BeltPanel(QGroupBox):
    slot_clicked = Signal(int)  # 0-based belt slot

    def __init__(self):
        super().__init__("Belt")
        layout = QGridLayout(self)
        self.buttons = []
        for i in range(8):
            btn = SlotButton(str(i), fixed_size=(70, 40))
            btn.clicked.connect(lambda checked=False, s=i: self.slot_clicked.emit(s))
            layout.addWidget(btn, 0, i)
            self.buttons.append(btn)

    def load_from(self, character):
        for entry in character.list_belt():
            btn = self.buttons[entry['slot']]
            if entry['empty']:
                btn.set_content(str(entry['slot']), filled=False)
                btn.setToolTip("empty")
            else:
                btn.set_content(entry['name'], filled=True, max_len=9)
                btn.setToolTip(f"{entry['name']} (idx {entry['idx']})")


class BackpackGrid(QGroupBox):
    cell_clicked = Signal(int)  # InvList slot index, or -1 for an empty cell click plus (row, col)
    empty_cell_clicked = Signal(int, int)  # (row, col) of a clicked empty cell

    ROWS, COLS = 4, 10

    def __init__(self):
        super().__init__("Backpack")
        layout = QGridLayout(self)
        layout.setSpacing(2)
        self.buttons = []
        for r in range(self.ROWS):
            row_buttons = []
            for c in range(self.COLS):
                btn = SlotButton("", fixed_size=(46, 46))
                btn.clicked.connect(lambda checked=False, row=r, col=c: self._on_click(row, col))
                layout.addWidget(btn, r, c)
                row_buttons.append(btn)
            self.buttons.append(row_buttons)
        self._occupant = [[None] * self.COLS for _ in range(self.ROWS)]

    def _on_click(self, row, col):
        slot = self._occupant[row][col]
        if slot is None:
            self.empty_cell_clicked.emit(row, col)
        else:
            self.cell_clicked.emit(slot)

    def load_from(self, character):
        cells = character.backpack_grid_cells()
        primary = character.backpack_grid_primary_cells()
        by_slot = {e['slot']: e['name'] for e in character.list_backpack()}
        for r in range(self.ROWS):
            for c in range(self.COLS):
                i = r * self.COLS + c
                slot = cells[i]
                self._occupant[r][c] = slot
                btn = self.buttons[r][c]
                if slot is None:
                    btn.set_content("", filled=False)
                    btn.setToolTip("empty")
                else:
                    name = by_slot.get(slot, '?')
                    label = name if primary[i] is not None else ""
                    btn.set_content(label, filled=True, max_len=7)
                    btn.setToolTip(f"{name} (backpack slot {slot})")


class StatsPanel(QGroupBox):
    """Live-editable stat fields. Calls back into `on_apply(changes)`
    with a dict of only the fields the user actually changed, on
    editingFinished (focus-out or Enter) rather than every keystroke."""

    def __init__(self, on_apply):
        super().__init__("Character")
        self._on_apply = on_apply
        self._loading = False  # suppress on_apply while load_from() sets values

        form = QFormLayout(self)

        self.name_edit = QLineEdit()
        self.name_edit.editingFinished.connect(lambda: self._apply('name', self.name_edit.text()))
        form.addRow("Name", self.name_edit)

        self.level_spin = self._make_spin(form, "Level", 1, 127, 'level')
        self.experience_spin = self._make_spin(form, "Experience", 0, 2_000_000_000, 'experience')
        self.gold_spin = self._make_spin(form, "Gold", 0, 2_000_000_000, 'gold')
        self.stat_points_spin = self._make_spin(form, "Unspent stat points", 0, 255, 'stat_points')

        self.str_spin = self._make_spin(form, "Strength", 0, 255, 'str')
        self.mag_spin = self._make_spin(form, "Magic", 0, 255, 'mag')
        self.dex_spin = self._make_spin(form, "Dexterity", 0, 255, 'dex')
        self.vit_spin = self._make_spin(form, "Vitality", 0, 255, 'vit')

        self.hp_spin = self._make_spin(form, "HP (current)", 0, 999_999, 'hp')
        self.max_hp_spin = self._make_spin(form, "HP (max)", 0, 999_999, 'max_hp')
        self.mana_spin = self._make_spin(form, "Mana (current)", 0, 999_999, 'mana')
        self.max_mana_spin = self._make_spin(form, "Mana (max)", 0, 999_999, 'max_mana')

        self.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Maximum)

    def _make_spin(self, form, label, lo, hi, field_name):
        spin = QSpinBox()
        spin.setRange(lo, hi)
        spin.editingFinished.connect(lambda: self._apply(field_name, spin.value()))
        form.addRow(label, spin)
        return spin

    def _apply(self, field_name, value):
        if self._loading:
            return
        self._on_apply(field_name, value)

    def load_from(self, character):
        self._loading = True
        try:
            self.name_edit.setText(character.name)
            self.level_spin.setValue(character.level)
            self.experience_spin.setValue(character.experience)
            self.gold_spin.setValue(character.gold)
            self.stat_points_spin.setValue(character.stat_points)
            attrs = character.attributes
            self.str_spin.setValue(attrs['str'])
            self.mag_spin.setValue(attrs['mag'])
            self.dex_spin.setValue(attrs['dex'])
            self.vit_spin.setValue(attrs['vit'])
            hp, max_hp = character.hp_display()
            mana, max_mana = character.mana_display()
            self.hp_spin.setValue(hp)
            self.max_hp_spin.setValue(max_hp)
            self.mana_spin.setValue(mana)
            self.max_mana_spin.setValue(max_mana)
        finally:
            self._loading = False

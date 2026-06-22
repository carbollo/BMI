"""Asistente interactivo de instalación FOMOD."""
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QStackedWidget,
    QWidget, QScrollArea, QGroupBox, QRadioButton, QCheckBox, QButtonGroup,
    QMessageBox, QTextEdit, QSizePolicy,
)

from ..fomod import FomodConfig, FomodGroup, FomodPlugin, auto_pick_group
from ..i18n import tr


_GROUP_HINT = {
    "SelectExactlyOne": "Elige exactamente una",
    "SelectAtMostOne": "Elige como máximo una",
    "SelectAtLeastOne": "Elige al menos una",
    "SelectAny": "Elige las que quieras",
    "SelectAll": "Todas obligatorias",
}


class FomodDialog(QDialog):
    """Presenta los pasos del FOMOD. Tras Aceptar, get_selection() da las opciones."""

    def __init__(self, config: FomodConfig, parent=None):
        super().__init__(parent)
        self.config = config
        self.setWindowTitle(tr("Instalar") + f": {config.module_name}")
        self.resize(900, 640)

        # step_idx -> [(group, [(plugin, widget), ...]), ...]
        self._step_data: dict[int, list[tuple[FomodGroup, list[tuple[FomodPlugin, QWidget]]]]] = {}
        self._pages: list[QWidget] = []
        self._visited: list[int] = []
        self._selection: list[FomodPlugin] = []

        # --- Layout principal ---
        root = QHBoxLayout(self)

        # Columna izquierda: pasos
        left = QVBoxLayout()
        self.title_lbl = QLabel()
        self.title_lbl.setStyleSheet("font-size:16px; font-weight:bold;")
        self.stack = QStackedWidget()
        left.addWidget(self.title_lbl)
        left.addWidget(self.stack, 1)

        nav = QHBoxLayout()
        self.back_btn = QPushButton(tr("← Atrás"))
        self.back_btn.clicked.connect(self._go_back)
        self.next_btn = QPushButton(tr("Siguiente →"))
        self.next_btn.clicked.connect(self._go_next)
        self.install_btn = QPushButton(tr("Instalar"))
        self.install_btn.clicked.connect(self._on_install)
        self.cancel_btn = QPushButton(tr("Cancelar"))
        self.cancel_btn.clicked.connect(self.reject)
        nav.addWidget(self.cancel_btn)
        nav.addStretch()
        nav.addWidget(self.back_btn)
        nav.addWidget(self.next_btn)
        nav.addWidget(self.install_btn)
        left.addLayout(nav)

        left_w = QWidget(); left_w.setLayout(left)

        # Columna derecha: descripción + imagen
        right = QVBoxLayout()
        right.addWidget(QLabel("<b>" + tr("Descripción") + "</b>"))
        self.desc = QTextEdit()
        self.desc.setReadOnly(True)
        self.desc.setMinimumWidth(300)
        self.image_lbl = QLabel()
        self.image_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.image_lbl.setMinimumHeight(220)
        right.addWidget(self.desc, 1)
        right.addWidget(self.image_lbl, 1)
        right_w = QWidget(); right_w.setLayout(right)

        root.addWidget(left_w, 3)
        root.addWidget(right_w, 2)

        self._build_pages()
        self._init_navigation()

    # ------------------------------------------------------------------
    def _build_pages(self) -> None:
        for idx, step in enumerate(self.config.steps):
            page = QScrollArea()
            page.setWidgetResizable(True)
            container = QWidget()
            vbox = QVBoxLayout(container)

            group_widgets: list[tuple[FomodGroup, list[tuple[FomodPlugin, QWidget]]]] = []
            default_selected = {id(p) for p in self._defaults_for_step(step)}

            for group in step.groups:
                box = QGroupBox(f"{group.name}   ·   {tr(_GROUP_HINT.get(group.type, group.type))}")
                gl = QVBoxLayout(box)
                exclusive = group.type in ("SelectExactlyOne", "SelectAtMostOne")
                btn_group = QButtonGroup(box) if exclusive else None
                if btn_group:
                    btn_group.setExclusive(True)

                plugin_widgets: list[tuple[FomodPlugin, QWidget]] = []
                for plugin in group.plugins:
                    if exclusive:
                        w = QRadioButton(plugin.name)
                    else:
                        w = QCheckBox(plugin.name)
                    if btn_group:
                        btn_group.addButton(w)

                    # Estado por defecto y tipo
                    if group.type == "SelectAll" or plugin.type == "Required":
                        w.setChecked(True)
                        w.setEnabled(False)
                    elif plugin.type == "NotUsable":
                        w.setEnabled(False)
                    elif id(plugin) in default_selected:
                        w.setChecked(True)

                    w.toggled.connect(
                        lambda checked, p=plugin: self._on_widget_toggled(checked, p)
                    )
                    gl.addWidget(w)
                    plugin_widgets.append((plugin, w))

                if group.type == "SelectAtMostOne":
                    none_rb = QRadioButton(tr("Ninguna"))
                    if btn_group:
                        btn_group.addButton(none_rb)
                    if not any(w.isChecked() for _, w in plugin_widgets):
                        none_rb.setChecked(True)
                    gl.addWidget(none_rb)

                group_widgets.append((group, plugin_widgets))
                vbox.addWidget(box)

            vbox.addStretch()
            page.setWidget(container)
            self.stack.addWidget(page)
            self._pages.append(page)
            self._step_data[idx] = group_widgets

    def _defaults_for_step(self, step) -> list[FomodPlugin]:
        out: list[FomodPlugin] = []
        for group in step.groups:
            out.extend(auto_pick_group(group))
        return out

    # ------------------------------------------------------------------
    def _init_navigation(self) -> None:
        first = self._next_visible_after(-1, {})
        if first is None:
            # No hay pasos visibles: instalar directo con lo que haya por defecto.
            self._visited = []
            self.stack.setVisible(False)
            self.title_lbl.setText(tr("Sin opciones que elegir."))
            self.next_btn.setVisible(False)
            self.back_btn.setVisible(False)
            self.install_btn.setVisible(True)
            return
        self._visited = [first]
        self._show_step(first)

    def _next_visible_after(self, after_idx: int, flags: dict[str, str]) -> int | None:
        for i in range(after_idx + 1, len(self.config.steps)):
            if self.config.steps[i].visible.evaluate(flags):
                return i
        return None

    def _read_step_selection(self, step_idx: int) -> list[FomodPlugin]:
        out: list[FomodPlugin] = []
        for _group, plugin_widgets in self._step_data.get(step_idx, []):
            for plugin, w in plugin_widgets:
                if w.isChecked():
                    out.append(plugin)
        return out

    def _flags_through_current(self) -> dict[str, str]:
        flags: dict[str, str] = {}
        for idx in self._visited:
            for plugin in self._read_step_selection(idx):
                flags.update(plugin.condition_flags)
        return flags

    def _show_step(self, step_idx: int) -> None:
        self.stack.setCurrentWidget(self._pages[step_idx])
        step = self.config.steps[step_idx]
        pos = self._visited.index(step_idx) + 1
        self.title_lbl.setText(tr("{name}   (paso {pos})").format(name=step.name, pos=pos))
        self._refresh_nav()

    def _refresh_nav(self) -> None:
        if not self._visited:
            return
        flags = self._flags_through_current()
        is_last = self._next_visible_after(self._visited[-1], flags) is None
        self.next_btn.setVisible(not is_last)
        self.install_btn.setVisible(is_last)
        self.back_btn.setEnabled(len(self._visited) > 1)

    # ------------------------------------------------------------------
    def _on_widget_toggled(self, checked: bool, plugin: FomodPlugin) -> None:
        if checked:
            self._show_plugin_info(plugin)
        self._refresh_nav()

    def _show_plugin_info(self, plugin: FomodPlugin) -> None:
        self.desc.setPlainText(plugin.description or tr("(sin descripción)"))
        if plugin.image:
            pix = QPixmap(plugin.image)
            if not pix.isNull():
                self.image_lbl.setPixmap(
                    pix.scaled(
                        self.image_lbl.width() or 360, self.image_lbl.height() or 220,
                        Qt.AspectRatioMode.KeepAspectRatio,
                        Qt.TransformationMode.SmoothTransformation,
                    )
                )
                return
        self.image_lbl.clear()

    # ------------------------------------------------------------------
    def _validate_current(self) -> bool:
        if not self._visited:
            return True
        step_idx = self._visited[-1]
        for group, plugin_widgets in self._step_data.get(step_idx, []):
            checked = [p for p, w in plugin_widgets if w.isChecked()]
            if group.type == "SelectAtLeastOne" and not checked:
                QMessageBox.warning(
                    self, tr("Selección requerida"),
                    tr("En '{group}' debes elegir al menos una opción.").format(group=group.name),
                )
                return False
            if group.type == "SelectExactlyOne" and len(checked) != 1:
                QMessageBox.warning(
                    self, tr("Selección requerida"),
                    tr("En '{group}' debes elegir exactamente una opción.").format(group=group.name),
                )
                return False
        return True

    def _go_next(self) -> None:
        if not self._validate_current():
            return
        flags = self._flags_through_current()
        nxt = self._next_visible_after(self._visited[-1], flags)
        if nxt is None:
            self._on_install()
            return
        self._visited.append(nxt)
        self._show_step(nxt)

    def _go_back(self) -> None:
        if len(self._visited) > 1:
            self._visited.pop()
            self._show_step(self._visited[-1])

    def _on_install(self) -> None:
        if not self._validate_current():
            return
        # Reunir selección de todos los pasos visitados (visibles).
        selection: list[FomodPlugin] = []
        for idx in self._visited:
            selection.extend(self._read_step_selection(idx))
        self._selection = selection
        self.accept()

    # ------------------------------------------------------------------
    def get_selection(self) -> list[FomodPlugin]:
        return self._selection

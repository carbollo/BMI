"""Asistente interactivo de instalación FOMOD."""
from __future__ import annotations

from PySide6.QtCore import Qt, QEvent
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QStackedWidget,
    QWidget, QScrollArea, QGroupBox, QRadioButton, QCheckBox, QButtonGroup,
    QMessageBox, QFrame, QProgressBar,
)

from ..fomod import FomodConfig, FomodGroup, FomodPlugin, auto_pick_group
from ..i18n import tr
from . import theme


_GROUP_HINT = {
    "SelectExactlyOne": "Elige exactamente una",
    "SelectAtMostOne": "Elige como máximo una",
    "SelectAtLeastOne": "Elige al menos una",
    "SelectAny": "Elige las que quieras",
    "SelectAll": "Todas obligatorias",
}


class FomodDialog(QDialog):
    """Presenta los pasos del FOMOD. Tras Instalar, get_selection() da las opciones."""

    def __init__(self, config: FomodConfig, parent=None):
        super().__init__(parent)
        self.config = config
        self.setWindowTitle(tr("Instalar") + f": {config.module_name}")
        self.resize(960, 680)

        # step_idx -> [(group, [(plugin, widget), ...]), ...]
        self._step_data: dict[int, list[tuple[FomodGroup, list[tuple[FomodPlugin, QWidget]]]]] = {}
        self._pages: list[QWidget] = []
        self._visited: list[int] = []
        self._selection: list[FomodPlugin] = []
        self._widget_plugin: dict[QWidget, FomodPlugin] = {}   # para info al pasar el ratón

        root = QVBoxLayout(self)
        root.setContentsMargins(16, 16, 16, 16)
        root.setSpacing(12)

        # --- Cabecera: título del mod + paso actual + progreso ---
        self.title_lbl = QLabel(config.module_name)
        self.title_lbl.setProperty("role", "title")
        self.step_lbl = QLabel()
        self.step_lbl.setProperty("role", "dim")
        self.progress = QProgressBar()
        self.progress.setTextVisible(False)
        self.progress.setFixedHeight(6)
        head = QVBoxLayout()
        head.setSpacing(5)
        head.addWidget(self.title_lbl)
        head.addWidget(self.step_lbl)
        head.addWidget(self.progress)
        root.addLayout(head)

        # --- Cuerpo: opciones (izq) | vista previa (der) ---
        body = QHBoxLayout()
        body.setSpacing(14)
        self.stack = QStackedWidget()
        body.addWidget(self.stack, 3)

        info = QFrame()
        info.setProperty("role", "card")
        info.setMinimumWidth(330)
        iv = QVBoxLayout(info)
        iv.setContentsMargins(14, 14, 14, 14)
        iv.setSpacing(10)
        self.info_name = QLabel(tr("Vista previa"))
        self.info_name.setProperty("role", "title")
        self.info_name.setWordWrap(True)
        self.image_lbl = QLabel(tr("(sin imagen)"))
        self.image_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.image_lbl.setMinimumHeight(230)
        self.image_lbl.setStyleSheet(
            f"background:{theme.BG_DARK}; border:1px solid {theme.BORDER};"
            f"border-radius:8px; color:{theme.TEXT_DIM};"
        )
        self.desc = QLabel(tr("Pasa el ratón por una opción para ver su descripción."))
        self.desc.setWordWrap(True)
        self.desc.setAlignment(Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignLeft)
        self.desc.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.desc.setStyleSheet(f"color:{theme.TEXT_DIM};")
        desc_scroll = QScrollArea()
        desc_scroll.setWidgetResizable(True)
        desc_scroll.setFrameShape(QFrame.Shape.NoFrame)
        desc_scroll.setWidget(self.desc)
        iv.addWidget(self.info_name)
        iv.addWidget(self.image_lbl)
        iv.addWidget(desc_scroll, 1)
        body.addWidget(info, 2)
        root.addLayout(body, 1)

        # --- Pie: navegación ---
        nav = QHBoxLayout()
        self.cancel_btn = QPushButton(tr("Cancelar"))
        self.cancel_btn.setProperty("variant", "danger")
        self.cancel_btn.clicked.connect(self.reject)
        self.back_btn = QPushButton(tr("← Atrás"))
        self.back_btn.clicked.connect(self._go_back)
        self.next_btn = QPushButton(tr("Siguiente →"))
        self.next_btn.setProperty("variant", "primary")
        self.next_btn.clicked.connect(self._go_next)
        self.install_btn = QPushButton(tr("✓ Instalar"))
        self.install_btn.setProperty("variant", "primary")
        self.install_btn.clicked.connect(self._on_install)
        nav.addWidget(self.cancel_btn)
        nav.addStretch()
        nav.addWidget(self.back_btn)
        nav.addWidget(self.next_btn)
        nav.addWidget(self.install_btn)
        root.addLayout(nav)

        self._build_pages()
        self._init_navigation()

    # ------------------------------------------------------------------
    def _label_for(self, plugin: FomodPlugin) -> str:
        if plugin.type == "Recommended":
            return f"{plugin.name}   ({tr('recomendado')})"
        if plugin.type == "NotUsable":
            return f"{plugin.name}   ({tr('no disponible')})"
        return plugin.name

    def _build_pages(self) -> None:
        for idx, step in enumerate(self.config.steps):
            page = QScrollArea()
            page.setWidgetResizable(True)
            page.setFrameShape(QFrame.Shape.NoFrame)
            container = QWidget()
            vbox = QVBoxLayout(container)
            vbox.setContentsMargins(2, 2, 8, 2)
            vbox.setSpacing(12)

            group_widgets: list[tuple[FomodGroup, list[tuple[FomodPlugin, QWidget]]]] = []
            default_selected = {id(p) for p in self._defaults_for_step(step)}

            for group in step.groups:
                hint = tr(_GROUP_HINT.get(group.type, group.type))
                box = QGroupBox(f"{group.name}   ·   {hint}")
                gl = QVBoxLayout(box)
                gl.setSpacing(7)
                exclusive = group.type in ("SelectExactlyOne", "SelectAtMostOne")
                btn_group = QButtonGroup(box) if exclusive else None
                if btn_group:
                    btn_group.setExclusive(True)

                plugin_widgets: list[tuple[FomodPlugin, QWidget]] = []
                for plugin in group.plugins:
                    w = QRadioButton(self._label_for(plugin)) if exclusive \
                        else QCheckBox(self._label_for(plugin))
                    if btn_group:
                        btn_group.addButton(w)

                    if group.type == "SelectAll" or plugin.type == "Required":
                        w.setChecked(True)
                        w.setEnabled(False)
                        w.setText(f"{plugin.name}   ({tr('obligatorio')})")
                    elif plugin.type == "NotUsable":
                        w.setEnabled(False)
                    elif id(plugin) in default_selected:
                        w.setChecked(True)

                    w.toggled.connect(
                        lambda checked, p=plugin: self._on_widget_toggled(checked, p)
                    )
                    w.installEventFilter(self)
                    self._widget_plugin[w] = plugin
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
            self.step_lbl.setText(tr("Este mod no tiene opciones que elegir."))
            self.progress.setVisible(False)
            self.next_btn.setVisible(False)
            self.back_btn.setVisible(False)
            self.install_btn.setVisible(True)
            self._show_module_preview()
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
        total = max(len(self.config.steps), pos)
        self.step_lbl.setText(tr("Paso {pos} de {total}  ·  {name}").format(
            pos=pos, total=total, name=step.name))
        self.progress.setRange(0, total)
        self.progress.setValue(pos)
        self._show_step_preview(step_idx)
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
    def eventFilter(self, obj, event):
        if event.type() == QEvent.Type.Enter:
            plugin = self._widget_plugin.get(obj)
            if plugin is not None:
                self._show_plugin_info(plugin)
        return super().eventFilter(obj, event)

    def _on_widget_toggled(self, checked: bool, plugin: FomodPlugin) -> None:
        if checked:
            self._show_plugin_info(plugin)
        self._refresh_nav()

    def _show_step_preview(self, step_idx: int) -> None:
        """Muestra en el panel la opción marcada (o la primera) del paso."""
        chosen = None
        groups = self._step_data.get(step_idx, [])
        for _group, plugin_widgets in groups:
            for plugin, w in plugin_widgets:
                if w.isChecked():
                    chosen = plugin
                    break
            if chosen:
                break
        if chosen is None and groups and groups[0][1]:
            chosen = groups[0][1][0][0]
        if chosen is not None:
            self._show_plugin_info(chosen)
        else:
            self._show_module_preview()

    def _show_module_preview(self) -> None:
        self.info_name.setText(self.config.module_name)
        self.desc.setText(tr("Pasa el ratón por una opción para ver su descripción."))
        self._set_image(self.config.module_image)

    def _show_plugin_info(self, plugin: FomodPlugin) -> None:
        self.info_name.setText(plugin.name)
        self.desc.setText(plugin.description or tr("(sin descripción)"))
        self._set_image(plugin.image)

    def _set_image(self, path: str) -> None:
        if path:
            pix = QPixmap(path)
            if not pix.isNull():
                w = max(self.image_lbl.width() - 16, 280)
                h = max(self.image_lbl.height() - 16, 210)
                self.image_lbl.setPixmap(pix.scaled(
                    w, h, Qt.AspectRatioMode.KeepAspectRatio,
                    Qt.TransformationMode.SmoothTransformation,
                ))
                return
        self.image_lbl.setPixmap(QPixmap())
        self.image_lbl.setText(tr("(sin imagen)"))

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
        selection: list[FomodPlugin] = []
        for idx in self._visited:
            selection.extend(self._read_step_selection(idx))
        self._selection = selection
        self.accept()

    # ------------------------------------------------------------------
    def get_selection(self) -> list[FomodPlugin]:
        return self._selection

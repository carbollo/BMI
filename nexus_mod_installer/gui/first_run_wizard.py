"""Asistente de configuración inicial (primer arranque)."""
from __future__ import annotations

from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QLineEdit, QPushButton, QFileDialog,
    QStackedWidget, QWidget, QMessageBox, QComboBox,
)

from ..config import AppConfig
from ..nexus_api import NexusApiClient
from .. import nxm, games
from ..i18n import tr, LANGUAGES
from . import theme


class FirstRunWizard(QDialog):
    def __init__(self, config: AppConfig, parent=None):
        super().__init__(parent)
        self.config = config
        self.setWindowTitle(tr("Configuración inicial — Nexus Mod Installer").replace("Nexus Mod Installer", "BMI"))
        self.setMinimumSize(620, 380)

        self.stack = QStackedWidget()
        self.stack.addWidget(self._page_welcome())
        self.stack.addWidget(self._page_data())
        self.stack.addWidget(self._page_protocol())

        nav = QHBoxLayout()
        self.back_btn = QPushButton(tr("← Atrás")); self.back_btn.clicked.connect(self._back)
        self.next_btn = QPushButton(tr("Siguiente →")); self.next_btn.setProperty("variant", "primary")
        self.next_btn.clicked.connect(self._next)
        skip = QPushButton(tr("Omitir")); skip.clicked.connect(self.reject)
        nav.addWidget(skip); nav.addStretch()
        nav.addWidget(self.back_btn); nav.addWidget(self.next_btn)

        root = QVBoxLayout(self)
        root.addWidget(self.stack, 1)
        root.addLayout(nav)
        self._update_nav()

    # ------------------------------------------------------------------
    def _page_welcome(self) -> QWidget:
        w = QWidget(); v = QVBoxLayout(w)
        title = QLabel(tr("¡Bienvenido!")); title.setProperty("role", "title")
        v.addWidget(title)
        v.addWidget(QLabel(tr("Vamos a configurar lo esencial en 3 pasos.")))

        v.addWidget(QLabel(tr("Elige el idioma:")))
        self.lang_combo = QComboBox()
        for code, name in LANGUAGES.items():
            self.lang_combo.addItem(name, code)
        li = self.lang_combo.findData(self.config.language)
        self.lang_combo.setCurrentIndex(li if li >= 0 else 0)
        v.addWidget(self.lang_combo)

        v.addWidget(QLabel(tr("Elige el juego:")))
        self.game_combo = QComboBox()
        for g in games.all_games():
            self.game_combo.addItem(g.name, g.key)
        gi = self.game_combo.findData(self.config.game_domain)
        self.game_combo.setCurrentIndex(gi if gi >= 0 else 0)
        self.game_combo.currentIndexChanged.connect(self._sync_data_default)
        v.addWidget(self.game_combo)

        info = QLabel(tr("Inicio de sesión: al terminar este asistente, pulsa «Iniciar "
                         "sesión con Nexus» en la pestaña Explorar (arriba a la derecha) "
                         "para entrar con tu cuenta de Nexus."))
        info.setWordWrap(True)
        v.addWidget(info)
        nota = QLabel(tr("BMI usa el inicio de sesión oficial de Nexus (OAuth); ya no hace "
                         "falta pegar ninguna API Key."))
        nota.setWordWrap(True); nota.setProperty("role", "dim")
        v.addWidget(nota)
        v.addStretch()
        return w

    def _page_data(self) -> QWidget:
        w = QWidget(); v = QVBoxLayout(w)
        title = QLabel(tr("Carpeta del juego")); title.setProperty("role", "title")
        v.addWidget(title)
        v.addWidget(QLabel(
            tr("Indica la carpeta de datos del juego.")
            + "\n(p.ej. ...\\steamapps\\common\\<...>\\Data)."
        ))
        row = QHBoxLayout()
        g0 = games.get(self.game_combo.currentData())
        self.data_edit = QLineEdit(self.config.game_data_path or games.default_data_path(g0))
        browse = QPushButton("…"); browse.clicked.connect(self._pick_data)
        row.addWidget(self.data_edit); row.addWidget(browse)
        v.addLayout(row)
        self.data_status = QLabel(""); v.addWidget(self.data_status)
        v.addStretch()
        return w

    def _page_protocol(self) -> QWidget:
        w = QWidget(); v = QVBoxLayout(w)
        title = QLabel(tr("Protocolo de descarga")); title.setProperty("role", "title")
        v.addWidget(title)
        v.addWidget(QLabel(
            tr("Registra el protocolo nxm:// para que el botón 'Mod Manager Download' de "
               "Nexus abra este programa.")
        ))
        reg = QPushButton(tr("Registrar protocolo nxm://")); reg.clicked.connect(self._register)
        v.addWidget(reg)
        self.proto_status = QLabel(); self._refresh_proto(); v.addWidget(self.proto_status)
        v.addStretch()
        v.addWidget(QLabel(tr("Pulsa 'Finalizar' para empezar.")))
        return w

    # ------------------------------------------------------------------
    def _sync_data_default(self) -> None:
        if hasattr(self, "data_edit"):
            self.data_edit.setText(games.default_data_path(games.get(self.game_combo.currentData())))

    def _pick_data(self) -> None:
        d = QFileDialog.getExistingDirectory(self, tr("Carpeta Data"), self.data_edit.text() or "")
        if d:
            self.data_edit.setText(d)

    def _register(self) -> None:
        ok, msg = nxm.register_protocol()
        self.config.protocol_registered = ok
        QMessageBox.information(self, tr("Protocolo nxm://"), msg)
        self._refresh_proto()

    def _refresh_proto(self) -> None:
        reg = nxm.is_protocol_registered()
        self.proto_status.setText(tr("✅ Registrado") if reg else tr("❌ No registrado"))
        self.proto_status.setStyleSheet(f"color:{theme.SUCCESS if reg else theme.DANGER};")

    # ------------------------------------------------------------------
    def _back(self) -> None:
        self.stack.setCurrentIndex(max(0, self.stack.currentIndex() - 1))
        self._update_nav()

    def _next(self) -> None:
        idx = self.stack.currentIndex()
        if idx == 1 and not self.data_edit.text().strip():
            QMessageBox.warning(self, tr("Falta la carpeta Data"),
                                tr("Indica la carpeta de datos del juego."))
            return
        if idx >= self.stack.count() - 1:
            self._finish()
            return
        self.stack.setCurrentIndex(idx + 1)
        self._update_nav()

    def _update_nav(self) -> None:
        idx = self.stack.currentIndex()
        last = idx >= self.stack.count() - 1
        self.back_btn.setEnabled(idx > 0)
        self.next_btn.setText(tr("Finalizar") if last else tr("Siguiente →"))

    def _finish(self) -> None:
        self.config.language = self.lang_combo.currentData() or self.config.language
        self.config.game_domain = self.game_combo.currentData() or self.config.game_domain
        g = games.get(self.config.game_domain)
        self.config.game_data_path = self.data_edit.text().strip()
        self.config.plugins_txt_path = games.default_plugins_txt(g)
        self.config.skse_loader_path = ""
        self.config.ensure_dirs()
        self.config.save()
        self.accept()

"""Diálogo de Ajustes (con pestañas)."""
from __future__ import annotations

from PySide6.QtCore import Signal, Qt
from PySide6.QtWidgets import (
    QDialog, QFormLayout, QLineEdit, QPushButton, QFileDialog, QComboBox,
    QCheckBox, QHBoxLayout, QVBoxLayout, QLabel, QWidget, QMessageBox, QSpinBox,
    QTabWidget,
)

from ..config import AppConfig
from ..nexus_api import NexusApiClient
from .. import nxm
from ..i18n import tr, LANGUAGES
from . import theme


class SettingsDialog(QDialog):
    detect_mods_requested = Signal()   # botón «Detectar mods de la carpeta» (lo gestiona la ventana)
    check_updates_requested = Signal() # botón «Buscar actualizaciones» (lo gestiona la ventana)

    def __init__(self, config: AppConfig, parent=None):
        super().__init__(parent)
        self.config = config
        self._orig_language = config.language
        self.setWindowTitle(tr("Ajustes"))
        self.setMinimumSize(680, 480)

        # --- Widgets (se reparten en pestañas) ---
        self.data_path_edit = QLineEdit(config.game_data_path)
        self.plugins_edit = QLineEdit(config.plugins_txt_path)
        self.skse_edit = QLineEdit(config.skse_loader_path)
        self.skse_edit.setPlaceholderText(tr("Opcional: se autodetecta en la carpeta del juego"))
        self.downloads_edit = QLineEdit(config.downloads_dir)
        self.mods_edit = QLineEdit(config.mods_dir)
        self.deploy_combo = QComboBox(); self.deploy_combo.addItems(["hardlink", "copy"])
        self.deploy_combo.setCurrentText(config.deploy_method)
        self.auto_deploy_cb = QCheckBox(tr("Desplegar a Data automáticamente tras instalar"))
        self.auto_deploy_cb.setChecked(config.auto_deploy)
        self.auto_plugins_cb = QCheckBox(tr("Activar plugins (.esp/.esm) en plugins.txt"))
        self.auto_plugins_cb.setChecked(config.auto_enable_plugins)
        self.deps_cb = QCheckBox(tr("Resolver e instalar dependencias automáticamente"))
        self.deps_cb.setChecked(config.resolve_dependencies)
        self.spanish_cb = QCheckBox(tr("Descargar la traducción del mod en el idioma de la app (si existe)"))
        self.spanish_cb.setChecked(config.install_spanish_translation)
        self.force_lang_cb = QCheckBox(tr("Forzar el idioma del juego = idioma de la app "
                                          "(sLanguage) al arrancar"))
        self.force_lang_cb.setChecked(getattr(config, "force_game_language", True))
        self.variant_cb = QCheckBox(tr("Bloquear variantes incompatibles al descargar "
                                       "(GOG en Steam, VR en Skyrim SE)"))
        self.variant_cb.setChecked(getattr(config, "block_wrong_variant", True))
        self.vfs_cb = QCheckBox(tr("Modo VFS (experimental): NO copiar a Data; virtualizar los "
                                   "mods al jugar y mantener Data limpia (estilo MO2)"))
        self.vfs_cb.setChecked(getattr(config, "vfs_mode", False))
        self.check_updates_cb = QCheckBox(tr("Buscar actualizaciones de BMI al arrancar"))
        self.check_updates_cb.setChecked(getattr(config, "check_updates", True))
        self.fomod_combo = QComboBox()
        self.fomod_combo.addItem(tr("Interactivo (elegir opciones)"), "interactive")
        self.fomod_combo.addItem(tr("Automático (obligatorias + recomendadas)"), "auto")
        idx = self.fomod_combo.findData(config.fomod_mode)
        self.fomod_combo.setCurrentIndex(idx if idx >= 0 else 0)

        self.lang_combo = QComboBox()
        for code, name in LANGUAGES.items():
            self.lang_combo.addItem(name, code)
        li = self.lang_combo.findData(config.language)
        self.lang_combo.setCurrentIndex(li if li >= 0 else 0)

        tabs = QTabWidget()
        tabs.addTab(self._tab_account(), tr("👤 Cuenta"))
        tabs.addTab(self._tab_language(), tr("🌐 Idioma"))
        tabs.addTab(self._tab_paths(), tr("📁 Rutas"))
        tabs.addTab(self._tab_install(), tr("⚙ Instalación"))
        tabs.addTab(self._tab_protocol(), tr("🔗 Protocolo"))

        buttons = QHBoxLayout()
        buttons.setSpacing(8)
        buttons.addStretch()
        cancel_btn = QPushButton(tr("Cancelar")); cancel_btn.clicked.connect(self.reject)
        save_btn = QPushButton(tr("Guardar")); save_btn.setProperty("variant", "primary")
        save_btn.clicked.connect(self.accept)
        buttons.addWidget(cancel_btn); buttons.addWidget(save_btn)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(10)
        layout.addWidget(tabs)
        layout.addLayout(buttons)

    # ------------------------------------------------------------------
    def _tab_account(self) -> QWidget:
        w = QWidget(); form = QFormLayout(w)
        form.setContentsMargins(18, 18, 18, 18)
        form.setSpacing(12)
        from .. import oauth
        logged = oauth.OAuthSession().is_logged_in
        form.addRow(tr("Cuenta de Nexus:"),
                    QLabel(tr("✓ Sesión iniciada") if logged else tr("✗ Sin sesión")))
        nota = QLabel(tr("BMI usa el inicio de sesión oficial de Nexus (OAuth). Para iniciar "
                         "o cerrar sesión, usa el botón «Iniciar sesión con Nexus» de la "
                         "pestaña Explorar (arriba a la derecha)."))
        nota.setWordWrap(True)
        nota.setProperty("role", "dim")
        form.addRow("", nota)
        # Versión actual del programa (abajo de la pestaña Cuenta).
        from .. import __version__
        ver = QLabel(f"BMI  ·  v{__version__}")
        ver.setProperty("role", "dim")
        ver.setAlignment(Qt.AlignmentFlag.AlignRight)
        form.addRow("", ver)
        return w

    def _tab_language(self) -> QWidget:
        w = QWidget(); form = QFormLayout(w)
        form.setContentsMargins(18, 18, 18, 18)
        form.setSpacing(12)
        form.addRow(tr("Idioma de la interfaz:"), self.lang_combo)
        note = QLabel(tr("El cambio de idioma se aplica al reiniciar el programa."))
        note.setWordWrap(True); note.setProperty("role", "dim")
        form.addRow("", note)
        return w

    def _tab_paths(self) -> QWidget:
        w = QWidget(); form = QFormLayout(w)
        form.setContentsMargins(18, 18, 18, 18)
        form.setSpacing(12)
        form.addRow(QLabel(f"<b>{tr('Juego activo')}:</b> {self.config.game().name}"))
        form.addRow(tr("Carpeta de datos del juego:"),
                    self._with_button(self.data_path_edit, self._dir_btn(self.data_path_edit)))
        form.addRow(tr("Ruta plugins.txt:"),
                    self._with_button(self.plugins_edit, self._file_btn(self.plugins_edit)))
        form.addRow(tr("Lanzador del Script Extender:"),
                    self._with_button(self.skse_edit, self._file_btn(self.skse_edit)))
        form.addRow(tr("Carpeta de descargas:"),
                    self._with_button(self.downloads_edit, self._dir_btn(self.downloads_edit)))
        form.addRow(tr("Carpeta de mods (gestionados):"),
                    self._with_button(self.mods_edit, self._dir_btn(self.mods_edit)))
        detect_btn = QPushButton(tr("🔎 Detectar mods de la carpeta"))
        detect_btn.setToolTip(tr("Escanea esta carpeta y añade a la lista los mods que ya tengas "
                                 "ahí (estructura estilo MO2), sin descargarlos. También quita "
                                 "los que hayas sacado de la carpeta."))
        detect_btn.clicked.connect(self._detect_mods_now)
        form.addRow("", detect_btn)
        return w

    def _detect_mods_now(self) -> None:
        """Aplica la ruta escrita a la config y pide a la ventana que escanee la carpeta."""
        self.config.mods_dir = self.mods_edit.text().strip()
        self.detect_mods_requested.emit()

    def _tab_install(self) -> QWidget:
        w = QWidget(); form = QFormLayout(w)
        form.setContentsMargins(18, 18, 18, 18)
        form.setSpacing(12)
        form.addRow(tr("Método de despliegue a Data:"), self.deploy_combo)
        form.addRow(tr("Instaladores FOMOD:"), self.fomod_combo)
        for cb in (self.auto_deploy_cb, self.auto_plugins_cb, self.deps_cb, self.spanish_cb,
                   self.force_lang_cb, self.variant_cb, self.vfs_cb, self.check_updates_cb):
            form.addRow("", cb)
        upd_btn = QPushButton(tr("🔄 Buscar actualizaciones ahora"))
        upd_btn.clicked.connect(self.check_updates_requested)
        form.addRow("", upd_btn)
        return w

    def _tab_protocol(self) -> QWidget:
        w = QWidget(); form = QFormLayout(w)
        form.setContentsMargins(18, 18, 18, 18)
        form.setSpacing(12)
        info = QLabel(
            tr("Registra el protocolo nxm:// para que el botón 'Mod Manager Download' de la "
               "web de Nexus abra este programa. Con cuenta Premium, pegar la URL de un mod "
               "ya lo descarga automáticamente vía API (sin clics).")
        )
        info.setWordWrap(True)
        form.addRow(info)
        self.proto_lbl = QLabel(); self._refresh_proto_label()
        proto_btn = QPushButton(tr("Registrar protocolo nxm://"))
        proto_btn.clicked.connect(self._register_proto)
        form.addRow(proto_btn, self.proto_lbl)
        return w

    # ------------------------------------------------------------------
    def _dir_btn(self, edit: QLineEdit) -> QPushButton:
        b = QPushButton("…"); b.clicked.connect(lambda: self._pick_dir(edit)); return b

    def _file_btn(self, edit: QLineEdit) -> QPushButton:
        b = QPushButton("…"); b.clicked.connect(lambda: self._pick_file(edit)); return b

    @staticmethod
    def _with_button(edit: QLineEdit, btn: QPushButton) -> QWidget:
        row = QHBoxLayout(); row.setContentsMargins(0, 0, 0, 0)
        row.addWidget(edit); row.addWidget(btn)
        w = QWidget(); w.setLayout(row)
        return w

    def _pick_dir(self, edit: QLineEdit) -> None:
        d = QFileDialog.getExistingDirectory(self, tr("Selecciona carpeta"), edit.text() or "")
        if d:
            edit.setText(d)

    def _pick_file(self, edit: QLineEdit) -> None:
        f, _ = QFileDialog.getOpenFileName(self, tr("Selecciona archivo"), edit.text() or "")
        if f:
            edit.setText(f)

    def _register_proto(self) -> None:
        ok, msg = nxm.register_protocol()
        self.config.protocol_registered = ok
        QMessageBox.information(self, tr("Protocolo nxm://"), msg)
        self._refresh_proto_label()

    def _refresh_proto_label(self) -> None:
        self.proto_lbl.setText(tr("✅ Registrado") if nxm.is_protocol_registered()
                               else tr("❌ No registrado"))

    def language_changed(self) -> bool:
        return self.config.language != self._orig_language

    # ------------------------------------------------------------------
    def apply_to_config(self) -> None:
        self.config.game_data_path = self.data_path_edit.text().strip()
        self.config.plugins_txt_path = self.plugins_edit.text().strip()
        self.config.skse_loader_path = self.skse_edit.text().strip()
        self.config.downloads_dir = self.downloads_edit.text().strip()
        self.config.mods_dir = self.mods_edit.text().strip()
        self.config.deploy_method = self.deploy_combo.currentText()
        self.config.auto_deploy = self.auto_deploy_cb.isChecked()
        self.config.auto_enable_plugins = self.auto_plugins_cb.isChecked()
        self.config.resolve_dependencies = self.deps_cb.isChecked()
        self.config.install_spanish_translation = self.spanish_cb.isChecked()
        self.config.force_game_language = self.force_lang_cb.isChecked()
        self.config.block_wrong_variant = self.variant_cb.isChecked()
        self.config.vfs_mode = self.vfs_cb.isChecked()
        self.config.check_updates = self.check_updates_cb.isChecked()
        self.config.fomod_mode = self.fomod_combo.currentData() or "interactive"
        self.config.language = self.lang_combo.currentData() or "es"
        self.config.ensure_dirs()
        self.config.save()

"""Pestaña de Inicio: panel de estado (dashboard)."""
from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QGridLayout, QLabel, QPushButton, QFrame,
)

from .. import launcher, scanner, nxm
from ..i18n import tr
from . import theme


class _Card(QFrame):
    def __init__(self, title: str):
        super().__init__()
        self.setProperty("role", "card")
        lay = QVBoxLayout(self)
        t = QLabel(title)
        t.setProperty("role", "dim")
        self.value = QLabel("…")
        self.value.setStyleSheet("font-size:18px; font-weight:bold;")
        self.value.setWordWrap(True)
        lay.addWidget(t)
        lay.addWidget(self.value)

    def set(self, text: str, color: str | None = None) -> None:
        self.value.setText(text)
        self.value.setStyleSheet(
            f"font-size:18px; font-weight:bold; color:{color or theme.TEXT};"
        )


class HomePanel(QWidget):
    open_settings = Signal()
    launch_game = Signal()
    go_explore = Signal()

    def __init__(self, manager, parent=None):
        super().__init__(parent)
        self.manager = manager
        self.config = manager.config

        root = QVBoxLayout(self)

        self.header = QLabel("BMI — Bethesda Mod Installer")
        self.header.setProperty("role", "title")
        root.addWidget(self.header)

        # Aviso de configuración inicial
        self.banner = QLabel()
        self.banner.setWordWrap(True)
        self.banner.setStyleSheet(
            f"background:{theme.PANEL}; border:1px solid {theme.ACCENT};"
            f"border-radius:8px; padding:10px; color:{theme.ACCENT_HI};"
        )
        root.addWidget(self.banner)

        # Tarjetas
        grid = QGridLayout()
        self.card_account = _Card(tr("Cuenta Nexus"))
        self.card_data = _Card(tr("Carpeta de datos del juego"))
        self.card_skse = _Card(tr("Script Extender"))
        self.card_mods = _Card(tr("Mods gestionados"))
        self.card_plugins = _Card(tr("Plugins activos"))
        cards = [self.card_account, self.card_data, self.card_skse,
                 self.card_mods, self.card_plugins]
        for i, c in enumerate(cards):
            grid.addWidget(c, i // 3, i % 3)
        root.addLayout(grid)

        # Acciones rápidas
        actions = QHBoxLayout()
        explore = QPushButton(tr("🔎 Explorar Nexus"))
        explore.setProperty("variant", "primary")
        explore.clicked.connect(self.go_explore)
        play = QPushButton(tr("▶ Jugar (SKSE64)"))
        play.setProperty("variant", "success")
        play.clicked.connect(self.launch_game)
        settings = QPushButton(tr("⚙ Ajustes"))
        settings.clicked.connect(self.open_settings)
        scan = QPushButton(tr("🔄 Actualizar estado"))
        scan.clicked.connect(self.refresh)
        for b in (explore, play, settings, scan):
            actions.addWidget(b)
        actions.addStretch()
        root.addLayout(actions)
        root.addStretch()

        self.refresh()

    # ------------------------------------------------------------------
    def refresh(self, scan_cache=None) -> None:
        cfg = self.config
        ok, bad = theme.SUCCESS, theme.DANGER
        g = cfg.game()
        self.header.setText(f"BMI — {g.name}")

        # Cuenta
        api = self.manager.api
        if api.user_name:
            premium = api.is_premium
            self.card_account.set(
                f"{api.user_name}\n({'PREMIUM' if premium else tr('Gratis')})",
                theme.ACCENT if premium else theme.TEXT,
            )
        elif cfg.api_key:
            self.card_account.set(tr("API key sin validar"), theme.TEXT_DIM)
        else:
            self.card_account.set(tr("Sin API key"), bad)

        # Data
        data_ok = bool(cfg.game_data_path) and Path(cfg.game_data_path).is_dir()
        self.card_data.set(tr("✓ Encontrada") if data_ok else tr("✗ No configurada"),
                           ok if data_ok else bad)

        # Script Extender (SKSE64/F4SE/NVSE/...)
        skse = launcher.find_skse(cfg)
        se = g.script_extender or "—"
        self.card_skse.set(f"✓ {se}" if skse else f"✗ {se}",
                           ok if skse else theme.TEXT_DIM)

        # Mods
        n_mods = len(self.manager.store.all())
        self.card_mods.set(str(n_mods))

        # Plugins activos (reutiliza el escaneo del gestor si se pasa, para no leer el
        # disco dos veces en el hilo de la GUI).
        try:
            mods = scan_cache if scan_cache is not None else scanner.scan_installed(
                cfg.game_data_path, cfg.plugins_txt_path,
                {p for m in self.manager.store.all() for p in m.plugins},
                game=g,
            )
            active = sum(1 for m in mods if m.enabled)
            self.card_plugins.set(f"{active} / {len(mods)}")
        except Exception:
            self.card_plugins.set("—")

        # Banner
        proto = nxm.is_protocol_registered()
        if not cfg.is_configured:
            self.banner.show()
            self.banner.setText(
                tr("⚠ Configuración pendiente: añade tu API Key de Nexus y la carpeta Data "
                   "en Ajustes para empezar.")
            )
        elif not proto:
            self.banner.show()
            self.banner.setText(
                tr("ℹ El protocolo nxm:// no está registrado. Regístralo en Ajustes para que "
                   "el botón de descarga de Nexus funcione.")
            )
        else:
            self.banner.hide()

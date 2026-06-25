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
from . import icons
from . import effects


class _Card(QFrame):
    def __init__(self, title: str):
        super().__init__()
        self.setProperty("role", "card")
        self.setMinimumHeight(84)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(16, 13, 16, 13)
        lay.setSpacing(9)
        t = QLabel(title.upper())
        t.setProperty("role", "label")
        lay.addWidget(t)

        row = QHBoxLayout()
        row.setSpacing(9)
        self.dot = QLabel()
        self.dot.setFixedSize(9, 9)
        self._dot(theme.TEXT_DIM)
        self.value = QLabel("…")
        self.value.setProperty("role", "value")
        self.value.setWordWrap(True)
        row.addWidget(self.dot, 0, Qt.AlignmentFlag.AlignVCenter)
        row.addWidget(self.value, 1)
        lay.addLayout(row)
        effects.add_shadow(self, blur=18, dy=3, alpha=120)

    def _dot(self, color: str) -> None:
        self.dot.setStyleSheet(f"background:{color}; border-radius:4px;")

    def set(self, text: str, color: str | None = None, dot: str | None = None) -> None:
        self.value.setText(text)
        self.value.setStyleSheet(f"font-size:18px; font-weight:bold; color:{color or theme.TEXT};")
        d = dot or color
        if d:
            self._dot(d)
            self.dot.show()
        else:
            self.dot.hide()


class HomePanel(QWidget):
    open_settings = Signal()
    launch_game = Signal()
    go_explore = Signal()

    def __init__(self, manager, parent=None):
        super().__init__(parent)
        self.manager = manager
        self.config = manager.config

        root = QVBoxLayout(self)
        root.setContentsMargins(20, 18, 20, 18)
        root.setSpacing(16)

        self.header = QLabel("BMI — Bethesda Mod Installer")
        self.header.setProperty("role", "title")
        self.header.setStyleSheet("font-size:21px; font-weight:bold;")
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
        grid.setSpacing(14)
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
        actions.setSpacing(10)
        explore = QPushButton(tr("Explorar Nexus"))
        explore.setIcon(icons.icon("search", "#1a1207"))
        explore.setProperty("variant", "primary")
        explore.clicked.connect(self.go_explore)
        play = QPushButton(tr("Jugar (SKSE64)"))
        play.setIcon(icons.icon("play", "#0b2a12"))
        play.setProperty("variant", "success")
        play.clicked.connect(self.launch_game)
        settings = QPushButton(tr("Ajustes"))
        settings.setIcon(icons.icon("settings", theme.TEXT))
        settings.clicked.connect(self.open_settings)
        scan = QPushButton(tr("Actualizar estado"))
        scan.setIcon(icons.icon("refresh", theme.TEXT))
        scan.clicked.connect(self.refresh)
        for b in (explore, play, settings, scan):
            actions.addWidget(b)
        actions.addStretch()
        root.addLayout(actions)

        # Últimos mods instalados (o guía de primeros pasos si aún no hay).
        root.addWidget(self._build_activity())
        root.addStretch()

        # Llamada a la acción para donaciones (Buy Me a Coffee).
        root.addWidget(self._build_donate_bar())

        self.refresh()

    # ------------------------------------------------------------------
    def _build_donate_bar(self) -> QFrame:
        bar = QFrame()
        bar.setProperty("role", "card")
        lay = QHBoxLayout(bar)
        lay.setContentsMargins(16, 12, 16, 12)
        lay.setSpacing(12)
        msg = QLabel(tr("¿Te resulta útil BMI? Es gratis y siempre lo será. "
                        "Si quieres, puedes apoyar su desarrollo:"))
        msg.setWordWrap(True)
        msg.setProperty("role", "dim")
        self.donate_btn = QPushButton(tr("  Invítame a un café"))
        self.donate_btn.setIcon(icons.icon("coffee", "#000000"))
        self.donate_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.donate_btn.setStyleSheet(
            "background:#FFDD00; color:#000000; border:1px solid #000000;"
            "border-radius:8px; padding:8px 18px; font-weight:bold;"
        )
        self.donate_btn.clicked.connect(self._open_donate)
        lay.addWidget(msg, 1)
        lay.addWidget(self.donate_btn, 0, Qt.AlignmentFlag.AlignRight)
        effects.add_shadow(bar, blur=18, dy=3, alpha=120)
        return bar

    def _open_donate(self) -> None:
        from .donate_dialog import DonateDialog
        DonateDialog(self).exec()

    # ------------------------------------------------------------------
    def _build_activity(self) -> QWidget:
        self.activity_box = QWidget()
        self._activity_lay = QVBoxLayout(self.activity_box)
        self._activity_lay.setContentsMargins(0, 0, 0, 0)
        self._activity_lay.setSpacing(10)
        return self.activity_box

    def _refresh_activity(self) -> None:
        if not hasattr(self, "_activity_lay"):
            return
        while self._activity_lay.count():
            w = self._activity_lay.takeAt(0).widget()
            if w:
                w.deleteLater()
        mods = sorted(self.manager.store.all(),
                      key=lambda m: m.installed_at or 0, reverse=True)
        if mods:
            title = QLabel(tr("Últimos mods instalados"))
            title.setProperty("role", "h2")
            self._activity_lay.addWidget(title)
            row = QHBoxLayout()
            row.setSpacing(12)
            for mod in mods[:4]:
                row.addWidget(self._mod_card(mod))
            row.addStretch()
            cont = QWidget()
            cont.setLayout(row)
            self._activity_lay.addWidget(cont)
        else:
            self._activity_lay.addWidget(self._build_first_steps())

    def _mod_card(self, mod) -> QFrame:
        from .images import ThumbLabel
        card = QFrame()
        card.setProperty("role", "card")
        card.setFixedWidth(232)
        card.setCursor(Qt.CursorShape.PointingHandCursor)
        lay = QHBoxLayout(card)
        lay.setContentsMargins(10, 10, 10, 10)
        lay.setSpacing(10)
        lay.addWidget(ThumbLabel(getattr(mod, "picture_url", "") or "", 46))
        col = QVBoxLayout()
        col.setSpacing(2)
        name = QLabel(mod.name)
        name.setStyleSheet("font-weight:bold; background:transparent;")
        name.setWordWrap(True)
        ver = QLabel(mod.version or tr("instalado"))
        ver.setProperty("role", "dim")
        col.addWidget(name)
        col.addWidget(ver)
        lay.addLayout(col, 1)
        effects.add_shadow(card, blur=16, dy=2, alpha=110)
        card.mousePressEvent = lambda e, m=mod: self._open_mod(m)
        return card

    def _open_mod(self, mod) -> None:
        from .mod_details_dialog import ModDetailsDialog
        ModDetailsDialog(self.manager.store.get(mod.mod_id) or mod, self).exec()

    def _build_first_steps(self) -> QFrame:
        card = QFrame()
        card.setProperty("role", "card")
        lay = QVBoxLayout(card)
        lay.setContentsMargins(18, 16, 18, 16)
        lay.setSpacing(9)
        t = QLabel(tr("Primeros pasos"))
        t.setProperty("role", "h2")
        lay.addWidget(t)
        for i, s in enumerate([
            tr("Pega arriba la URL de un mod de Nexus (o un enlace nxm://) y pulsa «Añadir / Descargar»."),
            tr("Con cuenta gratuita se abrirá la página del mod: pulsa una vez «Mod Manager Download»."),
            tr("BMI lo descarga, instala y resuelve dependencias y traducción. ¡Listo para jugar!"),
        ], 1):
            lbl = QLabel(f"{i}.   {s}")
            lbl.setWordWrap(True)
            lbl.setProperty("role", "dim")
            lay.addWidget(lbl)
        effects.add_shadow(card, blur=18, dy=3, alpha=120)
        return card

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
                dot=theme.ACCENT if premium else theme.SUCCESS,
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

        self._refresh_activity()

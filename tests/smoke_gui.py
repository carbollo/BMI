"""Smoke test de la GUI en modo headless (offscreen).

Construye los widgets reales para validar que la API de Qt usada existe
(enums, señales, constructores). NO abre ventana ni ejecuta el bucle de eventos.

Ejecutar:  python tests/smoke_gui.py
"""
import os
import sys
from pathlib import Path

# Debe configurarse ANTES de importar PySide6.
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("QTWEBENGINE_DISABLE_SANDBOX", "1")
os.environ.setdefault("QTWEBENGINE_CHROMIUM_FLAGS", "--no-sandbox --disable-gpu")

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


def main() -> int:
    # 1) Importar todos los módulos (detecta errores de import).
    import nexus_mod_installer.models
    import nexus_mod_installer.config
    import nexus_mod_installer.nxm
    import nexus_mod_installer.nexus_api
    import nexus_mod_installer.nexus_graphql
    import nexus_mod_installer.downloader
    import nexus_mod_installer.archive
    import nexus_mod_installer.deploy
    import nexus_mod_installer.fomod
    import nexus_mod_installer.installer
    import nexus_mod_installer.ipc
    import nexus_mod_installer.manager
    import nexus_mod_installer.gui.webview
    import nexus_mod_installer.gui.settings_dialog
    import nexus_mod_installer.gui.main_window
    print("OK  import de todos los módulos")

    # 2) Verificar enums/señales de QtWebEngine que usamos.
    from PySide6.QtWebEngineCore import QWebEngineDownloadRequest, QWebEngineProfile
    assert hasattr(QWebEngineDownloadRequest.DownloadState, "DownloadCompleted")
    assert hasattr(QWebEngineDownloadRequest.DownloadState, "DownloadCancelled")
    assert hasattr(QWebEngineDownloadRequest.DownloadState, "DownloadInterrupted")
    assert hasattr(QWebEngineDownloadRequest, "stateChanged")
    assert hasattr(QWebEngineProfile.PersistentCookiesPolicy, "ForcePersistentCookies")
    print("OK  enums/señales de QtWebEngine")

    # 3) Construir widgets reales (sin webview, que es pesado).
    from PySide6.QtWidgets import QApplication
    from nexus_mod_installer.config import AppConfig
    from nexus_mod_installer.manager import DownloadManager
    from nexus_mod_installer.gui.settings_dialog import SettingsDialog
    from nexus_mod_installer.gui.main_window import DownloadsPanel
    from nexus_mod_installer.gui.mods_panel import ModsPanel
    from nexus_mod_installer.gui.home_panel import HomePanel

    app = QApplication(sys.argv)  # noqa: F841
    config = AppConfig()  # config en memoria (no toca tu disco real más allá de carpetas tmp)

    dlg = SettingsDialog(config)
    assert dlg is not None
    print("OK  SettingsDialog construido")

    manager = DownloadManager(config)
    dp = DownloadsPanel(manager, window=None)  # type: ignore[arg-type]
    mp = ModsPanel(manager)
    hp = HomePanel(manager)
    assert dp.table.columnCount() == 5
    assert mp.mods_table.columnCount() == 5
    assert mp.tabs.count() == 5                 # Mods/Prioridad/Plugins/Conflictos/Perfiles
    print("OK  DownloadsPanel + ModsPanel + HomePanel construidos")

    # 4) Simular el ciclo de señales de una tarea (sin red).
    from nexus_mod_installer.models import DownloadTask, TaskStatus
    t = DownloadTask(game_domain="skyrimspecialedition", mod_id=266, file_id=1000,
                     mod_name="SkyUI", file_name="SkyUI_5_2_SE.7z")
    manager.task_added.emit(t)
    app.processEvents()
    assert dp.table.rowCount() == 1
    t.status = TaskStatus.NEEDS_CLICK
    manager.task_updated.emit(t)
    app.processEvents()
    # En NEEDS_CLICK debe aparecer el botón de acción.
    assert dp.table.cellWidget(0, 4) is not None
    print("OK  señales task_added/task_updated actualizan la tabla")

    # 5) Asistente FOMOD con dos pasos (uno condicionado por flag).
    from nexus_mod_installer.fomod import (
        FomodConfig, FomodStep, FomodGroup, FomodPlugin, Dependency,
    )
    from nexus_mod_installer.gui.fomod_dialog import FomodDialog

    cfg = FomodConfig(module_name="Demo", pkg_root=".")
    cfg.steps.append(FomodStep(name="Paso 1", groups=[
        FomodGroup(name="Calidad", type="SelectExactlyOne", plugins=[
            FomodPlugin(name="1K", type="Optional"),
            FomodPlugin(name="2K", type="Recommended", condition_flags={"q": "2k"}),
        ]),
    ]))
    cfg.steps.append(FomodStep(
        name="Paso 2 (solo si 2K)",
        visible=Dependency(operator="And", flags=[("q", "2k")]),
        groups=[FomodGroup(name="Extras", type="SelectAny",
                           plugins=[FomodPlugin(name="ENB")])],
    ))
    fd = FomodDialog(cfg)
    assert fd.stack.count() == 2
    assert fd._visited == [0]
    assert fd.get_selection() == []
    print("OK  FomodDialog (asistente con 2 pasos) construido")

    # 6) Guardia: no debe existir ningún módulo de automatización de la web; la descarga
    #    usa solo la API oficial / nxm:// (manager.enqueue_mod para Premium por URL).
    import importlib
    try:
        importlib.import_module("nexus_mod_installer.gui.bot")
        raise AssertionError("no debe existir un módulo de automatización web")
    except ModuleNotFoundError:
        pass
    assert hasattr(manager, "enqueue_mod")          # descarga automática Premium por URL
    print("OK  sin módulo de automatización; manager.enqueue_mod disponible")

    # 7) Dedupe por mod: un mod ya instalado no se vuelve a encolar (evita doble instalación).
    from nexus_mod_installer.models import InstalledMod
    manager.store.mods[99999] = InstalledMod(mod_id=99999, name="YaInstalado")
    before = len(manager.tasks)
    manager.enqueue_task(
        DownloadTask(game_domain="skyrimspecialedition", mod_id=99999, file_id=5)
    )
    assert len(manager.tasks) == before          # no se encoló (ya instalado)
    # En cola: un segundo intento del mismo mod en vuelo también se omite.
    manager._inflight_mods.add(88888)
    manager.enqueue_task(
        DownloadTask(game_domain="skyrimspecialedition", mod_id=88888, file_id=7)
    )
    assert len(manager.tasks) == before
    print("OK  dedupe por mod (instalado / en cola no se re-encola)")

    # 7b) Cola múltiple: panel con multi-selección + manager.remove_tasks quita varias.
    from PySide6.QtWidgets import QAbstractItemView
    assert dp.table.selectionMode() == QAbstractItemView.SelectionMode.ExtendedSelection
    t_err = DownloadTask(game_domain="skyrimspecialedition", mod_id=70001, file_id=11,
                         status=TaskStatus.ERROR)
    manager.tasks.append(t_err); manager._seen.add((70001, 11))
    assert manager.remove_tasks([t_err]) == 1
    assert t_err not in manager.tasks and t_err.cancelled
    # Una tarea descargando NO se puede quitar.
    t_dl = DownloadTask(game_domain="skyrimspecialedition", mod_id=70002, file_id=12,
                        status=TaskStatus.DOWNLOADING)
    manager.tasks.append(t_dl)
    assert manager.remove_tasks([t_dl]) == 0 and t_dl in manager.tasks
    manager.tasks.remove(t_dl)
    print("OK  cola múltiple (multi-selección + remove_tasks respeta descargas en curso)")

    # 8) Diálogos nuevos + gestor de mods con datos.
    from nexus_mod_installer.gui.first_run_wizard import FirstRunWizard
    from nexus_mod_installer.gui.mod_details_dialog import ModDetailsDialog
    wiz = FirstRunWizard(config)
    assert wiz.stack.count() == 3
    sample = InstalledMod(mod_id=5, name="Demo Mod", version="1.0", plugins=["Demo.esp"],
                          deployed_files=["meshes/x.nif"], size_bytes=2048, enabled=True)
    det = ModDetailsDialog(sample)
    assert det is not None
    manager.store.mods[5] = sample
    mp.refresh()
    assert mp.mods_table.rowCount() >= 1                 # el mod aparece en el gestor
    from PySide6.QtWidgets import QTabWidget
    assert dlg.findChild(QTabWidget) is not None         # SettingsDialog tiene pestañas
    print("OK  FirstRunWizard + ModDetailsDialog + ModsPanel con datos")

    manager.shutdown()
    print("\n[OK] smoke test de GUI superado.")
    return 0


if __name__ == "__main__":
    sys.exit(main())

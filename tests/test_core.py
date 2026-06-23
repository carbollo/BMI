"""Pruebas de la lógica pura (no requieren PySide6).

Ejecutar:  python -m tests.test_core
"""
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from nexus_mod_installer.models import NxmLink, DownloadTask
from nexus_mod_installer.nexus_graphql import parse_collection_url
from nexus_mod_installer import deploy, fomod


def test_nxm_parse_full():
    url = "nxm://skyrimspecialedition/mods/266/files/1000123?key=ABC123&expires=1699999999&user_id=42"
    link = NxmLink.parse(url)
    assert link.game_domain == "skyrimspecialedition"
    assert link.mod_id == 266
    assert link.file_id == 1000123
    assert link.key == "ABC123"
    assert link.expires == 1699999999
    assert link.user_id == 42
    assert link.has_credentials is True
    print("OK  nxm parse (completo)")


def test_nxm_parse_no_key():
    url = "nxm://skyrimspecialedition/mods/12/files/34"
    link = NxmLink.parse(url)
    assert link.mod_id == 12 and link.file_id == 34
    assert link.has_credentials is False
    task = DownloadTask.from_nxm(link)
    assert task.has_credentials is False
    print("OK  nxm parse (sin credenciales)")


def test_collection_url():
    assert parse_collection_url(
        "https://next.nexusmods.com/skyrimspecialedition/collections/abcdef"
    ) == ("abcdef", None)
    assert parse_collection_url(
        "https://www.nexusmods.com/games/skyrimspecialedition/collections/xyz123/revisions/4"
    ) == ("xyz123", 4)
    assert parse_collection_url("nxm://skyrimspecialedition/collections/qwerty/revisions/7") == (
        "qwerty", 7,
    )
    assert parse_collection_url("https://www.nexusmods.com/skyrimspecialedition/mods/266") is None
    print("OK  parseo de URLs de colección")


def test_find_data_root_flat():
    with tempfile.TemporaryDirectory() as d:
        root = Path(d)
        (root / "meshes").mkdir()
        (root / "MiMod.esp").write_text("x")
        assert deploy.find_data_root(root) == root
        plugins = deploy.list_plugins(root)
        assert plugins == ["MiMod.esp"]
    print("OK  find_data_root (estructura plana)")


def test_find_data_root_nested():
    with tempfile.TemporaryDirectory() as d:
        root = Path(d)
        inner = root / "00 Core"
        (inner / "textures").mkdir(parents=True)
        (inner / "Cool.esp").write_text("x")
        assert deploy.find_data_root(root) == inner
    print("OK  find_data_root (anidado)")


def test_enable_plugins():
    with tempfile.TemporaryDirectory() as d:
        p = Path(d) / "plugins.txt"
        deploy.enable_plugins(p, ["A.esp", "B.esp"])
        deploy.enable_plugins(p, ["A.esp", "C.esp"])  # A no debe duplicarse
        lines = p.read_text(encoding="utf-8").splitlines()
        assert lines == ["*A.esp", "*B.esp", "*C.esp"], lines
        deploy.disable_plugins(p, ["B.esp"])
        lines = p.read_text(encoding="utf-8").splitlines()
        assert lines == ["*A.esp", "*C.esp"], lines
    print("OK  plugins.txt (activar/desactivar/sin duplicar)")


def test_deploy_and_undeploy():
    with tempfile.TemporaryDirectory() as d:
        src = Path(d) / "modsrc"
        (src / "meshes" / "x").mkdir(parents=True)
        (src / "meshes" / "x" / "a.nif").write_text("nif")
        (src / "Plug.esp").write_text("esp")
        data = Path(d) / "Data"
        data.mkdir()
        deployed = deploy.deploy(src, data, method="copy")
        assert "Plug.esp" in deployed
        assert (data / "meshes" / "x" / "a.nif").is_file()
        n = deploy.undeploy(deployed, data)
        assert n == len(deployed)
        assert not (data / "Plug.esp").exists()
    print("OK  deploy + undeploy (copia)")


def test_fomod_auto_pick_group():
    from nexus_mod_installer.fomod import FomodGroup, FomodPlugin, auto_pick_group
    g = FomodGroup(name="g", type="SelectExactlyOne", plugins=[
        FomodPlugin(name="A", type="Optional"),
        FomodPlugin(name="B", type="Recommended"),
    ])
    sel = auto_pick_group(g)
    assert len(sel) == 1 and sel[0].name == "B"
    g2 = FomodGroup(name="g2", type="SelectAll",
                    plugins=[FomodPlugin(name="X"), FomodPlugin(name="Y")])
    assert len(auto_pick_group(g2)) == 2
    g3 = FomodGroup(name="g3", type="SelectAtMostOne",
                    plugins=[FomodPlugin(name="X", type="Optional")])
    assert auto_pick_group(g3) == []   # nada recomendado -> ninguno
    print("OK  FOMOD selección por tipo de grupo")


def test_fomod_parse_and_install():
    import xml.etree.ElementTree as ET  # noqa: F401
    with tempfile.TemporaryDirectory() as d:
        pkg = Path(d) / "pkg"
        (pkg / "fomod").mkdir(parents=True)
        (pkg / "Required").mkdir()
        (pkg / "Required" / "req.txt").write_text("req")
        (pkg / "OptA").mkdir(); (pkg / "OptA" / "a.txt").write_text("a")
        (pkg / "OptB").mkdir(); (pkg / "OptB" / "b.txt").write_text("b")
        (pkg / "fomod" / "ModuleConfig.xml").write_text(
            """<config>
              <moduleName>Test</moduleName>
              <requiredInstallFiles><folder source="Required" destination=""/></requiredInstallFiles>
              <installSteps><installStep name="Main"><optionalFileGroups>
                <group name="Choose" type="SelectExactlyOne"><plugins>
                  <plugin name="A"><files><folder source="OptA" destination=""/></files>
                    <typeDescriptor><type name="Optional"/></typeDescriptor></plugin>
                  <plugin name="B"><files><folder source="OptB" destination=""/></files>
                    <typeDescriptor><type name="Recommended"/></typeDescriptor></plugin>
                </plugins></group>
              </optionalFileGroups></installStep></installSteps>
            </config>""",
            encoding="utf-8",
        )
        cfg = fomod.parse_config(str(pkg / "fomod" / "ModuleConfig.xml"))
        assert cfg.module_name == "Test"
        assert cfg.has_choices
        selection = fomod.auto_select(cfg)              # debe elegir B (recomendada)
        assert [p.name for p in selection] == ["B"]
        staging = Path(d) / "out"
        data_root, notes = fomod.install_selection(cfg, selection, str(staging))
        assert (data_root / "req.txt").is_file()        # obligatorio
        assert (data_root / "b.txt").is_file()          # opción B
        assert not (data_root / "a.txt").exists()       # opción A no elegida
    print("OK  FOMOD parseo + instalación por selección")


def test_translation_heuristics():
    from nexus_mod_installer.translations import looks_spanish, name_overlap
    assert looks_spanish("Apocalypse - Spanish Translation")
    assert looks_spanish("Apocalypse Traducción al Español")
    assert looks_spanish("SkyUI - Castellano")
    assert not looks_spanish("Apocalypse - Magic of Skyrim")
    assert not looks_spanish("Apocalypse - French Translation")
    assert name_overlap("Apocalypse Magic of Skyrim", "Apocalypse Magic - Traduccion Español") >= 0.5
    assert name_overlap("Immersive Armors", "Immersive Armors - Spanish") >= 0.5
    assert name_overlap("Immersive Armors", "Some Random Mod Español") < 0.5
    # La traducción directa debe puntuar más que un mod que solo menciona el original.
    assert name_overlap("SkyUI", "SkyUI - Spanish") > \
        name_overlap("SkyUI", "Crafting Categories for SkyUI - Spanish")
    print("OK  heurísticas de traducción (español + solapamiento de nombre)")


def test_spanish_file_in_mod():
    from nexus_mod_installer import translations

    class _FakeGQ:
        def mod_files(self, gd, mid):
            return [
                {"fileId": 1, "name": "Main File", "description": "7z archive"},
                {"fileId": 2, "name": "Spanish Translation", "description": "Traducción al español"},
                {"fileId": 3, "name": "Loose ESP version", "description": "no language"},
            ]

    fq = _FakeGQ()
    assert translations.find_spanish_file_in_mod(fq, "skyrimspecialedition", 100, 1) == (2, "Spanish Translation")
    # "Loose ESP" NO debe contar como español (evita falsos positivos con .esp)
    assert translations.find_spanish_file_in_mod(fq, "skyrimspecialedition", 100, 2) is None
    print("OK  detección de archivo en español dentro del mod")


def test_fomod_prefer_spanish():
    from nexus_mod_installer.fomod import FomodGroup, FomodPlugin, auto_pick_group
    g = FomodGroup(name="Idioma", type="SelectExactlyOne", plugins=[
        FomodPlugin(name="English", type="Recommended"),
        FomodPlugin(name="Español", type="Optional"),
    ])
    assert auto_pick_group(g)[0].name == "English"                    # sin preferencia
    assert auto_pick_group(g, prefer_spanish=True)[0].name == "Español"  # con preferencia
    print("OK  FOMOD prefiere la opción en español cuando se pide")


def test_scanner_detects_all_plugins():
    from nexus_mod_installer import scanner
    with tempfile.TemporaryDirectory() as d:
        data = Path(d) / "Data"
        data.mkdir()
        for f in ["Skyrim.esm", "ccBGSSSE001-Fish.esm", "CCOR.esp", "SkyUI.esp",
                  "MiModExterno.esp", "Lux.esm", "Apronto.esl"]:
            (data / f).write_text("x")
        plugins_txt = Path(d) / "plugins.txt"
        plugins_txt.write_text("*SkyUI.esp\nMiModExterno.esp\n*Lux.esm\n*CCOR.esp\n", encoding="utf-8")

        mods = scanner.scan_installed(str(data), str(plugins_txt), managed_plugins={"SkyUI.esp"})
        by = {m.name: m for m in mods}
        # Master base: siempre activo, categoría vanilla.
        assert by["Skyrim.esm"].category == "vanilla" and by["Skyrim.esm"].enabled
        # Creation Club: categoría 'cc', estado SEGÚN plugins.txt (no forzado activo).
        assert by["ccBGSSSE001-Fish.esm"].category == "cc"
        assert by["ccBGSSSE001-Fish.esm"].enabled is False   # no está en plugins.txt
        # 'CCOR.esp' empieza por 'cc' pero NO es Creation Club -> no debe clasificarse cc.
        assert by["CCOR.esp"].category in ("gestionado", "externo")
        assert by["CCOR.esp"].enabled is True                # activado en plugins.txt
        assert by["SkyUI.esp"].category == "gestionado" and by["SkyUI.esp"].enabled
        assert by["MiModExterno.esp"].category == "externo" and by["MiModExterno.esp"].enabled is False
        assert by["Lux.esm"].category == "externo" and by["Lux.esm"].enabled and by["Lux.esm"].is_master
        assert by["Apronto.esl"].is_master and by["Apronto.esl"].enabled is False
        # Orden de carga efectivo: master base primero, otros masters antes que los .esp.
        assert by["Skyrim.esm"].load_index == 0
        assert by["Lux.esm"].load_index < by["SkyUI.esp"].load_index
        assert by["MiModExterno.esp"].load_index == -1       # inactivo -> sin índice
    print("OK  escáner: categorías, CC activable, orden de carga efectivo")


def test_scanner_bom_safe():
    from nexus_mod_installer import scanner
    with tempfile.TemporaryDirectory() as d:
        pt = Path(d) / "plugins.txt"
        # plugins.txt con BOM y primer plugin desactivado
        pt.write_text("﻿A.esp\n*B.esp\n", encoding="utf-8")
        enabled, _ = scanner.parse_plugins_txt(str(pt))
        assert "a.esp" in enabled and enabled["a.esp"] is False   # el BOM no rompe la 1ª línea
        # activar A no debe duplicar la entrada
        scanner.set_plugin_enabled(str(pt), "A.esp", True)
        enabled2, order2 = scanner.parse_plugins_txt(str(pt))
        assert enabled2["a.esp"] is True
        names = [l.lstrip("*").strip().lower() for l in pt.read_text(encoding="utf-8-sig").splitlines() if l.strip()]
        assert names.count("a.esp") == 1                          # sin duplicado
    print("OK  escáner tolera BOM en plugins.txt (sin corromper ni duplicar)")


def test_scanner_write_load_order():
    from nexus_mod_installer import scanner
    with tempfile.TemporaryDirectory() as d:
        pt = Path(d) / "plugins.txt"
        pt.write_text("*A.esp\n*B.esp\nC.esp\n", encoding="utf-8")
        # Reordenar: B, A (C inactivo no se pasa -> se conserva al final). NEW no está en
        # plugins.txt -> debe escribirse INACTIVO (no activar lo que el usuario no marcó).
        scanner.write_load_order(str(pt), ["B.esp", "A.esp", "NEW.esp"])
        lines = pt.read_text(encoding="utf-8-sig").splitlines()
        assert lines[0] == "*B.esp" and lines[1] == "*A.esp"   # nuevo orden, estado conservado
        assert "NEW.esp" in lines and "*NEW.esp" not in lines  # desconocido -> inactivo
        assert "C.esp" in lines                                # entrada no incluida se conserva
    print("OK  scanner.write_load_order (reordena; desconocidos quedan inactivos)")


def test_conflicts_detection():
    from nexus_mod_installer import conflicts
    from nexus_mod_installer.models import InstalledMod
    a = InstalledMod(mod_id=1, name="ModA", deployed_files=["meshes/x.nif", "textures/a.dds"],
                     installed_at=100.0, enabled=True)
    b = InstalledMod(mod_id=2, name="ModB", deployed_files=["meshes/x.nif"],
                     installed_at=200.0, enabled=True)
    c = InstalledMod(mod_id=3, name="ModC", deployed_files=["meshes/x.nif"],
                     installed_at=150.0, enabled=False)  # desactivado -> no cuenta
    res = conflicts.find_conflicts([a, b, c])
    assert len(res) == 1
    assert res[0].rel_path == "meshes/x.nif"
    assert res[0].winner == "ModB"             # el más reciente (200) gana
    assert set(res[0].mods) == {"ModA", "ModB"}
    print("OK  conflicts.find_conflicts (gana el más reciente; ignora desactivados)")


def test_profiles_roundtrip():
    import os
    from nexus_mod_installer import profiles
    prev = os.environ.get("APPDATA")
    with tempfile.TemporaryDirectory() as d:
        os.environ["APPDATA"] = d   # redirige app_data_dir a temporal
        try:
            store = profiles.ProfileStore()
            pt = Path(d) / "plugins.txt"
            pt.write_text("*A.esp\nB.esp\n", encoding="utf-8")
            store.save_from("MiPerfil", str(pt))
            assert store.exists("MiPerfil")
            # Cambiar plugins.txt y aplicar el perfil -> vuelve al estado guardado
            pt.write_text("*Z.esp\n", encoding="utf-8")
            assert store.apply_to("MiPerfil", str(pt))
            assert "*A.esp" in pt.read_text(encoding="utf-8-sig")
            # Nombre con caracteres especiales: se sanea; save_from devuelve el nombre real.
            prof = store.save_from("Build: v2", str(pt))
            assert prof.name == "Build_ v2"          # = nombre en disco (Profile.name)
            assert store.exists("Build: v2")         # exists() reconoce el nombre crudo
        finally:
            if prev is not None:
                os.environ["APPDATA"] = prev
            else:
                os.environ.pop("APPDATA", None)
    print("OK  profiles (guardar/aplicar/listar)")


def test_set_mod_enabled():
    import tempfile
    from nexus_mod_installer.config import AppConfig
    from nexus_mod_installer.installer import Installer, InstalledModsStore
    from nexus_mod_installer.models import InstalledMod
    with tempfile.TemporaryDirectory() as d:
        game = Path(d) / "Data"; game.mkdir()
        modsrc = Path(d) / "modsrc"; modsrc.mkdir()
        (modsrc / "Cosa.esp").write_text("esp")
        (modsrc / "meshes").mkdir(); (modsrc / "meshes" / "a.nif").write_text("nif")
        pt = Path(d) / "plugins.txt"
        cfg = AppConfig(game_data_path=str(game), plugins_txt_path=str(pt),
                        mods_dir=str(Path(d) / "mods"), deploy_method="copy")
        store = InstalledModsStore(cfg)
        inst = Installer(cfg, store)
        # Mod ya "instalado" (desplegado) que vamos a desactivar/activar
        from nexus_mod_installer import deploy
        deployed = deploy.deploy(modsrc, game, "copy")
        mod = InstalledMod(mod_id=10, name="Cosa", install_dir=str(modsrc),
                           deployed_files=deployed, plugins=["Cosa.esp"], enabled=True)
        store.add(mod)
        # Desactivar -> se retiran los archivos de Data
        assert inst.set_mod_enabled(10, False)
        assert not (game / "Cosa.esp").exists()
        assert store.get(10).enabled is False
        # Activar -> vuelven
        assert inst.set_mod_enabled(10, True)
        assert (game / "Cosa.esp").exists()
        assert store.get(10).enabled is True
    print("OK  installer.set_mod_enabled (repliega/despliega sin desinstalar)")


def test_scanner_toggle_plugin():
    from nexus_mod_installer import scanner
    with tempfile.TemporaryDirectory() as d:
        pt = Path(d) / "plugins.txt"
        pt.write_text("*A.esp\nB.esp\n", encoding="utf-8")
        scanner.set_plugin_enabled(str(pt), "B.esp", True)    # activar B
        scanner.set_plugin_enabled(str(pt), "A.esp", False)   # desactivar A
        enabled, order = scanner.parse_plugins_txt(str(pt))
        assert enabled["b.esp"] is True and enabled["a.esp"] is False
        assert order["a.esp"] == 0 and order["b.esp"] == 1   # orden preservado
        scanner.set_plugin_enabled(str(pt), "C.esp", True)    # nuevo -> al final, activo
        enabled2, _ = scanner.parse_plugins_txt(str(pt))
        assert enabled2["c.esp"] is True
    print("OK  escáner activa/desactiva plugins conservando orden")


def test_games_registry():
    from nexus_mod_installer import games
    assert games.get("newvegas").game_id == 130          # dominio correcto (no falloutnv)
    assert games.get("fallout4").script_extender == "F4SE"
    assert games.get("fallout4").loader_exes == ("f4se_loader.exe",)
    assert games.get("skyrimspecialedition").star_prefix is True
    assert games.get("newvegas").star_prefix is False     # FNV ordena por timestamp
    assert games.get("morrowind").data_subfolder == "Data Files"
    assert "newvegas" in [g.key for g in games.all_games()]
    # SE y AE separados pero comparten dominio de Nexus.
    ae = games.get("skyrimae")
    assert ae.key == "skyrimae" and ae.domain == "skyrimspecialedition" and ae.game_id == 1704
    assert ae.key != ae.domain
    assert games.get("skyrimspecialedition").domain == "skyrimspecialedition"
    from nexus_mod_installer.nexus_graphql import GAME_IDS
    assert GAME_IDS["newvegas"] == 130 and "falloutnv" not in GAME_IDS
    assert GAME_IDS["skyrimspecialedition"] == 1704     # indexado por dominio
    print("OK  registro de juegos (newvegas/fo4/morrowind + SE/AE + GAME_IDS)")


def test_scanner_nonstar_game():
    from nexus_mod_installer import scanner, games
    with tempfile.TemporaryDirectory() as d:
        data = Path(d) / "Data"; data.mkdir()
        for f in ["FalloutNV.esm", "DeadMoney.esm", "MiMod.esp", "Otro.esp"]:
            (data / f).write_text("x")
        pt = Path(d) / "plugins.txt"
        pt.write_text("MiMod.esp\nDeadMoney.esm\n", encoding="utf-8")  # sin '*': listado=activo
        g = games.get("newvegas")
        by = {m.name: m for m in scanner.scan_installed(str(data), str(pt), game=g)}
        assert by["FalloutNV.esm"].category == "vanilla" and by["FalloutNV.esm"].enabled
        assert by["MiMod.esp"].enabled is True           # listado (sin '*') = activo
        assert by["Otro.esp"].enabled is False           # no listado = inactivo
        # toggle sin '*': desactivar = quitar la línea; activar = añadir
        scanner.set_plugin_enabled(str(pt), "MiMod.esp", False, star_prefix=False)
        scanner.set_plugin_enabled(str(pt), "Otro.esp", True, star_prefix=False)
        en, _ = scanner.parse_plugins_txt(str(pt), star_prefix=False)
        assert "mimod.esp" not in en and en.get("otro.esp") is True
        assert "*" not in pt.read_text(encoding="utf-8-sig")   # nunca escribe '*'
        # Un '*' suelto en un archivo sin-star se normaliza a la misma clave (sin perder el plugin).
        en2, _ = scanner.parse_plugins_txt(str(Path(d) / "no_existe.txt"), star_prefix=False)
        assert en2 == {}
        pt.write_text("*Stray.esp\n", encoding="utf-8")
        en3, _ = scanner.parse_plugins_txt(str(pt), star_prefix=False)
        assert "stray.esp" in en3 and "*stray.esp" not in en3
    print("OK  escáner juego sin prefijo '*' (FNV: activo=presente, '*' tolerado)")


def test_config_switch_game():
    import os
    from nexus_mod_installer.config import AppConfig
    prev = os.environ.get("APPDATA")
    with tempfile.TemporaryDirectory() as d:
        os.environ["APPDATA"] = d
        try:
            cfg = AppConfig()
            cfg.game_data_path = "C:/SE/Data"; cfg.plugins_txt_path = "C:/SE/plugins.txt"
            cfg.switch_game("fallout4")
            assert cfg.game_domain == "fallout4" and cfg.game().script_extender == "F4SE"
            cfg.game_data_path = "C:/FO4/Data"
            cfg.switch_game("skyrimspecialedition")      # recupera rutas SE guardadas
            assert cfg.game_data_path == "C:/SE/Data"
            cfg.switch_game("fallout4")                  # recupera rutas FO4 guardadas
            assert cfg.game_data_path == "C:/FO4/Data"
        finally:
            if prev is not None:
                os.environ["APPDATA"] = prev
            else:
                os.environ.pop("APPDATA", None)
    print("OK  config.switch_game (recuerda rutas por juego)")


def test_launcher_find_skse():
    from nexus_mod_installer.config import AppConfig
    from nexus_mod_installer import launcher
    with tempfile.TemporaryDirectory() as d:
        game = Path(d) / "Skyrim Special Edition"
        data = game / "Data"
        data.mkdir(parents=True)
        (game / "skse64_loader.exe").write_text("x")
        (game / "SkyrimSE.exe").write_text("x")
        cfg = AppConfig(game_data_path=str(data), skse_loader_path="")
        assert launcher.game_dir(cfg) == game
        assert launcher.find_skse(cfg) == game / "skse64_loader.exe"
        assert launcher.find_game_exe(cfg) == game / "SkyrimSE.exe"
        (game / "skse64_loader.exe").unlink()
        assert launcher.find_skse(cfg) is None      # sin SKSE -> None
    print("OK  launcher localiza SKSE64 / exe del juego")


def test_find_root_files_engine_fixes():
    with tempfile.TemporaryDirectory() as d:
        ex = Path(d) / "_extracted"; ex.mkdir()
        for f in ["d3dx9_42.dll", "tbb.dll", "tbbmalloc.dll"]:   # Engine Fixes parte 2
            (ex / f).write_text("x")
        names = sorted(rel for _, rel in deploy.find_root_files(str(ex)))
        assert names == ["d3dx9_42.dll", "tbb.dll", "tbbmalloc.dll"], names
    print("OK  find_root_files (Engine Fixes parte 2 -> raíz)")


def test_find_root_files_root_folder_and_enb():
    with tempfile.TemporaryDirectory() as d:
        ex = Path(d) / "_extracted"; ex.mkdir()
        (ex / "Root").mkdir()                                    # convención MO2/Vortex
        (ex / "Root" / "binkw64.dll").write_text("x")
        (ex / "Root" / "subdir").mkdir()
        (ex / "Root" / "subdir" / "extra.dll").write_text("x")
        (ex / "enbseries").mkdir()                               # carpeta conservada
        (ex / "enbseries" / "weather.ini").write_text("x")
        (ex / "d3d11.dll").write_text("x")                       # wrapper ENB suelto
        (ex / "enblocal.ini").write_text("x")
        (ex / "meshes").mkdir(); (ex / "meshes" / "a.nif").write_text("x")  # Data: NO entra
        rf = {rel for _, rel in deploy.find_root_files(str(ex))}
        assert "binkw64.dll" in rf                  # Root/ aplanado
        assert "subdir/extra.dll" in rf             # Root/ recursivo, sin prefijo 'Root'
        assert "enbseries/weather.ini" in rf        # carpeta conservada
        assert "d3d11.dll" in rf and "enblocal.ini" in rf
        assert not any("meshes" in r for r in rf)   # Data nunca se trata como raíz
    print("OK  find_root_files (Root/, enbseries/, wrappers ENB)")


def test_find_root_files_skse_pattern():
    with tempfile.TemporaryDirectory() as d:
        ex = Path(d) / "_extracted"; ex.mkdir()
        (ex / "skse64_loader.exe").write_text("x")
        (ex / "skse64_1_6_640.dll").write_text("x")
        (ex / "Data").mkdir(); (ex / "Data" / "Scripts").mkdir()
        (ex / "Data" / "Scripts" / "x.pex").write_text("x")
        rf = {rel for _, rel in deploy.find_root_files(str(ex), extra_names=("skse64_loader.exe",))}
        assert "skse64_loader.exe" in rf and "skse64_1_6_640.dll" in rf
        assert not any(r.lower().startswith("data") for r in rf)
    print("OK  find_root_files (runtime skse64_* -> raíz)")


def test_deploy_root_excludes_from_data():
    with tempfile.TemporaryDirectory() as d:
        ex = Path(d) / "_extracted"; ex.mkdir()
        (ex / "d3dx9_42.dll").write_text("preloader")
        (ex / "Plug.esp").write_text("esp")
        data = Path(d) / "Skyrim SE" / "Data"; data.mkdir(parents=True)
        groot = data.parent
        root_files = deploy.find_root_files(str(ex))
        deployed = deploy.deploy(ex, data, method="copy", exclude=[s for s, _ in root_files])
        assert "Plug.esp" in deployed
        assert "d3dx9_42.dll" not in deployed and not (data / "d3dx9_42.dll").exists()
        root_dep = deploy.deploy_root(root_files, groot, method="copy")
        assert (groot / "d3dx9_42.dll").is_file()               # va a la raíz
        n = deploy.undeploy_root(root_dep, groot)
        assert n == 1 and not (groot / "d3dx9_42.dll").exists()
    print("OK  deploy: raíz va junto al .exe y se excluye de Data; undeploy_root limpia")


def test_installer_root_files_end_to_end():
    import zipfile
    from nexus_mod_installer.config import AppConfig
    from nexus_mod_installer.installer import Installer, InstalledModsStore
    from nexus_mod_installer.models import DownloadTask
    with tempfile.TemporaryDirectory() as d:
        game = Path(d) / "Skyrim Special Edition"
        data = game / "Data"; data.mkdir(parents=True)
        (game / "SkyrimSE.exe").write_text("x")
        arc = Path(d) / "ef2.zip"
        with zipfile.ZipFile(arc, "w") as z:                    # Engine Fixes parte 2
            z.writestr("d3dx9_42.dll", "preloader")
            z.writestr("tbb.dll", "tbb")
            z.writestr("tbbmalloc.dll", "tbbm")
        cfg = AppConfig(game_data_path=str(data), plugins_txt_path=str(Path(d) / "plugins.txt"),
                        mods_dir=str(Path(d) / "mods"), deploy_method="copy")
        store = InstalledModsStore(cfg); inst = Installer(cfg, store)
        task = DownloadTask(game_domain="skyrimspecialedition", mod_id=17230, file_id=1,
                            mod_name="Engine Fixes part 2", archive_path=str(arc))
        mod = inst.install(task)
        assert (game / "d3dx9_42.dll").is_file()               # raíz del juego
        assert not (data / "d3dx9_42.dll").exists()            # NO en Data
        assert sorted(Path(r).name for r in mod.deployed_root_files) == \
            ["d3dx9_42.dll", "tbb.dll", "tbbmalloc.dll"]
        assert mod.deployed_files == []                        # nada va a Data
        inst.set_mod_enabled(17230, False)                     # desactivar → retira de raíz
        assert not (game / "d3dx9_42.dll").exists()
        inst.set_mod_enabled(17230, True)                      # reactivar → vuelve
        assert (game / "d3dx9_42.dll").is_file()
        inst.uninstall(17230)                                  # desinstalar → limpia raíz
        assert not (game / "d3dx9_42.dll").exists()
    print("OK  installer: Engine Fixes parte 2 -> raíz (install/activar/desactivar/desinstalar)")


def test_oauth_pkce_and_flow():
    import base64 as _b64, hashlib as _hl
    from nexus_mod_installer import oauth
    # PKCE: challenge = base64url(SHA256(verifier)), método S256
    v, c = oauth.generate_pkce()
    assert 43 <= len(v) <= 128
    expect = _b64.urlsafe_b64encode(_hl.sha256(v.encode()).digest()).rstrip(b"=").decode()
    assert c == expect
    # Sin CLIENT_ID/REDIRECT_URI -> error claro (no se puede iniciar el flujo)
    try:
        oauth.LoginFlow(); assert False, "debería exigir configuración"
    except oauth.OAuthNotConfigured:
        pass
    # Configurado (monkeypatch de los huecos del registro)
    oauth.CLIENT_ID, oauth.REDIRECT_URI = "abc123", "https://127.0.0.1:5599/callback"
    try:
        flow = oauth.LoginFlow()
        url = flow.authorize_url()
        assert url.startswith("https://users.nexusmods.com/oauth/authorize?")
        for needle in ("response_type=code", "client_id=abc123", "code_challenge_method=S256",
                       "code_challenge=", "state="):
            assert needle in url, needle
        # is_redirect detecta nuestra redirect_uri y nada más
        assert oauth.LoginFlow.is_redirect("https://127.0.0.1:5599/callback?code=X&state=Y")
        assert not oauth.LoginFlow.is_redirect("https://users.nexusmods.com/oauth/authorize")
        # parse_code valida state y devuelve el code
        assert flow.parse_code(f"https://127.0.0.1:5599/callback?code=THECODE&state={flow.state}") == "THECODE"
        for bad in (f"https://127.0.0.1:5599/callback?code=X&state=wrong",
                    f"https://127.0.0.1:5599/callback?error=access_denied&state={flow.state}"):
            try:
                flow.parse_code(bad); assert False, "debería fallar"
            except oauth.OAuthError:
                pass
    finally:
        oauth.CLIENT_ID, oauth.REDIRECT_URI = "", ""
    print("OK  oauth PKCE + URL de autorización + parseo de redirect (state/error)")


def test_oauth_token_store():
    import os, time as _t
    from nexus_mod_installer import oauth
    prev = os.environ.get("APPDATA")
    with tempfile.TemporaryDirectory() as d:
        os.environ["APPDATA"] = d
        try:
            tok = oauth.OAuthToken.from_response(
                {"access_token": "AT", "refresh_token": "RT", "expires_in": 3600,
                 "token_type": "Bearer", "scope": "public"})
            assert not tok.is_expired
            assert tok.authorization_header()["Authorization"] == "Bearer AT"
            store = oauth.TokenStore()
            store.save(tok)
            loaded = store.load()
            assert loaded.access_token == "AT" and loaded.refresh_token == "RT"
            assert oauth.OAuthToken(access_token="x", expires_at=_t.time() - 10).is_expired
            store.clear()
            assert store.load() is None
        finally:
            if prev is not None:
                os.environ["APPDATA"] = prev
            else:
                os.environ.pop("APPDATA", None)
    print("OK  oauth TokenStore (guardar/cargar + caducidad)")


def main():
    tests = [v for k, v in globals().items() if k.startswith("test_")]
    for t in tests:
        t()
    print(f"\n[OK] {len(tests)} pruebas pasaron.")


if __name__ == "__main__":
    main()

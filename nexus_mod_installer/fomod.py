"""Motor FOMOD: parseo del instalador, modelo de datos e instalación.

Soporta dos modos:
  - Interactivo: se construye un modelo (pasos/grupos/opciones) que la GUI presenta
    como asistente; el usuario elige y aquí se instalan los archivos seleccionados.
  - Automático: se eligen las opciones obligatorias + recomendadas sin interfaz.

Cubre: requiredInstallFiles, installSteps/optionalFileGroups/group/plugin (todos los
tipos de grupo), conditionFlags, visibilidad de pasos por flags y conditionalFileInstalls
por flags. No evalúa dependencias de archivos del juego (fileDependency) — se avisa.
"""
from __future__ import annotations

import shutil
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from pathlib import Path

from .translations import looks_language


# ---------------------------------------------------------------------------
# Modelo de datos
# ---------------------------------------------------------------------------
@dataclass
class FomodFile:
    source: str
    destination: str
    is_folder: bool
    priority: int = 0


@dataclass
class Dependency:
    operator: str = "And"                      # "And" / "Or"
    flags: list[tuple[str, str]] = field(default_factory=list)  # (flag, valor)
    has_unsupported: bool = False              # había fileDependency u otra no evaluable

    def evaluate(self, flags_state: dict[str, str]) -> bool:
        if not self.flags:
            return True  # sin condiciones de flag -> visible/instalable
        checks = [flags_state.get(name, "") == value for name, value in self.flags]
        if self.operator.lower() == "or":
            return any(checks)
        return all(checks)


@dataclass
class FomodPlugin:
    name: str
    description: str = ""
    image: str = ""                            # ruta absoluta o ""
    type: str = "Optional"                     # Required/Recommended/Optional/NotUsable/CouldBeUsable
    files: list[FomodFile] = field(default_factory=list)
    condition_flags: dict[str, str] = field(default_factory=dict)


@dataclass
class FomodGroup:
    name: str
    type: str                                  # SelectExactlyOne/AtMostOne/AtLeastOne/Any/All
    plugins: list[FomodPlugin] = field(default_factory=list)


@dataclass
class FomodStep:
    name: str
    visible: Dependency = field(default_factory=Dependency)
    groups: list[FomodGroup] = field(default_factory=list)


@dataclass
class ConditionalInstall:
    dependency: Dependency
    files: list[FomodFile] = field(default_factory=list)


@dataclass
class FomodConfig:
    module_name: str
    module_image: str = ""
    pkg_root: str = ""
    required_files: list[FomodFile] = field(default_factory=list)
    steps: list[FomodStep] = field(default_factory=list)
    conditional_installs: list[ConditionalInstall] = field(default_factory=list)

    @property
    def has_choices(self) -> bool:
        return any(s.groups for s in self.steps)


# ---------------------------------------------------------------------------
# Localización / utilidades XML
# ---------------------------------------------------------------------------
def find_fomod_config(extracted_dir: str) -> Path | None:
    root = Path(extracted_dir)
    for p in root.rglob("*"):
        if p.is_file() and p.name.lower() == "moduleconfig.xml" and p.parent.name.lower() == "fomod":
            return p
    return None


def _strip_ns(tag: str) -> str:
    return tag.split("}", 1)[-1] if "}" in tag else tag


def _find(el: ET.Element, name: str) -> ET.Element | None:
    for child in el:
        if _strip_ns(child.tag).lower() == name.lower():
            return child
    return None


def _findall(el: ET.Element, name: str) -> list[ET.Element]:
    return [c for c in el if _strip_ns(c.tag).lower() == name.lower()]


def _resolve_source(pkg_root: Path, source: str) -> Path | None:
    source = (source or "").replace("\\", "/").strip("/")
    if not source:
        return pkg_root
    direct = pkg_root / source
    if direct.exists():
        return direct
    current = pkg_root
    for part in source.split("/"):
        if not current.is_dir():
            return None
        match = next((c for c in current.iterdir() if c.name.lower() == part.lower()), None)
        if match is None:
            return None
        current = match
    return current


# ---------------------------------------------------------------------------
# Parseo
# ---------------------------------------------------------------------------
def _parse_files(files_el: ET.Element | None) -> list[FomodFile]:
    out: list[FomodFile] = []
    if files_el is None:
        return out
    for entry in list(files_el):
        tag = _strip_ns(entry.tag).lower()
        if tag not in ("file", "folder"):
            continue
        try:
            priority = int(entry.get("priority", "0"))
        except ValueError:
            priority = 0
        out.append(
            FomodFile(
                source=entry.get("source", ""),
                destination=entry.get("destination", ""),
                is_folder=(tag == "folder"),
                priority=priority,
            )
        )
    return out


def _parse_dependency(deps_el: ET.Element | None) -> Dependency:
    dep = Dependency()
    if deps_el is None:
        return dep
    dep.operator = deps_el.get("operator", "And")
    for child in list(deps_el):
        tag = _strip_ns(child.tag).lower()
        if tag == "flagdependency":
            dep.flags.append((child.get("flag", ""), child.get("value", "")))
        elif tag == "dependencies":
            # Anidadas: aplanamos de forma simple.
            nested = _parse_dependency(child)
            dep.flags.extend(nested.flags)
            dep.has_unsupported = dep.has_unsupported or nested.has_unsupported
        else:
            # fileDependency, gameDependency, etc. -> no evaluables aquí.
            dep.has_unsupported = True
    return dep


def _parse_type(plugin_el: ET.Element) -> str:
    td = _find(plugin_el, "typeDescriptor")
    if td is None:
        return "Optional"
    type_el = _find(td, "type")
    if type_el is not None:
        return type_el.get("name", "Optional")
    dep_type = _find(td, "dependencyType")
    if dep_type is not None:
        default = _find(dep_type, "defaultType")
        if default is not None:
            return default.get("name", "Optional")
    return "Optional"


def _parse_plugin(plugin_el: ET.Element, pkg_root: Path) -> FomodPlugin:
    desc_el = _find(plugin_el, "description")
    image_el = _find(plugin_el, "image")
    image_path = ""
    if image_el is not None and image_el.get("path"):
        resolved = _resolve_source(pkg_root, image_el.get("path", ""))
        image_path = str(resolved) if resolved and resolved.is_file() else ""

    flags: dict[str, str] = {}
    cf = _find(plugin_el, "conditionFlags")
    if cf is not None:
        for flag in _findall(cf, "flag"):
            flags[flag.get("name", "")] = (flag.text or "").strip()

    return FomodPlugin(
        name=plugin_el.get("name", "?"),
        description=(desc_el.text or "").strip() if desc_el is not None else "",
        image=image_path,
        type=_parse_type(plugin_el),
        files=_parse_files(_find(plugin_el, "files")),
        condition_flags=flags,
    )


def parse_config(config_path: str) -> FomodConfig:
    config_file = Path(config_path)
    pkg_root = config_file.parent.parent  # <pkg>/fomod/ModuleConfig.xml
    root = ET.parse(config_file).getroot()

    name_el = _find(root, "moduleName")
    img_el = _find(root, "moduleImage")
    module_image = ""
    if img_el is not None and img_el.get("path"):
        resolved = _resolve_source(pkg_root, img_el.get("path", ""))
        module_image = str(resolved) if resolved and resolved.is_file() else ""

    config = FomodConfig(
        module_name=(name_el.text or "").strip() if name_el is not None else "Mod",
        module_image=module_image,
        pkg_root=str(pkg_root),
        required_files=_parse_files(_find(root, "requiredInstallFiles")),
    )

    steps_el = _find(root, "installSteps")
    if steps_el is not None:
        for step_el in _findall(steps_el, "installStep"):
            visible = Dependency()
            vis_el = _find(step_el, "visible")
            if vis_el is not None:
                deps = _find(vis_el, "dependencies") or vis_el
                visible = _parse_dependency(deps)
            step = FomodStep(name=step_el.get("name", "Paso"), visible=visible)

            groups_container = _find(step_el, "optionalFileGroups")
            if groups_container is not None:
                for group_el in _findall(groups_container, "group"):
                    group = FomodGroup(
                        name=group_el.get("name", ""),
                        type=group_el.get("type", "SelectAny"),
                    )
                    plugins_el = _find(group_el, "plugins")
                    if plugins_el is not None:
                        for p_el in _findall(plugins_el, "plugin"):
                            group.plugins.append(_parse_plugin(p_el, pkg_root))
                    step.groups.append(group)
            config.steps.append(step)

    cond_el = _find(root, "conditionalFileInstalls")
    if cond_el is not None:
        patterns = _find(cond_el, "patterns")
        if patterns is not None:
            for pat in _findall(patterns, "pattern"):
                dep = _parse_dependency(_find(pat, "dependencies"))
                files = _parse_files(_find(pat, "files"))
                config.conditional_installs.append(ConditionalInstall(dep, files))

    return config


# ---------------------------------------------------------------------------
# Selección automática
# ---------------------------------------------------------------------------
def auto_pick_group(group: FomodGroup, prefer_spanish: bool = False,
                    prefer_language: str = "") -> list[FomodPlugin]:
    plugins = group.plugins
    if not plugins:
        return []
    usable = [p for p in plugins if p.type != "NotUsable"]

    # Si se pide un idioma y hay una opción en ese idioma, se prioriza (grupos de idioma).
    lang = prefer_language or ("es" if prefer_spanish else "")
    if lang:
        matches = [p for p in usable if looks_language(p.name, lang)]
        if matches:
            if group.type in ("SelectExactlyOne", "SelectAtMostOne"):
                return [matches[0]]
            if group.type != "SelectAll":  # multi-selección
                return matches

    recommended = [p for p in plugins if p.type == "Recommended"]
    gt = group.type

    if gt == "SelectAll":
        return usable
    if gt == "SelectExactlyOne":
        return [recommended[0]] if recommended else [usable[0] if usable else plugins[0]]
    if gt == "SelectAtMostOne":
        return [recommended[0]] if recommended else []
    if gt == "SelectAtLeastOne":
        return recommended if recommended else ([usable[0]] if usable else [])
    # SelectAny y desconocidos
    return recommended


def auto_select(config: FomodConfig, prefer_spanish: bool = False,
                prefer_language: str = "") -> list[FomodPlugin]:
    selected: list[FomodPlugin] = []
    flags: dict[str, str] = {}
    for step in config.steps:
        if not step.visible.evaluate(flags):
            continue
        for group in step.groups:
            picks = auto_pick_group(group, prefer_spanish=prefer_spanish,
                                    prefer_language=prefer_language)
            selected.extend(picks)
            for p in picks:
                flags.update(p.condition_flags)
    return selected


# ---------------------------------------------------------------------------
# Instalación a partir de una selección
# ---------------------------------------------------------------------------
def _copy_entry(src: Path, dest_root: Path, destination: str) -> None:
    destination = (destination or "").replace("\\", "/").strip("/")
    if src.is_dir():
        target_base = dest_root / destination if destination else dest_root
        for f in src.rglob("*"):
            if f.is_dir():
                continue
            dst = target_base / f.relative_to(src)
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(f, dst)
    elif src.is_file():
        if destination:
            dst = dest_root / destination
            if dst.suffix == "" or destination.endswith("/"):
                dst = dst / src.name
        else:
            dst = dest_root / src.name
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dst)


def install_selection(
    config: FomodConfig, selected: list[FomodPlugin], staging_dir: str
) -> tuple[Path, list[str]]:
    """Instala los archivos según la selección. Devuelve (data_root, notas)."""
    pkg_root = Path(config.pkg_root)
    dest_root = Path(staging_dir)
    dest_root.mkdir(parents=True, exist_ok=True)
    notes: list[str] = []

    # Acumular flags de las opciones elegidas.
    flags: dict[str, str] = {}
    for plugin in selected:
        flags.update(plugin.condition_flags)

    # Reunir todos los archivos a instalar (con prioridad).
    pending: list[FomodFile] = list(config.required_files)
    for plugin in selected:
        pending.extend(plugin.files)
    for cond in config.conditional_installs:
        if cond.dependency.evaluate(flags):
            pending.extend(cond.files)
        if cond.dependency.has_unsupported:
            notes.append(
                "Hay instalaciones condicionales que dependen de archivos del juego "
                "(no evaluadas); revisa si falta algo opcional."
            )

    # Prioridad ascendente -> mayor prioridad se copia al final (sobrescribe).
    pending.sort(key=lambda f: f.priority)
    for ff in pending:
        src = _resolve_source(pkg_root, ff.source)
        if src is None:
            notes.append(f"No se encontró el origen: {ff.source}")
            continue
        _copy_entry(src, dest_root, ff.destination)

    return dest_root, notes

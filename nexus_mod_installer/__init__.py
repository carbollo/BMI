"""
BMI — Bethesda Mod Installer
============================

Gestor/descargador de mods desde Nexus Mods para los juegos de Bethesda (Skyrim,
Fallout, Oblivion, Starfield, Morrowind), pensado también para cuentas GRATUITAS.

Estrategia (igual que Vortex / Mod Organizer 2):
  - El programa se registra como manejador del protocolo ``nxm://``.
  - Incluye un navegador embebido (QtWebEngine) donde inicias sesión y navegas Nexus.
  - Al pulsar "Mod Manager Download" en una página de mod, Nexus genera un enlace
    ``nxm://...`` con una clave temporal (key + expires). El programa lo intercepta,
    pide el enlace de descarga real a la API oficial y descarga + extrae + instala +
    despliega el mod automáticamente, incluyendo sus dependencias y la traducción.

Respeta los Términos de Servicio de Nexus: usa la API oficial y el protocolo nxm://;
no automatiza la web ni elude la limitación de las cuentas gratuitas (el único clic
manual es el botón de descarga de cada mod, igual que en Vortex/MO2).
"""

__version__ = "1.1.0"
__app_name__ = "BMI - Bethesda Mod Installer"

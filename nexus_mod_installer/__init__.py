"""
Nexus Mod Installer
===================

Gestor/descargador automático de mods de Skyrim Special Edition desde Nexus Mods,
pensado para funcionar con cuentas GRATUITAS (sin Premium).

Estrategia (Camino A, igual que Vortex / Mod Organizer 2):
  - El programa se registra como manejador del protocolo ``nxm://``.
  - Incluye un navegador embebido (QtWebEngine) donde inicias sesión y navegas Nexus.
  - Al pulsar "Mod Manager Download" en una página de mod, Nexus genera un enlace
    ``nxm://...`` con una clave temporal (key + expires). El programa lo intercepta,
    pide el enlace de descarga real a la API y descarga + extrae + instala + despliega
    el mod automáticamente, incluyendo sus dependencias.

Esto respeta los Términos de Servicio de Nexus: la única acción manual es 1 clic en
el botón de descarga por cada mod (limitación que Nexus impone a las cuentas gratis).
"""

__version__ = "0.1.0"
__app_name__ = "Nexus Mod Installer"

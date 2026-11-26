import os
from pathlib import Path
from dotenv import load_dotenv

BASE_DIR = Path(__file__).parent
load_dotenv(BASE_DIR / ".env")

# Optional outbound HTTP proxy for the collectors (empty = direct connection).
HTTP_PROXY = os.environ.get("HTTP_PROXY_URL") or None

NTFY_TOPIC = os.environ.get("NTFY_TOPIC", "")

# Enlace al panel web (app.py), para que el resumen de ntfy lleve directo a
# los resultados en vez de solo avisar.
WEB_URL = os.environ.get("WEB_URL", "http://localhost:5051/")

# --- Filtros (puros, sin IA) ---
# Solo habitaciones -- sin tope de precio ni mínimo de m² (decisión explícita
# del usuario 2026-09-03: "pon solo estos").
MIN_HABITACIONES = 3

# La Rioja (provincia completa). Clave canónica usada en todo el proyecto;
# cada portal traduce esto a su propio slug de URL en collectors/*.py.
ZONAS = ["la-rioja"]

# --- Perfiles ---------------------------------------------------------------
# Cada perfil es una búsqueda independiente, con su propio inicio de sesión
# en la web (decisión del usuario, 2026-10-25): sus anuncios, favoritos,
# vistos y descartados no se mezclan nunca con los de otro perfil.
#   - la-rioja: el de siempre (España). Búsqueda de ejemplo en España.
#   - irlanda: el usuario y su pareja, con un login conjunto; buscan casa en
#     Irlanda para dentro de ~3 meses. Criterios amplios de momento ("lo
#     vemos con datos"): toda Irlanda, sin mínimo de habitaciones.
# Las contraseñas no van aquí: están (con hash) en la tabla perfiles de la
# BD y se cambian con `python3 perfiles.py clave <perfil>`.
#
# fuentes: colectores de collectors/ (nombre de módulo) y cada cuántos
# minutos como mínimo se recogen. 0 = en cada corrida del cron.
PERFILES = {
    "la-rioja": {
        "nombre": "La Rioja",
        "region": "La Rioja",
        "zonas": ZONAS,
        "min_habitaciones": MIN_HABITACIONES,
        "fuentes": {"fotocasa": 0, "pisos_com": 0, "habitaclia": 0},
        "ntfy_topic": NTFY_TOPIC,
        # Escalones de los desplegables de precio y botones de habitaciones.
        "precios": [400, 500, 600, 700, 800, 900, 1000, 1100, 1200, 1400, 1600, 1800, 2000, 2500, 3000],
        "habitaciones": [3, 4, 5],
    },
    "irlanda": {
        "nombre": "Irlanda",
        "region": "Irlanda",
        "zonas": ["ireland"],
        "min_habitaciones": 0,
        # MyHome se lee cada 2 horas, no cada 30 minutos, para no cargar el portal.
        "fuentes": {"myhome": 120},
        "ntfy_topic": os.environ.get("NTFY_TOPIC_IRLANDA", ""),
        "precios": [1000, 1250, 1500, 1750, 2000, 2250, 2500, 2750, 3000, 3500, 4000, 5000],
        "habitaciones": [1, 2, 3, 4],
    },
}
PERFIL_POR_DEFECTO = "la-rioja"

# Nombre de módulo en "fuentes" -> portal que pone en cada anuncio (y en la
# tabla recogidas).
PORTAL_DE_FUENTE = {"fotocasa": "fotocasa", "pisos_com": "pisos.com", "habitaclia": "habitaclia", "myhome": "myhome"}


def portales_del_perfil(perfil):
    return [PORTAL_DE_FUENTE[m] for m in PERFILES[perfil]["fuentes"]]

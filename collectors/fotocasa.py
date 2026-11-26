"""
Colector Fotocasa: HTML normal (verificado a mano con curl), con los resultados de búsqueda embebidos
como JSON en <script id="__initial_props__"> -- no hace falta parsear clases
CSS frágiles, se lee directo la estructura que usa su propio frontend.

La paginación por HTTP simple con solo "pageNumber"/"page"/"p" como query
param NO funciona (se ignora en silencio y siempre devuelve la página 1) --
pero SÍ funciona añadiendo "level=PROVINCE&locationCode=<código INE>" Y
metiendo el número de página como segmento de URL justo después de
"todas-las-zonas/l" (ej. ".../l/2"), como hace la propia web de Fotocasa.
Verificado a mano (2026-09-03, aportado por el usuario): con esos parámetros
la página 2 trae contenido realmente distinto y sin solapar con la página 1.

Además se sigue partiendo la búsqueda por tramo de habitaciones
("minRooms"/"maxRooms", ambos soportados por su servidor) en vez de por tipo
de vivienda -- "viviendas" ya incluye pisos y casas juntos, así que no hace
falta un fetch aparte para chalets. Combinando ambas cosas (partir por
habitaciones + paginar cada tramo) se cubre el 100% de los resultados reales
en vez del ~70% de antes de investigar esto.
"""
import json
import logging
import re
import time

import requests

from collectors import get_headers, jitter
from config import HTTP_PROXY, MIN_HABITACIONES

log = logging.getLogger("piso-radar.fotocasa")

BASE_URL = "https://www.fotocasa.es/es/alquiler/viviendas/{slug}/todas-las-zonas/l"
PROXIES = {"http": HTTP_PROXY, "https": HTTP_PROXY} if HTTP_PROXY else None
DELAY_ENTRE_PETICIONES = 3
# Tope de páginas por tramo de habitaciones, por seguridad (evita un bucle
# largo si "count" viniera mal) -- 5 páginas = 150 anuncios, de sobra para
# cualquier tramo real en La Rioja.
MAX_PAGINAS = 5

SLUGS = {
    "la-rioja": "la-rioja-provincia",
}
# level/locationCode son el código INE de la zona -- imprescindibles para
# que la paginación funcione de verdad (sin ellos, Fotocasa ignora el número
# de página). 26 es el código de la provincia de La Rioja.
LOCATION_CODES = {
    "la-rioja": {"level": "PROVINCE", "locationCode": "26"},
}

_PATTERN_JSON = re.compile(
    r'<script type="application/json" id="__initial_props__"[^>]*>(.*?)</script>', re.S
)


def _feature(features, key):
    for f in features:
        if f.get("key") == key:
            return f.get("value")
    return None


# El "titulo" de fotocasa es el nombre del barrio (ej. "Gran Via"), no una
# descripción tipo "Piso en..." -- no sirve para adivinar el tipo por
# palabras clave como en los otros portales, así que aquí se usa el
# buildingType que sí da el JSON.
_TIPOS_PISO = {"flat", "apartment", "penthouse", "duplex", "studio"}


def _parse_realestate(r):
    features = r.get("features") or []
    try:
        precio = int(float(r.get("rawPrice"))) if r.get("rawPrice") is not None else None
    except (TypeError, ValueError):
        precio = None
    direccion = r.get("address") or {}
    building_type = (r.get("buildingType") or "").lower()
    tipo = "Piso" if building_type in _TIPOS_PISO else "Casa"
    multimedia = r.get("multimedia") or []
    imagen = multimedia[0].get("src") if multimedia else None
    return {
        "titulo": (r.get("location") or direccion.get("district") or "Vivienda en alquiler").strip(),
        "precio": precio,
        "m2": _feature(features, "surface"),
        "habitaciones": _feature(features, "rooms"),
        "tipo": tipo,
        "zona": (direccion.get("district") or "").strip() or None,
        "ciudad": (direccion.get("municipality") or "").strip() or None,
        "url": "https://www.fotocasa.es" + (r.get("detail", {}).get("es-ES") or ""),
        "imagen": imagen,
        "descripcion": (r.get("description") or "").strip(),
        "fecha_publicacion": None,
        "portal": "fotocasa",
    }


def _fetch_pagina(slug, min_rooms, max_rooms, pagina, ubicacion):
    """Devuelve (pisos_de_esta_pagina, total_de_verdad). total viene del
    propio "count" que reporta Fotocasa para este tramo -- collect() lo usa
    para saber cuántas páginas más hacen falta."""
    url = BASE_URL.format(slug=slug)
    if pagina > 1:
        url += f"/{pagina}"
    params = {"minRooms": min_rooms, **ubicacion}
    if max_rooms is not None:
        params["maxRooms"] = max_rooms
    try:
        resp = requests.get(url, headers=get_headers(), proxies=PROXIES, timeout=20, params=params)
    except requests.RequestException as e:
        log.warning(f"Fotocasa: fallo de red en '{slug}' página {pagina}: {e}")
        return [], 0

    if resp.status_code == 404:
        log.warning(f"Fotocasa: 404 en '{slug}' -- slug incorrecto, revisar SLUGS")
        return [], 0
    if resp.status_code != 200:
        log.warning(f"Fotocasa: HTTP {resp.status_code} en '{slug}' página {pagina}")
        return [], 0

    m = _PATTERN_JSON.search(resp.text)
    if not m:
        log.warning(f"Fotocasa: no se encontró el JSON de resultados en '{slug}' (¿cambiaron el HTML?)")
        return [], 0

    try:
        data = json.loads(m.group(1))
        result = data["initialSearch"]["result"]
        realestates = result["realEstates"]
    except (json.JSONDecodeError, KeyError) as e:
        log.warning(f"Fotocasa: JSON con forma inesperada en '{slug}': {e}")
        return [], 0

    return [_parse_realestate(r) for r in realestates], result.get("count", 0)


def _fetch_tramo(slug, min_rooms, max_rooms, ubicacion):
    """Pide páginas seguidas de un mismo tramo de habitaciones hasta cubrir
    el "count" real que reporta Fotocasa (o hasta MAX_PAGINAS por
    seguridad)."""
    pisos, pagina = [], 1
    while pagina <= MAX_PAGINAS:
        pisos_pagina, total = _fetch_pagina(slug, min_rooms, max_rooms, pagina, ubicacion)
        if not pisos_pagina:
            break
        pisos.extend(pisos_pagina)
        if len(pisos) >= total:
            break
        pagina += 1
        time.sleep(jitter(DELAY_ENTRE_PETICIONES))
    return pisos


def collect(zonas):
    pisos = []
    for zona in zonas:
        slug = SLUGS.get(zona)
        ubicacion = LOCATION_CODES.get(zona)
        if not slug or not ubicacion:
            log.warning(f"Fotocasa: zona '{zona}' sin slug/localización configurados, se omite")
            continue

        log.info(f"Fotocasa: recogiendo '{zona}' ({MIN_HABITACIONES} habitaciones exactas)...")
        pisos.extend(_fetch_tramo(slug, MIN_HABITACIONES, MIN_HABITACIONES, ubicacion))
        time.sleep(jitter(DELAY_ENTRE_PETICIONES))

        log.info(f"Fotocasa: recogiendo '{zona}' ({MIN_HABITACIONES + 1}+ habitaciones)...")
        pisos.extend(_fetch_tramo(slug, MIN_HABITACIONES + 1, None, ubicacion))
        time.sleep(jitter(DELAY_ENTRE_PETICIONES))

    log.info(f"Fotocasa: {len(pisos)} anuncios recogidos en total")
    return pisos

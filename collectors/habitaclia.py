"""
Colector Habitaclia. Desde el 2026-09-14 Habitaclia usa la misma plataforma
que Fotocasa (las dos son de Adevinta): las URLs antiguas tipo
"alquiler-en-rioja_alta.htm" redirigen (301) al esquema nuevo y la página ya
no trae el HTML de tarjetas que se parseaba antes -- trae los resultados en
un JSON incrustado, `window.__INITIAL_PROPS__ = JSON.parse("...")`. El
colector viejo siguió recibiendo 200 sin encontrar ni un anuncio, y nadie se
enteró hasta el 2026-10-25 (ahora la web avisa si un portal devuelve 0).

Esquema nuevo, verificado en vivo el 2026-10-25:
- "/alquiler/viviendas/la-rioja-provincia/s?minRooms=3": ya deja buscar la
  provincia entera de una vez (antes había que recorrer 4 comarcas).
  minRooms es "N o más". "viviendas" incluye casas y chalets.
- Paginación como segmento de ruta: ".../s/2?minRooms=3" (?page=2 se ignora
  en silencio y devuelve la página 1). Páginas de 29 anuncios; el JSON trae
  totalCount y totalPages reales.
"""
import json
import logging
import re
import time

import requests

from collectors import get_headers, jitter, clasificar_tipo
from config import HTTP_PROXY, MIN_HABITACIONES

log = logging.getLogger("piso-radar.habitaclia")

BASE = "https://www.habitaclia.com"
BASE_URL = BASE + "/alquiler/viviendas/{slug}/s"
PROXIES = {"http": HTTP_PROXY, "https": HTTP_PROXY} if HTTP_PROXY else None
DELAY_ENTRE_PETICIONES = 3
MAX_PAGINAS = 5  # red de seguridad, ver collectors/fotocasa.py

SLUGS = {
    "la-rioja": "la-rioja-provincia",
}
# Nombre de la provincia tal como lo da cada anuncio (summary.location.province).
# Hace el papel del antiguo filtro de "list-ady": si el portal mete anuncios
# de fuera cuando hay pocos resultados, se descartan aquí.
PROVINCIAS = {
    "la-rioja": "La Rioja",
}

_PATTERN_JSON = re.compile(r'window\.__INITIAL_PROPS__\s*=\s*JSON\.parse\(("(?:[^"\\]|\\.)*")\)')

_TIPOS_CASA = {"house", "chalet", "country_house", "terraced_house", "semidetached_house"}


def _parse_item(item):
    summary = item.get("summary") or {}
    ubicacion = summary.get("location") or {}
    propiedad = item.get("property") or {}
    precio = ((item.get("transaction") or {}).get("price") or {})
    imagenes = (summary.get("multimedia") or {}).get("images") or []
    canonica = (item.get("urls") or {}).get("canonical") or item.get("navigationUrl")
    if not canonica:
        return None

    tipo_portal = (propiedad.get("propertyType") or "").lower()
    subtipo = (propiedad.get("propertySubtype") or "").lower()
    if tipo_portal or subtipo:
        tipo = "Casa" if tipo_portal in _TIPOS_CASA or subtipo in _TIPOS_CASA else "Piso"
    else:
        tipo = clasificar_tipo(summary.get("title"))

    zona = (ubicacion.get("district") or "").strip() or None
    ciudad = (ubicacion.get("municipality") or "").strip() or None
    # Hay anuncios sin título (el anunciante lo deja vacío): se compone uno
    # con el tipo y el sitio, como hace el propio portal en la tarjeta.
    titulo = re.sub(r"\s+", " ", summary.get("title") or "").strip()
    if not titulo:
        sitio = (ubicacion.get("displayAddressLine") or "").strip() or zona or ciudad
        titulo = f"{tipo} en {sitio}" if sitio else f"{tipo} en alquiler"

    importe = precio.get("amount")
    legacy = item.get("legacyNumericId")
    piso = {
        "titulo": titulo,
        # 0 o precio oculto = "a consultar", no un alquiler gratis.
        "precio": int(importe) if importe and not precio.get("hidden") else None,
        "m2": propiedad.get("builtSurface"),
        "habitaciones": propiedad.get("rooms"),
        "tipo": tipo,
        "zona": zona,
        "ciudad": ciudad,
        "url": BASE + canonica,
        "imagen": imagenes[0].get("url") if imagenes else None,
        "descripcion": (summary.get("description") or "").replace("\r\n", "\n").strip(),
        "fecha_publicacion": None,
        "portal": "habitaclia",
        "provincia": ubicacion.get("province"),
    }
    if legacy:
        # Las URLs del formato anterior acababan en "-i<legacyNumericId>.htm":
        # db.upsert_piso lo usa para trasladar el anuncio ya guardado a su URL
        # nueva (conservando favorito/visto) en vez de duplicarlo.
        piso["legacy_url_like"] = f"%-i{legacy}.htm%"
    return piso


def _fetch_pagina(slug, pagina):
    """Devuelve (pisos_de_esta_pagina, total_paginas)."""
    url = BASE_URL.format(slug=slug)
    if pagina > 1:
        url += f"/{pagina}"
    try:
        resp = requests.get(url, headers=get_headers(), proxies=PROXIES, timeout=20,
                            params={"minRooms": MIN_HABITACIONES})
    except requests.RequestException as e:
        log.warning(f"Habitaclia: fallo de red en '{slug}' página {pagina}: {e}")
        return [], 0

    if resp.status_code == 404:
        log.warning(f"Habitaclia: 404 en '{slug}' -- slug incorrecto, revisar SLUGS")
        return [], 0
    if resp.status_code != 200:
        log.warning(f"Habitaclia: HTTP {resp.status_code} en '{slug}' página {pagina}")
        return [], 0

    m = _PATTERN_JSON.search(resp.text)
    if not m:
        log.warning(f"Habitaclia: no se encontró el JSON de resultados en '{slug}' (¿cambiaron el HTML?)")
        return [], 0

    try:
        # Doble decodificación: primero el literal de texto JavaScript, luego
        # el JSON que lleva dentro.
        data = json.loads(json.loads(m.group(1)))
        resultados = data["initialSearchResultsPage"]["initialSearchContext"]["results"]
        items = resultados["items"]
        total_paginas = resultados.get("pagination", {}).get("totalPages", 1)
    except (json.JSONDecodeError, KeyError, TypeError) as e:
        log.warning(f"Habitaclia: JSON con forma inesperada en '{slug}': {e}")
        return [], 0

    return [p for i in items if (p := _parse_item(i))], total_paginas


def _fetch_zona(slug):
    pisos, pagina = [], 1
    while pagina <= MAX_PAGINAS:
        pisos_pagina, total_paginas = _fetch_pagina(slug, pagina)
        if not pisos_pagina:
            break
        pisos.extend(pisos_pagina)
        if pagina >= total_paginas:
            break
        pagina += 1
        time.sleep(jitter(DELAY_ENTRE_PETICIONES))
    return pisos


def collect(zonas):
    pisos = []
    for zona in zonas:
        slug = SLUGS.get(zona)
        if not slug:
            log.warning(f"Habitaclia: zona '{zona}' sin slug configurado, se omite")
            continue
        log.info(f"Habitaclia: recogiendo '{zona}' ({MIN_HABITACIONES}+ habitaciones)...")
        provincia = PROVINCIAS.get(zona)
        for piso in _fetch_zona(slug):
            provincia_piso = piso.pop("provincia")
            if provincia and provincia_piso not in (provincia, None):
                log.info(f"Habitaclia: descartado anuncio de otra provincia: {piso['url']}")
                continue
            pisos.append(piso)
    log.info(f"Habitaclia: {len(pisos)} anuncios recogidos en total")
    return pisos

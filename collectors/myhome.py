"""
Colector MyHome.ie (perfil "irlanda"), para uso personal. Respeta robots.txt
(solo la ruta limpia y "?page=N"), hace una petición cada pocos segundos y, en
config.PERFILES, como mucho una pasada cada 2 horas. Revisa las condiciones de
uso del portal antes de usarlo.

La página trae los resultados en un JSON de Angular (<script id="ng-state">),
bajo una clave "SEARCH_RESOLVER:<ruta>": SearchResults (20 por página),
ResultCount y PageSize. Verificado en vivo el 2026-10-25: 661 alquileres en
toda Irlanda.
"""
import json
import logging
import re
import time

import requests

from collectors import get_headers, jitter
from config import HTTP_PROXY

log = logging.getLogger("piso-radar.myhome")

BASE = "https://www.myhome.ie"
BASE_URL = BASE + "/rentals/{slug}/property-to-rent"
PROXIES = {"http": HTTP_PROXY, "https": HTTP_PROXY} if HTTP_PROXY else None
DELAY_ENTRE_PETICIONES = 4
MAX_PAGINAS = 60  # red de seguridad: 661 anuncios son 34 páginas

SLUGS = {
    "ireland": "ireland",
}

_PATTERN_JSON = re.compile(r'<script id="ng-state" type="application/json">(.*?)</script>', re.S)
# Eircode: clave de ruta (letra + 2 dígitos, o D6W) + 4 caracteres, con o sin espacio.
_RE_EIRCODE = re.compile(r"^(?:[AC-FHKNPRTV-Y]\d{2}|D6W)\s?[0-9AC-FHKNPRTV-Y]{4}$", re.I)
_RE_IMPORTE = re.compile(r"€\s*([\d,]+(?:\.\d+)?)")
_TIPOS_PISO = {"apartment", "studio", "penthouse", "duplex", "flat", "apartment block"}


def _precio_mensual(texto):
    """"€2,800 / month" -> 2800; "€450 / week" -> 1950 (x52/12); "€2,200 to
    €3,500 / month" -> 2200 (el "desde"); sin cifra -> None."""
    if not texto:
        return None
    m = _RE_IMPORTE.search(texto)
    if not m:
        return None
    importe = float(m.group(1).replace(",", ""))
    if "week" in texto.lower():
        importe = importe * 52 / 12
    return round(importe) or None


def _ubicacion(direccion):
    """"16C Cork Street, Kinsale, Cork, P17 D429" -> ("Kinsale", "Cork", "P17 D429").
    El último trozo que no es un Eircode es el condado o el distrito postal
    de Dublín ("Dublin 8"), que hace de "ciudad" para el filtro de la web; el
    anterior, la zona."""
    partes = [p.strip() for p in (direccion or "").split(",") if p.strip()]
    eircode = None
    if partes and _RE_EIRCODE.match(partes[-1]):
        eircode = partes.pop().upper()
    ciudad = re.sub(r"^(County|Co\.?)\s+", "", partes[-1], flags=re.I) if partes else None
    zona = partes[-2] if len(partes) >= 2 else None
    return zona, ciudad, eircode


def _parse_item(item):
    if not item.get("IsActive", True) or item.get("IsSaleAgreed") or item.get("PropertyStatus") not in (None, "ToLet"):
        return None  # ya alquilado / "let agreed"
    ruta = item.get("BrochureUrl") or item.get("SeoUrl")
    if not ruta:
        return None
    tipo_portal = item.get("PropertyType") or ""
    direccion_cruda = item.get("DisplayAddress") or item.get("Address") or ""
    # La búsqueda de alquiler también trae locales y naves ("Unit 11, N11
    # Business Park", "Alpine Service Station") y anuncios de prueba que el
    # portal dejó publicados ("Test Property 12"): todos sin tipo y con 0
    # habitaciones (visto el 2026-10-25, 116 de 661 sin tipo). Los que no
    # tienen tipo pero sí habitaciones son viviendas normales y se quedan.
    if (not tipo_portal and not item.get("NumberOfBeds")) or re.match(r"test\b", direccion_cruda.strip(), re.I):
        return None

    direccion = re.sub(r"\s+", " ", direccion_cruda).strip()
    zona, ciudad, eircode = _ubicacion(direccion)
    titulo = re.sub(r",?\s*" + re.escape(eircode) + r"$", "", direccion, flags=re.I) if eircode else direccion
    mapa = item.get("BrochureMap") or {}
    extra = {
        "tipo_portal": tipo_portal or None,
        "ber": item.get("BerRating") or None,
        "banos": item.get("NumberOfBathrooms") or None,
        "particular": bool(item.get("IsPrivateLandlord")),
        "precio_texto": item.get("PriceAsString") or None,
        "eircode": eircode,
        "lat": mapa.get("latitude") or None,
        "lon": mapa.get("longitude") or None,
    }
    return {
        "titulo": titulo or "Vivienda en alquiler",
        "precio": _precio_mensual(item.get("PriceAsString")),
        "m2": item.get("SizeStringMeters") or None,
        "habitaciones": item.get("NumberOfBeds"),
        "tipo": "Piso" if tipo_portal.lower() in _TIPOS_PISO else ("Casa" if tipo_portal else None),
        "zona": zona,
        "ciudad": ciudad,
        "url": BASE + ruta,
        "imagen": item.get("MainPhotoWeb") or item.get("MainPhoto") or None,
        "descripcion": "",
        "fecha_publicacion": item.get("CreatedOnDate"),
        "portal": "myhome",
        "extra": {k: v for k, v in extra.items() if v not in (None, "")},
    }


def _fetch_pagina(slug, pagina):
    """Devuelve (pisos_de_esta_pagina, total_paginas)."""
    url = BASE_URL.format(slug=slug)
    if pagina > 1:
        url += f"?page={pagina}"  # la única query string que robots.txt permite
    try:
        resp = requests.get(url, headers={**get_headers(), "Accept-Language": "en-IE,en;q=0.9"},
                            proxies=PROXIES, timeout=25)
    except requests.RequestException as e:
        log.warning(f"MyHome: fallo de red en '{slug}' página {pagina}: {e}")
        return [], 0
    if resp.status_code != 200:
        log.warning(f"MyHome: HTTP {resp.status_code} en '{slug}' página {pagina}")
        return [], 0

    m = _PATTERN_JSON.search(resp.text)
    if not m:
        log.warning(f"MyHome: no se encontró el JSON de resultados en '{slug}' (¿cambiaron la web?)")
        return [], 0
    try:
        estado = json.loads(m.group(1))
        resultado = next(v for k, v in estado.items()
                         if k.startswith("SEARCH_RESOLVER") and isinstance(v, dict) and "SearchResults" in v)
        items = resultado["SearchResults"]
        por_pagina = resultado.get("PageSize") or len(items) or 1
        total_paginas = -(-int(resultado.get("ResultCount") or 0) // por_pagina)
    except (json.JSONDecodeError, StopIteration, KeyError, TypeError, ValueError) as e:
        log.warning(f"MyHome: JSON con forma inesperada en '{slug}': {e!r}")
        return [], 0
    return [p for i in items if (p := _parse_item(i))], total_paginas


def _fetch_zona(slug):
    pisos, pagina = [], 1
    while pagina <= MAX_PAGINAS:
        pisos_pagina, total_paginas = _fetch_pagina(slug, pagina)
        if not pisos_pagina and total_paginas == 0:
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
            log.warning(f"MyHome: zona '{zona}' sin slug configurado, se omite")
            continue
        log.info(f"MyHome: recogiendo '{zona}'...")
        pisos.extend(_fetch_zona(slug))
    log.info(f"MyHome: {len(pisos)} anuncios recogidos en total")
    return pisos

"""
Colector pisos.com: HTML normal (sin bloqueo anti-bot detectado), parseado
con BeautifulSoup -- a diferencia de Fotocasa aquí no hay JSON embebido, así
que depende de las clases CSS `ad-preview*`. Si pisos.com rediseña la página
estas clases dejan de encontrarse y collect() empieza a devolver 0 anuncios
sin lanzar ningún error -- revisar aquí primero si eso pasa.

Filtro de habitaciones y paginación aportados por el usuario (2026-09-03),
verificados en vivo: "con-N-habitaciones" en la URL es "N o más" (no
exactas), y la página se pagina añadiendo el número al final
("con-3-habitaciones/2/"). El total real se lee de "N resultados" en
div.grid__title -- se sigue pidiendo página tras página hasta cubrirlo.
"""
import logging
import re
import time

import requests
from bs4 import BeautifulSoup

from collectors import get_headers, jitter, clasificar_tipo
from config import HTTP_PROXY, MIN_HABITACIONES

log = logging.getLogger("piso-radar.pisos_com")

# "pisos-<slug>" es en realidad la categoría general (el propio pisos.com la
# titula "casas y pisos en <zona>" y ya incluye chalets si los hay) -- pero
# un pueblo con mucho más volumen de pisos que de casas puede dejar una casa
# reciente fuera de las primeras páginas. "casas-<slug>" es la vista
# filtrada solo a casas/chalets, se pide aparte por el mismo motivo que en
# fotocasa.py.
BASE_URL = "https://www.pisos.com/alquiler/pisos-{slug}/con-{habs}-habitaciones/"
BASE_URL_CASAS = "https://www.pisos.com/alquiler/casas-{slug}/con-{habs}-habitaciones/"
PROXIES = {"http": HTTP_PROXY, "https": HTTP_PROXY} if HTTP_PROXY else None
DELAY_ENTRE_PETICIONES = 3
MAX_PAGINAS = 5  # red de seguridad, ver collectors/fotocasa.py

SLUGS = {
    "la-rioja": "la_rioja",
}

_RE_NUM = re.compile(r"[\d.,]+")
_RE_SUBTITULO = re.compile(r"^(.*?)\s*\((.+)\)$")
_RE_TOTAL = re.compile(r"(\d+)\s*resultados?", re.I)


def _num(texto):
    """'89 m²' -> 89, '2 habs.' -> 2, '1.500 €/mes' -> 1500. None si no hay dígitos."""
    m = _RE_NUM.search(texto or "")
    if not m:
        return None
    return int(m.group(0).replace(".", "").replace(",", ""))


def _zona_y_ciudad(subtitulo, zona_busqueda):
    """"Centro (Logroño)" -> ("Centro", "Logroño"). "Sojuela" (pueblos sin
    barrio) -> (None, "Sojuela"). Con la búsqueda ahora a nivel de provincia
    (zona_busqueda = "la-rioja"), esa clave ya no sirve como ciudad de
    verdad -- solo se usa como último recurso si ni siquiera hay subtítulo."""
    if not subtitulo:
        return None, zona_busqueda
    m = _RE_SUBTITULO.match(subtitulo)
    if m:
        return m.group(1).strip(), m.group(2).strip()
    return None, subtitulo


def _primera_imagen(card):
    """El carrusel de fotos repite la primera foto en un <div> extra oculto
    con "u-hide" (mismo motivo que las tarjetas duplicadas) -- hay que
    saltarlo o se coge una miniatura que a veces no es la real. Además, solo
    la primera tarjeta de la página trae la foto en "src" (carga eager, para
    no penalizar el LCP) -- el resto usa carga diferida con "data-src" y
    "src" vacío o ausente hasta que el navegador la desplaza a la vista."""
    for slide in card.select("div.carousel__slide"):
        if "u-hide" in slide.get("class", []):
            continue
        img = slide.select_one("img")
        if not img:
            continue
        src = img.get("src") or img.get("data-src")
        if src:
            return src
    return None


def _parse_card(card, zona_busqueda):
    title_el = card.select_one("a.ad-preview__title")
    if not title_el:
        return None
    price_el = card.select_one("span.ad-preview__price")
    subtitle_el = card.select_one("p.ad-preview__subtitle")
    desc_el = card.select_one("p.ad-preview__description")
    chars = [c.get_text(strip=True) for c in card.select("p.ad-preview__char")]

    m2 = next((_num(c) for c in chars if "m²" in c), None)
    habitaciones = next((_num(c) for c in chars if "hab" in c), None)

    href = title_el.get("href", "")
    url = href if href.startswith("http") else f"https://www.pisos.com{href}"

    titulo = title_el.get_text(strip=True)
    subtitulo = subtitle_el.get_text(strip=True) if subtitle_el else None
    zona, ciudad = _zona_y_ciudad(subtitulo, zona_busqueda)
    return {
        "titulo": titulo,
        "precio": _num(price_el.get_text(strip=True)) if price_el else None,
        "m2": m2,
        "habitaciones": habitaciones,
        "tipo": clasificar_tipo(titulo),
        "zona": zona,
        "ciudad": ciudad,
        "url": url,
        "imagen": _primera_imagen(card),
        "descripcion": desc_el.get_text(strip=True) if desc_el else "",
        "fecha_publicacion": None,
        "portal": "pisos.com",
    }


def _total_resultados(soup):
    titulo = soup.select_one("div.grid__title")
    if not titulo:
        return None
    m = _RE_TOTAL.search(titulo.get_text())
    return int(m.group(1)) if m else None


def _fetch_pagina(slug, zona_busqueda, base_url, pagina):
    """Devuelve (pisos_de_esta_pagina, total_de_verdad)."""
    url = base_url.format(slug=slug, habs=MIN_HABITACIONES)
    if pagina > 1:
        url += f"{pagina}/"
    try:
        resp = requests.get(url, headers=get_headers(), proxies=PROXIES, timeout=20)
    except requests.RequestException as e:
        log.warning(f"pisos.com: fallo de red en '{slug}' página {pagina}: {e}")
        return [], 0

    if resp.status_code != 200:
        log.warning(f"pisos.com: HTTP {resp.status_code} en '{slug}' página {pagina}")
        return [], 0

    soup = BeautifulSoup(resp.text, "html.parser")
    # Se excluyen dos tipos de tarjeta que NO son resultados reales de esta
    # zona: "u-hide" son variantes de layout duplicadas ocultas en el DOM, y
    # "js-similarAd" son anuncios "relacionados" que pisos.com mete cuando el
    # pueblo tiene pocos resultados -- pueden ser de OTRA provincia entera
    # (confirmado con un caso real: buscando Villamediana de Iregua devolvió
    # un piso en Palencia marcado con esta clase).
    cards = [
        c for c in soup.select("div.ad-preview")
        if not {"u-hide", "js-similarAd"} & set(c.get("class", []))
    ]

    pisos = [p for c in cards if (p := _parse_card(c, zona_busqueda))]
    total = _total_resultados(soup)
    return pisos, (total if total is not None else len(pisos))


def _fetch_tramo(slug, zona_busqueda, base_url):
    pisos, pagina = [], 1
    while pagina <= MAX_PAGINAS:
        pisos_pagina, total = _fetch_pagina(slug, zona_busqueda, base_url, pagina)
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
        if not slug:
            log.warning(f"pisos.com: zona '{zona}' sin slug configurado, se omite")
            continue

        log.info(f"pisos.com: recogiendo '{zona}' (pisos, {MIN_HABITACIONES}+ habitaciones)...")
        pisos.extend(_fetch_tramo(slug, zona, BASE_URL))
        time.sleep(jitter(DELAY_ENTRE_PETICIONES))

        log.info(f"pisos.com: recogiendo '{zona}' (casas, {MIN_HABITACIONES}+ habitaciones)...")
        pisos.extend(_fetch_tramo(slug, zona, BASE_URL_CASAS))
        time.sleep(jitter(DELAY_ENTRE_PETICIONES))

    log.info(f"pisos.com: {len(pisos)} anuncios recogidos en total")
    return pisos

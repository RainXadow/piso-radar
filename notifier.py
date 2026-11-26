import logging
from urllib.parse import urljoin

import requests

from config import NTFY_TOPIC, PERFILES, WEB_URL
from db import marcar_notificado, pisos_pendientes_de_notificar

log = logging.getLogger("piso-radar.notifier")

MAX_LINEAS_NUEVOS = 8
MAX_LINEAS_CAMBIOS = 8

_ETIQUETAS = {"precio": "€", "m2": " m²", "habitaciones": " hab."}


def _post(titulo, mensaje, priority, tags, click, proxies=None, topic=None):
    headers = {
        # requests codifica los headers str en latin-1 (RFC 7230), así que
        # un título con emoji revienta con UnicodeEncodeError -- pasarlo
        # como bytes UTF-8 evita ese paso.
        "Title": titulo.encode("utf-8"),
        "Priority": priority,
        "Tags": tags,
    }
    if click:
        # Header nativo de ntfy: tocar la notificación entera (no solo un
        # enlace de texto) abre esta URL directamente.
        headers["Click"] = click
    return requests.post(
        f"https://ntfy.sh/{NTFY_TOPIC if topic is None else topic}",
        data=mensaje.encode("utf-8"),
        headers=headers,
        timeout=10,
        proxies=proxies,
    )


def _notify(titulo, mensaje, priority="default", tags="house", click=None, topic=None):
    """topic: el del perfil. None = el NTFY_TOPIC general; "" = el perfil no
    tiene canal configurado, y entonces no se avisa (nunca se cae al canal
    general: los avisos de Irlanda no pueden acabar en el de La Rioja)."""
    if topic is None:
        topic = NTFY_TOPIC
    if not topic:
        log.warning("Notifier: topic de ntfy no configurado, no se puede avisar.")
        return False
    try:
        r = _post(titulo, mensaje, priority, tags, click, topic=topic)
    except Exception as e:
        log.error(f"Notifier: fallo enviando notificación ntfy: {e}")
        return False

    # requests.post no lanza excepción por un 4xx/5xx -- sin comprobar el
    # status a mano, un envío rechazado se registraba como "enviado" sin
    # haber llegado nunca.
    if r.status_code == 200:
        return True

    log.error(f"Notifier: ntfy respondió {r.status_code}: {r.text[:300]}")
    return False


def _linea_nuevo(piso):
    precio = f"{piso['precio']}€" if piso["precio"] else "?€"
    return f"• {precio} — {piso['titulo']} ({piso['ciudad'] or piso['zona'] or piso['portal']})"


def _linea_cambio(cambio):
    partes = []
    for campo, (antes, ahora) in cambio["cambios"].items():
        sufijo = _ETIQUETAS.get(campo, "")
        antes_txt = f"{antes}{sufijo}" if antes is not None else "?"
        ahora_txt = f"{ahora}{sufijo}" if ahora is not None else "?"
        partes.append(f"{antes_txt} → {ahora_txt}")
    return f"• {cambio['titulo']}: {', '.join(partes)}\n  {cambio['url']}"


def notificar_resumen(con, cambios_favoritos, perfil=None):
    """Un único aviso por corrida, en vez de uno por anuncio -- y solo si de
    verdad hay algo que contar (anuncios nuevos que pasaron el filtro, o
    cambios en pisos marcados como favoritos). Si una corrida no trae nada
    nuevo ni cambios, no se manda nada: ntfy es un complemento de la web
    para enterarse de las novedades, no un heartbeat de "sigo vivo"."""
    # Cada perfil avisa a su propio canal y solo de lo suyo.
    nuevos = pisos_pendientes_de_notificar(con, perfil)
    config_perfil = PERFILES.get(perfil, {})

    if not nuevos and not cambios_favoritos:
        log.info("Notifier: sin novedades esta corrida, no se avisa")
        return

    partes_titulo = []
    if nuevos:
        partes_titulo.append(f"{len(nuevos)} nuevo{'s' if len(nuevos) != 1 else ''}")
    if cambios_favoritos:
        partes_titulo.append(f"{len(cambios_favoritos)} favorito{'s' if len(cambios_favoritos) != 1 else ''} actualizado{'s' if len(cambios_favoritos) != 1 else ''}")
    titulo = "🏠 Piso Radar" + (f" {config_perfil['nombre']}" if config_perfil else "") + ": " + " · ".join(partes_titulo)

    bloques = []
    if nuevos:
        lineas = [_linea_nuevo(p) for p in nuevos[:MAX_LINEAS_NUEVOS]]
        if len(nuevos) > MAX_LINEAS_NUEVOS:
            lineas.append(f"... y {len(nuevos) - MAX_LINEAS_NUEVOS} más, mira la web")
        bloques.append("🆕 Nuevos:\n" + "\n".join(lineas))
    if cambios_favoritos:
        lineas = [_linea_cambio(c) for c in cambios_favoritos[:MAX_LINEAS_CAMBIOS]]
        if len(cambios_favoritos) > MAX_LINEAS_CAMBIOS:
            lineas.append(f"... y {len(cambios_favoritos) - MAX_LINEAS_CAMBIOS} más, mira la web")
        bloques.append("⭐ Favoritos actualizados:\n" + "\n".join(lineas))
    # Tocar el aviso lleva directo a lo que cuenta: las novedades, o los
    # favoritos si solo cambió alguno de ellos.
    enlace = urljoin(WEB_URL, "?novedades=1") if nuevos else urljoin(WEB_URL, "favoritos")
    bloques.append(f"🔗 {enlace}")
    mensaje = "\n\n".join(bloques)

    # Prioridad alta solo si hay movimiento en favoritos -- un lote de
    # anuncios nuevos sin más no necesita sonar como urgente.
    priority = "high" if cambios_favoritos else "default"

    topic = config_perfil.get("ntfy_topic", "") if config_perfil else None
    if _notify(titulo, mensaje, priority=priority, click=enlace, topic=topic):
        for piso in nuevos:
            marcar_notificado(con, piso["id"])
        log.info(f"Notifier: resumen enviado ({len(nuevos)} nuevos, {len(cambios_favoritos)} favoritos actualizados)")
    else:
        log.warning("Notifier: no se pudo enviar el resumen -- los nuevos se reintentarán en la próxima corrida")

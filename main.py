import logging
from collections import Counter
from datetime import datetime, timedelta, timezone

from config import PERFILES, PORTAL_DE_FUENTE as PORTAL_DE
from collectors import fotocasa, pisos_com, habitaclia, myhome
from db import connect, existe, purgar_retirados, registrar_recogida, upsert_piso
from filtro import cumple_filtros
from notifier import notificar_resumen

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("piso-radar.main")

# Holgura para los intervalos: el cron va cada 30 min y cada corrida tarda
# un poco distinto; sin ella, "cada 120 min" acabaría siendo cada 150.
HOLGURA_INTERVALO = timedelta(minutes=5)


def _colector(modulo):
    # Se resuelve al llamar (no a nivel de módulo) para que los tests puedan
    # sustituir collect() de cada colector.
    return {"fotocasa": fotocasa, "pisos_com": pisos_com, "habitaclia": habitaclia, "myhome": myhome}[modulo]


def fuentes_pendientes(con, perfil, ahora=None):
    """Módulos de las fuentes del perfil a los que ya les toca: los de
    intervalo 0 siempre; el resto, si su última recogida es más antigua que
    su intervalo (menos la holgura)."""
    ahora = ahora or datetime.now(timezone.utc)
    pendientes = []
    for modulo, minutos in PERFILES[perfil]["fuentes"].items():
        if minutos:
            ultima = con.execute(
                "SELECT MAX(fecha) f FROM recogidas WHERE portal = ?", (PORTAL_DE[modulo],)
            ).fetchone()["f"]
            if ultima and datetime.fromisoformat(ultima) > ahora - timedelta(minutes=minutos) + HOLGURA_INTERVALO:
                continue
        pendientes.append(modulo)
    return pendientes


def recoger(perfil, modulos):
    """Junta los anuncios de esas fuentes del perfil, marcados con el perfil.
    Un fallo inesperado en una (p. ej. el portal cambia su HTML y el parser
    revienta) no tumba las demás: esa cuenta como 0 anuncios y la web lo
    avisa. Quita repetidos (pisos.com devuelve algún anuncio en las dos
    búsquedas, pisos y casas)."""
    zonas = PERFILES[perfil]["zonas"]
    pisos, vistos = [], set()
    for modulo in modulos:
        try:
            encontrados = _colector(modulo).collect(zonas)
        except Exception:
            log.exception(f"{modulo}: el colector falló, se sigue con los demás")
            continue
        for piso in encontrados:
            clave = (piso["portal"], piso["url"])
            if clave not in vistos:
                vistos.add(clave)
                pisos.append({**piso, "perfil": perfil})
    return pisos


def procesar_perfil(con, perfil):
    config = PERFILES[perfil]
    modulos = fuentes_pendientes(con, perfil)
    if not modulos:
        log.info(f"[{perfil}] ninguna fuente toca en esta corrida")
        return

    pisos = recoger(perfil, modulos)
    if not pisos:
        # Cero anuncios en todas las fuentes a la vez suele ser un fallo de red.
        log.warning(f"[{perfil}] recogida vacía en todas las fuentes")

    # Con todas las fuentes recogidas a 0 de partida: las que no devuelven
    # nada también se registran (es justo lo que la web tiene que avisar).
    conteos = Counter({PORTAL_DE[m]: 0 for m in modulos})
    conteos.update(p["portal"] for p in pisos)

    nuevos = 0
    descartados = 0
    cambios_favoritos = []
    for piso in pisos:
        # El filtro solo decide si algo ENTRA de nuevo a la base de
        # datos -- un piso que ya estaba guardado (esté o no en
        # favoritos) se sigue actualizando aunque ahora mismo se salga
        # del filtro (ej. subió de precio), para no perder el rastro de
        # un favorito solo porque ya no encaja en los criterios generales.
        if not existe(con, piso["portal"], piso["url"]) and not cumple_filtros(piso, config["min_habitaciones"]):
            descartados += 1
            continue
        nuevo, cambios, favorito = upsert_piso(con, piso)
        if nuevo:
            nuevos += 1
        elif cambios and favorito:
            cambios_favoritos.append({
                "titulo": piso["titulo"],
                "url": piso["url"],
                "cambios": cambios,
            })
    log.info(
        f"[{perfil}] recogida completa: {len(pisos)} anuncios vistos, "
        f"{nuevos} nuevos guardados, {descartados} descartados por filtro, "
        f"{len(cambios_favoritos)} favoritos con cambios"
    )
    # Después de los upsert: así ultima_vista de lo recién visto nunca
    # queda por detrás de la fecha de esta recogida.
    registrar_recogida(con, dict(conteos))
    notificar_resumen(con, cambios_favoritos, perfil)


def main():
    con = connect()
    try:
        for perfil in PERFILES:
            try:
                procesar_perfil(con, perfil)
            except Exception:
                # Un perfil roto no deja sin recoger a los demás.
                log.exception(f"[{perfil}] la recogida falló")
        borrados = purgar_retirados(con)
        if borrados:
            log.info(f"Borrados {borrados} anuncios que llevaban más de una semana sin estar publicados")
    finally:
        con.close()


if __name__ == "__main__":
    main()

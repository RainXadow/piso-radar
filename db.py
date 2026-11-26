import functools
import hashlib
import json
import logging
import sqlite3
import unicodedata
from datetime import datetime, timedelta, timezone
from pathlib import Path

DB_PATH = Path(__file__).parent / "piso-radar.db"
PERFIL_POR_DEFECTO = "la-rioja"  # el de antes de que hubiera perfiles
SCHEMA_PATH = Path(__file__).parent / "schema.sql"

log = logging.getLogger("piso-radar.db")


# Rutas de BD cuyo esquema ya se aplicó en este proceso: la web abre una
# conexión por petición y no tiene sentido repetir el schema.sql y la
# migración cada vez (el servicio web vive semanas).
_ESQUEMA_APLICADO = set()


@functools.lru_cache(maxsize=4096)
def normalizar(texto):
    """Minúsculas y sin tildes: "ÁTICO Logroño" -> "atico logrono". SQLite
    solo ignora mayúsculas en ASCII, así que "ático" no encontraba "ÁTICO"
    ni "atico" encontraba "Ático".

    Con caché: una búsqueda lanza varias consultas (total, página, contadores)
    y cada una normaliza el texto de todos los anuncios; sin ella, buscar
    tardaba ~8 veces más. Los textos apenas cambian entre peticiones."""
    if texto is None:
        return None
    descompuesto = unicodedata.normalize("NFD", texto.casefold())
    return "".join(c for c in descompuesto if unicodedata.category(c) != "Mn")


def connect():
    con = sqlite3.connect(DB_PATH, timeout=30)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA busy_timeout=30000")
    con.create_function("normalizar", 1, normalizar, deterministic=True)
    clave = str(Path(DB_PATH).resolve())
    if clave not in _ESQUEMA_APLICADO:
        con.execute("PRAGMA journal_mode=WAL")  # persiste en el fichero, basta una vez
        con.executescript(SCHEMA_PATH.read_text())
        _migrar_columnas_nuevas(con)
        _ESQUEMA_APLICADO.add(clave)
    return con


def _migrar_columnas_nuevas(con):
    """CREATE TABLE IF NOT EXISTS del schema.sql no añade columnas a una
    tabla que ya existía de una versión anterior -- las nuevas se aplican
    aquí a mano (idempotente: no hace nada si la columna ya existe)."""
    columnas = {fila["name"] for fila in con.execute("PRAGMA table_info(pisos)")}
    if "tipo" not in columnas:
        con.execute("ALTER TABLE pisos ADD COLUMN tipo TEXT")
        con.commit()
    if "imagen" not in columnas:
        con.execute("ALTER TABLE pisos ADD COLUMN imagen TEXT")
        con.commit()
    if "descartado" not in columnas:
        con.execute("ALTER TABLE pisos ADD COLUMN descartado INTEGER NOT NULL DEFAULT 0")
        con.commit()
    if "ultima_vista" not in columnas:
        con.execute("ALTER TABLE pisos ADD COLUMN ultima_vista TEXT")
        con.commit()
    if "precio_anterior" not in columnas:
        con.execute("ALTER TABLE pisos ADD COLUMN precio_anterior INTEGER")
        con.commit()
    if "perfil" not in columnas:
        # Todo lo anterior a los perfiles era la búsqueda de La Rioja.
        con.execute("ALTER TABLE pisos ADD COLUMN perfil TEXT NOT NULL DEFAULT 'la-rioja'")
        con.commit()
    if "extra" not in columnas:
        con.execute("ALTER TABLE pisos ADD COLUMN extra TEXT")
        con.commit()
    # Aquí y no en schema.sql: en una BD antigua la columna aún no existe
    # cuando se ejecuta el schema.
    con.execute("CREATE INDEX IF NOT EXISTS idx_pisos_perfil ON pisos(perfil)")
    con.commit()


def make_id(portal, url):
    return hashlib.sha256(f"{portal}|{url}".strip().lower().encode()).hexdigest()[:16]


def existe(con, portal, url):
    return con.execute("SELECT 1 FROM pisos WHERE id = ?", (make_id(portal, url),)).fetchone() is not None


# Campos que se refrescan en cada recogida si el piso ya existía -- un
# anuncio puede bajar de precio, cambiar de m²/habitaciones anunciados, o
# simplemente reescribir el título/descripción, y la web no debe quedarse
# enseñando para siempre los datos del primer día que se vio.
CAMPOS_ACTUALIZABLES = ["precio", "m2", "habitaciones", "titulo", "descripcion", "imagen", "extra"]
# De esos, los únicos que vale la pena avisar si cambian (el resto es ruido
# de redacción, no algo que le interese al usuario que tiene el piso en favoritos).
CAMPOS_NOTIFICABLES = ["precio", "m2", "habitaciones"]


def _trasladar_url(con, piso, legacy_url_like):
    """Un portal que cambia de formato de URL (Habitaclia, 2026-09) haría que
    cada anuncio ya guardado volviera a entrar como "nuevo", duplicado y sin
    su favorito/visto. Si el colector sabe cómo era la URL antigua (patrón
    LIKE), la fila existente se pasa a la URL nueva y se devuelve."""
    vieja = con.execute(
        "SELECT * FROM pisos WHERE portal = ? AND url LIKE ? LIMIT 1", (piso["portal"], legacy_url_like)
    ).fetchone()
    if not vieja:
        return None
    con.execute("UPDATE pisos SET id = ?, url = ? WHERE id = ?", (piso["id"], piso["url"], vieja["id"]))
    con.commit()
    log.info(f"URL trasladada al formato nuevo: {vieja['url']} -> {piso['url']}")
    return con.execute("SELECT * FROM pisos WHERE id = ?", (piso["id"],)).fetchone()


def upsert_piso(con, piso):
    """Inserta el piso si no existe (por id = hash de portal+url). Si ya
    existía, actualiza sus campos mutables con los valores recién
    recogidos.

    Devuelve (nuevo, cambios, favorito):
      - nuevo: True si se acaba de insertar por primera vez.
      - cambios: {campo: (valor_antes, valor_ahora)} -- solo de
        CAMPOS_NOTIFICABLES, y solo si nuevo es False (un piso recién
        insertado no ha "cambiado" de nada).
      - favorito: si el piso YA existente estaba marcado como favorito
        (para que quien llame decida si el cambio merece un aviso)."""
    piso = dict(piso)
    piso["id"] = make_id(piso["portal"], piso["url"])
    ahora = datetime.now(timezone.utc).isoformat()
    piso.setdefault("fecha_recogida", ahora)
    piso.setdefault("perfil", PERFIL_POR_DEFECTO)
    # Datos propios de cada portal (BER, coordenadas, baños...), en JSON.
    piso["extra"] = json.dumps(piso["extra"], ensure_ascii=False, sort_keys=True) if piso.get("extra") else None
    legacy_url_like = piso.pop("legacy_url_like", None)

    existente = con.execute("SELECT * FROM pisos WHERE id = ?", (piso["id"],)).fetchone()
    if not existente and legacy_url_like:
        existente = _trasladar_url(con, piso, legacy_url_like)
    if not existente:
        con.execute(
            """INSERT INTO pisos
               (id, portal, titulo, precio, m2, habitaciones, tipo, zona, ciudad, url, imagen,
                descripcion, fecha_publicacion, fecha_recogida, ultima_vista, notificado, perfil, extra)
               VALUES (:id, :portal, :titulo, :precio, :m2, :habitaciones, :tipo, :zona, :ciudad, :url, :imagen,
                       :descripcion, :fecha_publicacion, :fecha_recogida, :fecha_recogida, 0, :perfil, :extra)""",
            piso,
        )
        con.commit()
        return True, {}, False

    cambios = {}
    # ultima_vista se refresca siempre: es lo que dice si el anuncio sigue
    # publicado (ver _cortes_disponibilidad).
    sets, valores = ["ultima_vista = ?"], [ahora]
    if piso.get("precio") is not None and existente["precio"] is not None and piso["precio"] != existente["precio"]:
        # Para la insignia "Bajada de precio" de la web.
        sets.append("precio_anterior = ?")
        valores.append(existente["precio"])
    for campo in CAMPOS_ACTUALIZABLES:
        nuevo_valor = piso.get(campo)
        viejo_valor = existente[campo]
        if nuevo_valor is None or nuevo_valor == viejo_valor:
            continue
        sets.append(f"{campo} = ?")
        valores.append(nuevo_valor)
        if campo in CAMPOS_NOTIFICABLES:
            cambios[campo] = (viejo_valor, nuevo_valor)
    valores.append(piso["id"])
    con.execute(f"UPDATE pisos SET {', '.join(sets)} WHERE id = ?", valores)
    con.commit()
    return False, cambios, bool(existente["favorito"])


def pisos_pendientes_de_notificar(con, perfil=None):
    if perfil:
        return con.execute("SELECT * FROM pisos WHERE notificado = 0 AND perfil = ?", (perfil,)).fetchall()
    return con.execute("SELECT * FROM pisos WHERE notificado = 0").fetchall()


def marcar_notificado(con, piso_id):
    con.execute("UPDATE pisos SET notificado = 1 WHERE id = ?", (piso_id,))
    con.commit()


# --- Recogidas (estado de cada portal) --------------------------------------

def registrar_recogida(con, conteos, fecha=None):
    """conteos = {portal: nº de anuncios devueltos en esta corrida}, incluidos
    los que devolvieron 0 (es justo lo que interesa ver en la web)."""
    fecha = fecha or datetime.now(timezone.utc).isoformat()
    con.executemany(
        "INSERT INTO recogidas (fecha, portal, anuncios) VALUES (?, ?, ?)",
        [(fecha, portal, n) for portal, n in conteos.items()],
    )
    con.commit()


def estado_portales(con, portales=None):
    """Por portal: última corrida, cuántos anuncios trajo y la última vez que
    trajo alguno. Lista ordenada por nombre de portal. Una sola consulta,
    que tira del índice (portal, fecha). portales: limitar a esos (los del
    perfil que se está viendo)."""
    filas = con.execute(
        """SELECT r.portal, r.fecha AS ultima, r.anuncios,
                  (SELECT MAX(fecha) FROM recogidas ok
                    WHERE ok.portal = r.portal AND ok.anuncios > 0) AS ultima_ok
             FROM recogidas r
            WHERE r.fecha = (SELECT MAX(fecha) FROM recogidas u WHERE u.portal = r.portal)
            GROUP BY r.portal
            ORDER BY r.portal"""
    ).fetchall()
    return [dict(f) for f in filas if portales is None or f["portal"] in portales]


# Margen antes de dar un anuncio por retirado: tiene que llevar más de esto
# sin aparecer respecto a la última recogida buena de su portal. Absorbe una
# corrida suelta en la que el portal devolvió menos páginas de lo normal.
MARGEN_DISPONIBILIDAD = timedelta(hours=6)


def cortes_disponibilidad(con, estado=None):
    """{portal: fecha ISO}: un anuncio de ese portal con ultima_vista anterior
    a esa fecha ya no está publicado. Los portales sin ninguna recogida buena
    registrada (p. ej. uno que lleva tiempo roto) no aparecen: de sus
    anuncios no se puede saber nada y se tratan como disponibles.
    estado: el de estado_portales() si ya se tiene, para no repetir la consulta."""
    cortes = {}
    for e in (estado if estado is not None else estado_portales(con)):
        if e["ultima_ok"]:
            cortes[e["portal"]] = (datetime.fromisoformat(e["ultima_ok"]) - MARGEN_DISPONIBILIDAD).isoformat()
    return cortes


DIAS_ANTES_DE_BORRAR = 7
DIAS_HISTORIAL_RECOGIDAS = 30


def purgar_retirados(con, ahora=None):
    """Borra los anuncios que llevan más de DIAS_ANTES_DE_BORRAR días sin
    estar publicados (decisión del usuario, 2026-10-25: verlos una semana
    con la insignia "No disponible" basta para enterarse; luego sobran).

    Se usa la misma noción de "no disponible" que la web, así que un portal
    roto no borra nada: sin recogidas buenas no hay corte. Los favoritos no se
    borran nunca. Los que no tienen ultima_vista (guardados antes de que
    existiera) cuentan desde la primera recogida buena de su portal.
    Devuelve cuántos se borraron."""
    ahora = ahora or datetime.now(timezone.utc)
    limite = (ahora - timedelta(days=DIAS_ANTES_DE_BORRAR)).isoformat()
    borrados = 0
    for portal, corte in cortes_disponibilidad(con).items():
        primera = con.execute(
            "SELECT MIN(fecha) f FROM recogidas WHERE portal = ? AND anuncios > 0", (portal,)
        ).fetchone()["f"]
        cur = con.execute(
            """DELETE FROM pisos
                WHERE portal = ? AND favorito = 0
                  AND COALESCE(ultima_vista, ?) < ?
                  AND COALESCE(ultima_vista, ?) < ?""",
            (portal, primera, corte, primera, limite),
        )
        borrados += cur.rowcount
    con.execute(
        "DELETE FROM recogidas WHERE fecha < ?",
        ((ahora - timedelta(days=DIAS_HISTORIAL_RECOGIDAS)).isoformat(),),
    )
    con.commit()
    return borrados


def esta_disponible(piso, cortes):
    corte = cortes.get(piso["portal"])
    if corte is None:
        return True
    return piso["ultima_vista"] is not None and piso["ultima_vista"] >= corte


# --- Web (listado, orden, filtros, consultado/favorito) --------------------

COLUMNAS_ORDEN = {"precio", "m2", "habitaciones", "fecha_recogida", "precio_m2"}
POR_PAGINA = 24  # divisible entre 2, 3 y 4: la rejilla llena siempre la última fila
HORAS_NOVEDAD = 48


def _patron_busqueda(q):
    """Patrón LIKE para buscar q como texto literal, sin tildes ni mayúsculas.
    % y _ del usuario se escapan: si no, "50%" o "a_b" funcionarían como
    comodines."""
    q = normalizar(q).replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return f"%{q}%"


def _where(con, portal=None, ciudad=None, solo_favoritos=False, descartados=False, q=None,
           precio_min=None, precio_max=None, hab_min=None, tipo=None, ocultar_vistos=False,
           solo_disponibles=False, solo_novedades=False, solo_bajadas=False, cortes=None, perfil=None):
    """cortes: los de cortes_disponibilidad() si ya se tienen (la web los
    calcula una vez por petición y cuenta varias veces con ellos).
    perfil: la web lo pasa SIEMPRE (el de la sesión); None = todos, solo
    para main.py y los tests."""
    condiciones = []
    parametros = []
    if perfil:
        condiciones.append("perfil = ?")
        parametros.append(perfil)
    if portal:
        condiciones.append("portal = ?")
        parametros.append(portal)
    if ciudad:
        condiciones.append("ciudad = ?")
        parametros.append(ciudad)
    if solo_favoritos:
        condiciones.append("favorito = 1")
    # Los descartados solo se ven en su propia vista.
    condiciones.append("descartado = ?")
    parametros.append(1 if descartados else 0)
    if q:
        condiciones.append(
            "normalizar(COALESCE(titulo, '') || ' ' || COALESCE(zona, '') || ' ' || COALESCE(ciudad, '') || ' '"
            " || COALESCE(descripcion, '')) LIKE ? ESCAPE '\\'"
        )
        parametros.append(_patron_busqueda(q))
    if precio_min is not None:
        condiciones.append("precio >= ?")
        parametros.append(precio_min)
    if precio_max is not None:
        condiciones.append("precio <= ?")
        parametros.append(precio_max)
    if hab_min is not None:
        condiciones.append("habitaciones >= ?")
        parametros.append(hab_min)
    if tipo:
        condiciones.append("tipo = ?")
        parametros.append(tipo)
    if ocultar_vistos:
        condiciones.append("consultado = 0")
    if solo_disponibles:
        if cortes is None:
            cortes = cortes_disponibilidad(con)
        for portal_corte, corte in cortes.items():
            condiciones.append("NOT (portal = ? AND (ultima_vista IS NULL OR ultima_vista < ?))")
            parametros.extend([portal_corte, corte])
    if solo_novedades:
        condiciones.append("fecha_recogida >= ?")
        parametros.append((datetime.now(timezone.utc) - timedelta(hours=HORAS_NOVEDAD)).isoformat())
    if solo_bajadas:
        condiciones.append("precio_anterior IS NOT NULL AND precio < precio_anterior")
    where = f"WHERE {' AND '.join(condiciones)}" if condiciones else ""
    return where, parametros


def listar_pisos(con, orden="fecha_recogida", direccion="desc", pagina=1, por_pagina=POR_PAGINA, ajuste=0,
                 **filtros):
    """Los NULL (m2/habitaciones que el portal no dio) siempre van al final,
    sea cual sea la dirección -- si no, SQLite los mete primero al ordenar
    ASC, mezclados con los pisos más baratos/pequeños de verdad.

    Paginado -- una consulta sin límite con cientos de pisos (cada uno con
    su foto) tarda demasiado en cargar en el móvil, que es donde se usa
    sobre todo esta web. filtros: los de _where.

    ajuste: cuántos anuncios de páginas anteriores han salido de la lista
    desde que se cargaron (descartados, quitados de favoritos...). Sin
    restarlos, al pasar de página esos huecos los ocupan anuncios que
    estaban en la siguiente, y el usuario no los llega a ver nunca."""
    if orden not in COLUMNAS_ORDEN:
        orden = "fecha_recogida"
    expresion = "(precio * 1.0 / NULLIF(m2, 0))" if orden == "precio_m2" else orden
    direccion = "ASC" if direccion == "asc" else "DESC"
    pagina = max(1, pagina)

    where, parametros = _where(con, **filtros)
    return con.execute(
        f"""SELECT * FROM pisos {where}
            ORDER BY ({expresion} IS NULL), {expresion} {direccion}
            LIMIT ? OFFSET ?
            """,
        [*parametros, por_pagina, max(0, (pagina - 1) * por_pagina - max(0, ajuste))],
    ).fetchall()


def contar_pisos(con, **filtros):
    where, parametros = _where(con, **filtros)
    return con.execute(f"SELECT COUNT(*) c FROM pisos {where}", parametros).fetchone()["c"]


def _distintos(con, columna, perfil):
    sql = f"SELECT DISTINCT {columna} FROM pisos WHERE {columna} IS NOT NULL AND {columna} != ''"
    parametros = []
    if perfil:
        sql += " AND perfil = ?"
        parametros.append(perfil)
    return [f[0] for f in con.execute(sql + f" ORDER BY {columna}", parametros).fetchall()]


def portales_distintos(con, perfil=None):
    return _distintos(con, "portal", perfil)


def ciudades_distintas(con, perfil=None):
    return _distintos(con, "ciudad", perfil)


def tipos_distintos(con, perfil=None):
    return _distintos(con, "tipo", perfil)


# Los marcar_* devuelven si el anuncio existía (en ese perfil): puede
# haberse borrado (purgar_retirados) mientras la página seguía abierta, y la
# web tiene que decirlo en vez de dar por guardado algo que no se guardó. Con
# perfil, un anuncio de otro perfil cuenta como inexistente: desde la sesión
# de Irlanda no se puede tocar nada de La Rioja aunque se conozca su id.

def _marcar(con, piso_id, sets, perfil):
    sql = f"UPDATE pisos SET {sets} WHERE id = ?"
    parametros = [piso_id]
    if perfil:
        sql += " AND perfil = ?"
        parametros.append(perfil)
    cur = con.execute(sql, parametros)
    con.commit()
    return cur.rowcount > 0


def marcar_consultado(con, piso_id, consultado, perfil=None):
    return _marcar(con, piso_id, f"consultado = {1 if consultado else 0}", perfil)


def marcar_favorito(con, piso_id, favorito, perfil=None):
    """Guardar en favoritos también recupera el anuncio si estaba descartado:
    favorito y descartado son decisiones opuestas (ver marcar_descartado), y
    con las dos a la vez el anuncio no salía en ninguna vista."""
    return _marcar(con, piso_id, "favorito = 1, descartado = 0" if favorito else "favorito = 0", perfil)


def marcar_descartado(con, piso_id, descartado, perfil=None):
    """Descartar también quita de favoritos: son decisiones opuestas, y así
    un piso descartado no sigue mandando avisos de cambios por ntfy."""
    return _marcar(con, piso_id, "descartado = 1, favorito = 0" if descartado else "descartado = 0", perfil)


# --- Perfiles (credenciales) ---------------------------------------------------

def fijar_clave(con, perfil, clave):
    from werkzeug.security import generate_password_hash
    con.execute(
        """INSERT INTO perfiles (id, clave_hash, actualizado) VALUES (?, ?, ?)
           ON CONFLICT(id) DO UPDATE SET clave_hash = excluded.clave_hash, actualizado = excluded.actualizado""",
        (perfil, generate_password_hash(clave), datetime.now(timezone.utc).isoformat()),
    )
    con.commit()


def comprobar_clave(con, perfil, clave):
    from werkzeug.security import check_password_hash
    fila = con.execute("SELECT clave_hash FROM perfiles WHERE id = ?", (perfil,)).fetchone()
    return bool(fila) and check_password_hash(fila["clave_hash"], clave)

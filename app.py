#!/usr/bin/env python3
"""
Panel web de piso-radar, pensado para una red privada, con un login por perfil.
"""
import gzip
import json
import logging
import os
import secrets
import time
from collections import defaultdict, deque
from datetime import datetime, timedelta, timezone

from flask import Flask, request, jsonify, redirect, render_template, session, url_for
from werkzeug.middleware.proxy_fix import ProxyFix

import db
from config import PERFILES, PORTAL_DE_FUENTE, portales_del_perfil

app = Flask(__name__)
# Detrás de un proxy inverso: sin esto, request.remote_addr sería siempre la IP
# del proxy y el límite de intentos de login sería común para todos. Se fía del
# último salto de X-Forwarded-For; quien entre directo al puerto y falsee esa
# cabecera solo se salta su propio límite.
app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1)
log = logging.getLogger("piso-radar.webapp")

# --- Sesiones -------------------------------------------------------------------
# Un login por perfil (config.PERFILES): La Rioja,
# Irlanda para el usuario y su pareja (login conjunto). Lo de un perfil no se
# ve ni se toca desde el otro. Solo accesible por red privada, pero con login
# igualmente: son personas distintas.
app.secret_key = os.environ.get("FLASK_SECRET_KEY") or secrets.token_urlsafe(48)
if not os.environ.get("FLASK_SECRET_KEY"):
    log.warning("FLASK_SECRET_KEY no está en .env: las sesiones se perderán al reiniciar el servicio")
app.config.update(
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE="Lax",
    # Un año: se usa sobre todo en el móvil y no tiene sentido pedir la
    # contraseña cada semana.
    PERMANENT_SESSION_LIFETIME=timedelta(days=365),
)

# Intentos de login fallidos por IP (en memoria del proceso): tras
# MAX_INTENTOS en VENTANA_INTENTOS se bloquea un rato. Con varios procesos de
# gunicorn el límite efectivo se multiplica por su número; de sobra para una
# web solo accesible por red privada.
MAX_INTENTOS = 5
VENTANA_INTENTOS = 15 * 60
_fallos_login = defaultdict(deque)


def _perfil_actual():
    perfil = session.get("perfil")
    return perfil if perfil in PERFILES else None


@app.before_request
def _requerir_sesion():
    if request.endpoint in ("login", "static") or _perfil_actual():
        return None
    if request.method == "POST":
        return jsonify({"error": "La sesión ha caducado. Vuelve a entrar."}), 401
    return redirect(url_for("login", siguiente=request.full_path.rstrip("?")))


def _destino_seguro(destino):
    """Solo rutas de esta misma web tras el login ("//otra.web" o
    "https://..." llevarían fuera)."""
    return destino if destino and destino.startswith("/") and not destino.startswith("//") else url_for("index")


@app.route("/login", methods=["GET", "POST"])
def login():
    error = None
    siguiente = request.values.get("siguiente", "")
    if request.method == "POST":
        ip = request.remote_addr or "?"
        ahora = time.monotonic()
        fallos = _fallos_login[ip]
        while fallos and ahora - fallos[0] > VENTANA_INTENTOS:
            fallos.popleft()
        if len(fallos) >= MAX_INTENTOS:
            return render_template("login.html", error="Demasiados intentos. Espera unos minutos.",
                                   perfiles=PERFILES, siguiente=siguiente), 429
        perfil = request.form.get("perfil", "")
        con = db.connect()
        try:
            correcto = perfil in PERFILES and db.comprobar_clave(con, perfil, request.form.get("clave", ""))
        finally:
            con.close()
        if correcto:
            fallos.clear()
            session.clear()
            session.permanent = True
            session["perfil"] = perfil
            return redirect(_destino_seguro(siguiente))
        fallos.append(ahora)
        error = "Perfil o contraseña incorrectos."
    return render_template("login.html", error=error, perfiles=PERFILES, siguiente=siguiente,
                           perfil_elegido=request.form.get("perfil")), (401 if error else 200)


@app.route("/salir")
def salir():
    session.clear()
    return redirect(url_for("login"))

# Las acciones (POST) solo las manda el JavaScript de la propia web, que
# añade esta cabecera. Un formulario o un fetch "simple" desde otra web que
# el usuario tenga abierta no puede ponerla sin una petición CORS previa que
# este servidor nunca aprueba: sin ella, cualquier página podría descartar o
# marcar anuncios con un POST a esta web.
CABECERA_ACCION = "X-Piso-Radar"


@app.before_request
def _proteger_acciones():
    # El login es un formulario normal: sin la cabecera (lo peor que puede
    # hacer otra web con él es iniciar sesión con una contraseña que conoce).
    if request.method == "POST" and request.endpoint != "login" and request.headers.get(CABECERA_ACCION) != "1":
        return jsonify({"error": "petición no permitida"}), 403


# La página lleva ~150 KB de HTML (tarjetas + datos de la vista rápida) y el
# servidor de desarrollo de Flask no comprime: en el móvil se nota. gzip la
# deja en una fracción.
_TIPOS_COMPRIMIBLES = ("text/html", "application/json")


@app.after_request
def _comprimir(resp):
    if (resp.status_code == 200 and not resp.direct_passthrough
            and resp.mimetype in _TIPOS_COMPRIMIBLES
            and "gzip" in request.headers.get("Accept-Encoding", "")
            and "Content-Encoding" not in resp.headers):
        datos = resp.get_data()
        if len(datos) > 1024:
            resp.set_data(gzip.compress(datos, compresslevel=6))
            resp.headers["Content-Encoding"] = "gzip"
    resp.vary.add("Accept-Encoding")
    return resp


MESES = ["ene", "feb", "mar", "abr", "may", "jun", "jul", "ago", "sep", "oct", "nov", "dic"]


def _fecha_relativa(iso):
    if not iso:
        return ""
    try:
        fecha = datetime.fromisoformat(iso)
    except ValueError:
        return iso
    diff = datetime.now(timezone.utc) - fecha
    minutos = diff.total_seconds() / 60
    if minutos < 1:
        return "ahora mismo"
    if minutos < 60:
        return f"hace {int(minutos)} min"
    if minutos < 24 * 60:
        return f"hace {int(minutos // 60)} h"
    if minutos < 48 * 60:
        return "ayer"
    if diff.days < 30:
        return f"hace {diff.days} días"
    return f"{fecha.day} {MESES[fecha.month - 1]}"


@app.template_filter("euros")
def _euros(valor):
    """1250 -> "1.250 €" (separador de miles español)."""
    if valor is None:
        return ""
    return f"{int(round(valor)):,} €".replace(",", ".")


# Un único desplegable "campo_dirección" en vez de columnas de tabla
# clicables -- en móvil es mucho más fácil de tocar que enlaces pequeños de
# flecha, y no exige espacio horizontal para una cabecera de tabla.
OPCIONES_ORDEN = [
    ("fecha_recogida_desc", "Más recientes"),
    ("precio_asc", "Más baratos"),
    ("precio_desc", "Más caros"),
    ("precio_m2_asc", "Menor precio por m²"),
    ("m2_desc", "Más grandes"),
    ("habitaciones_desc", "Más habitaciones"),
    ("fecha_recogida_asc", "Más antiguos"),
]
_ORDEN_DIR_VALIDOS = {valor for valor, _ in OPCIONES_ORDEN}
ORDEN_DIR_POR_DEFECTO = "fecha_recogida_desc"


VISTAS = {
    "index": {"titulo": "Explorar", "solo_favoritos": False, "descartados": False},
    "favoritos": {"titulo": "Favoritos", "solo_favoritos": True, "descartados": False},
    "descartados": {"titulo": "Descartados", "solo_favoritos": False, "descartados": True},
}


# Tope para los números de la URL: SQLite no admite enteros de más de 64
# bits y "?precio_max=99999999999999999999" tiraba la página con un 500.
MAX_ENTERO_URL = 10 ** 9


def _entero(nombre):
    try:
        valor = int(request.args.get(nombre, ""))
    except ValueError:
        return None
    return max(-MAX_ENTERO_URL, min(valor, MAX_ENTERO_URL))


def _leer_filtros(vista, perfil):
    """Filtros de la query string, ya validados. Devuelve (args, filtros):
    args son los parámetros tal cual para reconstruir URLs, filtros los
    kwargs de db.listar_pisos/contar_pisos."""
    args = {}
    for nombre in ("q", "portal", "ciudad", "tipo"):
        valor = request.args.get(nombre, "").strip()
        if valor:
            args[nombre] = valor
    for nombre in ("precio_min", "precio_max", "hab"):
        valor = _entero(nombre)
        if valor is not None and valor > 0:
            args[nombre] = valor
    for nombre in ("no_vistos", "retirados", "novedades", "bajadas"):
        if request.args.get(nombre) == "1":
            args[nombre] = 1
    if args.get("precio_min") and args.get("precio_max") and args["precio_min"] > args["precio_max"]:
        # Elegidos al revés en los desplegables: se entiende lo que se quería
        # en vez de devolver 0 resultados sin explicación.
        args["precio_min"], args["precio_max"] = args["precio_max"], args["precio_min"]

    filtros = {
        "portal": args.get("portal"), "ciudad": args.get("ciudad"), "tipo": args.get("tipo"),
        "q": args.get("q"), "precio_min": args.get("precio_min"), "precio_max": args.get("precio_max"),
        "hab_min": args.get("hab"), "ocultar_vistos": "no_vistos" in args,
        "solo_novedades": "novedades" in args, "solo_bajadas": "bajadas" in args,
        "solo_favoritos": VISTAS[vista]["solo_favoritos"], "descartados": VISTAS[vista]["descartados"],
        # En Explorar se esconden por defecto los anuncios que ya no están
        # publicados; en Favoritos y Descartados se ven siempre (con su
        # insignia), para no perder el rastro de lo que uno guardó.
        "solo_disponibles": vista == "index" and "retirados" not in args,
        "perfil": perfil,
    }
    return args, filtros


def _chips_activos(args, url_con):
    """Filtros aplicados como chips con su "×" (patrón de Idealista/Airbnb):
    cada uno enlaza a la misma búsqueda sin ese filtro."""
    etiquetas = {
        "q": lambda v: f"«{v}»",
        "precio_min": lambda v: f"Desde {_euros(v)}",
        "precio_max": lambda v: f"Hasta {_euros(v)}",
        "hab": lambda v: f"{v}+ habitaciones",
        "tipo": lambda v: v,
        "ciudad": lambda v: v,
        "portal": lambda v: v,
        "no_vistos": lambda v: "Sin ver",
        "novedades": lambda v: "Novedades",
        "bajadas": lambda v: "Bajadas de precio",
        "retirados": lambda v: "Incluye no disponibles",
    }
    return [
        {"clave": nombre, "etiqueta": etiquetas[nombre](valor), "url": url_con(**{nombre: None})}
        for nombre, valor in args.items() if nombre in etiquetas
    ]


def _paginas_visibles(pagina, total):
    """[1, None, 4, 5, 6, None, 12] -- None es un hueco "…"."""
    if total <= 7:
        return list(range(1, total + 1))
    visibles = {1, total, pagina - 1, pagina, pagina + 1}
    resultado, anterior = [], 0
    for n in sorted(v for v in visibles if 1 <= v <= total):
        if n - anterior > 1:
            resultado.append(None)
        resultado.append(n)
        anterior = n
    return resultado


def _url_segura(url):
    """Solo http(s): la URL viene de lo que publique el portal y se pone en
    un href; un "javascript:..." se ejecutaría al pulsar el enlace."""
    return url if url and url.lower().startswith(("https://", "http://")) else "#"


def _a_tarjeta(fila, cortes, ahora):
    f = dict(fila)
    f["url"] = _url_segura(f["url"])
    try:
        f["extra"] = json.loads(f.get("extra") or "{}")
    except ValueError:
        f["extra"] = {}
    f["fecha_relativa"] = _fecha_relativa(f["fecha_recogida"])
    f["disponible"] = db.esta_disponible(fila, cortes)
    try:
        f["nuevo"] = ahora - datetime.fromisoformat(f["fecha_recogida"]) < timedelta(hours=db.HORAS_NOVEDAD)
    except (TypeError, ValueError):
        f["nuevo"] = False
    anterior = f.get("precio_anterior")
    f["bajada"] = anterior - f["precio"] if anterior and f["precio"] and f["precio"] < anterior else None
    f["precio_m2"] = round(f["precio"] / f["m2"], 1) if f["precio"] and f["m2"] else None
    return f


CAMPOS_VISTA_RAPIDA = ["id", "titulo", "precio", "precio_anterior", "bajada", "m2", "habitaciones", "tipo", "zona",
                       "ciudad", "url", "imagen", "descripcion", "portal", "fecha_relativa", "disponible", "nuevo",
                       "precio_m2", "extra"]


def _render_listado(vista):
    orden_dir = request.args.get("orden_dir", ORDEN_DIR_POR_DEFECTO)
    if orden_dir not in _ORDEN_DIR_VALIDOS:
        orden_dir = ORDEN_DIR_POR_DEFECTO
    orden, direccion = orden_dir.rsplit("_", 1)
    perfil = _perfil_actual()
    config_perfil = PERFILES[perfil]
    args, filtros = _leer_filtros(vista, perfil)
    pagina = max(1, _entero("pagina") or 1)
    ajuste = max(0, _entero("ajuste") or 0)

    con = db.connect()
    try:
        # Estado de los portales y cortes de disponibilidad: una vez por
        # petición, se reutilizan en todos los recuentos de abajo.
        estado = db.estado_portales(con, portales_del_perfil(perfil))
        cortes = db.cortes_disponibilidad(con, estado)
        filtros["cortes"] = cortes
        barra = _datos_barra(con, estado, perfil)
        total = db.contar_pisos(con, **filtros)

        def paginas_para(ajuste):
            # Con ajuste, las páginas siguientes empiezan "ajuste" anuncios
            # antes: hacen falta las que cubran total + ajuste, o los últimos
            # quedarían sin página que los muestre.
            return max(1, -(-(total + ajuste) // db.POR_PAGINA))  # división hacia arriba
        pagina = min(pagina, paginas_para(ajuste))
        ajuste = min(ajuste, (pagina - 1) * db.POR_PAGINA)
        total_paginas = paginas_para(ajuste)
        filas_db = db.listar_pisos(con, orden=orden, direccion=direccion, pagina=pagina, ajuste=ajuste, **filtros)
        portales = db.portales_distintos(con, perfil)
        ciudades = db.ciudades_distintas(con, perfil)
        tipos = db.tipos_distintos(con, perfil)
        # Contadores de las píldoras rápidas, con el resto de filtros aplicados.
        base_rapidos = {**filtros, "solo_novedades": False, "solo_bajadas": False}
        n_novedades = db.contar_pisos(con, **{**base_rapidos, "solo_novedades": True})
        n_bajadas = db.contar_pisos(con, **{**base_rapidos, "solo_bajadas": True})
        n_retirados = 0
        if vista == "index" and "retirados" not in args:
            n_retirados = db.contar_pisos(con, **{**filtros, "solo_disponibles": False}) - total
    finally:
        con.close()

    ahora = datetime.now(timezone.utc)
    filas = [_a_tarjeta(f, cortes, ahora) for f in filas_db]
    # Lo que necesita la vista rápida (JS), sin campos internos.
    datos_js = [
        {
            **{k: f[k] for k in CAMPOS_VISTA_RAPIDA},
            "favorito": bool(f["favorito"]), "consultado": bool(f["consultado"]),
            "ultima_vista_relativa": _fecha_relativa(f["ultima_vista"]),
        }
        for f in filas
    ]

    def url_con(**cambios):
        """URL de esta misma vista con los filtros actuales más `cambios`
        (None quita el parámetro). Cambiar un filtro vuelve a la página 1."""
        nuevos = {"orden_dir": orden_dir, **args, **cambios}
        if "pagina" not in cambios:
            nuevos.pop("pagina", None)
        if nuevos.get("orden_dir") == ORDEN_DIR_POR_DEFECTO:
            nuevos.pop("orden_dir")
        return url_for(vista, **{k: v for k, v in nuevos.items() if v is not None})

    def url_pagina(destino):
        # El ajuste solo afecta a las páginas de después: lo de antes no se movió.
        return url_con(pagina=destino if destino > 1 else None,
                       ajuste=ajuste if ajuste and destino > pagina else None)

    n_filtros_panel = sum(1 for k in ("precio_min", "precio_max", "hab", "tipo", "ciudad", "portal", "no_vistos", "retirados")
                          if k in args)

    # El valor activo tiene que estar en su desplegable aunque no sea uno de
    # los de la lista (precio escrito a mano en la URL, municipio cuyos
    # anuncios ya se borraron...): si no, el navegador muestra la primera
    # opción y el filtro se pierde al tocar cualquier otro.
    precios = sorted(set(config_perfil["precios"]) | {v for v in (args.get("precio_min"), args.get("precio_max")) if v})
    if args.get("ciudad") and args["ciudad"] not in ciudades:
        ciudades = sorted([*ciudades, args["ciudad"]])
    if args.get("portal") and args["portal"] not in portales:
        portales = sorted([*portales, args["portal"]])

    return render_template(
        "listado.html",
        **barra,
        perfil=config_perfil,
        opciones_hab=_opciones_habitaciones(config_perfil["habitaciones"], args.get("hab")),
        vista=vista,
        titulo_vista=VISTAS[vista]["titulo"],
        filas=filas,
        datos_js=datos_js,
        portales=portales,
        ciudades=ciudades,
        tipos=tipos,
        precios=precios,
        args=args,
        chips=_chips_activos(args, url_con),
        n_filtros_panel=n_filtros_panel,
        opciones_orden=OPCIONES_ORDEN,
        orden_dir_activo=orden_dir,
        solo_favoritos=VISTAS[vista]["solo_favoritos"],
        pagina=pagina,
        ajuste=ajuste,
        total_paginas=total_paginas,
        paginas_visibles=_paginas_visibles(pagina, total_paginas),
        total_pisos=total,
        n_novedades=n_novedades,
        n_bajadas=n_bajadas,
        n_retirados=n_retirados,
        url_con=url_con,
        url_pagina=url_pagina,
    )


def _opciones_habitaciones(minimos, activo):
    """Botones de habitaciones: el primero ("N+", el mínimo del perfil) es
    "sin filtro". Un valor que no sea de la lista (URL a mano) se añade para
    que quede marcado y no se pierda al tocar otro filtro."""
    valores = sorted(set(minimos) | ({activo} if activo else set()))
    return [("" if i == 0 and v == minimos[0] else str(v), f"{v}+") for i, v in enumerate(valores)]


def _datos_barra(con, estado, perfil):
    """Contadores del menú y estado de los portales (barra superior)."""
    n_favoritos = db.contar_pisos(con, solo_favoritos=True, perfil=perfil)
    n_descartados = db.contar_pisos(con, descartados=True, perfil=perfil)
    estado = [dict(e) for e in estado]
    # Cada cuánto se revisa cada portal: su intervalo, y como poco lo que
    # tarda el cron (30 min).
    intervalos = {PORTAL_DE_FUENTE[m]: max(minutos, 30) for m, minutos in PERFILES[perfil]["fuentes"].items()}
    for e in estado:
        e["cada"] = intervalos.get(e["portal"], 30)
        e["ultima_relativa"] = _fecha_relativa(e["ultima"])
        e["ultima_ok_relativa"] = _fecha_relativa(e["ultima_ok"]) if e["ultima_ok"] else None
    ultima = max((e["ultima"] for e in estado), default=None)
    return {
        "n_favoritos": n_favoritos,
        "n_descartados": n_descartados,
        "estado_portales": estado,
        "portales_caidos": [e for e in estado if e["anuncios"] == 0],
        "ultima_actualizacion": _fecha_relativa(ultima) if ultima else None,
    }


@app.route("/")
def index():
    return _render_listado("index")


@app.route("/favoritos")
def favoritos():
    return _render_listado("favoritos")


@app.route("/descartados")
def descartados():
    return _render_listado("descartados")


def _accion(campo, funcion):
    piso_id = request.form.get("piso_id", "")
    if not piso_id:
        return jsonify({"error": "piso_id requerido"}), 400
    valor = request.form.get(campo) == "1"
    con = db.connect()
    try:
        # Con el perfil de la sesión: un anuncio de otro perfil es "inexistente".
        existia = funcion(con, piso_id, valor, perfil=_perfil_actual())
    finally:
        con.close()
    if not existia:
        # Lo más probable: purgar_retirados lo borró con la página abierta.
        return jsonify({"error": "El anuncio ya no existe. Recarga la página."}), 404
    return jsonify({"ok": True})


@app.route("/consultado", methods=["POST"])
def marcar_consultado():
    return _accion("consultado", db.marcar_consultado)


@app.route("/favorito", methods=["POST"])
def marcar_favorito():
    return _accion("favorito", db.marcar_favorito)


@app.route("/descartado", methods=["POST"])
def marcar_descartado():
    return _accion("descartado", db.marcar_descartado)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
    # En producción corre con gunicorn; esto es solo para probar a mano, en
    # una red privada (no exponer a internet).
    app.run(host="0.0.0.0", port=5051, debug=False)

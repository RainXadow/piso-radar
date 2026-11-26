"""
Flujos completos como en producción: una BD SQLite real (temporal), main.main()
recogiendo de colectores simulados en varias corridas con el reloj avanzando
días, y la web usada por HTTP igual que la usa el JavaScript del navegador.
Lo único simulado es lo que sale del servidor: los portales y ntfy.
"""
import gzip
import re
from datetime import datetime, timedelta, timezone

import pytest

import app as appmod
import db
import main
import notifier
from config import PERFILES
from tests.conftest import entrar, make_piso


# --- Infraestructura ---------------------------------------------------------

@pytest.fixture
def reloj(monkeypatch):
    """Reloj controlable para db y app (ultima_vista, recogidas, purga,
    "Nuevo", fechas relativas). Empieza el 2026-10-01 a las 10:00 UTC."""
    estado = {"ahora": datetime(2026, 10, 1, 10, 0, tzinfo=timezone.utc)}

    class Falso(datetime):
        @classmethod
        def now(cls, tz=None):
            return estado["ahora"] if tz else estado["ahora"].replace(tzinfo=None)

    monkeypatch.setattr(db, "datetime", Falso)
    monkeypatch.setattr(appmod, "datetime", Falso)

    class Reloj:
        def avanzar(self, **kw):
            estado["ahora"] += timedelta(**kw)

    return Reloj()


@pytest.fixture
def web(con):
    appmod.app.config["TESTING"] = True
    with appmod.app.test_client() as c:
        c.environ_base["HTTP_X_PISO_RADAR"] = "1"
        entrar(c)
        yield c


@pytest.fixture
def portales(monkeypatch):
    """Lo que "publica" cada portal en la próxima corrida de main.main(), y
    los avisos que se mandarían por ntfy."""
    publicado = {"fotocasa": [], "pisos.com": [], "habitaclia": []}
    avisos = []
    monkeypatch.setattr(main.fotocasa, "collect", lambda zonas: list(publicado["fotocasa"]))
    monkeypatch.setattr(main.pisos_com, "collect", lambda zonas: list(publicado["pisos.com"]))
    monkeypatch.setattr(main.habitaclia, "collect", lambda zonas: list(publicado["habitaclia"]))
    monkeypatch.setattr(notifier, "_notify", lambda titulo, mensaje, **kw: avisos.append(
        {"titulo": titulo, "mensaje": mensaje, **kw}) or True)

    # Estos flujos son del perfil de La Rioja; los de varios perfiles están
    # en test_perfiles.py.
    monkeypatch.setattr(main, "PERFILES", {"la-rioja": PERFILES["la-rioja"]})
    return {"publicado": publicado, "avisos": avisos}


def anuncio(clave, portal="fotocasa", **kw):
    datos = {"titulo": f"Piso {clave}", "precio": 800, "habitaciones": 3}
    datos.update(kw)
    return make_piso(portal=portal, url=f"https://{portal}/anuncio/{clave}", **datos)


def id_de(portal, clave):
    return db.make_id(portal, f"https://{portal}/anuncio/{clave}")


def ids_en(html):
    return re.findall(r'id="fila-([0-9a-f]+)"', html)


# --- Ciclo de vida completo --------------------------------------------------

class TestCicloDeVida:
    def test_anuncios_que_desaparecen_favoritos_descartes_y_purga(self, con, web, reloj, portales):
        pub, avisos = portales["publicado"], portales["avisos"]

        # Día 0: el portal publica A, B y C.
        pub["fotocasa"] = [anuncio("A"), anuncio("B", precio=900), anuncio("C")]
        main.main()
        assert len(avisos) == 1 and "3 nuevos" in avisos[0]["titulo"]
        assert avisos[0]["click"].endswith("?novedades=1")
        assert set(ids_en(web.get("/").text)) == {id_de("fotocasa", k) for k in "ABC"}

        # El usuario guarda A y B y descarta C, desde la web.
        for k in "AB":
            assert web.post("/favorito", data={"piso_id": id_de("fotocasa", k), "favorito": "1"}).status_code == 200
        assert web.post("/descartado", data={"piso_id": id_de("fotocasa", "C"), "descartado": "1"}).status_code == 200

        # Día 2: A y C ya no están publicados; B baja de precio.
        reloj.avanzar(days=2)
        pub["fotocasa"] = [anuncio("B", precio=850)]
        main.main()
        assert len(avisos) == 2
        assert "1 favorito actualizado" in avisos[1]["titulo"]
        assert "900€ → 850€" in avisos[1]["mensaje"]
        assert avisos[1]["click"].endswith("/favoritos")

        explorar = web.get("/").text
        assert ids_en(explorar) == [id_de("fotocasa", "B")]  # A no disponible, C descartado
        assert "mostrar 1 no disponibles" in explorar
        assert "<s>900 €</s>" in explorar and "−50 €" in explorar
        assert ids_en(web.get("/?bajadas=1").text) == [id_de("fotocasa", "B")]

        favoritos = web.get("/favoritos").text
        assert set(ids_en(favoritos)) == {id_de("fotocasa", "A"), id_de("fotocasa", "B")}
        tarjeta_a = favoritos.split(f'id="fila-{id_de("fotocasa", "A")}"')[1].split("</article>")[0]
        assert "No disponible" in tarjeta_a

        # Día 10: más de 7 días sin A ni C. C se borra; A se queda por ser favorito.
        reloj.avanzar(days=8)
        main.main()
        restantes = {f["url"].rsplit("/", 1)[1] for f in con.execute("SELECT url FROM pisos")}
        assert restantes == {"A", "B"}
        assert "No has descartado nada" in web.get("/descartados").text
        assert len(avisos) == 2  # sin novedades, sin aviso

    def test_anuncio_que_vuelve_a_publicarse_vuelve_a_estar_disponible(self, con, web, reloj, portales):
        pub = portales["publicado"]
        pub["fotocasa"] = [anuncio("A"), anuncio("B")]
        main.main()
        reloj.avanzar(days=2)
        pub["fotocasa"] = [anuncio("B")]
        main.main()
        assert ids_en(web.get("/").text) == [id_de("fotocasa", "B")]
        reloj.avanzar(days=1)
        pub["fotocasa"] = [anuncio("A"), anuncio("B")]
        main.main()
        assert set(ids_en(web.get("/").text)) == {id_de("fotocasa", "A"), id_de("fotocasa", "B")}

    def test_portal_roto_no_oculta_ni_borra_sus_anuncios_y_se_avisa(self, con, web, reloj, portales):
        pub = portales["publicado"]
        pub["fotocasa"] = [anuncio("F")]
        pub["habitaclia"] = [anuncio("H", portal="habitaclia")]
        main.main()
        # Habitaclia deja de devolver nada durante tres semanas.
        pub["habitaclia"] = []
        for _ in range(21):
            reloj.avanzar(days=1)
            main.main()
        html = web.get("/").text
        assert "Habitaclia no está devolviendo anuncios" in html
        # Lo último que vio sigue contando como disponible y no se ha borrado.
        assert id_de("habitaclia", "H") in ids_en(html)

    def test_un_colector_que_revienta_no_tumba_a_los_demas(self, con, web, reloj, portales, monkeypatch):
        pub = portales["publicado"]
        pub["fotocasa"] = [anuncio("F")]
        pub["habitaclia"] = [anuncio("H", portal="habitaclia")]

        def roto(zonas):
            raise KeyError("el portal cambió su HTML")
        monkeypatch.setattr(main.pisos_com, "collect", roto)
        main.main()

        portales_guardados = {f["portal"] for f in con.execute("SELECT portal FROM pisos")}
        assert portales_guardados == {"fotocasa", "habitaclia"}
        conteos = {f["portal"]: f["anuncios"] for f in con.execute("SELECT portal, anuncios FROM recogidas")}
        assert conteos == {"fotocasa": 1, "pisos.com": 0, "habitaclia": 1}
        assert "pisos.com no está devolviendo anuncios" in web.get("/").text

    def test_repetidos_en_una_corrida_cuentan_una_vez(self, con, portales):
        portales["publicado"]["pisos.com"] = [anuncio("X", portal="pisos.com")] * 2
        main.main()
        assert con.execute("SELECT anuncios FROM recogidas WHERE portal = 'pisos.com'").fetchone()[0] == 1
        assert "1 nuevo" in portales["avisos"][0]["titulo"]

    def test_cambio_de_formato_de_url_conserva_el_favorito_y_no_avisa(self, con, web, reloj, portales):
        pub, avisos = portales["publicado"], portales["avisos"]
        vieja = make_piso(portal="habitaclia", habitaciones=3,
                          url="https://www.habitaclia.com/alquiler-piso-centro-logrono-i30265000000466.htm?hab=3")
        pub["habitaclia"] = [vieja]
        main.main()
        id_viejo = db.make_id("habitaclia", vieja["url"])
        web.post("/favorito", data={"piso_id": id_viejo, "favorito": "1"})

        reloj.avanzar(hours=1)
        nueva = make_piso(portal="habitaclia", habitaciones=3,
                          url="https://www.habitaclia.com/alquiler/planta-intermedia/centro/logrono/62a6d768/d")
        pub["habitaclia"] = [{**nueva, "legacy_url_like": "%-i30265000000466.htm%"}]
        main.main()

        filas = con.execute("SELECT url, favorito FROM pisos").fetchall()
        assert [(f["url"], f["favorito"]) for f in filas] == [(nueva["url"], 1)]
        assert len(avisos) == 1  # solo el de la primera corrida
        assert nueva["url"] in web.get("/favoritos").text


# --- Paginación: ningún anuncio se queda sin ver ----------------------------

class TestPaginacionSinSaltos:
    def _sembrar(self, con, n):
        # Precios distintos y ordenables: "Más baratos" da un orden estable.
        for i in range(n):
            db.upsert_piso(con, anuncio(f"p{i:03d}", precio=500 + i))
        return [id_de("fotocasa", f"p{i:03d}") for i in range(n)]

    def _recorrer(self, web, quitar, accion, extra=""):
        """Recorre todas las páginas como el usuario: en cada una "quita"
        algunos anuncios (como los clics en el navegador) y pasa a la
        siguiente con el ajuste que calcula el JavaScript. Devuelve todos
        los ids vistos, en orden."""
        vistos, pagina, ajuste = [], 1, 0
        while True:
            url = f"/?orden_dir=precio_asc{extra}&pagina={pagina}" + (f"&ajuste={ajuste}" if ajuste else "")
            html = web.get(url).text
            ids = ids_en(html)
            if not ids:
                break
            vistos.extend(ids)
            for pid in ids:
                if pid in quitar:
                    ruta, datos = accion(pid)
                    assert web.post(ruta, data=datos).status_code == 200
                    ajuste += 1
            if f"pagina={pagina + 1}" not in html:
                break
            pagina += 1
        return vistos

    def test_descartando_por_el_camino_se_ven_todos_una_vez(self, con, web):
        todos = self._sembrar(con, 80)
        quitar = set(todos[::4])  # uno de cada cuatro, repartidos por todas las páginas
        vistos = self._recorrer(web, quitar, lambda pid: ("/descartado", {"piso_id": pid, "descartado": "1"}))
        assert vistos == todos

    def test_sin_ajuste_se_saltarian_anuncios(self, con, web):
        # Comprueba que el escenario de arriba era un problema real.
        todos = self._sembrar(con, 30)
        for pid in todos[:3]:
            web.post("/descartado", data={"piso_id": pid, "descartado": "1"})
        pagina2 = ids_en(web.get("/?orden_dir=precio_asc&pagina=2").text)
        assert todos[24] not in pagina2  # sin ajuste, 24..26 no salen ni en la 1 ni en la 2
        pagina2_ajustada = ids_en(web.get("/?orden_dir=precio_asc&pagina=2&ajuste=3").text)
        assert pagina2_ajustada == todos[24:]

    def test_con_ocultar_vistos_abrir_anuncios_tampoco_salta_ninguno(self, con, web):
        todos = self._sembrar(con, 60)
        quitar = set(todos[1::3])
        vistos = self._recorrer(web, quitar, lambda pid: ("/consultado", {"piso_id": pid, "consultado": "1"}),
                                extra="&no_vistos=1")
        assert vistos == todos

    def test_los_enlaces_a_paginas_siguientes_conservan_el_ajuste(self, con, web):
        self._sembrar(con, 80)
        html = web.get("/?orden_dir=precio_asc&pagina=2&ajuste=5").text
        assert 'href="/?orden_dir=precio_asc&amp;pagina=3&amp;ajuste=5"' in html
        # Hacia atrás no: esas páginas no se movieron.
        assert 'href="/?orden_dir=precio_asc">1</a>' in html

    def test_ajuste_absurdo_no_rompe_la_pagina(self, con, web):
        todos = self._sembrar(con, 30)
        assert ids_en(web.get("/?orden_dir=precio_asc&pagina=2&ajuste=999999").text) == todos[:24]
        assert web.get("/?ajuste=-5").status_code == 200


# --- Búsqueda ----------------------------------------------------------------

class TestBusqueda:
    @pytest.fixture
    def pisos(self, con):
        datos = {
            "atico": anuncio("atico", titulo="ÁTICO REFORMADO", zona="Centro", ciudad="Logroño"),
            "nino": anuncio("nino", titulo="Piso", zona="Oeste", ciudad="Nájera",
                            descripcion="Junto al colegio Niño Jesús"),
            "haro": anuncio("haro", titulo="Casa", zona=None, ciudad="Haro", descripcion="Con jardín"),
            "porc": anuncio("porc", titulo="Fianza 50% menos", zona=None, ciudad="Arnedo", descripcion=None),
        }
        for p in datos.values():
            db.upsert_piso(con, p)
        return {k: id_de("fotocasa", k) for k in datos}

    @pytest.mark.parametrize("q", ["atico", "Ático", "ÁTICO", "átICo reformado"])
    def test_sin_importar_tildes_ni_mayusculas(self, web, pisos, q):
        assert ids_en(web.get(f"/?q={q}").text) == [pisos["atico"]]

    def test_busca_en_la_descripcion_y_en_la_ubicacion(self, web, pisos):
        assert ids_en(web.get("/?q=nino jesus").text) == [pisos["nino"]]
        assert ids_en(web.get("/?q=logrono").text) == [pisos["atico"]]
        assert ids_en(web.get("/?q=centro logroño").text) == [pisos["atico"]]

    def test_porcentaje_y_guion_bajo_son_literales(self, web, pisos):
        assert ids_en(web.get("/?q=50%25").text) == [pisos["porc"]]
        assert ids_en(web.get("/?q=%25").text) == [pisos["porc"]]  # "%" a secas no lo encuentra todo
        assert ids_en(web.get("/?q=_").text) == []


# --- Seguridad y robustez de las acciones ------------------------------------

class TestProteccionAcciones:
    def test_post_desde_otra_web_se_rechaza_y_no_cambia_nada(self, con, piso):
        appmod.app.config["TESTING"] = True
        with appmod.app.test_client() as ajeno:  # sin la cabecera del JS de la web
            entrar(ajeno)  # aunque haya sesión: la otra web usa la cookie del navegador
            for ruta, campo in [("/descartado", "descartado"), ("/favorito", "favorito"), ("/consultado", "consultado")]:
                assert ajeno.post(ruta, data={"piso_id": piso["id"], campo: "1"}).status_code == 403
        fila = con.execute("SELECT descartado, favorito, consultado FROM pisos").fetchone()
        assert tuple(fila) == (0, 0, 0)

    def test_las_paginas_se_siguen_viendo_sin_la_cabecera(self, con):
        with appmod.app.test_client() as navegador:
            entrar(navegador)
            assert navegador.get("/").status_code == 200

    def test_anuncio_borrado_con_la_pagina_abierta(self, con, web, reloj, piso):
        con.execute("DELETE FROM pisos")
        con.commit()
        for ruta, campo in [("/favorito", "favorito"), ("/descartado", "descartado"), ("/consultado", "consultado")]:
            resp = web.post(ruta, data={"piso_id": piso["id"], campo: "1"})
            assert resp.status_code == 404
            assert "Recarga la página" in resp.get_json()["error"]

    def test_corazon_en_descartados_lo_recupera_a_favoritos(self, con, web, piso):
        web.post("/descartado", data={"piso_id": piso["id"], "descartado": "1"})
        web.post("/favorito", data={"piso_id": piso["id"], "favorito": "1"})
        assert ids_en(web.get("/favoritos").text) == [piso["id"]]
        assert ids_en(web.get("/descartados").text) == []

    def test_enlace_javascript_del_portal_no_llega_al_href(self, con, web):
        db.upsert_piso(con, anuncio("malo") | {"url": "javascript:alert(document.cookie)"})
        html = web.get("/").text
        assert "javascript:alert" not in html


class TestParametrosRaros:
    def test_numeros_gigantes_no_tiran_la_pagina(self, web, con, piso):
        for q in ["precio_max=99999999999999999999", "hab=99999999999999999999",
                  "pagina=99999999999999999999", "precio_min=-99999999999999999999"]:
            assert web.get(f"/?{q}").status_code == 200, q

    def test_minimo_y_maximo_al_reves_se_intercambian(self, con, web):
        for clave, precio in [("barato", 500), ("medio", 900), ("caro", 1500)]:
            db.upsert_piso(con, anuncio(clave, precio=precio))
        html = web.get("/?precio_min=1200&precio_max=800").text
        assert ids_en(html) == [id_de("fotocasa", "medio")]
        assert "Desde 800 €" in html and "Hasta 1.200 €" in html

    def test_valores_fuera_de_la_lista_quedan_seleccionados(self, con, web, piso):
        html = web.get("/?precio_max=950&ciudad=Pueblo Borrado&portal=idealista").text
        assert '<option value="950" selected>' in html
        assert '<option value="Pueblo Borrado" selected>' in html
        assert '<option value="idealista" selected>' in html


# --- Eficiencia ----------------------------------------------------------------

class TestEficiencia:
    def test_una_conexion_y_pocas_consultas_por_pagina(self, con, web, reloj, portales, monkeypatch):
        portales["publicado"]["fotocasa"] = [anuncio(f"x{i}") for i in range(40)]
        for _ in range(48 * 3):  # tres días de recogidas cada 30 min
            main.main()
            reloj.avanzar(minutes=30)

        sentencias, conexiones = [], []
        original = db.connect

        def contando():
            c = original()
            conexiones.append(c)
            c.set_trace_callback(sentencias.append)
            return c
        monkeypatch.setattr(db, "connect", contando)

        assert web.get("/").status_code == 200
        assert len(conexiones) == 1
        # Antes: 2 conexiones y 34 consultas (el estado de los portales se
        # recalculaba 6 veces). Hoy son 11.
        assert len(sentencias) <= 12, sentencias
        assert sum("FROM recogidas" in s for s in sentencias) == 1

    def test_el_esquema_se_aplica_una_vez_por_proceso(self, con, monkeypatch):
        llamadas = []
        original = db.SCHEMA_PATH.read_text
        monkeypatch.setattr(type(db.SCHEMA_PATH), "read_text", lambda self, *a, **k: llamadas.append(1) or original())
        for _ in range(5):
            db.connect().close()
        assert llamadas == []  # la fixture con ya lo aplicó para esta BD

    def test_el_html_va_comprimido_si_el_navegador_lo_acepta(self, con, web):
        for i in range(24):
            db.upsert_piso(con, anuncio(f"g{i}", descripcion="Descripción larga del piso. " * 20))
        plano = web.get("/")
        comprimido = web.get("/", headers={"Accept-Encoding": "gzip, deflate, br"})
        assert "Content-Encoding" not in plano.headers
        assert comprimido.headers["Content-Encoding"] == "gzip"
        assert gzip.decompress(comprimido.data) == plano.data
        assert len(comprimido.data) < len(plano.data) / 4
        assert "Accept-Encoding" in comprimido.headers["Vary"]

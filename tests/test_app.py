from datetime import datetime, timedelta, timezone

import pytest

import app as appmod
import db
from tests.conftest import entrar


@pytest.fixture
def client(con):
    # con ya dejó db.DB_PATH apuntando a la BD temporal -- las rutas de
    # app.py abren su propia conexión con db.connect(), pero al mismo
    # fichero parcheado.
    appmod.app.config["TESTING"] = True
    with appmod.app.test_client() as c:
        # Como el JavaScript de la web: sin esta cabecera los POST dan 403
        # (ver test_web_flujos.TestProteccionAcciones).
        c.environ_base["HTTP_X_PISO_RADAR"] = "1"
        entrar(c)
        yield c


class TestRutas:
    def test_index_responde_200(self, client):
        assert client.get("/").status_code == 200

    def test_favoritos_responde_200(self, client):
        assert client.get("/favoritos").status_code == 200

    def test_ruta_inexistente_da_404(self, client):
        assert client.get("/no-existe").status_code == 404

    def test_index_muestra_los_pisos_guardados(self, client, con, piso_factory):
        db.upsert_piso(con, piso_factory(titulo="Piso Único De Prueba"))
        resp = client.get("/")
        assert "Piso Único De Prueba" in resp.text

    def test_favoritos_no_muestra_pisos_sin_marcar(self, client, con, piso_factory):
        db.upsert_piso(con, piso_factory(titulo="No Favorito Distintivo", url="a"))
        resp = client.get("/favoritos")
        assert "No Favorito Distintivo" not in resp.text

    def test_favoritos_muestra_los_marcados(self, client, con, piso_factory):
        db.upsert_piso(con, piso_factory(titulo="Sí Favorito Distintivo", url="a"))
        fila = con.execute("SELECT id FROM pisos WHERE url='a'").fetchone()
        db.marcar_favorito(con, fila["id"], True)
        resp = client.get("/favoritos")
        assert "Sí Favorito Distintivo" in resp.text

    def test_index_filtra_por_portal(self, client, con, piso_factory):
        db.upsert_piso(con, piso_factory(portal="fotocasa", url="a", titulo="De Fotocasa"))
        db.upsert_piso(con, piso_factory(portal="pisos.com", url="b", titulo="De Pisos Com"))
        resp = client.get("/?portal=fotocasa")
        assert "De Fotocasa" in resp.text
        assert "De Pisos Com" not in resp.text


class TestPaginacion:
    def _sembrar(self, con, piso_factory, n):
        for i in range(n):
            db.upsert_piso(con, piso_factory(url=f"p{i}", titulo=f"Piso número {i}"))

    def test_sin_suficientes_pisos_no_muestra_controles(self, client, con, piso_factory):
        self._sembrar(con, piso_factory, 5)
        resp = client.get("/")
        assert 'class="paginacion"' not in resp.text

    def test_con_mas_de_24_pisos_pagina_a_24(self, client, con, piso_factory):
        self._sembrar(con, piso_factory, 30)
        resp = client.get("/")
        assert resp.text.count('id="fila-') == 24

    def test_segunda_pagina_trae_el_resto(self, client, con, piso_factory):
        self._sembrar(con, piso_factory, 30)
        resp = client.get("/?pagina=2")
        assert resp.text.count('id="fila-') == 6

    def test_muestra_controles_cuando_hay_mas_de_una_pagina(self, client, con, piso_factory):
        self._sembrar(con, piso_factory, 30)
        resp = client.get("/")
        assert 'class="paginacion"' in resp.text
        assert "Siguiente" in resp.text

    def test_pagina_fuera_de_rango_se_ajusta_a_la_ultima(self, client, con, piso_factory):
        self._sembrar(con, piso_factory, 30)
        resp = client.get("/?pagina=999")
        assert resp.status_code == 200
        assert resp.text.count('id="fila-') == 6

    def test_pagina_no_numerica_no_revienta(self, client, con, piso_factory):
        self._sembrar(con, piso_factory, 5)
        resp = client.get("/?pagina=no-es-un-numero")
        assert resp.status_code == 200

    def test_cambiar_de_pagina_conserva_los_filtros(self, client, con, piso_factory):
        for i in range(30):
            db.upsert_piso(con, piso_factory(portal="fotocasa", url=f"a{i}"))
        for i in range(3):
            db.upsert_piso(con, piso_factory(portal="pisos.com", url=f"b{i}"))
        resp = client.get("/?portal=fotocasa")
        assert 'href="/?portal=fotocasa&amp;pagina=2"' in resp.text

    def test_cambiar_de_pagina_conserva_el_orden_no_por_defecto(self, client, con, piso_factory):
        self._sembrar(con, piso_factory, 30)
        resp = client.get("/?orden_dir=precio_asc")
        assert 'href="/?orden_dir=precio_asc&amp;pagina=2"' in resp.text

    def test_muestra_numeros_de_pagina(self, client, con, piso_factory):
        self._sembrar(con, piso_factory, 30)
        resp = client.get("/")
        assert '<span class="actual" aria-current="page">1</span>' in resp.text
        assert 'href="/?pagina=2">2</a>' in resp.text


class TestMarcarConsultado:
    def test_marca_consultado(self, client, con, piso):
        resp = client.post("/consultado", data={"piso_id": piso["id"], "consultado": "1"})
        assert resp.status_code == 200
        assert resp.get_json() == {"ok": True}
        fila = con.execute("SELECT consultado FROM pisos WHERE id=?", (piso["id"],)).fetchone()
        assert fila["consultado"] == 1

    def test_desmarca_consultado(self, client, con, piso):
        db.marcar_consultado(con, piso["id"], True)
        client.post("/consultado", data={"piso_id": piso["id"], "consultado": "0"})
        fila = con.execute("SELECT consultado FROM pisos WHERE id=?", (piso["id"],)).fetchone()
        assert fila["consultado"] == 0

    def test_sin_piso_id_da_400(self, client):
        resp = client.post("/consultado", data={"consultado": "1"})
        assert resp.status_code == 400

    def test_piso_id_inexistente_da_404_con_mensaje(self, client):
        resp = client.post("/consultado", data={"piso_id": "no-existe", "consultado": "1"})
        assert resp.status_code == 404
        assert "ya no existe" in resp.get_json()["error"]


class TestMarcarFavorito:
    def test_marca_favorito(self, client, con, piso):
        resp = client.post("/favorito", data={"piso_id": piso["id"], "favorito": "1"})
        assert resp.status_code == 200
        fila = con.execute("SELECT favorito FROM pisos WHERE id=?", (piso["id"],)).fetchone()
        assert fila["favorito"] == 1

    def test_desmarca_favorito(self, client, con, piso):
        db.marcar_favorito(con, piso["id"], True)
        client.post("/favorito", data={"piso_id": piso["id"], "favorito": "0"})
        fila = con.execute("SELECT favorito FROM pisos WHERE id=?", (piso["id"],)).fetchone()
        assert fila["favorito"] == 0

    def test_sin_piso_id_da_400(self, client):
        resp = client.post("/favorito", data={"favorito": "1"})
        assert resp.status_code == 400


class TestFechaRelativa:
    def _iso(self, hace):
        return (datetime.now(timezone.utc) - hace).isoformat()

    def test_vacio_devuelve_cadena_vacia(self):
        assert appmod._fecha_relativa(None) == ""
        assert appmod._fecha_relativa("") == ""

    def test_menos_de_un_minuto(self):
        assert appmod._fecha_relativa(self._iso(timedelta(seconds=20))) == "ahora mismo"

    def test_menos_de_una_hora(self):
        assert appmod._fecha_relativa(self._iso(timedelta(minutes=10))) == "hace 10 min"

    def test_varias_horas(self):
        assert appmod._fecha_relativa(self._iso(timedelta(hours=5))) == "hace 5 h"

    def test_ayer(self):
        assert appmod._fecha_relativa(self._iso(timedelta(hours=30))) == "ayer"

    def test_varios_dias(self):
        assert appmod._fecha_relativa(self._iso(timedelta(days=3))) == "hace 3 días"

    def test_mas_de_un_mes_da_la_fecha(self):
        assert appmod._fecha_relativa("2026-09-03T10:00:00+00:00") == "3 sep"

    def test_fecha_invalida_se_devuelve_tal_cual(self):
        assert appmod._fecha_relativa("no-es-una-fecha") == "no-es-una-fecha"


class TestOrden:
    def test_todas_las_opciones_declaradas_dan_campo_y_direccion_validos(self):
        for valor, _ in appmod.OPCIONES_ORDEN:
            campo, direccion = valor.rsplit("_", 1)
            assert direccion in ("asc", "desc")

    def test_orden_por_precio_ascendente(self, client, con, piso_factory):
        db.upsert_piso(con, piso_factory(url="a", precio=900, titulo="Caro"))
        db.upsert_piso(con, piso_factory(url="b", precio=600, titulo="Barato"))
        resp = client.get("/?orden_dir=precio_asc")
        assert resp.text.index("Barato") < resp.text.index("Caro")

    def test_orden_por_precio_descendente(self, client, con, piso_factory):
        db.upsert_piso(con, piso_factory(url="a", precio=900, titulo="Caro"))
        db.upsert_piso(con, piso_factory(url="b", precio=600, titulo="Barato"))
        resp = client.get("/?orden_dir=precio_desc")
        assert resp.text.index("Caro") < resp.text.index("Barato")

    def test_valor_desconocido_cae_al_orden_por_defecto_sin_reventar(self, client):
        resp = client.get("/?orden_dir=algo-raro; DROP TABLE pisos;--")
        assert resp.status_code == 200

    def test_opcion_activa_queda_seleccionada_en_el_desplegable(self, client):
        resp = client.get("/?orden_dir=precio_asc")
        assert 'value="precio_asc" selected' in resp.text


class TestFiltroCiudad:
    def test_filtra_por_ciudad(self, client, con, piso_factory):
        db.upsert_piso(con, piso_factory(ciudad="Logroño", url="a", titulo="De Logroño"))
        db.upsert_piso(con, piso_factory(ciudad="Nájera", url="b", titulo="De Nájera"))
        resp = client.get("/?ciudad=Nájera")
        assert "De Nájera" in resp.text
        assert "De Logroño" not in resp.text

    def test_desplegable_incluye_las_ciudades_guardadas(self, client, con, piso_factory):
        db.upsert_piso(con, piso_factory(ciudad="Calahorra", url="a"))
        resp = client.get("/")
        assert "Calahorra" in resp.text

    def test_ciudad_y_portal_combinados(self, client, con, piso_factory):
        db.upsert_piso(con, piso_factory(portal="fotocasa", ciudad="Logroño", url="a", titulo="Coincide"))
        db.upsert_piso(con, piso_factory(portal="pisos.com", ciudad="Logroño", url="b", titulo="Otro Portal"))
        resp = client.get("/?portal=fotocasa&ciudad=Logroño")
        assert "Coincide" in resp.text
        assert "Otro Portal" not in resp.text


class TestImagenEnListado:
    def test_con_imagen_incluye_el_src(self, client, con, piso_factory):
        db.upsert_piso(con, piso_factory(imagen="https://example.com/foto-distintiva.jpg", url="a"))
        resp = client.get("/")
        assert "https://example.com/foto-distintiva.jpg" in resp.text

    def test_sin_imagen_muestra_marcador(self, client, con, piso_factory):
        db.upsert_piso(con, piso_factory(imagen=None, url="a"))
        resp = client.get("/")
        assert "sin-foto" in resp.text


class TestEuros:
    def test_separador_de_miles(self):
        assert appmod._euros(1250) == "1.250 €"

    def test_menos_de_mil(self):
        assert appmod._euros(800) == "800 €"

    def test_none_da_cadena_vacia(self):
        assert appmod._euros(None) == ""


class TestPaginasVisibles:
    def test_pocas_paginas_salen_todas(self):
        assert appmod._paginas_visibles(2, 5) == [1, 2, 3, 4, 5]

    def test_muchas_paginas_con_huecos(self):
        assert appmod._paginas_visibles(6, 12) == [1, None, 5, 6, 7, None, 12]

    def test_en_la_primera(self):
        assert appmod._paginas_visibles(1, 12) == [1, 2, None, 12]


class TestFiltrosNuevos:
    def test_busqueda_por_texto(self, client, con, piso_factory):
        db.upsert_piso(con, piso_factory(url="a", titulo="Ático en Gran Vía"))
        db.upsert_piso(con, piso_factory(url="b", titulo="Casa en Haro"))
        resp = client.get("/?q=gran vía")
        assert "Ático en Gran Vía" in resp.text
        assert "Casa en Haro" not in resp.text

    def test_rango_de_precio(self, client, con, piso_factory):
        db.upsert_piso(con, piso_factory(url="a", precio=500, titulo="Barato"))
        db.upsert_piso(con, piso_factory(url="b", precio=900, titulo="Medio"))
        db.upsert_piso(con, piso_factory(url="c", precio=1500, titulo="Caro"))
        resp = client.get("/?precio_min=600&precio_max=1000")
        assert "Medio" in resp.text
        assert "Barato" not in resp.text
        assert "Caro" not in resp.text

    def test_habitaciones_minimas(self, client, con, piso_factory):
        db.upsert_piso(con, piso_factory(url="a", habitaciones=3, titulo="Tres hab"))
        db.upsert_piso(con, piso_factory(url="b", habitaciones=4, titulo="Cuatro hab"))
        resp = client.get("/?hab=4")
        assert "Cuatro hab" in resp.text
        assert "Tres hab" not in resp.text

    def test_tipo(self, client, con, piso_factory):
        db.upsert_piso(con, piso_factory(url="a", tipo="Casa", titulo="Una casa"))
        db.upsert_piso(con, piso_factory(url="b", tipo="Piso", titulo="Un piso"))
        resp = client.get("/?tipo=Casa")
        assert "Una casa" in resp.text
        assert "Un piso" not in resp.text

    def test_ocultar_vistos(self, client, con, piso_factory):
        db.upsert_piso(con, piso_factory(url="a", titulo="Ya visto"))
        db.upsert_piso(con, piso_factory(url="b", titulo="Sin ver"))
        fila = con.execute("SELECT id FROM pisos WHERE url='a'").fetchone()
        db.marcar_consultado(con, fila["id"], True)
        resp = client.get("/?no_vistos=1")
        assert "Ya visto" not in resp.text
        assert "Sin ver" in resp.text

    def test_parametros_numericos_basura_se_ignoran(self, client, con, piso_factory):
        db.upsert_piso(con, piso_factory(url="a", titulo="Sigue saliendo"))
        resp = client.get("/?precio_max=abc&hab=;DROP")
        assert resp.status_code == 200
        assert "Sigue saliendo" in resp.text

    def test_chip_de_filtro_activo_enlaza_sin_ese_filtro(self, client, con, piso_factory):
        db.upsert_piso(con, piso_factory(url="a", precio=800))
        resp = client.get("/?precio_max=1000&hab=4")
        assert "Hasta 1.000 €" in resp.text
        assert 'href="/?hab=4" aria-label="Quitar filtro Hasta 1.000 €"' in resp.text

    def test_formulario_de_filtros_recuerda_lo_elegido(self, client):
        resp = client.get("/?precio_max=1000&hab=4")
        assert '<option value="1000" selected>' in resp.text
        assert 'name="hab" value="4" checked' in resp.text


class TestDescartados:
    def test_descartado_no_sale_en_explorar_ni_favoritos(self, client, con, piso):
        db.marcar_favorito(con, piso["id"], True)
        client.post("/descartado", data={"piso_id": piso["id"], "descartado": "1"})
        assert f'id="fila-{piso["id"]}"' not in client.get("/").text
        assert f'id="fila-{piso["id"]}"' not in client.get("/favoritos").text

    def test_descartado_sale_en_su_vista(self, client, con, piso):
        client.post("/descartado", data={"piso_id": piso["id"], "descartado": "1"})
        resp = client.get("/descartados")
        assert f'id="fila-{piso["id"]}"' in resp.text

    def test_recuperar(self, client, con, piso):
        client.post("/descartado", data={"piso_id": piso["id"], "descartado": "1"})
        client.post("/descartado", data={"piso_id": piso["id"], "descartado": "0"})
        assert f'id="fila-{piso["id"]}"' in client.get("/").text

    def test_sin_piso_id_da_400(self, client):
        assert client.post("/descartado", data={"descartado": "1"}).status_code == 400

    def test_vista_vacia_explica_que_hacer(self, client):
        assert "No has descartado nada" in client.get("/descartados").text


class TestDisponibilidadYEstado:
    def test_retirado_se_oculta_en_explorar_y_se_ofrece_verlo(self, client, con, piso_factory):
        viejo = (datetime.now(timezone.utc) - timedelta(days=3)).isoformat()
        db.upsert_piso(con, piso_factory(url="a", titulo="Ya Retirado", fecha_recogida=viejo))
        con.execute("UPDATE pisos SET ultima_vista = ?", (viejo,))
        con.commit()
        db.upsert_piso(con, piso_factory(url="b", titulo="Sigue Publicado"))
        db.registrar_recogida(con, {"fotocasa": 1})
        resp = client.get("/")
        assert "Sigue Publicado" in resp.text
        assert "Ya Retirado" not in resp.text
        assert "mostrar 1 no disponibles" in resp.text
        resp = client.get("/?retirados=1")
        assert "Ya Retirado" in resp.text
        assert "No disponible" in resp.text

    def test_favorito_retirado_sigue_en_favoritos_con_insignia(self, client, con, piso_factory):
        viejo = (datetime.now(timezone.utc) - timedelta(days=3)).isoformat()
        db.upsert_piso(con, piso_factory(url="a", titulo="Favorito Retirado"))
        con.execute("UPDATE pisos SET ultima_vista = ?, favorito = 1", (viejo,))
        con.commit()
        db.registrar_recogida(con, {"fotocasa": 5})
        resp = client.get("/favoritos")
        assert "Favorito Retirado" in resp.text
        assert "badge-retirado" in resp.text

    def test_aviso_si_un_portal_devuelve_cero(self, client, con):
        db.registrar_recogida(con, {"fotocasa": 10, "habitaclia": 0})
        resp = client.get("/")
        assert "Habitaclia no está devolviendo anuncios" in resp.text
        assert "Fotocasa no está devolviendo" not in resp.text

    def test_estado_de_portales_en_la_barra(self, client, con):
        db.registrar_recogida(con, {"fotocasa": 10})
        resp = client.get("/")
        assert "Actualizado ahora mismo" in resp.text
        assert "10 anuncios" in resp.text

    def test_sin_recogidas_no_hay_estado(self, client):
        assert 'class="estado"' not in client.get("/").text


class TestInsignias:
    def test_bajada_de_precio(self, client, con, piso_factory):
        db.upsert_piso(con, piso_factory(url="a", precio=900))
        db.upsert_piso(con, piso_factory(url="a", precio=850))
        resp = client.get("/")
        assert "−50 €" in resp.text
        assert "<s>900 €</s>" in resp.text

    def test_pildora_de_bajadas_filtra(self, client, con, piso_factory):
        db.upsert_piso(con, piso_factory(url="a", precio=900, titulo="Rebajado"))
        db.upsert_piso(con, piso_factory(url="a", precio=850, titulo="Rebajado"))
        db.upsert_piso(con, piso_factory(url="b", precio=700, titulo="Igual"))
        resp = client.get("/?bajadas=1")
        assert "Rebajado" in resp.text
        assert "Igual" not in resp.text

    def test_nuevo(self, client, con, piso_factory):
        db.upsert_piso(con, piso_factory(url="a"))
        assert "badge-nuevo" in client.get("/").text

    def test_contador_de_favoritos_en_el_menu(self, client, con, piso):
        db.marcar_favorito(con, piso["id"], True)
        assert 'data-contador="favoritos">1</span>' in client.get("/").text

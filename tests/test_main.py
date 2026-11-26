import pytest

import db
import main
from config import PERFILES


@pytest.fixture(autouse=True)
def solo_la_rioja(monkeypatch):
    """Estos tests son del perfil de La Rioja (el de siempre); los de varios
    perfiles están en test_perfiles.py."""
    monkeypatch.setattr(main, "PERFILES", {"la-rioja": PERFILES["la-rioja"]})


def fake_piso(**overrides):
    p = {
        "portal": "fotocasa", "titulo": "Piso en Centro", "precio": 800, "m2": 70,
        "habitaciones": 3, "tipo": "Piso", "zona": "Centro", "ciudad": "Logroño",
        "url": "https://example.com/1", "imagen": "https://example.com/foto.jpg",
        "descripcion": "desc", "fecha_publicacion": None,
    }
    p.update(overrides)
    return p


class TestRecoger:
    def test_concatena_las_tres_fuentes_y_marca_el_perfil(self, monkeypatch):
        monkeypatch.setattr(main.fotocasa, "collect", lambda zonas: [fake_piso(portal="fotocasa", url="a")])
        monkeypatch.setattr(main.pisos_com, "collect", lambda zonas: [fake_piso(portal="pisos.com", url="b")])
        monkeypatch.setattr(main.habitaclia, "collect", lambda zonas: [fake_piso(portal="habitaclia", url="c")])
        pisos = main.recoger("la-rioja", ["fotocasa", "pisos_com", "habitaclia"])
        assert len(pisos) == 3
        assert {p["portal"] for p in pisos} == {"fotocasa", "pisos.com", "habitaclia"}
        assert {p["perfil"] for p in pisos} == {"la-rioja"}

    def test_pasa_las_zonas_del_perfil_a_cada_colector(self, monkeypatch):
        recibido = {}

        def capturar(nombre):
            def _c(zonas):
                recibido[nombre] = zonas
                return []
            return _c

        monkeypatch.setattr(main.fotocasa, "collect", capturar("fotocasa"))
        monkeypatch.setattr(main.pisos_com, "collect", capturar("pisos_com"))
        monkeypatch.setattr(main.habitaclia, "collect", capturar("habitaclia"))
        main.recoger("la-rioja", ["fotocasa", "pisos_com", "habitaclia"])
        assert recibido == {nombre: PERFILES["la-rioja"]["zonas"] for nombre in ("fotocasa", "pisos_com", "habitaclia")}


def _mockear_recogida(monkeypatch, respuestas_fotocasa):
    """Solo fotocasa devuelve datos (una lista de respuestas, una por
    llamada sucesiva) -- pisos.com/habitaclia siempre vacíos, para aislar
    el comportamiento de reintento de main() sin ruido de las tres fuentes."""
    secuencia = iter(respuestas_fotocasa)
    monkeypatch.setattr(main.fotocasa, "collect", lambda zonas: next(secuencia, []))
    monkeypatch.setattr(main.pisos_com, "collect", lambda zonas: [])
    monkeypatch.setattr(main.habitaclia, "collect", lambda zonas: [])


class TestMain:
    def test_guarda_pisos_nuevos_que_pasan_el_filtro(self, con, monkeypatch):
        _mockear_recogida(monkeypatch, [[fake_piso(url="a")]])
        monkeypatch.setattr(main, "notificar_resumen", lambda con, cambios, perfil=None: None)
        main.main()
        assert con.execute("SELECT COUNT(*) c FROM pisos").fetchone()["c"] == 1

    def test_descarta_pisos_nuevos_que_no_pasan_el_filtro(self, con, monkeypatch):
        _mockear_recogida(monkeypatch, [[fake_piso(url="a", habitaciones=2)]])  # menos de MIN_HABITACIONES
        monkeypatch.setattr(main, "notificar_resumen", lambda con, cambios, perfil=None: None)
        main.main()
        assert con.execute("SELECT COUNT(*) c FROM pisos").fetchone()["c"] == 0

    def test_actualiza_un_piso_existente_aunque_ya_no_pase_el_filtro(self, con, piso_factory, monkeypatch):
        db.upsert_piso(con, piso_factory(url="a", habitaciones=3))
        _mockear_recogida(monkeypatch, [[fake_piso(url="a", habitaciones=2)]])  # ahora se sale del filtro
        monkeypatch.setattr(main, "notificar_resumen", lambda con, cambios, perfil=None: None)
        main.main()
        fila = con.execute("SELECT habitaciones FROM pisos WHERE url='a'").fetchone()
        assert fila["habitaciones"] == 2  # se actualiza igual, no se descarta un piso ya guardado

    def test_no_revienta_si_la_recogida_esta_vacia(self, con, monkeypatch):
        _mockear_recogida(monkeypatch, [[], []])
        monkeypatch.setattr(main, "notificar_resumen", lambda con, cambios, perfil=None: None)
        main.main()  # no debe lanzar excepción
        assert con.execute("SELECT COUNT(*) c FROM pisos").fetchone()["c"] == 0

    def test_cambio_en_favorito_se_pasa_a_notificar_resumen(self, con, piso_factory, monkeypatch):
        db.upsert_piso(con, piso_factory(url="a", precio=800, habitaciones=3, titulo="Piso en Centro"))
        fila = con.execute("SELECT id FROM pisos WHERE url='a'").fetchone()
        db.marcar_favorito(con, fila["id"], True)
        _mockear_recogida(monkeypatch, [[fake_piso(url="a", precio=600, titulo="Piso en Centro")]])
        capturado = {}
        monkeypatch.setattr(main, "notificar_resumen", lambda con, cambios, perfil=None: capturado.update(cambios=cambios))
        main.main()
        assert len(capturado["cambios"]) == 1
        assert capturado["cambios"][0]["cambios"] == {"precio": (800, 600)}
        assert capturado["cambios"][0]["titulo"] == "Piso en Centro"

    def test_cambio_en_no_favorito_no_se_reporta(self, con, piso_factory, monkeypatch):
        db.upsert_piso(con, piso_factory(url="a", precio=800))  # no marcado favorito
        _mockear_recogida(monkeypatch, [[fake_piso(url="a", precio=600)]])
        capturado = {}
        monkeypatch.setattr(main, "notificar_resumen", lambda con, cambios, perfil=None: capturado.update(cambios=cambios))
        main.main()
        assert capturado["cambios"] == []

    def test_piso_nuevo_no_genera_cambio_de_favorito(self, con, monkeypatch):
        _mockear_recogida(monkeypatch, [[fake_piso(url="a")]])
        capturado = {}
        monkeypatch.setattr(main, "notificar_resumen", lambda con, cambios, perfil=None: capturado.update(cambios=cambios))
        main.main()
        assert capturado["cambios"] == []

    def test_llama_a_notificar_resumen_exactamente_una_vez(self, con, monkeypatch):
        _mockear_recogida(monkeypatch, [[fake_piso(url="a")]])
        llamadas = {"n": 0}
        monkeypatch.setattr(main, "notificar_resumen", lambda con, cambios, perfil=None: llamadas.update(n=llamadas["n"] + 1))
        main.main()
        assert llamadas["n"] == 1


class TestRegistroDeRecogidas:
    def test_registra_cada_portal_incluidos_los_que_dan_cero(self, con, monkeypatch):
        _mockear_recogida(monkeypatch, [[fake_piso(url="a"), fake_piso(url="b")]])
        monkeypatch.setattr(main, "notificar_resumen", lambda con, cambios, perfil=None: None)
        main.main()
        filas = {f["portal"]: f["anuncios"] for f in con.execute("SELECT portal, anuncios FROM recogidas")}
        assert filas == {"fotocasa": 2, "pisos.com": 0, "habitaclia": 0}

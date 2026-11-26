import json

import pytest

from collectors import fotocasa as fc


class FakeResp:
    def __init__(self, status_code=200, text=""):
        self.status_code = status_code
        self.text = text


def realestate(rawPrice="800", surface=70, rooms=2, buildingType="Flat",
                district="Centro", municipality="Logroño", detail="/es/alquiler/vivienda/logrono/x/1/d",
                description="Piso luminoso.", location="Centro",
                multimedia=(("https://static.fotocasa.es/images/ads/1.jpg", "kitchen"),)):
    features = []
    if surface is not None:
        features.append({"key": "surface", "value": surface})
    if rooms is not None:
        features.append({"key": "rooms", "value": rooms})
    return {
        "rawPrice": rawPrice,
        "buildingType": buildingType,
        "address": {"district": district, "municipality": municipality},
        "features": features,
        "detail": {"es-ES": detail},
        "description": description,
        "location": location,
        "multimedia": [{"type": "image", "src": src, "roomType": rt} for src, rt in multimedia],
    }


def json_html(realestates, count=None, params=None):
    payload = {
        "initialSearch": {
            "params": params or {},
            "result": {"realEstates": realestates, "count": count if count is not None else len(realestates)},
        }
    }
    return f'<html><script type="application/json" id="__initial_props__">{json.dumps(payload)}</script></html>'


UBICACION = {"level": "PROVINCE", "locationCode": "26"}


@pytest.fixture(autouse=True)
def sin_esperas(monkeypatch):
    monkeypatch.setattr(fc.time, "sleep", lambda *a, **k: None)
    monkeypatch.setattr(fc, "jitter", lambda base: 0)


class TestFeature:
    def test_devuelve_el_valor_de_la_clave(self):
        assert fc._feature([{"key": "surface", "value": 80}], "surface") == 80

    def test_devuelve_none_si_no_esta(self):
        assert fc._feature([{"key": "surface", "value": 80}], "rooms") is None

    def test_lista_vacia(self):
        assert fc._feature([], "surface") is None


class TestParseRealestate:
    def test_campos_basicos(self):
        piso = fc._parse_realestate(realestate(rawPrice="950", surface=85, rooms=3))
        assert piso["precio"] == 950
        assert piso["m2"] == 85
        assert piso["habitaciones"] == 3
        assert piso["zona"] == "Centro"
        assert piso["ciudad"] == "Logroño"
        assert piso["portal"] == "fotocasa"
        assert piso["url"] == "https://www.fotocasa.es/es/alquiler/vivienda/logrono/x/1/d"

    def test_precio_con_decimales(self):
        piso = fc._parse_realestate(realestate(rawPrice="950.0"))
        assert piso["precio"] == 950

    def test_precio_none_no_revienta(self):
        piso = fc._parse_realestate(realestate(rawPrice=None))
        assert piso["precio"] is None

    def test_precio_no_numerico_no_revienta(self):
        piso = fc._parse_realestate(realestate(rawPrice="no-es-un-numero"))
        assert piso["precio"] is None

    def test_tipo_flat_es_piso(self):
        assert fc._parse_realestate(realestate(buildingType="Flat"))["tipo"] == "Piso"

    def test_tipo_desconocido_es_casa(self):
        assert fc._parse_realestate(realestate(buildingType="Chalet"))["tipo"] == "Casa"

    def test_titulo_usa_location_si_esta(self):
        assert fc._parse_realestate(realestate(location="Gran Via"))["titulo"] == "Gran Via"

    def test_titulo_cae_a_district_si_no_hay_location(self):
        piso = fc._parse_realestate(realestate(location=None, district="Casco Antiguo"))
        assert piso["titulo"] == "Casco Antiguo"

    def test_titulo_por_defecto_si_no_hay_nada(self):
        piso = fc._parse_realestate(realestate(location=None, district=None))
        assert piso["titulo"] == "Vivienda en alquiler"

    def test_imagen_es_la_primera_del_multimedia(self):
        piso = fc._parse_realestate(realestate(multimedia=(
            ("https://static.fotocasa.es/images/ads/primera.jpg", "kitchen"),
            ("https://static.fotocasa.es/images/ads/segunda.jpg", "living room"),
        )))
        assert piso["imagen"] == "https://static.fotocasa.es/images/ads/primera.jpg"

    def test_sin_multimedia_imagen_es_none(self):
        piso = fc._parse_realestate(realestate(multimedia=()))
        assert piso["imagen"] is None


class TestFetchPagina:
    def test_filtra_por_minrooms_en_el_servidor(self, monkeypatch):
        # Verificado a mano (2026-09-03): sin este filtro por servidor, la
        # página 1 sin filtrar de "La Rioja provincia" solo trae 14 pisos de
        # 3+ habitaciones de 97 totales -- con minRooms el conteo ya
        # coincide con el de la propia app de Fotocasa (51).
        capturado = {}

        def fake_get(url, params=None, **kwargs):
            capturado["params"] = params
            capturado["url"] = url
            return FakeResp(200, json_html([]))

        monkeypatch.setattr(fc.requests, "get", fake_get)
        fc._fetch_pagina("logrono", 3, None, 1, UBICACION)
        assert capturado["params"] == {"minRooms": 3, **UBICACION}

    def test_maxrooms_solo_se_incluye_si_se_pasa(self, monkeypatch):
        capturado = {}
        monkeypatch.setattr(fc.requests, "get", lambda url, params=None, **k: capturado.update(params=params) or FakeResp(200, json_html([])))
        fc._fetch_pagina("logrono", 3, 3, 1, UBICACION)
        assert capturado["params"] == {"minRooms": 3, "maxRooms": 3, **UBICACION}

    def test_pagina_1_no_anade_sufijo_a_la_url(self, monkeypatch):
        capturado = {}
        monkeypatch.setattr(fc.requests, "get", lambda url, **k: capturado.update(url=url) or FakeResp(200, json_html([])))
        fc._fetch_pagina("la-rioja-provincia", 3, None, 1, UBICACION)
        assert capturado["url"] == fc.BASE_URL.format(slug="la-rioja-provincia")

    def test_pagina_2_anade_el_numero_como_segmento_de_url(self, monkeypatch):
        # Verificado a mano (2026-09-03, con el patrón exacto de la propia
        # web de Fotocasa aportado por el usuario): sin esto, Fotocasa
        # ignora en silencio el número de página y siempre da la página 1.
        capturado = {}
        monkeypatch.setattr(fc.requests, "get", lambda url, **k: capturado.update(url=url) or FakeResp(200, json_html([])))
        fc._fetch_pagina("la-rioja-provincia", 3, None, 2, UBICACION)
        assert capturado["url"] == fc.BASE_URL.format(slug="la-rioja-provincia") + "/2"

    def test_200_con_json_valido_devuelve_pisos_y_total(self, monkeypatch):
        html = json_html([realestate(), realestate(rawPrice="1200")], count=33)
        monkeypatch.setattr(fc.requests, "get", lambda *a, **k: FakeResp(200, html))
        pisos, total = fc._fetch_pagina("logrono", 3, None, 1, UBICACION)
        assert len(pisos) == 2
        assert total == 33

    def test_404_devuelve_vacio(self, monkeypatch):
        monkeypatch.setattr(fc.requests, "get", lambda *a, **k: FakeResp(404, ""))
        assert fc._fetch_pagina("no-existe", 3, None, 1, UBICACION) == ([], 0)

    def test_otro_status_devuelve_vacio(self, monkeypatch):
        monkeypatch.setattr(fc.requests, "get", lambda *a, **k: FakeResp(500, ""))
        assert fc._fetch_pagina("logrono", 3, None, 1, UBICACION) == ([], 0)

    def test_html_sin_json_embebido_devuelve_vacio(self, monkeypatch):
        monkeypatch.setattr(fc.requests, "get", lambda *a, **k: FakeResp(200, "<html>nada aquí</html>"))
        assert fc._fetch_pagina("logrono", 3, None, 1, UBICACION) == ([], 0)

    def test_json_malformado_devuelve_vacio(self, monkeypatch):
        html = '<script type="application/json" id="__initial_props__">{esto no es json}</script>'
        monkeypatch.setattr(fc.requests, "get", lambda *a, **k: FakeResp(200, html))
        assert fc._fetch_pagina("logrono", 3, None, 1, UBICACION) == ([], 0)

    def test_json_con_forma_inesperada_devuelve_vacio(self, monkeypatch):
        html = '<script type="application/json" id="__initial_props__">{"algo": "distinto"}</script>'
        monkeypatch.setattr(fc.requests, "get", lambda *a, **k: FakeResp(200, html))
        assert fc._fetch_pagina("logrono", 3, None, 1, UBICACION) == ([], 0)

    def test_fallo_de_red_devuelve_vacio(self, monkeypatch):
        def fake_get(*a, **k):
            raise fc.requests.RequestException("timeout")
        monkeypatch.setattr(fc.requests, "get", fake_get)
        assert fc._fetch_pagina("logrono", 3, None, 1, UBICACION) == ([], 0)


class TestFetchTramo:
    def test_una_sola_pagina_si_el_total_cabe(self, monkeypatch):
        llamadas = []

        def fake_get(url, **k):
            llamadas.append(url)
            return FakeResp(200, json_html([realestate() for _ in range(18)], count=18))

        monkeypatch.setattr(fc.requests, "get", fake_get)
        pisos = fc._fetch_tramo("la-rioja-provincia", 4, None, UBICACION)
        assert len(pisos) == 18
        assert len(llamadas) == 1

    def test_pide_paginas_siguientes_hasta_cubrir_el_total(self, monkeypatch):
        # Caso real verificado a mano: "exactamente 3 habitaciones" en La
        # Rioja da count=33 -- página 1 trae 30, hace falta una página 2 con
        # las 3 restantes.
        respuestas = iter([
            (json_html([realestate() for _ in range(30)], count=33), "pagina1"),
            (json_html([realestate() for _ in range(3)], count=33), "pagina2"),
        ])
        llamadas = []

        def fake_get(url, **k):
            llamadas.append(url)
            html, _ = next(respuestas)
            return FakeResp(200, html)

        monkeypatch.setattr(fc.requests, "get", fake_get)
        pisos = fc._fetch_tramo("la-rioja-provincia", 3, 3, UBICACION)
        assert len(pisos) == 33
        assert len(llamadas) == 2
        assert llamadas[1].endswith("/2")

    def test_se_detiene_si_una_pagina_viene_vacia(self, monkeypatch):
        # Red de seguridad: si "count" viene mal y nunca se alcanza, no debe
        # quedarse pidiendo páginas para siempre.
        monkeypatch.setattr(fc.requests, "get", lambda *a, **k: FakeResp(200, json_html([], count=999)))
        pisos = fc._fetch_tramo("la-rioja-provincia", 3, None, UBICACION)
        assert pisos == []

    def test_no_supera_el_tope_de_paginas_por_seguridad(self, monkeypatch):
        llamadas = {"n": 0}

        def fake_get(url, **k):
            llamadas["n"] += 1
            return FakeResp(200, json_html([realestate()], count=999))  # 1 por página, total irreal

        monkeypatch.setattr(fc.requests, "get", fake_get)
        fc._fetch_tramo("la-rioja-provincia", 3, None, UBICACION)
        assert llamadas["n"] == fc.MAX_PAGINAS


class TestCollect:
    def test_pide_dos_tramos_de_habitaciones_por_zona(self, monkeypatch):
        capturas = []

        def fake_get(url, params=None, **kwargs):
            capturas.append(dict(params))
            return FakeResp(200, json_html([]))

        monkeypatch.setattr(fc.requests, "get", fake_get)
        fc.collect(["la-rioja"])
        assert len(capturas) == 2
        assert {"minRooms": fc.MIN_HABITACIONES, "maxRooms": fc.MIN_HABITACIONES, **UBICACION} in capturas
        assert {"minRooms": fc.MIN_HABITACIONES + 1, **UBICACION} in capturas

    def test_itera_varias_zonas_si_hay_mas_de_una_configurada(self, monkeypatch):
        # Ahora mismo SLUGS/LOCATION_CODES solo tienen "la-rioja", pero el
        # bucle de collect() debe seguir funcionando igual si en el futuro
        # se vuelve a buscar por varias zonas a la vez.
        monkeypatch.setitem(fc.SLUGS, "zona-extra", "zona-extra-slug")
        monkeypatch.setitem(fc.LOCATION_CODES, "zona-extra", {"level": "MUNICIPALITY", "locationCode": "999"})
        llamadas = []
        monkeypatch.setattr(fc.requests, "get", lambda url, **k: llamadas.append(url) or FakeResp(200, json_html([])))
        fc.collect(["la-rioja", "zona-extra"])
        assert len(llamadas) == 4  # 2 zonas * 2 tramos de habitaciones

    def test_zona_sin_slug_configurado_se_omite(self, monkeypatch, caplog):
        monkeypatch.setattr(fc.requests, "get", lambda *a, **k: FakeResp(200, json_html([])))
        with caplog.at_level("WARNING"):
            pisos = fc.collect(["zona-inexistente"])
        assert pisos == []
        assert "sin slug/localización configurados" in caplog.text

    def test_acumula_pisos_de_la_zona(self, monkeypatch):
        monkeypatch.setattr(fc.requests, "get", lambda *a, **k: FakeResp(200, json_html([realestate()], count=1)))
        pisos = fc.collect(["la-rioja"])
        # 2 tramos de habitaciones * 1 piso cada uno
        assert len(pisos) == 2

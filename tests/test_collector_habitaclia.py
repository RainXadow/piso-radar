import json

from collectors import habitaclia as hb


class FakeResp:
    def __init__(self, status_code=200, text=""):
        self.status_code = status_code
        self.text = text


def item(id_="62a6d768", titulo="Alquiler de fantástico piso en el Espolón.", precio=950, oculto=False,
         rooms=3, m2=121, tipo="flat", subtipo="flat", municipio="Logroño", distrito="Centro",
         provincia="La Rioja", calle="Calle Miguel Villanueva", legacy="30265000000466",
         imagenes=("https://static.fotocasa.es/images/ads/foto-1?rule=original",),
         descripcion="Bonito piso.\r\nCon terraza."):
    """Un anuncio con la forma real del JSON de Habitaclia (2026-10-25)."""
    return {
        "id": id_,
        "legacyNumericId": legacy,
        "navigationUrl": f"/alquiler/planta-intermedia/{id_}/d",
        "summary": {
            "title": titulo,
            "description": descripcion,
            "location": {"municipality": municipio, "district": distrito, "province": provincia,
                         "displayAddressLine": calle},
            "multimedia": {"images": [{"type": None, "url": u, "position": None} for u in imagenes]},
        },
        "property": {"propertyType": tipo, "propertySubtype": subtipo, "rooms": rooms, "builtSurface": m2},
        "transaction": {"type": "rent", "price": {"amount": precio, "hidden": oculto, "priceDrop": None}},
        "urls": {"canonical": f"/alquiler/planta-intermedia/calle/centro/logrono/{id_}/d"},
    }


def page_html(*items, total_paginas=1):
    data = {"initialSearchResultsPage": {"initialSearchContext": {"results": {
        "items": list(items),
        "pagination": {"page": 1, "pageSize": 29, "totalCount": len(items), "totalPages": total_paginas},
    }}}}
    # Igual que la página real: JSON.parse de un literal de texto JavaScript.
    return f"<script>window.__INITIAL_PROPS__ = JSON.parse({json.dumps(json.dumps(data))});</script>"


class TestParseItem:
    def test_extrae_todos_los_campos(self):
        piso = hb._parse_item(item())
        assert piso["titulo"] == "Alquiler de fantástico piso en el Espolón."
        assert piso["precio"] == 950
        assert piso["m2"] == 121
        assert piso["habitaciones"] == 3
        assert piso["tipo"] == "Piso"
        assert piso["ciudad"] == "Logroño"
        assert piso["zona"] == "Centro"
        assert piso["portal"] == "habitaclia"
        assert piso["url"] == "https://www.habitaclia.com/alquiler/planta-intermedia/calle/centro/logrono/62a6d768/d"
        assert piso["imagen"] == "https://static.fotocasa.es/images/ads/foto-1?rule=original"
        assert piso["descripcion"] == "Bonito piso.\nCon terraza."

    def test_patron_de_la_url_antigua(self):
        assert hb._parse_item(item(legacy="123"))["legacy_url_like"] == "%-i123.htm%"

    def test_sin_legacy_no_hay_patron(self):
        assert "legacy_url_like" not in hb._parse_item(item(legacy=None))

    def test_chalet_es_casa(self):
        assert hb._parse_item(item(tipo="house", subtipo="chalet"))["tipo"] == "Casa"

    def test_apartamento_es_piso(self):
        assert hb._parse_item(item(tipo="flat", subtipo="apartment"))["tipo"] == "Piso"

    def test_sin_tipo_cae_al_titulo(self):
        assert hb._parse_item(item(tipo=None, subtipo=None, titulo="Chalet adosado"))["tipo"] == "Casa"

    def test_sin_titulo_compone_uno(self):
        assert hb._parse_item(item(titulo=""))["titulo"] == "Piso en Calle Miguel Villanueva"

    def test_sin_titulo_ni_calle_usa_el_barrio(self):
        assert hb._parse_item(item(titulo=None, calle=None))["titulo"] == "Piso en Centro"

    def test_colapsa_espacios_del_titulo(self):
        assert hb._parse_item(item(titulo="Piso   en  el centro "))["titulo"] == "Piso en el centro"

    def test_precio_cero_es_a_consultar(self):
        assert hb._parse_item(item(precio=0))["precio"] is None

    def test_precio_oculto_es_a_consultar(self):
        assert hb._parse_item(item(oculto=True))["precio"] is None

    def test_sin_imagenes(self):
        assert hb._parse_item(item(imagenes=()))["imagen"] is None

    def test_pueblo_sin_barrio(self):
        piso = hb._parse_item(item(municipio="Pazuengos", distrito=None))
        assert (piso["ciudad"], piso["zona"]) == ("Pazuengos", None)

    def test_sin_url_devuelve_none(self):
        it = item()
        it["urls"] = {}
        it["navigationUrl"] = None
        assert hb._parse_item(it) is None

    def test_campos_nulos_no_revientan(self):
        it = {"urls": {"canonical": "/a/d"}, "summary": None, "property": None, "transaction": None}
        piso = hb._parse_item(it)
        assert piso["precio"] is None and piso["habitaciones"] is None


class TestFetchPagina:
    def _capturar(self, monkeypatch, resp):
        llamadas = []

        def fake_get(url, **kw):
            llamadas.append((url, kw))
            return resp
        monkeypatch.setattr(hb.requests, "get", fake_get)
        return llamadas

    def test_url_y_filtro_de_habitaciones(self, monkeypatch):
        llamadas = self._capturar(monkeypatch, FakeResp(text=page_html(item())))
        hb._fetch_pagina("la-rioja-provincia", 1)
        url, kw = llamadas[0]
        assert url == "https://www.habitaclia.com/alquiler/viviendas/la-rioja-provincia/s"
        assert kw["params"] == {"minRooms": hb.MIN_HABITACIONES}
        assert kw["proxies"] == hb.PROXIES

    def test_pagina_2_va_en_la_ruta(self, monkeypatch):
        llamadas = self._capturar(monkeypatch, FakeResp(text=page_html(item())))
        hb._fetch_pagina("la-rioja-provincia", 2)
        assert llamadas[0][0] == "https://www.habitaclia.com/alquiler/viviendas/la-rioja-provincia/s/2"

    def test_devuelve_pisos_y_total_de_paginas(self, monkeypatch):
        self._capturar(monkeypatch, FakeResp(text=page_html(item(id_="a"), item(id_="b"), total_paginas=3)))
        pisos, total = hb._fetch_pagina("la-rioja-provincia", 1)
        assert len(pisos) == 2
        assert total == 3

    def test_comillas_y_acentos_en_el_texto(self, monkeypatch):
        self._capturar(monkeypatch, FakeResp(text=page_html(item(titulo='Ático "único" en Logroño'))))
        pisos, _ = hb._fetch_pagina("la-rioja-provincia", 1)
        assert pisos[0]["titulo"] == 'Ático "único" en Logroño'

    def test_sin_json_devuelve_vacio(self, monkeypatch, caplog):
        self._capturar(monkeypatch, FakeResp(text="<html>otra cosa</html>"))
        assert hb._fetch_pagina("la-rioja-provincia", 1) == ([], 0)
        assert "no se encontró el JSON" in caplog.text

    def test_json_con_otra_forma_devuelve_vacio(self, monkeypatch):
        texto = f"<script>window.__INITIAL_PROPS__ = JSON.parse({json.dumps(json.dumps({'otra': 1}))});</script>"
        self._capturar(monkeypatch, FakeResp(text=texto))
        assert hb._fetch_pagina("la-rioja-provincia", 1) == ([], 0)

    def test_404_devuelve_vacio(self, monkeypatch):
        self._capturar(monkeypatch, FakeResp(status_code=404))
        assert hb._fetch_pagina("x", 1) == ([], 0)

    def test_otro_error_http_devuelve_vacio(self, monkeypatch):
        self._capturar(monkeypatch, FakeResp(status_code=503))
        assert hb._fetch_pagina("x", 1) == ([], 0)

    def test_fallo_de_red_devuelve_vacio(self, monkeypatch):
        def boom(*a, **k):
            raise hb.requests.ConnectionError("sin red")
        monkeypatch.setattr(hb.requests, "get", boom)
        assert hb._fetch_pagina("x", 1) == ([], 0)


class TestFetchZona:
    def test_pagina_hasta_el_total_de_paginas(self, monkeypatch):
        pedidas = []

        def fake(slug, pagina):
            pedidas.append(pagina)
            return [hb._parse_item(item(id_=f"p{pagina}"))], 2
        monkeypatch.setattr(hb, "_fetch_pagina", fake)
        monkeypatch.setattr(hb.time, "sleep", lambda s: None)
        assert len(hb._fetch_zona("x")) == 2
        assert pedidas == [1, 2]

    def test_se_detiene_si_una_pagina_viene_vacia(self, monkeypatch):
        respuestas = iter([([hb._parse_item(item())], 3), ([], 0)])
        monkeypatch.setattr(hb, "_fetch_pagina", lambda slug, pagina: next(respuestas))
        monkeypatch.setattr(hb.time, "sleep", lambda s: None)
        assert len(hb._fetch_zona("x")) == 1

    def test_no_supera_el_tope_de_paginas(self, monkeypatch):
        pedidas = []

        def fake(slug, pagina):
            pedidas.append(pagina)
            return [hb._parse_item(item(id_=f"p{pagina}"))], 99
        monkeypatch.setattr(hb, "_fetch_pagina", fake)
        monkeypatch.setattr(hb.time, "sleep", lambda s: None)
        hb._fetch_zona("x")
        assert len(pedidas) == hb.MAX_PAGINAS


class TestCollect:
    def test_una_busqueda_por_provincia(self, monkeypatch):
        slugs = []
        monkeypatch.setattr(hb, "_fetch_zona", lambda slug: slugs.append(slug) or [hb._parse_item(item())])
        pisos = hb.collect(["la-rioja"])
        assert slugs == ["la-rioja-provincia"]
        assert len(pisos) == 1
        assert "provincia" not in pisos[0]

    def test_descarta_anuncios_de_otra_provincia(self, monkeypatch):
        monkeypatch.setattr(hb, "_fetch_zona", lambda slug: [
            hb._parse_item(item(id_="a", provincia="La Rioja")),
            hb._parse_item(item(id_="b", provincia="Palencia")),
        ])
        pisos = hb.collect(["la-rioja"])
        assert [p["url"].split("/")[-2] for p in pisos] == ["a"]

    def test_anuncio_sin_provincia_se_acepta(self, monkeypatch):
        monkeypatch.setattr(hb, "_fetch_zona", lambda slug: [hb._parse_item(item(provincia=None))])
        assert len(hb.collect(["la-rioja"])) == 1

    def test_zona_sin_slug_se_omite(self, monkeypatch, caplog):
        monkeypatch.setattr(hb, "_fetch_zona", lambda slug: [hb._parse_item(item())])
        assert hb.collect(["madrid"]) == []
        assert "sin slug" in caplog.text

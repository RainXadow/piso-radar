"""
Colector de MyHome con la forma real de su página (JSON de Angular en
<script id="ng-state">, verificado el 2026-10-25) y los casos raros vistos en
los anuncios de verdad: precio semanal, horquilla de precios, sin precio,
Eircode pegado, "County Dublin", estudios con 0 habitaciones, "let agreed".
"""
import json

import pytest

from collectors import myhome as mh


class FakeResp:
    def __init__(self, status_code=200, text=""):
        self.status_code = status_code
        self.text = text


def item(pid=5028637, precio="€2,800 / month", direccion="10 Meathville, Portobello,   Dublin 8, D08 R6T2",
         tipo="Terraced House", camas=2, banos=1, m2=90, ber="C1", particular=True, activo=True,
         alquilado=False, estado="ToLet", foto="https://photos-a.propertyimages.ie/media/x_m.jpg"):
    return {
        "PropertyId": pid, "IsActive": activo, "IsSaleAgreed": alquilado, "PropertyStatus": estado,
        "IsPrivateLandlord": particular, "PriceAsString": precio, "DisplayAddress": direccion,
        "Address": direccion.split(",")[0], "PropertyType": tipo, "NumberOfBeds": camas,
        "NumberOfBathrooms": banos, "SizeStringMeters": m2, "BerRating": ber,
        "BrochureMap": {"longitude": -6.2717, "latitude": 53.3361},
        "BrochureUrl": f"/rentals/brochure/anuncio/{pid}", "MainPhotoWeb": foto,
        "CreatedOnDate": "2026-09-16T19:37:13.6130000+01:00",
    }


def pagina(*items, total=None, por_pagina=20):
    estado = {
        "SEARCH_RESOLVER:/rentals/ireland/property-to-rent": {
            "SearchResults": list(items), "ResultCount": len(items) if total is None else total,
            "PageSize": por_pagina, "Page": 1,
        },
        "otra-cosa": {"irrelevante": True},
    }
    return f'<html><script id="ng-state" type="application/json">{json.dumps(estado)}</script></html>'


class TestPrecio:
    @pytest.mark.parametrize("texto,esperado", [
        ("€2,800 / month", 2800),
        ("€450 / week", 1950),           # 450 * 52 / 12
        ("€2,200 to €3,500 / month", 2200),
        ("€1,234.50 / month", 1234),
        ("Price on application", None),
        (None, None),
        ("€0 / month", None),
    ])
    def test_a_euros_al_mes(self, texto, esperado):
        assert mh._precio_mensual(texto) == esperado


class TestUbicacion:
    @pytest.mark.parametrize("direccion,esperado", [
        ("10 Meathville, Portobello,   Dublin 8, D08 R6T2", ("Portobello", "Dublin 8", "D08 R6T2")),
        ("16C Cork Street, Kinsale, Cork, P17 D429", ("Kinsale", "Cork", "P17 D429")),
        ("22 Larkhill Road, Whitehall,   Dublin 9, D09FX60", ("Whitehall", "Dublin 9", "D09FX60")),
        ("Linden Court, Grove Ave, Stillorgan, County Dublin", ("Stillorgan", "Dublin", None)),
        ("3 Le Vere Terrace, Harold's Cross,   Dublin 6W", ("Harold's Cross", "Dublin 6W", None)),
        ("Apartment 4, Main St, Co. Galway, H91 X2Y3", ("Main St", "Galway", "H91 X2Y3")),
        ("Galway", (None, "Galway", None)),
        ("", (None, None, None)),
    ])
    def test_zona_condado_y_eircode(self, direccion, esperado):
        assert mh._ubicacion(direccion) == esperado

    def test_un_trozo_de_direccion_no_se_confunde_con_eircode(self):
        assert mh._ubicacion("Unit 5, The Docks, Cork")[2] is None


class TestParseItem:
    def test_anuncio_completo(self):
        piso = mh._parse_item(item())
        assert piso == {
            "titulo": "10 Meathville, Portobello, Dublin 8",
            "precio": 2800, "m2": 90, "habitaciones": 2, "tipo": "Casa",
            "zona": "Portobello", "ciudad": "Dublin 8",
            "url": "https://www.myhome.ie/rentals/brochure/anuncio/5028637",
            "imagen": "https://photos-a.propertyimages.ie/media/x_m.jpg",
            "descripcion": "", "fecha_publicacion": "2026-09-16T19:37:13.6130000+01:00", "portal": "myhome",
            "extra": {"tipo_portal": "Terraced House", "ber": "C1", "banos": 1, "particular": True,
                      "precio_texto": "€2,800 / month", "eircode": "D08 R6T2", "lat": 53.3361, "lon": -6.2717},
        }

    @pytest.mark.parametrize("tipo,esperado", [("Apartment", "Piso"), ("Studio", "Piso"), ("Penthouse", "Piso"),
                                               ("Semi-Detached", "Casa"), ("Country House", "Casa"), ("", None)])
    def test_tipo(self, tipo, esperado):
        assert mh._parse_item(item(tipo=tipo))["tipo"] == esperado

    @pytest.mark.parametrize("cambios", [
        {"tipo": "", "camas": 0, "precio": "POA", "direccion": "Unit 11, N11 Business Park, Enniscorthy"},
        {"tipo": "", "camas": 0, "precio": "POA", "direccion": "Alpine Service Station, Hobartstown, Moone, Kildare"},
        {"tipo": "", "camas": 1, "precio": "POA", "direccion": "Test Property 12, Leighlinbridge, Carlow"},
        {"tipo": "", "camas": 0, "precio": "POA", "direccion": "Test, Shercock, Cavan"},
    ])
    def test_locales_naves_y_anuncios_de_prueba_se_ignoran(self, cambios):
        assert mh._parse_item(item(**cambios)) is None

    def test_sin_tipo_pero_con_habitaciones_es_una_vivienda(self):
        piso = mh._parse_item(item(tipo="", camas=3, precio="€2,500 / month",
                                   direccion="9 Maple Lawns, Oldtown Demesne, Naas, County Kildare"))
        assert (piso["precio"], piso["habitaciones"], piso["ciudad"], piso["tipo"]) == (2500, 3, "Kildare", None)

    def test_una_calle_que_empieza_por_test_no_se_confunde(self):
        assert mh._parse_item(item(direccion="12 Testament Road, Cork")) is not None

    @pytest.mark.parametrize("cambios", [{"activo": False}, {"alquilado": True}, {"estado": "LetAgreed"}])
    def test_ya_alquilados_se_ignoran(self, cambios):
        assert mh._parse_item(item(**cambios)) is None

    def test_estudio_con_cero_habitaciones_se_conserva(self):
        assert mh._parse_item(item(tipo="Studio", camas=0))["habitaciones"] == 0

    def test_sin_datos_opcionales(self):
        piso = mh._parse_item(item(ber=None, m2=0, banos=None, particular=False, foto=None))
        assert piso["m2"] is None and piso["imagen"] is None
        assert "ber" not in piso["extra"] and "banos" not in piso["extra"]
        assert piso["extra"]["particular"] is False

    def test_sin_url_se_ignora(self):
        it = item()
        del it["BrochureUrl"]
        assert mh._parse_item(it) is None


class TestFetchPagina:
    def _capturar(self, monkeypatch, resp):
        llamadas = []
        monkeypatch.setattr(mh.requests, "get", lambda url, **kw: llamadas.append((url, kw)) or resp)
        return llamadas

    def test_primera_pagina_url_limpia_por_el_proxy(self, monkeypatch):
        llamadas = self._capturar(monkeypatch, FakeResp(text=pagina(item())))
        mh._fetch_pagina("ireland", 1)
        url, kw = llamadas[0]
        assert url == "https://www.myhome.ie/rentals/ireland/property-to-rent"
        assert "params" not in kw  # robots.txt bloquea cualquier query string salvo ?page=N
        assert kw["proxies"] == mh.PROXIES

    def test_paginas_siguientes_solo_con_page(self, monkeypatch):
        llamadas = self._capturar(monkeypatch, FakeResp(text=pagina(item())))
        mh._fetch_pagina("ireland", 3)
        assert llamadas[0][0] == "https://www.myhome.ie/rentals/ireland/property-to-rent?page=3"

    def test_total_de_paginas_desde_el_recuento(self, monkeypatch):
        self._capturar(monkeypatch, FakeResp(text=pagina(item(pid=1), item(pid=2), total=661)))
        pisos, total = mh._fetch_pagina("ireland", 1)
        assert len(pisos) == 2 and total == 34

    def test_html_sin_json(self, monkeypatch, caplog):
        self._capturar(monkeypatch, FakeResp(text="<html>Security Check</html>"))
        assert mh._fetch_pagina("ireland", 1) == ([], 0)
        assert "no se encontró el JSON" in caplog.text

    def test_json_sin_resultados_de_busqueda(self, monkeypatch, caplog):
        texto = '<script id="ng-state" type="application/json">{"otra": {}}</script>'
        self._capturar(monkeypatch, FakeResp(text=texto))
        assert mh._fetch_pagina("ireland", 1) == ([], 0)
        assert "forma inesperada" in caplog.text

    def test_http_403(self, monkeypatch):
        self._capturar(monkeypatch, FakeResp(status_code=403))
        assert mh._fetch_pagina("ireland", 1) == ([], 0)

    def test_sin_red(self):
        # La fixture sin_red de conftest corta requests.get.
        assert mh._fetch_pagina("ireland", 1) == ([], 0)


class TestRecorrido:
    def test_recorre_todas_las_paginas_y_para(self, monkeypatch):
        pedidas = []

        def fake(slug, n):
            pedidas.append(n)
            return [mh._parse_item(item(pid=n))], 3
        monkeypatch.setattr(mh, "_fetch_pagina", fake)
        monkeypatch.setattr(mh.time, "sleep", lambda s: None)
        assert len(mh.collect(["ireland"])) == 3
        assert pedidas == [1, 2, 3]

    def test_pagina_de_solo_alquilados_no_corta_el_recorrido(self, monkeypatch):
        respuestas = {1: ([], 3), 2: ([mh._parse_item(item(pid=2))], 3), 3: ([mh._parse_item(item(pid=3))], 3)}
        monkeypatch.setattr(mh, "_fetch_pagina", lambda slug, n: respuestas[n])
        monkeypatch.setattr(mh.time, "sleep", lambda s: None)
        assert len(mh.collect(["ireland"])) == 2

    def test_espera_entre_paginas(self, monkeypatch):
        esperas = []
        monkeypatch.setattr(mh, "_fetch_pagina", lambda slug, n: ([mh._parse_item(item(pid=n))], 3))
        monkeypatch.setattr(mh.time, "sleep", esperas.append)
        mh.collect(["ireland"])
        assert len(esperas) == 2 and all(e >= mh.DELAY_ENTRE_PETICIONES * 0.7 for e in esperas)

    def test_tope_de_paginas(self, monkeypatch):
        pedidas = []
        monkeypatch.setattr(mh, "_fetch_pagina", lambda slug, n: pedidas.append(n) or ([mh._parse_item(item(pid=n))], 999))
        monkeypatch.setattr(mh.time, "sleep", lambda s: None)
        mh.collect(["ireland"])
        assert len(pedidas) == mh.MAX_PAGINAS

    def test_zona_desconocida(self, caplog):
        assert mh.collect(["madrid"]) == []
        assert "sin slug" in caplog.text

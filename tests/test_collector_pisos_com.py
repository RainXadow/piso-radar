from bs4 import BeautifulSoup

from collectors import pisos_com as pc


class FakeResp:
    def __init__(self, status_code=200, text=""):
        self.status_code = status_code
        self.text = text


def card_html(clases="ad-preview", titulo="Piso en Centro", href="/alquilar/piso-1/",
              precio="750 €/mes", chars=("2 habs.", "1 baño", "70 m²"), subtitulo="Centro (Logroño)",
              descripcion="Bonito piso.", imagen_url="https://fotos.imghs.net/foto1.jpg", imagen_lazy=False):
    chars_html = "".join(f'<p class="ad-preview__char">{c}</p>' for c in chars)
    # El carrusel real repite la primera foto en un slide oculto ("u-hide")
    # antes del slide visible. Además, solo la primera tarjeta de la página
    # real trae "src" directo -- el resto usa "data-src" (carga diferida) --
    # imagen_lazy replica ese segundo caso.
    attr = "data-src" if imagen_lazy else "src"
    carousel_html = f'''
      <div class="carousel__slide carousel__hidden u-hide">
        <div class="carousel__main-photo"><picture><img {attr}="{imagen_url}"></picture></div>
      </div>
      <div class="carousel__slide">
        <div class="carousel__main-photo"><picture><img {attr}="{imagen_url}"></picture></div>
      </div>
    ''' if imagen_url else ""
    return f'''
    <div class="{clases}">
      <div class="carousel__container">{carousel_html}</div>
      <a class="ad-preview__title" href="{href}">{titulo}</a>
      <span class="ad-preview__price">{precio}</span>
      {chars_html}
      <p class="ad-preview__subtitle">{subtitulo}</p>
      <p class="ad-preview__description">{descripcion}</p>
    </div>
    '''


def page_html(*cards, total=None):
    titulo_html = f'<div class="grid__title"><span>{total} resultados</span></div>' if total is not None else ""
    return f"<html><body>{titulo_html}{''.join(cards)}</body></html>"


class TestNum:
    def test_precio_con_simbolo_y_texto(self):
        assert pc._num("750 €/mes") == 750

    def test_con_separador_de_miles(self):
        assert pc._num("1.500 €/mes") == 1500

    def test_metros_cuadrados(self):
        assert pc._num("89 m²") == 89

    def test_habitaciones(self):
        assert pc._num("2 habs.") == 2

    def test_sin_digitos_devuelve_none(self):
        assert pc._num("sin datos") is None

    def test_texto_vacio_o_none(self):
        assert pc._num("") is None
        assert pc._num(None) is None


class TestZonaYCiudad:
    def test_barrio_con_ciudad_entre_parentesis(self):
        assert pc._zona_y_ciudad("Centro (Logroño)", "la-rioja") == ("Centro", "Logroño")

    def test_pueblo_sin_barrio(self):
        assert pc._zona_y_ciudad("Sojuela", "la-rioja") == (None, "Sojuela")

    def test_sin_subtitulo_cae_a_zona_de_busqueda(self):
        assert pc._zona_y_ciudad(None, "la-rioja") == (None, "la-rioja")

    def test_subtitulo_vacio_cae_a_zona_de_busqueda(self):
        assert pc._zona_y_ciudad("", "la-rioja") == (None, "la-rioja")


class TestPrimeraImagen:
    def _card(self, html):
        soup = BeautifulSoup(html, "html.parser")
        return soup.select_one("div.ad-preview")

    def test_salta_el_slide_oculto_duplicado(self):
        card = self._card(card_html(imagen_url="https://fotos.imghs.net/real.jpg"))
        assert pc._primera_imagen(card) == "https://fotos.imghs.net/real.jpg"

    def test_sin_carrusel_devuelve_none(self):
        card = self._card(card_html(imagen_url=None))
        assert pc._primera_imagen(card) is None

    def test_usa_data_src_si_no_hay_src_directo(self):
        # Caso real: solo la primera tarjeta de la página trae "src" (carga
        # eager); el resto usa carga diferida con "data-src" -- confirmado
        # con datos reales de pisos.com/alquiler/pisos-la_rioja/.
        card = self._card(card_html(imagen_url="https://fotos.imghs.net/diferida.jpg", imagen_lazy=True))
        assert pc._primera_imagen(card) == "https://fotos.imghs.net/diferida.jpg"


class TestParseCard:
    def _card(self, html):
        soup = BeautifulSoup(html, "html.parser")
        return soup.select_one("div.ad-preview")

    def test_extrae_todos_los_campos(self):
        card = self._card(card_html(titulo="Piso en Cascajos", precio="850 €/mes",
                                     chars=["3 habs.", "2 baños", "6ª planta"],
                                     subtitulo="Cascajos-Piqueras (Logroño)"))
        piso = pc._parse_card(card, "la-rioja")
        assert piso["titulo"] == "Piso en Cascajos"
        assert piso["precio"] == 850
        assert piso["habitaciones"] == 3
        assert piso["m2"] is None  # no venía en los chars, como pasa de verdad a veces
        assert piso["zona"] == "Cascajos-Piqueras"
        assert piso["ciudad"] == "Logroño"
        assert piso["portal"] == "pisos.com"
        assert piso["imagen"] == "https://fotos.imghs.net/foto1.jpg"

    def test_url_relativa_se_completa(self):
        card = self._card(card_html(href="/alquilar/piso-x/"))
        piso = pc._parse_card(card, "logrono")
        assert piso["url"] == "https://www.pisos.com/alquilar/piso-x/"

    def test_url_absoluta_se_deja_igual(self):
        card = self._card(card_html(href="https://www.pisos.com/alquilar/piso-x/"))
        piso = pc._parse_card(card, "logrono")
        assert piso["url"] == "https://www.pisos.com/alquilar/piso-x/"

    def test_tipo_piso_por_titulo(self):
        card = self._card(card_html(titulo="Piso en Centro"))
        assert pc._parse_card(card, "logrono")["tipo"] == "Piso"

    def test_tipo_casa_por_titulo(self):
        card = self._card(card_html(titulo="Chalet adosado en calle de la Alegría"))
        assert pc._parse_card(card, "logrono")["tipo"] == "Casa"

    def test_sin_subtitulo_usa_zona_de_busqueda_como_ultimo_recurso(self):
        html = '''<div class="ad-preview">
          <a class="ad-preview__title" href="/x/">Piso en X</a>
          <span class="ad-preview__price">700 €/mes</span>
        </div>'''
        card = self._card(html)
        piso = pc._parse_card(card, "la-rioja")
        assert piso["zona"] is None
        assert piso["ciudad"] == "la-rioja"

    def test_pueblo_sin_barrio_todo_es_ciudad(self):
        card = self._card(card_html(subtitulo="Sojuela"))
        piso = pc._parse_card(card, "la-rioja")
        assert piso["zona"] is None
        assert piso["ciudad"] == "Sojuela"

    def test_sin_titulo_devuelve_none(self):
        html = '<div class="ad-preview"><span class="ad-preview__price">700 €/mes</span></div>'
        card = self._card(html)
        assert pc._parse_card(card, "logrono") is None


class TestTotalResultados:
    def test_extrae_el_numero(self):
        soup = BeautifulSoup(page_html(total=25), "html.parser")
        assert pc._total_resultados(soup) == 25

    def test_none_si_no_hay_div_de_titulo(self):
        soup = BeautifulSoup("<html></html>", "html.parser")
        assert pc._total_resultados(soup) is None


class TestFetchPagina:
    def test_filtra_por_habitaciones_en_la_url(self, monkeypatch):
        # Verificado a mano (2026-09-03, aportado por el usuario): "con-N-habitaciones"
        # en la URL de pisos.com es "N o más", no exactas.
        capturado = {}
        monkeypatch.setattr(pc.requests, "get", lambda url, **k: capturado.update(url=url) or FakeResp(200, page_html()))
        pc._fetch_pagina("la_rioja", "la-rioja", pc.BASE_URL, 1)
        assert capturado["url"] == f"https://www.pisos.com/alquiler/pisos-la_rioja/con-{pc.MIN_HABITACIONES}-habitaciones/"

    def test_pagina_2_anade_el_numero_al_final(self, monkeypatch):
        capturado = {}
        monkeypatch.setattr(pc.requests, "get", lambda url, **k: capturado.update(url=url) or FakeResp(200, page_html()))
        pc._fetch_pagina("la_rioja", "la-rioja", pc.BASE_URL, 2)
        assert capturado["url"].endswith("/2/")

    def test_devuelve_pisos_y_total(self, monkeypatch):
        html = page_html(card_html(titulo="Piso A"), card_html(titulo="Piso B", href="/b/"), total=25)
        monkeypatch.setattr(pc.requests, "get", lambda *a, **k: FakeResp(200, html))
        pisos, total = pc._fetch_pagina("la_rioja", "la-rioja", pc.BASE_URL, 1)
        assert len(pisos) == 2
        assert total == 25

    def test_sin_total_en_la_pagina_usa_el_numero_de_tarjetas(self, monkeypatch):
        html = page_html(card_html(titulo="Piso A"))
        monkeypatch.setattr(pc.requests, "get", lambda *a, **k: FakeResp(200, html))
        pisos, total = pc._fetch_pagina("la_rioja", "la-rioja", pc.BASE_URL, 1)
        assert total == 1

    def test_excluye_tarjetas_u_hide(self, monkeypatch):
        html = page_html(
            card_html(titulo="Real"),
            card_html(clases="ad-preview u-hide", titulo="Duplicado oculto"),
        )
        monkeypatch.setattr(pc.requests, "get", lambda *a, **k: FakeResp(200, html))
        pisos, _ = pc._fetch_pagina("logrono", "logrono", pc.BASE_URL, 1)
        assert [p["titulo"] for p in pisos] == ["Real"]

    def test_excluye_tarjetas_js_similar_ad(self, monkeypatch):
        # Caso real: buscando Villamediana de Iregua salió un piso en Palencia
        # marcado con esta clase -- no debe colarse.
        html = page_html(
            card_html(titulo="Piso en Villamediana"),
            card_html(clases="ad-preview js-similarAd", titulo="Piso en Palencia Capital"),
        )
        monkeypatch.setattr(pc.requests, "get", lambda *a, **k: FakeResp(200, html))
        pisos, _ = pc._fetch_pagina("villamediana", "villamediana-de-iregua", pc.BASE_URL, 1)
        assert [p["titulo"] for p in pisos] == ["Piso en Villamediana"]

    def test_status_no_200_devuelve_vacio(self, monkeypatch):
        monkeypatch.setattr(pc.requests, "get", lambda *a, **k: FakeResp(404, ""))
        assert pc._fetch_pagina("logrono", "logrono", pc.BASE_URL, 1) == ([], 0)

    def test_fallo_de_red_devuelve_vacio(self, monkeypatch):
        def fake_get(*a, **k):
            raise pc.requests.RequestException("boom")
        monkeypatch.setattr(pc.requests, "get", fake_get)
        assert pc._fetch_pagina("logrono", "logrono", pc.BASE_URL, 1) == ([], 0)

    def test_pagina_sin_tarjetas_devuelve_vacio(self, monkeypatch):
        monkeypatch.setattr(pc.requests, "get", lambda *a, **k: FakeResp(200, "<html></html>"))
        assert pc._fetch_pagina("logrono", "logrono", pc.BASE_URL, 1) == ([], 0)


class TestFetchTramo:
    def test_una_sola_pagina_si_el_total_cabe(self, monkeypatch):
        llamadas = []

        def fake_get(url, **k):
            llamadas.append(url)
            return FakeResp(200, page_html(*[card_html(href=f"/{i}/") for i in range(25)], total=25))

        monkeypatch.setattr(pc.requests, "get", fake_get)
        monkeypatch.setattr(pc.time, "sleep", lambda *a, **k: None)
        pisos = pc._fetch_tramo("la_rioja", "la-rioja", pc.BASE_URL)
        assert len(pisos) == 25
        assert len(llamadas) == 1

    def test_pide_paginas_siguientes_hasta_cubrir_el_total(self, monkeypatch):
        respuestas = iter([
            page_html(*[card_html(href=f"/a{i}/") for i in range(30)], total=50),
            page_html(*[card_html(href=f"/b{i}/") for i in range(20)], total=50),
        ])
        llamadas = []

        def fake_get(url, **k):
            llamadas.append(url)
            return FakeResp(200, next(respuestas))

        monkeypatch.setattr(pc.requests, "get", fake_get)
        monkeypatch.setattr(pc.time, "sleep", lambda *a, **k: None)
        pisos = pc._fetch_tramo("la_rioja", "la-rioja", pc.BASE_URL)
        assert len(pisos) == 50
        assert len(llamadas) == 2
        assert llamadas[1].endswith("/2/")

    def test_se_detiene_si_una_pagina_viene_vacia(self, monkeypatch):
        monkeypatch.setattr(pc.requests, "get", lambda *a, **k: FakeResp(200, page_html(total=999)))
        monkeypatch.setattr(pc.time, "sleep", lambda *a, **k: None)
        assert pc._fetch_tramo("la_rioja", "la-rioja", pc.BASE_URL) == []

    def test_no_supera_el_tope_de_paginas(self, monkeypatch):
        llamadas = {"n": 0}

        def fake_get(url, **k):
            llamadas["n"] += 1
            return FakeResp(200, page_html(card_html(href=f"/{llamadas['n']}/"), total=999))

        monkeypatch.setattr(pc.requests, "get", fake_get)
        monkeypatch.setattr(pc.time, "sleep", lambda *a, **k: None)
        pc._fetch_tramo("la_rioja", "la-rioja", pc.BASE_URL)
        assert llamadas["n"] == pc.MAX_PAGINAS


class TestCollect:
    def test_pide_pisos_y_casas_de_la_zona(self, monkeypatch):
        llamadas = []

        def fake_get(url, **kwargs):
            llamadas.append(url)
            return FakeResp(200, page_html())

        monkeypatch.setattr(pc.requests, "get", fake_get)
        monkeypatch.setattr(pc.time, "sleep", lambda *a, **k: None)
        pc.collect(["la-rioja"])
        assert len(llamadas) == 2
        assert any("pisos-la_rioja" in u for u in llamadas)
        assert any("casas-la_rioja" in u for u in llamadas)

    def test_zona_sin_slug_se_omite(self, monkeypatch, caplog):
        monkeypatch.setattr(pc.requests, "get", lambda *a, **k: FakeResp(200, page_html()))
        monkeypatch.setattr(pc.time, "sleep", lambda *a, **k: None)
        with caplog.at_level("WARNING"):
            pisos = pc.collect(["no-existe"])
        assert pisos == []
        assert "sin slug configurado" in caplog.text

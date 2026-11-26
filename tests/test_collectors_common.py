from collectors import jitter, get_headers, clasificar_tipo, USER_AGENT


class TestJitter:
    def test_dentro_del_rango_esperado(self):
        for _ in range(200):
            valor = jitter(10)
            assert 7 <= valor <= 13

    def test_factor_personalizado(self):
        for _ in range(200):
            valor = jitter(10, factor=0.1)
            assert 9 <= valor <= 11

    def test_no_devuelve_siempre_el_mismo_valor(self):
        valores = {jitter(10) for _ in range(20)}
        assert len(valores) > 1


class TestGetHeaders:
    def test_user_agent_fijo(self):
        assert get_headers()["User-Agent"] == USER_AGENT

    def test_incluye_accept_language_espanol(self):
        headers = get_headers()
        assert "es" in headers["Accept-Language"]


class TestClasificarTipo:
    def test_piso_normal(self):
        assert clasificar_tipo("Piso en Centro") == "Piso"

    def test_apartamento_es_piso(self):
        assert clasificar_tipo("Apartamento en Valdegastea") == "Piso"

    def test_chalet_es_casa(self):
        assert clasificar_tipo("Chalet adosado en calle de la Alegría, 2") == "Casa"

    def test_casa_es_casa(self):
        assert clasificar_tipo("Casa en Nájera") == "Casa"

    def test_unifamiliar_es_casa(self):
        assert clasificar_tipo("Vivienda unifamiliar con jardín") == "Casa"

    def test_es_insensible_a_mayusculas(self):
        assert clasificar_tipo("CHALET EN LARDERO") == "Casa"

    def test_titulo_vacio_es_piso_por_defecto(self):
        assert clasificar_tipo("") == "Piso"

    def test_titulo_none_no_revienta(self):
        assert clasificar_tipo(None) == "Piso"

    def test_duplex_es_piso(self):
        assert clasificar_tipo("Dúplex en calle de Bretón de los Herreros") == "Piso"

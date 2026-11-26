import db
import notifier


class FakeResp:
    def __init__(self, status_code=200, text=""):
        self.status_code = status_code
        self.text = text


class TestLineaNuevo:
    def test_formato_con_precio(self):
        piso = {"precio": 800, "titulo": "Piso en Centro", "ciudad": "Logroño", "zona": None, "portal": "fotocasa"}
        assert notifier._linea_nuevo(piso) == "• 800€ — Piso en Centro (Logroño)"

    def test_sin_precio_usa_interrogante(self):
        piso = {"precio": None, "titulo": "Piso sin precio", "ciudad": "Logroño", "zona": None, "portal": "fotocasa"}
        assert "?€" in notifier._linea_nuevo(piso)

    def test_sin_ciudad_cae_a_zona(self):
        piso = {"precio": 800, "titulo": "X", "ciudad": None, "zona": "Gran Via", "portal": "fotocasa"}
        assert "(Gran Via)" in notifier._linea_nuevo(piso)

    def test_sin_ciudad_ni_zona_cae_a_portal(self):
        piso = {"precio": 800, "titulo": "X", "ciudad": None, "zona": None, "portal": "habitaclia"}
        assert "(habitaclia)" in notifier._linea_nuevo(piso)


class TestLineaCambio:
    def test_formato_cambio_de_precio(self):
        cambio = {"titulo": "Piso en Centro", "url": "https://x.com/1", "cambios": {"precio": (800, 600)}}
        linea = notifier._linea_cambio(cambio)
        assert "800€ → 600€" in linea
        assert "Piso en Centro" in linea
        assert "https://x.com/1" in linea

    def test_varios_campos_cambiados_a_la_vez(self):
        cambio = {"titulo": "X", "url": "https://x.com", "cambios": {"m2": (70, 90), "habitaciones": (2, 3)}}
        linea = notifier._linea_cambio(cambio)
        assert "70 m² → 90 m²" in linea
        assert "2 hab. → 3 hab." in linea


class TestNotify:
    def test_sin_topic_configurado_no_intenta_enviar(self, monkeypatch):
        monkeypatch.setattr(notifier, "NTFY_TOPIC", "")
        llamado = {"veces": 0}
        monkeypatch.setattr(notifier.requests, "post", lambda *a, **k: llamado.update(veces=llamado["veces"] + 1))
        assert notifier._notify("t", "m") is False
        assert llamado["veces"] == 0

    def test_status_200_es_exito(self, monkeypatch):
        monkeypatch.setattr(notifier, "NTFY_TOPIC", "topic-test")
        monkeypatch.setattr(notifier.requests, "post", lambda *a, **k: FakeResp(200))
        assert notifier._notify("t", "m") is True

    def test_status_no_200_es_fallo(self, monkeypatch):
        monkeypatch.setattr(notifier, "NTFY_TOPIC", "topic-test")
        monkeypatch.setattr(notifier.requests, "post", lambda *a, **k: FakeResp(429, "cuota agotada"))
        assert notifier._notify("t", "m") is False

    def test_excepcion_de_red_es_fallo(self, monkeypatch):
        monkeypatch.setattr(notifier, "NTFY_TOPIC", "topic-test")

        def fake_post(*a, **k):
            raise notifier.requests.RequestException("timeout")

        monkeypatch.setattr(notifier.requests, "post", fake_post)
        assert notifier._notify("t", "m") is False

    def test_click_no_se_manda_si_no_se_pasa(self, monkeypatch):
        monkeypatch.setattr(notifier, "NTFY_TOPIC", "topic-test")
        capturado = {}
        monkeypatch.setattr(notifier.requests, "post", lambda url, data, headers, timeout, proxies=None: capturado.update(headers=headers) or FakeResp(200))
        notifier._notify("t", "m")
        assert "Click" not in capturado["headers"]

    def test_click_se_incluye_como_header(self, monkeypatch):
        monkeypatch.setattr(notifier, "NTFY_TOPIC", "topic-test")
        capturado = {}
        monkeypatch.setattr(notifier.requests, "post", lambda url, data, headers, timeout, proxies=None: capturado.update(headers=headers) or FakeResp(200))
        notifier._notify("t", "m", click="http://192.0.2.10:5051/")
        assert capturado["headers"]["Click"] == "http://192.0.2.10:5051/"

    def test_titulo_con_emoji_no_revienta_por_encoding(self, monkeypatch):
        monkeypatch.setattr(notifier, "NTFY_TOPIC", "topic-test")
        capturado = {}

        def fake_post(url, data, headers, timeout, proxies=None):
            capturado["headers"] = headers
            return FakeResp(200)

        monkeypatch.setattr(notifier.requests, "post", fake_post)
        assert notifier._notify("🏠 título con emoji", "mensaje") is True
        assert capturado["headers"]["Title"] == "🏠 título con emoji".encode("utf-8")

    def test_error_http_es_fallo_sin_reintento(self, monkeypatch):
        monkeypatch.setattr(notifier, "NTFY_TOPIC", "topic-test")
        llamadas = []
        monkeypatch.setattr(notifier.requests, "post", lambda *a, proxies=None, **k: llamadas.append(proxies) or FakeResp(500, "error del servidor"))
        assert notifier._notify("t", "m") is False
        assert len(llamadas) == 1


class TestNotificarResumen:
    def test_sin_novedades_no_envia_nada(self, con, monkeypatch):
        llamado = {"veces": 0}
        monkeypatch.setattr(notifier, "_notify", lambda *a, **k: llamado.update(veces=llamado["veces"] + 1) or True)
        notifier.notificar_resumen(con, [])
        assert llamado["veces"] == 0

    def test_pisos_nuevos_disparan_un_solo_envio(self, con, piso_factory, monkeypatch):
        db.upsert_piso(con, piso_factory(url="a"))
        db.upsert_piso(con, piso_factory(url="b"))
        llamadas = []
        monkeypatch.setattr(notifier, "_notify", lambda titulo, mensaje, **k: llamadas.append((titulo, mensaje)) or True)
        notifier.notificar_resumen(con, [])
        assert len(llamadas) == 1
        assert "2 nuevos" in llamadas[0][0]

    def test_envio_exitoso_marca_los_nuevos_como_notificados(self, con, piso_factory, monkeypatch):
        db.upsert_piso(con, piso_factory(url="a"))
        monkeypatch.setattr(notifier, "_notify", lambda *a, **k: True)
        notifier.notificar_resumen(con, [])
        pendientes = db.pisos_pendientes_de_notificar(con)
        assert pendientes == []

    def test_envio_fallido_no_marca_como_notificado(self, con, piso_factory, monkeypatch):
        db.upsert_piso(con, piso_factory(url="a"))
        monkeypatch.setattr(notifier, "_notify", lambda *a, **k: False)
        notifier.notificar_resumen(con, [])
        pendientes = db.pisos_pendientes_de_notificar(con)
        assert len(pendientes) == 1

    def test_solo_cambios_de_favoritos_tambien_envia(self, con, monkeypatch):
        llamadas = []
        monkeypatch.setattr(notifier, "_notify", lambda titulo, mensaje, **k: llamadas.append((titulo, mensaje)) or True)
        cambios = [{"titulo": "Piso favorito", "url": "https://x.com", "cambios": {"precio": (800, 600)}}]
        notifier.notificar_resumen(con, cambios)
        assert len(llamadas) == 1
        assert "1 favorito actualizado" in llamadas[0][0]
        assert "800€ → 600€" in llamadas[0][1]

    def test_prioridad_alta_solo_si_hay_cambios_de_favoritos(self, con, piso_factory, monkeypatch):
        db.upsert_piso(con, piso_factory(url="a"))
        capturada = {}
        monkeypatch.setattr(notifier, "_notify", lambda titulo, mensaje, priority="default", **k: capturada.update(p=priority) or True)
        notifier.notificar_resumen(con, [])
        assert capturada["p"] == "default"

    def test_prioridad_alta_con_cambios_de_favoritos(self, con, monkeypatch):
        capturada = {}
        monkeypatch.setattr(notifier, "_notify", lambda titulo, mensaje, priority="default", **k: capturada.update(p=priority) or True)
        cambios = [{"titulo": "X", "url": "https://x.com", "cambios": {"precio": (800, 600)}}]
        notifier.notificar_resumen(con, cambios)
        assert capturada["p"] == "high"

    def test_nuevos_y_cambios_a_la_vez_en_un_solo_mensaje(self, con, piso_factory, monkeypatch):
        db.upsert_piso(con, piso_factory(url="a"))
        llamadas = []
        monkeypatch.setattr(notifier, "_notify", lambda titulo, mensaje, **k: llamadas.append((titulo, mensaje)) or True)
        cambios = [{"titulo": "Favorito", "url": "https://x.com", "cambios": {"precio": (800, 600)}}]
        notifier.notificar_resumen(con, cambios)
        titulo, mensaje = llamadas[0]
        assert "1 nuevo" in titulo and "1 favorito actualizado" in titulo
        assert "🆕 Nuevos" in mensaje
        assert "⭐ Favoritos actualizados" in mensaje

    def test_muchos_nuevos_se_truncan_con_aviso(self, con, piso_factory, monkeypatch):
        for i in range(notifier.MAX_LINEAS_NUEVOS + 3):
            db.upsert_piso(con, piso_factory(url=f"piso-{i}"))
        llamadas = []
        monkeypatch.setattr(notifier, "_notify", lambda titulo, mensaje, **k: llamadas.append((titulo, mensaje)) or True)
        notifier.notificar_resumen(con, [])
        assert "y 3 más" in llamadas[0][1]

    def test_incluye_el_enlace_a_la_web_en_el_mensaje(self, con, piso_factory, monkeypatch):
        db.upsert_piso(con, piso_factory(url="a"))
        llamadas = []
        monkeypatch.setattr(notifier, "_notify", lambda titulo, mensaje, **k: llamadas.append((titulo, mensaje, k)) or True)
        notifier.notificar_resumen(con, [])
        assert notifier.WEB_URL in llamadas[0][1]

    def test_con_nuevos_el_click_abre_las_novedades(self, con, piso_factory, monkeypatch):
        db.upsert_piso(con, piso_factory(url="a"))
        llamadas = []
        monkeypatch.setattr(notifier, "_notify", lambda titulo, mensaje, **k: llamadas.append((titulo, mensaje, k)) or True)
        notifier.notificar_resumen(con, [])
        assert llamadas[0][2]["click"] == notifier.WEB_URL.rstrip("/") + "/?novedades=1"
        assert "?novedades=1" in llamadas[0][1]

    def test_solo_cambios_en_favoritos_el_click_abre_favoritos(self, con, monkeypatch):
        llamadas = []
        monkeypatch.setattr(notifier, "_notify", lambda titulo, mensaje, **k: llamadas.append((titulo, mensaje, k)) or True)
        cambio = {"titulo": "Fav", "url": "https://x/1", "cambios": {"precio": (900, 850)}}
        notifier.notificar_resumen(con, [cambio])
        assert llamadas[0][2]["click"] == notifier.WEB_URL.rstrip("/") + "/favoritos"

    def test_pisos_ya_notificados_no_se_reenvian(self, con, piso_factory, monkeypatch):
        db.upsert_piso(con, piso_factory(url="a"))
        monkeypatch.setattr(notifier, "_notify", lambda *a, **k: True)
        notifier.notificar_resumen(con, [])  # primer envío, marca notificado
        llamado = {"veces": 0}
        monkeypatch.setattr(notifier, "_notify", lambda *a, **k: llamado.update(veces=llamado["veces"] + 1) or True)
        notifier.notificar_resumen(con, [])  # nada nuevo esta vez
        assert llamado["veces"] == 0

"""
Perfiles como en producción: La Rioja e Irlanda (el
usuario y su pareja, login conjunto) en la misma BD y la misma web, sin
que nada de uno se vea ni se pueda tocar desde el otro. BD SQLite real,
main.main() con los colectores de los dos perfiles simulados, y la web por
HTTP con sesiones de verdad.
"""
import re
from datetime import datetime, timedelta, timezone

import pytest

import app as appmod
import db
import main
import notifier
import perfiles
from config import PERFILES
from tests.conftest import make_piso


CLAVES = {"la-rioja": "clave-del-amigo-1", "irlanda": "clave-de-los-dos-2"}


def ids_en(html):
    return re.findall(r'id="fila-([0-9a-f]+)"', html)


def de_irlanda(clave, **kw):
    datos = {"titulo": f"Apartment {clave}, Dublin 8", "precio": 2200, "habitaciones": 2, "zona": "Portobello",
             "ciudad": "Dublin 8", "extra": {"ber": "C1", "eircode": "D08 R6T2"}}
    datos.update(kw)
    return make_piso(portal="myhome", url=f"https://www.myhome.ie/rentals/brochure/{clave}/1", **datos)


def de_la_rioja(clave, **kw):
    datos = {"titulo": f"Piso {clave}", "precio": 700, "habitaciones": 3, "ciudad": "Logroño"}
    datos.update(kw)
    return make_piso(portal="fotocasa", url=f"https://www.fotocasa.es/anuncio/{clave}", **datos)


@pytest.fixture
def claves(con):
    for perfil, clave in CLAVES.items():
        db.fijar_clave(con, perfil, clave)


@pytest.fixture
def navegador(con, claves):
    """Cliente sin sesión, como un navegador que llega por primera vez."""
    appmod.app.config["TESTING"] = True
    appmod._fallos_login.clear()
    with appmod.app.test_client() as c:
        c.environ_base["HTTP_X_PISO_RADAR"] = "1"
        yield c


def entrar(cliente, perfil, clave=None):
    return cliente.post("/login", data={"perfil": perfil, "clave": clave or CLAVES[perfil]})


@pytest.fixture
def dos_perfiles(con):
    """Los dos perfiles con anuncios: Irlanda con un favorito y un
    descartado; La Rioja con su propio favorito."""
    for p in [de_la_rioja("rio-1"), de_la_rioja("rio-2", ciudad="Haro"),
              de_irlanda("ie-1"), de_irlanda("ie-2", ciudad="Cork", zona="Kinsale")]:
        db.upsert_piso(con, {**p, "perfil": "irlanda" if p["portal"] == "myhome" else "la-rioja"})
    ids = {f["url"].split("/")[-2] if f["portal"] == "myhome" else f["url"].rsplit("/", 1)[1]: f["id"]
           for f in con.execute("SELECT id, url, portal FROM pisos")}
    db.marcar_favorito(con, ids["rio-1"], True)
    db.marcar_favorito(con, ids["ie-1"], True)
    db.marcar_descartado(con, ids["rio-2"], True)
    return ids


# --- Inicio de sesión ------------------------------------------------------------

class TestLogin:
    def test_sin_sesion_se_va_al_login_y_luego_a_donde_se_queria(self, navegador):
        resp = navegador.get("/favoritos?portal=myhome")
        assert resp.status_code == 302
        assert resp.headers["Location"].startswith("/login?siguiente=/favoritos")
        resp = navegador.post("/login", data={"perfil": "irlanda", "clave": CLAVES["irlanda"],
                                              "siguiente": "/favoritos?portal=myhome"})
        assert resp.headers["Location"] == "/favoritos?portal=myhome"
        assert navegador.get("/favoritos?portal=myhome").status_code == 200

    def test_contrasena_de_otro_perfil_no_sirve(self, navegador):
        resp = entrar(navegador, "irlanda", CLAVES["la-rioja"])
        assert resp.status_code == 401
        assert "incorrectos" in resp.text
        assert navegador.get("/").status_code == 302

    def test_perfil_inexistente(self, navegador):
        assert entrar(navegador, "madrid", "lo-que-sea").status_code == 401

    def test_perfil_sin_contrasena_fijada_no_deja_entrar(self, navegador, con):
        con.execute("DELETE FROM perfiles WHERE id = 'irlanda'")
        con.commit()
        assert entrar(navegador, "irlanda", "").status_code == 401

    def test_no_redirige_fuera_de_la_web(self, navegador):
        for malo in ["//evil.example/robar", "https://evil.example/", "javascript:alert(1)"]:
            resp = navegador.post("/login", data={"perfil": "irlanda", "clave": CLAVES["irlanda"], "siguiente": malo})
            assert resp.headers["Location"] == "/", malo

    def test_bloqueo_tras_varios_fallos_incluso_con_la_buena(self, navegador):
        for _ in range(appmod.MAX_INTENTOS):
            assert entrar(navegador, "irlanda", "mal").status_code == 401
        assert entrar(navegador, "irlanda").status_code == 429
        assert navegador.get("/").status_code == 302

    def test_el_bloqueo_es_por_persona_aunque_todos_lleguen_por_el_proxy(self, navegador):
        # Detrás del proxy inverso todo llega desde su IP.
        proxy = {"REMOTE_ADDR": "172.22.0.5"}
        for _ in range(appmod.MAX_INTENTOS):
            navegador.post("/login", data={"perfil": "irlanda", "clave": "mal"},
                           environ_base={**proxy, "HTTP_X_FORWARDED_FOR": "100.64.0.10"})
        bloqueado = navegador.post("/login", data={"perfil": "irlanda", "clave": CLAVES["irlanda"]},
                                   environ_base={**proxy, "HTTP_X_FORWARDED_FOR": "100.64.0.10"})
        assert bloqueado.status_code == 429
        otra_persona = navegador.post("/login", data={"perfil": "irlanda", "clave": CLAVES["irlanda"]},
                                      environ_base={**proxy, "HTTP_X_FORWARDED_FOR": "100.64.0.22"})
        assert otra_persona.status_code == 302

    def test_cerrar_sesion(self, navegador):
        entrar(navegador, "irlanda")
        assert navegador.get("/").status_code == 200
        navegador.get("/salir")
        assert navegador.get("/").status_code == 302

    def test_acciones_sin_sesion_dan_401_y_no_cambian_nada(self, navegador, con, dos_perfiles):
        resp = navegador.post("/favorito", data={"piso_id": dos_perfiles["ie-2"], "favorito": "1"})
        assert resp.status_code == 401
        assert con.execute("SELECT favorito FROM pisos WHERE id = ?", (dos_perfiles["ie-2"],)).fetchone()[0] == 0

    def test_la_sesion_dura_un_anio(self, navegador):
        resp = entrar(navegador, "irlanda")
        cookie = resp.headers["Set-Cookie"]
        assert "HttpOnly" in cookie and "SameSite=Lax" in cookie
        caduca = datetime.strptime(re.search(r"Expires=([^;]+)", cookie).group(1),
                                   "%a, %d %b %Y %H:%M:%S GMT").replace(tzinfo=timezone.utc)
        assert caduca - datetime.now(timezone.utc) > timedelta(days=360)

    def test_programa_de_claves(self, con, capsys):
        assert perfiles.main(["clave", "irlanda", "--generar"]) == 0
        nueva = capsys.readouterr().out.rsplit("Es: ", 1)[1].strip()
        assert db.comprobar_clave(con, "irlanda", nueva)
        assert not db.comprobar_clave(con, "la-rioja", nueva)
        hash_guardado = con.execute("SELECT clave_hash FROM perfiles WHERE id = 'irlanda'").fetchone()[0]
        assert nueva not in hash_guardado


# --- Aislamiento entre perfiles -----------------------------------------------------

class TestAislamiento:
    def test_cada_perfil_ve_solo_lo_suyo(self, navegador, dos_perfiles):
        entrar(navegador, "irlanda")
        explorar = navegador.get("/").text
        assert set(ids_en(explorar)) == {dos_perfiles["ie-1"], dos_perfiles["ie-2"]}
        assert "Alquiler en Irlanda" in explorar
        assert "Logroño" not in explorar and "Haro" not in explorar  # ni en el desplegable de municipios
        assert ids_en(navegador.get("/favoritos").text) == [dos_perfiles["ie-1"]]
        assert ids_en(navegador.get("/descartados").text) == []
        assert 'data-contador="favoritos">1</span>' in explorar

        navegador.get("/salir")
        entrar(navegador, "la-rioja")
        explorar = navegador.get("/").text
        assert ids_en(explorar) == [dos_perfiles["rio-1"]]  # rio-2 está descartado
        assert "Dublin" not in explorar and "Cork" not in explorar
        assert ids_en(navegador.get("/descartados").text) == [dos_perfiles["rio-2"]]

    def test_buscar_no_encuentra_anuncios_del_otro_perfil(self, navegador, dos_perfiles):
        entrar(navegador, "irlanda")
        assert ids_en(navegador.get("/?q=logroño").text) == []
        assert ids_en(navegador.get("/?ciudad=Logroño").text) == []
        assert ids_en(navegador.get("/?portal=fotocasa").text) == []

    @pytest.mark.parametrize("ruta,campo", [("/favorito", "favorito"), ("/descartado", "descartado"),
                                            ("/consultado", "consultado")])
    def test_no_se_puede_tocar_un_anuncio_del_otro_perfil(self, navegador, con, dos_perfiles, ruta, campo):
        entrar(navegador, "irlanda")
        antes = tuple(con.execute("SELECT favorito, descartado, consultado FROM pisos WHERE id = ?",
                                  (dos_perfiles["rio-1"],)).fetchone())
        resp = navegador.post(ruta, data={"piso_id": dos_perfiles["rio-1"], campo: "0" if campo == "favorito" else "1"})
        assert resp.status_code == 404
        despues = tuple(con.execute("SELECT favorito, descartado, consultado FROM pisos WHERE id = ?",
                                    (dos_perfiles["rio-1"],)).fetchone())
        assert antes == despues

    def test_estado_de_fuentes_solo_del_perfil(self, navegador, con, dos_perfiles):
        db.registrar_recogida(con, {"fotocasa": 10, "pisos.com": 0, "habitaclia": 5, "myhome": 600})
        entrar(navegador, "irlanda")
        html = navegador.get("/").text
        panel = html.split('class="estado__panel"')[1].split("</details>")[0]
        assert "MyHome" in panel and "600 anuncios" in panel
        assert "Fotocasa" not in panel and "pisos.com" not in panel and "Habitaclia" not in panel
        assert "MyHome se revisa cada 120 min" in panel
        assert "no está devolviendo anuncios" not in html  # el 0 de pisos.com es cosa de La Rioja

    def test_opciones_del_perfil(self, navegador, dos_perfiles):
        entrar(navegador, "irlanda")
        html = navegador.get("/").text
        assert '<option value="5000"' in html and '<option value="400"' not in html
        assert '<span>1+</span>' in html
        assert "BER C1" in html and "Toda Irlanda" in html


# --- Recogida y avisos con los dos perfiles ------------------------------------------

@pytest.fixture
def reloj(monkeypatch):
    estado = {"ahora": datetime(2026, 11, 2, 9, 0, tzinfo=timezone.utc)}

    class Falso(datetime):
        @classmethod
        def now(cls, tz=None):
            return estado["ahora"] if tz else estado["ahora"].replace(tzinfo=None)

    for modulo in (db, appmod, main):
        monkeypatch.setattr(modulo, "datetime", Falso)

    class Reloj:
        def avanzar(self, **kw):
            estado["ahora"] += timedelta(**kw)
    return Reloj()


@pytest.fixture
def produccion(monkeypatch):
    """Los colectores de los dos perfiles y ntfy simulados, con un canal
    distinto por perfil."""
    publicado = {"fotocasa": [], "pisos_com": [], "habitaclia": [], "myhome": []}
    llamadas = {m: 0 for m in publicado}
    avisos = []

    def colector(modulo):
        def collect(zonas):
            llamadas[modulo] += 1
            return list(publicado[modulo])
        return collect
    for modulo in publicado:
        monkeypatch.setattr(getattr(main, modulo), "collect", colector(modulo))
    perfiles_test = {p: {**cfg} for p, cfg in PERFILES.items()}
    perfiles_test["la-rioja"]["ntfy_topic"] = "canal-del-amigo"
    perfiles_test["irlanda"]["ntfy_topic"] = "canal-de-los-dos"
    monkeypatch.setattr(main, "PERFILES", perfiles_test)
    monkeypatch.setattr(notifier, "PERFILES", perfiles_test)
    monkeypatch.setattr(notifier, "_post", lambda titulo, mensaje, priority, tags, click, proxies=None, topic=None:
                        avisos.append({"topic": topic, "titulo": titulo, "mensaje": mensaje}) or
                        type("R", (), {"status_code": 200, "text": ""})())
    return {"publicado": publicado, "llamadas": llamadas, "avisos": avisos, "perfiles": perfiles_test}


class TestRecogidaConDosPerfiles:
    def test_cada_anuncio_a_su_perfil_y_cada_aviso_a_su_canal(self, con, reloj, produccion):
        pub, avisos = produccion["publicado"], produccion["avisos"]
        pub["fotocasa"] = [de_la_rioja("r1"), de_la_rioja("r2")]
        pub["myhome"] = [de_irlanda("i1")]
        main.main()

        perfil_de = {f["portal"]: f["perfil"] for f in con.execute("SELECT portal, perfil FROM pisos")}
        assert perfil_de == {"fotocasa": "la-rioja", "myhome": "irlanda"}
        por_canal = {a["topic"]: a for a in avisos}
        assert set(por_canal) == {"canal-del-amigo", "canal-de-los-dos"}
        assert "La Rioja: 2 nuevos" in por_canal["canal-del-amigo"]["titulo"]
        assert "Irlanda: 1 nuevo" in por_canal["canal-de-los-dos"]["titulo"]
        assert "Dublin" not in por_canal["canal-del-amigo"]["mensaje"]
        assert "Piso r1" not in por_canal["canal-de-los-dos"]["mensaje"]

    def test_irlanda_sin_canal_no_avisa_al_del_amigo(self, con, reloj, produccion, monkeypatch):
        produccion["perfiles"]["irlanda"]["ntfy_topic"] = ""
        monkeypatch.setattr(notifier, "NTFY_TOPIC", "canal-del-amigo")
        produccion["publicado"]["myhome"] = [de_irlanda("i1")]
        main.main()
        assert produccion["avisos"] == []
        # Queda pendiente para cuando haya canal.
        assert [f["url"] for f in db.pisos_pendientes_de_notificar(con, "irlanda")] == [de_irlanda("i1")["url"]]

    def test_el_minimo_de_habitaciones_es_el_de_cada_perfil(self, con, reloj, produccion):
        produccion["publicado"]["fotocasa"] = [de_la_rioja("r-2hab", habitaciones=2)]
        produccion["publicado"]["myhome"] = [de_irlanda("i-estudio", habitaciones=0)]
        main.main()
        assert [f["portal"] for f in con.execute("SELECT portal FROM pisos")] == ["myhome"]

    def test_myhome_cada_dos_horas_y_los_demas_en_cada_corrida(self, con, reloj, produccion):
        produccion["publicado"]["fotocasa"] = [de_la_rioja("r1")]
        produccion["publicado"]["myhome"] = [de_irlanda("i1")]
        for _ in range(9):  # cuatro horas de cron, cada 30 min
            main.main()
            reloj.avanzar(minutes=30)
        assert produccion["llamadas"]["fotocasa"] == 9
        assert produccion["llamadas"]["myhome"] == 3  # a las 0, 2 y 4 horas

    def test_entre_pasadas_de_myhome_sus_anuncios_siguen_disponibles(self, con, reloj, produccion):
        produccion["publicado"]["myhome"] = [de_irlanda("i1")]
        produccion["publicado"]["fotocasa"] = [de_la_rioja("r1")]
        appmod.app.config["TESTING"] = True
        with appmod.app.test_client() as c:
            c.environ_base["HTTP_X_PISO_RADAR"] = "1"
            with c.session_transaction() as s:
                s["perfil"] = "irlanda"
            for _ in range(4):
                main.main()
                reloj.avanzar(minutes=30)
                assert len(ids_en(c.get("/").text)) == 1

    def test_un_perfil_roto_no_deja_sin_recoger_al_otro(self, con, reloj, produccion, monkeypatch):
        def roto(zonas):
            raise RuntimeError("MyHome cambió su web")
        monkeypatch.setattr(main.myhome, "collect", roto)
        produccion["publicado"]["fotocasa"] = [de_la_rioja("r1")]
        main.main()
        assert [f["portal"] for f in con.execute("SELECT portal FROM pisos")] == ["fotocasa"]
        assert con.execute("SELECT anuncios FROM recogidas WHERE portal = 'myhome'").fetchone()[0] == 0

    def test_migracion_de_una_bd_anterior_a_los_perfiles(self, tmp_path, monkeypatch):
        """La BD de producción no tenía columna perfil: todo lo que había es de
        La Rioja y tiene que seguir viéndose ahí, con sus favoritos."""
        import sqlite3
        ruta = tmp_path / "vieja.db"
        vieja = sqlite3.connect(ruta)
        vieja.executescript("""
            CREATE TABLE pisos (id TEXT PRIMARY KEY, portal TEXT NOT NULL, titulo TEXT NOT NULL, precio INTEGER,
              m2 INTEGER, habitaciones INTEGER, zona TEXT, ciudad TEXT, url TEXT NOT NULL, descripcion TEXT,
              fecha_publicacion TEXT, fecha_recogida TEXT NOT NULL, notificado INTEGER NOT NULL DEFAULT 0,
              consultado INTEGER NOT NULL DEFAULT 0, favorito INTEGER NOT NULL DEFAULT 0);
            INSERT INTO pisos (id, portal, titulo, url, fecha_recogida, favorito)
              VALUES ('viejo1', 'fotocasa', 'Piso de antes', 'https://f/1', '2026-09-03T10:00:00+00:00', 1);
        """)
        vieja.commit()
        vieja.close()
        monkeypatch.setattr(db, "DB_PATH", ruta)
        con = db.connect()
        try:
            fila = con.execute("SELECT perfil, favorito, extra FROM pisos WHERE id = 'viejo1'").fetchone()
            assert tuple(fila) == ("la-rioja", 1, None)
            assert [f["id"] for f in db.listar_pisos(con, perfil="la-rioja", solo_favoritos=True)] == ["viejo1"]
            assert db.listar_pisos(con, perfil="irlanda") == []
        finally:
            con.close()

    def test_pendiente_de_irlanda_nunca_va_en_el_aviso_del_amigo(self, con, reloj, produccion):
        # Corrida 1: el canal de Irlanda falla (sin configurar) y su anuncio
        # queda pendiente de avisar.
        produccion["perfiles"]["irlanda"]["ntfy_topic"] = ""
        produccion["publicado"]["myhome"] = [de_irlanda("pendiente")]
        main.main()
        # Corrida 2: La Rioja tiene un anuncio nuevo y avisa a su canal.
        reloj.avanzar(minutes=30)
        produccion["publicado"]["fotocasa"] = [de_la_rioja("nuevo")]
        main.main()
        avisos_amigo = [a for a in produccion["avisos"] if a["topic"] == "canal-del-amigo"]
        assert len(avisos_amigo) == 1
        assert "1 nuevo" in avisos_amigo[0]["titulo"]
        assert "pendiente" not in avisos_amigo[0]["mensaje"] and "Dublin" not in avisos_amigo[0]["mensaje"]
        # Y el de Irlanda sigue pendiente para su propio canal.
        assert len(db.pisos_pendientes_de_notificar(con, "irlanda")) == 1

    def test_un_fallo_procesando_un_perfil_no_frena_al_siguiente_ni_la_purga(self, con, reloj, produccion, monkeypatch):
        original = main.fuentes_pendientes

        def falla_la_rioja(con, perfil, ahora=None):
            if perfil == "la-rioja":
                raise RuntimeError("fallo inesperado procesando La Rioja")
            return original(con, perfil, ahora)
        monkeypatch.setattr(main, "fuentes_pendientes", falla_la_rioja)
        purgas = []
        monkeypatch.setattr(main, "purgar_retirados", lambda con: purgas.append(1) or 0)
        produccion["publicado"]["myhome"] = [de_irlanda("i1")]
        main.main()
        assert [f["perfil"] for f in con.execute("SELECT perfil FROM pisos")] == ["irlanda"]
        assert purgas == [1]

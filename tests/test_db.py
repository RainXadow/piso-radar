from datetime import datetime, timedelta, timezone
import db


class TestMakeId:
    def test_es_determinista(self):
        a = db.make_id("fotocasa", "https://x.com/1")
        b = db.make_id("fotocasa", "https://x.com/1")
        assert a == b

    def test_ignora_mayusculas(self):
        a = db.make_id("fotocasa", "https://x.com/1")
        b = db.make_id("FOTOCASA", "HTTPS://X.COM/1")
        assert a == b

    def test_distinto_portal_mismo_url_da_id_distinto(self):
        a = db.make_id("fotocasa", "https://x.com/1")
        b = db.make_id("pisos.com", "https://x.com/1")
        assert a != b

    def test_distinta_url_mismo_portal_da_id_distinto(self):
        a = db.make_id("fotocasa", "https://x.com/1")
        b = db.make_id("fotocasa", "https://x.com/2")
        assert a != b


class TestUpsertPisoNuevo:
    def test_devuelve_nuevo_true_sin_cambios_ni_favorito(self, con, piso_factory):
        nuevo, cambios, favorito = db.upsert_piso(con, piso_factory())
        assert (nuevo, cambios, favorito) == (True, {}, False)

    def test_guarda_todos_los_campos(self, con, piso_factory):
        p = piso_factory(titulo="Ático con vistas", precio=950, m2=85, habitaciones=3,
                          tipo="Piso", zona="Gran Via", ciudad="Logroño")
        db.upsert_piso(con, p)
        fila = con.execute("SELECT * FROM pisos").fetchone()
        assert fila["titulo"] == "Ático con vistas"
        assert fila["precio"] == 950
        assert fila["m2"] == 85
        assert fila["habitaciones"] == 3
        assert fila["tipo"] == "Piso"
        assert fila["zona"] == "Gran Via"
        assert fila["ciudad"] == "Logroño"
        assert fila["notificado"] == 0
        assert fila["consultado"] == 0
        assert fila["favorito"] == 0

    def test_id_es_hash_de_portal_y_url(self, con, piso_factory):
        p = piso_factory(portal="habitaclia", url="https://habitaclia.com/x")
        db.upsert_piso(con, p)
        fila = con.execute("SELECT id FROM pisos").fetchone()
        assert fila["id"] == db.make_id("habitaclia", "https://habitaclia.com/x")


class TestUpsertPisoExistente:
    def test_no_lo_vuelve_a_insertar(self, con, piso_factory):
        p = piso_factory()
        db.upsert_piso(con, p)
        nuevo, _, _ = db.upsert_piso(con, p)
        assert nuevo is False
        assert con.execute("SELECT COUNT(*) c FROM pisos").fetchone()["c"] == 1

    def test_actualiza_el_precio_y_lo_reporta_como_cambio(self, con, piso_factory):
        db.upsert_piso(con, piso_factory(precio=800))
        nuevo, cambios, _ = db.upsert_piso(con, piso_factory(precio=600))
        assert nuevo is False
        assert cambios == {"precio": (800, 600)}
        fila = con.execute("SELECT precio FROM pisos").fetchone()
        assert fila["precio"] == 600

    def test_actualiza_m2_y_habitaciones_a_la_vez(self, con, piso_factory):
        db.upsert_piso(con, piso_factory(m2=70, habitaciones=2))
        _, cambios, _ = db.upsert_piso(con, piso_factory(m2=90, habitaciones=3))
        assert cambios == {"m2": (70, 90), "habitaciones": (2, 3)}

    def test_sin_cambios_devuelve_diccionario_vacio(self, con, piso_factory):
        db.upsert_piso(con, piso_factory())
        _, cambios, _ = db.upsert_piso(con, piso_factory())
        assert cambios == {}

    def test_titulo_y_descripcion_se_actualizan_pero_no_se_reportan_como_cambio(self, con, piso_factory):
        db.upsert_piso(con, piso_factory(titulo="Piso viejo título", descripcion="desc vieja"))
        _, cambios, _ = db.upsert_piso(con, piso_factory(titulo="Piso nuevo título", descripcion="desc nueva"))
        assert cambios == {}  # titulo/descripcion no están en CAMPOS_NOTIFICABLES
        fila = con.execute("SELECT titulo, descripcion FROM pisos").fetchone()
        assert fila["titulo"] == "Piso nuevo título"
        assert fila["descripcion"] == "desc nueva"

    def test_valor_nuevo_none_no_pisa_el_valor_guardado(self, con, piso_factory):
        db.upsert_piso(con, piso_factory(m2=70))
        _, cambios, _ = db.upsert_piso(con, piso_factory(m2=None))
        assert cambios == {}
        assert con.execute("SELECT m2 FROM pisos").fetchone()["m2"] == 70

    def test_reporta_si_el_piso_existente_es_favorito(self, con, piso_factory):
        db.upsert_piso(con, piso_factory())
        fila = con.execute("SELECT id FROM pisos").fetchone()
        db.marcar_favorito(con, fila["id"], True)
        _, _, favorito = db.upsert_piso(con, piso_factory(precio=700))
        assert favorito is True

    def test_no_favorito_reporta_false(self, con, piso_factory):
        db.upsert_piso(con, piso_factory())
        _, _, favorito = db.upsert_piso(con, piso_factory(precio=700))
        assert favorito is False

    def test_consultado_y_favorito_sobreviven_a_una_actualizacion(self, con, piso_factory):
        db.upsert_piso(con, piso_factory())
        fila = con.execute("SELECT id FROM pisos").fetchone()
        db.marcar_favorito(con, fila["id"], True)
        db.marcar_consultado(con, fila["id"], True)
        db.upsert_piso(con, piso_factory(precio=700))
        fila = con.execute("SELECT favorito, consultado FROM pisos").fetchone()
        assert fila["favorito"] == 1
        assert fila["consultado"] == 1


class TestExiste:
    def test_true_si_ya_esta_guardado(self, con, piso_factory):
        p = piso_factory()
        db.upsert_piso(con, p)
        assert db.existe(con, p["portal"], p["url"]) is True

    def test_false_si_no_esta(self, con):
        assert db.existe(con, "fotocasa", "https://x.com/no-existe") is False


class TestNotificados:
    def test_piso_nuevo_esta_pendiente_de_notificar(self, con, piso_factory):
        db.upsert_piso(con, piso_factory())
        pendientes = db.pisos_pendientes_de_notificar(con)
        assert len(pendientes) == 1

    def test_marcar_notificado_lo_saca_de_pendientes(self, con, piso_factory):
        db.upsert_piso(con, piso_factory())
        fila = con.execute("SELECT id FROM pisos").fetchone()
        db.marcar_notificado(con, fila["id"])
        assert db.pisos_pendientes_de_notificar(con) == []

    def test_actualizar_un_piso_existente_no_lo_vuelve_a_marcar_pendiente(self, con, piso_factory):
        db.upsert_piso(con, piso_factory())
        fila = con.execute("SELECT id FROM pisos").fetchone()
        db.marcar_notificado(con, fila["id"])
        db.upsert_piso(con, piso_factory(precio=700))  # cambia de precio, no es nuevo
        assert db.pisos_pendientes_de_notificar(con) == []


class TestListarPisos:
    def test_orden_por_precio_ascendente(self, con, piso_factory):
        db.upsert_piso(con, piso_factory(url="a", precio=900))
        db.upsert_piso(con, piso_factory(url="b", precio=600))
        db.upsert_piso(con, piso_factory(url="c", precio=750))
        precios = [f["precio"] for f in db.listar_pisos(con, orden="precio", direccion="asc")]
        assert precios == [600, 750, 900]

    def test_orden_por_precio_descendente(self, con, piso_factory):
        db.upsert_piso(con, piso_factory(url="a", precio=900))
        db.upsert_piso(con, piso_factory(url="b", precio=600))
        precios = [f["precio"] for f in db.listar_pisos(con, orden="precio", direccion="desc")]
        assert precios == [900, 600]

    def test_nulos_van_al_final_en_ascendente(self, con, piso_factory):
        db.upsert_piso(con, piso_factory(url="a", m2=80))
        db.upsert_piso(con, piso_factory(url="b", m2=None))
        db.upsert_piso(con, piso_factory(url="c", m2=50))
        m2s = [f["m2"] for f in db.listar_pisos(con, orden="m2", direccion="asc")]
        assert m2s == [50, 80, None]

    def test_nulos_van_al_final_en_descendente_tambien(self, con, piso_factory):
        db.upsert_piso(con, piso_factory(url="a", m2=80))
        db.upsert_piso(con, piso_factory(url="b", m2=None))
        db.upsert_piso(con, piso_factory(url="c", m2=50))
        m2s = [f["m2"] for f in db.listar_pisos(con, orden="m2", direccion="desc")]
        assert m2s == [80, 50, None]

    def test_columna_de_orden_desconocida_cae_a_fecha_recogida_sin_reventar(self, con, piso_factory):
        db.upsert_piso(con, piso_factory())
        # "; DROP TABLE pisos;--" ni de coña debería colarse en el ORDER BY
        filas = db.listar_pisos(con, orden="precio; DROP TABLE pisos;--")
        assert len(filas) == 1
        assert con.execute("SELECT COUNT(*) c FROM pisos").fetchone()["c"] == 1

    def test_filtra_por_portal(self, con, piso_factory):
        db.upsert_piso(con, piso_factory(portal="fotocasa", url="a"))
        db.upsert_piso(con, piso_factory(portal="pisos.com", url="b"))
        filas = db.listar_pisos(con, portal="fotocasa")
        assert len(filas) == 1
        assert filas[0]["portal"] == "fotocasa"

    def test_sin_filtro_de_portal_devuelve_todos(self, con, piso_factory):
        db.upsert_piso(con, piso_factory(portal="fotocasa", url="a"))
        db.upsert_piso(con, piso_factory(portal="pisos.com", url="b"))
        assert len(db.listar_pisos(con)) == 2

    def test_solo_favoritos(self, con, piso_factory):
        db.upsert_piso(con, piso_factory(url="a"))
        db.upsert_piso(con, piso_factory(url="b"))
        fila_a = con.execute("SELECT id FROM pisos WHERE url='a'").fetchone()
        db.marcar_favorito(con, fila_a["id"], True)
        filas = db.listar_pisos(con, solo_favoritos=True)
        assert len(filas) == 1
        assert filas[0]["url"] == "a"

    def test_portal_y_favoritos_combinados(self, con, piso_factory):
        db.upsert_piso(con, piso_factory(portal="fotocasa", url="a"))
        db.upsert_piso(con, piso_factory(portal="fotocasa", url="b"))
        db.upsert_piso(con, piso_factory(portal="pisos.com", url="c"))
        for url in ("a", "c"):
            fila = con.execute("SELECT id FROM pisos WHERE url=?", (url,)).fetchone()
            db.marcar_favorito(con, fila["id"], True)
        filas = db.listar_pisos(con, portal="fotocasa", solo_favoritos=True)
        assert [f["url"] for f in filas] == ["a"]

    def test_filtra_por_ciudad(self, con, piso_factory):
        db.upsert_piso(con, piso_factory(ciudad="Logroño", url="a"))
        db.upsert_piso(con, piso_factory(ciudad="Nájera", url="b"))
        filas = db.listar_pisos(con, ciudad="Nájera")
        assert [f["url"] for f in filas] == ["b"]

    def test_ciudad_y_portal_combinados(self, con, piso_factory):
        db.upsert_piso(con, piso_factory(portal="fotocasa", ciudad="Logroño", url="a"))
        db.upsert_piso(con, piso_factory(portal="pisos.com", ciudad="Logroño", url="b"))
        db.upsert_piso(con, piso_factory(portal="fotocasa", ciudad="Nájera", url="c"))
        filas = db.listar_pisos(con, portal="fotocasa", ciudad="Logroño")
        assert [f["url"] for f in filas] == ["a"]

    def test_pagina_limita_resultados(self, con, piso_factory):
        for i in range(5):
            db.upsert_piso(con, piso_factory(url=f"p{i}", precio=100 + i))
        filas = db.listar_pisos(con, orden="precio", direccion="asc", pagina=1, por_pagina=2)
        assert [f["precio"] for f in filas] == [100, 101]

    def test_segunda_pagina_trae_los_siguientes(self, con, piso_factory):
        for i in range(5):
            db.upsert_piso(con, piso_factory(url=f"p{i}", precio=100 + i))
        filas = db.listar_pisos(con, orden="precio", direccion="asc", pagina=2, por_pagina=2)
        assert [f["precio"] for f in filas] == [102, 103]

    def test_ultima_pagina_incompleta_no_revienta(self, con, piso_factory):
        for i in range(5):
            db.upsert_piso(con, piso_factory(url=f"p{i}", precio=100 + i))
        filas = db.listar_pisos(con, orden="precio", direccion="asc", pagina=3, por_pagina=2)
        assert [f["precio"] for f in filas] == [104]

    def test_pagina_mas_alla_del_final_devuelve_vacio(self, con, piso_factory):
        db.upsert_piso(con, piso_factory(url="a"))
        assert db.listar_pisos(con, pagina=5, por_pagina=2) == []

    def test_pagina_cero_o_negativa_se_trata_como_uno(self, con, piso_factory):
        db.upsert_piso(con, piso_factory(url="a"))
        assert len(db.listar_pisos(con, pagina=0)) == 1
        assert len(db.listar_pisos(con, pagina=-3)) == 1

    def test_por_defecto_pagina_24(self, con, piso_factory):
        for i in range(30):
            db.upsert_piso(con, piso_factory(url=f"p{i}"))
        assert len(db.listar_pisos(con)) == 24

    def test_orden_por_precio_m2(self, con, piso_factory):
        db.upsert_piso(con, piso_factory(url="a", precio=1000, m2=100))  # 10 €/m²
        db.upsert_piso(con, piso_factory(url="b", precio=600, m2=40))    # 15 €/m²
        db.upsert_piso(con, piso_factory(url="c", precio=900, m2=None))
        urls = [f["url"] for f in db.listar_pisos(con, orden="precio_m2", direccion="asc")]
        assert urls == ["a", "b", "c"]


class TestContarPisos:
    def test_cuenta_todos_sin_filtro(self, con, piso_factory):
        for i in range(3):
            db.upsert_piso(con, piso_factory(url=f"p{i}"))
        assert db.contar_pisos(con) == 3

    def test_no_se_ve_afectado_por_la_paginacion(self, con, piso_factory):
        for i in range(30):
            db.upsert_piso(con, piso_factory(url=f"p{i}"))
        assert db.contar_pisos(con) == 30

    def test_respeta_los_mismos_filtros_que_listar_pisos(self, con, piso_factory):
        db.upsert_piso(con, piso_factory(portal="fotocasa", ciudad="Logroño", url="a"))
        db.upsert_piso(con, piso_factory(portal="pisos.com", ciudad="Logroño", url="b"))
        assert db.contar_pisos(con, portal="fotocasa") == 1
        assert db.contar_pisos(con, ciudad="Logroño") == 2

    def test_cero_si_no_hay_pisos(self, con):
        assert db.contar_pisos(con) == 0


class TestPortalesDistintos:
    def test_lista_ordenada_sin_duplicados(self, con, piso_factory):
        db.upsert_piso(con, piso_factory(portal="pisos.com", url="a"))
        db.upsert_piso(con, piso_factory(portal="fotocasa", url="b"))
        db.upsert_piso(con, piso_factory(portal="fotocasa", url="c"))
        assert db.portales_distintos(con) == ["fotocasa", "pisos.com"]

    def test_vacio_si_no_hay_pisos(self, con):
        assert db.portales_distintos(con) == []


class TestCiudadesDistintas:
    def test_lista_ordenada_sin_duplicados(self, con, piso_factory):
        db.upsert_piso(con, piso_factory(ciudad="Nájera", url="a"))
        db.upsert_piso(con, piso_factory(ciudad="Logroño", url="b"))
        db.upsert_piso(con, piso_factory(ciudad="Logroño", url="c"))
        assert db.ciudades_distintas(con) == ["Logroño", "Nájera"]

    def test_excluye_ciudad_nula_o_vacia(self, con, piso_factory):
        db.upsert_piso(con, piso_factory(ciudad=None, url="a"))
        db.upsert_piso(con, piso_factory(ciudad="", url="b"))
        db.upsert_piso(con, piso_factory(ciudad="Logroño", url="c"))
        assert db.ciudades_distintas(con) == ["Logroño"]

    def test_vacio_si_no_hay_pisos(self, con):
        assert db.ciudades_distintas(con) == []


class TestMarcarConsultadoYFavorito:
    def test_marcar_consultado_true(self, con, piso):
        db.marcar_consultado(con, piso["id"], True)
        assert con.execute("SELECT consultado FROM pisos WHERE id=?", (piso["id"],)).fetchone()["consultado"] == 1

    def test_marcar_consultado_false_desmarca(self, con, piso):
        db.marcar_consultado(con, piso["id"], True)
        db.marcar_consultado(con, piso["id"], False)
        assert con.execute("SELECT consultado FROM pisos WHERE id=?", (piso["id"],)).fetchone()["consultado"] == 0

    def test_marcar_favorito_true(self, con, piso):
        db.marcar_favorito(con, piso["id"], True)
        assert con.execute("SELECT favorito FROM pisos WHERE id=?", (piso["id"],)).fetchone()["favorito"] == 1

    def test_marcar_favorito_id_inexistente_no_revienta(self, con):
        db.marcar_favorito(con, "no-existe", True)  # no debe lanzar excepción


class TestMigracionColumnas:
    def test_connect_es_idempotente(self, con):
        # con() ya llamó a connect() una vez (via la fixture); llamarlo nombraría
        # dos veces la migración -- aquí se comprueba que una segunda pasada
        # sobre la misma BD no falla ni duplica nada.
        columnas_antes = {f["name"] for f in con.execute("PRAGMA table_info(pisos)")}
        db._migrar_columnas_nuevas(con)  # no debe lanzar aunque 'tipo' ya exista
        columnas_despues = {f["name"] for f in con.execute("PRAGMA table_info(pisos)")}
        assert columnas_antes == columnas_despues
        assert {"tipo", "descartado", "ultima_vista", "precio_anterior"} <= columnas_despues


class TestUltimaVistaYPrecioAnterior:
    def test_insertar_rellena_ultima_vista(self, con, piso):
        assert piso["ultima_vista"] == piso["fecha_recogida"]

    def test_volver_a_verlo_refresca_ultima_vista(self, con, piso_factory):
        db.upsert_piso(con, piso_factory())
        con.execute("UPDATE pisos SET ultima_vista = '2026-01-01T00:00:00+00:00'")
        con.commit()
        db.upsert_piso(con, piso_factory())  # sin cambios en los datos
        assert con.execute("SELECT ultima_vista FROM pisos").fetchone()["ultima_vista"] > "2026-01-01"

    def test_cambio_de_precio_guarda_el_anterior(self, con, piso_factory):
        db.upsert_piso(con, piso_factory(precio=900))
        db.upsert_piso(con, piso_factory(precio=850))
        fila = con.execute("SELECT precio, precio_anterior FROM pisos").fetchone()
        assert (fila["precio"], fila["precio_anterior"]) == (850, 900)

    def test_mismo_precio_no_toca_el_anterior(self, con, piso_factory):
        db.upsert_piso(con, piso_factory(precio=900))
        db.upsert_piso(con, piso_factory(precio=850))
        db.upsert_piso(con, piso_factory(precio=850))
        assert con.execute("SELECT precio_anterior FROM pisos").fetchone()["precio_anterior"] == 900


class TestRecogidasYDisponibilidad:
    def test_estado_portales(self, con):
        db.registrar_recogida(con, {"fotocasa": 10, "habitaclia": 0}, fecha="2026-10-25T10:00:00+00:00")
        db.registrar_recogida(con, {"fotocasa": 8, "habitaclia": 0}, fecha="2026-10-25T10:30:00+00:00")
        estado = {e["portal"]: e for e in db.estado_portales(con)}
        assert estado["fotocasa"]["anuncios"] == 8
        assert estado["fotocasa"]["ultima_ok"] == "2026-10-25T10:30:00+00:00"
        assert estado["habitaclia"]["anuncios"] == 0
        assert estado["habitaclia"]["ultima_ok"] is None

    def test_portal_sin_recogidas_buenas_no_tiene_corte(self, con):
        db.registrar_recogida(con, {"fotocasa": 10, "habitaclia": 0}, fecha="2026-10-25T10:00:00+00:00")
        assert set(db.cortes_disponibilidad(con)) == {"fotocasa"}

    def test_solo_disponibles(self, con, piso_factory):
        db.upsert_piso(con, piso_factory(url="visto"))
        db.upsert_piso(con, piso_factory(url="viejo"))
        db.upsert_piso(con, piso_factory(url="otro-portal", portal="habitaclia"))
        con.execute("UPDATE pisos SET ultima_vista = '2026-01-01T00:00:00+00:00' WHERE url IN ('viejo', 'otro-portal')")
        con.commit()
        db.registrar_recogida(con, {"fotocasa": 1, "habitaclia": 0})
        urls = {f["url"] for f in db.listar_pisos(con, solo_disponibles=True)}
        # habitaclia no tiene recogidas buenas: de sus anuncios no se sabe nada
        assert urls == {"visto", "otro-portal"}

    def test_ultima_vista_nula_cuenta_como_no_disponible(self, con, piso_factory):
        db.upsert_piso(con, piso_factory())
        con.execute("UPDATE pisos SET ultima_vista = NULL")
        con.commit()
        db.registrar_recogida(con, {"fotocasa": 1})
        assert db.listar_pisos(con, solo_disponibles=True) == []

    def test_margen_absorbe_una_corrida_corta(self, con, piso_factory):
        db.upsert_piso(con, piso_factory())
        hace_una_hora = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
        con.execute("UPDATE pisos SET ultima_vista = ?", (hace_una_hora,))
        con.commit()
        db.registrar_recogida(con, {"fotocasa": 1})
        assert len(db.listar_pisos(con, solo_disponibles=True)) == 1


class TestDescartar:
    def test_descartar_quita_de_favoritos(self, con, piso):
        db.marcar_favorito(con, piso["id"], True)
        db.marcar_descartado(con, piso["id"], True)
        fila = con.execute("SELECT descartado, favorito FROM pisos").fetchone()
        assert (fila["descartado"], fila["favorito"]) == (1, 0)

    def test_descartados_fuera_del_listado_normal(self, con, piso):
        db.marcar_descartado(con, piso["id"], True)
        assert db.listar_pisos(con) == []
        assert db.contar_pisos(con) == 0
        assert len(db.listar_pisos(con, descartados=True)) == 1

    def test_recuperar(self, con, piso):
        db.marcar_descartado(con, piso["id"], True)
        db.marcar_descartado(con, piso["id"], False)
        assert len(db.listar_pisos(con)) == 1


class TestFiltrosNuevos:
    def test_busqueda_mira_titulo_zona_ciudad_y_descripcion(self, con, piso_factory):
        db.upsert_piso(con, piso_factory(url="a", titulo="Nada", descripcion="con piscina comunitaria"))
        db.upsert_piso(con, piso_factory(url="b", titulo="Nada", zona="Piscinas"))
        db.upsert_piso(con, piso_factory(url="c", titulo="Nada", descripcion="sin más"))
        assert {f["url"] for f in db.listar_pisos(con, q="piscina")} == {"a", "b"}

    def test_novedades(self, con, piso_factory):
        db.upsert_piso(con, piso_factory(url="nuevo"))
        db.upsert_piso(con, piso_factory(url="viejo", fecha_recogida="2026-01-01T00:00:00+00:00"))
        assert [f["url"] for f in db.listar_pisos(con, solo_novedades=True)] == ["nuevo"]

    def test_bajadas_no_incluye_subidas(self, con, piso_factory):
        db.upsert_piso(con, piso_factory(url="baja", precio=900))
        db.upsert_piso(con, piso_factory(url="baja", precio=800))
        db.upsert_piso(con, piso_factory(url="sube", precio=800))
        db.upsert_piso(con, piso_factory(url="sube", precio=900))
        assert [f["url"] for f in db.listar_pisos(con, solo_bajadas=True)] == ["baja"]

    def test_tipos_distintos(self, con, piso_factory):
        db.upsert_piso(con, piso_factory(url="a", tipo="Piso"))
        db.upsert_piso(con, piso_factory(url="b", tipo="Casa"))
        db.upsert_piso(con, piso_factory(url="c", tipo=None))
        assert db.tipos_distintos(con) == ["Casa", "Piso"]


class TestTrasladarUrl:
    URL_VIEJA = "https://www.habitaclia.com/alquiler-piso-de_fantastico-logrono-i30265000000466.htm?hab=3&lo=55|0|0|0"
    URL_NUEVA = "https://www.habitaclia.com/alquiler/planta-intermedia/centro/logrono/62a6d768/d"

    def test_conserva_favorito_visto_y_notificado(self, con, piso_factory):
        db.upsert_piso(con, piso_factory(portal="habitaclia", url=self.URL_VIEJA, precio=900))
        viejo = con.execute("SELECT id FROM pisos").fetchone()["id"]
        db.marcar_favorito(con, viejo, True)
        db.marcar_consultado(con, viejo, True)
        db.marcar_notificado(con, viejo)

        nuevo, cambios, favorito = db.upsert_piso(con, {
            **piso_factory(portal="habitaclia", url=self.URL_NUEVA, precio=850),
            "legacy_url_like": "%-i30265000000466.htm%",
        })
        filas = con.execute("SELECT * FROM pisos").fetchall()
        assert len(filas) == 1
        fila = filas[0]
        assert fila["url"] == self.URL_NUEVA
        assert fila["id"] == db.make_id("habitaclia", self.URL_NUEVA)
        assert (fila["favorito"], fila["consultado"], fila["notificado"]) == (1, 1, 1)
        assert (nuevo, favorito) == (False, True)
        assert cambios == {"precio": (900, 850)}

    def test_sin_coincidencia_se_inserta_como_nuevo(self, con, piso_factory):
        nuevo, _, _ = db.upsert_piso(con, {
            **piso_factory(portal="habitaclia", url=self.URL_NUEVA), "legacy_url_like": "%-i999.htm%",
        })
        assert nuevo is True
        assert con.execute("SELECT COUNT(*) c FROM pisos").fetchone()["c"] == 1

    def test_no_traslada_anuncios_de_otro_portal(self, con, piso_factory):
        db.upsert_piso(con, piso_factory(portal="fotocasa", url=self.URL_VIEJA))
        db.upsert_piso(con, {
            **piso_factory(portal="habitaclia", url=self.URL_NUEVA), "legacy_url_like": "%-i30265000000466.htm%",
        })
        assert con.execute("SELECT COUNT(*) c FROM pisos").fetchone()["c"] == 2

    def test_si_la_url_nueva_ya_existe_no_toca_la_vieja(self, con, piso_factory):
        db.upsert_piso(con, piso_factory(portal="habitaclia", url=self.URL_VIEJA))
        db.upsert_piso(con, piso_factory(portal="habitaclia", url=self.URL_NUEVA))
        db.upsert_piso(con, {
            **piso_factory(portal="habitaclia", url=self.URL_NUEVA), "legacy_url_like": "%-i30265000000466.htm%",
        })
        urls = {f["url"] for f in con.execute("SELECT url FROM pisos")}
        assert urls == {self.URL_VIEJA, self.URL_NUEVA}


class TestPurgarRetirados:
    def _hace(self, **kw):
        return (datetime.now(timezone.utc) - timedelta(**kw)).isoformat()

    def _piso(self, con, piso_factory, url, ultima_vista, portal="fotocasa", favorito=False):
        db.upsert_piso(con, piso_factory(url=url, portal=portal))
        con.execute("UPDATE pisos SET ultima_vista = ?, favorito = ? WHERE url = ?", (ultima_vista, int(favorito), url))
        con.commit()

    def _urls(self, con):
        return {f["url"] for f in con.execute("SELECT url FROM pisos")}

    def test_borra_los_retirados_hace_mas_de_una_semana(self, con, piso_factory):
        db.registrar_recogida(con, {"fotocasa": 5}, fecha=self._hace(days=10))
        self._piso(con, piso_factory, "viejo", self._hace(days=8))
        self._piso(con, piso_factory, "reciente", self._hace(days=3))
        self._piso(con, piso_factory, "activo", self._hace(minutes=1))
        db.registrar_recogida(con, {"fotocasa": 5})
        assert db.purgar_retirados(con) == 1
        assert self._urls(con) == {"reciente", "activo"}

    def test_nunca_borra_favoritos(self, con, piso_factory):
        db.registrar_recogida(con, {"fotocasa": 5}, fecha=self._hace(days=30))
        self._piso(con, piso_factory, "fav", self._hace(days=20), favorito=True)
        db.registrar_recogida(con, {"fotocasa": 5})
        assert db.purgar_retirados(con) == 0
        assert self._urls(con) == {"fav"}

    def test_portal_roto_no_borra_nada(self, con, piso_factory):
        self._piso(con, piso_factory, "a", self._hace(days=40), portal="habitaclia")
        db.registrar_recogida(con, {"habitaclia": 0})
        assert db.purgar_retirados(con) == 0

    def test_portal_que_se_rompe_no_borra_lo_ultimo_que_vio(self, con, piso_factory):
        # Última recogida buena hace 10 días y desde entonces 0: lo que se vio
        # en esa recogida sigue contando como disponible.
        db.registrar_recogida(con, {"fotocasa": 5}, fecha=self._hace(days=10))
        self._piso(con, piso_factory, "a", self._hace(days=10))
        db.registrar_recogida(con, {"fotocasa": 0})
        assert db.purgar_retirados(con) == 0

    def test_sin_ultima_vista_cuenta_desde_la_primera_recogida(self, con, piso_factory):
        self._piso(con, piso_factory, "sin-fecha", None)
        db.registrar_recogida(con, {"fotocasa": 5}, fecha=self._hace(days=2))
        db.registrar_recogida(con, {"fotocasa": 5})
        assert db.purgar_retirados(con) == 0  # solo 2 días desde que se empezó a vigilar
        assert db.purgar_retirados(con, ahora=datetime.now(timezone.utc) + timedelta(days=6)) == 1

    def test_limpia_el_historial_de_recogidas_antiguo(self, con):
        db.registrar_recogida(con, {"fotocasa": 5}, fecha=self._hace(days=40))
        db.registrar_recogida(con, {"fotocasa": 5})
        db.purgar_retirados(con)
        assert con.execute("SELECT COUNT(*) c FROM recogidas").fetchone()["c"] == 1

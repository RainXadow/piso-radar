"""
Fixtures compartidas. Todo mockeado: ninguna llamada real a los portales, a
ntfy ni a la red -- tests rápidos, gratis, sin tocar piso-radar.db real.
"""
import sys
from pathlib import Path

import pytest
import requests

sys.path.insert(0, str(Path(__file__).parent.parent))

import db  # noqa: E402


@pytest.fixture(autouse=True)
def sin_red(monkeypatch):
    """Ningún test sale a internet: un colector o un aviso que se haya
    quedado sin simular falla como un corte de red (que todos manejan) en
    vez de ir a los portales o a ntfy de verdad. Los tests que necesitan una
    respuesta concreta parchean requests.get/post por encima de esto."""
    def cortado(*a, **k):
        raise requests.ConnectionError("red desactivada en los tests")
    monkeypatch.setattr(requests, "get", cortado)
    monkeypatch.setattr(requests, "post", cortado)


def entrar(cliente, perfil="la-rioja"):
    """Sesión iniciada en ese perfil, como tras pasar por /login."""
    with cliente.session_transaction() as sesion:
        sesion["perfil"] = perfil


@pytest.fixture
def con(tmp_path, monkeypatch):
    """Conexión a una BD SQLite temporal y vacía (esquema real aplicado)."""
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "test.db")
    connection = db.connect()
    yield connection
    connection.close()


def make_piso(portal="fotocasa", titulo="Piso en Centro", precio=800, m2=70,
              habitaciones=2, tipo="Piso", zona="Centro", ciudad="Logroño",
              url="https://example.com/piso/1", imagen="https://example.com/foto.jpg",
              descripcion="Bonito piso.", fecha_publicacion=None, fecha_recogida=None, extra=None):
    piso = {
        "portal": portal, "titulo": titulo, "precio": precio, "m2": m2,
        "habitaciones": habitaciones, "tipo": tipo, "zona": zona, "ciudad": ciudad,
        "url": url, "imagen": imagen, "descripcion": descripcion, "fecha_publicacion": fecha_publicacion,
    }
    if fecha_recogida:
        piso["fecha_recogida"] = fecha_recogida
    if extra:
        piso["extra"] = extra
    return piso


@pytest.fixture
def piso_factory():
    return make_piso


@pytest.fixture
def piso(con, piso_factory):
    """Un piso de prueba ya insertado en la BD temporal. Devuelve la Row."""
    p = piso_factory()
    db.upsert_piso(con, p)
    return con.execute("SELECT * FROM pisos WHERE portal = ? AND url = ?", (p["portal"], p["url"])).fetchone()

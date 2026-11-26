CREATE TABLE IF NOT EXISTS pisos (
    id                 TEXT PRIMARY KEY,
    portal             TEXT NOT NULL,
    titulo             TEXT NOT NULL,
    precio             INTEGER,
    m2                 INTEGER,
    habitaciones       INTEGER,
    tipo               TEXT,
    zona               TEXT,
    ciudad             TEXT,
    url                TEXT NOT NULL,
    imagen             TEXT,
    descripcion        TEXT,
    fecha_publicacion  TEXT,
    fecha_recogida     TEXT NOT NULL,
    notificado         INTEGER NOT NULL DEFAULT 0,
    consultado         INTEGER NOT NULL DEFAULT 0,
    favorito           INTEGER NOT NULL DEFAULT 0,
    descartado         INTEGER NOT NULL DEFAULT 0,
    ultima_vista       TEXT,
    precio_anterior    INTEGER,
    perfil             TEXT NOT NULL DEFAULT 'la-rioja',
    extra              TEXT
);

CREATE INDEX IF NOT EXISTS idx_pisos_portal ON pisos(portal);
CREATE INDEX IF NOT EXISTS idx_pisos_notificado ON pisos(notificado);
CREATE INDEX IF NOT EXISTS idx_pisos_favorito ON pisos(favorito);

-- Una fila por portal y corrida de main.py: cuántos anuncios devolvió. Sirve
-- para saber si un anuncio sigue publicado (se vio en la última recogida
-- buena de su portal) y para avisar en la web si un portal deja de responder.
CREATE TABLE IF NOT EXISTS recogidas (
    fecha     TEXT NOT NULL,
    portal    TEXT NOT NULL,
    anuncios  INTEGER NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_recogidas_portal_fecha ON recogidas(portal, fecha);

-- Credenciales de cada perfil (ver config.PERFILES). Solo el hash de la
-- contraseña (werkzeug, pbkdf2/scrypt), nunca la contraseña.
CREATE TABLE IF NOT EXISTS perfiles (
    id             TEXT PRIMARY KEY,
    clave_hash     TEXT NOT NULL,
    actualizado    TEXT NOT NULL
);

#!/usr/bin/env python3
"""
Contraseñas de los perfiles de la web (config.PERFILES).

    ./venv/bin/python3 perfiles.py lista
    ./venv/bin/python3 perfiles.py clave irlanda      # la pide sin mostrarla
    ./venv/bin/python3 perfiles.py clave irlanda --generar

En la BD solo se guarda el hash. Cambiar la contraseña no cierra las
sesiones ya abiertas (duran un año); para echar a todo el mundo, cambiar
FLASK_SECRET_KEY en .env y reiniciar piso-radar-web.
"""
import argparse
import getpass
import secrets
import sys

import db
from config import PERFILES

LONGITUD_MINIMA = 8


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="orden", required=True)
    sub.add_parser("lista", help="perfiles y si tienen contraseña")
    clave = sub.add_parser("clave", help="fijar o cambiar la contraseña de un perfil")
    clave.add_argument("perfil", choices=sorted(PERFILES))
    clave.add_argument("--generar", action="store_true", help="generar una aleatoria y mostrarla una vez")
    args = parser.parse_args(argv)

    con = db.connect()
    try:
        if args.orden == "lista":
            con_clave = {f["id"]: f["actualizado"] for f in con.execute("SELECT id, actualizado FROM perfiles")}
            for perfil, datos in PERFILES.items():
                estado = f"contraseña fijada {con_clave[perfil][:10]}" if perfil in con_clave else "SIN contraseña"
                print(f"{perfil:10} {datos['nombre']:10} {estado}")
            return 0

        if args.generar:
            nueva = "-".join(secrets.token_urlsafe(4) for _ in range(3))
        else:
            nueva = getpass.getpass(f"Nueva contraseña para {args.perfil}: ")
            if nueva != getpass.getpass("Repítela: "):
                print("No coinciden.", file=sys.stderr)
                return 1
        if len(nueva) < LONGITUD_MINIMA:
            print(f"Mínimo {LONGITUD_MINIMA} caracteres.", file=sys.stderr)
            return 1
        db.fijar_clave(con, args.perfil, nueva)
        print(f"Contraseña de {args.perfil} actualizada." + (f" Es: {nueva}" if args.generar else ""))
        return 0
    finally:
        con.close()


if __name__ == "__main__":
    sys.exit(main())

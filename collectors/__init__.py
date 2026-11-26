import random

USER_AGENT = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"


def get_headers():
    return {
        "User-Agent": USER_AGENT,
        "Accept-Language": "es-ES,es;q=0.9",
    }


def jitter(base_delay, factor=0.3):
    """Pausa entre peticiones: base_delay +/- factor (30 % por defecto)."""
    return random.uniform(base_delay * (1 - factor), base_delay * (1 + factor))


_PALABRAS_CASA = ("chalet", "casa", "adosad", "unifamiliar", "pareado", "torre", "villa")


def clasificar_tipo(titulo):
    """"Piso" o "Casa" a partir del título -- ninguno de los tres portales
    da un campo de tipo consistente entre sí (Fotocasa sí, en su JSON, pero
    Habitaclia y pisos.com no lo exponen igual de fiable), así que se usa el
    mismo criterio simple para los tres en vez de tres lógicas distintas."""
    titulo = (titulo or "").lower()
    return "Casa" if any(p in titulo for p in _PALABRAS_CASA) else "Piso"

"""
Filtro puro (sin IA, coste cero): un piso pasa si tiene el mínimo de
habitaciones de su perfil o más. Sin precio máximo ni m² mínimo (decisión
explícita del usuario 2026-09-03 para La Rioja). Si el portal no dio el número
de habitaciones, se deja pasar ese piso -- mejor un falso positivo que se
revisa a mano un segundo, que perder uno real por un dato que faltaba en la
vista de resultados.
"""
from config import MIN_HABITACIONES


def cumple_filtros(piso, min_habitaciones=MIN_HABITACIONES):
    habitaciones = piso.get("habitaciones")
    if habitaciones is not None and habitaciones < min_habitaciones:
        return False

    return True

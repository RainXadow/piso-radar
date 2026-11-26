from filtro import cumple_filtros
from config import MIN_HABITACIONES


def base(**overrides):
    piso = {"habitaciones": MIN_HABITACIONES}
    piso.update(overrides)
    return piso


class TestHabitaciones:
    def test_habitaciones_igual_al_minimo_pasa(self):
        assert cumple_filtros(base(habitaciones=MIN_HABITACIONES)) is True

    def test_habitaciones_por_encima_del_minimo_pasa(self):
        assert cumple_filtros(base(habitaciones=MIN_HABITACIONES + 5)) is True

    def test_habitaciones_por_debajo_del_minimo_no_pasa(self):
        assert cumple_filtros(base(habitaciones=MIN_HABITACIONES - 1)) is False

    def test_habitaciones_none_pasa_el_criterio(self):
        # Dato que el portal no dio -- mejor falso positivo que perder el piso.
        assert cumple_filtros(base(habitaciones=None)) is True

    def test_cero_habitaciones_no_pasa(self):
        assert cumple_filtros(base(habitaciones=0)) is False


class TestSinFiltroDePrecioNiM2:
    def test_precio_altisimo_pasa_igualmente(self):
        # Decisión explícita del usuario: sin tope de precio.
        assert cumple_filtros(base(precio=999999)) is True

    def test_m2_pequeno_pasa_igualmente(self):
        assert cumple_filtros(base(m2=1)) is True

    def test_dict_sin_habitaciones_pasa(self):
        assert cumple_filtros({"precio": 100}) is True

    def test_dict_vacio_pasa(self):
        assert cumple_filtros({}) is True

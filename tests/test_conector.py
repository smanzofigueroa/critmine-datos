"""Pruebas sin red: usan respuestas reales guardadas de las APIs (tests/muestras)."""
import json
import sys
from pathlib import Path

RAIZ = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(RAIZ))

import conector as c  # noqa: E402

MUESTRAS = RAIZ / "tests" / "muestras"


def cargar(nombre):
    return json.loads((MUESTRAS / nombre).read_text(encoding="utf-8"))


def test_periodos_bcrp():
    assert c.parsear_periodo_bcrp("05.Oct.26") == "2026-10-05"
    assert c.parsear_periodo_bcrp("Ene.2024") == "2024-01"
    assert c.parsear_periodo_bcrp("Set.2025") == "2025-09"


def test_bcrp_omite_sin_dato():
    filas = c.parsear_bcrp(cargar("bcrp_diario.json"), ["PD04701XD", "PD04702XD", "PD04704XD"])
    assert all(f["valor"] > 0 for f in filas)
    assert not any(f["fecha"] == "2026-10-07" for f in filas)
    cobre = [f for f in filas if f["codigo"] == "PD04701XD"]
    assert cobre[-1] == {"codigo": "PD04701XD", "fecha": "2026-10-06", "valor": 657.935739205431}


def test_comtrade_solo_total_mundo():
    filas = c.parsear_comtrade(cargar("comtrade_chile.json"))
    assert len(filas) == 8
    assert {f["anio"] for f in filas} == {2021, 2022, 2023, 2024}


def test_conversion_cobre():
    # 657,94 cUS$/lb ≈ 14.505 US$/t
    assert round(c.cobre_usd_t(657.935739205431)) == 14505


def test_pipeline_completo(tmp_path):
    con = c.conectar_db(tmp_path / "t.db")
    for clave_json, codigos in (("bcrp_diario.json", ["PD04701XD", "PD04702XD", "PD04704XD"]),
                                ("bcrp_mensual.json", ["PN01652XM", "PN01655XM", "PN01654XM"])):
        for f in c.parsear_bcrp(cargar(clave_json), codigos):
            clave, _, unidad, _ = c.SERIES_BCRP[f["codigo"]]
            con.execute("INSERT OR REPLACE INTO precios VALUES (?,?,?,?,?,?)",
                        (clave, f["fecha"], f["valor"], unidad, "BCRP", c.ahora()))
    for f in c.parsear_comtrade(cargar("comtrade_chile.json")):
        con.execute("INSERT OR REPLACE INTO exportaciones VALUES (?,?,?,?,?,?,?)",
                    (f["pais"], f["anio"], f["hs"], f["valor_usd"], f["peso_kg"], "UN Comtrade", c.ahora()))
    datos = c.exportar_json(con, tmp_path / "d.json")
    assert datos["cobre"]["ultimo"]["fecha"] == "2026-10-06"
    assert len(datos["cobre"]["mensual"]) == 33
    carb_2024 = [e for e in datos["litio_exportaciones"] if e["anio"] == 2024 and e["hs"] == "283691"][0]
    assert carb_2024["precio_unitario_usd_t"] == 9952


def test_bcch_parser():
    r = {"Codigo": 0, "Descripcion": "Success", "Series": {"seriesId": "F073.TCO.PRE.Z.D", "Obs": [
        {"indexDateString": "01-10-2024", "value": "897.68", "statusCode": "OK"},
        {"indexDateString": "02-10-2024", "value": "NaN", "statusCode": "ND"}]}}
    assert c.parsear_bcch(r) == [{"fecha": "2024-10-01", "valor": 897.68}]


def test_bcch_sin_token_no_falla(tmp_path, monkeypatch):
    monkeypatch.delenv("BCCH_TOKEN", raising=False)
    assert c.descargar_bcch(c.conectar_db(tmp_path / "t.db")) == 0

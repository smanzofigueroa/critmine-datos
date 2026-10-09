"""
Carga las respuestas reales guardadas en tests/muestras a la base y exporta datos.json.
Sirve para ver el panel funcionando sin conexión, o si una API está caída.
Uso: python cargar_muestras.py
"""
import json
from pathlib import Path

import conector as c

M = Path(__file__).resolve().parent / "tests" / "muestras"

con = c.conectar_db()
for archivo, codigos in (("bcrp_diario.json", ["PD04701XD", "PD04702XD", "PD04704XD"]),
                         ("bcrp_mensual.json", ["PN01652XM", "PN01655XM", "PN01654XM"])):
    for f in c.parsear_bcrp(json.loads((M / archivo).read_text(encoding="utf-8")), codigos):
        clave, _, unidad, _ = c.SERIES_BCRP[f["codigo"]]
        con.execute("INSERT OR REPLACE INTO precios VALUES (?,?,?,?,?,?)",
                    (clave, f["fecha"], f["valor"], unidad, f"BCRP {f['codigo']}", c.ahora()))
for f in c.parsear_comtrade(json.loads((M / "comtrade_chile.json").read_text(encoding="utf-8"))):
    con.execute("INSERT OR REPLACE INTO exportaciones VALUES (?,?,?,?,?,?,?)",
                (f["pais"], f["anio"], f["hs"], f["valor_usd"], f["peso_kg"], "UN Comtrade", c.ahora()))
con.commit()
c.exportar_json(con)
print("Listo:", c.JSON_PATH)

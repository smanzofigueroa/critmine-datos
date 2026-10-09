"""
Genera panel.html (autocontenido, con los datos incrustados) a partir de datos/datos.json.

Uso:
    python generar_panel.py               # crea panel.html (documento completo para el sitio)
    python generar_panel.py --fragmento   # versión sin <!doctype>, para publicarla como artifact
"""
import argparse
import json
from pathlib import Path

BASE = Path(__file__).resolve().parent


def generar(fragmento: bool = False, salida: Path | None = None) -> Path:
    datos = json.loads((BASE / "datos" / "datos.json").read_text(encoding="utf-8"))
    plantilla = (BASE / "plantilla_panel.html").read_text(encoding="utf-8")
    incrustado = json.dumps(datos, ensure_ascii=False).replace("</", "<\\/")
    html = plantilla.replace("/*DATOS*/null/*FIN*/", incrustado)
    if not fragmento:
        html = '<!doctype html>\n<html lang="es">\n<head>\n<meta charset="utf-8">\n' + html.replace(
            "<div class=\"wrap\">", "</head>\n<body>\n<div class=\"wrap\">", 1) + "\n</body>\n</html>\n"
    salida = salida or BASE / ("panel_fragmento.html" if fragmento else "panel.html")
    salida.write_text(html, encoding="utf-8")
    return salida


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--fragmento", action="store_true")
    a = ap.parse_args()
    print("Generado:", generar(a.fragmento))

"""
CritMine · Conector de datos públicos (Etapa 0)

Descarga datos de fuentes públicas con API, los guarda en una base SQLite
y exporta un archivo datos.json que alimenta el panel de la portada.

Fuentes:
  1. Banco Central de Reserva del Perú (BCRP) — API de series estadísticas
     - Cobre LME (diario y promedio mensual), plata y oro.
     - Sin registro ni clave. Docs: https://estadisticas.bcrp.gob.pe/estadisticas/series/ayuda/api
  3. Banco Central de Chile (BDE) — series chilenas (dólar observado, cobre, exportaciones).
     - Requiere token gratuito en la variable de entorno BCCH_TOKEN.
     - Las series se eligen en series_bcch.json; para descubrir códigos:
       python conector.py --buscar-bcch cobre
  2. UN Comtrade — exportaciones anuales de litio de Chile
     - HS 283691: carbonato de litio · HS 282520: óxido e hidróxido de litio.
     - Endpoint público "preview" sin clave (máx. 500 registros por consulta).
       Con una clave gratuita de comtradeplus.un.org se usa el endpoint completo.

Uso:
    python conector.py                 # descarga todo y exporta datos/datos.json
    python conector.py --solo bcrp     # solo precios
    python conector.py --solo comtrade # solo exportaciones
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sqlite3
import sys
import time
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import requests

BASE = Path(__file__).resolve().parent
DIR_DATOS = BASE / "datos"
DB_PATH = DIR_DATOS / "critmine.db"
JSON_PATH = DIR_DATOS / "datos.json"

log = logging.getLogger("critmine")

# ---------------------------------------------------------------------------
# Configuración de series
# ---------------------------------------------------------------------------

BCRP_URL = "https://estadisticas.bcrp.gob.pe/estadisticas/series/api/{codigos}/json/{desde}/{hasta}"

# código BCRP -> (clave interna, nombre, unidad, frecuencia)
SERIES_BCRP = {
    "PD04701XD": ("cobre_diario", "Cobre LME", "cUS$/lb", "diaria"),
    "PD04702XD": ("plata_diario", "Plata (H. Harman)", "US$/oz troy", "diaria"),
    "PD04704XD": ("oro_diario", "Oro (Londres)", "US$/oz troy", "diaria"),
    "PN01652XM": ("cobre_mensual", "Cobre LME, promedio mensual", "cUS$/lb", "mensual"),
    "PN01655XM": ("plata_mensual", "Plata, promedio mensual", "US$/oz troy", "mensual"),
    "PN01654XM": ("oro_mensual", "Oro LME, promedio mensual", "US$/oz troy", "mensual"),
}

COMTRADE_PREVIEW = "https://comtradeapi.un.org/public/v1/preview/C/A/HS"
COMTRADE_FULL = "https://comtradeapi.un.org/data/v1/get/C/A/HS"
PAISES_COMTRADE = {"152": "Chile"}  # se pueden sumar: 32 Argentina, 76 Brasil, 604 Perú
PRODUCTOS_HS = {
    "283691": "Carbonato de litio",
    "282520": "Óxido e hidróxido de litio",
}

# Banco Central de Chile (BDE). Requiere token gratuito: variable de entorno BCCH_TOKEN.
BCCH_URL = "https://si3.bcentral.cl/SieteRestWS/SieteRestWS.ashx"
SERIES_BCCH_PATH = BASE / "series_bcch.json"   # lista editable de series a descargar

LIBRAS_POR_TONELADA = 2204.62262

MESES = {
    "ene": 1, "feb": 2, "mar": 3, "abr": 4, "may": 5, "jun": 6,
    "jul": 7, "ago": 8, "sep": 9, "set": 9, "oct": 10, "nov": 11, "dic": 12,
}

# ---------------------------------------------------------------------------
# Utilidades HTTP
# ---------------------------------------------------------------------------

SESION = requests.Session()
SESION.headers.update({"User-Agent": "CritMine-conector/1.0 (+https://critmine.com)"})


def obtener_json(url: str, params: dict | None = None, intentos: int = 4) -> dict:
    """GET con reintentos y espera exponencial (las APIs públicas a veces fallan)."""
    ultimo_error: Exception | None = None
    for i in range(intentos):
        try:
            r = SESION.get(url, params=params, timeout=40)
            if r.status_code == 429:
                raise requests.HTTPError("429 demasiadas solicitudes")
            r.raise_for_status()
            return r.json()
        except (requests.RequestException, ValueError) as e:
            ultimo_error = e
            espera = 2 ** i * 3
            log.warning("Fallo %s (intento %d/%d): %s. Reintento en %ds", url, i + 1, intentos, e, espera)
            time.sleep(espera)
    raise RuntimeError(f"No se pudo obtener {url}: {ultimo_error}")


# ---------------------------------------------------------------------------
# Parsers (separados para poder probarlos sin red)
# ---------------------------------------------------------------------------

def parsear_periodo_bcrp(nombre: str) -> str:
    """Convierte '05.Oct.26' -> '2026-10-05' y 'Ene.2024' -> '2024-01'."""
    partes = nombre.strip().replace(" ", "").split(".")
    if len(partes) == 3:  # diario: dd.Mmm.aa
        dia, mes, anio = partes
        anio_n = int(anio) + (2000 if len(anio) == 2 else 0)
        return date(anio_n, MESES[mes[:3].lower()], int(dia)).isoformat()
    if len(partes) == 2:  # mensual: Mmm.aaaa
        mes, anio = partes
        return f"{int(anio):04d}-{MESES[mes[:3].lower()]:02d}"
    raise ValueError(f"Periodo BCRP no reconocido: {nombre!r}")


def parsear_bcrp(respuesta: dict, codigos: list[str]) -> list[dict]:
    """Devuelve filas {codigo, fecha, valor}; omite 'n.d.' (sin dato)."""
    filas = []
    for periodo in respuesta.get("periods", []):
        fecha = parsear_periodo_bcrp(periodo["name"])
        for codigo, valor in zip(codigos, periodo.get("values", [])):
            if valor in (None, "", "n.d.", "n.d"):
                continue
            filas.append({"codigo": codigo, "fecha": fecha, "valor": float(valor)})
    return filas


def parsear_comtrade(respuesta: dict) -> list[dict]:
    filas = []
    for reg in respuesta.get("data", []) or []:
        if str(reg.get("partnerCode")) != "0":  # 0 = total mundo
            continue
        valor = reg.get("primaryValue") or reg.get("fobvalue")
        peso = reg.get("netWgt")
        if valor is None:
            continue
        filas.append({
            "pais": str(reg["reporterCode"]),
            "anio": int(reg["refYear"]),
            "hs": str(reg["cmdCode"]),
            "valor_usd": float(valor),
            "peso_kg": float(peso) if peso else None,
        })
    return filas


def parsear_bcch(respuesta: dict) -> list[dict]:
    """GetSeries del Banco Central de Chile -> filas {fecha, valor}; omite 'NaN'/ND."""
    if str(respuesta.get("Codigo")) not in ("0", "None"):
        raise RuntimeError(f"BCCh: {respuesta.get('Descripcion')}")
    filas = []
    for o in (respuesta.get("Series") or {}).get("Obs") or []:
        if o.get("statusCode") != "OK" or o.get("value") in (None, "", "NaN"):
            continue
        d, m, a = o["indexDateString"].split("-")
        filas.append({"fecha": f"{a}-{m}-{d}", "valor": float(o["value"])})
    return filas


# ---------------------------------------------------------------------------
# Base de datos
# ---------------------------------------------------------------------------

ESQUEMA = """
CREATE TABLE IF NOT EXISTS precios (
    serie TEXT NOT NULL,
    fecha TEXT NOT NULL,
    valor REAL NOT NULL,
    unidad TEXT NOT NULL,
    fuente TEXT NOT NULL,
    actualizado TEXT NOT NULL,
    PRIMARY KEY (serie, fecha)
);
CREATE TABLE IF NOT EXISTS exportaciones (
    pais TEXT NOT NULL,
    anio INTEGER NOT NULL,
    hs TEXT NOT NULL,
    valor_usd REAL NOT NULL,
    peso_kg REAL,
    fuente TEXT NOT NULL,
    actualizado TEXT NOT NULL,
    PRIMARY KEY (pais, anio, hs)
);
CREATE TABLE IF NOT EXISTS ejecuciones (
    inicio TEXT NOT NULL,
    fuente TEXT NOT NULL,
    filas INTEGER NOT NULL,
    estado TEXT NOT NULL,
    detalle TEXT
);
"""


def conectar_db(ruta: Path = DB_PATH) -> sqlite3.Connection:
    ruta.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(ruta)
    con.executescript(ESQUEMA)
    return con


def ahora() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def registrar(con, fuente, filas, estado, detalle=""):
    con.execute("INSERT INTO ejecuciones VALUES (?,?,?,?,?)", (ahora(), fuente, filas, estado, detalle))
    con.commit()


# ---------------------------------------------------------------------------
# Descargas
# ---------------------------------------------------------------------------

def descargar_bcrp(con: sqlite3.Connection, dias: int = 120, meses: int = 36) -> int:
    hoy = date.today()
    diarias = [c for c, s in SERIES_BCRP.items() if s[3] == "diaria"]
    mensuales = [c for c, s in SERIES_BCRP.items() if s[3] == "mensual"]
    total = 0
    consultas = [
        (diarias, (hoy - timedelta(days=dias)).isoformat(), hoy.isoformat()),
        (mensuales, f"{(hoy.year * 12 + hoy.month - 1 - meses) // 12}-{(hoy.year * 12 + hoy.month - 1 - meses) % 12 + 1}",
         f"{hoy.year}-{hoy.month}"),
    ]
    for codigos, desde, hasta in [([c], d, h) for cs, d, h in consultas for c in cs]:
        url = BCRP_URL.format(codigos="-".join(codigos), desde=desde, hasta=hasta)
        filas = parsear_bcrp(obtener_json(url), codigos)
        for f in filas:
            clave, _, unidad, _ = SERIES_BCRP[f["codigo"]]
            con.execute(
                "INSERT OR REPLACE INTO precios VALUES (?,?,?,?,?,?)",
                (clave, f["fecha"], f["valor"], unidad, f"BCRP {f['codigo']}", ahora()),
            )
        total += len(filas)
        time.sleep(1)
    con.commit()
    return total


def descargar_comtrade(con: sqlite3.Connection, anios: list[int] | None = None) -> int:
    anios = anios or list(range(date.today().year - 6, date.today().year))
    clave = os.environ.get("COMTRADE_API_KEY")
    url = COMTRADE_FULL if clave else COMTRADE_PREVIEW
    headers = {"Ocp-Apim-Subscription-Key": clave} if clave else None
    total = 0
    for pais in PAISES_COMTRADE:
        for anio in anios:  # un año por consulta: el endpoint público es más estable así
            params = {
                "reporterCode": pais, "period": str(anio), "flowCode": "X",
                "partnerCode": "0", "cmdCode": ",".join(PRODUCTOS_HS), "includeDesc": "true",
            }
            try:
                if headers:
                    SESION.headers.update(headers)
                filas = parsear_comtrade(obtener_json(url, params))
            except RuntimeError as e:
                log.warning("Comtrade %s %s sin datos: %s", pais, anio, e)
                continue
            for f in filas:
                con.execute(
                    "INSERT OR REPLACE INTO exportaciones VALUES (?,?,?,?,?,?,?)",
                    (f["pais"], f["anio"], f["hs"], f["valor_usd"], f["peso_kg"], "UN Comtrade", ahora()),
                )
            total += len(filas)
            time.sleep(2)  # el endpoint público limita la frecuencia
    con.commit()
    return total


def cargar_series_bcch() -> dict:
    if SERIES_BCCH_PATH.exists():
        return json.loads(SERIES_BCCH_PATH.read_text(encoding="utf-8"))
    return {}


def descargar_bcch(con: sqlite3.Connection, anios: int = 3) -> int:
    token = os.environ.get("BCCH_TOKEN")
    if not token:
        log.info("BCCh omitido: falta la variable de entorno BCCH_TOKEN")
        return 0
    desde = date(date.today().year - anios, 1, 1).isoformat()
    total = 0
    for sid, meta in cargar_series_bcch().items():
        params = {"token": token, "function": "GetSeries", "timeseries": sid,
                  "firstdate": desde, "lastdate": date.today().isoformat()}
        filas = parsear_bcch(obtener_json(BCCH_URL, params))
        for f in filas:
            con.execute("INSERT OR REPLACE INTO precios VALUES (?,?,?,?,?,?)",
                        (f"bcch:{sid}", f["fecha"], f["valor"], meta.get("unidad", ""), f"BCCh {sid}", ahora()))
        total += len(filas)
        time.sleep(1)
    con.commit()
    return total


def buscar_bcch(palabras: list[str], salida: Path = BASE / "series_bcch_encontradas.csv") -> int:
    """Busca series del BCCh cuyo título contenga TODAS las palabras y las guarda en un CSV."""
    import csv
    token = os.environ.get("BCCH_TOKEN")
    if not token:
        raise SystemExit("Define primero la variable de entorno BCCH_TOKEN")
    palabras = [p.lower() for p in palabras]
    encontradas = []
    for frecuencia in ("DAILY", "MONTHLY", "QUARTERLY", "ANNUAL"):
        r = obtener_json(BCCH_URL, {"token": token, "function": "SearchSeries", "frequency": frecuencia})
        for s_ in r.get("SeriesInfos") or []:
            titulo = (s_.get("spanishTitle") or "").lower()
            if all(p in titulo for p in palabras):
                encontradas.append([s_["seriesId"], frecuencia, s_.get("spanishTitle"), s_.get("lastObservation")])
    with salida.open("w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["seriesId", "frecuencia", "titulo", "ultima_observacion"])
        w.writerows(encontradas)
    print(f"{len(encontradas)} series encontradas -> {salida}")
    return len(encontradas)


# ---------------------------------------------------------------------------
# Exportación a JSON para el panel
# ---------------------------------------------------------------------------

def cobre_usd_t(c_lb: float) -> float:
    return c_lb / 100 * LIBRAS_POR_TONELADA


def exportar_json(con: sqlite3.Connection, ruta: Path = JSON_PATH) -> dict:
    def serie(clave):
        return [{"fecha": f, "valor": v} for f, v in con.execute(
            "SELECT fecha, valor FROM precios WHERE serie=? ORDER BY fecha", (clave,))]

    def resumen(clave_diaria, clave_mensual, convertir=None):
        d = serie(clave_diaria)
        m = serie(clave_mensual)
        conv = convertir or (lambda x: x)
        out = {"diario": [{"fecha": p["fecha"], "valor": round(conv(p["valor"]), 2)} for p in d],
               "mensual": [{"fecha": p["fecha"], "valor": round(conv(p["valor"]), 2)} for p in m]}
        if d:
            ult = d[-1]
            out["ultimo"] = {"fecha": ult["fecha"], "valor": round(conv(ult["valor"]), 2)}
            # variación frente al dato de hace ~5 sesiones (una semana hábil)
            if len(d) >= 6:
                ref = d[-6]["valor"]
                out["var_semanal_pct"] = round((ult["valor"] / ref - 1) * 100, 2)
        return out

    exportaciones = []
    for pais, anio, hs, valor, peso in con.execute(
            "SELECT pais, anio, hs, valor_usd, peso_kg FROM exportaciones ORDER BY pais, anio, hs"):
        exportaciones.append({
            "pais": PAISES_COMTRADE.get(pais, pais), "anio": anio, "hs": hs,
            "producto": PRODUCTOS_HS.get(hs, hs),
            "valor_musd": round(valor / 1e6, 1),
            "toneladas": round(peso / 1000) if peso else None,
            "precio_unitario_usd_t": round(valor / (peso / 1000)) if peso else None,
        })

    chile = []
    for sid, meta in cargar_series_bcch().items():
        filas = serie(f"bcch:{sid}")
        if filas:
            chile.append({"id": sid, "nombre": meta.get("nombre", sid), "unidad": meta.get("unidad", ""),
                          "ultimo": filas[-1], "serie": filas[-400:]})

    datos = {
        "generado": ahora(),
        "fuentes": {
            "bcrp": "Banco Central de Reserva del Perú, estadisticas.bcrp.gob.pe",
            "comtrade": "UN Comtrade, comtradeplus.un.org",
            **({"bcch": "Banco Central de Chile, si3.bcentral.cl"} if chile else {}),
        },
        "cobre": {"unidad": "US$/t", **resumen("cobre_diario", "cobre_mensual", cobre_usd_t)},
        "plata": {"unidad": "US$/oz troy", **resumen("plata_diario", "plata_mensual")},
        "oro": {"unidad": "US$/oz troy", **resumen("oro_diario", "oro_mensual")},
        "litio_exportaciones": exportaciones,
        "chile_bcch": chile,
    }
    ruta.parent.mkdir(parents=True, exist_ok=True)
    ruta.write_text(json.dumps(datos, ensure_ascii=False, indent=2), encoding="utf-8")
    return datos


# ---------------------------------------------------------------------------

def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="CritMine · conector de datos públicos")
    ap.add_argument("--solo", choices=["bcrp", "comtrade", "bcch"], help="ejecuta una sola fuente")
    ap.add_argument("--buscar-bcch", nargs="+", metavar="PALABRA",
                    help="busca series del Banco Central de Chile por palabras del título y sale")
    ap.add_argument("--db", default=str(DB_PATH))
    ap.add_argument("--json", default=str(JSON_PATH))
    args = ap.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    if args.buscar_bcch:
        buscar_bcch(args.buscar_bcch)
        return 0
    con = conectar_db(Path(args.db))
    errores = 0

    for nombre, funcion in (("bcrp", descargar_bcrp), ("comtrade", descargar_comtrade), ("bcch", descargar_bcch)):
        if args.solo and args.solo != nombre:
            continue
        try:
            n = funcion(con)
            registrar(con, nombre, n, "ok")
            log.info("%s: %d filas guardadas", nombre, n)
        except Exception as e:  # noqa: BLE001 — una fuente caída no debe frenar las demás
            errores += 1
            registrar(con, nombre, 0, "error", str(e))
            log.error("%s falló: %s", nombre, e)

    datos = exportar_json(con, Path(args.json))
    log.info("Exportado %s (cobre: %s)", args.json, datos["cobre"].get("ultimo"))
    return 1 if errores >= 2 else 0


if __name__ == "__main__":
    sys.exit(main())

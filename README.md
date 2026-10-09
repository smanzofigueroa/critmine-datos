# CritMine · Conector de datos públicos (Etapa 0)

Descarga automáticamente precios de metales y exportaciones de litio desde fuentes públicas gratuitas, los guarda en una base de datos y genera el panel **CritMine · Datos** para la portada.

| Fuente | Qué trae | Clave |
|---|---|---|
| Banco Central de Reserva del Perú (BCRP) | Cobre LME, plata y oro: diario y promedio mensual | No necesita |
| Banco Central de Chile (BDE) | Dólar observado y las series chilenas que elijas (cobre, exportaciones) | Token gratuito |
| UN Comtrade | Exportaciones anuales de litio de Chile (carbonato HS 283691; óxido e hidróxido HS 282520): toneladas, valor y precio unitario | Opcional (gratis) |

Estas fuentes son públicas y se pueden mostrar en el sitio citando la fuente.

## Archivos

```
conector.py          descarga, guarda en SQLite y exporta datos/datos.json
generar_panel.py     crea panel.html con los datos incrustados
plantilla_panel.html diseño del panel (mismo estilo de CritMine)
cargar_muestras.py   carga datos reales guardados, para probar sin internet
tests/               pruebas automáticas con respuestas reales de las APIs
.github/workflows/   ejecución diaria automática en GitHub (gratis)
datos/               aquí quedan critmine.db y datos.json
```

## Paso a paso: correrlo en tu computador

1. Instala Python 3.10 o superior (python.org). En Windows, marca "Add Python to PATH".
2. Abre una terminal en esta carpeta e instala lo necesario:
   ```
   pip install -r requirements.txt
   ```
3. Comprueba que todo funciona (no necesita internet):
   ```
   python -m pytest -q tests
   ```
4. Descarga los datos reales:
   ```
   python conector.py
   ```
   Verás cuántas filas se guardaron de cada fuente. Si una fuente falla, las demás siguen.
5. Genera el panel y ábrelo en el navegador:
   ```
   python generar_panel.py
   ```
   Abre `panel.html`.

## Paso a paso: que se actualice solo cada día (GitHub Actions)

1. Crea una cuenta en github.com y un repositorio nuevo (por ejemplo `critmine-datos`).
2. Sube todos estos archivos, incluida la carpeta oculta `.github`.
3. En el repositorio, entra a **Actions** y habilita los workflows.
4. Pulsa **CritMine conector → Run workflow** para la primera ejecución.
5. Desde ahí corre solo de lunes a viernes y guarda `datos/datos.json` y `panel.html` actualizados.

Opcional: crea una clave gratuita en comtradeplus.un.org y agrégala en **Settings → Secrets and variables → Actions** con el nombre `COMTRADE_API_KEY`. Da más estabilidad y más consultas que el acceso sin clave.

## Paso a paso: mostrarlo en critmine.com

Tienes dos opciones:

- **Simple:** sube `panel.html` a tu sitio cada vez que se actualice (o publica el repositorio con GitHub Pages y enlázalo).
- **Integrada:** copia `plantilla_panel.html` a tu sitio y deja junto a ella `datos/datos.json`. Si la página no tiene datos incrustados, los lee de ese archivo; basta con actualizar el JSON.

Quien administre el sitio puede leer `datos/datos.json` directamente para usar los precios en la portada o en los informes.

## Banco Central de Chile (token)

1. Obtén tu token en la Base de Datos Estadísticos → Mi Cuenta → Apikey Token. Dura un año: renuévalo antes de que venza.
2. **Nunca lo escribas en el código.** Guárdalo así:
   - En GitHub: Settings → Secrets and variables → Actions → New repository secret, nombre `BCCH_TOKEN`.
   - En tu computador (Windows, PowerShell), solo para la sesión abierta: `$env:BCCH_TOKEN="tu_token"`
3. Busca los códigos de las series que quieras (crea `series_bcch_encontradas.csv`):
   ```
   python conector.py --buscar-bcch cobre
   python conector.py --buscar-bcch exportaciones litio
   ```
4. Copia los códigos que te interesen a `series_bcch.json` con su nombre y unidad, por ejemplo:
   ```json
   {
     "F073.TCO.PRE.Z.D": {"nombre": "Dólar observado", "unidad": "CLP/US$"}
   }
   ```
5. Corre `python conector.py`. Las series aparecen en el panel en la sección "Chile: series del Banco Central".

Sin token, el conector simplemente omite esta fuente y sigue con las demás.

## Cómo agregar más datos

- **Más países en Comtrade:** en `conector.py`, suma códigos a `PAISES_COMTRADE` (32 Argentina, 76 Brasil, 604 Perú).
- **Más productos:** suma códigos HS a `PRODUCTOS_HS` (por ejemplo 2603 concentrado de cobre, 7403 cátodos de cobre, 2530 tierras raras crudas).
- **Más series del BCRP:** busca el código en estadisticas.bcrp.gob.pe y agrégalo a `SERIES_BCRP`.

## Notas

- El cobre del BCRP viene en centavos de dólar por libra; el conector lo convierte a US$/t (1 t = 2.204,62 lb).
- El precio unitario de exportación es valor ÷ peso neto: refleja el precio realizado, no el spot.
- Los datos de Comtrade llegan con meses de retraso; el año en curso suele no estar disponible.
- Los datos de LME, Fastmarkets, SMM o Benchmark **no** se incluyen: requieren licencia para mostrarlos.

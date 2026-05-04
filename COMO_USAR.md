# WebScraping — Guía de uso

Scrapers + análisis competitivo de los catálogos de **Makro**, **Plaza Vea** y **Tottus** (Perú).

---

## 1. Estructura del proyecto

```
WebScraping/
├── api_endpoint_extractor.py        # utilitario: descubre endpoints JSON de cualquier web
├── proceso/
│   ├── makro/
│   │   ├── makro_scraper.py         # scraper VTEX (8 600+ SKUs)
│   │   ├── analizar_competencia.py  # análisis: ARO, pricing, promo, quiebres
│   │   └── analisis/                # 8 PNG + executive_summary.txt
│   ├── plazavea/
│   │   ├── 01_probe_vtex_endpoints.py   # script de descubrimiento (artifact)
│   │   ├── plazavea_scraper.py
│   │   ├── analizar_competencia.py
│   │   └── analisis/
│   └── tottus/
│       ├── 01_probe_endpoints.py        # confirma que NO hay API JSON
│       ├── 02_explore_dom.py            # identifica selectores DOM
│       ├── 03_paginacion_y_scroll.py    # comportamiento de paginación
│       ├── 04_descubrir_categorias.py   # genera categorias.json
│       ├── tottus_scraper.py            # scraper SSR-DOM
│       ├── analizar_competencia.py
│       └── analisis/
└── COMO_USAR.md                     # este archivo
```

**Por qué Makro/PlazaVea son ~iguales y Tottus es distinto:**

- **Makro** y **Plaza Vea** corren sobre **VTEX**: la misma plataforma e-commerce expone los productos por API JSON pública (`/api/catalog_system/pub/...`). Diferencia: distinto base URL y `sales channel`.
- **Tottus** es **SSR (Server-Side Rendering)**: no expone API JSON pública, los productos vienen renderizados dentro del HTML. Por eso el scraper navega con Playwright y parsea el DOM (`a[data-pod="catalyst-pod"]`).

Los scripts numerados `01_*.py`, `02_*.py`, etc. dentro de `tottus/` y `plazavea/` son **artefactos de la investigación** que documentan cómo se llegó al scraper actual. Pueden re-ejecutarse para verificar que la web sigue igual.

---

## 2. Setup inicial

### 2.1 Conda environment

Usamos el env `api_prueba` (ya creado). Si no existe, créalo así:

```bash
conda create -n api_prueba python=3.11 -y
conda activate api_prueba
pip install playwright pandas openpyxl vaex matplotlib seaborn
playwright install chromium
```

Para todos los comandos siguientes usa el python del env directamente:

```bash
PY=/home/jason/.conda/envs/api_prueba/bin/python
```

### 2.2 Clonar el repo

```bash
git clone <url-del-repo>
cd WebScraping
```

---

## 3. Cómo correr cada scraper

> Los CSV/XLSX/JSON de productos **NO están en el repo** (son pesados y reproducibles). Se generan al correr el scraper.

### 3.1 Makro (VTEX, ~8 600 SKUs, 30-50 min full)

```bash
cd proceso/makro

# Catálogo completo (todas las categorías nivel 2)
$PY makro_scraper.py

# Test rápido
$PY makro_scraper.py --max-categorias 2 --max-productos 100

# Solo una categoría
$PY makro_scraper.py --categoria bebidas
```

Salidas (con timestamp):
```
makro_productos_YYYYMMDD_HHMM.csv
makro_productos_YYYYMMDD_HHMM.xlsx
makro_productos_YYYYMMDD_HHMM.json
makro_categorias.csv
```

### 3.2 Plaza Vea (VTEX, ~10-15 mil SKUs full, 25-40 min)

```bash
cd proceso/plazavea

# Catálogo completo
$PY plazavea_scraper.py

# Test
$PY plazavea_scraper.py --max-categorias 5 --max-productos 200

# Solo una categoría
$PY plazavea_scraper.py --categoria bebidas
```

### 3.3 Tottus (SSR-DOM, 1 125 categorías totales, ~2.5-3 h full)

Tottus es el más lento porque cada categoría requiere navegar con Playwright y esperar render. Se recomienda **primero generar el árbol de categorías**:

```bash
cd proceso/tottus

# Paso 1: generar categorias.json (rápido, ~2 min)
$PY 04_descubrir_categorias.py

# Paso 2a: prueba rápida (1 categoría)
$PY tottus_scraper.py --categoria-id CATG17609 --max-paginas 3

# Paso 2b: lote moderado (20 cats, 3 páginas, ~15 min)
$PY tottus_scraper.py --max-categorias 20 --max-paginas 3

# Paso 2c: catálogo completo (todas las 1125 cats, ~2.5-3 h)
$PY tottus_scraper.py
```

---

## 4. Cómo correr los análisis

Cada análisis lee el CSV más reciente de su carpeta y genera 8 PNG + un `executive_summary.txt` en `analisis/`.

```bash
cd proceso/makro    && $PY analizar_competencia.py
cd proceso/plazavea && $PY analizar_competencia.py
cd proceso/tottus   && $PY analizar_competencia.py
```

### 4.1 Qué hay en `analisis/`

Cada carpeta tiene 8 charts + `executive_summary.txt`. Convención compartida:

| # | Chart | Lectura |
|---|---|---|
| 01 | `penetracion_marca_propia.png` | % del surtido que cubre la marca propia, por categoría — **donde no se compite por precio** |
| 02 | `categorias_surtido_y_promo.png` | profundidad (# SKUs) + % en oferta, todas las categorías |
| 03 | `pricing_ladder.png` | distribución global de SKUs por rango de precio |
| 03b | `pricing_ladder_por_categoria.png` | heatmap categoría × rango — combate vs premium por categoría |
| 04 | `top_marcas_terceras.png` | top 50 marcas (sin contar la propia) — target de captura |
| 05 | `descuento_*.png` o `saturacion_*.png` | dónde agreden con descuento / retail-media |
| 06 | `*_vs_terceros_precio.png` o `diferencial_cmr.png` | spread propia vs terceros / incentivo CMR |
| 07 | `quiebres_*.png` o `descuento_y_patrocinio.png` | quiebres de stock o patrocinio |

### 4.2 Diferenciador único de cada tienda

| Tienda | Indicador único | Por qué importa |
|---|---|---|
| **Makro** | penetración **ARO** (marca propia) | Makro mayoreo HoReCa: ARO define precio en >30% de cada categoría donde está |
| **Plaza Vea** | `num_colecciones` por SKU | Saturación retail-media (Ofertas Oh!, landings) — cuánto monetizan con proveedores |
| **Tottus** | diferencial **CMR** vs precio internet | Cuánto sacrifican margen para empujar tarjeta Falabella |

---

## 5. Cómo ver los PNG desde Neovim

Si configuraste `~/.config/nvim/lua/plugins/image_viewer.lua` (incluido en este repo workflow):

```vim
:e proceso/makro/analisis/03b_pricing_ladder_por_categoria.png
```

Abre `feh` automáticamente.

Otros atajos:
- `:Img <ruta>` — abrir cualquier imagen sin cargar buffer
- `<leader>iv` — abrir imagen del buffer actual
- `<leader>id` — abrir feh recursivo en cwd (carrusel)

---

## 6. Workflow típico del analista

```bash
# 1. Asegurarse del env
conda activate api_prueba

# 2. Refrescar datos de las 3 tiendas (en paralelo si quieres acelerar)
PY=/home/jason/.conda/envs/api_prueba/bin/python

cd proceso/makro    && $PY makro_scraper.py    > makro.log 2>&1 &
cd proceso/plazavea && $PY plazavea_scraper.py > plazavea.log 2>&1 &
cd proceso/tottus   && $PY tottus_scraper.py --max-categorias 50 --max-paginas 3 > tottus.log 2>&1 &
wait

# 3. Generar análisis
for d in makro plazavea tottus; do
  cd proceso/$d && $PY analizar_competencia.py
done

# 4. Revisar charts y summary
cat proceso/*/analisis/executive_summary.txt
```

---

## 7. Re-descubrir endpoints (si las webs cambian)

Si una de las webs cambia su estructura y los scrapers fallan, usar
`api_endpoint_extractor.py` (en la raíz) para volver a sniffear:

```bash
$PY api_endpoint_extractor.py 'https://www.plazavea.com.pe/' \
    -o proceso/plazavea/probe_results.json --scroll
```

Para Tottus la API JSON no existe, hay que verificar el DOM:

```bash
cd proceso/tottus
$PY 02_explore_dom.py    # genera dom_findings.json + dom_pod_sample.html
```

Si los selectores cambiaron, actualizar `EXTRACT_PODS_JS` en `tottus_scraper.py`.

---

## 8. Troubleshooting

| Problema | Causa probable | Fix |
|---|---|---|
| `ModuleNotFoundError: playwright` | env mal activado | `conda activate api_prueba` |
| `Executable doesn't exist` (chromium) | playwright incompleto | `playwright install chromium` |
| Tottus devuelve 0 productos | la web cambió selectores | re-correr `02_explore_dom.py` |
| Plaza Vea `sc=3` da 400 | normal, ese SC está inactivo | usar `sc=1` (default) |
| Charts vacíos | CSV de productos no existe | correr el scraper primero |

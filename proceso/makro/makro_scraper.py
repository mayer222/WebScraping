"""
makro_scraper_v2.py
===================
Extrae el catálogo completo de Makro Perú (makro.plazavea.com.pe) usando
la API pública VTEX correctamente.

ENDPOINTS UTILIZADOS:
  1. /api/catalog_system/pub/category/tree/3
       → Árbol real de categorías (3 niveles de profundidad).
         Devuelve id, name, hasChildren, children[] por cada nodo.

  2. /api/catalog_system/pub/products/search
       → Búsqueda de productos VTEX con paginación.
         Parámetros clave:
           fq=C:/<id_path>/      ← filtro por árbol de categoría (CORRECTO)
                                   id_path = ruta acumulada de IDs, ej "/1/15/600/"
           _from=0  _to=49       ← paginación de 50 en 50 (máx VTEX)
           sc=9                  ← canal de ventas Makro Perú

  IMPORTANTE: en VTEX, el filtro `H:` es para productClusters (colecciones de
  marketing tipo "Aniversario Makro", IDs en rango 30000-60000), NO para
  categorías. El filtro de categoría es `C:` y espera el path con slashes.

ESTRATEGIA ANTI-BLOQUEO:
  Playwright lanza un browser Chromium headless, visita la home para
  obtener cookies válidas y hace fetch() desde ese contexto. Esto evita
  los bloqueos de Host/Origin que sufren los requests directos con requests/httpx.

INSTALACIÓN:
    pip install playwright pandas openpyxl
    playwright install chromium

USO:
    # Catálogo completo
    python makro_scraper_v2.py

    # Solo prueba rápida (primeras 2 categorías, máx 100 productos c/u)
    python makro_scraper_v2.py --max-categorias 2 --max-productos 100

    # Solo una categoría por nombre
    python makro_scraper_v2.py --categoria bebidas
"""

from __future__ import annotations

import asyncio
import json
import argparse
import re
import pandas as pd
from datetime import datetime
from pathlib import Path


BASE      = "https://www.makro.plazavea.com.pe"
SC        = 9        # canal de ventas Makro
PAGE_SIZE = 50       # máx recomendado por VTEX


# ─────────────────────────────────────────────
# UTILIDADES DE FETCH
# ─────────────────────────────────────────────

async def browser_fetch(page, url: str) -> dict | list | None:
    """
    Ejecuta fetch() desde el contexto del browser (con cookies válidas).
    Evita bloqueos de Origin/Host que afectan a requests directos.
    """
    result = await page.evaluate(f"""
        async () => {{
            try {{
                const r = await fetch("{url}", {{
                    headers: {{ "Accept": "application/json" }}
                }});
                if (!r.ok) return {{ __status: r.status, __error: r.statusText }};
                return await r.json();
            }} catch(e) {{
                return {{ __error: e.toString() }};
            }}
        }}
    """)
    if isinstance(result, dict) and "__error" in result:
        print(f"  ⚠  fetch error: {result} → {url[:90]}")
        return None
    return result


# ─────────────────────────────────────────────
# PASO 1: ÁRBOL DE CATEGORÍAS
# ─────────────────────────────────────────────

async def obtener_arbol_categorias(page) -> list[dict]:
    """
    Llama a /api/catalog_system/pub/category/tree/3
    Devuelve la lista plana de todas las categorías (niveles 1-3),
    cada una con su id VTEX y el id_path acumulado para usar en fq=C:/.../.
    """
    url = f"{BASE}/api/catalog_system/pub/category/tree/3"
    data = await browser_fetch(page, url)

    if not data or not isinstance(data, list):
        print("  ✗ No se pudo obtener el árbol de categorías.")
        return []

    categorias_planas = []
    _aplanar(data, padre_nombre="", padre_id=None, padre_id_path="/",
             nivel=1, resultado=categorias_planas)
    return categorias_planas


def _aplanar(nodos: list, padre_nombre: str, padre_id,
             padre_id_path: str, nivel: int, resultado: list):
    """
    Recorre recursivamente el árbol VTEX y aplana en lista.
    Acumula `id_path` tipo "/1/15/600/" — esto es lo que VTEX necesita
    para el filtro fq=C:/... (no se puede usar solo el id final).
    """
    for nodo in nodos:
        cat_id   = nodo.get("id")
        nombre   = nodo.get("name", "")
        hijos    = nodo.get("children", [])
        ruta     = f"{padre_nombre} > {nombre}".lstrip(" > ")
        id_path  = f"{padre_id_path}{cat_id}/"

        resultado.append({
            "cat_id":          cat_id,
            "id_path":         id_path,
            "nombre":          nombre,
            "nivel":           nivel,
            "padre_id":        padre_id,
            "padre_nombre":    padre_nombre or "(raíz)",
            "ruta_completa":   ruta,
            "tiene_hijos":     len(hijos) > 0,
        })

        if hijos:
            _aplanar(hijos, ruta, cat_id, id_path, nivel + 1, resultado)


# ─────────────────────────────────────────────
# PASO 2: PRODUCTOS POR CATEGORÍA
# ─────────────────────────────────────────────

async def scrape_categoria(page, id_path: str, cat_nombre: str,
                            cat_id: int, max_productos: int = 9999) -> list[dict]:
    """
    Extrae todos los productos de una categoría usando paginación.
    Usa fq=C:/<id_path>/ que es el filtro correcto de VTEX para árbol de
    categorías. id_path viene tipo "/1/15/600/" desde _aplanar().
    """
    productos = []
    desde = 0

    while desde < max_productos:
        hasta = min(desde + PAGE_SIZE - 1, max_productos - 1)
        url = (
            f"{BASE}/api/catalog_system/pub/products/search"
            f"?fq=C:{id_path}"
            f"&_from={desde}&_to={hasta}"
            f"&sc={SC}"
        )

        data = await browser_fetch(page, url)
        if not data:
            break

        lote = data if isinstance(data, list) else []
        if not lote:
            break  # sin más productos

        for raw in lote:
            productos.append(_parsear_producto(raw, cat_nombre, cat_id))

        print(f"    {cat_nombre[:35]:35s} | pág {desde//PAGE_SIZE+1}"
              f" | +{len(lote)} → total: {len(productos)}")

        if len(lote) < PAGE_SIZE:
            break  # última página (respuesta incompleta = no hay más)

        desde += PAGE_SIZE
        await asyncio.sleep(0.3)  # pausa cortés entre páginas

    return productos


# Regex para parsear unidades por pack desde el productName.
# Ej: "Paquete 15 Botellas", "Pack x12", "Caja 6", "x24", "Display de 6".
# Makro vende casi todo en presentaciones de mayoreo, asi que esta es la
# senal mas confiable para derivar precio unitario.
_PACK_PATTERNS = [
    re.compile(r"(?:paquete|pack|caja|display|fardo|bolsa|bandeja|set|kit|"
               r"docena|six\s*pack)\s*(?:de\s+|x\s*|\xd7\s*)?(\d+)", re.I),
    re.compile(r"\bx\s*(\d+)\b", re.I),
    re.compile(r"\b(\d+)\s*(?:un|u|unidades|botellas|latas|sobres|"
               r"piezas|pzas|pza|pcs|pc)\b", re.I),
]


def _parsear_unidades_pack(nombre: str) -> int:
    """Devuelve unidades por pack. 1 si no se detecta."""
    if not nombre:
        return 1
    for pat in _PACK_PATTERNS:
        m = pat.search(nombre)
        if m:
            try:
                n = int(m.group(1))
                if 1 < n <= 500:  # rango razonable
                    return n
            except (ValueError, TypeError):
                pass
    if re.search(r"\bdocena\b", nombre, re.I):
        return 12
    if re.search(r"\bsix\s*pack\b", nombre, re.I):
        return 6
    return 1


def _parsear_cuotas(installments: list) -> dict:
    """Extrae max cuotas y valor de cuota minimo (sin interes si existe)."""
    if not installments:
        return {"cuotas_max": None, "cuota_valor": None}
    sin_interes = [i for i in installments
                   if (i.get("InterestRate") or 0) == 0]
    pool = sin_interes or installments
    cuotas_max = max((i.get("NumberOfInstallments") or 0 for i in pool),
                     default=None)
    candidatas = [i for i in pool
                  if i.get("NumberOfInstallments") == cuotas_max]
    valor = candidatas[0].get("Value") if candidatas else None
    return {"cuotas_max": cuotas_max or None, "cuota_valor": valor}


def _parsear_promos(teasers: list, promo_teasers: list) -> str | None:
    """Concatena nombres de promociones activas."""
    nombres = []
    for t in (teasers or []) + (promo_teasers or []):
        nombre = t.get("name") or t.get("Name") or t.get("<Name>k__BackingField")
        if nombre:
            nombres.append(nombre)
    return " | ".join(nombres) if nombres else None


def _parsear_precio(items: list, nombre_producto: str = "") -> dict:
    """Extrae precio normal, oferta, descuento, unitario derivado y promos."""
    empty = {"precio_normal": None, "precio_oferta": None,
             "descuento_pct": None, "disponible": False,
             "precio_unitario": None, "unidades_por_pack": 1,
             "cuotas_max": None, "cuota_valor": None,
             "oferta_valida_hasta": None, "promos_activas": None}

    if not items:
        return empty

    sellers = items[0].get("sellers", [])
    if not sellers:
        return empty

    oferta = sellers[0].get("commertialOffer", {})
    p_normal  = oferta.get("ListPrice")
    p_oferta  = oferta.get("Price")
    disponible = oferta.get("AvailableQuantity", 0) > 0

    descuento = None
    if p_normal and p_oferta and p_normal > 0 and p_normal != p_oferta:
        descuento = round((1 - p_oferta / p_normal) * 100, 1)
        if descuento <= 0:
            descuento = None

    unidades = _parsear_unidades_pack(nombre_producto)
    p_unit = round(p_oferta / unidades, 2) if (p_oferta and unidades) else None

    cuotas = _parsear_cuotas(oferta.get("Installments", []))
    promos = _parsear_promos(oferta.get("Teasers"),
                             oferta.get("PromotionTeasers"))

    return {
        "precio_normal":       p_normal,
        "precio_oferta":       p_oferta,
        "descuento_pct":       descuento,
        "disponible":          disponible,
        "precio_unitario":     p_unit,
        "unidades_por_pack":   unidades,
        "cuotas_max":          cuotas["cuotas_max"],
        "cuota_valor":         cuotas["cuota_valor"],
        "oferta_valida_hasta": oferta.get("PriceValidUntil"),
        "promos_activas":      promos,
    }


def _parsear_producto(raw: dict, cat_nombre: str, cat_id) -> dict:
    """Convierte un producto VTEX raw en una fila plana."""
    items       = raw.get("items", [])
    nombre      = raw.get("productName") or ""
    precio      = _parsear_precio(items, nombre_producto=nombre)
    sku         = items[0] if items else {}

    # EAN del primer SKU
    ean = sku.get("ean") or None

    # Imagen principal
    imagenes  = sku.get("images", [])
    imagen_url = imagenes[0].get("imageUrl") if imagenes else None

    # Especificaciones (aplanar dict)
    specs = {}
    for clave in raw.get("allSpecifications", []):
        val = raw.get(clave)
        if val and isinstance(val, list):
            specs[clave] = " | ".join(str(v) for v in val)

    # Description puede venir como string O como lista en VTEX
    desc = raw.get("description") or ""
    if isinstance(desc, list):
        desc = " ".join(map(str, desc))
    desc = desc[:300]

    # Colecciones/clusters (máx 5)
    clusters = raw.get("productClusters", {})

    return {
        # Identificadores
        "product_id":        raw.get("productId"),
        "sku_id":            sku.get("itemId"),
        "referencia":        raw.get("productReference"),
        "ean":               ean,

        # Producto
        "nombre":            raw.get("productName"),
        "marca":             raw.get("brand"),
        "descripcion":       desc,
        "link_texto":        raw.get("linkText"),
        "url_producto":      f"{BASE}/{raw.get('linkText')}/p"
                             if raw.get("linkText") else None,

        # Categorización
        "categoria_id":      cat_id,
        "categoria_nombre":  cat_nombre,
        "categoria_id_vtex": raw.get("categoryId"),

        # Precio
        "precio_normal":       precio["precio_normal"],
        "precio_oferta":       precio["precio_oferta"],
        "precio_unitario":     precio["precio_unitario"],
        "unidades_por_pack":   precio["unidades_por_pack"],
        "descuento_pct":       precio["descuento_pct"],
        "tiene_descuento":     precio["descuento_pct"] is not None,
        "disponible":          precio["disponible"],
        "moneda":              "PEN",
        "cuotas_max":          precio["cuotas_max"],
        "cuota_valor":         precio["cuota_valor"],
        "oferta_valida_hasta": precio["oferta_valida_hasta"],
        "promos_activas":      precio["promos_activas"],

        # Colecciones
        "colecciones":       " | ".join(list(clusters.values())[:5]),
        "num_colecciones":   len(clusters),

        # Media
        "imagen_url":        imagen_url,

        # Metadata
        "fecha_extraccion":  datetime.now().strftime("%Y-%m-%d %H:%M"),
    }


# ─────────────────────────────────────────────
# MAIN
# ─────────────────────────────────────────────

async def main(max_productos_por_cat: int = 9999,
               max_categorias: int = 9999,
               solo_categoria: str = None):

    try:
        from playwright.async_api import async_playwright
    except ImportError:
        print("Playwright no instalado. Ejecuta:\n"
              "  pip install playwright\n"
              "  playwright install chromium")
        return

    print("=" * 60)
    print("MAKRO SCRAPER v2  —  Inicio")
    print("=" * 60)

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        ctx = await browser.new_context(
            locale="es-PE",
            extra_http_headers={"Referer": BASE},
        )
        page = await ctx.new_page()

        # ── 1. Visitar home para obtener cookies/sesión ──────────
        print("\n[1/4] Abriendo home de Makro para iniciar sesión...")
        await page.goto(BASE, wait_until="domcontentloaded")
        await asyncio.sleep(2)

        # ── 2. Árbol de categorías ───────────────────────────────
        print("[2/4] Descargando árbol de categorías VTEX...")
        categorias = await obtener_arbol_categorias(page)

        if not categorias:
            print("  ✗ Árbol vacío. Revisa conectividad o cookies.")
            await browser.close()
            return

        print(f"  ✓ {len(categorias)} categorías/subcategorías encontradas")

        # Guardar árbol de categorías
        df_cats = pd.DataFrame(categorias)
        cats_path = "makro_categorias.csv"
        df_cats.to_csv(cats_path, index=False, encoding="utf-8-sig")
        print(f"  → {cats_path} guardado")
        print(df_cats[["nivel", "nombre", "id_path", "ruta_completa"]]
              .to_string(index=False, max_rows=20))

        # ── 3. Seleccionar categorías objetivo ───────────────────
        # Usar nivel 2 (subcategorías) para no duplicar productos
        # que ya aparecen en el nivel padre.
        print("\n[3/4] Preparando categorías objetivo (nivel 2)...")
        cats_objetivo = [c for c in categorias if c["nivel"] == 2]

        if solo_categoria:
            cats_objetivo = [
                c for c in cats_objetivo
                if solo_categoria.lower() in c["nombre"].lower()
            ]

        cats_objetivo = cats_objetivo[:max_categorias]
        print(f"  Scrapeando {len(cats_objetivo)} subcategorías...")

        # ── 4. Scraping de productos ─────────────────────────────
        print("\n[4/4] Extrayendo productos...")
        todos = []

        for i, cat in enumerate(cats_objetivo):
            print(f"\n  [{i+1}/{len(cats_objetivo)}] {cat['ruta_completa']}"
                  f"  (C:{cat['id_path']})")
            prods = await scrape_categoria(
                page,
                id_path=cat["id_path"],
                cat_nombre=cat["nombre"],
                cat_id=cat["cat_id"],
                max_productos=max_productos_por_cat,
            )
            todos.extend(prods)
            await asyncio.sleep(0.5)

        await browser.close()

    # ── Construir DataFrame ──────────────────────────────────────
    print(f"\n{'='*60}\nPROCESANDO RESULTADOS\n{'='*60}")

    if not todos:
        print("No se obtuvieron productos. Verifica la conectividad "
              "y que el sitio esté accesible desde tu red.")
        return

    df = pd.DataFrame(todos)

    # Deduplicar por product_id
    antes = len(df)
    df = df.drop_duplicates(subset=["product_id"])
    print(f"Productos únicos: {len(df)}  (removidos {antes - len(df)} dupl.)")

    # Resumen
    print(f"\nResumen del catálogo:")
    print(f"  Total productos    : {len(df)}")
    print(f"  Con precio         : {df['precio_oferta'].notna().sum()}")
    print(f"  Disponibles        : {df['disponible'].sum()}")
    print(f"  Con descuento      : {df['tiene_descuento'].sum()}")
    if df["precio_oferta"].notna().any():
        print(f"  Precio prom.       : S/ {df['precio_oferta'].mean():.2f}")
    print(f"\nTop 10 categorías:")
    print(df["categoria_nombre"].value_counts().head(10).to_string())
    print(f"\nTop 10 marcas:")
    print(df["marca"].value_counts().head(10).to_string())

    # ── Exportar ─────────────────────────────────────────────────
    ts = datetime.now().strftime("%Y%m%d_%H%M")
    out = Path(".")

    csv_path  = out / f"makro_productos_{ts}.csv"
    xlsx_path = out / f"makro_productos_{ts}.xlsx"
    json_path = out / f"makro_productos_{ts}.json"

    df.to_csv(csv_path,  index=False, encoding="utf-8-sig")
    df.to_excel(xlsx_path, index=False)
    df.to_json(json_path, orient="records", force_ascii=False, indent=2)

    print(f"\n✓ Archivos exportados:")
    print(f"  → {csv_path}")
    print(f"  → {xlsx_path}")
    print(f"  → {json_path}")
    print(f"  → {cats_path}")


# ─────────────────────────────────────────────
# CLI
# ─────────────────────────────────────────────

if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Scraper del catálogo de Makro Perú (v2)"
    )
    parser.add_argument("--max-productos",  type=int, default=9999,
                        help="Máx productos por categoría (default: todos)")
    parser.add_argument("--max-categorias", type=int, default=9999,
                        help="Máx categorías a procesar (default: todas)")
    parser.add_argument("--categoria",      type=str, default=None,
                        help="Filtrar por nombre de categoría")
    args = parser.parse_args()

    asyncio.run(main(
        max_productos_por_cat=args.max_productos,
        max_categorias=args.max_categorias,
        solo_categoria=args.categoria,
    ))
    
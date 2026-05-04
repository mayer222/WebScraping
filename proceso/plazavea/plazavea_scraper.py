"""
plazavea_scraper.py
===================
Extrae el catalogo de Plaza Vea (www.plazavea.com.pe) usando la API publica
de VTEX. Es la misma plataforma que Makro (de hecho Makro es un sub-portal
de la misma cuenta VTEX), por lo que la mecanica es identica:

  1. /api/catalog_system/pub/category/tree/3
       Arbol de categorias con id, nombre, hijos.

  2. /api/catalog_system/pub/products/search?fq=C:/<id_path>/
       Productos por categoria con paginacion (50 por request).

Diferencias vs Makro:
  - BASE distinta (www.plazavea.com.pe en vez de makro.plazavea.com.pe)
  - SC = 1 (canal de ventas Plaza Vea principal); con SC=2 se obtienen
    productos del marketplace, asi que por defecto usamos sc=1.

SCOPE: solo el tab "Supermercado" del menu principal de plazavea.com.pe
(comestibles + cuidado del consumidor). Excluye Tecnologia, Electrohogar,
Moda, Muebles, Deportes, Juguetes, Automotriz, Decohogar, etc. La lista
exacta de departamentos esta en DEPARTAMENTOS_SUPERMERCADO mas abajo.

Uso:
    python plazavea_scraper.py
    python plazavea_scraper.py --max-categorias 2 --max-productos 100
    python plazavea_scraper.py --categoria bebidas
"""

import asyncio
import argparse
import pandas as pd
from datetime import datetime
from pathlib import Path


BASE      = "https://www.plazavea.com.pe"
SC        = 1
PAGE_SIZE = 50

# Departamentos (nivel 1 del arbol VTEX) que componen la seccion
# "Supermercado" del menu principal de plazavea.com.pe. Es la
# union de comestibles + cuidado del consumidor: lo que realmente
# se vende en un supermercado, excluyendo el surtido tipo
# hipermercado/depto (tecnologia, electrohogar, moda, muebles,
# juguetes, deportes, automotriz, etc.).
DEPARTAMENTOS_SUPERMERCADO = {
    "Bebidas",
    "Abarrotes",
    "Frutas y Verduras",
    "Congelados",
    "Quesos y Fiambres",
    "Panadería y Pastelería",
    "Lácteos y Huevos",
    "Desayunos",
    "Pollo Rostizado y Comidas Preparadas",
    "Mercado Saludable",
    "Cuidado Personal y Salud",
    "Limpieza",
    "Mascotas",
    "Belleza",
    "Bebé e Infantil",
}


async def browser_fetch(page, url: str):
    result = await page.evaluate(f"""
        async () => {{
            try {{
                const r = await fetch("{url}", {{
                    headers: {{ "Accept": "application/json" }}
                }});
                if (!r.ok && r.status !== 206) {{
                    return {{ __status: r.status, __error: r.statusText }};
                }}
                return await r.json();
            }} catch(e) {{
                return {{ __error: e.toString() }};
            }}
        }}
    """)
    if isinstance(result, dict) and "__error" in result:
        print(f"  fetch error: {result} -> {url[:90]}")
        return None
    return result


async def obtener_arbol_categorias(page):
    url = f"{BASE}/api/catalog_system/pub/category/tree/3"
    data = await browser_fetch(page, url)
    if not data or not isinstance(data, list):
        print("  no se pudo obtener el arbol de categorias")
        return []
    categorias = []
    _aplanar(data, padre_nombre="", padre_id=None, padre_id_path="/",
             nivel=1, resultado=categorias)
    return categorias


def _aplanar(nodos, padre_nombre, padre_id, padre_id_path, nivel, resultado):
    for nodo in nodos:
        cat_id   = nodo.get("id")
        nombre   = nodo.get("name", "")
        hijos    = nodo.get("children", [])
        ruta     = f"{padre_nombre} > {nombre}".lstrip(" > ")
        id_path  = f"{padre_id_path}{cat_id}/"

        resultado.append({
            "cat_id":         cat_id,
            "id_path":        id_path,
            "nombre":         nombre,
            "nivel":          nivel,
            "padre_id":       padre_id,
            "padre_nombre":   padre_nombre or "(raiz)",
            "ruta_completa":  ruta,
            "tiene_hijos":    len(hijos) > 0,
        })

        if hijos:
            _aplanar(hijos, ruta, cat_id, id_path, nivel + 1, resultado)


async def scrape_categoria(page, id_path, cat_nombre, cat_id, departamento,
                           max_productos=9999):
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
            break

        for raw in lote:
            productos.append(_parsear_producto(raw, cat_nombre, cat_id, departamento))

        print(f"    {cat_nombre[:35]:35s} | pag {desde//PAGE_SIZE+1}"
              f" | +{len(lote)} -> total: {len(productos)}")

        if len(lote) < PAGE_SIZE:
            break

        desde += PAGE_SIZE
        await asyncio.sleep(0.3)

    return productos


def _parsear_precio(items):
    empty = {"precio_normal": None, "precio_oferta": None,
             "descuento_pct": None, "disponible": False,
             "precio_unitario": None}

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

    return {
        "precio_normal":   p_normal,
        "precio_oferta":   p_oferta,
        "descuento_pct":   descuento,
        "disponible":      disponible,
        "precio_unitario": oferta.get("spotPrice"),
    }


def _parsear_producto(raw, cat_nombre, cat_id, departamento):
    items = raw.get("items", [])
    precio = _parsear_precio(items)
    sku = items[0] if items else {}

    ean = sku.get("ean") or None

    imagenes = sku.get("images", [])
    imagen_url = imagenes[0].get("imageUrl") if imagenes else None

    desc = raw.get("description") or ""
    if isinstance(desc, list):
        desc = " ".join(map(str, desc))
    desc = desc[:300]

    clusters = raw.get("productClusters", {})

    return {
        "product_id":        raw.get("productId"),
        "sku_id":            sku.get("itemId"),
        "referencia":        raw.get("productReference"),
        "ean":               ean,

        "nombre":            raw.get("productName"),
        "marca":             raw.get("brand"),
        "descripcion":       desc,
        "link_texto":        raw.get("linkText"),
        "url_producto":      f"{BASE}/{raw.get('linkText')}/p"
                             if raw.get("linkText") else None,

        "departamento":      departamento,
        "categoria_id":      cat_id,
        "categoria_nombre":  cat_nombre,
        "categoria_id_vtex": raw.get("categoryId"),

        "precio_normal":     precio["precio_normal"],
        "precio_oferta":     precio["precio_oferta"],
        "precio_unitario":   precio["precio_unitario"],
        "descuento_pct":     precio["descuento_pct"],
        "tiene_descuento":   precio["descuento_pct"] is not None,
        "disponible":        precio["disponible"],
        "moneda":            "PEN",

        "colecciones":       " | ".join(list(clusters.values())[:5]),
        "num_colecciones":   len(clusters),

        "imagen_url":        imagen_url,

        "fecha_extraccion":  datetime.now().strftime("%Y-%m-%d %H:%M"),
    }


async def main(max_productos_por_cat=9999, max_categorias=9999, solo_categoria=None):
    try:
        from playwright.async_api import async_playwright
    except ImportError:
        print("Playwright no instalado. Ejecuta:\n"
              "  pip install playwright\n  playwright install chromium")
        return

    print("=" * 60)
    print("PLAZA VEA SCRAPER  -  Inicio")
    print("=" * 60)

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        ctx = await browser.new_context(
            locale="es-PE",
            extra_http_headers={"Referer": BASE},
        )
        page = await ctx.new_page()

        print("\n[1/4] Abriendo home de Plaza Vea para iniciar sesion...")
        await page.goto(BASE, wait_until="domcontentloaded")
        await asyncio.sleep(2)

        print("[2/4] Descargando arbol de categorias VTEX...")
        categorias = await obtener_arbol_categorias(page)

        if not categorias:
            print("  arbol vacio. revisa conectividad o cookies.")
            await browser.close()
            return

        print(f"  {len(categorias)} categorias/subcategorias encontradas")

        df_cats = pd.DataFrame(categorias)
        cats_path = "plazavea_categorias.csv"
        df_cats.to_csv(cats_path, index=False, encoding="utf-8-sig")
        print(f"  -> {cats_path} guardado")
        print(df_cats[["nivel", "nombre", "id_path", "ruta_completa"]]
              .to_string(index=False, max_rows=20))

        print("\n[3/4] Preparando categorias objetivo (nivel 2)...")
        cats_objetivo = [
            c for c in categorias
            if c["nivel"] == 2
            and c["padre_nombre"] in DEPARTAMENTOS_SUPERMERCADO
        ]
        print(f"  filtrado a departamentos 'Supermercado': "
              f"{len(cats_objetivo)} subcategorias "
              f"(de {sum(1 for c in categorias if c['nivel']==2)} totales)")

        if solo_categoria:
            cats_objetivo = [
                c for c in cats_objetivo
                if solo_categoria.lower() in c["nombre"].lower()
            ]

        cats_objetivo = cats_objetivo[:max_categorias]
        print(f"  scrapeando {len(cats_objetivo)} subcategorias...")

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
                departamento=cat["padre_nombre"],
                max_productos=max_productos_por_cat,
            )
            todos.extend(prods)
            await asyncio.sleep(0.5)

        await browser.close()

    print(f"\n{'='*60}\nPROCESANDO RESULTADOS\n{'='*60}")

    if not todos:
        print("No se obtuvieron productos.")
        return

    df = pd.DataFrame(todos)

    antes = len(df)
    df = df.drop_duplicates(subset=["product_id"])
    print(f"Productos unicos: {len(df)}  (removidos {antes - len(df)} dupl.)")

    print(f"\nResumen del catalogo:")
    print(f"  Total productos    : {len(df)}")
    print(f"  Con precio         : {df['precio_oferta'].notna().sum()}")
    print(f"  Disponibles        : {df['disponible'].sum()}")
    print(f"  Con descuento      : {df['tiene_descuento'].sum()}")
    if df["precio_oferta"].notna().any():
        print(f"  Precio prom.       : S/ {df['precio_oferta'].mean():.2f}")
    print(f"\nTop 10 categorias:")
    print(df["categoria_nombre"].value_counts().head(10).to_string())
    print(f"\nTop 10 marcas:")
    print(df["marca"].value_counts().head(10).to_string())

    ts = datetime.now().strftime("%Y%m%d_%H%M")
    out = Path(".")

    csv_path  = out / f"plazavea_productos_{ts}.csv"
    xlsx_path = out / f"plazavea_productos_{ts}.xlsx"
    json_path = out / f"plazavea_productos_{ts}.json"

    df.to_csv(csv_path,  index=False, encoding="utf-8-sig")
    df.to_excel(xlsx_path, index=False)
    df.to_json(json_path, orient="records", force_ascii=False, indent=2)

    print(f"\nArchivos exportados:")
    print(f"  -> {csv_path}")
    print(f"  -> {xlsx_path}")
    print(f"  -> {json_path}")
    print(f"  -> {cats_path}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Scraper Plaza Vea")
    parser.add_argument("--max-productos",  type=int, default=9999)
    parser.add_argument("--max-categorias", type=int, default=9999)
    parser.add_argument("--categoria",      type=str, default=None)
    args = parser.parse_args()

    asyncio.run(main(
        max_productos_por_cat=args.max_productos,
        max_categorias=args.max_categorias,
        solo_categoria=args.categoria,
    ))

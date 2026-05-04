"""
tottus_scraper.py
=================
Scraper de Tottus Peru (www.tottus.com.pe).

A diferencia de Makro/Plaza Vea (VTEX, API JSON publica), Tottus es SSR:
  - El catalogo NO se expone via API JSON (ver 01_probe_endpoints.py).
  - Los productos vienen renderizados en el HTML por el servidor.
  - Selectores: a[data-pod="catalyst-pod"], data-key, .pod-title, etc.
  - Paginacion por URL ?page=N. Listados de productos con 48 por pagina.

Estrategia:
  1. Cargar categorias.json (generado por 04_descubrir_categorias.py).
  2. Para cada categoria, navegar a su URL ?page=N y extraer pods.
  3. Avanzar paginas hasta que no haya mas pods.
  4. Deduplicar por product_id y exportar CSV/JSON/XLSX.

Si categorias.json no existe, lo genera al vuelo desde el home.

Uso:
    python tottus_scraper.py
    python tottus_scraper.py --max-categorias 5 --max-paginas 2
    python tottus_scraper.py --categoria-id CATG17609
"""

import asyncio
import argparse
import json
import re
from datetime import datetime
from pathlib import Path
import pandas as pd

BASE = "https://www.tottus.com.pe"
HERE = Path(__file__).parent

CAT_URL_RE = re.compile(r"/tottus-pe/lista/(CATG\d+)(?:/([^?#\"\s]+))?")
ART_URL_RE = re.compile(r"/articulo/(\d+)/[^/]*/?(\d+)?")


# ─────────────────────────────────────────────
# CATEGORIAS
# ─────────────────────────────────────────────

async def descubrir_categorias_desde_home(page) -> list[dict]:
    """Extrae enlaces /tottus-pe/lista/CATG####/... del home."""
    await page.goto(BASE, wait_until="domcontentloaded", timeout=60000)
    await asyncio.sleep(3)
    links = await page.evaluate("""() => {
        const out = [];
        for (const a of document.querySelectorAll('a[href*="/tottus-pe/lista/"]')) {
            out.push({href: a.href, text: (a.textContent || '').trim().slice(0, 80)});
        }
        return out;
    }""")
    cats = {}
    for hl in links:
        m = CAT_URL_RE.search(hl["href"])
        if not m:
            continue
        cid, slug = m.group(1), m.group(2) or ""
        if cid not in cats:
            cats[cid] = {
                "cat_id": cid, "slug": slug,
                "url": hl["href"], "menu_text": hl["text"],
            }
    return sorted(cats.values(), key=lambda c: c["cat_id"])


def cargar_categorias() -> list[dict]:
    """Lee categorias.json si existe."""
    cats_file = HERE / "categorias.json"
    if cats_file.exists():
        return json.loads(cats_file.read_text())
    return []


# ─────────────────────────────────────────────
# EXTRAE PODS DE UNA PAGINA
# ─────────────────────────────────────────────

EXTRACT_PODS_JS = """() => {
    const pods = Array.from(document.querySelectorAll('a[data-pod=catalyst-pod]'));
    return pods.map(p => {
        const txt = sel => p.querySelector(sel)?.textContent?.trim() || null;
        const attr = (sel, a) => p.querySelector(sel)?.getAttribute(a) || null;
        return {
            href: p.href || null,
            product_id: p.getAttribute('data-key'),
            data_category: p.getAttribute('data-category'),
            sponsored: p.getAttribute('data-sponsored') === 'true',
            ssr: !!p.closest('[data-testid=ssr-pod]'),
            brand: txt('.pod-title'),
            name: txt('.pod-subTitle'),
            package_info: txt('.pod-subtitle-unit'),
            seller: txt('.pod-sellerText'),
            price_internet: attr('[data-internet-price]', 'data-internet-price'),
            price_cmr: attr('[data-cmr-price]', 'data-cmr-price'),
            price_normal: attr('[data-normal-price]', 'data-normal-price'),
            image_url: p.querySelector('img')?.src || null,
            badges: Array.from(p.querySelectorAll('.meatstickers, [class*=Badge]'))
                         .map(b => (b.textContent || '').trim())
                         .filter(Boolean)
                         .slice(0, 5),
        };
    });
}"""


async def scrape_categoria(page, cat: dict, max_paginas: int = 20) -> list[dict]:
    """Scrapea todas las paginas de una categoria."""
    productos = {}  # dedupe por product_id
    base_url = cat["url"].split("?")[0]
    total_resultados = None

    for n in range(1, max_paginas + 1):
        url = base_url + (f"?page={n}" if n > 1 else "")
        try:
            await page.goto(url, timeout=45000)
        except Exception as e:
            print(f"    pag {n} timeout: {e}")
            break
        await asyncio.sleep(3)
        # scroll para cargar lazy
        for _ in range(4):
            await page.evaluate("window.scrollBy(0, 1000)")
            await asyncio.sleep(0.4)

        if total_resultados is None:
            total_resultados = await page.evaluate("""() => {
                const el = Array.from(document.querySelectorAll('*'))
                  .filter(e => e.children.length === 0 && /Resultados\\s*\\(/i.test(e.textContent || ''))[0];
                if (!el) return null;
                const m = el.textContent.match(/(\\d+)/);
                return m ? parseInt(m[1]) : null;
            }""")

        pods = await page.evaluate(EXTRACT_PODS_JS)
        nuevos = 0
        for raw in pods:
            pid = raw.get("product_id")
            if not pid or pid in productos:
                continue
            productos[pid] = _parsear_pod(raw, cat)
            nuevos += 1

        print(f"    {cat['cat_id']} {cat.get('menu_text','')[:25]:25s} | "
              f"pag {n} | +{nuevos:>3} ({len(pods):>3} pods) | "
              f"total: {len(productos)}/{total_resultados or '?'}")

        if not pods or nuevos == 0:
            break
        if total_resultados and len(productos) >= total_resultados:
            break

    return list(productos.values())


def _parse_float(s):
    if s is None:
        return None
    try:
        return float(s)
    except (ValueError, TypeError):
        return None


def _parsear_pod(raw: dict, cat: dict) -> dict:
    href = raw.get("href") or ""
    m = ART_URL_RE.search(href)
    p_id = m.group(1) if m else raw.get("product_id")
    sku = m.group(2) if m and m.group(2) else None

    p_internet = _parse_float(raw.get("price_internet"))
    p_normal = _parse_float(raw.get("price_normal")) or p_internet
    p_cmr = _parse_float(raw.get("price_cmr"))

    descuento = None
    if p_normal and p_internet and p_normal > 0 and p_normal != p_internet:
        descuento = round((1 - p_internet / p_normal) * 100, 1)
        if descuento <= 0:
            descuento = None

    return {
        "product_id":        p_id,
        "sku_id":            sku,
        "referencia":        None,
        "ean":               None,

        "nombre":            raw.get("name"),
        "marca":             raw.get("brand"),
        "descripcion":       raw.get("package_info") or "",
        "link_texto":        href,
        "url_producto":      href,

        "categoria_id":      cat["cat_id"],
        "categoria_nombre":  cat.get("menu_text") or cat.get("slug"),
        "categoria_id_vtex": raw.get("data_category"),

        "precio_normal":     p_normal,
        "precio_oferta":     p_internet,
        "precio_unitario":   p_internet,
        "descuento_pct":     descuento,
        "tiene_descuento":   descuento is not None,
        "disponible":        p_internet is not None,
        "moneda":            "PEN",

        "colecciones":       " | ".join(raw.get("badges") or []),
        "num_colecciones":   len(raw.get("badges") or []),

        "imagen_url":        raw.get("image_url"),

        "fecha_extraccion":  datetime.now().strftime("%Y-%m-%d %H:%M"),

        # extras de Tottus
        "vendedor":          raw.get("seller"),
        "precio_cmr":        p_cmr,
        "patrocinado":       raw.get("sponsored"),
    }


# ─────────────────────────────────────────────
# MAIN
# ─────────────────────────────────────────────

async def main(max_productos_por_cat=9999, max_categorias=9999,
               max_paginas=20, categoria_id=None):
    try:
        from playwright.async_api import async_playwright
    except ImportError:
        print("Playwright no instalado.")
        return

    print("=" * 60)
    print("TOTTUS SCRAPER  -  Inicio")
    print("=" * 60)

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        ctx = await browser.new_context(
            locale="es-PE",
            user_agent=("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                        "AppleWebKit/537.36 Chrome/121.0.0.0 Safari/537.36"),
        )
        page = await ctx.new_page()

        print("\n[1/3] Categorias...")
        cats = cargar_categorias()
        if not cats:
            print("  categorias.json no existe; descubriendo desde home...")
            cats = await descubrir_categorias_desde_home(page)
            (HERE / "categorias.json").write_text(
                json.dumps(cats, indent=2, ensure_ascii=False))
        print(f"  {len(cats)} categorias")

        # Filtros
        if categoria_id:
            cats = [c for c in cats if c["cat_id"] == categoria_id]
            print(f"  filtrado por --categoria-id={categoria_id}: {len(cats)} cats")
        cats = cats[:max_categorias]

        # Scraping
        print(f"\n[2/3] Extrayendo productos de {len(cats)} categorias...")
        todos = []
        for i, cat in enumerate(cats):
            print(f"\n  [{i+1}/{len(cats)}] {cat['cat_id']} {cat.get('menu_text','')[:40]}")
            try:
                prods = await scrape_categoria(page, cat, max_paginas=max_paginas)
                todos.extend(prods[:max_productos_por_cat])
            except Exception as e:
                print(f"    ERROR: {e}")
            await asyncio.sleep(0.4)

        await browser.close()

    print(f"\n[3/3] {'='*40}\nPROCESANDO RESULTADOS\n{'='*40}")

    if not todos:
        print("Sin productos.")
        return

    df = pd.DataFrame(todos)
    antes = len(df)
    df = df.drop_duplicates(subset=["product_id"])
    print(f"Productos unicos: {len(df)}  (removidos {antes - len(df)} dupl.)")

    print(f"\nResumen:")
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
    csv_path = HERE / f"tottus_productos_{ts}.csv"
    xlsx_path = HERE / f"tottus_productos_{ts}.xlsx"
    json_path = HERE / f"tottus_productos_{ts}.json"

    df.to_csv(csv_path, index=False, encoding="utf-8-sig")
    df.to_excel(xlsx_path, index=False)
    df.to_json(json_path, orient="records", force_ascii=False, indent=2)

    print(f"\nArchivos exportados:")
    print(f"  -> {csv_path.name}")
    print(f"  -> {xlsx_path.name}")
    print(f"  -> {json_path.name}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Scraper Tottus")
    parser.add_argument("--max-productos",  type=int, default=9999)
    parser.add_argument("--max-categorias", type=int, default=9999)
    parser.add_argument("--max-paginas",    type=int, default=20)
    parser.add_argument("--categoria-id",   type=str, default=None)
    args = parser.parse_args()

    asyncio.run(main(
        max_productos_por_cat=args.max_productos,
        max_categorias=args.max_categorias,
        max_paginas=args.max_paginas,
        categoria_id=args.categoria_id,
    ))

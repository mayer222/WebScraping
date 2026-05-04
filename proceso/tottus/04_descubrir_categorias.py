"""
04_descubrir_categorias.py
==========================
Descubre el listado completo de categorias de Tottus. Pruebas:
  1. /sitemap.xml (y sus sub-sitemaps).
  2. Extraccion de links /tottus-pe/lista/CATG####/... desde el home.
  3. Extraccion del menu del sidebar/header (si esta en HTML).

Guarda:
  - categorias.json: lista deduplicada (cat_id, slug, url, fuente).
  - categorias_debug.json: detalle por fuente.
"""

import asyncio
import json
import re
from pathlib import Path
from urllib.parse import urljoin
from playwright.async_api import async_playwright

OUT = Path(__file__).parent / "categorias.json"
DBG = Path(__file__).parent / "categorias_debug.json"
BASE = "https://www.tottus.com.pe"

CAT_URL_RE = re.compile(r"/tottus-pe/lista/(CATG\d+)(?:/([^?#\"\s]+))?")


async def fetch_text(page, url):
    return await page.evaluate(f"""async () => {{
        try {{
            const r = await fetch('{url}', {{headers:{{Accept:'application/xml,text/html,*/*'}}}});
            return {{status: r.status, body: await r.text()}};
        }} catch(e) {{ return {{err: e.toString()}}; }}
    }}""")


async def main():
    debug = {"sitemap": [], "home_links": [], "menu_items": []}
    cats = {}  # cat_id -> info

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        ctx = await browser.new_context(
            locale="es-PE",
            user_agent=("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                        "AppleWebKit/537.36 Chrome/121.0.0.0 Safari/537.36"),
        )
        page = await ctx.new_page()
        await page.goto(BASE, wait_until="domcontentloaded", timeout=60000)
        await asyncio.sleep(3)

        # 1. sitemap.xml
        sm = await fetch_text(page, "/sitemap.xml")
        debug["sitemap"].append({"url": "/sitemap.xml", "status": sm.get("status"),
                                  "head": (sm.get("body") or "")[:600]})
        sub_sitemaps = re.findall(r"<loc>([^<]+)</loc>", sm.get("body") or "")
        debug["sitemap_subs_found"] = len(sub_sitemaps)
        debug["sitemap_subs_sample"] = sub_sitemaps[:5]

        cat_sitemaps = [s for s in sub_sitemaps if "categor" in s.lower() or "lista" in s.lower()]
        for sub_url in cat_sitemaps[:5]:
            r = await fetch_text(page, sub_url)
            debug["sitemap"].append({"url": sub_url, "status": r.get("status"),
                                      "head": (r.get("body") or "")[:300]})
            for url in re.findall(r"<loc>([^<]+)</loc>", r.get("body") or ""):
                m = CAT_URL_RE.search(url)
                if m:
                    cid = m.group(1); slug = m.group(2) or ""
                    cats.setdefault(cid, {"cat_id": cid, "slug": slug,
                                          "url": url, "source": "sitemap"})

        # 2. Home: links a /tottus-pe/lista/CATG.../...
        home_links = await page.evaluate("""() => {
            const out = [];
            for (const a of document.querySelectorAll('a[href*="/tottus-pe/lista/"]')) {
                out.push({href: a.href, text: (a.textContent || '').trim().slice(0, 80)});
            }
            return out;
        }""")
        debug["home_links"] = home_links[:200]
        for hl in home_links:
            m = CAT_URL_RE.search(hl["href"])
            if m:
                cid = m.group(1); slug = m.group(2) or ""
                if cid not in cats:
                    cats[cid] = {"cat_id": cid, "slug": slug, "url": hl["href"],
                                  "source": "home", "menu_text": hl["text"]}

        # 3. abrir/hover menus del header para descubrir categorias
        # Tottus suele tener un menu "Marcas/Categorias". Probar abrirlo via boton.
        try:
            await page.click("text=Categorías", timeout=3000)
            await asyncio.sleep(2)
        except Exception:
            pass

        more = await page.evaluate("""() => {
            const out = [];
            for (const a of document.querySelectorAll('a[href*="/tottus-pe/lista/"]')) {
                out.push({href: a.href, text: (a.textContent || '').trim().slice(0, 80)});
            }
            return out;
        }""")
        debug["menu_items"] = more[:200]
        for hl in more:
            m = CAT_URL_RE.search(hl["href"])
            if m:
                cid = m.group(1); slug = m.group(2) or ""
                if cid not in cats:
                    cats[cid] = {"cat_id": cid, "slug": slug, "url": hl["href"],
                                  "source": "menu", "menu_text": hl["text"]}

        await browser.close()

    cats_list = sorted(cats.values(), key=lambda c: c["cat_id"])
    OUT.write_text(json.dumps(cats_list, indent=2, ensure_ascii=False))
    DBG.write_text(json.dumps(debug, indent=2, ensure_ascii=False))
    print(f"✓ {len(cats_list)} categorias unicas -> {OUT.name}")
    print(f"  fuentes:", {k: sum(1 for c in cats_list if c['source'] == k)
                          for k in {c['source'] for c in cats_list}})
    print(f"  primeras 5:")
    for c in cats_list[:5]:
        print(f"    {c['cat_id']:10s} {c.get('menu_text','')[:30]:30s} {c.get('slug','')[:30]}")


if __name__ == "__main__":
    asyncio.run(main())

"""
02_explore_dom.py
=================
Explora el DOM de una pagina de listado de Tottus para identificar:
  - selectores de tarjetas de producto
  - paginacion (URL pattern, total de paginas)
  - estructura interna del card (titulo, precio, marca, imagen, link)

Salida:
  - dom_findings.json con la informacion estructurada.
  - dom_pod_sample.html con el HTML de un card para inspeccion.
"""

import asyncio
import json
from pathlib import Path
from playwright.async_api import async_playwright

OUT_JSON = Path(__file__).parent / "dom_findings.json"
OUT_HTML = Path(__file__).parent / "dom_pod_sample.html"

URL = "https://www.tottus.com.pe/tottus-pe/lista/CATG17609/Pastas-Especiales"


async def main():
    findings = {"url": URL, "pages": []}

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        ctx = await browser.new_context(
            locale="es-PE",
            user_agent=("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                        "AppleWebKit/537.36 Chrome/121.0.0.0 Safari/537.36"),
        )
        page = await ctx.new_page()

        # Pagina 1
        await page.goto(URL, timeout=60000)
        await asyncio.sleep(6)

        page1 = await page.evaluate("""() => {
            const sel_counts = {};
            for (const sel of ['[data-pod]','[data-testid=ssr-pod]','article','[class*=Pod]']) {
                sel_counts[sel] = document.querySelectorAll(sel).length;
            }
            // Resultados text
            const allLeaves = Array.from(document.querySelectorAll('*'))
                .filter(e => e.children.length === 0);
            const resultados = allLeaves
                .filter(e => /Resultados/i.test(e.textContent || ''))
                .map(e => e.textContent.trim())[0] || null;

            // Pagination
            const paginationEl = document.querySelector('[class*=agination]');
            const pageLinks = Array.from(document.querySelectorAll('a'))
                .filter(a => /[?&]page=/i.test(a.href))
                .slice(0, 10)
                .map(a => a.href);

            // Sample pod (first)
            const pod = document.querySelector('[data-pod=catalyst-pod]');
            const podHTML = pod ? pod.outerHTML : null;

            // Extract data from first 3 pods to validate selectors
            const pods = Array.from(document.querySelectorAll('[data-pod=catalyst-pod]')).slice(0, 3);
            const samples = pods.map(p => ({
                href: p.href || p.getAttribute('href'),
                productId: p.getAttribute('data-key'),
                category: p.getAttribute('data-category'),
                sponsored: p.getAttribute('data-sponsored'),
                brand: p.querySelector('.pod-title')?.textContent?.trim(),
                name: p.querySelector('.pod-subTitle')?.textContent?.trim(),
                packageInfo: p.querySelector('.pod-subtitle-unit')?.textContent?.trim(),
                seller: p.querySelector('.pod-sellerText')?.textContent?.trim(),
                priceInternet: p.querySelector('[data-internet-price]')?.getAttribute('data-internet-price'),
                priceCMR: p.querySelector('[data-cmr-price]')?.getAttribute('data-cmr-price'),
                priceNormal: p.querySelector('[data-normal-price]')?.getAttribute('data-normal-price'),
                imageUrl: p.querySelector('img')?.src,
            }));

            return {
                sel_counts,
                resultados,
                paginationHTML: paginationEl?.outerHTML?.slice(0, 800),
                pageLinks,
                podHTML,
                samples,
            };
        }""")

        findings["pages"].append({"page": 1, "url": page.url, **{k: v for k, v in page1.items() if k != 'podHTML'}})
        if page1.get("podHTML"):
            OUT_HTML.write_text(page1["podHTML"])

        # Pagina 2
        await page.goto(URL + "?page=2", timeout=60000)
        await asyncio.sleep(5)
        page2 = await page.evaluate("""() => ({
            podCount: document.querySelectorAll('[data-pod=catalyst-pod]').length,
            firstId: document.querySelector('[data-pod=catalyst-pod]')?.getAttribute('data-key'),
            url: location.href,
        })""")
        findings["pages"].append({"page": 2, **page2})

        await browser.close()

    OUT_JSON.write_text(json.dumps(findings, indent=2, ensure_ascii=False))
    print(f"✓ {OUT_JSON.name}")
    print(f"✓ {OUT_HTML.name}")
    # resumen
    p1 = findings["pages"][0]
    print(f"\nP1 selectors:        {p1['sel_counts']}")
    print(f"P1 resultados:       {p1['resultados']}")
    print(f"P1 paginationLinks:  {p1['pageLinks']}")
    print(f"P1 sample[0]:")
    if p1.get("samples"):
        for k, v in p1["samples"][0].items():
            print(f"  {k:15s}: {str(v)[:100]}")
    p2 = findings["pages"][1]
    print(f"\nP2 podCount={p2['podCount']} firstId={p2['firstId']} url={p2['url']}")


if __name__ == "__main__":
    asyncio.run(main())

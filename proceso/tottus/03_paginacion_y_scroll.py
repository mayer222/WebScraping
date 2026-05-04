"""
03_paginacion_y_scroll.py
=========================
Determina la mecanica de paginacion de Tottus:
  - cuantos productos por pagina entrega ?page=N
  - si hace falta scroll para que aparezcan todos los pods
  - si los recomendados/cross-sell se mezclan con productos del listado

Salida:
  - paginacion_findings.json
"""

import asyncio
import json
from pathlib import Path
from playwright.async_api import async_playwright

OUT = Path(__file__).parent / "paginacion_findings.json"

URL_BASE = "https://www.tottus.com.pe/tottus-pe/lista/CATG17609/Pastas-Especiales"


async def contar(page):
    return await page.evaluate("""() => {
        const pods = Array.from(document.querySelectorAll('a[data-pod=catalyst-pod]'));
        const ssr = document.querySelectorAll('[data-testid=ssr-pod]').length;
        const ids = pods.map(p => p.getAttribute('data-key'));
        // unique
        const unique_ids = Array.from(new Set(ids));
        // resultados text
        const resultados = Array.from(document.querySelectorAll('*'))
            .filter(e => e.children.length === 0 && /Resultados\\s*\\(/i.test(e.textContent || ''))
            .map(e => e.textContent.trim())[0] || null;
        return {
            allPods: pods.length,
            ssrPods: ssr,
            uniqueIds: unique_ids.length,
            firstId: ids[0],
            lastId: ids[ids.length - 1],
            resultados,
        };
    }""")


async def main():
    out = {"url_base": URL_BASE, "tests": []}

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        ctx = await browser.new_context(
            locale="es-PE",
            user_agent=("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                        "AppleWebKit/537.36 Chrome/121.0.0.0 Safari/537.36"),
        )
        page = await ctx.new_page()

        for n in [1, 2, 3]:
            url = URL_BASE + (f"?page={n}" if n > 1 else "")
            await page.goto(url, timeout=60000)
            await asyncio.sleep(5)

            antes = await contar(page)

            # scroll y volver a contar
            for _ in range(8):
                await page.evaluate("window.scrollBy(0, 1000)")
                await asyncio.sleep(0.6)
            await asyncio.sleep(2)

            despues = await contar(page)
            out["tests"].append({
                "page": n, "url": page.url,
                "antesScroll": antes, "despuesScroll": despues,
            })
            print(f"P{n}: antes={antes['allPods']:>3}/{antes['ssrPods']:>3}ssr  "
                  f"despues={despues['allPods']:>3}/{despues['ssrPods']:>3}ssr  "
                  f"unique={despues['uniqueIds']:>3}  res={despues['resultados']}")

        await browser.close()

    OUT.write_text(json.dumps(out, indent=2, ensure_ascii=False))
    print(f"\n✓ {OUT.name}")


if __name__ == "__main__":
    asyncio.run(main())

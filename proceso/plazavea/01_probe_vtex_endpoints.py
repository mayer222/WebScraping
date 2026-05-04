"""
01_probe_vtex_endpoints.py
==========================
Verifica que Plaza Vea expone la misma API VTEX que Makro y descubre el SC
(sales channel) correcto. Guarda los hallazgos en probe_results.json.

Salida:
  - probe_results.json: respuestas de cada endpoint probado.
"""

import asyncio
import json
from pathlib import Path
from playwright.async_api import async_playwright

BASE = "https://www.plazavea.com.pe"
OUT = Path(__file__).parent / "probe_results.json"


async def main():
    results = {"base": BASE, "tests": []}

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        ctx = await browser.new_context(
            locale="es-PE",
            extra_http_headers={"Referer": BASE},
        )
        page = await ctx.new_page()
        await page.goto(BASE, wait_until="domcontentloaded")
        await asyncio.sleep(3)

        # 1. Tree de categorias
        for depth in [3, 5]:
            url = f"{BASE}/api/catalog_system/pub/category/tree/{depth}"
            r = await page.evaluate(f"""async () => {{
                const r = await fetch('{url}', {{headers:{{Accept:'application/json'}}}});
                const t = await r.text();
                return {{status: r.status, len: t.length, head: t.slice(0, 250)}};
            }}""")
            results["tests"].append({"endpoint": url, **r})

        # 2. Search con varios sales channels y paths
        for path in ["/2/", "/2/6/", "/2/6/950/"]:
            for sc in [None, 1, 2, 3]:
                qs = f"?fq=C:{path}&_from=0&_to=2"
                if sc is not None:
                    qs += f"&sc={sc}"
                url = f"{BASE}/api/catalog_system/pub/products/search{qs}"
                r = await page.evaluate(f"""async () => {{
                    const r = await fetch('{url}', {{headers:{{Accept:'application/json'}}}});
                    const t = await r.text();
                    return {{status: r.status, len: t.length, head: t.slice(0, 200)}};
                }}""")
                results["tests"].append({
                    "endpoint": url,
                    "path": path,
                    "sc": sc,
                    **r,
                })

        await browser.close()

    OUT.write_text(json.dumps(results, indent=2, ensure_ascii=False))
    print(f"✓ guardado en {OUT}")
    # Resumen humano
    for t in results["tests"]:
        print(f'  {t["status"]} len={t["len"]:>6} {t["endpoint"][-70:]}')


if __name__ == "__main__":
    asyncio.run(main())

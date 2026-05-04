"""
01_probe_endpoints.py
=====================
Confirma si Tottus expone una API JSON de catalogo. Reusa
api_endpoint_extractor.py (root del repo) navegando a una pagina de listado
y guarda los endpoints capturados.

Salida:
  - probe_endpoints.json: catalogo de endpoints capturados.
  - probe_endpoints.txt:  resumen humano (metodo + URL truncada).
"""

import asyncio
import json
import sys
from pathlib import Path

ROOT = Path(__file__).parents[2]   # WebScraping/
sys.path.insert(0, str(ROOT))

from api_endpoint_extractor import APIEndpointExtractor

OUT_JSON = Path(__file__).parent / "probe_endpoints.json"
OUT_TXT = Path(__file__).parent / "probe_endpoints.txt"

URL_LISTADO = "https://www.tottus.com.pe/tottus-pe/lista/CATG17659/Frutas"


async def main():
    ex = APIEndpointExtractor(headless=True)
    result = await ex.extract(URL_LISTADO, auto_scroll=True, click_buttons=False)

    OUT_JSON.write_text(json.dumps(result, indent=2, ensure_ascii=False))
    print(f"✓ {result['total_endpoints']} endpoints -> {OUT_JSON.name}")

    lines = []
    for ep in result["endpoints"]:
        lines.append(f"{ep['method']:5s} {ep['domain']}{ep['path']} | qkeys={ep.get('query_keys')}")
    OUT_TXT.write_text("\n".join(lines))
    for l in lines:
        print(l)


if __name__ == "__main__":
    asyncio.run(main())

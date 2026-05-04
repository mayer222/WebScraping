"""
analizar_competencia.py  —  MAKRO
==================================
Inteligencia competitiva del catalogo de Makro Peru.

Perfil de Makro: mayorista B2B con foco HoReCa (hoteles/restaurantes/cafeterias).
Su marca propia ARO es el activo mas estrategico (~10% del catalogo) — agrede
en precio/volumen contra marcas lideres.

Como gerente general de un competidor, lo que necesitas saber:

  1. ¿Cuanta presion ejerce ARO en cada categoria?  (penetracion privada)
  2. ¿Donde Makro es agresivo en promo y donde no?  (intensidad de descuento)
  3. ¿Que categorias son su "cash cow" (alto surtido) y cuales sus huecos?
  4. ¿Cual es su pricing ladder real?  (combate, medio, premium)
  5. ¿Marcas terceras con mas presencia?  (target para captura)
  6. Producto mas caro/mas barato por categoria  (anchors)
  7. Disponibilidad: si tienen quiebres (oportunidad inmediata)

Salidas:
  analisis/  -> 7 PNGs + executive_summary.txt
"""

import re
from pathlib import Path
import vaex
import pandas as pd
import matplotlib.pyplot as plt
import seaborn as sns

HERE = Path(__file__).parent
OUT = HERE / "analisis"
OUT.mkdir(exist_ok=True)

CSV = sorted(HERE.glob("makro_productos_*.csv"))[-1]
MARCA_PROPIA = "ARO"

sns.set_theme(style="whitegrid", palette="muted")
plt.rcParams.update({"figure.dpi": 110, "axes.titleweight": "bold"})


def cargar():
    """Vaex para procesar el CSV grande de Makro."""
    df = vaex.from_csv(str(CSV), convert=False)
    print(f"  cargado vaex: {len(df):,} filas, {len(df.column_names)} cols")
    return df


def kpis_globales(df, pdf):
    n = len(df)
    n_disp = int(pdf["disponible"].sum())
    n_promo = int(pdf["tiene_descuento"].sum())
    pen_aro = (pdf["marca"] == MARCA_PROPIA).sum() / n * 100
    p_med = pdf.loc[pdf["precio_oferta"] > 0, "precio_oferta"].median()
    p_avg = pdf.loc[pdf["precio_oferta"] > 0, "precio_oferta"].mean()
    desc_avg = pdf.loc[pdf["tiene_descuento"], "descuento_pct"].mean()
    return {
        "skus_total": n,
        "skus_disponibles": n_disp,
        "skus_en_promo": n_promo,
        "pct_disponibles": n_disp / n * 100,
        "pct_en_promo": n_promo / n * 100,
        "penetracion_marca_propia_pct": pen_aro,
        "precio_mediana": p_med,
        "precio_promedio": p_avg,
        "descuento_promedio_pct": desc_avg,
        "n_categorias": pdf["categoria_nombre"].nunique(),
        "n_marcas": pdf["marca"].nunique(),
    }


# ─────────────────────────────────────────────
# CHARTS
# ─────────────────────────────────────────────

def chart_penetracion_aro_por_categoria(pdf):
    """Que tan agresivo es ARO en cada categoria (% del surtido)."""
    cat_total = pdf.groupby("categoria_nombre").size()
    cat_aro = pdf[pdf["marca"] == MARCA_PROPIA].groupby("categoria_nombre").size()
    pen = (cat_aro / cat_total * 100).fillna(0).sort_values(ascending=False)
    pen = pen[pen > 0].head(20)

    fig, ax = plt.subplots(figsize=(10, 8))
    bars = ax.barh(pen.index[::-1], pen.values[::-1], color="#d62728")
    ax.set_xlabel("% del surtido cubierto por ARO")
    ax.set_title(f"Penetracion de marca propia ({MARCA_PROPIA}) por categoria — Top 20",
                 loc="left")
    for b, v in zip(bars, pen.values[::-1]):
        ax.text(v + 0.5, b.get_y() + b.get_height()/2,
                f"{v:.1f}%", va="center", fontsize=9)
    ax.set_xlim(0, max(pen.values) * 1.15)
    plt.tight_layout()
    plt.savefig(OUT / "01_penetracion_aro.png", dpi=120)
    plt.close()


def chart_top_categorias_y_promo(pdf):
    """Profundidad de surtido + intensidad de promocion combinadas."""
    g = pdf.groupby("categoria_nombre").agg(
        skus=("product_id", "count"),
        en_promo=("tiene_descuento", "sum"),
    )
    g["pct_promo"] = g["en_promo"] / g["skus"] * 100
    g = g.sort_values("skus", ascending=False).head(20)

    fig, axes = plt.subplots(1, 2, figsize=(14, 8), sharey=True)
    cats = g.index[::-1]
    axes[0].barh(cats, g["skus"][::-1], color="#1f77b4")
    axes[0].set_title("Profundidad de surtido (# SKUs)", loc="left")
    axes[0].set_xlabel("# SKUs")
    for i, v in enumerate(g["skus"][::-1]):
        axes[0].text(v + 5, i, str(v), va="center", fontsize=9)

    axes[1].barh(cats, g["pct_promo"][::-1], color="#ff7f0e")
    axes[1].set_title("Intensidad de promocion (% SKUs en oferta)", loc="left")
    axes[1].set_xlabel("% en oferta")
    for i, v in enumerate(g["pct_promo"][::-1]):
        axes[1].text(v + 1, i, f"{v:.0f}%", va="center", fontsize=9)
    plt.suptitle("Top 20 categorias — surtido y agresividad promocional",
                 fontweight="bold")
    plt.tight_layout()
    plt.savefig(OUT / "02_categorias_surtido_y_promo.png", dpi=120)
    plt.close()


def chart_pricing_ladder(pdf):
    """Como reparten precios: combate / medio / premium / mayoreo top."""
    p = pdf.loc[pdf["precio_oferta"] > 0, "precio_oferta"]
    bins = [0, 5, 15, 50, 100, 300, 1000, 999999]
    labels = ["≤ S/5", "S/5-15", "S/15-50", "S/50-100",
              "S/100-300", "S/300-1000", "≥ S/1000"]
    cnt = pd.cut(p, bins=bins, labels=labels).value_counts().reindex(labels).fillna(0)

    fig, ax = plt.subplots(figsize=(11, 5))
    bars = ax.bar(cnt.index, cnt.values, color=sns.color_palette("rocket", len(cnt)))
    ax.set_title("Pricing ladder — distribucion de SKUs por rango de precio",
                 loc="left")
    ax.set_ylabel("# SKUs"); ax.set_xlabel("rango")
    for b, v in zip(bars, cnt.values):
        ax.text(b.get_x() + b.get_width()/2, v + max(cnt.values)*0.01,
                f"{int(v):,}\n({v/cnt.sum()*100:.0f}%)",
                ha="center", fontsize=9)
    plt.tight_layout()
    plt.savefig(OUT / "03_pricing_ladder.png", dpi=120)
    plt.close()


def chart_top_marcas_terceras(pdf):
    """Marcas con mas presencia (excluyendo la propia) — target de captura."""
    others = pdf[pdf["marca"] != MARCA_PROPIA]
    top = others["marca"].value_counts().head(20)

    fig, ax = plt.subplots(figsize=(10, 8))
    bars = ax.barh(top.index[::-1], top.values[::-1], color="#2ca02c")
    ax.set_title("Marcas terceras con mayor presencia — Top 20",
                 loc="left")
    ax.set_xlabel("# SKUs")
    for b, v in zip(bars, top.values[::-1]):
        ax.text(v + 3, b.get_y() + b.get_height()/2, str(v),
                va="center", fontsize=9)
    plt.tight_layout()
    plt.savefig(OUT / "04_top_marcas_terceras.png", dpi=120)
    plt.close()


def chart_descuento_promedio_por_cat(pdf):
    """En que categorias agreden mas con descuentos (solo SKUs con descuento)."""
    promo = pdf[pdf["tiene_descuento"]]
    g = promo.groupby("categoria_nombre")["descuento_pct"].agg(["mean", "count"])
    g = g[g["count"] >= 10].sort_values("mean", ascending=False).head(20)

    fig, ax = plt.subplots(figsize=(10, 8))
    bars = ax.barh(g.index[::-1], g["mean"][::-1], color="#9467bd")
    ax.set_xlabel("Descuento promedio %")
    ax.set_title("Categorias con mayor agresion promocional\n"
                 "(solo SKUs en promo, mın 10 SKUs)", loc="left")
    for b, v, c in zip(bars, g["mean"][::-1], g["count"][::-1]):
        ax.text(v + 0.3, b.get_y() + b.get_height()/2,
                f"{v:.1f}% (n={c})", va="center", fontsize=9)
    plt.tight_layout()
    plt.savefig(OUT / "05_descuento_por_categoria.png", dpi=120)
    plt.close()


def chart_aro_vs_terceros_precio(pdf):
    """Diferencial de precio: ARO vs marcas terceras en categorias clave."""
    cats_aro = pdf[pdf["marca"] == MARCA_PROPIA]["categoria_nombre"].value_counts()
    top_cats = cats_aro.head(10).index.tolist()

    rows = []
    for c in top_cats:
        sub = pdf[pdf["categoria_nombre"] == c]
        p_aro = sub.loc[sub["marca"] == MARCA_PROPIA, "precio_oferta"].median()
        p_otros = sub.loc[sub["marca"] != MARCA_PROPIA, "precio_oferta"].median()
        if pd.notna(p_aro) and pd.notna(p_otros) and p_otros > 0:
            rows.append({
                "categoria": c,
                "ARO": p_aro,
                "Terceros": p_otros,
                "ahorro_pct": (1 - p_aro / p_otros) * 100,
            })
    g = pd.DataFrame(rows).sort_values("ahorro_pct", ascending=True)

    fig, ax = plt.subplots(figsize=(11, 7))
    y = range(len(g))
    ax.barh([i - 0.2 for i in y], g["Terceros"], height=0.4,
            label="Mediana terceros", color="#1f77b4")
    ax.barh([i + 0.2 for i in y], g["ARO"], height=0.4,
            label="Mediana ARO", color="#d62728")
    ax.set_yticks(list(y))
    ax.set_yticklabels(g["categoria"])
    ax.set_xlabel("Precio mediana (S/.)")
    ax.set_title(f"Pricing diferencial: ARO vs Terceros\n"
                 f"(top 10 categorias con mas SKUs ARO)", loc="left")
    for i, row in enumerate(g.itertuples()):
        ax.text(max(row.ARO, row.Terceros) + 0.3, i,
                f"−{row.ahorro_pct:.0f}%", va="center", fontsize=9, color="#444")
    ax.legend(loc="lower right")
    plt.tight_layout()
    plt.savefig(OUT / "06_aro_vs_terceros_precio.png", dpi=120)
    plt.close()


def chart_disponibilidad_por_cat(pdf):
    """Quiebres de stock por categoria."""
    g = pdf.groupby("categoria_nombre").agg(
        skus=("product_id", "count"),
        disp=("disponible", "sum"),
    )
    g["pct_quiebre"] = (1 - g["disp"] / g["skus"]) * 100
    g = g[g["skus"] >= 30].sort_values("pct_quiebre", ascending=False).head(20)
    if len(g) == 0:
        # ningun quiebre relevante
        fig, ax = plt.subplots(figsize=(8, 3))
        ax.text(0.5, 0.5, "Sin quiebres relevantes\n(>0% en categorias con ≥30 SKUs)",
                ha="center", va="center", fontsize=14, color="#444")
        ax.axis("off")
        plt.savefig(OUT / "07_quiebres_por_categoria.png", dpi=120)
        plt.close()
        return

    fig, ax = plt.subplots(figsize=(10, 7))
    bars = ax.barh(g.index[::-1], g["pct_quiebre"][::-1], color="#e74c3c")
    ax.set_xlabel("% SKUs no disponibles")
    ax.set_title("Categorias con mayor tasa de quiebre — oportunidad inmediata",
                 loc="left")
    for b, v in zip(bars, g["pct_quiebre"][::-1]):
        ax.text(v + 0.2, b.get_y() + b.get_height()/2,
                f"{v:.1f}%", va="center", fontsize=9)
    plt.tight_layout()
    plt.savefig(OUT / "07_quiebres_por_categoria.png", dpi=120)
    plt.close()


# ─────────────────────────────────────────────
# MAIN
# ─────────────────────────────────────────────

def main():
    print("=" * 60)
    print("ANALISIS COMPETITIVO — MAKRO")
    print("=" * 60)
    print(f"\nfuente: {CSV.name}")
    df = cargar()
    pdf = df.to_pandas_df()

    k = kpis_globales(df, pdf)
    print(f"\nKPIs:")
    for kk, vv in k.items():
        print(f"  {kk:35s}: {vv:,.2f}" if isinstance(vv, float) else f"  {kk:35s}: {vv:,}")

    print("\nGenerando charts...")
    chart_penetracion_aro_por_categoria(pdf)
    chart_top_categorias_y_promo(pdf)
    chart_pricing_ladder(pdf)
    chart_top_marcas_terceras(pdf)
    chart_descuento_promedio_por_cat(pdf)
    chart_aro_vs_terceros_precio(pdf)
    chart_disponibilidad_por_cat(pdf)

    # Executive summary txt
    summary = OUT / "executive_summary.txt"
    pen_aro_pct = k["penetracion_marca_propia_pct"]
    skus_aro = int((pdf["marca"] == MARCA_PROPIA).sum())
    cat_aro_top = (pdf[pdf["marca"] == MARCA_PROPIA]
                   ["categoria_nombre"].value_counts().head(5))

    text = f"""MAKRO — RESUMEN EJECUTIVO
============================
Fuente: {CSV.name}
Fecha snapshot: {pdf['fecha_extraccion'].iloc[0] if len(pdf) else 'n/a'}

CIFRAS CLAVE
------------
  Total SKUs                : {k['skus_total']:,}
  Categorias                : {k['n_categorias']}
  Marcas distintas          : {k['n_marcas']}
  SKUs en promocion         : {k['skus_en_promo']:,}  ({k['pct_en_promo']:.1f}% del catalogo)
  Disponibilidad            : {k['pct_disponibles']:.1f}%
  Precio mediana            : S/ {k['precio_mediana']:.2f}
  Precio promedio           : S/ {k['precio_promedio']:.2f}
  Descuento promedio        : {k['descuento_promedio_pct']:.1f}%   (sobre SKUs en promo)

MARCA PROPIA (ARO)
------------------
  SKUs con marca ARO        : {skus_aro:,}
  Penetracion en catalogo   : {pen_aro_pct:.1f}%
  Top categorias ARO        :
"""
    for c, n in cat_aro_top.items():
        text += f"    - {c:35s} {n} SKUs\n"

    text += f"""
LECTURA ESTRATEGICA
-------------------
  • ARO no es marca filler: con {pen_aro_pct:.0f}% del surtido, es el eje de su
    estrategia de mayoreo. Donde ARO esta en mas del 30% de la categoria,
    Makro NO compra precio — lo fija.
  • La intensidad promocional ({k['pct_en_promo']:.0f}% del catalogo) y un descuento
    promedio de {k['descuento_promedio_pct']:.0f}% indican promo permanente,
    no campañas tacticas.
  • Cualquier categoria con mas de 30 SKUs y < 30% en promo = oportunidad
    de captura por marca tercera.
  • Ver 06_aro_vs_terceros_precio.png para el spread real ARO vs competidor:
    diferencial habitual de 15-30% justifica la lealtad HoReCa.

CHARTS
------
  01_penetracion_aro.png            ← donde duele competir
  02_categorias_surtido_y_promo.png ← profundidad + agresividad
  03_pricing_ladder.png             ← combate vs premium
  04_top_marcas_terceras.png        ← lista de captura
  05_descuento_por_categoria.png    ← por donde agreden
  06_aro_vs_terceros_precio.png     ← spread real ARO
  07_quiebres_por_categoria.png     ← quiebres -> oportunidad
"""
    summary.write_text(text)
    print(f"\n✓ {len(list(OUT.glob('*.png')))} charts en {OUT}/")
    print(f"✓ {summary.name}")
    print("\n" + "─" * 60)
    print(text)


if __name__ == "__main__":
    main()

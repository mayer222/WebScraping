"""
analizar_competencia.py  —  PLAZA VEA
=====================================
Inteligencia competitiva del catalogo de Plaza Vea Peru.

Perfil: cadena de retail B2C, hipermercados/supermercados con catalogo
amplio. Marcas propias historicas: BELL'S, BOREAL, HAGEN, LA FLORENCIA
(linea casa), HOOME DECO. Tambien linea "GENERICO" (sin marca, masivo).
Su monetizacion incluye Retail Media (productClusters > 25 = saturacion
de plataformas promocionales).

Como gerente general de un competidor, lo que necesitas saber:

  1. ¿Que tan profunda es la canibalizacion de marca propia/genericos?
  2. ¿Que tan saturado esta el espacio promocional? (productClusters)
  3. ¿Donde concentran SKUs vs donde son superficiales?
  4. ¿Cual es el diferencial precio entre marca propia y terceros?
  5. ¿Donde agreden con descuento?  (mas alla del cluster promocional)
  6. ¿Marcas terceras dominantes — target de captura.
  7. Pricing ladder: ¿son combate, medio o premium?

Salidas:
  analisis/  -> 7 PNGs + executive_summary.txt
"""

from pathlib import Path
import vaex
import pandas as pd
import matplotlib.pyplot as plt
import seaborn as sns

HERE = Path(__file__).parent
OUT = HERE / "analisis"
OUT.mkdir(exist_ok=True)

CSV = sorted(HERE.glob("plazavea_productos_*.csv"))[-1]

# Marcas propias / genericos identificados
MARCAS_PROPIAS = {"BELL'S", "BOREAL", "HAGEN", "HOOME DECO", "LA FLORENCIA",
                  "GENÉRICO", "GENERICO"}

sns.set_theme(style="whitegrid", palette="muted")
plt.rcParams.update({"figure.dpi": 110, "axes.titleweight": "bold"})


def _figsize_horiz(n_rows, width=11, per_row=0.28, min_h=4):
    return (width, max(min_h, n_rows * per_row))


def _font_for(n_rows):
    if n_rows <= 25:  return 10
    if n_rows <= 50:  return 8
    if n_rows <= 100: return 7
    return 6


def cargar():
    df = vaex.from_csv(str(CSV), convert=False)
    print(f"  cargado vaex: {len(df):,} filas")
    return df


def kpis_globales(df, pdf):
    n = len(df)
    n_disp = int(pdf["disponible"].sum())
    n_promo = int(pdf["tiene_descuento"].sum())
    pen_propia = pdf["marca"].isin(MARCAS_PROPIAS).sum() / n * 100
    p_med = pdf.loc[pdf["precio_oferta"] > 0, "precio_oferta"].median()
    p_avg = pdf.loc[pdf["precio_oferta"] > 0, "precio_oferta"].mean()
    desc_avg = pdf.loc[pdf["tiene_descuento"], "descuento_pct"].mean()
    cluster_avg = pdf["num_colecciones"].mean()
    return {
        "skus_total": n,
        "skus_disponibles": n_disp,
        "skus_en_promo": n_promo,
        "pct_disponibles": n_disp / n * 100,
        "pct_en_promo": n_promo / n * 100,
        "penetracion_marca_propia_pct": pen_propia,
        "precio_mediana": p_med,
        "precio_promedio": p_avg,
        "descuento_promedio_pct": desc_avg,
        "n_categorias": pdf["categoria_nombre"].nunique(),
        "n_marcas": pdf["marca"].nunique(),
        "clusters_promedio_por_sku": cluster_avg,
    }


# ─────────────────────────────────────────────
# CHARTS
# ─────────────────────────────────────────────

def chart_penetracion_propia_por_categoria(pdf):
    cat_total = pdf.groupby("categoria_nombre").size()
    cat_propia = pdf[pdf["marca"].isin(MARCAS_PROPIAS)] \
                    .groupby("categoria_nombre").size()
    pen = (cat_propia / cat_total * 100).fillna(0).sort_values(ascending=False)
    pen = pen[pen > 0]
    n = len(pen)
    fs = _font_for(n)

    fig, ax = plt.subplots(figsize=_figsize_horiz(n, width=11))
    bars = ax.barh(pen.index[::-1], pen.values[::-1], color="#d62728")
    ax.set_xlabel("% del surtido cubierto por marcas propias")
    ax.set_title(f"Penetracion de marca propia por categoria\n"
                 f"({n} categorias con presencia, ordenadas por % penetracion)\n"
                 f"BELL'S, BOREAL, HAGEN, HOOME DECO, LA FLORENCIA, GENERICO",
                 loc="left", fontsize=11)
    ax.tick_params(axis="y", labelsize=fs)
    for b, v in zip(bars, pen.values[::-1]):
        ax.text(v + 0.5, b.get_y() + b.get_height()/2,
                f"{v:.1f}%", va="center", fontsize=fs)
    plt.tight_layout()
    plt.savefig(OUT / "01_penetracion_marca_propia.png", dpi=120)
    plt.close()


def chart_top_categorias_y_promo(pdf):
    g = pdf.groupby("categoria_nombre").agg(
        skus=("product_id", "count"),
        en_promo=("tiene_descuento", "sum"),
    )
    g["pct_promo"] = g["en_promo"] / g["skus"] * 100
    g = g.sort_values("skus", ascending=False)
    n = len(g)
    fs = _font_for(n)

    h = max(5, n * 0.22)
    fig, axes = plt.subplots(1, 2, figsize=(15, h), sharey=True)
    cats = g.index[::-1]
    axes[0].barh(cats, g["skus"][::-1], color="#1f77b4")
    axes[0].set_title("Profundidad de surtido (# SKUs)", loc="left")
    axes[0].set_xlabel("# SKUs")
    axes[0].tick_params(axis="y", labelsize=fs)
    for i, v in enumerate(g["skus"][::-1]):
        axes[0].text(v + max(g["skus"])*0.005, i, str(v),
                     va="center", fontsize=fs)

    axes[1].barh(cats, g["pct_promo"][::-1], color="#ff7f0e")
    axes[1].set_title("Intensidad de promocion (% SKUs en oferta)", loc="left")
    axes[1].set_xlabel("% en oferta")
    for i, v in enumerate(g["pct_promo"][::-1]):
        axes[1].text(v + 0.5, i, f"{v:.0f}%", va="center", fontsize=fs)
    plt.suptitle(f"Surtido y agresividad promocional — TODAS las categorias "
                 f"({n})  •  ordenadas por # SKUs",
                 fontweight="bold", fontsize=12)
    plt.tight_layout()
    plt.savefig(OUT / "02_categorias_surtido_y_promo.png", dpi=120)
    plt.close()


def chart_pricing_ladder(pdf):
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


def chart_pricing_ladder_por_categoria(pdf):
    """Heatmap categoria x rango de precio."""
    bins = [0, 5, 15, 50, 100, 300, 1000, 999999]
    labels = ["≤5", "5-15", "15-50", "50-100", "100-300", "300-1k", "≥1k"]

    p = pdf.loc[pdf["precio_oferta"] > 0,
                ["categoria_nombre", "precio_oferta"]].copy()
    p["bin"] = pd.cut(p["precio_oferta"], bins=bins, labels=labels)

    cats_ord = (p.groupby("categoria_nombre").size()
                  .sort_values(ascending=False).index)
    ct = pd.crosstab(p["categoria_nombre"], p["bin"], normalize="index") * 100
    ct = ct.reindex(cats_ord)
    counts = p.groupby("categoria_nombre").size().reindex(cats_ord)
    ct.index = [f"{c}  (n={counts[c]})" for c in ct.index]

    n = len(ct)
    fs = _font_for(n)
    h = max(6, n * 0.30)
    fig, ax = plt.subplots(figsize=(14, h))
    sns.heatmap(ct, annot=True, fmt=".0f", cmap="rocket_r",
                annot_kws={"size": fs - 1},
                cbar_kws={"label": "% SKUs en el rango"},
                linewidths=0.5, linecolor="white", ax=ax)
    ax.set_title(f"Pricing ladder por categoria — % SKUs en cada rango (S/.)\n"
                 f"(TODAS las {n} categorias, ordenadas por # SKUs)",
                 loc="left", fontsize=11)
    ax.set_xlabel("Rango de precio (S/.)")
    ax.set_ylabel("")
    ax.tick_params(axis="y", labelsize=fs)
    plt.tight_layout()
    plt.savefig(OUT / "03b_pricing_ladder_por_categoria.png", dpi=120)
    plt.close()


def chart_top_marcas_terceras(pdf):
    others = pdf[~pdf["marca"].isin(MARCAS_PROPIAS)]
    top = others["marca"].value_counts().head(50)
    n = len(top)
    fs = _font_for(n)

    fig, ax = plt.subplots(figsize=_figsize_horiz(n, width=11, per_row=0.25))
    bars = ax.barh(top.index[::-1], top.values[::-1], color="#2ca02c")
    ax.set_title(f"Marcas terceras con mayor presencia — Top {n}\n"
                 f"(target de captura / negociacion)", loc="left", fontsize=11)
    ax.set_xlabel("# SKUs")
    ax.tick_params(axis="y", labelsize=fs)
    for b, v in zip(bars, top.values[::-1]):
        ax.text(v + max(top)*0.005, b.get_y() + b.get_height()/2, str(v),
                va="center", fontsize=fs)
    plt.tight_layout()
    plt.savefig(OUT / "04_top_marcas_terceras.png", dpi=120)
    plt.close()


def chart_saturacion_promocional(pdf):
    """numero de productClusters por SKU = saturacion de plataformas
    promocionales. TODAS las categorias con al menos 5 SKUs."""
    g = pdf.groupby("categoria_nombre")["num_colecciones"].agg(["mean", "count"])
    g = g[g["count"] >= 5].sort_values("mean", ascending=False)
    n = len(g)
    fs = _font_for(n)

    fig, ax = plt.subplots(figsize=_figsize_horiz(n, width=12, per_row=0.28))
    bars = ax.barh(g.index[::-1], g["mean"][::-1], color="#9467bd")
    ax.set_xlabel("# productClusters promedio por SKU")
    ax.set_title(f"Saturacion promocional / retail-media por categoria\n"
                 f"(TODAS las {n} categorias con ≥5 SKUs)",
                 loc="left", fontsize=11)
    ax.tick_params(axis="y", labelsize=fs)
    for b, v, c in zip(bars, g["mean"][::-1], g["count"][::-1]):
        ax.text(v + 0.1, b.get_y() + b.get_height()/2,
                f"{v:.1f}  (n={c})", va="center", fontsize=fs)
    plt.tight_layout()
    plt.savefig(OUT / "05_saturacion_promocional.png", dpi=120)
    plt.close()


def chart_descuento_promedio_por_cat(pdf):
    promo = pdf[pdf["tiene_descuento"]]
    if len(promo) == 0:
        return
    g = promo.groupby("categoria_nombre")["descuento_pct"].agg(["mean", "count"])
    g = g[g["count"] >= 5].sort_values("mean", ascending=False)
    n = len(g)
    fs = _font_for(n)

    fig, ax = plt.subplots(figsize=_figsize_horiz(n, width=12, per_row=0.28))
    bars = ax.barh(g.index[::-1], g["mean"][::-1], color="#e67e22")
    ax.set_xlabel("Descuento promedio %")
    ax.set_title(f"Categorias con mayor agresion promocional\n"
                 f"(TODAS las {n} cats con ≥5 SKUs en promo)",
                 loc="left", fontsize=11)
    ax.tick_params(axis="y", labelsize=fs)
    for b, v, c in zip(bars, g["mean"][::-1], g["count"][::-1]):
        ax.text(v + 0.2, b.get_y() + b.get_height()/2,
                f"{v:.1f}% (n={c})", va="center", fontsize=fs)
    plt.tight_layout()
    plt.savefig(OUT / "06_descuento_por_categoria.png", dpi=120)
    plt.close()


def chart_propia_vs_terceros_precio(pdf):
    cats_propia = pdf[pdf["marca"].isin(MARCAS_PROPIAS)] \
                    ["categoria_nombre"].value_counts()
    cats_validas = cats_propia[cats_propia >= 3].index.tolist()

    rows = []
    for c in cats_validas:
        sub = pdf[pdf["categoria_nombre"] == c]
        n_otros = (~sub["marca"].isin(MARCAS_PROPIAS)).sum()
        if n_otros < 3:
            continue
        p_propia = sub.loc[sub["marca"].isin(MARCAS_PROPIAS), "precio_oferta"].median()
        p_otros = sub.loc[~sub["marca"].isin(MARCAS_PROPIAS), "precio_oferta"].median()
        if pd.notna(p_propia) and pd.notna(p_otros) and p_otros > 0:
            rows.append({
                "categoria": c,
                "Propia": p_propia,
                "Terceros": p_otros,
                "ahorro_pct": (1 - p_propia / p_otros) * 100,
            })
    if not rows:
        return
    g = pd.DataFrame(rows).sort_values("ahorro_pct", ascending=True)
    n = len(g)
    fs = _font_for(n)

    fig, ax = plt.subplots(figsize=_figsize_horiz(n, width=12, per_row=0.32))
    y = range(n)
    ax.barh([i - 0.2 for i in y], g["Terceros"], height=0.4,
            label="Mediana terceros", color="#1f77b4")
    ax.barh([i + 0.2 for i in y], g["Propia"], height=0.4,
            label="Mediana marca propia", color="#d62728")
    ax.set_yticks(list(y))
    ax.set_yticklabels(g["categoria"], fontsize=fs)
    ax.set_xlabel("Precio mediana (S/.)")
    ax.set_title(f"Pricing diferencial: marca propia vs Terceros\n"
                 f"(TODAS las {n} cats con ≥3 SKUs propios y ≥3 terceros)",
                 loc="left", fontsize=11)
    for i, row in enumerate(g.itertuples()):
        sign = "−" if row.ahorro_pct >= 0 else "+"
        ax.text(max(row.Propia, row.Terceros) * 1.02, i,
                f"{sign}{abs(row.ahorro_pct):.0f}%",
                va="center", fontsize=fs, color="#444")
    ax.legend(loc="lower right")
    plt.tight_layout()
    plt.savefig(OUT / "07_propia_vs_terceros_precio.png", dpi=120)
    plt.close()


# ─────────────────────────────────────────────
# MAIN
# ─────────────────────────────────────────────

def main():
    print("=" * 60)
    print("ANALISIS COMPETITIVO — PLAZA VEA")
    print("=" * 60)
    print(f"\nfuente: {CSV.name}")
    df = cargar()
    pdf = df.to_pandas_df()

    k = kpis_globales(df, pdf)
    print(f"\nKPIs:")
    for kk, vv in k.items():
        print(f"  {kk:35s}: {vv:,.2f}" if isinstance(vv, float)
              else f"  {kk:35s}: {vv:,}")

    print("\nGenerando charts...")
    chart_penetracion_propia_por_categoria(pdf)
    chart_top_categorias_y_promo(pdf)
    chart_pricing_ladder(pdf)
    chart_pricing_ladder_por_categoria(pdf)
    chart_top_marcas_terceras(pdf)
    chart_saturacion_promocional(pdf)
    chart_descuento_promedio_por_cat(pdf)
    chart_propia_vs_terceros_precio(pdf)

    skus_propia = int(pdf["marca"].isin(MARCAS_PROPIAS).sum())
    cats_propia_top = (pdf[pdf["marca"].isin(MARCAS_PROPIAS)]
                       ["categoria_nombre"].value_counts().head(5))

    text = f"""PLAZA VEA — RESUMEN EJECUTIVO
==============================
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
  Clusters promo / SKU      : {k['clusters_promedio_por_sku']:.1f}  (saturacion retail-media)

MARCAS PROPIAS (BELL'S, BOREAL, HAGEN, HOOME DECO, LA FLORENCIA, GENERICO)
---------------------------------------------------------------------------
  SKUs marca propia         : {skus_propia:,}
  Penetracion en catalogo   : {k['penetracion_marca_propia_pct']:.1f}%
  Top categorias            :
"""
    for c, n in cats_propia_top.items():
        text += f"    - {c:35s} {n} SKUs\n"

    text += f"""
LECTURA ESTRATEGICA
-------------------
  • Plaza Vea opera como hipermercado de surtido amplio. La penetracion
    de marca propia ({k['penetracion_marca_propia_pct']:.0f}%) es agresiva en categorias
    no-perecibles (decoracion, mascota, limpieza); ahi compiten en
    valor, no en precio absoluto.
  • Saturacion promocional alta ({k['clusters_promedio_por_sku']:.1f} clusters/SKU promedio):
    cada producto pasa por multiples plataformas (Ofertas Oh!, landings
    de marca, retail media). Indicador de monetizacion intensa con
    proveedores via clusters/displays.
  • Descuento promedio de {k['descuento_promedio_pct']:.0f}% en {k['pct_en_promo']:.0f}% del catalogo —
    estrategia de precio dinamico, no campañas puntuales.
  • Categorias con mas de 50 SKUs propios y >40% penetracion =
    territorio defendido, no entrar a competir precio.
  • Categorias con bajo penetracion + bajo cluster promedio =
    huecos donde Plaza Vea no esta empujando, oportunidad para meterse.

CHARTS
------
  01_penetracion_marca_propia.png   ← donde concentran su marca privada
  02_categorias_surtido_y_promo.png ← profundidad y % en oferta
  03_pricing_ladder.png             ← combate vs premium (global)
  03b_pricing_ladder_por_categoria.png ← mismo, desglosado por categoria
  04_top_marcas_terceras.png        ← marcas a captar / negociar
  05_saturacion_promocional.png     ← retail media intensity por categoria
  06_descuento_por_categoria.png    ← donde agreden con descuento real
  07_propia_vs_terceros_precio.png  ← spread propia vs terceros
"""
    (OUT / "executive_summary.txt").write_text(text)
    print(f"\n✓ {len(list(OUT.glob('*.png')))} charts en {OUT}/")
    print("\n" + "─" * 60)
    print(text)


if __name__ == "__main__":
    main()

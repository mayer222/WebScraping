"""
analizar_competencia.py  —  TOTTUS
==================================
Inteligencia competitiva del catalogo de Tottus Peru.

Perfil: cadena de hipermercados del grupo Falabella. Marca propia TOTTUS
(linea masiva) y TOTTUS VERDURAS (frescos). Diferenciador unico vs Makro/
PlazaVea: precio CMR (tarjeta del grupo Falabella) — incentivo financiero
para aumentar share-of-wallet. Tambien hay flag "patrocinado" (retail
media) y "vendedor" (TOTTUS vs marketplace).

Como gerente general de un competidor, lo que necesitas saber:

  1. ¿Cuanta penetracion tiene la marca propia TOTTUS?
  2. ¿Que categorias usan precio CMR (incentivo tarjeta) y cuanto descuenta?
     → Indicador de donde estan dispuestos a sacrificar margen para mover
       cliente al ecosistema Falabella/CMR.
  3. ¿Que % es marketplace vs propio?
  4. ¿Que % esta patrocinado? (retail media)
  5. Profundidad por categoria + intensidad promocional.
  6. Pricing ladder.
  7. Marcas terceras dominantes.

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

CSV = sorted(HERE.glob("tottus_productos_*.csv"))[-1]

MARCAS_PROPIAS = {"TOTTUS", "TOTTUS VERDURAS"}

sns.set_theme(style="whitegrid", palette="muted")
plt.rcParams.update({"figure.dpi": 110, "axes.titleweight": "bold"})


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
    n_cmr = int(pdf["precio_cmr"].notna().sum())
    n_marketplace = int((pdf["vendedor"] != "Por TOTTUS").sum())
    n_patrocinado = int(pdf["patrocinado"].sum()) if "patrocinado" in pdf.columns else 0

    # diferencial CMR
    sub_cmr = pdf[pdf["precio_cmr"].notna() & (pdf["precio_cmr"] > 0) & (pdf["precio_oferta"] > 0)]
    diff_cmr = ((1 - sub_cmr["precio_cmr"] / sub_cmr["precio_oferta"]) * 100).mean() if len(sub_cmr) else 0

    return {
        "skus_total": n,
        "skus_disponibles": n_disp,
        "skus_en_promo": n_promo,
        "skus_con_precio_cmr": n_cmr,
        "skus_marketplace_terceros": n_marketplace,
        "skus_patrocinado": n_patrocinado,
        "pct_disponibles": n_disp / n * 100,
        "pct_en_promo": n_promo / n * 100,
        "pct_con_cmr": n_cmr / n * 100,
        "pct_marketplace": n_marketplace / n * 100,
        "pct_patrocinado": n_patrocinado / n * 100,
        "penetracion_marca_propia_pct": pen_propia,
        "precio_mediana": p_med,
        "precio_promedio": p_avg,
        "descuento_promedio_pct": desc_avg,
        "diferencial_cmr_promedio_pct": diff_cmr,
        "n_categorias": pdf["categoria_nombre"].nunique(),
        "n_marcas": pdf["marca"].nunique(),
    }


# ─────────────────────────────────────────────
# CHARTS
# ─────────────────────────────────────────────

def chart_penetracion_propia_por_categoria(pdf):
    cat_total = pdf.groupby("categoria_nombre").size()
    cat_propia = pdf[pdf["marca"].isin(MARCAS_PROPIAS)] \
                    .groupby("categoria_nombre").size()
    pen = (cat_propia / cat_total * 100).fillna(0).sort_values(ascending=False)
    pen = pen[pen > 0].head(20)
    if len(pen) == 0:
        return

    fig, ax = plt.subplots(figsize=(10, max(4, len(pen) * 0.4)))
    bars = ax.barh(pen.index[::-1], pen.values[::-1], color="#d62728")
    ax.set_xlabel("% del surtido cubierto por marca propia TOTTUS")
    ax.set_title("Penetracion de marca propia TOTTUS por categoria",
                 loc="left", fontsize=11)
    for b, v in zip(bars, pen.values[::-1]):
        ax.text(v + 0.5, b.get_y() + b.get_height()/2,
                f"{v:.1f}%", va="center", fontsize=9)
    plt.tight_layout()
    plt.savefig(OUT / "01_penetracion_marca_propia.png", dpi=120)
    plt.close()


def chart_top_categorias_y_promo(pdf):
    g = pdf.groupby("categoria_nombre").agg(
        skus=("product_id", "count"),
        en_promo=("tiene_descuento", "sum"),
    )
    g["pct_promo"] = g["en_promo"] / g["skus"] * 100
    g = g.sort_values("skus", ascending=False).head(20)

    fig, axes = plt.subplots(1, 2, figsize=(14, max(4, len(g) * 0.4)), sharey=True)
    cats = g.index[::-1]
    axes[0].barh(cats, g["skus"][::-1], color="#1f77b4")
    axes[0].set_title("Profundidad de surtido (# SKUs)", loc="left")
    axes[0].set_xlabel("# SKUs")
    for i, v in enumerate(g["skus"][::-1]):
        axes[0].text(v + 0.5, i, str(v), va="center", fontsize=9)

    axes[1].barh(cats, g["pct_promo"][::-1], color="#ff7f0e")
    axes[1].set_title("Intensidad de promocion (% SKUs en oferta)", loc="left")
    axes[1].set_xlabel("% en oferta")
    for i, v in enumerate(g["pct_promo"][::-1]):
        axes[1].text(v + 0.5, i, f"{v:.0f}%", va="center", fontsize=9)
    plt.suptitle("Top 20 categorias — surtido y agresividad promocional",
                 fontweight="bold")
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
        ax.text(b.get_x() + b.get_width()/2, v + max(cnt.values)*0.01 + 0.2,
                f"{int(v):,}\n({v/cnt.sum()*100:.0f}%)" if cnt.sum() else "0",
                ha="center", fontsize=9)
    plt.tight_layout()
    plt.savefig(OUT / "03_pricing_ladder.png", dpi=120)
    plt.close()


def chart_top_marcas_terceras(pdf):
    others = pdf[~pdf["marca"].isin(MARCAS_PROPIAS)]
    top = others["marca"].value_counts().head(20)
    if len(top) == 0:
        return

    fig, ax = plt.subplots(figsize=(10, max(4, len(top) * 0.4)))
    bars = ax.barh(top.index[::-1], top.values[::-1], color="#2ca02c")
    ax.set_title("Marcas terceras con mayor presencia — Top 20", loc="left")
    ax.set_xlabel("# SKUs")
    for b, v in zip(bars, top.values[::-1]):
        ax.text(v + 0.1, b.get_y() + b.get_height()/2, str(v),
                va="center", fontsize=9)
    plt.tight_layout()
    plt.savefig(OUT / "04_top_marcas_terceras.png", dpi=120)
    plt.close()


def chart_cmr_diferencial(pdf):
    """Diferenciador unico de Tottus: precio_cmr vs precio_oferta.
    Cuanto descuento adicional consigues con tarjeta CMR, por categoria.
    Dato critico para entender donde sacrifican margen para fidelizar."""
    sub = pdf[pdf["precio_cmr"].notna() &
              (pdf["precio_cmr"] > 0) &
              (pdf["precio_oferta"] > 0)].copy()
    sub["dif_cmr_pct"] = (1 - sub["precio_cmr"] / sub["precio_oferta"]) * 100

    fig, ax = plt.subplots(figsize=(11, 5))
    if len(sub) == 0:
        ax.text(0.5, 0.5,
                "Ningun SKU usa precio_cmr en este snapshot",
                ha="center", va="center", fontsize=14, color="#444")
        ax.axis("off")
    else:
        g = sub.groupby("categoria_nombre")["dif_cmr_pct"].agg(["mean", "count"])
        g = g.sort_values("mean", ascending=False).head(15)
        bars = ax.barh(g.index[::-1], g["mean"][::-1], color="#8e44ad")
        ax.set_xlabel("% descuento adicional con tarjeta CMR")
        ax.set_title("Diferencial CMR por categoria — incentivo tarjeta Falabella\n"
                     f"({len(sub)} SKUs con precio CMR distinto al precio internet)",
                     loc="left", fontsize=11)
        for b, v, c in zip(bars, g["mean"][::-1], g["count"][::-1]):
            ax.text(v + 0.1, b.get_y() + b.get_height()/2,
                    f"{v:.1f}% (n={c})", va="center", fontsize=9)
    plt.tight_layout()
    plt.savefig(OUT / "05_diferencial_cmr.png", dpi=120)
    plt.close()


def chart_marketplace_vs_propio(pdf):
    """Tottus muestra vendedor — el % marketplace dice cuanto del GMV
    es comision (no inventario propio). Diferenciador estrategico."""
    vendedores = pdf["vendedor"].fillna("Desconocido").value_counts()

    fig, ax = plt.subplots(figsize=(8, 5))
    if len(vendedores) <= 1:
        ax.text(0.5, 0.5,
                f"Snapshot tiene 1 unico vendedor: {vendedores.index[0]}\n"
                f"({vendedores.iloc[0]} SKUs)\n\n"
                f"Para ver el split marketplace vs propio se requiere\n"
                f"correr el scraper sobre categorias non-food.",
                ha="center", va="center", fontsize=12, color="#444")
        ax.axis("off")
    else:
        bars = ax.barh(vendedores.index[::-1], vendedores.values[::-1],
                       color=sns.color_palette("mako", len(vendedores)))
        ax.set_xlabel("# SKUs")
        ax.set_title("Distribucion por vendedor (propio vs marketplace)",
                     loc="left")
        for b, v in zip(bars, vendedores.values[::-1]):
            ax.text(v + 0.5, b.get_y() + b.get_height()/2, str(v),
                    va="center", fontsize=9)
    plt.tight_layout()
    plt.savefig(OUT / "06_marketplace_vs_propio.png", dpi=120)
    plt.close()


def chart_descuento_y_patrocinio(pdf):
    """Doble panel: descuento por categoria + % patrocinado por categoria.
    Patrocinado = retail media; saber donde proveedores estan pagando
    visibilidad."""
    promo = pdf[pdf["tiene_descuento"]]
    g_d = (promo.groupby("categoria_nombre")["descuento_pct"]
                 .agg(["mean", "count"])) if len(promo) else pd.DataFrame()
    if len(g_d):
        g_d = g_d.sort_values("mean", ascending=False).head(15)

    g_p = pdf.groupby("categoria_nombre")["patrocinado"].agg(["sum", "count"])
    g_p["pct"] = g_p["sum"] / g_p["count"] * 100
    g_p = g_p[g_p["pct"] > 0].sort_values("pct", ascending=False).head(15)

    fig, axes = plt.subplots(1, 2, figsize=(14, 7))
    if len(g_d):
        axes[0].barh(g_d.index[::-1], g_d["mean"][::-1], color="#e67e22")
        axes[0].set_title("Descuento promedio % por categoria\n(SKUs en promo)",
                          loc="left")
        axes[0].set_xlabel("descuento %")
        for i, (v, c) in enumerate(zip(g_d["mean"][::-1], g_d["count"][::-1])):
            axes[0].text(v + 0.2, i, f"{v:.1f}% (n={c})",
                         va="center", fontsize=9)
    else:
        axes[0].text(0.5, 0.5, "Sin SKUs en promo", ha="center", va="center")
        axes[0].axis("off")

    if len(g_p):
        axes[1].barh(g_p.index[::-1], g_p["pct"][::-1], color="#3498db")
        axes[1].set_title("% SKUs patrocinados por categoria\n(retail media)",
                          loc="left")
        axes[1].set_xlabel("% patrocinado")
        for i, v in enumerate(g_p["pct"][::-1]):
            axes[1].text(v + 0.5, i, f"{v:.0f}%", va="center", fontsize=9)
    else:
        axes[1].text(0.5, 0.5, "Sin SKUs patrocinados\nen este snapshot",
                     ha="center", va="center", fontsize=12, color="#444")
        axes[1].axis("off")

    plt.tight_layout()
    plt.savefig(OUT / "07_descuento_y_patrocinio.png", dpi=120)
    plt.close()


# ─────────────────────────────────────────────
# MAIN
# ─────────────────────────────────────────────

def main():
    print("=" * 60)
    print("ANALISIS COMPETITIVO — TOTTUS")
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
    chart_top_marcas_terceras(pdf)
    chart_cmr_diferencial(pdf)
    chart_marketplace_vs_propio(pdf)
    chart_descuento_y_patrocinio(pdf)

    skus_propia = int(pdf["marca"].isin(MARCAS_PROPIAS).sum())
    cats_propia_top = (pdf[pdf["marca"].isin(MARCAS_PROPIAS)]
                       ["categoria_nombre"].value_counts().head(5))

    text = f"""TOTTUS — RESUMEN EJECUTIVO
============================
Fuente: {CSV.name}
Fecha snapshot: {pdf['fecha_extraccion'].iloc[0] if len(pdf) else 'n/a'}

CIFRAS CLAVE
------------
  Total SKUs                : {k['skus_total']:,}
  Categorias                : {k['n_categorias']}
  Marcas distintas          : {k['n_marcas']}
  Disponibilidad            : {k['pct_disponibles']:.1f}%

  Promocion / Descuento
    SKUs en promo           : {k['skus_en_promo']:,}  ({k['pct_en_promo']:.1f}%)
    Descuento promedio      : {k['descuento_promedio_pct']:.1f}%   (sobre SKUs en promo)

  Tarjeta CMR (Falabella)
    SKUs con precio CMR     : {k['skus_con_precio_cmr']:,}  ({k['pct_con_cmr']:.1f}%)
    Diferencial CMR promedio: {k['diferencial_cmr_promedio_pct']:.1f}%  (vs precio internet)

  Marketplace / Retail Media
    SKUs marketplace        : {k['skus_marketplace_terceros']:,}  ({k['pct_marketplace']:.1f}%)
    SKUs patrocinados       : {k['skus_patrocinado']:,}  ({k['pct_patrocinado']:.1f}%)

  Pricing
    Mediana                 : S/ {k['precio_mediana']:.2f}
    Promedio                : S/ {k['precio_promedio']:.2f}

MARCAS PROPIAS (TOTTUS, TOTTUS VERDURAS)
-----------------------------------------
  SKUs marca propia         : {skus_propia:,}
  Penetracion en catalogo   : {k['penetracion_marca_propia_pct']:.1f}%
  Top categorias            :
"""
    for c, n in cats_propia_top.items():
        text += f"    - {c:35s} {n} SKUs\n"

    text += f"""
LECTURA ESTRATEGICA
-------------------
  • Marca propia TOTTUS pesa {k['penetracion_marca_propia_pct']:.0f}% — concentrada en frescos
    (TOTTUS VERDURAS) y commodities. Donde aparece, define el precio
    referencia de la categoria.
  • CMR es la palanca de fidelizacion, no el descuento abierto: solo
    {k['pct_con_cmr']:.0f}% de los SKUs tienen precio CMR diferenciado, pero el
    diferencial promedio es {k['diferencial_cmr_promedio_pct']:.0f}%. Esto significa que
    CMR se reserva para drivers de trafico, no para todo el catalogo.
    Una empresa sin tarjeta tiene desventaja real solo en esos SKUs.
  • Patrocinados ({k['pct_patrocinado']:.0f}%) y marketplace ({k['pct_marketplace']:.0f}%)
    indican que tan diversificado esta el ingreso (vs solo margen).
  • Para entrar a competir: replicar el efecto CMR en SKUs concretos
    cuesta menos que igualar precio internet en todo el catalogo.

CHARTS
------
  01_penetracion_marca_propia.png   ← donde TOTTUS define precio
  02_categorias_surtido_y_promo.png ← profundidad y % en oferta
  03_pricing_ladder.png             ← combate vs premium
  04_top_marcas_terceras.png        ← marcas a captar / negociar
  05_diferencial_cmr.png            ← incentivo tarjeta CMR (UNICO)
  06_marketplace_vs_propio.png      ← split inventario propio / 3P
  07_descuento_y_patrocinio.png     ← agresion promo + retail media
"""
    (OUT / "executive_summary.txt").write_text(text)
    print(f"\n✓ {len(list(OUT.glob('*.png')))} charts en {OUT}/")
    print("\n" + "─" * 60)
    print(text)


if __name__ == "__main__":
    main()

from pathlib import Path

import numpy as np
import pandas as pd
import plotly.graph_objects as go
import streamlit as st

RES = Path(__file__).parent / "resultados"

QS = [0.05, 0.10, 0.25, 0.50, 0.75, 0.90, 0.95]
QCOLS = ["q05", "q10", "q25", "q50", "q75", "q90", "q95"]
AZUL, AZUL_MED, AZUL_CLARO, AZUL_OSC = "#2a78d6", "#86b6ef", "#cde2fb", "#184f95"
NARANJA, VERDE, GRIS, TINTA = "#eb6834", "#1baf7a", "#8f8e88", "#0b0b0b"

MODELOS = {
    "naive_168": "Naive semanal",
    "naive_24": "Naive diario",
    "naive_semana": "Promedio de 7 días",
    "sarimax": "SARIMAX",
    "lightgbm": "LightGBM (puntual)",
}
MESES = ["ene", "feb", "mar", "abr", "may", "jun", "jul", "ago", "sep", "oct", "nov", "dic"]

st.set_page_config(page_title="Precio MDA · CENACE", page_icon="⚡", layout="wide")


@st.cache_data
def cargar():
    pred = pd.read_parquet(RES / "predicciones_backtest.parquet")
    cuant = pd.read_parquet(RES / "pronostico_cuantiles.parquet")
    umbrales = pd.read_parquet(RES / "umbrales_pico.parquet").set_index("clave")["umbral"]
    df = cuant.merge(pred.drop(columns="precio"), on=["clave", "timestamp"], how="inner")
    df["umbral"] = df["clave"].map(umbrales)
    return df


def pinball(y, qhat, q):
    d = y - qhat
    return np.mean(np.maximum(q * d, (q - 1) * d))


def cobertura(d, lo, hi):
    return 100 * np.mean((d["precio"] >= d[lo]) & (d["precio"] <= d[hi]))


def ancho(d, lo, hi):
    return np.mean(d[hi] - d[lo])


def mae_modelos(d):
    return pd.Series({nombre: np.mean(np.abs(d["precio"] - d[col])) for col, nombre in MODELOS.items()})


def tabla_picos(d):
    real = d["precio"] > d["umbral"]
    reglas = {
        "Naive (hubo pico ayer)": d["naive_24"] > d["umbral"],
        "Puntual (mediana)": d["q50"] > d["umbral"],
        "Riesgo (cuantil 75)": d["q75"] > d["umbral"],
        "Riesgo (cuantil 90)": d["q90"] > d["umbral"],
    }
    filas = []
    for nombre, pred in reglas.items():
        vp = (real & pred).sum()
        prec = vp / max(pred.sum(), 1)
        rec = vp / max(real.sum(), 1)
        f1 = 2 * prec * rec / (prec + rec) if prec + rec else 0
        filas.append({"Regla": nombre, "Alertas": int(pred.sum()), "Precisión": prec,
                      "Recall": rec, "F1": f1})
    return pd.DataFrame(filas).set_index("Regla")


def layout(fig, alto=420):
    fig.update_layout(
        height=alto, margin=dict(l=10, r=10, t=30, b=10), paper_bgcolor="#fcfcfb",
        plot_bgcolor="#fcfcfb", font=dict(color="#52514e"), hovermode="x unified",
        legend=dict(orientation="h", y=1.08, x=0),
    )
    fig.update_xaxes(showgrid=False)
    fig.update_yaxes(gridcolor="#e8e7e2")
    return fig


df = cargar()
zonas = sorted(df["clave"].unique())
fecha_min, fecha_max = df["timestamp"].min().date(), df["timestamp"].max().date()

st.sidebar.title("Precio MDA · CENACE")
st.sidebar.caption("Pronóstico day-ahead del precio en nodos distribuidos (PEND), sistema SIN.")
rango = st.sidebar.date_input("Periodo de análisis", value=(fecha_min, fecha_max),
                              min_value=fecha_min, max_value=fecha_max)
if not isinstance(rango, tuple) or len(rango) != 2:
    st.info("Selecciona fecha de inicio y fin en la barra lateral.")
    st.stop()

ini, fin = pd.Timestamp(rango[0]), pd.Timestamp(rango[1]) + pd.Timedelta(hours=23)
sub = df[(df["timestamp"] >= ini) & (df["timestamp"] <= fin)]
st.sidebar.markdown(f"**{len(sub):,}** horas-zona en el periodo")
st.sidebar.markdown(
    "Backtest nov-2025 a oct-2026. El modelo se entrena solo con datos anteriores a cada periodo."
)

st.title("Pronóstico probabilístico del precio de energía")
st.caption("Mercado del Día en Adelanto · Precios en Nodos Distribuidos (zonas de carga) · $/MWh")

t_res, t_pron, t_cal, t_pico = st.tabs(["Resumen", "Pronóstico", "Calibración", "Alerta de picos"])

with t_res:
    if sub.empty:
        st.warning("No hay datos en el periodo seleccionado.")
    else:
        mae = mae_modelos(sub)
        mejora = 100 * (1 - mae["LightGBM (puntual)"] / mae["Naive semanal"])
        c80 = cobertura(sub, "q10", "q90")
        c90 = cobertura(sub, "q05", "q95")
        picos = tabla_picos(sub)

        k1, k2, k3, k4 = st.columns(4)
        k1.metric("MAE LightGBM", f"{mae['LightGBM (puntual)']:.0f} $/MWh",
                  f"{-mejora:.0f} % vs naive semanal", delta_color="inverse")
        k2.metric("Cobertura del 80 %", f"{c80:.1f} %", "nominal 80 %", delta_color="off")
        k3.metric("Cobertura del 90 %", f"{c90:.1f} %", "nominal 90 %", delta_color="off")
        k4.metric("Picos detectados (cuantil 90)", f"{100 * picos.loc['Riesgo (cuantil 90)', 'Recall']:.0f} %",
                  "recall", delta_color="off")

        st.subheader("Error absoluto medio por modelo y zona")
        por_zona = sub.groupby("clave").apply(mae_modelos, include_groups=False)
        orden = por_zona["LightGBM (puntual)"].sort_values().index
        fig = go.Figure()
        for nombre, color in [("Naive semanal", GRIS), ("SARIMAX", NARANJA), ("LightGBM (puntual)", AZUL)]:
            fig.add_bar(name=nombre, x=orden, y=por_zona.loc[orden, nombre], marker_color=color)
        fig.update_layout(barmode="group")
        st.plotly_chart(layout(fig, 380), width="stretch")

        st.subheader("MAE por modelo (todas las zonas)")
        tabla = pd.DataFrame({"MAE ($/MWh)": mae}).sort_values("MAE ($/MWh)")
        tabla["mejora vs naive semanal (%)"] = 100 * (1 - tabla["MAE ($/MWh)"] / mae["Naive semanal"])
        st.dataframe(tabla.round(1), width="stretch")

with t_pron:
    zona = st.selectbox("Zona de carga", zonas, index=zonas.index("MONTERREY"), key="zona_pron")
    z = sub[sub["clave"] == zona].sort_values("timestamp")
    if z.empty:
        st.warning("No hay datos de esta zona en el periodo.")
    else:
        ventana = st.slider(
            "Ventana a graficar", min_value=z["timestamp"].min().to_pydatetime(),
            max_value=z["timestamp"].max().to_pydatetime(),
            value=(max(z["timestamp"].min(), z["timestamp"].max() - pd.Timedelta(days=14)).to_pydatetime(),
                   z["timestamp"].max().to_pydatetime()),
            format="DD MMM YYYY HH:mm", step=pd.Timedelta(hours=1).to_pytimedelta(),
        )
        v = z[(z["timestamp"] >= ventana[0]) & (z["timestamp"] <= ventana[1])]
        fig = go.Figure()
        fig.add_scatter(x=v.timestamp, y=v.q95, line=dict(width=0), showlegend=False, hoverinfo="skip")
        fig.add_scatter(x=v.timestamp, y=v.q05, fill="tonexty", fillcolor="rgba(205,226,251,0.9)",
                        line=dict(width=0), name="IC 90 %", hovertemplate="%{y:,.0f}")
        fig.add_scatter(x=v.timestamp, y=v.q90, line=dict(width=0), showlegend=False, hoverinfo="skip")
        fig.add_scatter(x=v.timestamp, y=v.q10, fill="tonexty", fillcolor="rgba(134,182,239,0.7)",
                        line=dict(width=0), name="IC 80 %", hovertemplate="%{y:,.0f}")
        fig.add_scatter(x=v.timestamp, y=v.q75, line=dict(width=0), showlegend=False, hoverinfo="skip")
        fig.add_scatter(x=v.timestamp, y=v.q25, fill="tonexty", fillcolor="rgba(58,135,229,0.75)",
                        line=dict(width=0), name="IC 50 %", hovertemplate="%{y:,.0f}")
        fig.add_scatter(x=v.timestamp, y=v.q50, line=dict(color=AZUL_OSC, width=1.2), name="mediana",
                        hovertemplate="%{y:,.0f}")
        fig.add_scatter(x=v.timestamp, y=v.precio, line=dict(color=TINTA, width=1.4), name="real",
                        hovertemplate="%{y:,.0f}")
        fig.add_hline(y=float(z["umbral"].iloc[0]), line=dict(color=NARANJA, dash="dot", width=1),
                      annotation_text="umbral de pico (p95)", annotation_position="top left")
        fig.update_yaxes(title_text="$/MWh")
        st.plotly_chart(layout(fig, 460), width="stretch")

        st.caption(
            "Cada banda es un intervalo de predicción: el precio real debería caer dentro de la banda "
            "con la probabilidad indicada. Pasa el cursor para ver los valores por hora."
        )

        m1, m2, m3 = st.columns(3)
        m1.metric("MAE de la mediana", f"{np.mean(np.abs(z.precio - z.q50)):.0f} $/MWh")
        m2.metric("Pinball loss (7 cuantiles)",
                  f"{np.mean([pinball(z.precio.to_numpy(), z[c].to_numpy(), q) for q, c in zip(QS, QCOLS)]):.1f}")
        m3.metric("Ancho medio del IC 80 %", f"{ancho(z, 'q10', 'q90'):.0f} $/MWh")

with t_cal:
    zona_c = st.selectbox("Zona de carga", zonas, index=zonas.index("MONTERREY"), key="zona_cal")
    z = sub[sub["clave"] == zona_c]
    if z.empty:
        st.warning("No hay datos de esta zona en el periodo.")
    else:
        c_a, c_b = st.columns(2)

        with c_a:
            emp = [100 * np.mean(z["precio"] <= z[c]) for c in QCOLS]
            fig = go.Figure()
            fig.add_scatter(x=[0, 100], y=[0, 100], line=dict(color=TINTA, dash="dot", width=1),
                            name="calibración perfecta")
            fig.add_scatter(x=[100 * q for q in QS], y=emp, mode="lines+markers",
                            line=dict(color=AZUL), marker=dict(size=8), name="modelo",
                            hovertemplate="nominal %{x:.0f} % → observado %{y:.1f} %")
            fig.update_xaxes(title_text="Cuantil nominal (%)", range=[0, 100])
            fig.update_yaxes(title_text="Observado (%)", range=[0, 100])
            st.plotly_chart(layout(fig, 380).update_layout(title="Diagrama de confiabilidad"),
                            width="stretch")

        with c_b:
            por_hora = z.assign(ok=(z.precio >= z.q10) & (z.precio <= z.q90)).groupby("hora")["ok"].mean() * 100
            fig = go.Figure()
            fig.add_scatter(x=por_hora.index, y=por_hora.values, mode="lines+markers",
                            line=dict(color=VERDE), marker=dict(size=7), name="cobertura IC 80 %",
                            hovertemplate="hora %{x} → %{y:.1f} %")
            fig.add_hline(y=80, line=dict(color=TINTA, dash="dot", width=1))
            fig.update_xaxes(title_text="Hora de operación", dtick=2, range=[0.5, 24.5])
            fig.update_yaxes(title_text="% dentro del intervalo", range=[50, 100])
            st.plotly_chart(layout(fig, 380).update_layout(title="Cobertura del 80 % por hora"),
                            width="stretch")

        cob_m = z.assign(mes=z.timestamp.dt.to_period("M"),
                         ok=(z.precio >= z.q10) & (z.precio <= z.q90),
                         anc=z.q90 - z.q10).groupby("mes").agg(cobertura=("ok", "mean"), ancho=("anc", "mean"))
        x = cob_m.index.to_timestamp()
        fig = go.Figure()
        fig.add_bar(x=x, y=cob_m["cobertura"] * 100, marker_color=AZUL_MED, name="cobertura %",
                    hovertemplate="%{y:.1f} %")
        fig.add_scatter(x=x, y=cob_m["ancho"], yaxis="y2", mode="lines+markers", line=dict(color=NARANJA),
                        name="ancho medio ($/MWh)", hovertemplate="%{y:,.0f}")
        fig.add_hline(y=80, line=dict(color=TINTA, dash="dot", width=1))
        fig.update_layout(yaxis=dict(title="cobertura %", range=[0, 100]),
                          yaxis2=dict(title="ancho $/MWh", overlaying="y", side="right", showgrid=False))
        st.plotly_chart(layout(fig, 380).update_layout(title="Cobertura y ancho del intervalo 80 % por mes"),
                        width="stretch")

with t_pico:
    st.write(
        "Un **pico** es una hora con precio por encima del percentil 95 histórico de su zona. "
        "Cada regla emite una alerta cuando el pronóstico supera ese umbral."
    )
    if sub.empty:
        st.warning("No hay datos en el periodo seleccionado.")
    else:
        tp = tabla_picos(sub)
        st.dataframe(tp.style.format({"Precisión": "{:.2f}", "Recall": "{:.2f}", "F1": "{:.2f}"}),
                     width="stretch")

        filas = []
        for zn, g in sub.groupby("clave"):
            t = tabla_picos(g)
            filas.append({"Zona": zn, "Recall naive": t.loc["Naive (hubo pico ayer)", "Recall"],
                          "Recall riesgo (q90)": t.loc["Riesgo (cuantil 90)", "Recall"],
                          "Precisión riesgo (q90)": t.loc["Riesgo (cuantil 90)", "Precisión"]})
        por_z = pd.DataFrame(filas).set_index("Zona").sort_values("Recall riesgo (q90)")
        fig = go.Figure()
        fig.add_bar(y=por_z.index, x=por_z["Recall naive"], orientation="h", marker_color=GRIS,
                    name="naive (pico ayer)", hovertemplate="%{x:.0%}")
        fig.add_bar(y=por_z.index, x=por_z["Recall riesgo (q90)"], orientation="h", marker_color=AZUL,
                    name="riesgo (cuantil 90)", hovertemplate="%{x:.0%}")
        fig.update_layout(barmode="group")
        fig.update_xaxes(title_text="recall (proporción de picos detectados)", range=[0, 1], tickformat=".0%")
        st.plotly_chart(layout(fig, 420), width="stretch")

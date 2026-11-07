import numpy as np
import pandas as pd
from dateutil.easter import easter

LAGS = (24, 48, 72, 96, 120, 144, 168, 336)
CALENDARIO = ["hora", "dow", "mes", "doy_sin", "doy_cos", "festivo", "festivo_ayer", "zona"]


def lunes_n(anio, mes, n):
    d = pd.Timestamp(anio, mes, 1)
    d += pd.Timedelta(days=(7 - d.dayofweek) % 7)
    return d + pd.Timedelta(weeks=n - 1)


def festivos_mx(anios=range(2016, 2028)):
    f = []
    for a in anios:
        pascua = pd.Timestamp(easter(a))
        f += [pd.Timestamp(a, 1, 1), lunes_n(a, 2, 1), lunes_n(a, 3, 3), pd.Timestamp(a, 5, 1),
              pd.Timestamp(a, 9, 16), lunes_n(a, 11, 3), pd.Timestamp(a, 12, 25),
              pascua - pd.Timedelta(days=3), pascua - pd.Timedelta(days=2)]
    return pd.DatetimeIndex(f).normalize()


FESTIVOS = festivos_mx()


def construir_features(df, mediana):
    """Todas las variables usan desplazamientos >= 24 h: válidas para pronóstico day-ahead."""
    d = df.sort_values(["clave", "timestamp"]).reset_index(drop=True).copy()
    d["m"] = d["clave"].map(mediana)
    d["y"] = np.arcsinh(d["precio"] / d["m"])
    d["fecha"] = d["timestamp"].dt.normalize()
    g = d.groupby("clave", sort=False)

    for k in LAGS:
        d[f"y_lag{k}"] = g["y"].shift(k)
    d["y_mean_mismahora_7d"] = d[[f"y_lag{k}" for k in LAGS[:7]]].mean(axis=1)
    for c in ("energia", "congestion", "perdidas"):
        d[f"{c}_lag24"] = g[c].shift(24) / d["m"]
    d["energia_lag168"] = g["energia"].shift(168) / d["m"]

    dia = d.groupby(["clave", "fecha"])["y"].agg(["mean", "max", "min", "std", "last"])
    dia = dia.groupby(level=0).shift(1).add_prefix("dprev_")
    dia["dprev_mean_7d"] = dia.groupby(level=0)["dprev_mean"].transform(
        lambda s: s.rolling(7, min_periods=1).mean())
    d = d.join(dia, on=["clave", "fecha"])

    d = d.join(d.groupby("timestamp")["y_lag24"].mean().rename("sis_y_lag24"), on="timestamp")

    d["hora"] = d["timestamp"].dt.hour + 1
    d["dow"] = d["timestamp"].dt.dayofweek
    d["mes"] = d["timestamp"].dt.month
    doy = d["timestamp"].dt.dayofyear
    d["doy_sin"] = np.sin(2 * np.pi * doy / 365.25)
    d["doy_cos"] = np.cos(2 * np.pi * doy / 365.25)
    d["festivo"] = d["fecha"].isin(FESTIVOS).astype(int)
    d["festivo_ayer"] = (d["fecha"] - pd.Timedelta(days=1)).isin(FESTIVOS).astype(int)
    d["zona"] = d["clave"].astype("category")
    return d


def lista_features(feat):
    pref = ("y_lag", "y_mean", "energia_", "congestion_", "perdidas_", "dprev_", "sis_")
    return [c for c in feat.columns if c.startswith(pref)] + CALENDARIO


def inv(y, clave, mediana):
    return np.sinh(np.asarray(y)) * pd.Series(clave).map(mediana).to_numpy()

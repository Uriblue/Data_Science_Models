from pathlib import Path

import pandas as pd

CAL_INICIO = pd.Timestamp("2025-08-01")

df = pd.read_parquet("data/pend_sin_mda_clean.parquet")
umbrales = (df[df.timestamp < CAL_INICIO]
            .groupby("clave")["precio"].quantile(0.95)
            .rename("umbral").reset_index())

Path("resultados").mkdir(exist_ok=True)
umbrales.to_parquet("resultados/umbrales_pico.parquet", index=False)
print(umbrales.round(1).to_string(index=False))

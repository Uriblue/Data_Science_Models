# Pronóstico probabilístico del precio de energía · CENACE (MDA)

Pronóstico *day-ahead* del precio marginal en nodos distribuidos (PEND) del Mercado del Día en Adelanto del Sistema Interconectado Nacional, con intervalos de predicción calibrados y un dashboard interactivo.

**Datos:** precios horarios por zona de carga de 2017-01-27 a 2026-11-06, descargados de los servicios web oficiales del CENACE (SW-PEND). Ocho zonas: Acapulco, Aguascalientes, Guadalajara, Hermosillo, Mérida, Monterrey, Puebla y Querétaro.

## Preguntas que responde

1. ¿Qué tanto se puede anticipar el precio del día siguiente, usando solo información disponible al cierre del MDA del día anterior?
2. ¿Qué tan confiables son los intervalos de predicción, y cuánto cambian según la hora?
3. ¿Sirve el pronóstico probabilístico para detectar picos de precio que el pronóstico puntual no ve?

## Resultados (backtest nov-2025 a oct-2026)

| Modelo | MAE ($/MWh) | vs. naive semanal |
|---|---|---|
| Naive semanal | 425 | — |
| Naive diario | 328 | −23 % |
| SARIMAX por hora | 298 | −30 % |
| **LightGBM global** | **265** | **−38 %** |

- LightGBM gana en las ocho zonas. Contra SARIMAX la mejora va de 6 % (Mérida) a 15 % (Querétaro); con Diebold-Mariano es significativa en todas.
- Pronóstico probabilístico (LightGBM por cuantiles + calibración conformal por bloque horario de 6 h):

| Intervalo | Cobertura observada | Nominal |
|---|---|---|
| 80 % | 78.2 % | 80 % |
| 90 % | 88.4 % | 90 % |

- Un LightGBM cuantílico sin calibrar cubría solo 66 % del intervalo de 80 %. La corrección conformal lo lleva a la cobertura nominal.
- Alerta de picos (precio > p95 histórico de la zona): el cuantil 90 detecta 74 % de los picos contra 24 % del pronóstico puntual y 50 % de la regla ingenua "hubo pico ayer".

## Hallazgos del EDA

- **Curva de pato:** en 2017–2019 el precio máximo era al mediodía y en la tarde; desde 2023 el mediodía es la hora más barata y el pico se mueve a las 20–22 h. La entrada de solar lo explica.
- **Congestión:** el componente de energía es el mismo en todas las zonas (≈971 $/MWh). Las diferencias vienen de pérdidas y congestión. Mérida está congestionada (congestión media +351 $/MWh) y Hermosillo tiene excedente (−240 $/MWh).
- **Spread MTR − MDA:** en Monterrey el tiempo real queda en promedio ≈ +160 $/MWh por encima del día en adelanto entre las 19 y las 22 h.
- **Colas pesadas:** la curtosis va de 27 a 84 y el máximo es hasta 8 veces el p99, por eso el modelo trabaja en escala asinh(precio / mediana).

## Decisiones metodológicas

- **Sin fuga de información.** Pronosticar el día D+1 solo puede usar datos hasta la hora 24 del día D. Todos los rezagos son de al menos 24 h. El notebook 03 incluye una prueba que altera los precios de un día y verifica que las variables de ese día no cambien.
- **Validación con origen móvil.** Entrenamiento con todo lo anterior a cada mes de prueba, sin mirar el futuro.
- **Calibración conformal móvil.** Cada cuantil se corrige con los residuos de los últimos 90 días, separados por bloque horario, porque la rampa nocturna tiene un error distinto al del mediodía.

## Estructura

```
CENACE_PEND/
├── 02_eda_cenace.ipynb          # calidad de datos, EDA, componentes, spread MTR−MDA
├── 03_modelado_cenace.ipynb     # baselines, SARIMAX, LightGBM, backtest, Diebold-Mariano
├── 04_probabilistico_cenace.ipynb  # cuantiles, calibración conformal, alerta de picos
├── cenace_download.py           # descarga desde los servicios web de CENACE
├── cenace_features.py           # construcción de variables compartida por 03 y 04
├── exportar_umbrales.py         # umbrales de pico (p95 previo al periodo de prueba)
├── app.py                       # dashboard en Streamlit
├── requirements.txt
└── resultados/                  # predicciones, cuantiles y umbrales que usa el dashboard
```

## Cómo correrlo

```bash
pip install -r requirements.txt

# 1. descargar datos (guarda en data/)
python cenace_download.py --reporte PEND --proceso MDA --inicio 2017-01-27 \
  --claves MONTERREY GUADALAJARA MERIDA HERMOSILLO PUEBLA QUERETARO AGUASCALIENTES ACAPULCO

# 2. notebooks 02 → 03 → 04 en orden (generan resultados/)

# 3. dashboard
streamlit run app.py
```

Los datos crudos (`data/`) no se suben al repositorio; los resultados que usa el dashboard sí están en `resultados/`.

## Limitaciones y siguientes pasos

- Las variables exógenas (temperatura, demanda pronosticada) todavía no entran al modelo. Se espera que ayuden sobre todo en las horas 19–22.
- Mérida tiene intervalos muy anchos: el modelo detecta riesgo, pero no el nivel exacto del bloque caro.
- El pronóstico es para la zona de carga, no para un nodo específico (PML).

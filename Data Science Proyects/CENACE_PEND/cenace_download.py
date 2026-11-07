import argparse
import logging
import time
import xml.etree.ElementTree as ET
from datetime import date, timedelta
from pathlib import Path

import pandas as pd
import requests

BASE = "https://ws01.cenace.gob.mx:8082"

SPECS = {
    "PEND": {"path": "SWPEND", "item_tag": "Zona_Carga", "key_tag": "zona_carga",
             "prefix": "pz", "max_items": 10},
    "PML": {"path": "SWPML", "item_tag": "Nodo", "key_tag": "clv_nodo",
            "prefix": "pml", "max_items": 20},
}

INICIO = {"SIN": date(2016, 1, 29), "BCA": date(2016, 1, 27), "BCS": date(2016, 3, 23)}

log = logging.getLogger("cenace")


def chunks(seq, n):
    for i in range(0, len(seq), n):
        yield seq[i:i + n]


def date_windows(start, end, days=7):
    cur = start
    while cur <= end:
        fin = min(cur + timedelta(days=days - 1), end)
        yield cur, fin
        cur = fin + timedelta(days=1)


def build_url(reporte, sistema, proceso, items, d0, d1):
    spec = SPECS[reporte]
    lista = ",".join(i.strip().upper().replace(" ", "-") for i in items)
    return (f"{BASE}/{spec['path']}/SIM/{sistema}/{proceso}/{lista}/"
            f"{d0:%Y/%m/%d}/{d1:%Y/%m/%d}/XML")


def parse_xml(text, reporte):
    spec = SPECS[reporte]
    p = spec["prefix"]
    root = ET.fromstring(text)
    status = root.findtext("status")
    if status and status.upper() != "OK":
        log.warning("status=%s", status)
    rows = []
    for item in root.iter(spec["item_tag"]):
        clave = item.findtext(spec["key_tag"])
        for v in item.iter("Valor"):
            rows.append({
                "clave": clave,
                "fecha": v.findtext("fecha"),
                "hora": v.findtext("hora"),
                "precio": v.findtext(p),
                "energia": v.findtext(f"{p}_ene"),
                "perdidas": v.findtext(f"{p}_per"),
                "congestion": v.findtext(f"{p}_cng"),
            })
    return rows


class FetchError(RuntimeError):
    pass


def fetch(session, url, retries=5, timeout=(15, 120)):
    ultimo = None
    for intento in range(1, retries + 1):
        try:
            r = session.get(url, timeout=timeout)
            if r.status_code in (204, 404):
                return None
            r.raise_for_status()
            if "<Reporte" not in r.text:
                raise requests.RequestException(f"respuesta no XML ({len(r.text)} bytes)")
            return r.text
        except requests.RequestException as e:
            ultimo = e
            espera = min(5 * 2 ** (intento - 1), 120)
            log.warning("intento %d/%d falló (%s); reintento en %ds", intento, retries, e, espera)
            time.sleep(espera)
    raise FetchError(f"{ultimo} | {url}")


def fetch_window(session, reporte, sistema, proceso, grupo, d0, d1, pausa):
    url = build_url(reporte, sistema, proceso, grupo, d0, d1)
    try:
        text = fetch(session, url)
        return parse_xml(text, reporte) if text else []
    except FetchError:
        if d0 == d1:
            raise
    # la ventana completa falló: se intenta día por día
    log.warning("ventana %s → %s falló; reintentando día por día", d0, d1)
    rows = []
    for dia, _ in date_windows(d0, d1, days=1):
        text = fetch(session, build_url(reporte, sistema, proceso, grupo, dia, dia))
        rows += parse_xml(text, reporte) if text else []
        time.sleep(pausa)
    return rows


def to_frame(rows, reporte, sistema, proceso):
    df = pd.DataFrame(rows)
    if df.empty:
        return df
    df["fecha"] = pd.to_datetime(df["fecha"])
    df["hora"] = df["hora"].astype(int)
    for c in ["precio", "energia", "perdidas", "congestion"]:
        df[c] = pd.to_numeric(df[c], errors="coerce")
    # hora 1 = 00:00–01:00; la hora 25 (cambio de horario pre-2022) cae en el día siguiente
    df["timestamp"] = df["fecha"] + pd.to_timedelta(df["hora"] - 1, unit="h")
    df.insert(0, "reporte", reporte)
    df.insert(1, "sistema", sistema)
    df.insert(2, "proceso", proceso)
    return df


def download(reporte, sistema, proceso, items, start, end, out_dir, pausa=1.0, verify=True):
    spec = SPECS[reporte]
    raw_dir = Path(out_dir) / "raw" / f"{reporte}_{sistema}_{proceso}"
    raw_dir.mkdir(parents=True, exist_ok=True)

    session = requests.Session()
    session.verify = verify
    session.headers["User-Agent"] = "cenace-ts-research/1.0"

    grupos = list(chunks(items, spec["max_items"]))
    ventanas = list(date_windows(start, end))
    total = len(grupos) * len(ventanas)
    n = 0
    fallidos = []

    for gi, grupo in enumerate(grupos):
        for d0, d1 in ventanas:
            n += 1
            cache = raw_dir / f"g{gi:02d}_{d0:%Y%m%d}_{d1:%Y%m%d}.parquet"
            if cache.exists():
                continue
            log.info("[%d/%d] %s → %s  (%d claves)", n, total, d0, d1, len(grupo))
            try:
                rows = fetch_window(session, reporte, sistema, proceso, grupo, d0, d1, pausa)
            except FetchError as e:
                log.error("se omite %s → %s: %s", d0, d1, e)
                fallidos.append({"grupo": gi, "inicio": d0, "fin": d1, "error": str(e)})
                time.sleep(pausa)
                continue
            df = to_frame(rows, reporte, sistema, proceso)
            if df.empty:
                log.warning("sin datos para %s → %s", d0, d1)
                df = pd.DataFrame(columns=["reporte", "sistema", "proceso", "clave", "fecha",
                                           "hora", "precio", "energia", "perdidas",
                                           "congestion", "timestamp"])
            df.to_parquet(cache, index=False)
            time.sleep(pausa)

    log_fallidos = Path(out_dir) / f"fallidos_{reporte}_{sistema}_{proceso}.csv"
    if fallidos:
        pd.DataFrame(fallidos).to_csv(log_fallidos, index=False)
        log.warning("%d ventanas fallaron (ver %s). Vuelve a correr para reintentarlas.",
                    len(fallidos), log_fallidos)
    elif log_fallidos.exists():
        log_fallidos.unlink()

    partes = [pd.read_parquet(p) for p in sorted(raw_dir.glob("*.parquet"))]
    partes = [p for p in partes if not p.empty]
    if not partes:
        raise RuntimeError("No se descargó nada; revisa las claves en el catálogo de NodosP.")
    df = (pd.concat(partes, ignore_index=True)
            .drop_duplicates(["clave", "fecha", "hora"], keep="last")
            .sort_values(["clave", "timestamp"])
            .reset_index(drop=True))
    df = df[(df["fecha"] >= pd.Timestamp(start)) & (df["fecha"] <= pd.Timestamp(end))]

    faltantes = set(i.upper().replace(" ", "-") for i in items) - set(df["clave"].unique())
    if faltantes:
        log.warning("Claves sin datos (nombre incorrecto o no existen en %s): %s",
                    sistema, sorted(faltantes))

    destino = Path(out_dir) / f"{reporte.lower()}_{sistema.lower()}_{proceso.lower()}.parquet"
    df.to_parquet(destino, index=False)
    log.info("Guardado %s  (%d filas, %d claves, %s → %s)", destino, len(df),
             df["clave"].nunique(), df["fecha"].min().date(), df["fecha"].max().date())
    return df


def parse_args():
    ap = argparse.ArgumentParser(description="Descarga PEND/PML del SIM de CENACE")
    ap.add_argument("--reporte", choices=SPECS, default="PEND")
    ap.add_argument("--sistema", choices=["SIN", "BCA", "BCS"], default="SIN")
    ap.add_argument("--proceso", choices=["MDA", "MTR"], default="MDA")
    ap.add_argument("--claves", nargs="+", required=True,
                    help="Zonas de carga (PEND) o NodosP (PML), p. ej. MONTERREY GUADALAJARA")
    ap.add_argument("--inicio", type=date.fromisoformat, required=True)
    ap.add_argument("--fin", type=date.fromisoformat, default=date.today())
    ap.add_argument("--out", default="data")
    ap.add_argument("--pausa", type=float, default=1.0)
    ap.add_argument("--ca-bundle", default=None,
                    help="Ruta a un bundle de certificados si falla la verificación TLS")
    return ap.parse_args()


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    a = parse_args()
    inicio = max(a.inicio, INICIO[a.sistema])
    fin = min(a.fin, date.today() - timedelta(days=7)) if a.proceso == "MTR" else a.fin
    download(a.reporte, a.sistema, a.proceso, a.claves, inicio, fin, a.out,
             pausa=a.pausa, verify=a.ca_bundle or True)

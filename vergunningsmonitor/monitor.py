#!/usr/bin/env python3
"""
Vergunningsmonitor Limburg
--------------------------
Haalt de omgevingsvergunningen (ingedeelde inrichtingen of activiteiten, IIOA)
op uit de publieke Mercator OGC API Features-dienst van de Vlaamse overheid,
filtert op provincie Limburg, vergelijkt met de vorige run en schrijft de
resultaten weg voor het dashboard in docs/.

Gebruik:
    python monitor.py                 # normale run
    python monitor.py --inspect       # toon velden + 3 voorbeeldrecords, schrijf niets
    python monitor.py --base-url URL  # andere server (bv. om te testen)

Geen externe libraries nodig (enkel Python 3.9+).
"""

import argparse
import datetime as dt
import hashlib
import json
import os
import re
import sys
import time
import urllib.parse
import urllib.request
from email.utils import format_datetime
from xml.sax.saxutils import escape

# ----------------------------------------------------------------------------
# CONFIGURATIE  (pas hier aan indien nodig)
# ----------------------------------------------------------------------------
BASE_URL = "https://www.mercator.vlaanderen.be/raadpleegdienstenmercatorpubliek/ogc/features/v1"
COLLECTION = "lu:lu_omv_iioa_v2"

# Ruwe rechthoek rond provincie Limburg (lon/lat, WGS84). Verkleint de download.
LIMBURG_BBOX = (4.95, 50.67, 5.95, 51.38)

# Limburgse postcodes: 3500-3999
POSTCODE_MIN, POSTCODE_MAX = 3500, 3999

# Limburgse gemeenten (incl. namen van vóór de fusies van 2025), kleine letters.
LIMBURG_GEMEENTEN = {
    "alken", "as", "beringen", "bilzen", "bilzen-hoeselt", "bocholt", "borgloon",
    "bree", "diepenbeek", "dilsen-stokkem", "genk", "gingelom", "ham", "hamont-achel",
    "hasselt", "hechtel-eksel", "heers", "herk-de-stad", "herstappe", "heusden-zolder",
    "hoeselt", "houthalen-helchteren", "kortessem", "lanaken", "leopoldsburg",
    "lommel", "lummen", "maaseik", "maasmechelen", "nieuwerkerken", "oudsbergen",
    "peer", "pelt", "riemst", "sint-truiden", "tessenderlo", "tessenderlo-ham",
    "tongeren", "tongeren-borgloon", "voeren", "wellen", "zonhoven", "zutendaal",
}

# Hoeveel dagen een wijziging in het "wijzigingen"-overzicht blijft staan.
CHANGES_KEEP_DAYS = 365

PAGE_SIZE = 1000
OUT_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "docs", "data")
STATE_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "state", "snapshot.json")
DASHBOARD_URL = os.environ.get("DASHBOARD_URL", "")  # optioneel, voor de RSS-links

# Herkenning van veldnamen (we kennen het exacte schema niet op voorhand).
ROLE_PATTERNS = {
    "id":       [r"^(oidn|uidn|objectid|id)$", r"projectnummer", r"dossiernummer", r"vergunning.*nummer", r"nummer$", r"_id$", r"^id"],
    "gemeente": [r"^gemeente$", r"gemeente.*naam", r"gemeente", r"^gem", r"municip"],
    "postcode": [r"postcode", r"^pc$", r"postnr"],
    "nis":      [r"nis", r"niscode"],
    "titel":    [r"projectnaam", r"naam.*inrichting", r"inrichting.*naam", r"omschrijving", r"onderwerp", r"titel", r"exploitant", r"naam"],
    "datum":    [r"datum.*beslis", r"beslis.*datum", r"datum.*vergun", r"vergun.*datum", r"datum.*indien", r"datum", r"date", r"begin", r"start"],
    "einddatum":[r"einddatum", r"eind.*datum", r"vervaldatum", r"geldig.*tot", r"end"],
    "status":   [r"status", r"toestand", r"fase"],
    "klasse":   [r"klasse", r"categorie", r"class"],
    "adres":    [r"adres", r"straat", r"locatie"],
}


# ----------------------------------------------------------------------------
# HTTP
# ----------------------------------------------------------------------------
def http_get_json(url, retries=4):
    last = None
    for attempt in range(retries):
        try:
            req = urllib.request.Request(url, headers={
                "Accept": "application/geo+json, application/json",
                "User-Agent": "Vergunningsmonitor-Limburg/1.0 (journalistiek onderzoek)",
            })
            with urllib.request.urlopen(req, timeout=120) as r:
                return json.loads(r.read().decode("utf-8"))
        except Exception as e:  # noqa: BLE001
            last = e
            wait = 5 * (attempt + 1)
            print(f"  ! fout bij {url[:120]}…: {e} — nieuwe poging over {wait}s", file=sys.stderr)
            time.sleep(wait)
    raise RuntimeError(f"Kon {url} niet ophalen: {last}")


def with_params(url, **params):
    parts = urllib.parse.urlsplit(url)
    q = dict(urllib.parse.parse_qsl(parts.query))
    q.update({k: str(v) for k, v in params.items() if v is not None})
    return urllib.parse.urlunsplit(parts._replace(query=urllib.parse.urlencode(q, safe=",:")))


def fetch_all(base_url, collection, bbox, limit_pages=None):
    url = with_params(
        f"{base_url}/collections/{urllib.parse.quote(collection, safe=':')}/items",
        f="json", limit=PAGE_SIZE, bbox=",".join(str(x) for x in bbox) if bbox else None,
    )
    feats, page = [], 0
    while url:
        page += 1
        print(f"  pagina {page}: {url[:160]}")
        data = http_get_json(url)
        batch = data.get("features", [])
        feats.extend(batch)
        nxt = next((l.get("href") for l in data.get("links", [])
                    if l.get("rel") == "next" and l.get("href")), None)
        if not batch or (limit_pages and page >= limit_pages):
            break
        url = nxt
    return feats


# ----------------------------------------------------------------------------
# Velden herkennen
# ----------------------------------------------------------------------------
def detect_roles(features):
    keys = []
    for f in features[:500]:
        for k in (f.get("properties") or {}):
            if k not in keys:
                keys.append(k)
    roles = {}
    used = set()
    for role, pats in ROLE_PATTERNS.items():
        for p in pats:
            hit = next((k for k in keys if k not in used and re.search(p, k, re.I)), None)
            if hit:
                roles[role] = hit
                used.add(hit)
                break
    # een 'id'-veld moet uniek zijn, anders niet gebruiken
    if "id" in roles:
        vals = [str((f.get("properties") or {}).get(roles["id"])) for f in features]
        if len(set(vals)) < len(vals) * 0.98:
            roles.pop("id")
    return keys, roles


def feature_key(f, roles):
    p = f.get("properties") or {}
    if roles.get("id") and p.get(roles["id"]) not in (None, ""):
        return str(p[roles["id"]])
    if f.get("id") not in (None, ""):
        return str(f["id"])
    # laatste redmiddel: hash van inhoud
    return hashlib.sha1(json.dumps(f, sort_keys=True).encode()).hexdigest()[:16]


def props_hash(f):
    return hashlib.sha1(json.dumps(f.get("properties") or {}, sort_keys=True, default=str).encode()).hexdigest()


# ----------------------------------------------------------------------------
# Limburg-filter
# ----------------------------------------------------------------------------
def is_limburg(f, roles):
    p = f.get("properties") or {}
    if roles.get("postcode"):
        m = re.search(r"\d{4}", str(p.get(roles["postcode"]) or ""))
        if m:
            return POSTCODE_MIN <= int(m.group()) <= POSTCODE_MAX
    if roles.get("nis"):
        v = re.sub(r"\D", "", str(p.get(roles["nis"]) or ""))
        if len(v) == 5:
            return v.startswith("7")  # NIS-codes Limburg: 7xxxx
    if roles.get("gemeente"):
        g = str(p.get(roles["gemeente"]) or "").strip().lower()
        if g:
            return g in LIMBURG_GEMEENTEN
    return True  # niets om op te filteren: bbox volstaat


def representative_point(geom):
    """Geeft (lon, lat) van een geometrie — gemiddelde van de coördinaten."""
    if not geom:
        return None
    pts = []

    def walk(c):
        if isinstance(c, (list, tuple)) and c and isinstance(c[0], (int, float)):
            pts.append(c[:2])
        elif isinstance(c, (list, tuple)):
            for x in c:
                walk(x)
    if geom.get("type") == "GeometryCollection":
        for g in geom.get("geometries", []):
            walk(g.get("coordinates"))
    else:
        walk(geom.get("coordinates"))
    if not pts:
        return None
    lon = sum(p[0] for p in pts) / len(pts)
    lat = sum(p[1] for p in pts) / len(pts)
    return [round(lon, 6), round(lat, 6)]


# ----------------------------------------------------------------------------
# Hoofdprogramma
# ----------------------------------------------------------------------------
def load_json(path, default):
    try:
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)
    except FileNotFoundError:
        return default


def save_json(path, obj, compact=False):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        if compact:
            json.dump(obj, fh, ensure_ascii=False, separators=(",", ":"), default=str)
        else:
            json.dump(obj, fh, ensure_ascii=False, indent=1, default=str)


def label(p, roles):
    for r in ("titel", "adres", "gemeente"):
        if roles.get(r) and p.get(roles[r]):
            return str(p[roles[r]])
    return "(zonder naam)"


def write_rss(path, changes, roles, now):
    items = []
    for c in changes[:200]:
        if c["type"] not in ("nieuw", "gewijzigd"):
            continue
        p = c.get("properties") or {}
        title = f"[{c['type'].upper()}] {label(p, roles)}"
        if roles.get("gemeente") and p.get(roles["gemeente"]):
            title += f" — {p[roles['gemeente']]}"
        desc = "<br>".join(f"<b>{escape(str(k))}</b>: {escape(str(v))}" for k, v in p.items() if v not in (None, ""))
        if c.get("diff"):
            desc = "<b>Gewijzigd:</b><br>" + "<br>".join(
                f"{escape(k)}: {escape(str(d['oud']))} → {escape(str(d['nieuw']))}" for k, d in c["diff"].items()
            ) + "<hr>" + desc
        pub = dt.datetime.fromisoformat(c["gezien"]).replace(tzinfo=dt.timezone.utc)
        link = f"{DASHBOARD_URL}#id={urllib.parse.quote(c['key'])}" if DASHBOARD_URL else ""
        items.append(
            f"<item><title>{escape(title)}</title>"
            + (f"<link>{escape(link)}</link>" if link else "")
            + f"<guid isPermaLink=\"false\">{escape(c['key'] + '|' + c['gezien'] + '|' + c['type'])}</guid>"
            f"<pubDate>{format_datetime(pub)}</pubDate>"
            f"<description><![CDATA[{desc}]]></description></item>"
        )
    xml = (
        '<?xml version="1.0" encoding="UTF-8"?>\n<rss version="2.0"><channel>'
        "<title>Vergunningsmonitor Limburg — omgevingsvergunningen (IIOA)</title>"
        f"<link>{escape(DASHBOARD_URL or 'https://www.mercator.vlaanderen.be')}</link>"
        "<description>Nieuwe en gewijzigde dossiers in Limburg</description>"
        f"<lastBuildDate>{format_datetime(now)}</lastBuildDate>"
        + "".join(items) + "</channel></rss>"
    )
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(xml)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--base-url", default=BASE_URL)
    ap.add_argument("--collection", default=COLLECTION)
    ap.add_argument("--inspect", action="store_true", help="Toon velden en voorbeelden, schrijf niets weg")
    ap.add_argument("--no-bbox", action="store_true", help="Haal heel Vlaanderen op (traag)")
    args = ap.parse_args()

    now = dt.datetime.now(dt.timezone.utc).replace(microsecond=0)
    print(f"Vergunningsmonitor — {now.isoformat()}")

    if args.inspect:
        feats = fetch_all(args.base_url, args.collection, LIMBURG_BBOX, limit_pages=1)
        keys, roles = detect_roles(feats)
        print(f"\n{len(feats)} records in eerste pagina.\nVelden: {keys}\nHerkende rollen: {json.dumps(roles, indent=1)}")
        for f in feats[:3]:
            print(json.dumps(f.get("properties"), ensure_ascii=False, indent=1, default=str))
        return

    feats = fetch_all(args.base_url, args.collection, None if args.no_bbox else LIMBURG_BBOX)
    print(f"Opgehaald: {len(feats)} records binnen de zoekzone")
    if not feats:
        sys.exit("Geen records ontvangen — controleer de URL of de collectienaam. Bestaande data blijft ongewijzigd.")

    keys, roles = detect_roles(feats)
    print(f"Herkende velden: {roles}")
    feats = [f for f in feats if is_limburg(f, roles)]
    print(f"Na Limburg-filter: {len(feats)} records")

    # ---- vergelijken met vorige run ---------------------------------------
    prev = load_json(STATE_FILE, {"records": {}, "first_run": None})
    prev_recs = prev.get("records", {})
    first_run = not prev_recs

    current, out_features = {}, []
    changes_new = []
    for f in feats:
        k = feature_key(f, roles)
        h = props_hash(f)
        p = f.get("properties") or {}
        old = prev_recs.get(k)
        eerst_gezien = old["eerst_gezien"] if old else now.isoformat()
        laatst_gewijzigd = old.get("laatst_gewijzigd", eerst_gezien) if old else now.isoformat()
        if old is None and not first_run:
            changes_new.append({"type": "nieuw", "key": k, "gezien": now.isoformat(), "properties": p})
        elif old is not None and old["hash"] != h:
            oldp = old.get("properties", {})
            diff = {kk: {"oud": oldp.get(kk), "nieuw": p.get(kk)}
                    for kk in sorted(set(oldp) | set(p)) if oldp.get(kk) != p.get(kk)}
            changes_new.append({"type": "gewijzigd", "key": k, "gezien": now.isoformat(), "properties": p, "diff": diff})
            laatst_gewijzigd = now.isoformat()
        current[k] = {"hash": h, "eerst_gezien": eerst_gezien, "laatst_gewijzigd": laatst_gewijzigd, "properties": p}
        out_features.append({
            "k": k,
            "pt": representative_point(f.get("geometry")),
            "p": p,
            "eg": eerst_gezien,
            "lw": laatst_gewijzigd,
        })
    if not first_run:
        for k, old in prev_recs.items():
            if k not in current:
                changes_new.append({"type": "verdwenen", "key": k, "gezien": now.isoformat(), "properties": old.get("properties", {})})

    # Veiligheidsrem: als ineens >50% verdwijnt, is er wellicht iets mis met de bron.
    removed = sum(1 for c in changes_new if c["type"] == "verdwenen")
    if prev_recs and removed > 0.5 * len(prev_recs):
        sys.exit(f"STOP: {removed} van {len(prev_recs)} records zouden verdwenen zijn. "
                 "Waarschijnlijk een probleem bij de bron; er wordt niets overschreven.")

    # ---- wijzigingslog ------------------------------------------------------
    changes_path = os.path.join(OUT_DIR, "changes.json")
    changes = changes_new + load_json(changes_path, [])
    cutoff = (now - dt.timedelta(days=CHANGES_KEEP_DAYS)).isoformat()
    changes = [c for c in changes if c["gezien"] >= cutoff]

    # ---- wegschrijven -------------------------------------------------------
    save_json(STATE_FILE, {"records": current, "first_run": prev.get("first_run") or now.isoformat()}, compact=True)
    save_json(os.path.join(OUT_DIR, "features.json"), out_features, compact=True)
    save_json(changes_path, changes, compact=True)
    runs = load_json(os.path.join(OUT_DIR, "meta.json"), {}).get("runs", [])
    runs = ([{"tijd": now.isoformat(), "totaal": len(current),
              "nieuw": sum(c["type"] == "nieuw" for c in changes_new),
              "gewijzigd": sum(c["type"] == "gewijzigd" for c in changes_new),
              "verdwenen": removed}] + runs)[:400]
    save_json(os.path.join(OUT_DIR, "meta.json"), {
        "bron": f"{args.base_url}/collections/{args.collection}",
        "laatste_update": now.isoformat(),
        "eerste_run": prev.get("first_run") or now.isoformat(),
        "aantal": len(current),
        "velden": keys,
        "rollen": roles,
        "runs": runs,
    })
    write_rss(os.path.join(OUT_DIR, "feed.xml"), changes, roles, now)

    print(f"Klaar. Totaal {len(current)} | nieuw {runs[0]['nieuw']} | gewijzigd {runs[0]['gewijzigd']} | verdwenen {removed}"
          + ("  (eerste run: alles als basislijn opgeslagen)" if first_run else ""))


if __name__ == "__main__":
    main()

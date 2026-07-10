#!/usr/bin/env python3
"""Sube el texto de las cartas de motivacion de ApplyPilot a Supabase (columna cl_text).

Se ejecuta EN TU PC (donde viven las cartas), no en la web. Solo usa la libreria
estandar de Python — no hay que instalar nada.

Uso:
  python upload_letters.py --dir "C:\\Users\\cdorr\\.applypilot"            # simulacro (no sube nada)
  python upload_letters.py --dir "C:\\Users\\cdorr\\.applypilot" --apply    # sube de verdad
  python upload_letters.py --dir ... --apply --force                        # re-sube tambien las que ya tienen texto

Que hace:
  1. Te pide tu contraseña del dashboard (mismo login que la web).
  2. Descarga la lista de ofertas de Supabase.
  3. Busca recursivamente en --dir ficheros de carta (.txt/.md/.docx cuyo nombre
     o carpeta contenga: carta, cover, letter, motivation, cl).
  4. Empareja fichero <-> oferta: primero por rowid en la ruta, si no por
     nombre de empresa (y palabras del titulo como desempate).
  5. En modo --apply sube el texto a la columna cl_text; sin --apply solo
     muestra el plan para que lo revises.
"""
import argparse
import getpass
import json
import re
import sys
import unicodedata
import urllib.parse
import urllib.request
import zipfile
from pathlib import Path
from xml.etree import ElementTree

SUPABASE_URL = "https://wzikfazbfgtxljtkkphm.supabase.co"
ANON_KEY = "sb_publishable_pMmT_8oxKBcqU-fD_4yTqA_YKNVm6jN"
DEFAULT_EMAIL = "carlos.dorronsoro.malaga@gmail.com"
LETTER_HINTS = ("carta", "cover", "letter", "motivation", "cl_", "_cl")
STOPWORDS = {"the", "and", "for", "with", "de", "la", "el", "y", "en", "a", "of",
             "bv", "b.v", "nv", "group", "inc", "ltd", "gmbh"}


def norm(s):
    s = unicodedata.normalize("NFKD", s or "").encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z0-9]+", " ", s.lower()).strip()


def words(s):
    return {w for w in norm(s).split() if len(w) > 2 and w not in STOPWORDS}


def api(path, token, method="GET", body=None):
    req = urllib.request.Request(SUPABASE_URL + path, method=method)
    req.add_header("apikey", ANON_KEY)
    req.add_header("Authorization", "Bearer " + token)
    req.add_header("Content-Type", "application/json")
    if method == "PATCH":
        req.add_header("Prefer", "return=minimal")
    data = json.dumps(body).encode() if body is not None else None
    with urllib.request.urlopen(req, data=data) as r:
        raw = r.read()
        return json.loads(raw) if raw.strip() else None


def login(email, password):
    req = urllib.request.Request(
        SUPABASE_URL + "/auth/v1/token?grant_type=password", method="POST")
    req.add_header("apikey", ANON_KEY)
    req.add_header("Content-Type", "application/json")
    body = json.dumps({"email": email, "password": password}).encode()
    with urllib.request.urlopen(req, data=body) as r:
        return json.loads(r.read())["access_token"]


def fetch_jobs(token):
    jobs, offset = [], 0
    while True:
        page = api(f"/rest/v1/applyboard_jobs?select=rowid,url,company,title,cl_exists,cl_text"
                   f"&limit=1000&offset={offset}", token)
        jobs += page
        if len(page) < 1000:
            return jobs
        offset += 1000


def read_docx(path):
    ns = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
    with zipfile.ZipFile(path) as z:
        root = ElementTree.fromstring(z.read("word/document.xml"))
    paras = ["".join(t.text or "" for t in p.iter(ns + "t")) for p in root.iter(ns + "p")]
    return "\n".join(paras).strip()


def read_letter(path):
    if path.suffix.lower() == ".docx":
        return read_docx(path)
    return path.read_text(encoding="utf-8", errors="replace").strip()


def find_letter_files(base):
    out = []
    for p in base.rglob("*"):
        if p.suffix.lower() not in (".txt", ".md", ".docx"):
            continue
        hint_zone = norm(str(p.relative_to(base)))
        if any(h.strip("_") in hint_zone for h in LETTER_HINTS):
            out.append(p)
    return out


def match(jobs, files, base):
    """Devuelve lista de (job, path, motivo). Prioriza rowid > empresa+titulo > empresa."""
    plan, used = [], set()
    scored = []
    for j in jobs:
        cw, tw = words(j.get("company") or ""), words(j.get("title") or "")
        for p in files:
            rel = norm(str(p.relative_to(base)))
            score, why = 0, ""
            if re.search(rf"(?<!\d){j['rowid']}(?!\d)", str(p)):
                score, why = 1000, f"rowid {j['rowid']} en la ruta"
            else:
                hits_c = sum(1 for w in cw if w in rel)
                hits_t = sum(1 for w in tw if w in rel)
                if cw and hits_c == len(cw):
                    score = 100 + hits_t
                    why = f"empresa '{j['company']}'" + (f" + {hits_t} palabras del titulo" if hits_t else "")
            if score:
                scored.append((score, j, p, why))
    for score, j, p, why in sorted(scored, key=lambda x: -x[0]):
        jkey, fkey = j["url"], str(p)
        if jkey in used or fkey in used:
            continue
        used.add(jkey); used.add(fkey)
        plan.append((j, p, why))
    return plan


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--dir", required=True, help="Carpeta raiz de ApplyPilot (ej: C:\\Users\\cdorr\\.applypilot)")
    ap.add_argument("--apply", action="store_true", help="Subir de verdad (sin esto: simulacro)")
    ap.add_argument("--force", action="store_true", help="Re-subir tambien ofertas que ya tienen cl_text")
    ap.add_argument("--email", default=DEFAULT_EMAIL)
    args = ap.parse_args()

    base = Path(args.dir)
    if not base.is_dir():
        sys.exit(f"No existe la carpeta: {base}")

    files = find_letter_files(base)
    print(f"Encontrados {len(files)} ficheros con pinta de carta bajo {base}")
    if not files:
        sys.exit("Nada que subir. Revisa --dir o los nombres de fichero (deben contener "
                 "'carta', 'cover', 'letter', 'motivation' o 'cl').")

    token = login(args.email, getpass.getpass(f"Contraseña del dashboard ({args.email}): "))
    jobs = fetch_jobs(token)
    print(f"{len(jobs)} ofertas en Supabase, {sum(1 for j in jobs if j.get('cl_exists'))} con cl_exists=1")

    pending = [j for j in jobs if args.force or not j.get("cl_text")]
    plan = match(pending, files, base)

    print(f"\nPLAN ({len(plan)} emparejamientos):")
    for j, p, why in plan:
        print(f"  {j['company']} — {(j['title'] or '')[:55]}\n      <- {p.relative_to(base)}   [{why}]")

    matched_files = {str(p) for _, p, _ in plan}
    orphan_files = [p for p in files if str(p) not in matched_files]
    if orphan_files:
        print(f"\nSin emparejar ({len(orphan_files)} ficheros): revisa a mano si corresponden a alguna oferta")
        for p in orphan_files[:20]:
            print(f"  ? {p.relative_to(base)}")

    if not args.apply:
        print("\nSimulacro: no se ha subido nada. Repite con --apply para subir.")
        return

    ok = 0
    for j, p, _ in plan:
        try:
            text = read_letter(p)
            if not text:
                print(f"  VACIO, saltado: {p}")
                continue
            api("/rest/v1/applyboard_jobs?url=eq." + urllib.parse.quote(j["url"], safe=""),
                token, "PATCH", {"cl_text": text})
            ok += 1
            print(f"  ✓ {j['company']} — {(j['title'] or '')[:55]}")
        except Exception as e:
            print(f"  ✗ {j['company']}: {e}")
    print(f"\nSubidas {ok}/{len(plan)} cartas. Refresca la web y veras el boton ✉️ Carta con el texto.")


if __name__ == "__main__":
    main()

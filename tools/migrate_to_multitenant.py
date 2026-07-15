#!/usr/bin/env python3
"""Copia ofertas de una tabla ANTIGUA (applyboard_jobs / applyboard_jobs_alejandra)
a la tabla MULTI-TENANT nueva `jobs`, poniendo tu user_id automáticamente.

Sirve para PROBAR el producto nuevo (ApplyBoard multi-tenant) con tus datos
reales sin tocar el PC. Solo librería estándar de Python — no instala nada.

Cómo funciona el aislamiento: te logueas con TU email; la columna `user_id` de
`jobs` tiene default = auth.uid(), así que cada fila que insertas queda marcada
como tuya y RLS impide que nadie más la vea. No hay que pasar user_id a mano.

Uso:
  # Simulacro (no escribe nada, enseña el plan):
  python migrate_to_multitenant.py --from applyboard_jobs --email TU_EMAIL

  # De verdad:
  python migrate_to_multitenant.py --from applyboard_jobs --email TU_EMAIL --apply

  # Para Alejandra (su tabla -> jobs, con SU login):
  python migrate_to_multitenant.py --from applyboard_jobs_alejandra \
      --email EMAIL_DE_ALEJANDRA --blocking-language-from needs_french --apply

Notas:
  * --blocking-language-from: nombre de la columna booleana de la tabla antigua
    que marca "idioma que NO habla" (para Carlos: needs_dutch -> 'holandés';
    para Alejandra: needs_french -> 'francés'). Se traduce al campo genérico
    `blocking_language`. Si se omite, no se rellena.
  * Es idempotente por (user_id, url): reejecutar hace UPSERT, no duplica.
  * No copia cl_text (las cartas se suben aparte con upload_letters.py --table jobs).
"""
import argparse
import getpass
import json
import sys
import urllib.request

SUPABASE_URL = "https://wzikfazbfgtxljtkkphm.supabase.co"
ANON_KEY = "sb_publishable_pMmT_8oxKBcqU-fD_4yTqA_YKNVm6jN"

# Columnas comunes que copiamos tal cual si existen en la tabla origen.
PASSTHROUGH = [
    "url", "application_url", "company", "title", "location", "site", "descr",
    "fit_score", "score_reasoning", "discovered_at", "applied_at", "apply_status",
    "response_status", "starred", "followup_sent_at", "cv_version", "cv_exists",
    "cl_exists", "is_nl", "is_spain", "is_be", "is_lu", "is_ireland", "is_europe",
    "is_remote", "is_us", "is_senior", "is_internship", "easy_apply",
    "salary_text", "conditions_notes", "job_type",
]
LABEL = {"needs_french": "francés", "needs_dutch": "holandés",
         "needs_german": "alemán", "needs_italian": "italiano"}


def api(path, token, method="GET", body=None, prefer=None):
    req = urllib.request.Request(SUPABASE_URL + path, method=method)
    req.add_header("apikey", ANON_KEY)
    req.add_header("Authorization", "Bearer " + token)
    req.add_header("Content-Type", "application/json")
    if prefer:
        req.add_header("Prefer", prefer)
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


def fetch_all(table, token):
    rows, offset = [], 0
    while True:
        page = api(f"/rest/v1/{table}?select=*&limit=1000&offset={offset}", token)
        rows += page
        if len(page) < 1000:
            return rows
        offset += 1000


def map_row(src, blocking_col):
    """Traduce una fila antigua al esquema de `jobs`. Defensivo: usa .get()."""
    out = {}
    for c in PASSTHROUGH:
        if c in src and src[c] is not None:
            out[c] = src[c]
    # notas: carlos_notes / notes -> user_notes
    notes = src.get("carlos_notes") or src.get("user_notes") or src.get("notes")
    if notes:
        out["user_notes"] = notes
    # idioma bloqueante: de la columna booleana indicada -> nombre del idioma
    if blocking_col and src.get(blocking_col):
        out["blocking_language"] = LABEL.get(blocking_col, blocking_col)
    return out


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--from", dest="source", required=True,
                    help="Tabla origen: applyboard_jobs | applyboard_jobs_alejandra")
    ap.add_argument("--email", required=True, help="Email con el que te logueas")
    ap.add_argument("--blocking-language-from", dest="blocking_col", default=None,
                    help="Columna booleana de idioma no hablado (needs_dutch / needs_french)")
    ap.add_argument("--apply", action="store_true", help="Escribir de verdad (sin esto: simulacro)")
    args = ap.parse_args()

    token = login(args.email, getpass.getpass(f"Contraseña ({args.email}): "))
    src_rows = fetch_all(args.source, token)
    print(f"{len(src_rows)} ofertas en {args.source}")

    mapped = [map_row(r, args.blocking_col) for r in src_rows]
    mapped = [m for m in mapped if m.get("url")]
    print(f"{len(mapped)} con URL válida, listas para copiar a `jobs`")
    if args.blocking_col:
        n = sum(1 for m in mapped if m.get("blocking_language"))
        print(f"  ({n} marcadas con idioma bloqueante = '{LABEL.get(args.blocking_col, args.blocking_col)}')")

    if not args.apply:
        print("\nSimulacro. Ejemplo de la primera fila mapeada:")
        if mapped:
            print(json.dumps(mapped[0], indent=2, ensure_ascii=False)[:800])
        print("\nRepite con --apply para escribir en `jobs`.")
        return

    # UPSERT por (user_id, url). user_id lo pone el default = auth.uid().
    ok = 0
    for i in range(0, len(mapped), 100):
        batch = mapped[i:i + 100]
        try:
            api("/rest/v1/jobs?on_conflict=user_id,url", token, "POST", batch,
                prefer="resolution=merge-duplicates,return=minimal")
            ok += len(batch)
            print(f"  subidas {ok}/{len(mapped)}")
        except Exception as e:
            print(f"  ✗ lote {i}-{i+len(batch)}: {e}")
    print(f"\nHecho: {ok}/{len(mapped)} ofertas en `jobs`. Abre ApplyBoard y refresca.")


if __name__ == "__main__":
    main()

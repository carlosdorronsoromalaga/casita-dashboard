-- ============================================================================
-- ApplyBoard — esquema MULTI-TENANT (una sola app para N usuarios)
-- Pegar en: Supabase Dashboard -> SQL Editor -> Run. Es idempotente:
-- se puede re-ejecutar sin borrar datos.
--
-- FILOSOFÍA (por qué así):
--   * UNA tabla `jobs` para todos, con columna `user_id`. No una tabla por
--     persona. Alta de un usuario nuevo = crear su login + una fila en
--     `profiles`; CERO cambios de esquema, CERO SQL nuevo.
--   * El AISLAMIENTO lo garantiza el MOTOR (Postgres RLS), no el código de la
--     web. La política es UNA regla igual para todos: `auth.uid() = user_id`.
--     Aunque el frontend tuviera un bug y olvidara un filtro, Postgres NUNCA
--     devuelve una fila de otro usuario. Es imposible que un job de uno
--     aparezca en la cuenta de otro. Este es el requisito innegociable.
--   * Todo lo que hoy está QUEMADO en el HTML de cada persona (palabras clave
--     de sector, etiquetas de CV, plantillas de outreach, tema de color) vive
--     ahora en `profiles`. La web lo pinta dinámicamente. Bonus: saca las
--     plantillas del repo público.
--   * Borrado = SOFT DELETE (`deleted_at`). "Eliminar" en la interfaz nunca
--     pierde datos: se puede recuperar.
-- ============================================================================

-- ---------------------------------------------------------------------------
-- 0. Utilidad: trigger para mantener updated_at al día
-- ---------------------------------------------------------------------------
create or replace function public.touch_updated_at()
returns trigger language plpgsql as $$
begin
  new.updated_at = now();
  return new;
end $$;

-- ---------------------------------------------------------------------------
-- 1. profiles — configuración por usuario (1 fila por persona)
-- ---------------------------------------------------------------------------
create table if not exists public.profiles (
  user_id           uuid primary key references auth.users(id) on delete cascade,
  display_name      text,                       -- "Carlos", "Alejandra" (cabecera + login)
  full_name         text,                       -- firma de cartas / asunto de emails
  theme             text not null default 'blue',  -- identidad visual: blue | purple | green | amber
  -- Palabras clave de sector: alimentan la pestaña/filtro "Mi sector" y el
  -- realce 🎯. Coma-separadas. Cada usuario define las suyas -> filtro dinámico,
  -- no una regex quemada en el código.
  sector_keywords   text not null default '',   -- Carlos: "ai, ia, machine learning, llm, genai"
                                                 -- Alejandra: "wealth management, private banking, family office"
  sector_label      text not null default 'Mi sector',  -- etiqueta de la pestaña
  -- Ámbitos geográficos que aparecen en el desplegable de este usuario.
  -- Array de {value,label}. Si vacío, la web usa un set por defecto.
  scopes            jsonb not null default '[]'::jsonb,
  -- Etiquetas de las versiones de CV (el nº de CV lo sugiere ApplyPilot).
  -- {"1":"CV1 · Español", "2":"CV2 · English", ...}
  cv_labels         jsonb not null default '{}'::jsonb,
  -- Plantillas de outreach (follow-ups, LinkedIn, puerta fría...).
  -- [{"id":"fu_applied","title":"...","body":"..."}]  -> fuera del HTML público.
  templates         jsonb not null default '[]'::jsonb,
  created_at        timestamptz not null default now(),
  updated_at        timestamptz not null default now()
);

drop trigger if exists profiles_touch on public.profiles;
create trigger profiles_touch before update on public.profiles
  for each row execute function public.touch_updated_at();

-- ---------------------------------------------------------------------------
-- 2. jobs — ofertas (1 fila por oferta y por usuario)
-- ---------------------------------------------------------------------------
create table if not exists public.jobs (
  id                bigint generated always as identity primary key,
  user_id           uuid not null default auth.uid() references auth.users(id) on delete cascade,

  -- identidad de la oferta
  url               text not null,
  application_url   text,
  company           text,
  title             text,
  location          text,
  site              text,                        -- fuente: linkedin, indeed, manual...
  descr             text,

  -- scoring (lo rellena ApplyPilot)
  fit_score         int,
  score_reasoning   text,

  -- estado de la candidatura
  discovered_at     timestamptz default now(),
  applied_at        timestamptz,
  apply_status      text,                        -- null | applied | skipped
  response_status   text,                        -- sin_respuesta | entrevista | en_proceso | oferta | rechazo
  starred           int not null default 0,
  followup_sent_at  timestamptz,
  user_notes        text,                        -- notas de la persona (genérico, no "carlos_notes")

  -- documentos
  cv_version        int,                         -- qué CV usar (1..N, etiquetas en profiles.cv_labels)
  cv_exists         int not null default 0,      -- hay CV preparado en el PC
  cl_exists         int not null default 0,      -- hay carta en el PC
  cl_text           text,                        -- texto de la carta subido a la nube

  -- flags geográficos (objetivos: un job en NL es is_nl=1 para todos)
  is_nl             int not null default 0,
  is_spain          int not null default 0,
  is_be             int not null default 0,
  is_lu             int not null default 0,
  is_ireland        int not null default 0,
  is_europe         int not null default 0,
  is_remote         int not null default 0,
  is_us             int not null default 0,

  -- filtros generales (casillas comunes a todos)
  is_senior         int not null default 0,      -- pide senior/lead
  is_internship     int not null default 0,      -- es prácticas/beca
  easy_apply        int not null default 0,

  -- idioma bloqueante: ApplyPilot conoce los idiomas del usuario (profiles) y,
  -- si la oferta exige uno que NO habla, guarda aquí su nombre. La web filtra
  -- por "hay idioma bloqueante" (genérico) y muestra el chip "pide <idioma>".
  -- Carlos: se rellena con "holandés"; Alejandra: con "francés". Mismo campo.
  blocking_language text,

  -- condiciones (relevante para ofertas fuera: salario vs coste de vida, mudanza)
  salary_text       text,
  conditions_notes  text,
  job_type          text,                        -- indefinido | graduate_program | traineeship | practicas | temporal

  -- soft delete: "eliminar" en la interfaz pone deleted_at; nunca borra de verdad
  deleted_at        timestamptz,

  created_at        timestamptz not null default now(),
  updated_at        timestamptz not null default now(),

  -- unicidad POR USUARIO: dos personas pueden tener la misma URL de oferta,
  -- cada una la suya. La clave es (user_id, url), no url a secas.
  unique (user_id, url)
);

create index if not exists jobs_user_idx      on public.jobs (user_id) where deleted_at is null;
create index if not exists jobs_user_fit_idx  on public.jobs (user_id, fit_score desc) where deleted_at is null;

drop trigger if exists jobs_touch on public.jobs;
create trigger jobs_touch before update on public.jobs
  for each row execute function public.touch_updated_at();

-- ---------------------------------------------------------------------------
-- 3. contacts — puerta fría (1 fila por contacto y por usuario)
-- ---------------------------------------------------------------------------
create table if not exists public.contacts (
  id                bigint generated always as identity primary key,
  user_id           uuid not null default auth.uid() references auth.users(id) on delete cascade,
  company           text not null,
  name              text,
  role              text,
  email             text,
  linkedin          text,
  status            text not null default 'por_contactar',
  notes             text,
  source            text,
  last_contacted_at timestamptz,
  deleted_at        timestamptz,
  created_at        timestamptz not null default now(),
  updated_at        timestamptz not null default now()
);

create index if not exists contacts_user_idx on public.contacts (user_id) where deleted_at is null;

drop trigger if exists contacts_touch on public.contacts;
create trigger contacts_touch before update on public.contacts
  for each row execute function public.touch_updated_at();

-- ---------------------------------------------------------------------------
-- 4. SEGURIDAD (RLS) — la parte que hace imposible el cruce de datos
-- ---------------------------------------------------------------------------
-- Una sola regla, igual para todos, aplicada por el motor en CADA consulta.
-- `auth.uid()` es el id del usuario del token de sesión; sólo ve/escribe sus
-- filas. No hay emails quemados, no hay que tocar nada al dar de alta a nadie.
alter table public.profiles enable row level security;
alter table public.jobs     enable row level security;
alter table public.contacts enable row level security;

drop policy if exists "own_profile" on public.profiles;
create policy "own_profile" on public.profiles for all to authenticated
  using (auth.uid() = user_id) with check (auth.uid() = user_id);

drop policy if exists "own_jobs" on public.jobs;
create policy "own_jobs" on public.jobs for all to authenticated
  using (auth.uid() = user_id) with check (auth.uid() = user_id);

drop policy if exists "own_contacts" on public.contacts;
create policy "own_contacts" on public.contacts for all to authenticated
  using (auth.uid() = user_id) with check (auth.uid() = user_id);

-- El sync desde el PC usa la service_role key, que IGNORA RLS. Por eso el
-- script de sync DEBE fijar user_id en cada fila que inserta (el user_id del
-- dueño de esa instancia de ApplyPilot). Ver applypilot_multitenant/.

-- ---------------------------------------------------------------------------
-- 5. Alta de un usuario nuevo (plantilla — ejecutar 1 vez por persona)
-- ---------------------------------------------------------------------------
-- Después de crear el usuario en Authentication -> Users, copia su UUID
-- (columna "UID" de la tabla de usuarios) y crea su perfil:
--
--   insert into public.profiles (user_id, display_name, full_name, theme,
--                                 sector_keywords, sector_label, cv_labels)
--   values ('UUID-DEL-USUARIO', 'Nombre', 'Nombre Apellido', 'blue',
--           'palabra1, palabra2', 'Mi sector',
--           '{"1":"CV1 · ...","2":"CV2 · ..."}'::jsonb)
--   on conflict (user_id) do nothing;
--
-- (La web también crea un perfil por defecto la primera vez que el usuario
--  entra, así que este paso es opcional para arrancar — pero recomendable para
--  fijar nombre, tema y palabras clave desde el principio.)

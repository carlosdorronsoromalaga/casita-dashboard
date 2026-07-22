-- OPCIONAL — buzón de solicitudes de alta (onboarding).
-- Sin esto, el formulario /onboarding/ sigue funcionando: manda el resumen por
-- email/copia a Carlos. Con esto, ademas queda registrado en Supabase para
-- tener un historial ordenado de altas.
--
-- Pegar en: Supabase Dashboard -> SQL Editor -> Run.

create table if not exists public.onboarding_requests (
  id          bigint generated always as identity primary key,
  name        text,
  email       text,
  payload     jsonb not null,           -- todo el formulario, tal cual
  status      text not null default 'nuevo',  -- nuevo | en_proceso | activado | descartado
  created_at  timestamptz not null default now()
);

alter table public.onboarding_requests enable row level security;

-- El formulario es PUBLICO (sin login): se permite INSERT anonimo, pero NADIE
-- puede leer/editar sin service_role. Asi las altas entran pero no son visibles
-- para otros. Carlos las lee con la service key / desde el panel de Supabase.
drop policy if exists "anon_insert_onboarding" on public.onboarding_requests;
create policy "anon_insert_onboarding" on public.onboarding_requests
  for insert to anon, authenticated with check (true);

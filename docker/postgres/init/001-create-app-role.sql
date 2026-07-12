do $$
begin
  if not exists (select from pg_catalog.pg_roles where rolname = 'league_api') then
    create role league_api login password 'league_api';
  end if;
end
$$;

grant all privileges on database league_api to league_api;

\connect league_api

grant all on schema public to league_api;
alter default privileges in schema public grant all on tables to league_api;
alter default privileges in schema public grant all on sequences to league_api;

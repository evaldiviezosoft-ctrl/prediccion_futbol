do $$
begin
  if to_regprocedure('public.rls_auto_enable()') is not null then
    execute 'revoke execute on function public.rls_auto_enable() from public, anon, authenticated';
  end if;
end;
$$;

create index fixtures_league_kickoff_idx
  on public.fixtures (league_id, kickoff);

create index predictions_league_id_idx
  on public.predictions (league_id);

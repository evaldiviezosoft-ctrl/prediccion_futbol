-- Prevent a slow, older AI response from replacing a newer attempt or a
-- calibration based on a superseded statistical prediction.
create or replace function public.publish_prediction_calibration(
  p_calibration_id uuid
)
returns public.prediction_calibrations
language plpgsql
security invoker
set search_path = pg_catalog
as $$
declare
  target_fixture_id bigint;
  latest_attempt_number integer;
  current_prediction_updated_at timestamptz;
  published_calibration public.prediction_calibrations;
begin
  select calibration.fixture_id
  into target_fixture_id
  from public.prediction_calibrations as calibration
  where calibration.id = p_calibration_id;

  if not found then
    raise exception 'Prediction calibration not found'
      using errcode = 'P0002';
  end if;

  -- All publication decisions for one fixture are serialized.
  perform pg_catalog.pg_advisory_xact_lock(target_fixture_id);

  select calibration.*
  into published_calibration
  from public.prediction_calibrations as calibration
  where calibration.id = p_calibration_id
  for update;

  if not found then
    raise exception 'Prediction calibration not found'
      using errcode = 'P0002';
  end if;

  if published_calibration.status <> 'updated' then
    raise exception 'Only an updated prediction calibration can be published'
      using errcode = '23514';
  end if;

  select max(calibration.attempt_number)
  into latest_attempt_number
  from public.prediction_calibrations as calibration
  where calibration.fixture_id = target_fixture_id;

  if published_calibration.attempt_number <> latest_attempt_number then
    raise exception 'Only the latest prediction calibration attempt can be published'
      using errcode = '40001';
  end if;

  -- Lock the current base prediction through commit so it cannot change
  -- between this validation and publication.
  select prediction.updated_at
  into current_prediction_updated_at
  from public.predictions as prediction
  where prediction.fixture_id = target_fixture_id
  for update;

  if not found then
    raise exception 'Base prediction not found'
      using errcode = 'P0002';
  end if;

  if published_calibration.base_prediction_updated_at
      is distinct from current_prediction_updated_at then
    raise exception 'Prediction calibration is based on a superseded prediction'
      using errcode = '40001';
  end if;

  update public.prediction_calibrations
  set
    published = false,
    superseded_at = coalesce(superseded_at, pg_catalog.now())
  where fixture_id = target_fixture_id
    and id <> p_calibration_id
    and published;

  update public.prediction_calibrations
  set
    published = true,
    published_at = coalesce(published_at, pg_catalog.now()),
    superseded_at = null
  where id = p_calibration_id
  returning *
  into published_calibration;

  return published_calibration;
end;
$$;

revoke all on function public.publish_prediction_calibration(uuid)
  from public, anon, authenticated, service_role;
grant execute on function public.publish_prediction_calibration(uuid)
  to service_role;

comment on function public.publish_prediction_calibration(uuid) is
  'Atomically publishes only the latest attempt built from the current base prediction; service role only.';

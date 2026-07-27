create index if not exists prediction_evaluations_prediction_version_id_idx
  on public.prediction_evaluations (prediction_version_id)
  where prediction_version_id is not null;

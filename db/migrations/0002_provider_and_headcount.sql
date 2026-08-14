-- 0002_provider_and_headcount.sql
--
-- Two additions:
--   1. job_scores.provider — which LLM vendor produced this score. `model`
--      alone is ambiguous now that llama-3.3-70b is served by both Groq and
--      Cerebras, and calibration can differ per vendor even for one model id.
--   2. companies.headcount_band — company size, which no ATS response exposes,
--      so it is seeded by hand. Nullable and defaulted to 'unknown' so an
--      untagged company is never penalised.

alter table job_scores
  add column if not exists provider text;

comment on column job_scores.provider is
  'LLM vendor that produced this score: google | groq | cerebras | prefilter';

alter table companies
  add column if not exists headcount_band text
    not null default 'unknown'
    check (headcount_band in ('micro','small','mid','large','unknown'));

comment on column companies.headcount_band is
  'micro <25 | small 25-100 | mid 100-1000 | large 1000+ | unknown. '
  'Seeded manually — no ATS exposes headcount. Only micro carries a penalty.';

create index if not exists companies_headcount_idx on companies (headcount_band);

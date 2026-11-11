# GreenQueue demo kit

## 30-second pitch

> Data centres are full of flexible batch work, but most of it runs whenever a
> cron expression says so. Google's carbon-intelligent computing and
> Microsoft's carbon-aware scheduling already shift compute to cleaner hours at
> data-centre scale. GreenQueue brings that idea to ordinary teams: submit a job
> and a deadline, and it scans the next 48 hours of UK grid carbon, runs in the
> cleanest feasible window, and reports the CO₂ avoided. The whole system is
> serverless and event-driven. At 1,000 teams, the same scheduling saves about
> 42 tonnes of CO₂ a year.

## Two-minute stage script

**Before you start:** run `./deploy.sh` (defaults to a reliable synthetic
forecast), open the printed dashboard URL, and confirm the stat counters look
populated. If you want live grid data, run `MOCK_MODE=false ./deploy.sh`
instead. The page falls back to preview data automatically if the API is
unreachable, so do not restart the demo if the network drops.

### 0:00–0:20 — The problem

> Every cloud team has work that does not care when it runs: nightly ETL, model
> retraining, media transcoding, report generation. It all runs at a fixed time,
> even when the grid is at its dirtiest. The grid's carbon intensity swings by
> 2–3× each day. Google and Microsoft already move flexible compute into the
> clean hours. Most teams do not have a scheduling layer to do the same.

### 0:20–0:40 — The product

Point at the hero panel.

> This is GreenQueue. The hero shows the cleanest four-hour window in the next
> 24 hours, with the live intensity strip underneath. Below it is the full
> 48-hour forecast. The chart is coloured by intensity, and every dot is a job
> that GreenQueue placed inside this window.

### 0:40–1:05 — Schedule a job

Scroll to the form, enter a name (for example, **Podcast archive re-encode**),
set **2.5 hours**, and keep the default deadline. Press **Schedule job**.

> The form validates inline, then the Lambda validates again and calls the
> planner. The planner scans 96 half-hour slots, rejects windows that miss the
> deadline, and picks the lowest average intensity. It computes energy at 100
> watts per job-hour and returns the grams of CO₂ avoided versus running now.

### 1:05–1:25 — The result

When the success panel appears:

> It chose this window, not the current one. The job is now queued, and a marker
> has appeared on the forecast at exactly that start time. The dashboard totals
> updated: total grams avoided, jobs scheduled, and the average reduction across
> the queue.

Scroll to the queue.

> Every job carries its window, live status, and projected saving. EventBridge
> wakes a runner every minute; due jobs move from queued to running to completed
> with conditional DynamoDB writes, so two ticks can never double-start a job.

### 1:25–1:45 — Architecture

> The stack is one SAM template. API Gateway HTTP API, four Python Lambdas, a
> shared planner layer, DynamoDB, an EventBridge rule, and an S3-hosted
> dashboard. Each function has only the DynamoDB permissions it needs. The
> carbon API needs no key, and if it fails or `MOCK_MODE` is on, the planner uses
> a synthetic curve with a dirty evening peak and a clean overnight dip. The
> demo cannot break on stage.

### 1:45–2:00 — Impact and scale

> At 1,000 teams, 10 flexible jobs per team per week, 8 job-hours each, and 40%
> carbon reduction, that is 416 MWh shifted a year and about 42 tonnes of CO₂
> avoided. That is the point: no new hardware, no sacrificed deadlines, just
> better timing.

Close with the dashboard visible.

## Scaling statement, with the calculation

Assumptions, stated plainly:

- 1,000 teams use GreenQueue.
- Each team runs 10 flexible batch jobs per week.
- Each job averages 8 job-hours of compute.
- Power draw is 100 W per job-hour (0.1 kWh).
- Shifting into the cleanest feasible window saves 40% of the emissions versus
  running immediately.
- Grid carbon intensity averages 250 gCO₂/kWh.

```text
Weekly job-hours
  1,000 teams × 10 jobs/week × 8 h           = 80,000 job-hours/week

Weekly energy
  80,000 job-hours × 0.1 kWh/job-hour        = 8,000 kWh/week

Annual energy
  8,000 kWh/week × 52 weeks                  = 416,000 kWh/year
                                              = 416 MWh/year

Emissions if run immediately
  416,000 kWh × 0.25 kgCO₂/kWh               = 104,000 kgCO₂/year
                                              = 104 tonnes/year

With a 40% reduction from timing
  104 tonnes × 0.40                          = 41.6 tonnes CO₂/year

Answer: ≈ 42 tonnes CO₂ avoided per year
```

This is an illustrative aggregate, not a measurement of any one company's
workload. The dashboard demonstrates the method on real per-job data. The
saving scales linearly with flexible energy and with the carbon spread of the
grid; both are conservative inputs here.

## Judge Q&A

**Why not just run at night?**
Fixed overnight windows are a guess. GreenQueue uses the actual 48-hour forecast
per job, respects each deadline, and adapts when wind, demand, or the season
changes the curve.

**How do you know the CO₂ number is real?**
Grid intensity is the measured/forecast UK average in gCO₂/kWh. Energy is a
stated assumption of 100 W per job-hour. The comparison is like-for-like: the
same energy, evaluated at the clean window versus immediately. The assumption is
visible in the UI and README rather than hidden.

**What happens if the carbon API goes down?**
`MOCK_MODE` can force a deterministic synthetic curve. With live mode, any API
or parse failure also falls back to synthetic data with a `fallback_reason`. The
dashboard labels the source, and the demo continues.

**Is this really serverless?**
Yes. API Gateway, Lambda, DynamoDB on demand, EventBridge, and S3 static
hosting. No servers, no NAT Gateway, no database cluster, and no always-on
compute. At demo volume the bill rounds to zero.

**What about security?**
IAM is least privilege per function, the table is not publicly reachable, and
the S3 bucket policy only permits public `GetObject`. The public API deliberately
has no auth for the hackathon; production would add Cognito/API keys, throttling,
WAF, and per-tenant quotas.

**How does it scale?**
Submission and listing are independent Lambda requests. The scan-based runner is
the first thing to change at high volume: use a GSI on a due-time bucket, query
only due partitions, and move execution to AWS Batch/ECS. The planner is O(96 ×
duration slots) per request, effectively constant at this horizon.

**Why does the job complete so quickly?**
Execution is simulated for the demo: one tick marks it running, the next tick
completes it. The scheduling and measurement path is real; the compute launch is
the part that becomes Batch/ECS.

## Next steps

### 1. Real workloads with AWS Batch or ECS

Replace the simulated completion in the runner with a `SubmitJob` call to AWS
Batch (or an ECS `RunTask`). Store the AWS Batch job ID on the DynamoDB item.
Use a Step Functions workflow or EventBridge rule on job state changes to write
`COMPLETED` back. Keep the same conditional status transitions so retries are
idempotent. Add the job's vCPU and memory to the CO₂ calculation instead of the
flat 100 W assumption.

### 2. Multi-region carbon optimisation

Query carbon intensity for several regions or grid zones. For workloads without
data-residency constraints, choose the region with the cleanest feasible window,
including network transfer in the carbon estimate. Add a policy layer for data
locality, latency, and egress cost. The planner already has the right shape:
forecast per zone, feasible window search, explicit objective. Extend it to a
small constrained optimisation rather than a single-zone minimum.

### 3. Slack notifications

EventBridge already emits job lifecycle events. Add a small Lambda that formats
`job_submitted`, `job_started`, and `job_completed` events and posts them to a
Slack incoming webhook, with the scheduled window and CO₂ saved. Keep the
webhook in Secrets Manager; never commit it. A daily digest can sum saved grams
by team and compare it with the previous week.

### Smaller production steps

- Cognito or API-key auth, request quotas, and WAF on the public API.
- A DynamoDB GSI on due-time buckets instead of scanning every minute.
- CloudFront in front of the S3 website for HTTPS and a custom domain.
- Structured logs, alarms on runner errors, and an SLO for job start latency.
- Batteries-included energy factors per instance family, not a flat 100 W.
- Per-team carbon budgets and a "greener alternative" recommendation API.

## Runbook for the live demo

1. `./deploy.sh` (synthetic) or `MOCK_MODE=false ./deploy.sh` (live).
2. Open the printed website URL and check that the header says **Live forecast**
   or **Synthetic forecast**, not **Connecting**.
3. Confirm the job table has 15 rows from the seed. If not, run
   `python seed.py --table GreenQueueJobs --region eu-west-2`.
4. Submit one disposable job before going on stage to confirm the success state.
5. If the page ever loses the API, it switches to **Preview data** and remains
   fully interactive. Do not reload; keep presenting. The queue is local in that
   mode and is labelled as such.
6. If Chart.js is blocked, the page shows a fallback message and the queue still
   works. Do not spend stage time debugging the CDN.

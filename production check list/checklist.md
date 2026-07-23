The design can support 1,000 users conceptually, but the current implementation should not run a full refresh for all 1,000 users concurrently on one worker.

### Cost profile

The expensive embedding operation is shared globally per job:

```text
One scraped job → one job embedding
```

It is not embedded once per user.

Profile embeddings are also generated only when a user uploads or changes a profile.

Therefore, normal scraping costs do not multiply by 1,000 users. The main workload is vector comparison and database writes.

### Workload at 1,000 users

A full refresh could involve approximately:

```text
1,000 users × 200 automatic matches = 200,000 match rows
```

Plus refreshing each user’s manually analyzed jobs.

That is manageable as a background workload, but it can overwhelm the database if all users are refreshed simultaneously, especially because the current ranking worker processes user refreshes in a relatively straightforward loop.

The main risks are:

- PostgreSQL/pgvector query load;
- 200,000 upserts during a refresh;
- Celery worker queue congestion;
- profile updates triggering many refresh jobs at once;
- incremental scrape ranking when a popular scrape affects many subscribers.

### Embedding and LLM costs

Embedding costs should remain controlled because:

- job embeddings are shared globally;
- changed descriptions are the only scrape jobs re-embedded;
- Analyze Match embeds a job only when necessary;
- batch ranking itself does not call the embedding provider;
- batch ranking does not call the LLM;
- Detailed AI Assessment is user-triggered and is the main LLM cost source.

### Recommendations before supporting 1,000 users

Use controlled background processing:

- Limit full-refresh concurrency, for example 2–5 ranking workers.
- Queue one refresh task per user and deduplicate already-pending refreshes.
- Process users in batches, such as 25–100 users per task.
- Add rate limits for profile-triggered refreshes.
- Keep nightly refresh distributed over a time window instead of starting all users at once.
- Use bulk database upserts instead of one insert per match where possible.
- Add indexes for `(user_id, origin, vector_score)` and the manual-match exclusion query.
- Add task retry/backoff and queue monitoring.
- Track ranking duration, queue depth, database CPU, and failed tasks.
- Keep the automatic limit configurable so 200 can be reduced if necessary.

### Practical capacity expectation

A reasonable rollout would be:

```text
10 users → validate correctness
100 users → measure refresh duration
500 users → tune worker/database concurrency
1,000 users → run a controlled load test
```

I would not promise 1,000-user production capacity purely from the current tests. The feature is architecturally suitable, but capacity depends on:

- PostgreSQL hardware;
- pgvector index/query performance;
- number of manual matches;
- number of simultaneous profile changes;
- scrape frequency;
- Celery worker count;
- embedding and LLM provider limits.

The safest operating model is to keep ranking asynchronous, rate-limited, and observable. The current separation—shared job embeddings, incremental scrape ranking, per-user full refresh, and optional LLM assessment—is a good cost foundation.















For 1,000 users, I would keep PostgreSQL + pgvector + Celery, but change the retention and ranking policies before growing the job corpus.

The priority changes:

1. Don’t silently delete useful match history

   Keep three tiers:

   - Automatic feed: top 200–500 active vector matches per user.
   - User-interest matches: manually analyzed, saved, applied, or shortlisted jobs retained indefinitely (or per account-retention policy).
   - Historical match snapshots: retain score/explanation history for 30–90 days instead of deleting it when a job falls out of the top set.

   Today, an automatic match falling below top 200 is deleted entirely, including its detailed AI assessment. That makes the product feel inconsistent and repeats work.

2. Make ranking incremental, not daily full re-ranking

   Your code already has an HNSW vector index and an incremental-ranking worker path—good foundations. At 1,000 users:

   - Rank a newly embedded job only for relevant active users.
   - Re-rank only one user when their resume/profile changes.
   - Keep a slower weekly reconciliation job, rather than relying on a full daily all-user re-rank.
   - Keep per-user/job idempotency locks and queue metrics.

   Ensure exactly one Celery Beat scheduler runs in production, otherwise periodic work can be duplicated. [Celery periodic-task documentation](https://docs.celeryq.dev/en/3.1/userguide/periodic-tasks.html)

3. Separate “feed ranking” from “detailed assessment”

   Treat the vector score as retrieval/ranking and the LLM score as an on-demand assessment:

   - Use vector score to find candidate jobs cheaply.
   - Run detailed AI only for a user action, saved jobs, or perhaps the top 20–50 candidates.
   - Never present them as interchangeable; show both with their purpose.
   - Do not let a one-off LLM score reorder the entire feed without a deliberate ranking policy.

4. Add a real job-retention policy

   Current behavior expires jobs after 30 days, which removes them from active lists but retains all rows forever.

   Recommended:

   - Active: 30–45 days, with a source refresh extending freshness only when the job is confirmed present.
   - Expired: keep 90–180 days for user history, applications, auditability, and deduplication.
   - Purge or anonymize afterward, except jobs tied to applications/manual saves where you retain the minimum necessary record.

   Do not introduce table partitioning immediately, but plan monthly partitions for raw jobs and events once volume justifies it; PostgreSQL partitioning makes time-based retention much cheaper operationally. [PostgreSQL partitioning documentation](https://www.postgresql.org/docs/current/ddl-partitioning.html)

5. Change `/jobs` into a discovery feed, not an unbounded list

   The UI currently stops loading at 500 jobs. Keep that cap, but make it intentional:

   - Default to “new since last visit” or last 7/14/30 days.
   - Provide strong filters and server-side search.
   - Show total count and explain that browsing is capped.
   - Add Saved, Applied, Archived, and Expired views for long-term user records.

6. Operate with explicit capacity metrics

   Before 1,000 users, add dashboards/alerts for:

   - jobs ingested and embedded per day;
   - embedding and LLM cost per user;
   - ranking latency, queue depth, retries, and failures;
   - HNSW index size and query latency;
   - number of active vs expired jobs;
   - automatic/manual match retention counts.

Your existing HNSW index is the right direction for vector retrieval; pgvector supports approximate nearest-neighbor indexing for this purpose. [pgvector documentation](https://github.com/pgvector/pgvector)

My practical recommendation: implement retention tiers and incremental ranking first. That resolves the confusing disappearing-match behavior and prevents daily work from growing linearly with both users and jobs.
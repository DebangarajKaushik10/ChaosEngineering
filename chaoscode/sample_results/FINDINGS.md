# Real-run findings: Hotel Reservation (2026-10-10)

Run on the real DeathStarBench Hotel Reservation code (10 Go services, built from source)
in the no-Docker sandbox: FerretDB for MongoDB, real memcached, Toxiproxy on every link,
a minimal Consul stand-in. Load: 20 req/s in the repo's traffic mix.
Baseline: 0% errors, p50 7 ms, p95 20 ms.

## Experiments

| # | Experiment | Kill switch | Fault active | Recovery | Impact |
|---|---|---|---|---|---|
| 1 | Kill `search` (kill-switch test, 90 s limit) | Fired (60% errors) | 9 s | 3 s | `/hotels` 0% → 100% errors |
| 2 | Stop `memcached-profile` (AI-chosen #1) | Fired (98% errors) | 9 s | 4 s | `/hotels` 93% and `/recommendations` 89% errors; `profile` crash-looped (5 restarts) |
| 3 | 300 ms delay on `profile` | Not needed | 30 s | 2 s | p95 18 → 316 ms on both main endpoints, no errors |
| 4 | Stop `memcached-reserve` | Fired (63% errors) | 11 s | 7 s | `/hotels` 88% and `/reservation` 100% errors; `reservation` crash-looped (6 restarts) |
| 5 | Pause (hang) `geo` | Fired (p95 5 s) | 11 s | 3 s | `/hotels` requests hung until the 5 s client timeout |
| 6 | CPU throttle `mongodb-profile` to 5% | Not needed | 30 s | 0 s | No impact: memcached serves profile data |
| 7 | Stop `consul` | Not needed | 30 s | 0 s | No impact on running services |
| 8 | Stop `consul`, then crash `search` | Manual | 30 s | Not recovered | `search` could not start; `/hotels` stayed down after Consul returned |

## Weaknesses found, ranked

1. **Consul restart causes a lasting outage (critical).** Dev-mode Consul keeps registrations in
   memory, and services register only once, at startup. After Consul restarts, only services that
   restart afterward are registered. `search` came back but found zero addresses for `geo` and `rate`,
   and `/hotels` stayed down until every service was restarted. While Consul is down, a crashed service
   cannot start at all (`FTL failed register`).
   Fix: persistent Consul with a data volume, periodic re-registration or TTL health checks, and
   start-up retry instead of a fatal exit.
2. **A cache failure crashes the service (critical).** `profile`, `reservation`, `rate` and `review` call
   `log.Panic` on any memcached error (verified: `services/profile/server.go:116`,
   `services/reservation/server.go:251`). Losing `memcached-profile` took down about 99% of traffic.
   Fix: treat cache errors like cache misses, read from MongoDB, and log a warning.
3. **No request timeouts (high).** A hung `geo` made `/hotels` callers wait indefinitely. Only the client's
   own 5 s timeout ended the requests. No service sets a gRPC deadline.
   Fix: `context.WithTimeout` on every outbound call, plus retries with backoff and a circuit breaker.
4. **Latency adds up with no protection (medium).** 300 ms on `profile` added about 300 ms to both main
   endpoints. Nothing degraded gracefully.
5. **Cache absorbs database slowness (positive).** Throttling `mongodb-profile` to 5% CPU had no
   visible effect, because the cache served the data.

## Upstream bugs found while setting up (not caused by chaos)

- User seeding writes `Cornell_%x` of the number's text (user 1 is `Cornell_31`). The repo's wrk2
  script logs in as `Cornell_1`, so all its logins and bookings fail. Our load generator uses the
  real names.
- The frontend dials `srv-review` and `srv-attractions` as plain hostnames instead of through Consul,
  so `/review`, `/restaurants`, `/museums` and `/cinema` always fail. `reviewHandler` then
  dereferences a nil response (`services/frontend/server.go:380`).

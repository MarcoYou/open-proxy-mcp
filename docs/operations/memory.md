# Memory operations contract

`GET /health` is a readiness and diagnostics response. `health_schema=2`, `observed_at`
and `instance` let a monitor reject absent, stale, or wrong-machine observations.
Required law data must load; a missing data field, load error, or drain lease returns
HTTP 503 with `status=degraded`. Normal service returns HTTP 200 and `status=ok`.
Do not interpret a failed observation as healthy service or confirmed outage.

All memory fields ending in `_mb` use MiB. `mem.rss_mb` is process RSS;
`vm_total_mb` and `vm_available_mb` describe the VM, and cgroup fields are provided
when available. Peak RSS uses the operating system's documented unit.

## Caches

Registered memory caches expose bytes, budgets, hits, misses, expirations and
capacity evictions. Expired entries are removed on lookup and on writes with a
bounded sweep frequency; pressure reclamation also removes all expired entries.
The financial results cache defaults to 16 MiB and 300 seconds, configurable with
`OPM_FINANCIAL_CACHE_MB`. It shares the registered cache clearing contract.
Cache payloads must not grow after insertion because byte accounting occurs at put.

The document cache defaults to 96 MiB. The 2 GiB Fly deployment explicitly sets
`OPM_DOC_CACHE_MB=144`, bringing registered cache budgets to 360 MiB per process
(proxy advice 128, document 144, KRX 32, screener 24, dividend 16, financial 16).
These budgets account for retained payloads, not total RSS or transient parsing
allocations. The disk budget and memory reclamation/restart thresholds are unchanged.

Treat this as a staged capacity trial. Observe at least 24 hours including an actual
workload peak before considering another increase. Compare cache hits, capacity
evictions, latency, RSS peaks and VM available memory; quiet uptime alone is not
evidence of capacity. Pause expansion on repeated RSS at or above 1300 MiB or
sustained VM available memory below 384 MiB. An OOM or automatic memory restart
requires rollback and investigation. Roll back by reverting the Fly override through
the normal CI deployment. Both machines receive the override; this is not an
isolated single-machine canary.

Disk accounting and sweeping both include `.json` and `.json.gz`, excluding partial
writes. Disk sweeping is byte-triggered rather than an immediate hard capacity
invariant. Observe volume free space separately from the configured cache budget.

`clients.entries == clients.max` is normal occupancy, not a capacity alarm.
`recent`, `busy`, `evictable` and `over_limit` describe activity and reclamation
eligibility; recent and busy sets overlap. No API keys are exposed. Clients serving
any tool request or document task are protected against idle eviction.

## Authenticated administrative requests

Use `x-admin-key` and explicitly target the intended instance. The response instance
must match. Never place credentials in URLs or logs.

- `POST /admin/cache?cache=0`: retain cached payloads, collect unused objects and
  return free allocator pages where supported.
- `POST /admin/cache?mode=expired`: remove only expired registered payloads, then
  collect and return pages.
- `POST /admin/cache?mode=pressure&target_mb=64`: expire first, then remove oldest
  payloads from the largest registered caches until the requested amount is reached.
  Targets must be between 0 and 256 MiB. A single entry can overshoot the target.
- Legacy `POST /admin/cache` clears registered memory caches. It is a manual fallback,
  not the recommended periodic policy. Static data and client connections are excluded.
- `disk=1` is an explicit legacy disk sweep, not a guarantee of an empty disk cache.

`removed_bytes` describes cache accounting; `freed_mb` reports observed process RSS
change in separate cache, GC and allocator steps. These quantities are not interchangeable.
GC cannot prove the absence of retained live references or native allocations.

`GET /admin/memtop` is observational and reports counts and registered cache sizes.
`?detail=1` opts into expensive reference estimates and object enumeration. Estimates
are not exclusive ownership and must not be summed as exact memory usage. Neither
GET triggers collection. Escalate to detailed sampling only during sustained growth.

## Draining and restarting

`POST /admin/drain?seconds=120` starts a lease (0..120 seconds); zero cancels it.
During a lease, health returns 503 and new MCP requests receive 503 with Retry-After.
Already admitted POST requests continue and are counted in `maintenance.active_posts`.
The lease expires automatically if its operator disappears. Old GET streams do not
count as active tool work. This is an operational safeguard, not a zero-error promise.

Before restart, verify a different instance is ready and has capacity, begin a short
lease, wait for active POST/tool/document work to reach zero, and recheck the peer.
Persist the restart intent and cooldown before issuing the command. A successful CLI
exit is insufficient: verify readiness, identity and reduced RSS on the same machine.
Abort on unknown observations and cancel the lease on any incomplete attempt.

Read-only diagnostics, issue state, automatic action state, notification emission and
notification delivery are separate outcomes. Monitor incident keys should use machine
identity plus issue code, never a changing human-readable message.

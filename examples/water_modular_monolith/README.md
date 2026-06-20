# `water` — Modular Monolith Reference

A complete rewrite of `app/fittbot_api/v2/Fymble/water/` following strict
modular-monolith rules. **Not wired into the running app** — read it
side-by-side with the original to see what changed and why.

---

## What you're looking at

```
water/
├── __init__.py        ← Public API surface — the ONLY legal entry point
├── api.py             ← Public Protocol + factory (the contract)
├── schemas.py         ← Public DTOs returned by api.py
├── routes.py          ← FastAPI adapter (depends on api.py only)
│
├── _http_schemas.py   ← HTTP request/response envelopes (private)
├── _service.py        ← Application service / orchestrator (private)
├── _domain.py         ← Pure domain logic — no I/O (private)
├── _repository.py     ← DB adapter (private)
├── _cache.py          ← Redis adapter (private)
└── _events.py         ← Events emitted by this module (PUBLIC types,
                         private bus impl)
```

**Convention:** `_filename.py` = private to the module. Anything outside
`water/` that imports from `water._service`, `water._repository`, etc. is
violating the boundary. An import-linter rule (see bottom) enforces this.

---

## The five rules this rewrite follows

### Rule 1 — Other modules see only `water.__init__`

The original `water/` exposed `WaterService`, `WaterRepository`, all the
schemas, all helpers — anything was importable. Here, `__init__.py`
re-exports exactly five names:

- `WaterAPI` — the Protocol type
- `build_water_api` — the factory other modules call
- `WaterStatus` — the public DTO
- `WaterIntakeAdded`, `WaterTargetSet` — events for cross-module coupling

Everything else lives behind `_` and is invisible to the rest of the app.

### Rule 2 — The public API is a Protocol, not a class

`api.py` defines `WaterAPI` as a `typing.Protocol` with five methods. The
concrete `_service.WaterService` *implements* the protocol but isn't
*the* protocol — meaning we can swap in a fake for tests, a remote stub
if we ever extract this into its own service, or a no-op for migrations.
Other modules type-hint against `WaterAPI`, never against `WaterService`.

### Rule 3 — Three separate adapters, one orchestrator

The original `service.py` mixed business logic, DB calls, Redis calls,
and HTTP shape. Here those are split:

| Layer            | File              | Knows about          |
|------------------|-------------------|----------------------|
| Pure domain      | `_domain.py`      | Nothing — no I/O     |
| DB adapter       | `_repository.py`  | SQLAlchemy + tables  |
| Cache adapter    | `_cache.py`       | Redis only           |
| HTTP envelope    | `_http_schemas.py`| Pydantic             |
| Orchestrator     | `_service.py`     | All four above       |
| HTTP adapter     | `routes.py`       | FastAPI + the API    |

`_domain.py` functions are unit-testable with no fixtures. `_repository`
and `_cache` are mockable. `_service` is the only place that knows the
*sequence* of operations.

### Rule 4 — Cross-module side effects happen via events

The original `service.py` reached across module boundaries in two ways:

1. `_award_xp` directly read `ClientTarget` and wrote `CalorieEvent`.
   Those tables belong to a *rewards/calorie* concern, not to *water*.
2. `_invalidate_caches` deleted Redis keys like
   `client{id}:initial_target_actual` — those are *home* / *diet*
   keys; water shouldn't know they exist.

This rewrite emits two events:

- `WaterIntakeAdded(client_id, total_litres, target_litres)`
- `WaterTargetSet(client_id, target_litres)`

A future XP module subscribes to `WaterIntakeAdded` and awards XP. The
home module subscribes and invalidates its own cache. Water doesn't know
who's listening — and crucially, water no longer breaks when those
modules change their internals.

### Rule 5 — Data ownership is documented, not just in code

`ClientTarget`, `ClientActual`, and `Reminder` are physically shared
SQLAlchemy tables in your legacy schema, so we can't move them. Instead,
the README declares **column-level ownership** and the repository only
ever touches owned columns. A future migration can split these into
`water_targets`, `water_intakes`, `water_reminders` tables — at which
point `_repository.py` is the only file that changes.

**Owned by `water`:**
- `ClientTarget.water_intake` (writes only)
- `ClientActual.water_intake`, `ClientActual.last_water_time` (writes only)
- `Reminder` rows where `reminder_mode = 'water'` (writes only)

Other modules may *read* these columns but must not write them.

---

## What changed vs. the original

| Concern                          | Original                                            | Modular                                                |
|----------------------------------|-----------------------------------------------------|--------------------------------------------------------|
| Public surface                   | Anything in the folder                              | 5 names from `__init__.py`                             |
| HTTP layer touches business code | `WaterService(db, redis)` directly                  | `build_water_api(...)` factory only                    |
| Service mixes I/O + logic        | Yes — `_award_xp`, `_compute_next_water_time`, DB   | Pure logic in `_domain.py`, I/O in `_repository`/`_cache` |
| Repository owns its session      | Shares caller's session                             | Same — but only this file imports SQLAlchemy           |
| Cache invalidation               | Hardcoded keys for `home`, `diet` modules           | Emits `WaterIntakeAdded` event                         |
| XP/calorie cross-table writes    | Inlined in `_award_xp`                              | Removed — XP module subscribes to event                |
| Time helpers (compute_next, etc.)| Static methods on `WaterService`                    | Module-level functions in `_domain.py`                 |
| Tests                            | Need DB + Redis fixtures for everything             | `_domain` tests need nothing; `_service` tests mock 3 ports |

---

## Enforcing the boundaries

Add this to `.importlinter` at the project root once you adopt the
pattern across modules:

```ini
[importlinter]
root_packages = app

[importlinter:contract:water-internals]
name = Nothing outside water/ may import water internals
type = forbidden
source_modules =
    app.fittbot_api.v2.Fymble
forbidden_modules =
    app.fittbot_api.v2.Fymble.water._service
    app.fittbot_api.v2.Fymble.water._repository
    app.fittbot_api.v2.Fymble.water._cache
    app.fittbot_api.v2.Fymble.water._domain
    app.fittbot_api.v2.Fymble.water._http_schemas
    app.fittbot_api.v2.Fymble.water._events
ignore_imports =
    app.fittbot_api.v2.Fymble.water.* -> app.fittbot_api.v2.Fymble.water._*
```

Run `lint-imports` in CI. Any boundary leak fails the build.

---

## Reading order

If you want to internalise the pattern in 15 minutes, read the files in
this order — outside-in:

1. `__init__.py` — what the world sees
2. `api.py` — the contract
3. `schemas.py` — the public DTOs
4. `routes.py` — how HTTP plugs in
5. `_service.py` — the orchestration sequence
6. `_domain.py` — pure logic (compare with original `service.py` lines 232-289)
7. `_repository.py` — boring CRUD
8. `_cache.py` — boring Redis
9. `_events.py` — the seam for cross-module work

Each file is short on purpose. Total LOC is roughly the same as the
original; the gain is that **changes stay local** — adding a new water
endpoint touches `routes.py` + `_service.py` and nothing else.

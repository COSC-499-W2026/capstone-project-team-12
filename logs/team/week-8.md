# Week 8 Team Log (Oct. 26 - Nov. 1)

Author: Fabiha Afifa

This week, we focused on extending the game's core functionality beyond class management and session status. The main goals were to finish pause and resume support, add automatic session expiration, implement points and achievements, and move forward with quitting, resetting, shuffling, content imports, and GPS navigation. We also continued improving authentication tests and checking concurrency behavior so that overlapping requests do not corrupt session or content data.

The work was distributed so that:

- **Mia** worked on game session control (A8: #257, #259; A9: #277, #278, #279, #280, #281, #282, #283, #284). A8 added the pause endpoint and the scheduled expiration job, completing the pause/resume workflow. A9 added quitting, resetting and shuffling games. A reset starts a new game with the same class, language and difficulty, with a limit of three resets per local day. A shuffle replaces unfinished locations without changing completed locations or earned points, with at most two shuffles per game. She also began GPS navigation and arrival confirmation (A19: #305–#310).

- **Fabiha** worked on points, levels and achievements (A12, #86: #225, #228, #229, #230, #298, #299, #300). The work added a points ledger, per-language statistics, idempotent location scoring, levels 1–5, and achievements for completing 1, 5 and 20 locations. The student endpoints expose points, levels and achievements, while the gameplay integration provides a shared function for finalizing a location's score. The first four PRs merged on Nov. 1; the remaining three were still open. She also reviewed authentication, game-session and navigation PRs, including the session-expiration job and the reset and shuffle workflows.

- **Eddy** worked on content management (A20, #60: #287–#296, #301, #303, #304). This included the location CSV import service, import limits, clue and feedback audio ZIP imports, location and clue-text routes, clue audio upload and coverage routes, and request-body limits. The import work validates individual files and reports accepted and rejected entries. The later changes address concurrent imports, including deadlocks when two imports process the same content in different orders.

- **Kaedyn** finished the login test work (A2: #266), covering successful login, access and refresh tokens, token rotation, revoked and expired tokens, invalid credentials and disabled accounts. The PR merged into main after review corrections. These tests strengthen the authentication functionality introduced in the previous weeks.

Based on the PR statuses reviewed, we made substantial progress, although several features remained in stacked PRs rather than being available on main:

- **Accounts:** login tests merged (#266), with stronger coverage of authentication and refresh-token behavior.
- **Game sessions:** pause/resume and automatic expiration completed through A8. The expiration job runs every five minutes and ends idle games in batches. A9's quit, reset and shuffle changes were implemented in open PRs but had not all merged.
- **Engagement:** the A12 database models, scoring service and achievement-unlocking logic merged. Student-facing engagement and achievement endpoints, and the gameplay scoring integration, remained open.
- **Content:** location management, CSV imports, audio ZIP imports, upload routes and coverage reporting progressed through A20. Much of this work remained under review, including the request-body middleware.
- **Navigation:** A19's navigation rules, current-location endpoint, GPS ping endpoint, arrival confirmation and race-condition tests were implemented in stacked PRs but had not yet merged.

The team collectively worked on the following to avoid duplicated logic, inconsistent state and unnecessary dependencies:

- **One session-expiration rule (#257, #259):** the pause endpoint and the scheduled expiration job use the existing session lifecycle logic. `expire_idle_sessions` selects idle games and ends them through `abandon_sessions`, so unfinished locations are skipped and completed locations retain their points. A session can be expired by an ordinary game request or by the scheduled job without introducing a separate definition of expiration.

- **Safe concurrent expiration (#259):** the expiration job selects open sessions in ID order using `FOR UPDATE SKIP LOCKED`. If another request is already modifying a game, the job skips it rather than waiting for its lock. The CLI processes batches of 500, commits each batch, and stops when a batch is smaller than the limit. This reduces contention with pause, resume and other game operations.

- **Shared game creation and local-day calculations (#278, #279):** `save_new_game` extracts the final game-creation step so that starting and resetting a game use the same logic. `start_of_today` calculates local midnight for daily rules, including reset limits and carry-over behavior. Tests cover local midnight and daylight-saving transitions.

- **Consistent locking for resets (#279):** resetting a game locks the student before locking the game, following the same order as starting a game. This prevents two concurrent resets from independently passing the daily limit and avoids inconsistent lock ordering between game operations.

- **Preserving game history during shuffles (#281, #283):** shuffled-out session-location rows are retained, including their previous photo attempts. A partial unique index allows replacement locations to occupy the same positions while the historical rows remain stored. The shuffle excludes locations already used in that game, and completed locations and points are not changed.

- **Stale-request protection for shuffles (#283, #284):** the shuffle request includes `shuffles_used` as a precondition. If the value no longer matches the game, the request returns `409 STATE_CHANGED` without making changes. This prevents a delayed or repeated request from accidentally consuming another shuffle.

- **Idempotent scoring (#225, #228):** A12 records awarded points through `PointsLedger`, preventing a completed location from receiving points more than once. Completed locations receive 100 points, while failed or skipped locations receive zero. `UserLanguageStats` maintains separate cumulative totals for each language, so progress in one language does not change another language's level.

- **Shared scoring and achievement progression (#228, #230, #300):** the scoring service updates language-specific totals and levels, while the achievement logic checks completion thresholds. The gameplay integration introduces `score_finalized_location` so that the game component can finalize a location's score through the engagement interface rather than duplicating the points rules.

- **Pure navigation rules (#305):** the distance and trend calculations are separated from database operations. The navigation logic uses distance bands, a 25-second trend reference, a 10-meter dead band and a 60-second reset window. Keeping these calculations independent of database state allows the team to test GPS behavior without waiting for real time to pass.

- **Navigation state and concurrency (#307–#310):** the current-location and navigation endpoints operate on the game's current session location. GPS pings are rate-limited, arrival confirmation checks recent position data or an allowed GPS override, and repeated confirmations are designed to be idempotent. Real-Postgres race tests cover overlapping pings, confirmations, shuffles, quits and deactivated-location transitions.

- **Shared content validation (#287–#291):** location CSV imports reuse the location validation rules, while audio ZIP imports reuse the existing audio checks and recording services. The imports report rejected rows or files without rejecting every valid entry in the same upload. CSV dry runs use a savepoint so the preview can perform validation without retaining the changes.

- **Content import concurrency (#301, #304):** two imports can target the same clues or feedback phrases. The import changes process shared content in a consistent lock order and use an upsert for feedback phrases that may be created concurrently. Separate PostgreSQL connections and transactions are used in the concurrency tests, rather than relying only on mocked database behavior.

- **Request-body protection (#296):** the proposed ASGI middleware limits request bodies before the application reads an unbounded amount of data. It distinguishes ordinary request bodies from file uploads and handles requests with and without `Content-Length`. Review identified the need for tighter per-route limits so that a single small-audio upload cannot consume the full ZIP-import allowance.

These changes support the team's architectural goal of keeping business rules within their owning components. Game session transitions remain in game, points and achievements remain in engagement, authentication remains in accounts, and content validation remains in content. API routes coordinate these services rather than implementing separate versions of their rules.

We considered alternative implementations and identified several tradeoffs:

- **Expire every idle session through a single scheduled job.** We kept expiration in normal game operations as well, so an expired session can be recognized immediately when it is accessed rather than waiting for the next five-minute job.

- **Delete shuffled-out session locations.** We kept the old rows instead, preserving historical attempts and allowing replacement rows to reuse positions through a partial unique index.

- **Allow resets and shuffles without request limits.** We introduced a daily reset limit and a per-game shuffle limit to prevent repeatedly drawing new locations until a preferred set appears. Shuffle requests also require the client's expected shuffle count to prevent stale updates.

- **Calculate points directly in game.** We placed scoring in engagement and exposed an integration function, so the game component does not need to reproduce the ledger, level or achievement rules.

- **Import ZIP entries in their original order.** Concurrent imports containing the same content in opposite orders can deadlock. Processing shared records in a consistent order avoids this, while the import report can still preserve the original ZIP order.

- **Rely only on the per-file upload limit.** This would not protect the server from a very large multipart request, because the framework processes the request body before route-level validation. The proposed request-body middleware addresses that earlier stage, with per-route limits still needing review.

- **Test concurrency with ordinary database fixtures.** Uncommitted fixture data is not visible to a second database connection. The team added real-Postgres tests using separate committed transactions to verify locking, duplicate creation and race-condition behavior.

The main unfinished work at the end of the reporting period was merging the A9 and A19 stacks, completing the A20 content-management review and request-body protections, and connecting A12 scoring to the live gameplay completion path. The A12 scoring integration had been implemented and tested, but the actual A10 photo-verification caller still needed to be connected.

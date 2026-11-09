# Week 8 Individual Log

## Mia Kuang

Week 8 (Oct 26 – Nov 1)

**This week** I finished A8 (pause, resume and the expire job, issue #43) and A9 (quit, reset and shuffle, issue #44), and started A19 (GPS guidance, issue #45). A8 was the four PRs that were still open at the end of week 7 (#255, #257, #258, #259). Eddy and Fabiha reviewed all four, and they were merged on Oct 29 and Oct 30. A9 is eight stacked PRs (#277 to #284) and was merged on Nov 1. I split it that way because quit, reset and shuffle together were far over the 500-line limit, and two of the PRs (#278, #282) only move shared code so the later ones stay small. A19 is six stacked PRs (#305 to #310). They are open and waiting for review, so they only have a row in the table.

| PR | What | Files | Lines | Tests | Status |
|---|---|---|---|---|---|
| #255 | Pause and resume service | 5 | +477 / -21 | 11 | Merged Oct 29 |
| #257 | `POST /games/{id}/pause` | 4 | +295 / -11 | 9 | Merged Oct 29 |
| #258 | `POST /games/{id}/resume` | 3 | +88 / -4 | 3 | Merged Oct 30 |
| #259 | Expire-sessions job | 5 | +341 / -6 | 11 | Merged Oct 30 |
| #277 | Quit a game | 8 | +348 / -30 | 8 | Merged Nov 1 |
| #278 | Share saving a new game and the start of the local day | 4 | +56 / -8 | 1 | Merged Nov 1 |
| #279 | Reset a game, at most 3 times a day | 4 | +484 / -7 | 12 | Merged Nov 1 |
| #280 | `POST /games/{id}/reset` | 3 | +166 / -14 | 4 | Merged Nov 1 |
| #281 | Keep the locations a shuffle replaces | 6 | +143 / -3 | 3 | Merged Nov 1 |
| #282 | Share the game lookup and the draw that a shuffle needs | 6 | +144 / -30 | 1 | Merged Nov 1 |
| #283 | Shuffle a game's unfinished locations | 2 | +473 / -0 | 14 | Merged Nov 1 |
| #284 | `POST /games/{id}/shuffle` | 3 | +268 / -2 | 8 | Merged Nov 1 |
| #305 | Stage 1 navigation rules | 4 | +497 / -0 | 13 | Open, waiting for review |
| #306 | Weak GPS, the speed flag and the override time | 3 | +291 / -12 | 15 | Open, waiting for review |
| #307 | `GET /games/{id}/current-location` | 5 | +497 / -2 | 8 | Open, waiting for review |
| #308 | `POST /games/{id}/navigation/ping` | 9 | +485 / -4 | 9 | Open, waiting for review |
| #309 | `POST /games/{id}/navigation/confirm-arrival` | 4 | +485 / -1 | 10 | Open, waiting for review |
| #310 | Navigation race tests | 4 | +391 / -23 | 10 | Open, waiting for review |

Lines are GitHub's numbers. Tests are new test functions; a parametrized test counts once. `openapi.json` and the dependency graph are no longer committed (#271), so none of these sizes include them.

**One thing went wrong:** `_play`, the helper that runs a game action and commits, committed whenever the action raised an `AppError`. That was fine for pause and resume, because they only raise before they write, or right after the idle expiry that has to be kept. Eddy pointed out while reviewing #277 that reset is the first action that writes in several steps. If it ever raised after abandoning the old game, `_play` would commit the abandon: the student would have no game and a reset used up. It couldn't happen with the code at that point, only because the draw and the clue link come before the first write, but nothing enforced it, only a comment. I fixed it in #280: `_play` now commits only for the idle expiry and rolls back on any other error, and a test shows that a reset failing after the abandon keeps the old game and doesn't use up a reset. Lesson: if a shared helper depends on a rule, make the code enforce it. A comment that says "actions must only raise before a write" is not a guarantee for the next action someone adds.

**A second thing went wrong:** the A8 branches had merge commits and the wrong time zone on some commit times, and a GitHub stack needs linear history. I rebuilt #255 to #259 so each branch is main plus its own commits, and checked that every commit passes the static checks and that each branch tip passes the tests. The A9 stack then needed a rebase after almost every squash merge, as in week 7. I did it with `git rebase --onto` each time, without merge commits.

## PR #255: Pause and resume a game (A8 part 1)

**Requirement:** issue #43 and the spec's Session Lifecycle and API table. A student needs to pause a game and resume it where they left it. This covers Test Cases 1-3 at the service level. The routes come in #257 and #258.

**What I did:**

- `pause_game(student_id, game_id, db)` in `app/game/control.py` pauses an active game: it sets `paused_at` and `last_activity_at` to now and returns the game status plus `expires_at` (now plus the expiry limit). Pausing a paused game changes nothing (Game, frontend)
- `resume_game(student_id, game_id, db)` resumes a paused game: it clears `paused_at`, sets `last_activity_at` to now and adds 1 to `resume_count`. Resuming an active game changes nothing (Game)
- Both lock the game row first (`FOR UPDATE`, read again), and the query filters by student, so another student's game or an unknown id is 404 `NOT_FOUND` and that game isn't locked or touched. A game idle for the limit (exactly 24 hours counts, the same `<=` as `is_idle`) is expired first and gives 409 `SESSION_NOT_ACTIVE`, and so does a completed or abandoned game, with `[{"session_id", "state"}]` in the details. Nothing commits (Game)
- `expire_if_idle` and `status_of` in `app/game/status.py` are public now, so pause and resume reuse them, with no behaviour change (Game)
- A shared `freeze_clock(now, *modules)` fixture in `tests/backend/conftest.py` replaces the copy in `test_game_interface.py` (developers)

**Data modeling:** the decisions are written in #43 and the spec: pause and resume count as game actions (the 24 hours start again on pause), repeating either one changes nothing, and a game idle for 24 hours can't be paused or resumed but is expired instead.

**Review:** The first draft looked the game up by id with `FOR UPDATE` and checked the owner afterwards, so a request for another student's game locked that student's row. The query now filters by student too. The frozen-clock helper was copied into each test file, and it's one fixture now. Eddy checked the lock query, the idempotence, the `<=` check and the `status.py` rename, and asked for three small things: say in the docstrings that a caller who wants the idle expiry kept has to commit before re-raising the 409 (the service doesn't commit, and `get_db` never does), say in `freeze_clock` that `frozen` must be timezone-aware, and that only the modules passed in are frozen. I did all three, and added a two-connection test that `pause_game` holds the row lock until the transaction ends and that the 404 for another student doesn't lock their row. Fabiha and Eddy approved.

**Tests** (`tests/backend/test_game_control.py`): 11 new tests. Pause and resume keep the stage and attempts, each resume adds one to `resume_count`, pausing starts the idle time again, pausing again and resuming an active game change nothing, a finished game is not active, another student's game or an unknown id is not found, a game idle for the limit is expired instead, a game ended elsewhere is not resumed, pause locks the caller's row until the transaction ends, and pause and resume don't commit. Integration tests (real Postgres).

## PR #257: POST /games/{id}/pause (A8 part 2)

**Requirement:** the `POST /games/{id}/pause` / resume row in the spec's API table, which now says what a repeated pause, a finished game and an idle game return (agreed in #43).

**What I did:**

- `POST /games/{game_id}/pause` (`pauseGame`) in `app/api/v1/games.py` pauses the student's game with `pause_game` and returns `PausedGameResponse`: the same body as `GET /games/{id}` plus `expires_at` (frontend)
- Pausing a paused game is 200 and changes nothing. 409 `SESSION_NOT_ACTIVE` for a completed or abandoned game or one idle for 24 hours, which is expired; 404 for another student's game; 403 `FORBIDDEN_ROLE` for an admin or `ACCOUNT_DISABLED`; 401 without a token; 422 for an id that isn't a UUID (frontend)
- `_play` runs the action and commits, also when it is refused, so the expiry done before the 409 is kept (Game)
- A shared `assert_error(response, status, code)` fixture in `conftest.py` replaces the copy in `test_game_status_api.py` (developers)

**Review:** The first version tested pause and resume in one test, so this PR couldn't have pause tests without the resume route. They are separate tests now. `assert_error` was copied into each API test file, and it's one fixture now. Eddy found that the docstring (which becomes the OpenAPI description) said `expires_at` is "now plus the expiry limit", but a repeat pause changes nothing, so it returns the original pause time plus 24 hours. I reworded it and `test_pausing_again_is_200` now checks that `paused_at`, `expires_at` and `last_activity_at` don't change. He also asked for a contract comment on `_play` (actions must raise `AppError` only before a write, or after the expiry), which I added. That comment turned out not to be enough (see "One thing went wrong"). Fabiha and Eddy approved.

**Tests** (`tests/backend/test_game_control_api.py`): 9 new tests. Pause, pausing again (200), an idle game (409 with the expiry committed), a finished game (409), another student's game or an unknown id (404), an id that isn't a UUID (422), an admin (403), a disabled account (403), and no token (401).

## PR #258: POST /games/{id}/resume (A8 part 3)

**Requirement:** #43's Test Cases 1-3 over HTTP: a paused game resumes where it was left, a game that isn't open can't be resumed, and a game idle for 24 hours is expired instead of resumed.

**What I did:**

- `POST /games/{game_id}/resume` (`resumeGame`) resumes the student's paused game with `resume_game` and returns the same body as `GET /games/{id}`. Resuming an active game is 200 and changes nothing. The errors are the same as for pause (frontend)
- The API tests from #257 now run for both routes, plus two resume tests (developers)

**Review:** Nothing to fix in the first version, because the route reuses `_play`. Fabiha asked for an assertion in `test_resuming_an_active_game_is_200` that `resume_count` stays the same. It now also checks that `last_activity_at` is unchanged and that the body equals `GET /games/{id}`, and `test_resume` checks that `last_activity_at` is refreshed. Eddy's nitpick about `last_activity_at` led to the same change. I added an OpenAPI test that `pauseGame` and `resumeGame` declare a 409; it builds the spec in memory, so it doesn't need a generated file. Fabiha and Eddy approved.

**Tests:** 3 new tests (`test_resume`, `test_resuming_an_active_game_is_200`, and `test_pause_and_resume_declare_a_conflict` in `tests/utils/test_export_openapi.py`).

## PR #259: The expire-sessions job (A8 part 4)

**Requirement:** #43 (the expire-sessions job) and the spec's Session Lifecycle and Scheduled Jobs. This PR closes #43.

**What I did:**

- `expire_idle_sessions(db, *, batch_size)` in `app/game/sessions.py`, exposed through `app/game/interface.py`, expires up to `batch_size` open games idle for the limit and returns how many ended. It picks them with `ORDER BY id ... FOR UPDATE SKIP LOCKED`, so a game another request is changing is left for the next run instead of waited on, and two runs can't deadlock. It ends them with `abandon_sessions(EXPIRED, ...)`, so unfinished locations are skipped (`session_ended`) and points stay. It doesn't commit (Game)
- `python -m app.cli expire-sessions` runs it in batches of 500 (`EXPIRE_BATCH_SIZE`), committing each batch until a batch comes back short, and only logs when it expired something. The crontab runs it every 5 minutes (the line was there, commented out) (developers)
- The two "only log when something happened" comments in `cli.py` are on their own lines now (developers)

**Data modeling:** skipping locked games comes from the review of #194, and the spec's Abandonment section now says it works this way. An idle game is now expired by whichever comes first: a read, a start, a pause or resume, or this job.

**Review:** The tests counted every idle game in the database, so they would fail on a dev database with old games in it. A fixture now expires those inside the test's transaction first. Nothing proved that a locked game is skipped, so `test_a_game_another_request_has_locked_is_skipped` commits a real game, locks it from a second connection and fails on the lock timeout without `skip_locked=True`. A test puts an already-ended game first in id order with a batch size of 1. Fabiha asked for a check that `batch_size` is positive in `expire_idle_sessions`, and I added it with `test_a_non_positive_batch_is_refused`. Eddy approved with nothing to change.

No workaround. A note for A10 (#62) is in the PR: the job locks the game row and then updates its session locations, so if the photo upload ever locks a session location and then its game, they can deadlock. It has to lock the game first too.

**Tests** (`tests/backend/test_expire_sessions.py`): 11 new tests. Idle active and paused games expire, only games idle for the limit expire, finished games don't take up the batch, a batch takes the lowest ids, a locked game is skipped (two real connections), one call expires at most one batch, a non-positive batch is refused, the command runs until every idle game is expired, the command logs nothing when no game is idle, and the crontab runs the job every five minutes and every job in it is a command.

## PR #277: Quit a game (A9 part 1)

**Requirement:** issue #44 and the spec's Session Lifecycle (Abandonment). A student can quit an active or paused game without resuming it.

**What I did:**

- `quit_game(student_id, game_id, db)` in `app/game/control.py` ends the game as abandoned with end reason `quit`. It uses the same locked lookup as pause and resume, so another student's game is 404, a finished game is 409 `SESSION_NOT_ACTIVE`, and an idle game is expired first. Points already earned stay and the unfinished locations are skipped (`session_ended`) (Game)
- `abandon_sessions` takes an optional `last_activity_at`. Quit passes the quit time, because the carry-over draw (A11) uses it to tell whether the last game was quit today. Class closing and expiry keep the old value (Game)
- `POST /games/{id}/quit` (`quitGame`) returns the game status for now. Once `GET /games/{id}/summary` exists (A23, #47) it returns the summary instead (frontend)

**Data modeling:** paused games can be quit without resuming (decided Oct 27, written in #44 and the spec).

**Review:** The first version reloaded the game with `db.refresh` after the UPDATE. Removing it didn't fail any test, because the ORM UPDATE already changes the loaded (locked) game, so it's gone, with a comment saying why. The docstring of `abandon_sessions` said callers never pass something the student did, which is no longer true. Eddy found the `_play` problem here (see "One thing went wrong"). Fabiha approved.

**Tests** (`test_quit_game.py` and others): 8 new tests. Quit keeps the points, a paused game is quit without resuming, quitting twice is not active, an idle game is expired instead, a game ended elsewhere is not quit, the next game today starts with the unplayed locations, `last_activity_at` changes only for an end the student asked for, and one route test. The lookup and HTTP error tests for pause and resume now run for quit too.

## PR #278: Share saving a new game and the start of the local day (A9 part 2)

**Requirement:** none new. A refactor with no behaviour change, split out so the reset PR stays under 500 lines.

**What I did:**

- `start_game`'s last step is now `save_new_game(...)` in `app/game/start.py`, so a reset can save its new game the same way after ending the old one, instead of copying `_create_game` and `_session_locations` (Game)
- The local midnight that the carry-over used is now `start_of_today()` in `app/game/sessions.py`. `draw.py` uses it, and the daily reset limit does too. Keeping it in one place means the reset limit and the carry-over can't disagree about what "today" is (Game)

**Review:** Nothing to fix in this one. Eddy compared it with #277 and checked that `save_new_game` runs the same sequence `start_game` ran before and still doesn't commit, and that `draw.py` importing from `sessions.py` adds no import cycle. He had three small suggestions, which he left to me.

**Tests** (`tests/backend/test_game_sessions.py`): 1 new test, `test_today_starts_at_local_midnight`, at 00:30, at 23:30 (already the next day in UTC) and on the night daylight saving ends.

## PR #279: Reset a game, at most 3 times a day (A9 part 3)

**Requirement:** #44 and the spec's Session Lifecycle (Reset). The limit stops students from resetting until they get the nearest locations.

**What I did:**

- `reset_game(student_id, game_id, db)` in `app/game/control.py` ends the game (abandoned, end reason `reset`, `last_activity_at` now) and starts a new one with the same class (or Practice), language and difficulty. A paused game can be reset too (Game)
- It locks the student first (`lock_user`), then the game: the same order as starting a game, so a reset can't deadlock with a start or a class switch, and two resets can't both pass the limit (Game)
- At most `max_resets_per_day` (3, a new setting) per local day, otherwise 409 `RESET_LIMIT_REACHED`. It counts the student's games ended by reset whose `last_activity_at` is today, in any mode, so there is no new column (Game)
- The new draw has no carry-over. The draw and the first clue's link come before anything is written, so `NOT_ENOUGH_CONTENT` or a missing recording leaves the current game as it is (Game)

**Data modeling:** the spec said 3 resets per local day but not where to keep the count. Counting games by `last_activity_at` avoids a new column and matches the carry-over's idea of "today". I wrote it in #44.

**Review:** The midnight test built its times with `start_of_today()` itself, so it still passed when I broke the function to use UTC. It now uses fixed Vancouver times (a reset at 23:30 the evening before must not count). The clue link test looked for the audio asset id in the URL, but the link uses the storage key, so it now compares with the URL the audio service makes. Fabiha asked what happens if saving the new game fails after the old one is abandoned. That is covered in #280 with a test. Eddy asked whether a reset can redraw the same locations. That is intended: the spec says a reset starts with a new draw and doesn't exclude the old game's locations, and excluding them could give `NOT_ENOUGH_CONTENT` when there is little content. He also pointed out that leaving a class takes the student's lock first, so it waits for a reset and then ends the new game too. Archive and remove aren't built yet (A17, #90) and will need the same lock. Eddy approved.

**Tests** (`tests/backend/test_reset_game.py`): 12 new tests. A new game with the same settings, a paused game reset without resuming, no locations brought back from a quit game, the fourth reset today refused, three allowed, only today's resets by this student count, the day turns over at local midnight and not UTC, not enough content and a missing recording leave the game as it is, an idle game is expired instead of reset, reset doesn't commit, and reset holds the student's lock until the transaction ends (two real connections).

## PR #280: POST /games/{id}/reset (A9 part 4)

**Requirement:** #44, the API table row for reset.

**What I did:**

- `POST /games/{id}/reset` (`resetGame`) returns 201 with the new game, the same body as `POST /games`. Errors: 409 `RESET_LIMIT_REACHED`, `NOT_ENOUGH_CONTENT` and `SESSION_NOT_ACTIVE`, 404 for another student's game, 403 for an admin or a disabled account (frontend)
- `_play` commits only when the action raised the idle expiry, and rolls back on any other error (Game)

**Review:** The test expected one commit, but the new game's savepoint fires a commit event too (as in the start game test). It now expects two and says why. Eddy's `_play` point from #277 is fixed here. Fabiha asked for an HTTP-level test that too little content gives 409 `NOT_ENOUGH_CONTENT` and leaves the original game active, which I added.

**Tests** (`tests/backend/test_game_control_api.py`): 4 new tests. A reset over HTTP, the fourth reset today is 409, a reset that can't save the new game is not counted, and not enough content returns 409. The HTTP error tests for pause, resume and quit now run for reset too.

## PR #281: Keep the locations a shuffle replaces (A9 part 5)

**Requirement:** #44 and the spec (Session Lifecycle, Shuffle, and the `session_locations` table), decided Oct 23. A shuffle has to keep the rows it replaces, with their photo attempts, and the new row takes the same position.

**What I did:**

- New skip reason `shuffled` (Game)
- `UNIQUE (session_id, position)` becomes a partial unique index over the rows that weren't shuffled, with the same name `uq_session_locations_session_id_position`. The migration drops the constraint, creates the index and adds `shuffled` to the `skip_reason` check (developers)
- The game status leaves shuffled rows out (frontend)

**Data modeling:** the ER diagram already showed the partial key, so the code follows the diagram and the spec. The downgrade fails on purpose once any game has been shuffled, because those rows break the old unique key and the old check, and deleting them would lose their photo attempts. I wrote this in the PR.

**Review:** `alembic check` doesn't compare an index's `WHERE`, so a model and migration that disagree would still pass CI. A test now reads the index from `pg_indexes` and checks the model's condition too. The shuffle migration also had to move after the achievements migration that landed on main. Eddy found nothing to change here and noted that his two follow-up points are handled in #283.

**Tests:** 3 new tests (`test_game_schema.py`: a shuffle can replace the row at a position, and the position index is partial in the model and the database; `test_game_status.py`: rows replaced by a shuffle are not shown). Integration tests (real Postgres).

## PR #282: Share the game lookup and the draw that a shuffle needs (A9 part 6)

**Requirement:** none new. A refactor with no behaviour change, split out so the shuffle PR stays under 500 lines.

**What I did:**

- `lock_open_game` and `session_not_active` in `control.py` are public, so shuffle uses the same lookup as the other game actions (Game)
- `new_location_row(..., current=, now=)` in `start.py` builds one session location with its content snapshot; a new game and a shuffle both use it (Game)
- `UNFINISHED` in `sessions.py` is public (what ending a game skips and what a shuffle replaces) (Game)
- `draw_locations`' random fill moves to `_pick`, which the new `draw_replacements` also uses: eligible locations not already in the game, ones the student hasn't completed first, no carry-over (Game)

**Review:** `new_location_row` first decided the status from whether `started_at` was set. It now takes `current` explicitly. Eddy found the refactor behaviour-preserving (`_pick` returns the same locations in the same order as the old loop). He noted that `draw_replacements` trusts the caller to pass every location already in the game, shuffled rows included. #283 does that and has a test. He also made `_pick`'s arguments keyword-only a small cleanup, which I did.

**Tests** (`tests/backend/test_draw_locations.py`): 1 new test, that replacements skip the excluded locations and prefer new ones. The existing start and control tests still pass.

## PR #283: Shuffle a game's unfinished locations (A9 part 7)

**Requirement:** #44 and the spec's Session Lifecycle (Shuffle and Retry safety).

**What I did:**

- `shuffle_game(student_id, game_id, shuffles_used, db)` in the new `app/game/shuffle.py` replaces every unfinished location (the current one and the ones still to come). Finished locations and points don't change (Game)
- `shuffles_used` is the precondition: if it doesn't match, 409 `STATE_CHANGED` and nothing changes. It is checked before the limit, so a retry of a shuffle that already went through gets `STATE_CHANGED`, not `SHUFFLE_LIMIT_REACHED`. At most 2 per game. A paused game is 409 `SESSION_NOT_ACTIVE` and the shuffle isn't used up (Game)
- The new locations exclude every location already in the game, including ones an earlier shuffle replaced, because of `UNIQUE (session_id, location_id)`. Not enough left is 409 `NOT_ENOUGH_CONTENT`. The draw and the new clue's link come before any write. Replaced rows become skipped `shuffled`, the new rows take their positions, `shuffles_used` goes up and `last_activity_at` is now (Game)

**Data modeling:** a paused game can't be shuffled (decided Oct 27, in #44 and the spec).

**Review:** I first removed a flush between marking the old rows and adding the new ones, because removing it didn't fail anything: one flush writes the UPDATEs before the INSERTs. Fabiha asked for an explicit flush instead of relying on SQLAlchemy's flush order for the partial unique index, so I put it back, and added a test where the app retries the second shuffle and must get `STATE_CHANGED`. If the current location were already finished, `by_position[game.current_position]` would raise a `KeyError` (a 500). It now returns 409 `STATE_CHANGED` before writing anything, and the details also have `current_position`. Eddy asked whether shuffling a location that is being verified is allowed. It is: the spec says a shuffle replaces every unfinished location, the new one starts with no attempts, and the limit of 2 keeps it in check. The docstring says so now, and the A10 PRs will need a test that a verification result can't be written to a shuffled row. I also wrote in the docstring that `lock_open_game` can expire the game before raising, so the caller commits that 409. Fabiha and Eddy approved.

**Tests** (`tests/backend/test_shuffle_game.py`): 14 new tests. Only unfinished locations are replaced, a second shuffle never brings back a replaced location, a stale `shuffles_used` changes nothing, a third shuffle is refused, a retry of the last shuffle gets `STATE_CHANGED` and not the limit, a game whose current location is finished is refused and not a 500, a paused game is not shuffled, not enough new locations and a missing recording leave the game as it is, a finished game, another student's game or an unknown id, and an idle game are refused, a quit after a shuffle carries over only the new locations, and shuffle doesn't commit.

## PR #284: POST /games/{id}/shuffle (A9 part 8)

**Requirement:** #44, the API table row for shuffle. This PR closes #44.

**What I did:**

- `POST /games/{id}/shuffle` (`shuffleGame`) takes `{"shuffles_used": n}`, a non-negative int, and returns the game status plus `clue_audio_url` for the new current location (frontend)
- 409 `STATE_CHANGED` has the current `shuffles_used`, `state` and `current_position` in its details, and the app gets the locations from `GET /games/{id}` (frontend)

**Review:** The route docstring (which becomes the API reference) didn't say where the locations come from after a `STATE_CHANGED`. It does now. Eddy suggested making `shuffles_used` a strict integer, so `"1"`, `1.0` and `true` are rejected, with matching validation tests, and a route-level test for the late errors (`NOT_ENOUGH_CONTENT` and a missing recording). Fabiha suggested an HTTP-level test that too little content gives 409 `NOT_ENOUGH_CONTENT` and doesn't increase `shuffles_used`.

**Tests** (`tests/backend/test_shuffle_game_api.py`): 8 new tests. The response, a stale `shuffles_used` (409 with the current value), refused shuffles (409), an idle game (409 with the expiry committed), a missing or negative `shuffles_used`, another student's game or an unknown id (404), an admin and a disabled account (403), and no token (401).

## Other work this week

- **Spec and issues:** quit and reset work on a paused game without resuming it (and a reset counts toward the daily limit), and shuffle needs an active game (spec and #44, decided Oct 27). For A19 I wrote a plan with eight decisions (weak GPS, speech rules, a 2 second ping rate limit, idempotent arrival confirmation, who owns the progress code) and opened the six PRs with their checklist items ticked on #45. For A15, the last test case of #41 (Practice points don't count on the class leaderboard or the participation export) needs the leaderboard (#88) and the export (#89), so I moved it to a new issue, #312, and left a comment on #41.
- **Reviews:** I reviewed 16 PRs by Eddy and Fabiha. Fabiha's A12 stack (#225, #228, #229, #230): I asked for changes on #228, #229 and #230 on Oct 30 and approved all four the same day. All four merged on Nov 1. Eddy's A20 PRs: I approved #285, #287, #288, #290, #292, #293 and #295. On Oct 30 I asked for changes on #291 and approved it later that day, and it merged on Nov 1. I also asked for changes on #296, the request-body limit, and it is still open. On Nov 1 I asked for changes on Fabiha's #298, #299 and #300 (the A12 endpoints and the scoring helper), and they are still open.
- **Next week:** get A19 (#305 to #310) reviewed and merged, then A15's last test case (#312) once the leaderboard and export exist. We will also divide up the work for the client demo and continue with the later part of the project. The `GET /games/{id}/summary` endpoint (A23, #47) will replace the status that quit returns now. For A10, a test that a verification result can't be written to a shuffled row.

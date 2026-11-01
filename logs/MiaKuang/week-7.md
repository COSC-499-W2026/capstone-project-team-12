# Week 7 Individual Log

## Mia Kuang

Week 7 (Oct 19 – Oct 25)

**This week** I worked on A15 (current class and Practice language, issue #41), A25 (game status, issue #42) and the start of A8 (pause, resume and the expire job, issue #43). A15 is five stacked PRs: #235, #240, #241, #244 and #247 (from Eddy's review). A25 was about 785 lines, so I split it into three stacked PRs: #248, #249 and #250. #240 and #248 are both based on #235's branch, because A25 reuses `session_idle_limit()`. A8 is four stacked PRs (#255, #257, #258, #259), open and waiting for Eddy. Fabiha has reviewed all four. I also opened #270 (API reference and dependency graph on GitHub Pages) and #271 (stop committing the two generated files, stacked on #270). Eddy and Fabiha approved and merged all the others (Oct 23 to Oct 25).

| PR | What | Files | Lines | Tests | Status |
|---|---|---|---|---|---|
| #235 | `get_open_session_id` | 5 | +113 / -13 | 4 | Merged Oct 23 |
| #240 | Current class and Practice language rules | 8 | +495 / -5 | 14 | Merged Oct 23 |
| #241 | `PATCH /me/profile` | 5 | +494 / -1 | 10 | Merged Oct 25 |
| #244 | `CLASS_ARCHIVED` for an archived class | 8 | +150 / -33 | 7 | Merged Oct 25 |
| #247 | `SESSION_ALREADY_OPEN` in one place | 7 | +61 / -25 | 2 | Merged Oct 25 |
| #248 | Game status lookup | 4 | +317 / -3 | 10 | Merged Oct 24 |
| #249 | `GET /games/current` | 7 | +449 / -11 | 8 | Merged Oct 25 |
| #250 | `GET /games/{id}` | 3 | +204 / -8 | 5 | Merged Oct 25 |
| #255 | Pause and resume service | 6 | +362 / -22 | 9 | Open, waiting for Eddy (Fabiha approved) |
| #257 | `POST /games/{id}/pause` | 6 | +472 / -12 | 9 | Open, waiting for Eddy (Fabiha approved) |
| #258 | `POST /games/{id}/resume` | 3 | +155 / -3 | 2 | Open, waiting for Eddy (Fabiha's suggestion not fixed yet) |
| #259 | Expire-sessions job | 6 | +302 / -7 | 8 | Open, waiting for Eddy (Fabiha's suggestion not fixed yet) |
| #270 | GitHub Pages site for the API reference and graph | 5 | +136 / -0 | 0 | Merged Oct 25 |
| #271 | Stop committing the generated files | 11 | +22 / -2495 | 0 (7 removed) | Merged Oct 25 |

Lines are GitHub's numbers and include the generated `docs/api/openapi.json` (+177 in #241, +229 in #249, +79 in #250, +187 in #257, +89 in #258). Tests are new test functions; a parametrized test counts once. In #271, 2,377 of the 2,495 deleted lines are the two generated files (`openapi.json` and `dependency-graph.md`); without them it is 9 files, +22 / -118. It is one file over the 10-file limit because of the `utils/` cleanup Eddy asked for. #270 and #271 only change CI and docs, so they add no tests.

**One thing went wrong:** I first opened A15 parts 2 and 3 as #236 and #238. Their history was a mess after many rebases, so I closed them and moved the work to fresh branches with categorized commits (#240 and #241). Later, while reviewing A25, I found that #240 and #241 have the re-exported `openapi.json` and dependency graph in a separate docs commit after the code, so the feature commit alone fails CI. I fixed this for A25, but #240 and #241 were merged with it. Lesson: put generated files in the same commit as the code that changes them, and check each commit, not just the branch tip.

**A second thing went wrong:** Squash merges broke our stacked PRs this week. After each merge the next PR in the stack still had the old commits, so I had to rebuild and rebase two stacks several times. The A25/A8 stack sat on #235's branch, so GitHub couldn't retarget it. I made a new stack (#267) on main with gh stack and dropped #235's old commits by hand, because the squash commit has a different hash. #244 and #247 were blocked because I had merged the branch below into them, and a GitHub stack needs linear history. I rebuilt them on main by cherry-picking their own commits. The generated `openapi.json` and dependency graph also conflicted in almost every stacked PR (#249 to #259 were rebased each time main moved), so we decided to generate them in CI instead (#270 and #271). Lesson: update stacked branches by rebasing, never with merge commits, and expect a rebase after every squash merge.

## PR #235: get_open_session_id (A15 part 1)

**Requirement:** issue #41 and the spec's Starting a Session section. Class and Practice-language changes are refused while a game is open (409 `SESSION_ALREADY_OPEN`), so Accounts must ask Game.

**What I did:**

- `get_open_session_id(student_id, db)` in `app/game/interface.py` returns the id of the student's active or paused game, or `None`. Read-only (Accounts)
- A game idle for 24 hours doesn't count as open, like in `start_game`. It stays in the data until the next start or the expire job (A8) (Game)
- `session_idle_limit()` in `app/game/sessions.py` reads `SESSION_IDLE_EXPIRY_HOURS` in one place (Game)
- `dependency-graph.md` is redrawn (developers)

**Data modeling:** the spec doesn't cover an idle game that isn't expired yet. I don't count it as open, and wrote in the PR that it can stay in the data if the expire job is late.

**Review:** Eddy found that the "exactly at the limit" test would pass with `<` instead of `<=`, because it took `now` before the function did. The test now freezes the clock in `app.game.interface` and fails with `<`. I also moved the copied idle-limit lines into `session_idle_limit()`, and added his note about idle games to the description.

No workaround. Code is in `app/game/`, because Accounts can only reach Game through its interface.

**Tests** (`tests/backend/test_game_interface.py`): an open game is found, no open game gives `None`, a game idle for exactly the expiry time doesn't count, and the lookup only reads. 4 integration tests (real Postgres).

## PR #240: Current class and Practice language rules (A15 part 2)

**Requirement:** issue #41. The app follows the student's current class, falls back to another active class or Practice when the enrolment ended, and refuses changes while a game is open. Covers Test Case 1 and the "refused while a session is open" part of Test Case 2.

**What I did:**

- `current_class_id(user, db)` in `app/accounts/profile.py` returns the stored class while its enrolment is active, otherwise the most recently joined active class, or `None` (Practice). Read-only (Accounts, frontend)
- `select_class(user, class_id, db)` makes an active class current, or `None` for Practice. Another class is 404 `NOT_FOUND`; only then 409 `SESSION_ALREADY_OPEN` while a game is open. Choosing the current class always works (Accounts)
- `set_practice_language(user, language, db)` sets the Practice language, with the same 409 unless nothing changes (Accounts)
- `get_active_class_ids` in `app/engagement/interface.py` lists active classes, newest first (Engagement)
- `lock_user` in `app/services/locks.py` locks the user row. `select_class`, `set_practice_language`, `start_game` and `validate_code_and_enrol` take it first, so a class switch and a game start can't both see "no open game" (Accounts, Game, Engagement)

**Data modeling:** the spec doesn't say which class to fall back to, so I picked the most recently joined active one. Joining a class still makes it current, even during a game, because the open game keeps its own class and language. I added this to the spec's Starting a Session section.

**Review:** The first draft wrote the fallback class to the user row on every read, which could overwrite a class the student was choosing. `current_class_id` now only reads. I also fixed a 409 when repeating the current setting, and lost unsaved changes in the lock (`lock_user` flushes first now). Eddy found that a class that isn't yours got 409 instead of 404, because `select_class` checked the open game first, and that joining a class skips the open-game check. I swapped the order with a test and kept join as it is (explained in the spec and PR). Of his optional points, `validate_code_and_enrol` now uses `lock_user`, the shared 409 is in #247, and I left the extra query for later.

No workaround, but leaving a class and archiving aren't written yet. They should take the same lock before ending the enrolment (written in the PR). Code is in `app/accounts/`, `app/services/locks.py` and `app/engagement/interface.py`, because Accounts owns the current class and reads enrolments through Engagement's interface.

**Tests** (`tests/backend/test_current_class.py` and `test_user_lock.py`): the fallback for each end reason, selecting a class or Practice, a class the student isn't in (404), an archived class, every change blocked by an open game (409), repeating a setting during a game (works), and that `start_game` and a class switch lock before looking for an open game. 14 integration tests (real Postgres).

## PR #241: PATCH /me/profile (A15 part 3)

**Requirement:** the endpoint in #41 and the spec's API table: change the current class and Practice language over HTTP.

**What I did:**

- `PATCH /me/profile` (`updateProfile`) in `app/api/v1/me.py` changes the current class (null means Practice), the Practice language, or both, in one transaction. It returns a `ProfileResponse` (`id`, `username`, `role`, `current_class_id`, `preferred_language`) (frontend)
- 404 `NOT_FOUND` for a class the student isn't in, 409 `SESSION_ALREADY_OPEN` with the game id while a game is open, 403 `FORBIDDEN_ROLE` for an admin, 422 when nothing is sent. If either change is refused, nothing is saved (frontend)
- Staff can set a Practice language, since any staff account can play Practice (Accounts)
- `docs/api/openapi.json` is re-exported and the dependency graph is redrawn (developers)

**Data modeling:** the spec didn't say what an admin gets. Admins don't play, so they get 403 `FORBIDDEN_ROLE`, as in `POST /games`. I added this to the spec's API table and to #41. `GET /me/profile` is A24 (Kaedyn), and I wrote on #73 that it must return the same `ProfileResponse`.

**Review:** A null `current_class_id` was read as "not sent", so a student couldn't switch to Practice. The request now checks which fields were sent (`model_fields_set`). `preferred_language` wasn't nullable in the response, so a student who never chose one got a 500. Admins weren't refused. Eddy asked me to reword the `preferred_language` description, because `{"preferred_language": null}` alone gets 422 while the description says null "leaves it as it is", and to explain in the PR why I chose `/me/profile` over `PATCH /me`. I did both and added a test that a null language next to a null class changes nothing.

No workaround. Code is in `app/api/v1/me.py`, because routes live in the API layer.

**Tests** (`tests/backend/test_profile_api.py`): changing class and language, null switching to Practice and back, staff setting a Practice language, a class the student isn't in (404), a game blocking changes but not repeats (409), an admin (403), no token (401), invalid bodies (422). 10 integration tests through the real app. The OpenAPI export doesn't change on Python 3.11.

## PR #244: CLASS_ARCHIVED when selecting an archived class (A15 part 4)

**Requirement:** issue #41. `POST /games` already tells a student their class was archived, but `PATCH /me/profile` returned 404. A student with the app open at the end of term should see why.

**What I did:**

- `select_class` and `start_game` both call `require_active_enrolment(student_id, class_id, db)` in `app/engagement/interface.py`: 404 `NOT_FOUND` unless the student is actively enrolled, and 409 `CLASS_ARCHIVED` if the class was archived while they were in it. `start_game`'s own copy is removed (Accounts, Game, Engagement)
- The `PATCH /me/profile` description mentions the new case, and `openapi.json` is re-exported (frontend)

**Data modeling:** the spec didn't cover this. I used the `POST /games` rule: only students who were in the class when it was archived are told, so it doesn't show whether a class exists. I added it to the `PATCH /me/profile` row in the spec and to #41.

**Review:** The first version still returned 404 to students who were in the archived class. Eddy found the same order problem as in #240 (an archived class with a game open got 409 first), so I added a test. He asked me to put back the check that `current_class_id` doesn't change after the refusal in `test_class_left_before_it_was_archived_is_not_found`, and I did. I first left his `require_active_enrolment` helper idea for later, but added it because `select_class` and `start_game` had two copies of the same rules.

No workaround. The last part of #41 (Practice points don't count on the class leaderboard or participation export) needs Fabiha's points and leaderboard work, which isn't merged. That test is a later PR, and I wrote this on #41.

**Tests** (`test_current_class.py`, `test_profile_api.py`, `test_engagement_interface.py`): a former student gets `CLASS_ARCHIVED` (`test_archived_class_is_explained_only_to_its_students`), a student who never joined or left before archiving gets 404, a former student with a game open gets `CLASS_ARCHIVED`, one API test, and the helper's own tests. 7 integration tests (real Postgres).

## PR #247: SESSION_ALREADY_OPEN in one place (A15)

**Requirement:** follow-up from Eddy's review on #240. The same 409 was built in `profile._refuse_if_game_open` and `start._check_no_open_game`, so the client could get two different 409s for one rule.

**What I did:**

- `game_already_open(game_id)` in `app/game/sessions.py` builds the 409 with the game id in the details (Game)
- `refuse_if_game_open(student_id, db)` in `app/game/interface.py` raises it when a game is open. `accounts/profile.py` calls it, and `start_game` uses `game_already_open` (Accounts, Game)
- `docs/api-conventions.md` says what the `details` hold (`[{"session_id": ...}]`) and that they are empty when two starts race and the winner's game has already ended (developers)

**Review:** `ruff` caught a missing `pytest` import in the new test, and the dependency graph was out of date. Nothing changes for the client.

No workaround. Code is in Game, because the open-game rule belongs to Game and Accounts reaches it through the interface.

**Tests** (`tests/backend/test_game_interface.py`): 2 new integration tests for `refuse_if_game_open` (it raises with the game id and does nothing without an open game; it doesn't refuse a game idle for the expiry time). The existing tests still pass.

## PR #248: Game status lookup (A25 part 1)

**Requirement:** issue #42 and the spec's Session Lifecycle and API table. A student coming back needs to see their game: current location, what is done, and the score. Expiry is checked whenever a session is read.

**What I did:**

- `find_current_game(student_id, db)` in `app/game/status.py` returns the student's open (active or paused) game, or `None` (Game)
- `get_game(student_id, game_id, db)` returns one of the student's games, open or finished. Another student's game is 404 `NOT_FOUND`, as is an unknown id, and is left as it is (Game)
- Both return a `GameStatus` (game state, class, language, position, score, and each location's status and points) with nothing that gives a location away: no name, room, coordinates or answers (frontend)
- An open game idle for 24 hours is expired while reading with `abandon_sessions(EXPIRED, ...)`. The UPDATE checks the idle condition again, and the game is always reloaded afterwards (Game)

**Data modeling:** the spec didn't say what another student's session returns, so I picked 404, as for classes, and the spec's API table now says so and lists the fields. These reads don't skip a deactivated location. The spec says that happens when the session moves on or through `current-location` (A19, #45), so the reads write only the expiry.

**Review:** The expiry only reloaded the game when its own UPDATE changed something, so a game ended at the same moment by the expire job or a quit was still reported as active or paused. It now always reloads, and `test_game_ended_elsewhere_is_not_reported_as_open` fails with the old code. I also fixed wrong "Flushes" docstrings and a test setup missing `completed_at`, which the `session_locations` CHECK needs. I added tests for another student reading an idle game (must not expire it), a class game, and a `class_closed` game.

No workaround. The expiry reuses `abandon_sessions` and `idle_condition` from #194, so the idle rule isn't written again. Code is in `app/game/status.py`, because reporting a game belongs to Game.

**Tests** (`tests/backend/test_game_status.py`): open games with their locations in order, a finished game, a class game, a `class_closed` game, no open game, an idle game expired when read (by both lookups), a game within the limit not expired, another student's game looking missing and not expired by their read, and a game ended elsewhere not reported as open. I checked the new tests fail when I put the bugs back. 10 integration tests (real Postgres).

## PR #249: GET /games/current (A25 part 2)

**Requirement:** #42's Test Case 1 (the status of an active game is returned) and Test Case 2 (an error when there is no active game).

**What I did:**

- `GET /games/current` (`getCurrentGame`) in `app/api/v1/games.py` returns the student's open game (paused counts too) as a `GameStatusResponse` (frontend)
- 404 `NOT_FOUND` if there is no open game, including when the only one was idle and got expired while reading. The route commits before raising the 404, so the expiry is kept. 401 without a token, 403 `ACCOUNT_DISABLED` for a disabled account, 403 `FORBIDDEN_ROLE` for an admin, as in `POST /games` (frontend)
- Times are always UTC with a trailing `Z`: the engine in `app/db.py` and the test engine in `conftest.py` connect with `-c timezone=utc` (frontend, developers)
- `openapi.json` and the dependency graph are in the same commit as the route (developers, CI)

**Review:** The first `test_expiry_while_reading_is_committed` only checked the game's state, which passes without the commit because the test shares the session with the route. It now checks the commit with `transaction_calls` and fails if the commit moves after the 404. The generated files were in a separate docs commit, so the feature commit alone would fail CI. `started_at` and `paused_at` were UTC only because my local database was, so I set the time zone on the engine and added tests for the Z and the UTC connection. `GET /games/current` gave an admin 404 while `POST /games` gave 403, so both give 403 now.

No workaround. Code is in `app/api/v1/games.py`, because routes live in the API layer and call Game.

**Tests** (`tests/backend/test_game_status_api.py`): the open game returned without anything that gives a location away, no open game (404), an idle game expired while reading (404, commit checked), no token (401), a disabled account (403), an admin (403), and times in UTC with a Z. 8 integration tests through the real app. The OpenAPI export doesn't change on Python 3.11.

## PR #250: GET /games/{id} (A25 part 3)

**Requirement:** the spec's API table (`GET /games/{id}`: session state and per-location status). A student can look at any of their games, open or finished.

**What I did:**

- `GET /games/{game_id}` (`getGame`) returns one of the student's games with the same body as `GET /games/current`. It's declared after `/current`, so "current" isn't read as an id (frontend)
- Another student's game or an unknown id is 404, and the other student's idle game is not expired or committed (Game). An id that isn't a UUID is 422. The student's own idle open game is expired while reading and the route commits it. 403 `FORBIDDEN_ROLE` for an admin (frontend)
- `openapi.json` is re-exported in the same commit (developers, CI)

**Review:** Nothing checked that this route commits the expiry: deleting its `db.commit()` still passed every test, so I added `test_expiry_while_reading_by_id_is_committed`. There were no tests for an id that isn't a UUID or a disabled account, and the tests only checked a few fields, so they now check the full body (no room, answer or coordinate), the 200 with the new database state, and `VALIDATION_ERROR`. Parametrized tests used `uuid4()`, so I use a fixed id now.

No workaround.

**Tests** (`tests/backend/test_game_status_api.py`): the student's own game by id, another student's game or an unknown id (404), an id that isn't a UUID (422), an idle game expired while reading, another student's idle game (404, not expired), an admin (403), and the 401 and 403 tests, now parametrized over both routes. Integration tests through the real app.

## PR #270: Publish the API reference and the dependency graph on GitHub Pages

**Requirement:** no issue. The generated `docs/api/openapi.json` and `docs/architecture/dependency-graph.md` keep causing merge conflicts. This is the first of two PRs and only adds the Pages site.

**What I did:**

- New workflow `.github/workflows/pages.yml` runs on a push to `main` and on `workflow_dispatch`, generates both files, builds a small `site/` folder and deploys it with `actions/deploy-pages` (developers, CI)
- New pages in `.github/pages/`: `index.html`, `api.html` (Redoc 2.5.4) and `graph.html` (Mermaid 12.2.0), both libraries loaded from jsDelivr with a fixed version. `README.md` links to the site (developers)
- `ci.yml`, the `--check` steps and the committed files are not touched

**Review:** `api.html` was blank with Redoc 3, because the v3 bundle is an ES module and a normal `<script>` fails with an `import.meta` error. I pinned Redoc 2.5.4 and call `Redoc.init`, and checked in headless Chrome that the title and endpoints render. I found nothing else wrong. The first deploy can only happen after the merge, and in Settings > Pages, Source is set to `GitHub Actions`. It was squash-merged on Oct 25 at 11:10 pm Pacific. The Pages workflow ran and succeeded, and the site (https://cosc-499-w2026.github.io/capstone-project-team-12/) and its API reference and dependency graph pages load.

**Tests:** none added. I ran `actionlint`, both generators and `pytest` in `tests/utils`, and all passed (from the PR description).

## PR #271: Generate the OpenAPI document and the dependency graph in CI

**Requirement:** no issue. The second of two PRs. #270 merged first, so the size is counted against `main`.

**What I did:**

- `docs/api/openapi.json` and `docs/architecture/dependency-graph.md` are removed from git and added to `.gitignore`. The `backend` job in `ci.yml` runs `python utils/export_openapi.py` and `python utils/dep_graph.py --graph --check`, so `--check` only fails on a broken import rule (developers, CI)
- `test_committed_spec_is_up_to_date` and `test_committed_graph_is_up_to_date` are removed, because they read the committed files (developers)
- `README.md`, `CONTRIBUTING.md` and `src/backend/README.md` point to the Pages site and the new commands (developers)
- `utils/` cleanup, from Eddy's review: `dep_graph.py --check` only checks the import rules and `graph_is_up_to_date` is removed. `--check` and `is_up_to_date` are removed from `export_openapi.py`, with their docstrings and the staleness tests. The rule-check tests stay (developers)

**Trade-off:** PR diffs no longer show API changes, because `openapi.json` isn't committed. After this merges, open PRs that touch the two files will conflict.

**Review:** Without `--graph` in front of `--check` the dependency step would always fail, because the file no longer exists, so CI uses both flags. Eddy found that `utils/` still described the old workflow: `dep_graph.py --check` always failed on a fresh clone and told people to commit a git-ignored file, and `export_openapi.py --check` and its docstring still talked about a committed copy. I cleaned that up in this PR, which is why it is 11 files. Not run: the full backend `pytest` suite, because it needs Postgres and CI runs it. I ran `actionlint`, both generators, `dep_graph.py --graph --check`, `ruff` and `pytest` in `tests/utils` (53 passed). It was merged on Oct 25 at 11:21 pm Pacific.

**Tests:** none added. 7 tests are removed: the two above and the 5 staleness tests in `tests/utils` (`test_dep_graph.py`, `test_export_openapi.py`).

## Other work this week

- **Reviews:** I reviewed 22 PRs this week, with 56 reviews and 74 inline comments. On Fabiha's A13 class stack (#220, #221, #222, #224, #245) I approved #220, #221 and #222 after her fixes. The main points were `VALIDATION_ERROR`s with empty `details`, a class name with a NUL character giving a 500 instead of a 422, routes that don't match the spec's `/teacher/...` paths, #224 being over 500 lines, and no test for a missing class. On her A14 stack (#225, #228, #229, #230) I pointed out that ledger rows can have a language that doesn't match their class, the source types should be an enum, the seed and new tables have no tests, the spec's ledger index is missing, and nothing in Game calls `apply_location_result` yet. On #233 I noted that a join now overwrites the Practice language. On Eddy's A20 stack I approved #219 and #223, and on #226 I pointed out that a very short MP3 gets 415, which the spec only uses for wrong file types. I requested changes on #207 (wrong base and conflicts) and approved #208, #211, #214 and #216.
- **Spec:** `PATCH /me/profile` (409 `CLASS_ARCHIVED`, 403 `FORBIDDEN_ROLE`), `POST /games` (403 for admins), joining a class during a game (Starting a Session), the `GET /games/current` and `GET /games/{id}` rows (fields, 404 for another student's session, expiry on read), the deactivated-location rule in Content Changes, and for A8 that pause and resume count as game actions (Session Lifecycle) and the expire job skips locked games (Abandonment).
- **Issues:** I updated #41 (endpoints, `CLASS_ARCHIVED`, what is left for A15), #42 (endpoints), #43 (the pause and resume decisions), #45 (A19 owns `current-location` and skipping a deactivated location) and #73 (A24 must return the same `ProfileResponse`).
- **DFDs:** I updated three level 1 DFDs: Game Session (new flow 41 for the expiry and skipped locations, flow 37 "(or 404)"), Auth & Accounts (flows 21 and 22) and Class Engagement (new flow 40 for the current class written to D1). At the end of the week I updated the ER diagram for the open A12 and A2 PRs (language and the (class_id, language) foreign key on points_ledger, the unique key on user_achievements, replaced_by on refresh_tokens) and four flow labels to match the merged PRs: Auth & Accounts flow 3, Class Engagement flow 4 and Content Management flows 10 and 12.
- **Next week:** get #255, #257, #258 and #259 reviewed and merged (including Fabiha's suggestions on #258 and #259), then A9 (quit and reset) and A19.

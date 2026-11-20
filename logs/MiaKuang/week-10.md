# Week 10 Individual Log

## Mia Kuang

Week 10 (Nov 9 – Nov 15)

**This week** I finished A23 (game history and summary, #47), #46 (the GPS replay script) and the two A3 password reset follow-ups (#198, #199). I also got CI running again on our own runner and pinned a missing dependency. Eddy and Fabiha approved all eleven PRs, and they were merged between Nov 11 and Nov 15. Nothing I opened this week is still open.

| PR | What | Files | Lines | Tests | Status |
|---|---|---|---|---|---|
| #347 | Run CI on our self-hosted runner | 3 | +19 / -4 | 0 | Merged Nov 11 |
| #326 | GPS replay script for walking routes (#46 part 1) | 3 | +469 / -0 | 8 | Merged Nov 14 |
| #335 | Pin `limits` and its dependencies | 1 | +3 / -0 | 0 | Merged Nov 14 |
| #349 | `GET /me/games`, the game history (A23) | 3 | +495 / -2 | 12 | Merged Nov 14 |
| #350 | The level and badges one game added (A23) | 4 | +291 / -3 | 8 | Merged Nov 14 |
| #351 | `GET /games/{id}/summary` (A23) | 5 | +380 / -3 | 7 | Merged Nov 14 |
| #334 | A reset revokes every refresh token and stops older reset mail (A3) | 6 | +313 / -17 | 6 | Merged Nov 14 |
| #336 | A login or refresh during a reset can't keep a working token (A3) | 5 | +306 / -20 | 3 | Merged Nov 14 |
| #352 | Quit and a finished current-location return the summary (A23) | 5 | +61 / -20 | 1 | Merged Nov 15 |
| #327 | Replay tests against the real routes (#46 part 2) | 5 | +356 / -2 | 13 | Merged Nov 15 |
| #340 | GPS replay review fixes (#46) | 6 | +385 / -60 | 16 | Merged Nov 15 |

Tests are new test functions (a parametrized test counts once). Generated files (openapi.json, the dependency graph) are not included. Function lengths include the docstring.

**How I checked code quality on every PR.** I ran ruff, strict mypy, the component dependency check, the alembic checks and the full test suite, and removed each line under test to see its test fail. The sections below only list what these checks or the reviews found.

**One thing went wrong: CI could not run.**

- GitHub Actions jobs never started because of the organization's billing issue, so PRs were only checked on my machine. Therefore, I moved all three workflows to a self-hosted runner, `hunt-ci-runner` on an Oracle Cloud free VM (#347).
- I added an `if:` that skips fork PRs as a safeguard. Eddy pointed out that a fork can edit the workflow, and a skipped job counts as passed. Therefore, I removed it. The real protection is the repo setting that makes a fork's workflow wait for approval.
- Our runner keeps files between runs, so one branch's packages could leak into the next. Therefore, each job installs into a new venv.
- One runner means jobs queue. Therefore, a new push cancels the PR's older run; runs on main are never cancelled.
- The branch's upstream was `origin/main`, so a plain `git push` would have pushed to main. I removed the upstream before pushing.
- **Lesson: a self-hosted runner on a public repo is a security decision, and a skipped check is not a passed check.**

**A second thing went wrong: some tests passed without the code they test.**

- When I removed each tested line in #350, #336 and #340, four changes still passed every test: two conditions in the level and badge query (#350), the login's lock (#336), and the rate-limit wait when the replay catches up (#340).
- Therefore, each of them now has a test that fails without it.
- **Lesson: a test only counts once I have seen it fail without the fix, so I do this check before opening a PR.**

## PR #349: GET /me/games, the game history (A23 part 1)

**Requirement:** As part of A23 (#47), a student opens their game history, filters it by language and pages through it. Therefore, I implemented `list_history` and `GET /me/games` for students (and staff for their own Practice games).

**Data modeling:** Paging and language are validated as in `docs/api-conventions.md` (422 otherwise). No new column. A response never contains a location's name, room or coordinates.

**Generated code review:** When I reviewed the paging, I noticed a huge `page` overflowed the database OFFSET and returned 500. Therefore, I capped `page` (replaced after review, below).

**Code quality:** No temporary workaround: the end time for an expired game is a documented choice, and the real end-time column is tracked in #348. Functions are 8 to 55 lines; the longest, `list_history`, is one query with its docstring.

**Teammate review:** Eddy pointed out that my cap broke our API conventions. Therefore, a page past the end now returns early with no items. I kept the last action as an expired game's end time, because that is when the student last played; a real end-time column is in #348. His optional points went to #354.

**Design:** Written in the new `app/game/history.py` because history only reads game sessions, which the game component owns; the route is in `app/api/v1/me.py` with the other `/me` routes. It belongs in process 2.7 Report status & history (DFD Level 2, Game Session), which reads open and finished sessions from D5.

**Tests:** 12 integration tests in `tests/backend/test_game_history.py`. Happy path: games newest first, filtered and paged. Abnormal: an empty history, a page past the end, an idle game expired and listed. Negative: another user's games never appear; bad paging and an admin are refused. The full suite passed before merge.

## PR #350: The level and badges one game added (A23 part 2)

**Requirement:** As part of A23 (#47), a student who finishes a game sees the level reached, any level-up and the badges that game unlocked. Therefore, I implemented `get_game_engagement` for the game summary.

**Data modeling:** No new column. A badge is saved with the award that earned it, so both have the same time; comments on both columns keep it that way.

**Generated code review:** See "A second thing went wrong" above: two conditions in the query had no test, and now each does.

**Code quality:** No temporary workaround. Functions are 8 to 61 lines; the longest, `get_game_engagement`, is three related queries for one game. `engagement/interface.py` was already over 500 lines; I added 6.

**Teammate review:** Eddy found nothing blocking. Of his optional points I computed the level once instead of twice; a shared badge helper went to #354.

**Design:** Written in `app/engagement/stats.py` because points and badges belong to the engagement component; the game component reads them through its interface. It belongs in process 4.6 Report engagement (DFD Level 2, Class & Engagement).

**Tests:** 8 unit tests with a real database in `tests/backend/test_game_engagement.py`. Happy path: the level and badges as of the game's last award. Abnormal: a game with no award. Negative: other games, other students and non-location points are not counted. The route's integration tests are in #351. The full suite passed before merge.

## PR #351: GET /games/{id}/summary (A23 part 3)

**Requirement:** As part of A23 (#47), a student opens the summary of any of their games to see results, points, level and new badges. Therefore, I implemented `GET /games/{id}/summary` for students and staff in Practice.

**Generated code review:** When I reviewed the response, I noticed the level and badges were at the top level instead of in the nested `engagement` object we agreed on Oct 23. Therefore, I moved them. I also noticed the lookup, 404 and expiry were copied from `get_game`. Therefore, both now use a shared `load_game`.

**Code quality:** No temporary workaround. Functions are 4 to 19 lines. `api/v1/games.py` was already 520 lines before this stack and is 581 after it; moving the summary into its own module is a follow-up.

**Teammate review:** Eddy found that my new badge schema duplicated `RecentBadgeResponse`, so the frontend would get two types for one thing. Therefore, the summary reuses it.

**Design:** Written in the new `app/game/summary.py` because the summary combines game results (game component) with engagement read through its interface. It belongs in process 2.7 Report status & history.

**Tests:** 7 integration tests in `tests/backend/test_game_summary_api.py`. Happy path: a completed game with its points and badge. Abnormal: a quit game, an open game, an idle game. Negative: another student's game is 404; an admin is refused. The full suite passed before merge.

## PR #352: Quit and a finished current-location return the summary (A23 part 4)

**Requirement:** As part of A23 (#47, which this PR closes), a student who quits, or whose last location is deactivated, should land straight on the summary. Therefore, quit and current-location return the summary for that case.

**Generated code review:** When I reviewed the change, I noticed a comment in `current_location.py` still said the game returns its status. Therefore, I updated it.

**Code quality:** No temporary workaround; the response keeps every field of the old one, so existing clients keep working. Functions are 3 to 33 lines. `games.py` is 591 lines after this PR (see #351).

**Teammate review:** Eddy suggested a second way to build the summary. I kept one shared path, so quit didn't have to change. I merged #349 first, so #47 didn't close without the history.

**Design:** In `app/api/v1/games.py` and `app/game/current_location.py`, where the two routes already were. It belongs in processes 2.5 Control session and 2.2 Serve current location.

**Tests:** 1 new integration test: quit returns the level and badge the game earned. The completed current-location test now expects the summary. The full suite passed before merge.

## PR #326: GPS replay script for walking routes (#46 part 1)

**Requirement:** As part of #46 and the spec's Simulation Scripts, a developer replays a recorded walking route against a real game and reads the feedback at each step. Therefore, I implemented `utils/gps_replay.py` for developers and testers.

**Data modeling:** The route file must have the header `t_s,lat,lng,accuracy_m`, real numbers in range, and at least 2 s between rows. It is checked before anything is sent.

**Generated code review:** When I reviewed the error handling, I noticed a timeout crashed the script and a lost connection let it carry on. Therefore, both now stop the replay with exit 1.

**Code quality:** No temporary workaround. Functions are 2 to 47 lines; the longest, `replay`, is the main loop. `MIN_GAP_S = 2.0` is a named constant for the server's ping limit, because the script runs outside the backend and can't read its settings.

**Teammate review:** Eddy found six problems. Therefore, I fixed all six in #340.

**Design:** Written in `utils/gps_replay.py`, next to `dep_graph.py` and `export_openapi.py`, because it is a developer tool that calls the API from outside the backend. It is not a DFD process; it acts as the Player and calls processes 2.3 Process GPS ping and 2.4 Confirm arrival.

**Tests:** 8 unit tests against a fake API in `tests/utils/test_gps_replay.py`. Happy path: a route replayed and confirmed. Abnormal: a refused ping is skipped. Negative: a bad route file is refused. Integration tests are in #327. The full suite passed before merge.

## Test PR #327, written to assess the functionality in #326 (GPS replay script)

**Generated code review:** When I reviewed the README section, I noticed it didn't say to run the script from the repo root. Therefore, I added it.

**Teammate review:** Eddy found that the pause test used private code (`api._call` and a patched `Api.ping`). Therefore, it pauses through the public route (in #340).

**Tests:** 13 tests. 4 integration tests in `tests/backend/test_gps_replay_api.py` run the script against the real routes. Happy path: walking in and confirming. Abnormal: a weak signal. Negative: a pause stops the replay. 9 unit tests in `tests/utils/test_gps_replay.py` cover stopping errors and the HTTP client. The full suite passed before merge.

## PR #340: GPS replay review fixes (#46 part 3)

**Requirement:** Eddy's reviews of #326 and #327, and #46, which this PR closes. A tester's replay must follow the recorded timeline and stop cleanly on bad input. Therefore, I fixed the six problems from his review.

**Data modeling:** Route files saved from Excel (with a BOM) are accepted, rows with extra columns are refused, and errors give the real file line.

**Generated code review:** When I reviewed the catch-up test, I noticed it never reached the 2 s rate-limit check. Therefore, I changed it so it reaches the check and fails without it.

**Code quality:** No temporary workaround. Functions are 2 to 61 lines; `replay` grew to 61 because it now keeps the timeline and the rate limit.

**Teammate review:** Eddy checked each fix and asked for five small robustness fixes, such as a clear exit for a file that isn't UTF-8. Therefore, I made all five.

**Design:** Same files as #326 and #327.

**Tests:** 16 unit tests in `tests/utils/test_gps_replay.py`. Happy path: a route stays on time when answers are slow. Abnormal: catching up keeps the rate limit. Negative: a file that isn't UTF-8 and an unusable answer stop the replay. The full suite passed before merge.

## PR #334: A reset revokes every refresh token and stops older reset mail (A3 follow-up 1)

**Requirement:** As part of A3 (#198, #199), a user who resets a forgotten password must be signed out of every other device, and an older reset email must stop working. Therefore, a reset revokes every refresh token, and the email job skips an older reset email.

**Data modeling:** Both times (link used, mail queued) are the real clock time, so the job can tell which came first.

**Generated code review:** When I reviewed the email check, I noticed it compared two transaction-start times, so one ordering still let the mail out. Therefore, both use the clock time, with a test for that ordering.

**Code quality:** No temporary workaround. Functions are 11 to 62 lines; `queue_email` (62) already existed and only its timestamp changed.

**Teammate review:** Eddy pointed out that the code said a reset "signs the account out", but an access token still works until it expires (60 minutes). Therefore, I reworded the comment, the route description and the PR. He also noted that already-revoked tokens got a new time; that is fixed in #336.

**Design:** Written in `app/accounts/email_links.py` (the reset) and `app/services/email_sender.py` (the job), where each already lived. It belongs in processes 1.3 Verify email, reset password and 1.5 Send queued emails (DFD Level 2, Auth & Accounts).

**Tests:** 6 integration tests with real Postgres in `tests/backend/test_reset_password.py` and `test_email_sender.py`. Happy path: every device is signed out. Abnormal: a reset racing the email job. Negative: a refused reset revokes nothing. The full suite passed before merge.

## PR #336: A login or refresh during a reset can't keep a working token (A3 follow-up 2)

**Requirement:** As part of A3 (#198, which this PR closes), a reset must revoke every refresh token, including one that a login or refresh is creating at that moment. Therefore, login and refresh now wait for a reset on the same user. Split out of #334 to keep it under 500 lines.

**Generated code review:** When I reviewed the tests, I noticed removing the login's lock left every test passing. Therefore, the new race test runs for both login and refresh, and fails without either lock.

**Code quality:** No temporary workaround. Functions are 3 to 42 lines; the longest, `rotate_tokens`, has to lock the user before it checks the token.

**Teammate review:** Eddy found that if one test thread failed, the other was never finished. Therefore, each thread is finished on its own. He also asked for the rule "lock the user first" to be written where callers see it, so I added it to the interface.

**Design:** Written in `app/accounts/tokens.py` and `app/accounts/login.py`, where refresh and login already were. It belongs in process 1.2 Log in, refresh and log out.

**Tests:** 3 integration tests with two real database sessions in `tests/backend/test_login.py`. Happy path: a refresh waits for a reset without a deadlock. Abnormal: a token created during a reset is revoked too. Negative: a login checked before a reset is refused. The full suite passed before merge.

## PR #347: Run CI on our self-hosted runner

**Requirement:** No issue. The team needs CI on every PR again. Therefore, all three workflows run on our own runner.

**Teammate review:** see "One thing went wrong" above.

**Design and tests:** Only `.github/workflows/`. The CI run on this PR used the new runner and passed.

## PR #335: Pin `limits` and its dependencies

**Requirement:** Follow-up to #314, which added `limits` without pinning it, so installs picked any version. Therefore, I pinned it and the two packages it needs in `requirements.txt`.

**Teammate review:** Eddy checked the versions against PyPI and our Python 3.11 and approved.

## Other work this week

- **Spec and issues:** I opened #348 (the streak in the game summary, and one definition of when a game ended) and #354 (the A23 review follow-ups: a shared expire-on-read helper, a shared page type, and small cleanups). I didn't change the spec, the DFDs or the system architecture this week; the A23 routes go into all three in week 11.
- **Reviews:** I reviewed 21 PRs. I approved Eddy's seven A10 PRs (#337 to #346 and #355, Nov 10–15). For Fabiha, I approved four A13 PRs on Nov 14 and asked for changes on her A13, A14 and A16 stacks (#356 to #372, 14 PRs) on Nov 15.
- **Next week:** get my open PRs reviewed and merged: rate limits with Kaedyn (#322), the A23 follow-ups (#354), the shared game errors (#400) and the expire-sessions batch cap (#315). Update the spec, the DFDs and the system architecture for the A23 routes. Run the client demo with Eddy on the real backend.

# Week 11 Individual Log

## Mia Kuang

Week 11 (Nov 16 – Nov 22)

**This week** I finished the rate-limit hardening (#322) and moved the class staff checks onto the routes (#378). I also finished the A4 scan of student responses (#71) and moved the docs site to Netlify. I also brought the README in line with main and the new diagrams. Fabiha and Kaedyn reviewed the thirteen merged PRs, and Eddy reviewed the Netlify one. Six PRs are still open: the A23 follow-ups, the shared game errors, the expire-sessions cap and the clue plays (#436).

| PR | What | Files | Lines | Tests | Status |
|---|---|---|---|---|---|
| #375 | Deploy the docs site to Netlify | 4 | +169 / -21 | 0 | Merged Nov 17 |
| #377 | CI Postgres on a free host port, so runners can share the VM | 1 | +8 / -5 | 0 | Merged Nov 17 |
| #379 | `require_class_role` returns 404 for an unknown class (#378) | 2 | +14 / -1 | 1 | Merged Nov 17 |
| #380 | Role access tests on the shared fixtures (#378) | 1 | +154 / -205 | 0 | Merged Nov 21 |
| #388 | Class staff access checked on the routes (#378) | 10 | +109 / -308 | 6 | Merged Nov 21 |
| #390 | Global rate limit per signed-in user, 120 a minute (#322) | 9 | +223 / -42 | 5 | Merged Nov 21 |
| #392 | Rate limits counted with `limits`, slowapi dropped (#322) | 10 | +84 / -71 | 1 | Merged Nov 21 |
| #394 | Registration limited to 100 a minute per IP (#322) | 5 | +30 / -2 | 1 | Merged Nov 22 |
| #399 | Photo uploads limited to 10 a minute per user (#322) | 5 | +35 / -2 | 1 | Merged Nov 22 |
| #383 | Scan of every response a student can get (A4, #71) | 1 | +246 / -0 | 6 | Merged Nov 22 |
| #396 | Real client IP behind nginx (#322) | 2 | +15 / -0 | 0 | Merged Nov 22 |
| #451 | README matches main and the new diagrams | 1 | +17 / -15 | 0 | Merged Nov 22 |
| #454 | Reverse-proxy advice that #396 missed (#322) | 2 | +6 / -2 | 0 | Merged Nov 22 |
| #401 | Cap the expire-sessions batches (#315) | 4 | +51 / -3 | – | Open, approved |
| #402 | Shared expire-on-read and badge helpers (#354) | 5 | +40 / -39 | – | Open, approved |
| #403 | Shared page type for paged routes (#354) | 3 | +72 / -48 | – | Open, approved |
| #404 | Game stage and state errors in one place (#400) | 10 | +58 / -127 | – | Open, approved |
| #442 | Count each play of the current clue (#436) | 8 | +289 / -11 | – | Open, waiting for review |
| #446 | One class name check and one staff lookup (#400) | 3 | +34 / -63 | – | Open, waiting for review |

Tests are new test functions (a parametrized test counts once). #380 rewrote 18 tests as 8. #390 also carries Kaedyn's two tests from #328. Generated files are not included.

**How I checked code quality on every PR.** I ran ruff, strict mypy, the component dependency check, the alembic checks and the full test suite. I also removed each line under test to see its test fail. The sections below only list what these checks or the reviews found.

**One thing went wrong: two checks passed without checking anything.**

- The global rate limit from #314 never limited any `/api/v1` route. slowapi's middleware couldn't find routes in included routers on FastAPI 0.142, and no test sent enough requests to notice. Therefore, #390 made the limit a dependency on the API router. A new test checks that every route documents 429.
- The first version of the #383 scan read `app.routes`, which on FastAPI 0.142 only lists `/health`. It scanned nothing and still passed. Therefore, it now collects the routers itself, and a guard test fails if any documented route is skipped.
- **Lesson: a check that never runs looks the same as one that passes, so every check needs a test that proves it ran.**

## PR #379: require_class_role returns 404 for an unknown class (#378 part 1)

**Requirement:** As part of #378, a teacher or TA who opens a class that doesn't exist should get 404, not 403. Therefore, I made `require_class_role` check that the class exists before the role.

**Generated code review:** When I reviewed the plan, I noticed it called `is_class_staff` from engagement. accounts already imports engagement, so that is an import cycle. Therefore, the check stays in accounts and runs as a route dependency.

**Code quality:** No temporary workaround. Functions are 15 to 20 lines.

**Teammate review:** Fabiha and Kaedyn found nothing blocking.

**Design:** Written in `app/accounts/interface.py`, because who is staff of a class belongs to accounts. It guards processes 4.1 Set up classes and 4.2 Manage access codes (DFD Level 2, Class & Engagement).

**Tests:** 1 integration test in `tests/backend/test_role_access.py`. Negative: an unknown class is 404 `NOT_FOUND`. The full suite passed before merge.

## Test PR #380, written to assess the functionality in #379 (class role checks)

**Generated code review:** When I reviewed the bad-token cases, I noticed one used a short key, so the JWT library warned. Therefore, it uses a key of 32 bytes or more.

**Code quality:** The file went from 359 to 212 lines. Copied JWT setup became the shared fixtures, and repeated cases became parametrized tests.

**Teammate review:** Fabiha and Kaedyn found nothing blocking.

**Tests:** 8 integration tests (18 before) in `tests/backend/test_role_access.py`. Happy path: an owner and a TA reach the routes they may use. Abnormal: no expiry, no subject, a user that doesn't exist. Negative: expired tokens, another key, and roles that aren't allowed. The full suite passed before merge.

## PR #388: Class staff access checked on the routes (#378 part 3)

**Requirement:** As part of #378, every teacher class route checks access the same way, with the same 401, 403 and 404 order. Therefore, I moved the role checks out of four engagement services into route dependencies for teachers and TAs.

**Generated code review:** When I reviewed the services after the move, I noticed `deactivate_access_code` still had a 404 that can't happen. Therefore, I removed it and tested the missing-code case. Admins creating a class also had no test, so the student test now covers admins too.

**Code quality:** No temporary workaround. Functions are 8 to 90 lines. The longest, `generate_access_code`, already existed; this PR shortened it from 103 lines. `api/v1/classes.py` was 742 lines before this PR and 745 after. It holds every class route and its models, and the A16 routes made it grow. Splitting it by feature is a follow-up.

**Teammate review:** Fabiha worried that services without their own check could be called from somewhere unguarded. Therefore, I checked every caller: only the guarded routes call them. Keeping both checks would bring back the duplication #378 removes, so the route tests now fail if a guard is removed.

**Design:** The guards are in `app/accounts/interface.py` and used in `app/api/v1/classes.py`. Engagement can't import accounts, so the check runs on the routes. It belongs to processes 4.1 Set up classes and 4.2 Manage access codes.

**Tests:** 6 tests, unit and integration, in the class and access-code test files under `tests/backend/`. Happy path: the owner updates the class. Abnormal: an archived class can't be updated. Negative: an outsider, a TA on an owner-only route and an admin are refused. The full suite passed before merge.

## PR #390: Global rate limit per signed-in user (#322 part 1)

**Requirement:** As part of #322 and the spec's Rate Limits, a signed-in user gets 120 requests a minute of their own. Many students behind one campus IP no longer share one bucket. Therefore, I implemented a global limit keyed on the user, or on the IP for everyone else.

**Data modeling:** An invalid or made-up token counts against the IP, so nobody can use another user's bucket. A token with a malformed subject now gets 401 instead of 500.

**Generated code review:** When I reviewed the plan, I noticed it put the token check in `services/limiter.py`. Shared code may not import accounts. Therefore, the limit moved to the entry layer, `app/api/rate_limit.py`.

**Code quality:** No temporary workaround. Functions are 7 to 16 lines.

**Teammate review:** Fabiha and Kaedyn found nothing blocking.

**Design:** Written in the new `app/api/rate_limit.py`, because the entry layer may import every component. It is not a DFD process. It guards every flow into the system and is shown in the system architecture's shared core.

**Tests:** 5 integration tests in `tests/backend/test_rate_limit_keys.py`. Happy path: a user has their own limit. Abnormal: a valid token on an auth route still counts on the IP. Negative: bad, expired and non-bearer tokens count on the IP. The full suite passed before merge.

## PR #392: Rate limits counted with `limits`, slowapi dropped (#322 part 2)

**Requirement:** As part of #322, every 429 should look the same and come with `Retry-After`. Therefore, I replaced slowapi with one counter that every limit uses.

**Generated code review:** When I reviewed the first draft, I noticed it also added the register limit, which took it to 12 files. Therefore, register moved to #394.

**Code quality:** No temporary workaround. Nothing uses slowapi's private API any more, so an upgrade can't turn a 429 into a 500. Functions are 2 to 23 lines. `tests/backend/conftest.py` is 686 lines; this PR only changed four of them. Splitting the shared fixtures into plain modules is a follow-up.

**Teammate review:** Fabiha and Kaedyn found nothing blocking.

**Design:** The counter is in `app/services/limiter.py`, shared code with no component imports. The limits are in `app/api/rate_limit.py`.

**Tests:** 1 integration test in `tests/backend/test_rate_limit.py`. Negative: login is limited per IP across different emails. Kaedyn's tests from #328 pass unchanged. The full suite passed before merge.

## PR #394: Registration limited to 100 a minute per IP (#322 part 3)

**Requirement:** As part of #322, the spec limits sign-ups to 100 a minute per IP. Therefore, I added a register limit for signed-out callers.

**Code quality:** No temporary workaround. The new function is 2 lines.

**Teammate review:** Kaedyn and Fabiha found nothing blocking.

**Design:** In `app/api/rate_limit.py`, used by `POST /auth/register`. It guards process 1.1 Register account (DFD Level 2, Auth & Accounts).

**Tests:** 1 integration test. Abnormal: invalid sign-ups still count. Negative: the next one gets 429 with `Retry-After`, while another IP isn't affected. The full suite passed before merge.

## PR #399: Photo uploads limited to 10 a minute per user (#322 part 4)

**Requirement:** As part of #322 (which this PR closes), a student can upload at most 10 photos a minute. OCR can't be run in a loop. Therefore, I added a per-user limit on the photo route.

**Code quality:** No temporary workaround. Functions are 3 to 32 lines; the longest is the existing upload route.

**Teammate review:** Kaedyn and Fabiha found nothing blocking.

**Design:** In `app/api/rate_limit.py`, used by `POST /games/{id}/verification/photo`. It runs before process 3.1 Check idempotency (DFD Level 2, Photo Verification), so refused files count too.

**Tests:** 1 integration test. Negative: one user gets 429 with `Retry-After` while another can still upload. The full suite passed before merge.

## Test PR #383, written to assess A4 (#71): no student response gives a location away

**Generated code review:** See "One thing went wrong" above.

**Code quality:** One 246-line test file, with no changes to the app.

**Teammate review:** Fabiha pointed out that camelCase names such as `roomNumber` would slip through. She also asked to say it checks names, not values. Therefore, I split camelCase names and added test cases for them, and the docstring says it checks schema field names.

**Tests:** 6 tests in `tests/backend/test_student_responses.py`. Happy path: the game and student routes are scanned. Abnormal: fields inside nested objects, lists and references are found. Negative: every known bad name is caught, and safe names are not. The full suite passed before merge.

## PR #396: Real client IP behind nginx (#322)

**Requirement:** As part of #322, the per-IP limits must see the real client behind nginx. Therefore, I documented `FORWARDED_ALLOW_IPS` and the nginx headers. No code changed.

**Teammate review:** Kaedyn found that trusting the whole Docker network is unsafe while port 8000 is published. The host-nginx case was also unclear. I replied that the README was updated, but the push just before my reply was only a rebase. The fix never reached the branch, so #396 merged with the old advice. Therefore, I added it in #454 on the same day.

## PR #454: Reverse-proxy advice that #396 missed (#322)

**Requirement:** The README now says not to publish port 8000 behind nginx, and to trust only the one address nginx connects from. It also explains the case where nginx runs on the host. Kaedyn and Fabiha approved it.

## PR #451: README matches main and the new diagrams

**Requirement:** No issue. The README links the new DFD Level 1 overview and calls the component diagrams Level 2. It also fixes paths, commands and notes that no longer matched main. Fabiha and Kaedyn approved it.

## PR #375: Deploy the docs site to Netlify

**Requirement:** No issue. GitHub Pages can't deploy for our organization, so the site moved to Netlify.

**Teammate review:** Eddy found that the free plan allows about 20 production deploys a month. He also found that the token was visible to every step. Therefore, the deploy is skipped when the site hasn't changed and capped at 19 a cycle. Only the deploy steps get the token.

## PR #377: CI Postgres on a free host port

**Requirement:** No issue. We want more runners on the same VM, and two jobs both asked for host port 5432. Therefore, each job's Postgres gets a free port. Fabiha and Kaedyn approved.

## Other work this week

- **Spec and issues:** I updated the DFDs and the system architecture. There is a new Level 1 overview, and the component diagrams are now Level 2. The flows match the A23 routes and the new rate limiter. The details are in the week 11 team log, which I wrote (#453). I opened #378, #395, #400, #411 to #414, #433 to #438 and #450. #433 to #436 come from the Nov 19 client meeting.
- **Reviews:** I reviewed 44 PRs. For Fabiha, I asked for changes and then approved her A13, A14 and A16 stacks (Nov 17–18). I reviewed her A5, A6, A12, A16 and A17 follow-ups on Nov 21–22. For Eddy, I approved his A10 photo route and A20 content PRs (Nov 17–18). For Kaedyn, I asked for changes on #325 on Nov 17 and approved it on Nov 22. I also asked for changes on #410 and #439, and approved #440 (Nov 22).
- **Client demo:** I prepared and ran the Nov 19 client demo with Eddy on the real backend. On the `demo/client-demo` branch I built a small web app for students and teachers (17 commits; Eddy wrote the photo module). It is not merged, since M1 is backend only. It shows the class name, every badge with locked ones dimmed, and the analytics by room. I seeded a demo class and real campus locations, and wrote `seed_class.py` so Eddy could set up the same class. We played on our phones through a tunnel to my machine. The client asked for Basic games to stay in one building, which became #433 to #435.
- **Next week:** get my open PRs merged (#354, #400, #315, #436). Start Basic games from one building (#434). Update the diagrams for the clue plays.

# Week 9 Individual Logs
## Eddy Tian

(Nov. 2nd - Nov. 8th)

Tasks Completed:
This week I put the first HTTP routes of content management (requirement A20) on top of the content services from weeks 7 and 8. A content manager can now set a clue's text and upload clue audio over HTTP, and staff can read the coverage report:
- **Clue text route**, in #293: `PUT /api/v1/content/locations/{location_id}/clues/{difficulty}/{language}`, over `set_clue_text`.
- **Clue audio upload and coverage routes**, in #295: `POST .../clues/{difficulty}/{language}/audio` and `GET /api/v1/content/coverage`, over `add_clue_recording` and `coverage_report`.

Both PRs are merged into `main` (#293 as `179e4de`, #295 as `39b1f71`). They were stacked on the locations router (#292, merged earlier as `e69ca72`; it is not part of this log). #295 was also stacked on #293, with the coverage service branch (#285, week 8) merged in.

## For PR [#293](https://github.com/COSC-499-W2026/capstone-project-team-12/pull/293): content route for clue text

As part of requirement A20 (the specification's Content Management endpoints list "PUT /content/locations/{id}/clues/{difficulty}/{language}: Clue text (a blank text is stored as null)"), a content manager needs to choose a location, pick a difficulty (1-3) and a language, type the transcript of the clue's recording, and save it, or clear it again.

Therefore, I implemented `PUT /api/v1/content/locations/{location_id}/clues/{difficulty}/{language}` (`setClueText`, tag `content`) in `app/api/v1/content.py`, over `set_clue_text` from #223. It needs content rights (`can_manage_content`), returns 200 with the clue and commits once. `ClueText` (the body: `text` is required, and null or blank clears it) and `ClueOut` (`id`, `location_id`, `difficulty`, `language`, `text`) are in `app/content/schemas.py`, next to the location models. The route is for content managers only; `ClueOut` is staff-only like `LocationOut`, because students only ever hear clues as audio.

When I reviewed the route test for the number of commits, I noticed that the check `count("commit") == 2` only passed because `transaction_calls` counts a released savepoint as a commit, so it could not tell the route's own commit from a commit inside the service. Therefore, I made the route test spy on `db.commit()` itself (the `commits` fixture) and expect exactly one call. The existing service test already checks that the services never commit, so together they cover the rule.

The PR has three design points that I put in the PR text for reviewers to confirm, so they are decisions and not accidents:
- `text` is required: a PUT replaces the text, so clearing it is an explicit null rather than an empty body (a body without `text` is a 422).
- There is no maximum length: the specification sets none, only content managers can write, and an app-level request-size limit is the general fix.
- Students never see clue text (`ClueOut` is staff-only).

The PR does not contain a temporary workaround: the route is the real endpoint, over the real `set_clue_text`, and the next routes (#295) were built on it. The route body `put_clue_text` is 3 lines long (set the text, commit, return the clue), `ClueText` has 1 field and `ClueOut` has 5. I checked the PR for the listed problems:
- No hardcoded values: the difficulty and language in the path are the `Difficulty` and `Language` enums, so FastAPI rejects `4` or `xx` with a 422 and no range is written in the route.
- No duplicate code: the trimming of the text, the blank-to-null rule and the creation of the missing clue are in `set_clue_text`, and the access check is the shared `CONTENT_MANAGER` dependency, not copied.
- No dead code, unnecessary calls, nesting or branching: the route has no conditions, and it never flushes (the service does).

This work is written in the existing `app/api/v1/content.py` because it is the content router that #292 created, and `ClueText` and `ClueOut` are in the existing `app/content/schemas.py` because that is where the location models are. It belongs to process 5.2 Edit clue text in the content management DFD (flows 5 to 7: clue text in, the clue read, the saved clue out; D4 Content). The DFD is unchanged.

**Tests:**
- Happy path: `test_set_clue_text_creates_the_clue_and_blank_clears_it` sets text with spaces around it (stored trimmed, 200, the whole body checked, one commit), then sends blank text and checks that the stored text is null.
- Abnormal: `test_clue_text_must_be_sent` (a body without `text` is a 422 on `text`, because clearing is an explicit null), and the unknown location case, where the clue route is added to the 404 test.
- Negative: the clue route is added to the refused-user cases (students, TAs, admins and teachers without content rights all get 403, and `content_rows` checks that no location and no clue was created, where it used to count locations only), and `test_unknown_difficulty_or_language_is_422` for `4/fr` (422 on path field `difficulty`) and `2/xx` (422 on path field `language`).
- They are integration tests in `tests/backend/test_content_locations_api.py`, over HTTP with a real PostgreSQL database. The file went from 29 to 39 tests (new tests plus the clue route in the existing parametrised ones).
- The PR did not break anything else: on the merge commit `179e4de`, the full suite gives 1294 passed and 6 skipped, and `ruff check .`, `ruff format --check .`, `mypy app` and `dep_graph.py --check` pass. The PR changes 3 files, 124 lines added and 10 removed, under the 500-line PR limit.

## For PR [#295](https://github.com/COSC-499-W2026/capstone-project-team-12/pull/295): clue audio upload and coverage routes

As part of requirement A20 (spec 5.3 "Upload clue audio, optionally as the new primary" and 5.7 report coverage, `POST /content/locations/{id}/clues/{difficulty}/{language}/audio` and `GET /content/coverage`), a content manager needs to pick a clue, upload an MP3 or M4A recording of it (optionally as the new primary one), and then open the coverage view to see that the location is now playable in that language and difficulty. Any other staff account needs to open the same coverage view, but students (TAs included) and admins must not. This PR covers acceptance test cases 1 and 2 of A20 over HTTP: a valid MP3 makes the location playable, and audio over 5 MB, over 60 s or not audio at all is rejected and nothing is stored.

Therefore, I implemented two routes in `app/api/v1/content.py`:
- `POST /api/v1/content/locations/{location_id}/clues/{difficulty}/{language}/audio` (`uploadClueAudio`, 201): a multipart `file` and an optional form field `make_primary` (default false), over `add_clue_recording`. The clue's first recording is primary, a later one only with `make_primary`; the file is re-encoded without its tags and stored under a random name, and earlier recordings are kept. It needs content rights, and returns `ClueRecordingOut` (`id`, `clue_id`, `is_primary`, `duration_seconds`, `file_size_kb`; the storage key is left out because it is random and files are only served through signed media links). The route also takes `get_current_user`, because `can_manage_content` returns None and the recording must record who uploaded it.
- `GET /api/v1/content/coverage` (`getContentCoverage`): the `coverage_report` from the coverage service, for any staff account (the shared `STAFF` dependency).

Both have explicit operation IDs, descriptions and documented errors (404, 413, 415 and the access errors for the upload).

When I reviewed the code and the tests, I noticed that:
- The docstring, which becomes the OpenAPI description, left out one of the route's 422 outcomes: a recording under 5 ms raises `VALIDATION_ERROR` on `file`, but the docs listed it only for a missing file. Therefore, I edited the description so that `VALIDATION_ERROR` on `file` covers both a missing file and a recording under 5 ms.
- No API test showed that students, TAs or admins are refused the upload route: the only negative access test used a staff teacher without content rights, so a change to the route's dependencies that let non-staff users through would not have been caught. Therefore, I changed the 403 test into `test_only_content_managers_can_upload`, which tries a teacher without content rights, a student who is a TA and an admin, and checks that nothing is stored. The new `assistant` fixture is shared with the coverage 403 test.
- `test_a_later_recording_becomes_primary_only_when_asked` called `.json()` on each upload without checking its status, so a failed upload would show up as a confusing `KeyError` or a wrong-list assertion instead of the server's error. Therefore, I added a `created(response)` helper that asserts 201 and prints the server's error otherwise, and used it in the `make_primary` test and in the TC1 test.

The PR does not contain a temporary workaround: both routes are the real endpoints, over the real services. These points are not solved by this PR and are written in its description for the team: the specification gives no response body or preview URL for the upload (so it returns the stored recording); Starlette writes the whole multipart body to a temporary file before any auth check, so the 5 MB check does not protect the server from a huge request. PR #296 with app-level request-body middleware was opened to address this, and is expected to merge soon. `can_manage_content` returns None (the open question is whether it should return the `User`); a file stays in storage if the transaction fails after it was saved (the specification has no cleanup job); and the upload holds a database connection while the clip is re-encoded (about 0.4 s for 60 s of audio, measured), which is fine for single uploads. The bodies are `upload_clue_audio` (5 lines) and `get_coverage` (1 line), and `ClueRecordingOut` has 5 fields. I checked the PR for the listed problems:
- No hardcoded values: the limits are the existing settings `MAX_MEDIA_FILE_MB` and `MAX_AUDIO_SECONDS` used by `check_audio`, the path values are enums, and the access rules are the shared `CONTENT_MANAGER` and `STAFF` dependencies.
- No duplicate code: the file checks, the re-encoding, the storage and the choice of the primary recording are `add_clue_recording`, and the report is `coverage_report`; the routes only pass the file, the user and the database, commit once and return.
- No dead code or deep nesting: there is no branching in either route. The only extra dependency, `get_current_user`, is needed for `uploaded_by`.
- Before the PR was opened, I broke the router in 9 ways (no commit, `make_primary` ignored or defaulting to true, the uploader not recorded, the upload open to any staff or to everyone, the wrong status code, and coverage needing content rights or open to everyone) and a test failed each time.

This work is written in the existing `app/api/v1/content.py` because it is the content router, and `ClueRecordingOut` is in `app/content/schemas.py` with the other content models. The upload belongs to process 5.3 Upload clue audio in the content management DFD (flows 8 to 12: the file and primary flag in, the clue read, the audio asset record to D4 Content, the re-encoded file to D8 Media Storage, and the result or error out), and the coverage route belongs to process 5.7 Report coverage (flows 27 to 29). The DFD structure is unchanged. Flow 12 is labelled "Upload result or error (FILE_TOO_LARGE, AUDIO_TOO_LONG)", but the route can also return 415 `UNSUPPORTED_MEDIA_TYPE`, so that label could be extended.

**Tests:**
- Happy path: `test_uploaded_mp3_makes_the_location_playable_in_the_coverage_report` (acceptance test case 1 end to end over HTTP: upload, then `GET /coverage` shows the combination playable) and `test_a_later_recording_becomes_primary_only_when_asked` (the first recording is primary, a later one only with `make_primary`, and earlier files are kept in storage).
- Abnormal: `test_upload_to_an_unknown_location_is_404` and `test_upload_without_a_file_is_422_on_file`.
- Negative: `test_rejected_file_stores_nothing` (acceptance test case 2, three cases: audio over 60 s gives 422 `AUDIO_TOO_LONG`, over 5 MB gives 413 `FILE_TOO_LARGE`, a non-audio file gives 415 `UNSUPPORTED_MEDIA_TYPE`, and nothing is stored each time), `test_only_content_managers_can_upload` (teacher without content rights, student TA and admin all get 403), `test_tas_and_admins_cannot_read_coverage`, and `test_every_route_needs_a_token` (401 for both routes).
- The 11 tests in `tests/backend/test_content_uploads_api.py` are integration tests, because they go through HTTP with a real PostgreSQL database, real audio built with the `make_audio` fixture and a real temporary media folder (`stored_media_dir`).
- The PR did not break anything else: on the merge commit `39b1f71`, the full suite gives 1380 passed and 6 skipped, and `ruff check .`, `ruff format --check .`, `mypy app` and `dep_graph.py --check` pass. Running this PR's test file and the locations API test file with coverage (50 tests) shows `app/api/v1/content.py` at 100% (49 statements). The PR changes 3 files, 348 lines added and 4 removed (not counting the coverage service files from #285 that its branch merged in while #285 was still open).

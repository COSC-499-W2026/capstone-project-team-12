# Week 5 Individual Logs
## Eddy Tian

(Oct. 4th - Oct. 11th)

Tasks Completed:
This week I built two of the content interfaces that game (Mia) and accounts (Kaedyn) need by the Oct 12 interface deadline (Proposal Table 4):
- **Interface card #12** (content eligibility and snapshot), in #149.
- **Interface card #13** (media URLs), in #162 and #163, which build on each other.

The content tables are not built yet, so the interfaces are stubs with final signatures. This lets callers code against them now.

## For PR [#149](https://github.com/COSC-499-W2026/capstone-project-team-12/pull/149): content eligibility and snapshot interface stub

As part of requirement A18 (the student chooses a difficulty and plays "the matching clue recordings") and A20 (content managers manage clues for each language and difficulty), the student needs to start a game in a language and difficulty and get 5 locations that have content for that combination. When a location is drawn, its answers and entrance points must be fixed for the rest of the session.

Therefore, I implemented these in `app/content/interface.py` so that game can draw locations:
- `eligible_location_ids`: which locations can be drawn for a language and difficulty.
- `is_location_active`: whether a drawn location is still active.
- `location_snapshot`: the clue, recording, accepted answers, entrance points and radius that game copies into `session_locations`.

I also added the shared `Language` and `Difficulty` enums and a `gps_radius_meters` setting. The stub serves 30 fake locations.

When the code and tests were reviewed, it was noticed that:
- `LocationSnapshot`'s `repr` showed the accepted answers, so a log line or traceback could leak them. I hid those fields from `repr` and added a test.
- The 55 m stub spacing let two 30 m arrival circles overlap. I widened it to 67 m and added `test_stub_arrival_circles_never_overlap`.
- One test checked that every language had the same locations, which is a stub detail and not the agreed contract. I changed it to check, for each language and difficulty, that the result is repeatable.

The PR does not contain a temporary workaround because the stub follows the team rule to "code against an agreed interface or a stub". The signatures were agreed with Mia, and the real version only replaces the bodies with queries. The functions are 1 to 16 lines long. `location_snapshot` is the longest, and most of its lines build the returned snapshot.

The stub data is in named constants (`_STUB_LOCATION_COUNT`, `_STUB_ORIGIN`, `_STUB_SPACING_DEGREES`), and the radius is a setting. I ran ruff and mypy and read each function against the card. I can confirm the PR has no hardcoded values, duplicate code, dead code, unnecessary function calls, excessive conditional logic, deep nesting, high cyclomatic complexity, or modules with many unrelated responsibilities.

This work is written in `app/content/interface.py` because it is the only module of the content component that other components may import. It belongs to 2.1 Start session and 2.5 Control session (shuffle) in the DFD, which read D4 Content and copy the snapshot into D5 Game Sessions. The DFD is unchanged.

**Tests:**
- Happy path tests passed for repeatable eligible locations for each of the 18 language and difficulty pairs, eligible locations being active, the snapshot fields, and a positive radius setting.
- Abnormal tests passed for the snapshot's `repr` hiding answers, a different clue for each mode, and arrival circles that never overlap. Another passed test checks that lookups never commit, flush or roll back the caller's session, or change what is pending in it.
- Negative tests failed as expected for an unknown location (not active, and the snapshot is refused) and for a radius of 0 or -30.
- When I reviewed the read-only test, I noticed it failed on SQLAlchemy's automatic flush, which a real query does too. I turned autoflush off in the test and watched for explicit flushes instead.
- All 29 tests (26 in `tests/backend/test_content_eligibility.py`, 3 in `tests/backend/test_config.py`) are unit tests. Integration tests are not required because the stub uses no tables or routes.
- The PR did not break anything else: CI (ruff, mypy, pytest) passed before the merge.

## For PR [#162](https://github.com/COSC-499-W2026/capstone-project-team-12/pull/162): StorageService for signed, expiring media URLs

As part of requirement A20 (uploaded audio "is stored under a random name ... so a file name can never reveal an answer"), the student needs to receive a link to the clue audio and play it. The specification also says media is "served only through short-lived signed URLs (15 minutes)", so an edited or expired link must be refused.

Therefore, I implemented `StorageService` in `app/services/storage.py` so that game, content and accounts can:
- `signed_url`: sign a link with HMAC-SHA256 and an expiry time.
- `verify`: raise a 403 `MediaUrlError` for a tampered or expired link.
- `path`: find a file, refusing keys that lead outside the media directory.
- `delete`: remove a file, where a file that is already gone is not an error.

The 15-minute lifetime is a new setting, `media_url_ttl_seconds`.

When the first version was reviewed, it was noticed that:
- Keys containing `?`, `#` or a space broke the URL. I percent-encoded the key.
- The path lookup was private, so the media route would have had to repeat the safety check. I made `path()` public.
- `MediaUrlError` was a plain exception, so an uncaught one would have become a 500. I made it an `AppError` with a new 403 `FORBIDDEN` code.
- The new code was not in the 403 row of the status table in `docs/api-conventions.md`, so a client developer could not find it. I added it there.

The PR does not contain a temporary workaround because it is the specification's long-term design. Moving to S3 later only changes this class. The functions are 1 to 5 lines long. The lifetime is a setting, the URL prefix and signing label are constants, and the URL parsing used in tests is one shared `split_media_url` fixture. When `_path_for` became `path()`, I removed the old method. Ruff and mypy pass. I can confirm the PR has none of the listed problems.

This work is written in `app/services/storage.py` because the specification places StorageService there as a service shared by several components. It belongs to the data flows out of D8 Media Storage in the DFD. It serves audio to 2.2 Serve current location and 2.3 Process GPS ping, and it deletes files for 1.9 Anonymise account and 3.7 Purge old photos. The DFD is unchanged.

**Tests:**
- Happy path tests passed for a link that verifies and gives back its key, a link that still works 1 second before expiry, `path` and `delete`, the default 15-minute lifetime, and the cached service.
- Abnormal tests passed for keys with `?`, `#`, a space, `é` and `%`, deleting a missing file, and a key that names a directory.
- Negative tests failed as expected for a changed key, a changed expiry, a wrong signature, a non-ASCII signature, another secret, a signature made for another purpose, a link at exactly its expiry time, a lifetime of 0 or -900, and keys such as `../outside.jpg` (the file outside is not deleted).
- All 28 tests in `tests/backend/test_storage_service.py` are unit tests. Integration tests are not required yet because there is no media route, and the tests use a temporary directory and a fixed clock. The route will get integration tests in its own PR.
- The PR did not break anything else: the full suite passes on the branch after rebasing onto the latest main (see the result under #163, which includes this PR), and ruff, mypy and `dep_graph.py --check` pass. The PR changes 7 files and about 240 lines.

---

## For PR [#163](https://github.com/COSC-499-W2026/capstone-project-team-12/pull/163): AudioService stub for clue and feedback audio URLs

As part of requirement A20 (clue and feedback audio for each language) and Interface card #13, the student needs to hear the clue for the current location, and hear spoken feedback (for example `NAV_GETTING_CLOSER`) in the target language after each GPS ping.

Therefore, I implemented `app/services/audio.py` so that game can:
- `clue_audio_asset_id`: choose a clue's recording when a location is drawn.
- `clue_audio_url`: get a signed URL for that recording.
- `feedback_audio_url`: get a signed URL for any of the 13 feedback codes in 6 languages, which is 78 clips.

These functions raise a 404 `MediaNotFoundError` when no recording exists. `location_snapshot` now gets its recording from AudioService, and it refuses the location if the recording has been deleted.

When I reviewed `audio.py`, I did not find any problems because I checked each of these with a test:
- Stub IDs and keys are HMAC digests, so they cannot reveal a clue or phrase.
- The stub keeps no state, so it survives a restart.
- An ID the stub never handed out is refused with 404.

The PR does not contain a temporary workaround in the sense of the template. It is a stub only because the audio tables do not exist yet. The signatures were agreed with Mia, and the real version only replaces the bodies. The functions are 1 to 4 lines long.

I moved the `transaction_calls` fixture from `test_content_eligibility.py` into `conftest.py`, so the content and audio tests share one copy. I put the feedback codes in a shared `FeedbackCode` enum, and I removed the "planned card #13" to-do from the content docstring. Signing stays in StorageService. Ruff and mypy pass. I can confirm the PR has none of the listed problems.

This work is written in `app/services/audio.py` because the specification places AudioService there. It belongs to 2.1 Start session (the recording is chosen), 2.2 Serve current location and 2.3 Process GPS ping in the DFD, which read D4 Content and D8 Media Storage. The DFD is unchanged.

**Tests:**
- Happy path tests passed for every one of the 78 phrases getting its own verifiable, random-looking URL, each clue keeping its own recording, the same recording always having the same key, and the snapshot's recording being playable.
- Abnormal tests passed for a location whose recording disappears after it was listed as eligible, and for lookups that never commit, flush or roll back the caller's session.
- The negative test failed as expected for an unknown recording ID (404).
- The 6 tests in `tests/backend/test_audio_service.py` are unit tests. The 2 new snapshot tests in `tests/backend/test_content_eligibility.py` are integration tests between content and AudioService.
- The PR did not break anything else: on this branch (which includes #162 and the latest main), the full suite gave 268 passed and 5 skipped (the OCR smoke tests, which only run in the API image), including all 28 content eligibility tests. Ruff, mypy and `dep_graph.py --check` also pass. Counted on top of #162, the PR changes 6 files and about 285 lines.

Otherwise, Mia can now draw locations and fetch clue and feedback audio, and Kaedyn can delete media for privacy requests. The next step is the `/api/v1/media` route, which will call `verify` and then send the file at `path`.

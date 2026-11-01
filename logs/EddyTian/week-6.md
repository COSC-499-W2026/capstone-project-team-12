# Week 6 Individual Logs
## Eddy Tian

(Oct. 12th - Oct. 18th)

Tasks Completed:
This week I started replacing the content stubs from week 5 with real tables, and tidied the interfaces other components call:
- **Interface convention**, in #178: every interface takes the database session last.
- **Content schema** for A20, in #189 (`locations`, `clues`) and #208 (`audio_assets`, `feedback_audio`).
- **AudioService factory** from the specification, in #200, which also makes sure a language is only playable when all its feedback phrases exist.

## For PR [#178](https://github.com/COSC-499-W2026/capstone-project-team-12/pull/178): put `db` after the required arguments in every interface

As part of the team's interface conventions (`docs/api-conventions.md`), a developer calling another component needs to call every interface function in the same way. The content interface was the only one that took `db` first (`location_snapshot(db, location_id, ...)`), while accounts, engagement and game took it last (`is_class_staff(user_id, class_id, db)`).

Therefore, I changed `eligible_location_ids`, `is_location_active` and `location_snapshot` to take `db` after their required arguments, updated their tests, and wrote the rule into `docs/api-conventions.md` so that new functions follow it. The rule covers interface functions, shared services, and the other functions inside a component.

When the PR was reviewed, it was noticed that:
- The rule only named interface functions, so functions inside a component, such as `register_account(request, db)`, were not covered. I extended it to every function in a component.
- The example in the docs used the old `require_class_role(user, class_id, required_role, db)` signature, which no longer exists after Kaedyn's role-based access PRs. I replaced it with `is_class_staff(user_id, class_id, db)`, which does.

A rebase onto main also went wrong while resolving conflicts, and I fixed it in a separate commit before the merge.

The PR does not contain a temporary workaround because it only reorders arguments and documents the rule. It adds no new functions. Ruff, mypy and `dep_graph.py --check` pass, and the PR has none of the listed problems (hardcoded values, duplicate code, dead code, unnecessary function calls, excessive conditional logic, deep nesting, high cyclomatic complexity, or modules with many unrelated responsibilities).

This work is written in `app/content/interface.py` and `docs/api-conventions.md` because the interface is the only module other components may import, and the conventions document is where the team's API rules live. It belongs to the same processes as the content interface (2.1 Start session and 2.5 Control session in the DFD), which it does not change.

**Tests:**
- The 28 existing content eligibility tests were updated to the new argument order and pass. No new tests were needed, since the behaviour did not change.
- The PR did not break anything else: the full suite, ruff, mypy and `dep_graph.py --check` passed before the merge. The PR changes 3 files and about 60 lines.

## For PR [#189](https://github.com/COSC-499-W2026/capstone-project-team-12/pull/189): `locations` and `clues` schema

As part of requirement A20 (content managers "manage locations, clue text and clue audio for each language and difficulty"), a content manager needs to add a location with its room number, accepted answers and building entrances, and then add a clue for each difficulty and language. The answers are later matched against OCR text (A10), and the entrances are where arrival is checked (A19).

Therefore, I implemented the `Location` and `Clue` models in `app/models/content.py` and their migration. The database itself refuses content that could never be played:
- a room number that is not in the sign format (`SCI 333`, or 4 digits for the one building that uses them), or one already used by another location,
- an empty list of answers or entrances, an answer that is not normalised like OCR text (`SCI333`), or an entrance outside the valid latitude and longitude,
- a blank name, a radius of 0 or less, or a second clue for the same location, difficulty and language.

I also moved `non_empty_array` out of `models/game.py` and added `difficulty_check`, both into the shared `models/columns.py`, so game and content build the same constraints.

When the code was reviewed, it was noticed that:
- The first version only checked that the answers and entrances were non-empty arrays, so an answer like `"SCI 333"` (which OCR text could never match) or an entrance with no `lat` was accepted. I added two constraints that check every item with PostgreSQL JSON path queries.
- It was not clear why the room number is unique even for inactive locations. I explained in a comment that bulk audio files name their location by room number, so a deactivated room is reactivated rather than created again.
- Two test cases were lost while rewriting the tests (the 4-digit room number and difficulty 0). I added them back.

The PR does not contain a temporary workaround because it is the specification's schema. The content interface still serves stub locations until the content service reads these tables, as planned in its docstring. The new helpers are 1 to 2 lines long. The room number and answer patterns are named constants, the difficulty values come from the `Difficulty` enum, and the shared constraint helpers remove a copy from `models/game.py`. Ruff and mypy pass, and the PR has none of the listed problems.

This work is written in `app/models/content.py` because each component's tables live in `app/models/<component>.py`, with a migration in `alembic/versions/`. It belongs to the D4 Content data store in the DFD, written by 5.1 Manage locations and 5.2 Edit clue text and read by 2.1 Start session. The DFD is unchanged. The ER diagram should mark `locations.room_number` as UK.

**Tests:**
- Happy path tests passed for server defaults when rows are inserted with raw SQL, a location at the format and range limits (several answers, the 4-digit form, the latitude and longitude limits), editing answers or entrances in place being saved, and a clue being added to an existing location.
- Abnormal tests passed for empty arrays, objects, strings and nulls in place of the answer and entrance lists.
- Negative tests failed as expected for a duplicate room number, room numbers not in the sign format, answers that are not normalised, entrances out of range or missing `lat`/`lng`, blank names, a radius of 0, an unknown verification type or a missing room number, a second clue for the same location, difficulty and language, and a clue with a difficulty of 0 or 4, an unknown language or an unknown location.
- All 42 tests in `tests/backend/test_content_schema.py` are integration tests, because they check the constraints in a real PostgreSQL database. Unit tests are not needed because the models contain no logic outside the database. The new `make_location` and `make_clue` fixtures are in `tests/backend/conftest.py`.
- The PR did not break anything else: the full suite on main gives 589 passed and 6 skipped, ruff, mypy and `dep_graph.py --check` pass, and CI's `alembic check` passed before the merge. The PR changes 8 files and about 490 lines.

## For PR [#200](https://github.com/COSC-499-W2026/capstone-project-team-12/pull/200): AudioService factory, and every feedback phrase required

As part of the specification's AudioService design ("the active implementation is chosen by the AUDIO_SERVICE_TYPE setting and created by an AudioServiceFactory at startup") and requirement A20 (feedback audio for each language), a student starting a game needs every spoken feedback phrase to exist in the game's language, or a feedback link could fail partway through the game.

Therefore, I implemented these in `app/services/audio.py`:
- an `AudioService` protocol that every implementation provides,
- `UploadedAudioService`, the only implementation this project builds (the week 5 stub, now behind the protocol),
- `get_audio_service()`, the factory, which builds the implementation named by the new `audio_service_type` setting once.

An unknown value such as `hybrid` fails settings validation, so the app does not start. I also added `has_all_feedback_phrases`. `eligible_location_ids` now returns no locations for a language that is missing a phrase, so a game can only start in a language that has them all. I updated the callers in `content/interface.py` and Mia's `game/start.py` to use the factory.

When the code was reviewed, it was noticed that:
- `UploadedAudioService` subclassed the protocol, so a method it forgot to implement would silently return `None` from the protocol's empty body. I stopped subclassing it, and mypy now checks that it matches the protocol through the factory's table of implementations.
- A new `AudioServiceType` value without an implementation would only fail when the factory ran. I added a test that every type has an implementation.
- The docstring of `eligible_location_ids` said it returns `None` for a missing phrase, while it returns an empty set. I corrected it.

Mia then reviewed it and noticed that:
- `location_snapshot` checked the phrases again for every drawn location, although game only draws from `eligible_location_ids`, which already checks them once per draw. I removed the second check and its test.
- I had added a factory call to `create_app` so that a bad setting stopped the app, but settings validation already does this when the app is imported. I removed the call and its test, and explained it in the docstring.

The PR does not contain a temporary workaround because the factory and protocol are the specification's design. `UploadedAudioService` is still a stub until the audio tables from #208 are read, and its docstring says how the real version will work. The methods are 1 to 4 lines long. The setting is a typed `Literal`, so an invalid value cannot be used, and `MediaNotFoundError` now comes from one class in StorageService instead of two copies. Ruff, mypy and `dep_graph.py --check` pass, and the PR has none of the listed problems.

This work is written in `app/services/audio.py` because the specification places AudioService in the shared services, and the setting is in `app/config.py` with the other settings. It belongs to 2.1 Start session (eligible locations), 2.2 Serve current location and 2.3 Process GPS ping (clue and feedback audio) in the DFD. The DFD and the architecture diagram, which already shows AudioService as pluggable, are unchanged.

**Tests:**
- Happy path tests passed for `uploaded` being the default, the factory being cached, every type having an implementation, the stub having all 13 phrases in each of the 6 languages, and the existing clue and feedback URL tests through the factory.
- Abnormal tests passed for a language missing a phrase having no eligible locations at each difficulty, while other languages still have all 30.
- Negative tests failed as expected for the setting values `hybrid`, `ai_generated` and an empty string.
- The 18 tests in `tests/backend/test_audio_service.py` (12 new) are unit tests. The 3 new tests in `tests/backend/test_content_eligibility.py` are integration tests between content and AudioService, using small test implementations (`MissingAPhraseIn`, `WithoutRecordings`) instead of patching single functions.
- The PR did not break anything else: on this branch, the full suite gives 599 passed and 6 skipped, including Mia's `test_start_game.py`, which now uses the factory. Ruff, mypy and `dep_graph.py --check` pass. The PR changes 8 files and about 295 lines.

## For PR [#208](https://github.com/COSC-499-W2026/capstone-project-team-12/pull/208): `audio_assets` and `feedback_audio` schema

As part of requirement A20 (content managers upload clue audio and import feedback audio, "stored under a random name ... so a file name can never reveal an answer"), a content manager needs to upload recordings for a clue and choose the primary one, and import the feedback phrase library for each language.

Therefore, I implemented the `AudioAsset` and `FeedbackAudio` models in `app/models/content.py` and their migration:
- `audio_assets`: a clue's recordings, with at most one primary recording per clue (a partial unique index), a unique storage key, a source type (`uploaded`, or `ai_generated` for Future Work), and a positive duration and size.
- `feedback_audio`: one recording per feedback code and language, with only known codes and languages and a unique storage key.

Replacing a recording only moves `is_primary` and never deletes the old one, so a game in progress keeps the clue its answers belong to.

When the code was reviewed, it was noticed that:
- It was not explained how to move `is_primary`. The one-primary index cannot be deferred, so clearing it on the old recording and setting it on the new one in the same flush works or fails depending on the order SQLAlchemy writes the rows in. I documented that the service must flush between the two steps, and tested that the replacement works this way.
- It was not clear why `uploaded_by` can be null. I explained that it is null for generated audio and allowed for uploads with no user, such as seeded demo data.

The PR does not contain a temporary workaround because it is the specification's schema, and AudioService will read it in a later PR. The models contain no functions, and the new fixtures are 5 to 6 lines long. The allowed source types and feedback codes come from the `AudioSourceType` and `FeedbackCode` enums, so adding a value needs no new constraint by hand. Ruff and mypy pass, and the PR has none of the listed problems.

This work is written in `app/models/content.py` with the other content tables. It belongs to the D4 Content data store in the DFD, written by 5.3 Upload clue audio and 5.4 Bulk import and read by 2.1 Start session and 5.7 Report coverage. The files themselves stay in D8 Media Storage. The DFD is unchanged. The ER diagram should mark `file_path` as UK in `audio_assets` and `feedback_audio`.

**Tests:**
- Happy path tests passed for server defaults when rows are inserted with raw SQL, an upload recording who uploaded it, each clue having its own primary recording, the primary recording being replaced, and a phrase being recorded in every language.
- Abnormal tests passed for older, non-primary recordings being kept alongside a clue's primary one.
- Negative tests failed as expected for a second primary recording for the same clue, a reused storage key (for recordings and for phrases), a second recording of a phrase in the same language, a duration or size of 0, an unknown source type, clue or uploader, and an unknown phrase code or language.
- The 16 new tests in `tests/backend/test_content_schema.py` (58 in all) are integration tests, because they check the constraints in a real PostgreSQL database. The new `make_audio_asset` and `make_feedback_audio` fixtures are in `tests/backend/conftest.py`.
- The PR did not break anything else: on this branch, the full suite gives 600 passed and 6 skipped, and ruff, mypy and `dep_graph.py --check` pass. The PR changes 5 files and about 340 lines.

Otherwise, the next step is the real content service. It will read these tables instead of the stub locations, and `UploadedAudioService` will read `audio_assets` and `feedback_audio`, without changing either interface's signatures.

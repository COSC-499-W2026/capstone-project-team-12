# Week 7 Individual Logs
## Eddy Tian

(Oct. 19th - Oct. 25th)

Tasks Completed:
This week I wrote the first three services of content management (requirement A20) as small, separate PRs. They sit on the tables from week 6, and the content router that will call them comes next:
- **Audio checks**, in #219: a check that an uploaded recording is a real MP3 or M4A within the size and length limits, which re-encodes it so that no tag can reveal an answer.
- **Location and clue-text services**, in #223: create, replace, list and deactivate locations, and set the clue text for a difficulty and language.
- **Clue audio service**, in #226: store a clue recording and choose which recording is the primary one.

All three PRs are merged into `main`.

## For PR [#219](https://github.com/COSC-499-W2026/capstone-project-team-12/pull/219): check uploaded audio and strip its metadata

As part of requirement A20 ("The system shall let content managers manage locations, clue text and clue audio for each language and difficulty ... Uploaded audio is stored under a random name and its metadata tags are removed, so a file name can never reveal an answer"), a content manager needs to upload a recording of a clue. The system must refuse anything that is not an MP3 or M4A recording, a file over 5 MB, or a recording over 60 seconds, and must remove any tag such as an ID3 title that says "SCI333".

Therefore, I implemented `check_audio` in `app/content/audio_files.py`. It reads at most 5 MB (plus one byte, to know the file is too big), recognises MP3 and M4A by their first bytes instead of their name, and then decodes the audio and encodes it again into a new file, so that only the sound survives. It returns the new bytes, the file suffix, the length to the nearest 10 ms and the size in KB, ready for StorageService. The limits are two new settings, `max_media_file_mb` and `max_audio_seconds`, from the specification. The PyAV library (`av`, with FFmpeg included) was added to `pyproject.toml` and `requirements.txt`.

When the code was reviewed, it was noticed that:
- The first version copied the audio packets into a new file without re-encoding. FFmpeg's MP3 reader counts bytes appended after the last frame (ID3v1 and Lyrics3 tags, or plain text such as "SCI333") as part of that frame, so they were copied too. I changed the check to decode and re-encode the audio (an MP3 stays MP3, an M4A is always stored as AAC, because Chrome and Firefox cannot play ALAC), and added tests that appended tags and text are gone. The re-encoding also meant the length is now counted from the decoded samples, and I allow 50 ms of encoder padding so that a recording of exactly 60 seconds is still accepted.
- Tags in an unknown text encoding made FFmpeg fail, although the tags are thrown away anyway. They are now ignored when reading, and a test covers it.
- Any audio in an `.mp3` or `.mp4` file was accepted, including MPEG layer I and II, FLAC or Opus inside MP4, and `.mov` or `.3gp` files, which browsers may not play. Now an MP3 must hold layer III audio and an M4A must have an M4A brand and hold AAC or ALAC. The function also takes the uploaded file object instead of all its bytes, so the read is bounded.
- The documentation said only an MP3's cover picture was dropped, but an M4A's cover picture is dropped too. I corrected the docstring and added a test for each format.

The PR does not contain a temporary workaround: the check is the real one the router and the bulk import will call (the router is a later PR). The functions are `check_audio` (11 lines), `_detect_format` (8), `_reencode` (14), `_only_audio_stream` (9) and `_transcode` (40 lines, with its docstring). I checked the PR for the listed problems:
- No hardcoded values: the limits are settings, and the sample rate, bit rate, channel layout, padding and M4A brands are named constants with a comment.
- No duplicate code: the MP3 and M4A differences are one `_AudioFormat` record each, so there is one code path for both.
- No dead code, deep nesting or unrelated responsibilities: the module only checks and re-encodes audio, and storing it is left to `clue_audio.py`.

This work is written in `app/content/audio_files.py` because audio checks belong to content management, and the settings are in `app/config.py` with the other settings. It belongs to process 5.3 Upload clue audio in the content management DFD (flow 11 "Audio file (random name, tags stripped)"), and the bulk import 5.4 will use the same function. The DFD structure is unchanged. Flow 12 lists only FILE_TOO_LARGE and AUDIO_TOO_LONG, while the check can also return UNSUPPORTED_MEDIA_TYPE, so that label could be extended.

**Tests:**
- Happy path tests passed for a valid MP3 and M4A being kept with the right length and no tags, an MP3 having no ID3 tag, tags appended after the audio being dropped, an ALAC M4A being stored as AAC, a recording of exactly the time limit being accepted, tags in an unknown encoding being dropped, and MP3 and M4A files with a cover picture being accepted without it.
- Abnormal tests passed for raw AAC that starts like an MP3 frame, an M4A whose audio does not decode, and an MP4 with only a cover picture, which are all rejected.
- Negative tests failed as expected for MP2, FLAC and Opus audio, an audio-only `.mov`, a recording over the time limit, a file over the size limit (without reading all of it), files that are not audio (a PNG, text named `.mp3`, headers with no audio, an empty file), and an MP4 video with or without sound.
- The 29 tests in `tests/backend/test_audio_files.py` are unit tests, because they check the function on bytes without a database. The `make_audio` fixture builds the test recordings with PyAV and is in `tests/backend/conftest.py`. Four tests were also added to `tests/backend/test_config.py` for the new settings (defaults of 5 and 60, changing them, and refusing 0 or less).
- The PR did not break anything else: on the merge commit, the full suite gives 849 passed and 6 skipped, ruff check, ruff format, `mypy` and `dep_graph.py --check` pass. The PR changes 8 files and about 480 lines.

## For PR [#223](https://github.com/COSC-499-W2026/capstone-project-team-12/pull/223): location and clue-text services

As part of requirement A20 (content managers "manage locations, clue text and clue audio for each language and difficulty"), a content manager needs to add a location by typing its room number as it is on the sign (`sci-333`), its entrances and radius, to change or deactivate it later, and to write the clue text for a difficulty and language.

Therefore, I implemented these in `app/content/locations.py` and `app/content/schemas.py`:
- `list_locations`, `create_location`, `replace_location` and `deactivate_location`. Deactivating is a soft delete, and a replace with `is_active` true reactivates, because the room number stays unique across inactive locations.
- `set_clue_text`, which creates the clue the first time a location gets text for that difficulty and language, and stores blank text as null.
- `LocationFields` and `LocationOut`, which accept `sci 333`, `SCI333` and `SCI-333`, store the sign form `SCI 333`, and normalise the accepted answers the way OCR text is normalised. They follow the checks of the `locations` table, so a location that passes is never refused by the database.

When the code was reviewed, it was noticed that:
- An accepted-answers list given by the content manager replaced the room number's own answer, so a typo in it could make the location impossible to complete. The room number's answer is now always first, and the list is for extra answers only.
- Text with a NUL character, or a floor number, radius or other integer too large for PostgreSQL, passed validation and failed on save with a 500. Both are now refused in the schema, and the clue text is refused with a VALIDATION_ERROR.
- A duplicate room number rolled the whole session back, so work done earlier in the same request was lost. The save now runs in a savepoint, which undoes only the failed save, and a test checks that earlier work is kept.
- The duplicate room number error had a `details` entry different from the request validation errors. It now has the same `in`, `field` and `message` entries, through one `_invalid_field` helper.

The PR does not contain a temporary workaround. The services are the real ones, and they flush but never commit, because the router will own the transaction (the router is a later PR). The functions are 3 to 15 lines long (`set_clue_text` 12, `get_or_create_clue` 15, `_saving_room_number` 15). I checked the PR for the listed problems:
- No hardcoded values: the default radius comes from the `gps_radius_meters` setting, and the integer limit and the NUL pattern are named constants.
- No duplicate code: one `_saving_room_number` is used by create and replace, and one `_invalid_field` builds every error detail.
- No dead code or deep nesting.

This work is written in `app/content/` because location and clue management is the content component, and the services are separate from `interface.py`, which is the only module other components may import. It belongs to processes 5.1 Manage locations and 5.2 Edit clue text and the D4 Content data store in the content management DFD (flows 1 to 7, with "Saved location or validation error" and "Clue"). The DFD is unchanged.

**Tests:**
- Happy path tests passed for the room number being stored as on the sign (five typed forms), answers being normalised and including the room's own, a location being created with every field, locations being listed with inactive ones by room number, a replace changing every field and reactivating, a deactivated location keeping its row, and clue text being created and then updated.
- Abnormal tests passed for a duplicate room number keeping the work done before it, other constraint errors not being reported as a duplicate, blank clue text (`""`, spaces, `None`) being stored as null, and the services flushing without committing.
- Negative tests failed as expected for bad room numbers, bad answers, bad entrance points, blank names and names with a NUL, numbers the database cannot hold, reusing the room number of an inactive location, clue text with a NUL, and an unknown location ID for replace, deactivate and set clue text (NOT_FOUND).
- The 48 tests in `tests/backend/test_content_locations.py` are unit tests for the schema (no database) and integration tests for the services (they use the `db` fixture, a real PostgreSQL database).
- The PR did not break anything else: on the merge commit, the full suite gives 897 passed and 6 skipped, and ruff check, ruff format, `mypy` and `dep_graph.py --check` pass. The PR changes 4 files and about 495 lines.

## For PR [#226](https://github.com/COSC-499-W2026/capstone-project-team-12/pull/226): clue audio service

As part of requirement A20 (content managers manage "clue audio for each language and difficulty"), a content manager needs to upload a recording for a clue and either let it become the clue's primary recording (the first one always is) or choose it as the new primary one. A game in progress keeps playing the recording it drew.

Therefore, I implemented `add_clue_recording` in `app/content/clue_audio.py`. It checks the location, runs the audio check from #219, creates the clue if needed, stores the file under a random name with StorageService, and adds the `audio_assets` row. The upload becomes primary if the clue has no primary recording yet or `make_primary` is set. Old recordings are never deleted, only `is_primary` moves, so open games keep their recording. This PR is built on #219 and #223.

When the code was reviewed, it was noticed that:
- Two uploads to the same clue at the same time could both find no primary recording and both take it. I replaced the lock taken after the clue was created with a `lock` option on `get_or_create_clue`, which reads the clue with `FOR NO KEY UPDATE`, and added a test that the clue is locked before the primary is chosen.
- If the row could not be saved, the stored file was left behind with nothing pointing to it. The file is now deleted when the flush fails (with a test), and the docstring says that a transaction rolled back later by the caller still leaves a file, since the specification has no cleanup job yet.
- A recording under 5 ms is real audio, but its length rounds to 0.00 seconds, which `audio_assets` refuses, and it was first reported as UNSUPPORTED_MEDIA_TYPE. It is now a VALIDATION_ERROR on the `file` field, and a test checks that 4 ms is refused and 6 ms is stored as 0.01 s.
- The provider name `"teacher"` was written both in the service and in the test fixture. The fixture now uses the service's `UPLOAD_PROVIDER` constant, and `_get_location` became `get_location` because this module needs it too.

The PR does not contain a temporary workaround. The router and the bulk import that will call the service are later PRs. `add_clue_recording` is 44 lines with its docstring. I checked the PR for the listed problems:
- No hardcoded values or duplicate code: see the provider constant above.
- No dead code or deep nesting: the only branch is the choice of the primary recording.
- The function does one thing (store one recording), and the checks and the lookups are in the functions it calls.

This work is written in `app/content/clue_audio.py` because it is process 5.3 Upload clue audio of the content component. It reads and writes the D4 Content data store (`audio_assets`) and writes the file to D8 Media Storage (flows 8 to 12 of the content management DFD). The DFD and the architecture diagram, which already shows StorageService, are unchanged. The ER diagram already marks "one primary per clue" on `audio_assets`.

**Tests:**
- Happy path tests passed for the first recording being the primary one, a later recording not being primary unless asked, a new primary replacing the old one which keeps its row, the stored file having a random name and no tags, and the upload flushing but never committing.
- Abnormal tests passed for the stored file being deleted when its row cannot be saved, and the clue being locked before the primary is chosen.
- Negative tests failed as expected for an unknown location storing nothing, and rejected audio storing nothing and creating no clue. The too-short recording test was added to `tests/backend/test_audio_files.py` (30 tests in all).
- The 9 tests in `tests/backend/test_clue_audio.py` are integration tests, because they use a real PostgreSQL database and a real temporary media folder (the new `stored_media_dir` fixture in `tests/backend/conftest.py`).
- The PR did not break anything else: on the merge commit, the full suite gives 907 passed and 6 skipped, and ruff check, ruff format, `mypy` and `dep_graph.py --check` pass. The PR changes 7 files and about 320 lines.

Otherwise, the next step is the content router and the bulk imports, which will call these three services without changing their signatures.

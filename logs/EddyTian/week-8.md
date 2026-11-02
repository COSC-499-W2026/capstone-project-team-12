# Week 8 Individual Logs
## Eddy Tian

(Oct. 26th - Nov. 1st)

Tasks Completed:
This week I wrote the coverage report and the three bulk imports of content management (requirement A20) as small services. They sit on the services from week 7, and the content router that will call them comes next:
- **Coverage report**, in #285: which location, language and difficulty combinations are playable, and what each language still lacks.
- **Locations CSV import**, in #287: import locations from a CSV with a row-by-row report and a dry run.
- **Limits for the CSV import**, in #288: a size limit and a row limit, and friendlier reasons for rejected rows.
- **Clue audio zip import**, in #290: import clue recordings from a zip, each named after its clue.
- **Feedback audio zip import**, in #291: import feedback phrases from a zip, and make the zip reading safe against damaged, encrypted and oversized zips.

All five PRs are merged into `main`.

## For PR [#285](https://github.com/COSC-499-W2026/capstone-project-team-12/pull/285): content coverage report service

As part of requirement A20 (the content portal has "a coverage matrix showing which combinations are playable", from `GET /content/coverage`), a content manager, or another staff member who may view content, needs to open the coverage view and see which locations can be played in each language and difficulty, and why a language is not playable yet.

Therefore, I implemented `coverage_report` in `app/content/coverage.py`, with the report models (`CoverageReport`, `LanguageCoverage`, `DifficultyCoverage`, `LocationCoverage` and `CombinationCoverage`) in `app/content/schemas.py`. It lists every location, inactive ones too, with all combinations of language and difficulty. For each one it says whether there is clue text, whether there is a primary recording and whether new games can draw it. It also lists, for each language, the feedback phrases that are missing and how many locations are playable at each difficulty. A combination is playable when the location is active, has a room number and a primary recording, and the language has all its feedback phrases, because a game plays all of them.

When the code was reviewed, it was noticed that:
- The module did not say how locations are ordered. Room numbers sort as text (`SCI 1000` before `SCI 333`), as in `list_locations`, and the spec has no order, so I added a comment that says so, and why name and id are in the `ORDER BY` (a fixed order for locations without a room number).
- The docstring said that a phrase import committed during the report would only make the report show the newer phrase list. The phrases are read first, so a later import is not in the report (READ COMMITTED) and the report shows the list as it was when the read began. I corrected the docstring.
- The `statements` fixture, which records the SQL a test sends, was in the shared `conftest.py`. I moved it out of `conftest.py` into the test files that use it, so the coverage tests keep their own copy.

The PR does not contain a temporary workaround: it is the real report. The one thing that is not yet connected is `eligible_location_ids` in the content interface, which is still a stub. `PRIMARY_RECORDING` and `DRAWABLE` are named so that the real function can reuse them and the report and the draw cannot disagree. The functions are `coverage_report` (25 lines with its docstring), `_missing_feedback_codes` (7), `_locations` (31) and `_location_coverage` (26). I checked the PR for the listed problems:
- No hardcoded values: languages, difficulties and feedback codes come from the enums, and the report does not repeat the draw conditions in each query but uses the two constants.
- No duplicate code and no unnecessary calls: the report is two queries however many locations there are (a test counts the statements), instead of one query per location.
- No dead code or deep nesting. The module only reads and builds the report; it never flushes or commits.

This work is written in the new `app/content/coverage.py` because the coverage report is its own read-only service of the content component, separate from the location services. It belongs to process 5.7 Report coverage in the content management DFD (flows 27 to 29, reading the D4 Content data store). The DFD structure is unchanged. Flow 28 is labelled "Locations, clues, primary audio" but the report also reads the feedback phrases, so that label could be extended.

**Tests:**
- Happy path tests passed for an uploaded MP3 making the location playable in that language and difficulty, locations being listed by room number, and each language counting its playable locations per difficulty.
- Abnormal tests passed for a location without clues listing every combination as not playable, a location without a room number, the report taking two queries however many locations there are, and the report only reading.
- Negative tests failed as expected (nothing playable) for a language with a missing feedback phrase, an inactive location, a recording that is not primary, and clue text alone, which is reported but does not make a location playable.
- The 11 tests in `tests/backend/test_content_coverage.py` are integration tests, because they use the `db` fixture, a real PostgreSQL database, and real uploads through `add_clue_recording`.
- The PR did not break anything else: on the merge commit, the full suite gives 1057 passed and 6 skipped, and ruff check, ruff format, `mypy` and `dep_graph.py --check` pass. The PR changes 3 files and 495 lines.

## For PR [#287](https://github.com/COSC-499-W2026/capstone-project-team-12/pull/287): locations CSV import service

As part of requirement A20 ("Import locations from a CSV; dry_run=true returns a preview with row-by-row validation (room number format, coordinates, duplicates)", and "Bulk imports check each row or file the same way, and the response lists every imported and rejected row or file with the reason"), a content manager needs to upload a CSV with their locations, look at a preview of what would happen, and then import it with every rejected row explained.

Therefore, I implemented `import_locations(csv_data, db, dry_run)` in the new `app/content/location_import.py`, with `ImportedRow`, `RejectedRow` and `LocationImportReport` in `app/content/schemas.py`. The first row names the columns (`name`, `building_name`, `room_number`, `entrances`, and optionally `floor_number` and `gps_radius_meters`), and `entrances` holds every entrance in one quoted cell, `lat,lng;lat,lng`. Each row is checked as a location created through the API (`LocationRow` is a `LocationFields` with the entrances cell), so the import accepts exactly what the API accepts. Valid rows are imported even when others are rejected. The report gives the spreadsheet row number of each row. A dry run does the same inserts in a savepoint of its own and rolls only that savepoint back. A problem with the file (not UTF-8, wrong header, a quoting mistake) refuses the whole file. The CSV columns are not in the specification, so they still need the client's confirmation.

When the code was reviewed, it was noticed that:
- A quote left open in the CSV made one cell swallow the rows after it, and `csv.Error` was reported with a generic message. The file is now read with `strict=True`, and a malformed file is refused whole with the row where reading stopped and all three causes (a quote not closed, text after a closing quote, a cell over the csv field limit).
- The first dry run rolled back inside a `with` block after the loop, and the docstring did not say what happens to the changes the caller already has pending. The dry run now opens a savepoint of its own and rolls back only that, in a `finally`, so it is undone also when a row raises, and the docstring says that pending changes are flushed first and stay in the caller's transaction. Tests cover both (`test_dry_run_is_undone_when_a_row_raises`, and the pending-changes check inside `test_dry_run_saves_nothing_and_keeps_the_callers_changes`).
- Two imports at the same time that insert the same room numbers in different orders would each wait for the other's row, which is a deadlock. Imports now take a PostgreSQL advisory lock that is held until the caller's transaction ends, taken before the savepoint so a dry run keeps it, and a test checks that a second import waits.
- The message for a duplicate room number did not help when the room belongs to a deactivated location. `_saving_room_number` in `locations.py` now says to reactivate it (`is_active` true) instead, for create (POST), replace (PUT) and the import, and the existing test covers both POST and PUT. The error helper `_invalid_field` became `invalid_field` so that the import can use it instead of copying it.
- The first version also had the row limit (`max_import_rows`). I moved the import limits out of this PR into #288 (commit "move the import limits out of the locations CSV import PR").
- Two dry-run tests that checked the caller's pending changes overlapped, so I merged them into one, and trimmed the inactive room number test in `test_content_locations.py` (commit "merge the dry-run caller-changes tests and trim the inactive room number test").
- `_problem` names a CSV column and numbers entrances from 1, unlike a 422 `field`, which numbers from 0; the docstring now says why, since the people who read the report look at the file. The check for repeated header columns uses `Counter` instead of `header.count` for each column.

The PR does not contain a temporary workaround. The service is the real one, and the router that will pass the uploaded file and own the transaction is a later PR. The only open point is the CSV format, which is documented as awaiting confirmation. The module has 9 functions of 6 to 17 lines (`_rows` 17, `_check_header` 17, `import_locations` 14, `_read_csv` 14, `_import_row` 13). I checked the PR for the listed problems:
- No hardcoded values: the columns, the optional columns and the lock key are named constants, and the default radius is the existing `gps_radius_meters` setting through `LocationFields`.
- No duplicate code: the row rules, the room number normalisation and the duplicate check are `LocationFields` and `create_location`, not copies.
- No dead code, deep nesting or unrelated responsibilities: reading the file (`_read_csv`, `_rows`, `_cells`, `_check_header`) is separate from importing a row (`_import_row`, `_location_row`, `_problem`).
- The file cannot set accepted answers or `is_active`, on purpose: an imported location accepts the answer its room number gives and is active. The docstring says so, because the portal's import help should tell the content manager to add extra answers with PUT afterwards. <!-- TODO: confirm with the client that the import does not need accepted answers. -->

This work is written in the new `app/content/location_import.py` because the bulk import is a separate service from the single-location services in `locations.py`, which it calls. It belongs to process 5.4 Bulk import in the content management DFD (flows 13 to 17: the CSV in, existing locations read, saved records, and the row-by-row preview or result out; D4 Content). The DFD is unchanged.

**Tests:**
- Happy path tests passed for every column being saved, optional columns being blank or left out, stray semicolons in the entrances being ignored, empty lines being skipped but counted in the row numbers, and a dry run saving nothing and keeping the caller's pending changes.
- Abnormal tests passed for a dry run undone when a row raises, a second import waiting until the first one's transaction ends, and the import leaving the transaction to the caller.
- Negative tests failed as expected for invalid rows (reported while the others are imported), a room number already in use (by an earlier row, or by an inactive location), and files that cannot be read (not UTF-8, bad quoting, a wrong header), which are refused whole.
- The 50 tests in `tests/backend/test_location_import.py` are integration tests, because they use a real PostgreSQL database, including the advisory lock. The duplicate room number test in `tests/backend/test_content_locations.py` (48 tests) was changed to cover POST and PUT and the new message.
- The PR did not break anything else: on the PR head, the full suite gives 1107 passed and 6 skipped, and ruff check, ruff format, `mypy` and `dep_graph.py --check` pass. The PR changes 5 files, 483 lines added and 4 removed, under the 500-line PR limit.

## For PR [#288](https://github.com/COSC-499-W2026/capstone-project-team-12/pull/288): limits and clearer reasons for the locations CSV import

As part of requirement A20 (a bulk import "checks each row" and reports "the reason"), a content manager who uploads a CSV needs a file that is too big to be refused quickly, and needs to understand why a row was rejected. The specification sets no limit for a CSV (it has 5 MB and 60 s for audio only), so the limits are proposals for the team and client to confirm.

Therefore, I implemented these changes:
- Two settings in `app/config.py`: `max_import_rows` (1000, far above the campus's few dozen locations, so one import cannot hold its transaction and its room number locks for long; 1000 rows take about 1.5 s) and `max_import_file_kb` (512; 1000 rows are about 35 KB).
- `import_locations` now takes the uploaded file object instead of its bytes, reads at most the size limit plus one byte, and returns 413 `FILE_TOO_LARGE`. Over the row limit, `_read_csv` stops reading at the limit plus one row and refuses the file whole with a VALIDATION_ERROR on `file`.
- Reasons in plain words: a missing value is "is blank", an invalid character is "contains an invalid character", our own errors have no "Value error, " prefix, and a room number repeated in the file says "Same room number as row N".
- The new `app/content/uploads.py` has `read_upload(upload, max_bytes, too_large)`, which `check_audio` now uses too, so there is one bounded read for every upload.

When the code was reviewed, it was noticed that:
- The first version of the size limit reused `max_media_file_mb` (5 MB). Parsing holds about 23 times the file in memory at worst (a row of commas), so 5 MB is too much for a CSV. I added the CSV-specific setting `max_import_file_kb` and changed `read_upload` to take the limit and the message from the caller instead of reading the media setting itself.
- The test for pending changes in a dry run did not call the import through the `import_csv` test helper, like the other tests do with the new file-based signature. I changed it to use `import_csv`.
- The row-limit message said only that the file had more rows than "the most one import takes". It now says to split the file into files of at most that many rows.

The PR does not contain a temporary workaround; the limits are real settings that the router will rely on. The new functions are `read_upload` (7 lines) and `_same_room_as` (7); `_read_csv` and `import_locations` grew to 19 lines each, and `_import_row` to 16. I checked the PR for the listed problems:
- No hardcoded values: both limits are settings with a comment saying where the number comes from, and `BYTES_PER_KB` and `BYTES_PER_MB` are named constants.
- No duplicate code: before this PR the size check was written in `check_audio`; now `check_audio` and the CSV import call the same `read_upload`.
- `_same_room_as` is only called after a save has failed, so rows that import pay nothing for it. No dead code or deep nesting.

This work is written in `config.py`, `uploads.py` and `location_import.py` because the limits are settings like the audio limits, the bounded read is shared by every upload, and the reasons belong to the import that produces them. It belongs to process 5.4 Bulk import in the content management DFD (flows 13 and 17). The DFD is unchanged.

**Tests:**
- Happy path tests passed for a file of exactly the row limit and exactly the size limit being accepted, and the defaults of the two settings (1000 and 512) and changing them.
- Abnormal tests passed for the friendlier reasons ("name: is blank", "contains an invalid character"), a room number repeated in the file ("Same room number as row 2"), and the dry run with pending changes called through the helper.
- Negative tests failed as expected for `test_file_over_the_row_limit_is_refused_whole` (even with a bad row after the limit) and `test_file_over_the_size_limit_is_refused_unread`, and `test_upload_limits_must_be_positive` refuses 0 for both new settings.
- They are integration tests in `tests/backend/test_location_import.py` (55 tests, 4 more than before) and unit tests in `tests/backend/test_config.py` (23 tests, 3 more), which need no database.
- The PR did not break anything else: on the PR head, the full suite gives 1115 passed and 6 skipped, and ruff check, ruff format, `mypy` and `dep_graph.py --check` pass. The PR changes 6 files and 157 lines added and 41 removed. This PR is built on #287.

## For PR [#290](https://github.com/COSC-499-W2026/capstone-project-team-12/pull/290): clue audio zip import

As part of requirement A20 ("Upload a zip of clue recordings named {ROOM}_{difficulty}_{lang}.mp3 (e.g., SCI333_2_fr.mp3)", and "Bulk imports check each row or file the same way"), a content manager needs to upload one zip with many recordings, have the valid ones imported as the primary recording of their clue, and read why each of the others was rejected.

Therefore, I implemented `import_clue_audio(zip_file, uploaded_by, db)` in the new `app/content/audio_import.py`, with `ImportedFile`, `RejectedFile` and `AudioImportReport` in `app/content/schemas.py`. A name is `ROOM_difficulty_language.mp3` (or `.m4a`, in any case), with the room number in capitals and no space, and only the file's own name counts, not its folders. Each file is read whole (at most `max_media_file_mb`) and imported with `add_clue_recording(..., make_primary=True)`, so it gets the same audio checks as a single upload and the old primary keeps its row for games that are using it. Folders and hidden files (`.DS_Store`, macOS's `__MACOSX/._` files) are skipped and left out of the report. A name that does not match, a room number no location has, an unreadable file and the audio checks' errors reject only that file, and nothing is stored for it. If several files are named for the same clue, none of them is imported, because which one is meant cannot be told. An upload that is not a zip is refused with 415 `UNSUPPORTED_MEDIA_TYPE`.

I extracted `read_audio_file` from `check_audio` in `audio_files.py` so that a file inside the zip is bounded by the same 5 MB limit, reading one byte more at most, whatever size the zip states for it. `read_upload` now takes any `IO[bytes]`.

When the code was reviewed, it was noticed that:
- Each file locks its clue row (`add_clue_recording` takes `get_or_create_clue(lock=True)`) and the locks are held until commit, in zip order. Two imports with the same two clues in opposite order deadlock: on PostgreSQL one failed with a deadlock error (a 500) and the files it had already stored were orphaned. Therefore, I made `_import_zip` process the files in the order of their name's key (room, difficulty, language), while the report keeps the zip's order, and ran the same two-zip reproduction again: both imports commit.
- The import holds every clue lock until it commits, and a full zip took a minute or two, which is longer than a default 60 s proxy timeout. I measured re-encoding at 0.08 s for a 10 s clip and 0.41 s for a 60 s clip, so `max_import_files` goes from 600 to 100 (under about 45 s even if every clip is 60 s). I kept one transaction, without chunked commits or a background job. A campus becomes one zip per language (90 clue files).
- No test checked that a large entry is cut off at the limit: replacing `read_upload` with `file.read()` in `_read_file` still passed. I added a test that a 20 MB entry is read no further than 5 MB plus one byte.
- Correctness relied on `add_clue_recording` raising every `AppError` before it writes anything. Each file is now imported in a savepoint, with a test where a callee writes and then raises and nothing is left behind.
- Smaller points: the docstring now says that re-importing the same zip adds a new primary row for each clue (intended, as recordings are never deleted and open games may point at them), that audio is imported into deactivated locations as a single upload does, that each report list is in zip order (not across both lists), and that paths are reported as given.
- Not changed in code: the "Upload a zip file" 415 is not covered by the spec's 415 description, so the spec's error table needs an update (I have not edited the spec), and the spec's mention of a preview for zip imports is not built. I propose `dry_run=true` on both zip routes once the team and client agree.

These fixes are written and tested, but they are not in this PR as pushed: they land in A20 part 11, before any route can call the imports. The numbers and tests below describe this PR as pushed.

The PR does not contain a temporary workaround: the import uses the real `add_clue_recording` and audio checks, and the router that supplies `uploaded_by` and owns the transaction is a later PR. The functions are `_import_zip` (24 lines), `import_clue_audio` (19, with its inner `import_file` of 12), `_file_key` (12), `_read_file` (10), `_location_id` (7) and `_open_zip`, `_skipped` and `_path` (4 to 5). I checked the PR for the listed problems:
- No hardcoded values: the name pattern and its description are built from the `Difficulty` and `Language` enums, so a new language needs no change here.
- No duplicate code: the audio checks, the storage of the file and the choice of the primary recording are `add_clue_recording`, and the size-limited read is shared (`read_audio_file`, `read_upload`).
- `_import_zip` takes the name pattern and an import function as arguments, so the next PR can reuse it for the feedback zip without copying it. No dead code, and the deepest nesting is a `with`, a `for` and a `try` in `_import_zip`.

This work is written in the new `app/content/audio_import.py` because the zip import is a separate service of the content component, which calls the clue audio service. It belongs to process 5.4 Bulk import in the content management DFD (flows 13 to 17; D4 Content for the records and D8 Media Storage for the audio files, flow 16). The DFD is unchanged.

**Tests:**
- Happy path tests passed for valid files being imported while each invalid one is reported, an imported recording becoming the primary one while the old one keeps its row, and folders and hidden files being skipped.
- Abnormal tests passed for a damaged file being rejected while the others are imported, an error after the file was read not being reported as unreadable, and the import flushing but never committing.
- Negative tests failed as expected for names not in the format (several wrong names), a room number no location has, several files for one clue (none imported), and an upload that is not a zip (empty, text, cut short).
- The 21 tests in `tests/backend/test_clue_audio_import.py` are integration tests, because they use a real PostgreSQL database, real audio built with the `make_audio` fixture and a real temporary media folder (`stored_media_dir`).
- The PR did not break anything else: on the PR head, the full suite gives 1136 passed and 6 skipped, and ruff check, ruff format, `mypy` and `dep_graph.py --check` pass. The PR changes 5 files and 484 lines added and 7 removed. This PR is built on #288.

## For PR [#291](https://github.com/COSC-499-W2026/capstone-project-team-12/pull/291): feedback audio zip import and safer zip reading

As part of requirement A20 ("Upload a zip of feedback phrases named {CODE}_{lang}.mp3", and the fixed library of 13 phrases per language uploaded as a zip), a content manager needs to upload the feedback phrases of a language as a zip, so that each file replaces the phrase it is named for, and the zip must not be able to break the server.

Therefore, I implemented `import_feedback_audio(zip_file, db)` in `app/content/audio_import.py`. A name is `CODE_language.mp3` (or `.m4a`), for example `NAV_START_fr.mp3`, with the code one of the feedback codes. `_replace_phrase` checks the recording with `check_audio`, stores it under a random name, and replaces the file of the phrase (its transcript is cleared, because it no longer matches) or adds the phrase. The replaced file stays in storage, as the specification has no cleanup job. It uses the same `_import_zip` as the clue zip, so the reading, the skipping, the duplicates and the report are the same for both.

I also hardened the zip reading, and added the setting `max_import_files` (600; every clue of 30 locations is 540 files, and each file is re-encoded, so a full zip takes a minute or two).

When the code was reviewed, it was noticed that:
- `_replace_phrase` looked the phrase up and then added it, so when two feedback imports add the same new phrase at the same time, the second fails on the primary key (`UniqueViolation`) and the whole import returns 500. I reproduced it on PostgreSQL. `_replace_phrase` is now a single `INSERT ... ON CONFLICT (code, language) DO UPDATE`, so the second import waits and then replaces the phrase; one row remains.
- The feedback import has the same lock-order deadlock as the clue import in #290, since `_import_zip` is shared: two imports touching the same existing phrases in a different zip order deadlock. The one fix in `_import_zip` (files processed in the order of their name's key, the report in zip order) covers both imports.
- There was no real-PostgreSQL test for either case, because the suite's `db` fixture never commits, so a second connection cannot see its rows. The tests (clue deadlock, feedback deadlock and the new-phrase race, each in its own thread and session, with setup rows committed and then deleted) are not written yet and go in with the zip routes.

These fixes are written and tested, but they are not in this PR as pushed: they land in A20 part 11, before the zip routes can ship. The numbers and tests below describe this PR as pushed.

When the zip reading was checked, it was also noticed that:
- A zip with a bad compressed stream, a wrong offset, a bad name encoding or an unsupported compression raised `zlib.error`, `LZMAError`, `OSError`, `ValueError` or `NotImplementedError` instead of `BadZipFile`, and only `BadZipFile` was caught. A tuple `_ZIP_ERRORS` now lists what `zipfile` raises, both when the zip is opened and when a file is read, and the upload is refused with 415 or the file is reported as unreadable. 
- Encrypted entries were not checked. They are now recognised from the flag bits and reported as "encrypted, damaged or compressed in an unsupported way".
- A zip could hold any number of files, each one re-encoded. `_check_file_count` refuses a zip of more than `max_import_files` files whole, before any file is read.

The PR does not contain a temporary workaround. The feedback import is the real one, and the router is a later PR. The functions are `_replace_phrase` (17 lines), `import_feedback_audio` (10, with an inner `import_file` of 3), `_check_file_count` (5) and `_import_zip` (25), which only grew by the file count check. I checked the PR for the listed problems:
- No hardcoded values: the limit is a setting with a comment, the encryption flag is a named constant, and the name pattern and its description come from the `FeedbackCode` and `Language` enums.
- No duplicate code: both imports share `_import_zip`, `_file_key`, `_read_file` and the report, and differ only in the name pattern and the function that imports one file.
- `_replace_phrase` deletes the stored file if the row cannot be saved (a test covers it), as `add_clue_recording` does. No dead code or deep nesting.

This work is written in `app/content/audio_import.py` because both zip imports are the same service of the content component. It belongs to process 5.4 Bulk import in the content management DFD (flows 13 to 17; the feedback phrases are in D4 Content and the audio files in D8 Media Storage). The DFD is unchanged, and so are the architecture diagram and the ER diagram (no migration).

**Tests:**
- Happy path tests passed for valid files being imported while each invalid one is reported, the stored file having a random name and no tags, and an imported file replacing the phrase and clearing its transcript.
- Abnormal tests passed for a file deleted when its row cannot be saved, the import flushing but never committing, and files that cannot be read from the zip (several kinds of damage) being rejected while the others are imported.
- Negative tests failed as expected for names not in the format, several files for one phrase (none imported), an upload that is not a zip, and a zip with more files than the limit, which is refused whole.
- The 16 tests in the new `tests/backend/test_feedback_audio_import.py` and the 29 tests in `tests/backend/test_clue_audio_import.py` (8 more than before) are integration tests, because they use a real PostgreSQL database and a real media folder. `tests/backend/test_config.py` (25 tests, 2 more) checks the new setting without a database.
- The PR did not break anything else: on the PR head, the full suite gives 1162 passed and 6 skipped, and ruff check, ruff format, `mypy` and `dep_graph.py --check` pass. The PR changes 5 files and 405 lines added and 26 removed. This PR is built on #290.

Otherwise, the next step is A20 part 11, which contains the review fixes for #290 and #291 described above and the content router (`/content/...`) that calls these services. After that, the audio problem reports (5.5 and 5.6) remain.

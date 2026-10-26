# Week 7 Team Log (Oct. 19 - Oct. 25)

Author: Mia Kuang

This week, we focused on finishing the first user-facing endpoints for each component: classes and access codes for teachers, joining and leaving a class, choosing the current class, reading a game's status, uploading content, and enrolling at registration. The goal was that a teacher can set up a class, a student can join it and pick it, and a game's state can be read back, without anyone waiting on unmerged work.

The work was distributed so that:

- Mia worked on the current class and Practice language (A15: #235, #240, #241, #244, #247), so a student can choose which class the app follows with `PATCH /me/profile` and is refused while a game is open, and on game status (A25: #248, #249, #250), so a student can read their open game or any of their games with `GET /games/current` and `GET /games/{id}`. A25 was about 785 lines, so it was split into three PRs.

- Fabiha finished the teacher side of classes (#84: #220, #221, #222, #245, #224, #260, #262, #263, #264): creating a class, its access codes (more codes, deactivating a code, and codes that expire at the end of the semester by default), updating class settings, and listing the classes a teacher or TA works in. #260 moved the routes to the spec's `/teacher/...` paths. She also finished joining and leaving a class (#85: #233, #252, #253).

- Eddy worked on content management (A20: #219, #223, #226): checking uploaded audio and re-encoding it without metadata, the location and clue-text services, and uploading clue recordings with a primary recording per clue.

- Kaedyn added enrolment to registration (A1: #215), so a student who signs up with an access code joins that class and it becomes their current class. He also merged the first part of sign-in (A2 part 1: #254) on Oct 25.

Kaedyn's auth work is about three weeks behind the plan. Mia already took over the email part (verification, reset and the send-emails job) and finished it in week 6, so the rest of auth is sign-in and refresh tokens. If sign-in is still not merged next week, we will sit down and find out what is blocking it, and split the remaining auth work if needed.

Based on the 27 merged PRs, we mostly met this goal:

- **Engagement**: classes, access codes, class settings, class listing, joining and leaving
- **Accounts**: choosing the current class and Practice language, enrolment at registration, and the first part of sign-in (A2 part 1: #254, merged Oct 25). The rest of sign-in is not merged yet, so the goal for accounts is only partly met.
- **Game**: reading a game's status, open or finished, with idle games expired when they are read
- **Content**: audio checks, locations, clue text and clue recordings

The team collectively did the following to keep the code base free of repeated logic and tangled dependencies:

- **One lock for anything that depends on the open game (#240, #252):** `lock_user` in `app/services/locks.py` locks the user row. Choosing a class, setting the Practice language, starting a game, joining a class and leaving a class all take it first, so a class switch and a game start can't both see "no open game" at the same moment.
- **Accounts asks game, it does not read game sessions (#235, #247):** `get_open_session_id` and `refuse_if_game_open` in `app/game/interface.py` answer whether a student has an open game. The 409 `SESSION_ALREADY_OPEN` is built once in `game_already_open`, so `PATCH /me/profile` and `POST /games` always return the same error.
- **One enrolment check (#244):** `require_active_enrolment` in the engagement interface decides whether a student can use a class, and returns `CLASS_ARCHIVED` only to students who were in the class when it was archived. Choosing a class and starting a game both call it, instead of each keeping its own copy of the rules.
- **Registration reuses joining (#215):** registering with an access code calls `validate_code_and_enrol`, the same function the join route uses, so the code checks and the use count are only written once.
- **Leaving a class reuses ending games (#252, #253):** the leave route calls `leave_class` in engagement and then `end_open_sessions` in game, in one transaction, the same way archiving does. Engagement still does not import game.
- **One idle rule (#235, #248):** `session_idle_limit` reads the expiry setting in one place, and the status reads expire idle games with the existing `abandon_sessions` and `idle_condition`, so "idle" still has one definition.
- **Shared content helpers (#219, #223, #226):** every upload goes through `check_audio` in `app/content/audio_files.py`, and setting a clue's text and uploading its recording both use `get_or_create_clue`, so a clue is created the same way in both places.
- **Generated files kept in sync (#270, #271):** every PR re-exported `docs/api/openapi.json` and redrew the dependency graph, and CI checked both. The graph grew from 44 modules and 119 import edges to 53 modules and 161 import edges, and no rule is broken. These two files caused merge conflicts in almost every stacked PR this week, so we decided to stop committing them and have CI generate both instead, with the API reference and the graph published on GitHub Pages (merged Oct 25).

Now, the team can confirm that the code base does not contain any of the following:

- similar logic implemented multiple times
- many dependencies between modules
- changes in one component require changes elsewhere

With everyone's newly merged work for this week, four of the DFDs below are updated and marked **Updated**. The other merged PRs fill in processes and flows that were already drawn.

We considered alternative data flows:

- **Accounts reads `game_sessions` to check for an open game.** We went through the game interface instead, so accounts does not depend on how game stores sessions, and the idle rule stays in game.
- **The current class is fixed up when it is read.** The first version wrote the fallback class back to the user row on every read, so a read could overwrite a class the student was choosing at the same moment. Reads now only read, and the class is only written when the student chooses one, joins a class or leaves their current class.
- **Status reads skip deactivated locations.** We left that to when the session moves on to the location, so reading a game's status writes nothing except an expiry.

### DFD Level 0
![DFD Level 0](img/dfd_level0_context.png)

### DFD Level 1
**1.0 Auth & Accounts (Updated)**
- Flow 3 (1.1 Register account to D1) changed from "New user, password hash" to "New user, password hash, current class", because registering with an access code writes the current class directly (#215).

![1.0 Auth & Accounts](img/dfd_level1_auth_accounts.png)

**2.0 Game Session (Updated)**
- Flow 37 returns the current status or 404.
- The new flow 41 writes the expired idle session and its skipped locations when a status read finds an idle game (#248, #249, #250).

![2.0 Game Session](img/dfd_level1_game_session.png)

**3.0 Photo Verification (not changed)**

![3.0 Photo Verification](img/dfd_level1_photo_verification.png)

**4.0 Class & Engagement (Updated)**
- Flow 4 (4.1 Set up classes to Teacher) changed from "Class details, 403 or 404" to "Class list or details, 403 or 404", because a teacher can now list all their classes (#262).

![4.0 Class & Engagement](img/dfd_level1_class_engagement.png)

**5.0 Content Management (Updated)**
- Flow 10 (5.3 Upload clue audio to D4) changed from "Audio asset record, primary flag" to "Clue (created if missing), audio asset record, primary flag", because an upload creates the clue first when it does not exist (#226).
- Flow 12 (5.3 to Content Manager) now also lists `UNSUPPORTED_MEDIA_TYPE` and `NOT_FOUND` next to `FILE_TOO_LARGE` and `AUDIO_TOO_LONG` (#226).

![5.0 Content Management](img/dfd_level1_content_management.png)

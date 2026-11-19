# Client demo plan (Nov 19, 2026)

Mia and Eddy. Revised by Eddy, then by Mia. Section 6 lists what we've decided.

## Changes in this revision

By Eddy (checked against `main` on Nov 19):

- Act 3 uses a class prepared beforehand with games from other demo students. A class created live would only hold the one demo game. Act 1 still creates a class live, to show how it's done.
- Shuffle is hidden unless we load 10 locations. A shuffle replaces every unfinished location and never redraws one the game already had.
- Notes on the class request fields, challenge labels, the shared rate limit through the proxy, Vite's `allowedHosts` for the tunnel, and the 3-minute GPS fallback.
- Eddy plays the seeded games through the API (section 4).

By Mia:

- The student plays only the first location, which is the classroom: walk there, scan the sign, then quit. The other four locations are marked as skipped.
- The classroom is made to come first by preparing the demo student beforehand (section 2, "Prepared beforehand"). No backend change.
- The reward screen reads the summary after the quit (`GET /games/{id}/summary`), not from a photo response.
- Only the classroom needs real entrance coordinates. The demo takes about 12 minutes.
- The open questions in section 6 are now decisions.

## 1. Goal and rules

- Show the client what the real backend already does: a student plays a game, and a teacher sees the results. The emphasis is the student's game.
- One web page, built only on the real API. No visual design work: that is Milestone 2. The page has every screen and button the flow needs, with a clean placeholder style.
- Nothing in the demo may contradict the spec. Where the backend doesn't support something yet, we leave it out rather than fake it.
- Backend: `main` once Eddy's A20 stack is merged (up to `feat/a20-session-location-fks`). Locations, clues, clue recordings and feedback phrases then all come from the database. No backend changes for the demo.
- Frontend: branch `demo/client-demo`, cut from that final `main`, files in `src/frontend/demo/`. Not merged into `main`.

## 2. Demo flow (about 12 minutes)

Two devices: a laptop for the teacher, a phone for the student. A game draws five locations, but the student only plays the first one, the classroom: walk there, scan the sign, then quit the game. The other four locations are marked as skipped, and the reward screen shows the points from the classroom. Both accounts are created beforehand and already logged in on their device when the demo starts.

### Prepared beforehand

- A class "JAPN 101 Demo" (Japanese) owned by the demo teacher, with three or four other demo students who have each finished a game in it. Its leaderboard, analytics and export therefore have data.
- The demo student is not in any Japanese class yet: a student can have only one active class per language, so joining would fail otherwise.
- **The classroom comes first.** A new game puts the locations the student hasn't completed yet (same language and difficulty, in any mode) before the ones they have, in random order. So before the demo, the demo student plays in **Practice** (Japanese, difficulty 1) and completes every location except the classroom. In the demo's class game, the classroom is then the only location they haven't completed, so it is always drawn first.
  - The classroom is drawn into the Practice games too. It must not be completed there: when it comes up, either fail it (six non-matching photos), or leave it for last and quit the game, so it is only skipped.
  - Quitting in Practice is fine: the carry-over of unplayed locations only applies to the same mode, and the demo game is in the class.
  - The demo uses the same language and difficulty as the preparation (Japanese, difficulty 1).
  - The rehearsal student and the spare student get the same preparation.
  - Check: right after S5, the operator looks up position 1 of the game by SQL on the laptop before the walk starts.

### Act 1: teacher sets up a class (laptop, ~3 min)

| # | Step | What the client sees | API |
|---|---|---|---|
| T1 | Teacher's class list | Already logged in; "JAPN 101 Demo" is listed | `GET /teacher/classes` |
| T2 | Create a class | Name, Japanese, semester (2026W1); the access code comes back in the response. This class only shows the flow and stays empty | `POST /teacher/classes` |
| T3 | Access code for the demo class | Open "JAPN 101 Demo", make a new code, large on screen | `POST /teacher/classes/{id}/access-codes` |
| T4 | Leaderboard setting (optional) | On/off toggle on "JAPN 101 Demo" (on by default; leave it on for S11) | `PATCH /teacher/classes/{id}` |

### Act 2: student joins and plays the classroom (phone, on campus, ~6 min)

| # | Step | What the client sees | API | Owner |
|---|---|---|---|---|
| S1 | Home, signed in | Student account made beforehand, already logged in | — | Mia |
| S2 | Join the class | Types the code from T3 | `POST /me/classes/join` (`access_code`) | Mia |
| S3 | Home | Class, level, points, streak, today's challenges | `GET /me/profile`, `GET /me/engagement` | Mia |
| S4 | Permissions | Short screen asking for location and camera | browser APIs | Mia |
| S5 | Start a game | Basic, class mode; the classroom is location 1 | `POST /games` (`difficulty`, `class_id`) | Mia |
| S6 | Stage 1 | Tap Start to hear the clue; pings every 5 s; voice feedback only; "I'm here" when `can_confirm`; "Continue without GPS" from `gps_override_available_at` | `GET .../current-location`, `POST .../navigation/ping` | Mia |
| S7 | Arrive | Confirm arrival at the classroom door, Stage 2 starts | `POST .../navigation/confirm-arrival` | Mia |
| S8 | Photo | Live camera, framing guide, upload, OCR result at the classroom sign | `POST .../verification/photo` | Eddy |
| S9 | Quit | The game moves to location 2; tap Quit and confirm. Locations 2 to 5 become skipped | `POST /games/{id}/quit` | Mia |
| S10 | Reward screen | Points from the classroom, level, new badges from the summary; streak and challenges from engagement | `GET /games/{id}/summary`, `GET /me/engagement` | Mia |
| S11 | Leaderboard and history | Own rank among the seeded students; the game just played | `GET /classes/{id}/leaderboard`, `GET /me/games` | Mia |

During play the page also has Replay clue, Pause/Resume, Reset and Quit (Reset and Quit ask for confirmation). Shuffle is hidden (section 6, row 2).

### Act 3: teacher sees the results (laptop, after the walk, ~3 min)

All on "JAPN 101 Demo", which now holds the seeded games plus the one just played.

| # | Step | What the client sees | API |
|---|---|---|---|
| T5 | Class page | "JAPN 101 Demo" | `GET /teacher/classes` |
| T6 | Location analytics | Success rate, attempts, time per location | `GET /teacher/classes/{id}/analytics` |
| T7 | Students | Points, sessions, streak, difficulty hints | `GET /teacher/classes/{id}/students` |
| T8 | Participation export | CSV for a date range | `GET /teacher/classes/{id}/participation.csv` |
| T9 | Deactivate the code (optional) | Code stops working | `PATCH /teacher/access-codes/{id}` |

## 3. Spec rules the demo follows

- The clue is audio only and never shown as text.
- Stage 1 guidance is voice only: no direction, distance, colour or text hint; the page plays feedback only when the API returns `speak: true`.
- Audio starts from a tap (Start, Next location); clips go through one queue so they never overlap; Replay works at any time, and an expired link is fetched again from current-location.
- The photo comes from a live in-app camera (`getUserMedia`) with a framing guide. No gallery upload and no file-picker fallback. The client resizes it to at most 1600 px JPEG before upload (the server refuses files over 5 MB).
- Permission prompts, errors and the Reset/Quit confirmations are bilingual: Japanese first, English underneath.
- Login, registration and join-a-class are in English.
- Large touch targets (at least 44 px).
- Report audio problem: the button next to Replay and its dialog (four reasons, an optional note, always in English) are on the game screen, so the client can see the feature. The backend has no report endpoint yet, so Send only says that reports aren't sent in this demo; nothing is faked.

Left out because the backend doesn't have them yet: Idempotency-Key on uploads, TA invitations, the admin panel, the streak in the game summary (#348).

## 4. Who does what

### Mia

- Page shell: routing, login and registration, token handling, API client, audio queue, bilingual prompts.
- Student screens: home, join a class, permissions, new game, Stage 1, arrival, reward screen, leaderboard, history.
- Teacher screens: classes, create class, access codes, settings, analytics, students, CSV export.
- Dev server: Vite forwards `/api/` to the backend, so the page and the API share one origin; `server.allowedHosts` lets the tunnel's hostname through.
- HTTPS for the phone (tunnel), tested on a real phone.
- Accounts and data: create the teacher, the demo, rehearsal and spare students, and the seeded students; create "JAPN 101 Demo" and enrol the seeded students. Log the teacher and the demo student in on the laptop and phone before we start.
- Dry run through the page on a phone once Eddy's content is loaded.

### Eddy

- Content pack and loading:
  - locations CSV;
  - clue audio zip;
  - feedback audio zip (all 13 phrases);
  - a load script;
  - a runbook from `docker compose up` to a playable game.
- Japanese phrase and clue texts, and the TTS audio made from them.
- Photo module `photo.js` (contract in section 5), including the OCR result display.
- Docker dry run through the API: one full game with real sign photos; OCR time and memory recorded.
- Seeded games: the seeded students each play one game in "JAPN 101 Demo" through the API (GPS replay and sign photos), using the dry-run script. Some should retry or fail a location, so the success rates aren't all 100%.
- Classroom-first preparation: with the same script, the demo, rehearsal and spare students each complete every location except the classroom in Practice (Japanese, difficulty 1), without completing the classroom (section 2).

## 5. Photo module contract

```js
takePhoto({ gameId, api, audio, container, attemptsLeft }) -> Promise<PhotoResponse>
```

- `container`: the module renders the camera and results inside it; the game screen clears it afterwards.
- Upload with `api.upload(`/games/${gameId}/verification/photo`, formData)`, field `photo`. `api` adds the token.
- Show each result (`match` / `unclear` / `no_match`) with an animation, play `response.feedback` (a list of `{code, audio_url}`) with `audio.playAll(...)`, and show `attempts_remaining`.
- While `location_result` is null, the location is still open: let the student retake.
- Resolve with the last response once `location_result` isn't null; the game screen then goes to `next`, or to the reward screen when `next` is null. In the demo the student quits from `next`.
- Let API errors throw; the game screen shows them.

## 6. Decisions

| # | Topic | Decision | Owner |
|---|---|---|---|
| 1 | Machine | Eddy's laptop runs everything on demo day: Docker backend (at least 3 GB memory for Docker; OCR peaks at ~1.7 GB), the Vite dev server and the tunnel. The content pack, accounts and seeded games are loaded there, so we don't move the database. The teacher screens run on the same laptop | Eddy |
| 2 | Locations | Five locations: the classroom plus four others. Shuffle stays hidden | Eddy |
| 3 | Difficulty | Difficulty 1 only. The page offers only Basic | Eddy (clues), Mia (page) |
| 4 | Entrance coordinates | Real coordinates for the classroom only. The other four are only played by scripts, so placeholder entrances are fine for them (marked as placeholders in the CSV) | Eddy |
| 5 | Japanese texts | We write and check the clue and phrase texts ourselves; no outside review for the demo | Eddy + Mia |
| 6 | HTTPS | Cloudflare quick tunnel (`cloudflared tunnel --url http://localhost:5173`): no account needed. Tested on the demo phone before the demo | Mia |
| 7 | Accounts | All created beforehand. One staff account is both the content manager and the demo teacher (role and content rights set by SQL; no admin API yet). Demo, rehearsal and spare students are registered in advance and not in any Japanese class. Teacher and demo student are logged in on their devices that morning; the refresh cookie (14 days) keeps them signed in | Mia |
| 8 | Mode | Class mode in "JAPN 101 Demo" | Mia |
| 9 | Challenge names | The page maps each `description_key` (e.g. `complete_3_locations`) to a short English label | Mia |
| 10 | Runbook | One runbook in Eddy's local `demo-content/` folder (not committed): Eddy writes content, seeded games and the classroom-first preparation; Mia adds the accounts, class, frontend and tunnel steps | Mia + Eddy |
| 11 | Rate limit | Every request through the proxy shares one IP and the 100-a-minute limit. No scripts run during the demo | Mia + Eddy |
| 12 | Classroom | A room near where we meet the client, with a readable number sign that OCR handles well (confidence of at least 0.85 in the OCR test photos). Eddy picks it and collects its entrance coordinates | Eddy |

## 7. Order of work

1. Eddy's A20 stack merges into `main`.
2. Eddy picks the classroom and collects its entrance coordinates.
3. Eddy: dry run through the API in Docker, content pack (with the classroom), runbook.
4. Mia: `demo/client-demo` from the final `main`; pages built against this plan; `photo.js` stub with the contract.
5. Eddy: `photo.js` in the same branch.
6. On Eddy's laptop: load the pack (Eddy); create the accounts and "JAPN 101 Demo" (Mia); play the seeded games and the classroom-first preparation through the API (Eddy).
7. Together: start the tunnel, play the classroom on a phone through the page with the rehearsal student, quit, then Act 3 on the laptop. The demo student must still be outside every Japanese class. The rehearsal game stays in the class's analytics, which is fine.
8. Demo morning: same checklist; log in the teacher on the laptop and the demo student on the phone; keep the spare student and a charged phone ready.

# Client demo page

A single web page for the November 2026 client demo, built only on the real backend API. It shows a student's game and the teacher screens with a placeholder style; the real frontend (React, Milestone 2) comes later. This branch (`demo/client-demo`) is not merged into `main`.

The demo plan, the flow and who does what are in [PLAN.md](PLAN.md).

## Run it

1. Start the backend from the repo root. Rebuild the image whenever the backend code changed, or the API container can fail on a migration it doesn't know:

   ```
   docker compose up -d --build
   docker compose ps
   ```

   `curl http://localhost:8000/health` should return `{"status":"ok"}`.

2. Start the page:

   ```
   cd src/frontend/demo
   npx vite@6.0.11 --config vite.config.mjs
   ```

   Open http://localhost:5173. Vite forwards `/api/` to the backend at `http://127.0.0.1:8000` (set `API_URL` to change it), so the page and the API share one origin, as they will behind nginx in production.

3. On a phone, use HTTPS: browsers only allow location and camera on secure pages. In a second terminal, with Vite still running:

   ```
   cloudflared tunnel --url http://localhost:5173
   ```

   Open the `https://...trycloudflare.com` address it prints (it changes on every run). The phone doesn't need to be on the same Wi-Fi. For a quick look without location or camera, the `Network:` address Vite prints works on the same Wi-Fi.

## Before you can play

- **Content.** A game needs at least five playable Japanese locations at difficulty 1 (locations, clue audio and all 13 feedback phrases). Load the demo content pack with its load script; without it, starting a game returns 409 NOT_ENOUGH_CONTENT.
- **A teacher account.** Registration only makes students. Register an account, then make it staff (and a content manager, to load content) by SQL:

  ```sql
  UPDATE users SET role = 'staff', can_manage_content = true WHERE email = 'teacher@example.com';
  ```

- **Emails** (verification, password reset) go to Mailpit at http://localhost:8025, not to real inboxes. Login doesn't need a verified email.

## Files

| File | What it does | Owner |
|---|---|---|
| `index.html`, `styles.css` | Page skeleton; cupcake colours and M PLUS Rounded 1c | Mia |
| `api.js` | Calls `/api/v1` with the access token, renews it with the refresh cookie, turns error bodies into `ApiError` | Mia |
| `audio.js` | One queue for clue and feedback clips, so they never overlap | Mia |
| `i18n.js` | Bilingual prompts and error messages (Japanese first, English underneath) | Mia |
| `ui.js` | Small DOM helpers, toast, confirm dialog | Mia |
| `app.js` | Routes (same paths as the spec, behind `#`), login, register, home, join a class, new game, leaderboard, history | Mia |
| `game.js` | Game screen: Stage 1 navigation, arrival, pause, reset, quit, reward screen | Mia |
| `teacher.js` | Teacher screens: classes, access codes, settings, analytics, students, participation CSV | Mia |
| `photo.js` | Stage 2 camera, upload and OCR result. Currently a stub; the contract is at the top of the file | Eddy |
| `vite.config.mjs` | Dev server and the `/api/` proxy | Mia |

## Spec rules the page keeps

- The clue is audio only, never shown as text; Stage 1 guidance is voice only (feedback plays when the API returns `speak: true`), with no direction or distance on screen.
- Audio starts from a tap; Replay is always there; an expired clue link is fetched again.
- Reset and Quit ask for confirmation; prompts and errors are bilingual.
- The photo comes from a live in-app camera only, no gallery or file picker (`photo.js`).

Report audio problem is on the game screen as the spec describes, but the backend has no report endpoint yet, so Send only says reports aren't sent in this demo. Left out because the backend doesn't support them yet: Idempotency-Key on uploads, TA invitations, the admin panel. Shuffle is hidden: it needs ten loaded locations. The page offers difficulty 1 only, the one the demo content has.

## Troubleshooting

- **`Can't locate revision identified by ...` in `docker compose logs api`**: the image is older than the database. Rebuild with `docker compose up -d --build`. Switching back to a branch based on an older `main` gives the same error, because the database is already migrated past it.
- **`http proxy error ... ECONNREFUSED` in the Vite terminal**: the backend isn't running; check `docker compose ps`.
- **No location or camera prompt on the phone**: use the HTTPS tunnel address, and check the phone's settings (iPhone: Location Services for Safari Websites, Safari camera access). The permissions screen shows once per browser; use a private tab to see it again.
- **`.vite/`** is Vite's cache and is ignored.

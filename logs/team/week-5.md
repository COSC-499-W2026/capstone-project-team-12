# Week 5 Team Log (Oct 5 – Oct 11)

Author: Eddy Tian

This week, we decided to focus on the shared database schema and the component interfaces that the team agreed to finish by Oct 11 and 12. The goal was for every member to build their Milestone 1 features against merged tables and interfaces, without waiting on each other's code.

The work was distributed so that:
- **Mia** focused on the core schema: users, classes and class staff (#150), and access codes and enrolments (#151). She also built the `game_sessions` and `session_locations` tables (#154, #161), the shared error format and handlers (#158), and the shared test fixtures (#157). On the game side, she added `end_open_sessions`, which ends a student's open games when a class is archived or the student leaves (#165, built on her `session_locations` table). She also automated PR metadata (#153), and moved Kaedyn's week 4 log from #139 onto `weekly-logs` (#159).
- **Eddy** focused on the content interfaces for game: content eligibility and snapshot (#149), and media URLs for clue and feedback audio (#162, #163). I also did the follow-ups from the OCR spike review (#148).
- **Kaedyn** focused on accounts: the current user and permission checks other components call (#152), and the first feature endpoint, student registration (A1, `POST /api/v1/auth/register`, #167). Registration checks the email, username and password rules (422 `VALIDATION_ERROR`), rejects taken usernames and emails (409), and stores the password as a bcrypt hash (cost 12, per the spec). Kaedyn later moved the route into its own auth router under `/api/v1` and removed the registration functions from `accounts/interface.py`, since only the router calls them.
- **Fabiha** focused on the engagement interface: the class and enrolment lookups (#155) and the access code check with enrolment (#156).

We considered having each owner write the tables for their own area. We gave the core tables to one person because almost every component reads `users`, `classes` and enrolments, and one author kept the constraints and naming consistent.

Based on the 17 merged PRs (#148–#159, #161–#163, #165 and #167), we met this goal because every interface due Oct 11 and 12 is merged:
- accounts: current user and permissions (#152),
- engagement: class and enrolment lookups and the access code check (#155, #156),
- content: eligibility, snapshot and media URLs (#149, #162, #163),
- game: ending open games when a class closes (#165).

The core schema, shared errors and shared test fixtures they depend on are also merged. Registration (#167) also shows that a full feature can now be built end to end on this foundation, from a request, through the accounts component and the shared error format, to the `users` table.

The team collectively did the following to keep the code base free of repeated logic and tangled dependencies:
- **Shared test fixtures (#157):** Mia found that the core schema tests built their own users, classes and enrolments in every test, so she added shared factories (`make_user`, `make_class`, `make_class_staff`, `make_access_code`, `make_enrollment`) to `conftest.py`. #154, #161 and #165 use them.
- **Shared error format (#158):** Without a shared format, each router would build its own error responses. Mia added one `AppError` with an `ErrorCode` → HTTP status table in `app/errors.py`, with handlers in `app/main.py`. She added `errors` to the shared core in `utils/dep_graph.py`, so every component can raise it without importing another component. #162, #163 and #167 already raise their errors through it.
- **Ending games when a class closes (#165):** Game already calls engagement for class and enrolment lookups, so engagement calling `end_open_sessions` would create a cycle that `dep_graph.py --check` rejects. Instead, the route calls engagement and then game in the same transaction, as the spec's session lifecycle describes.
- **Owner index (#150):** In review, the "one owner per class" index used a hard-coded role string, so renaming the role would have needed a second edit. It is now built from the `ClassStaffRole` enum.
- **Schema checks in CI (#151):** CI now runs `alembic check` and a downgrade/upgrade round trip, so the models and migrations cannot drift apart.

Now, the team can confirm that the code base does not contain any of the following:
- similar logic implemented multiple times
- many dependencies between modules
- changes in one component require changes elsewhere

With everyone's newly merged work for this week, the system DFD looks like the updated Level 1 diagrams below. The specific changes follow the TA rule change: a user can no longer be a student and a TA of the same class, where before it was the same language. #158 renamed the error code to `TA_SAME_CLASS_CONFLICT` to match. In 1.10 Manage teaching assistants, flow 54 now reads enrolments in the same class instead of the same language, and flows 57 and 59 return `TA_SAME_CLASS_CONFLICT`. In 4.4 Join or leave class, flow 16 now reads class staff of the same class.

The other merged PRs fill in processes and flows that were already drawn:
- 1.1 Register account now writes D1 Users & Auth (#167).
- The class-closed flow from 4.3 Archive and 4.4 Leave to 2.5 Control session is now `end_open_sessions`, which updates D5 Game Sessions (#165).
- 2.1 Start session now reads eligible locations and snapshots from D4 Content (#149).

We considered three alternative data flows:
- **Content chooses the 5 locations itself.** We kept the picking in 2.1, because game owns carry-over, shuffle exclusions and `NOT_ENOUGH_CONTENT`. Content only answers which locations qualify.
- **Game reads D4 Content live during play.** We chose to copy a snapshot into D5 when a location is drawn, so a content manager's edit never changes a game in progress.
- **Engagement sends the class-closed notice to game directly.** We had the route make both calls, to avoid the cycle described above.

With everyone's newly merged work for this week, the system architecture diagram has one change: `app/errors` joins config, db, models and services in the shared core box (#158), since every component now raises `AppError` from it. Registration (#167) fits the existing AuthService in the 1.0 box, so it needs no change. We considered letting routers raise FastAPI's `HTTPException` directly, but the services and interfaces would then depend on the web layer, and each router would repeat the status mapping. We considered two alternative dependencies:
- **Our own model-to-migration comparison:** we used Alembic's built-in `alembic check` in CI instead.
- **passlib for password hashing:** the spec lists it, but #167 calls `bcrypt` directly because passlib is no longer maintained and does not work cleanly with current bcrypt releases.

We changed our collaboration process so that a GitHub Action (#153) fills in each PR's assignee, reviewers (following the review cycle in our team contract), labels and milestone. We also made the AI section of the PR template optional for PRs without AI-generated code. The reason we did this is that these fields were often missed when filled in by hand, which made the board harder to track. We considered checking them manually at our Monday meeting, but that only caught problems after review had already started. 

## Diagrams

Source files (draw.io) are in our [Drive folder](https://drive.google.com/drive/folders/1nHJDhfVOvWCLNEbqzlOMssgTrlTugm7J?usp=sharing).

### System architecture
![System architecture](img/system_architecture.png)

### DFD Level 0
![DFD Level 0](img/dfd_level0_context.png)

### DFD Level 1
![1.0 Auth & Accounts](img/dfd_level1_auth_accounts.png)
![2.0 Game Session](img/dfd_level1_game_session.png)
![3.0 Photo Verification](img/dfd_level1_photo_verification.png)
![4.0 Class & Engagement](img/dfd_level1_class_engagement.png)
![5.0 Content Management](img/dfd_level1_content_management.png)

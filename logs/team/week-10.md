# Week 10 Team Log (Nov. 9 - Nov. 15)

Authors: Mia Kuang

## Goal

This week, we decided to focus on merging the PRs left open in week 9 and starting photo verification (A10). The open PRs were the A20 content imports, the account routes (A2, A22, A24) and the GPS replay script (#46). Mia also planned to finish game history and summary (A23, #47). The goal was to have every step of one game on main: start, navigate, photo and summary (observed: week 9 team log).

## Work distribution

Only PRs merged into `origin/main` between Nov 9 and Nov 15 count as merged. Details are in each person's individual log.

- **Mia** focused on game history and summary (A23), the GPS replay script (#46), the password reset follow-ups (A3) and CI.
  - *Merged:* 11 PRs: #349, #350, #351, #352 (A23), #326, #327, #340 (#46), #334, #336 (A3), #347 (CI) and #335 (a dependency pin). The largest is #349 (3 files, +495/-2). See `logs/MiaKuang/week-10.md`.
  - *Not merged yet:* none.
- **Eddy** focused on photo verification (A10) and content imports (A20).
  - *Merged:* #338 (sign text matching, 5 files, +485/-2), #337 (upload reading moved to shared services, 4 files, +38/-3) and #355 (Pillow security update, 1 line).
  - *Not merged yet:* A10 parts 3 to 6 (#341, #342, #344, #346) and A20 #296, #301, #303, #304, #321.
- **Fabiha** focused on daily challenges and streaks (A13), the class leaderboard (A14) and class analytics (A16).
  - *Merged:* none.
  - *Not merged yet:* 14 PRs opened this week (#356 to #372). Changes were requested on Nov 15.
- **Kaedyn** focused on accounts (A2, A22, A24).
  - *Merged:* none into main. #328 (rate-limit tests) was merged into its base branch, not into main.
  - *Not merged yet:* #323 (logout), #324 (profile) and #325 (change password).

We considered two other workload distributions (AI-suggested). First, Fabiha could write the level-and-badges part of the summary, since she owns engagement. We kept it with Mia because the summary is game work. Mia split that part into its own PR (#350) so Fabiha could review it. Second, Mia could review Fabiha's stacks before finishing A23. We did not do this because Mia's own stacks were waiting for review. The cost was that Fabiha got her feedback only on the last day of the week.

## Goal evaluation

Based on the merged PRs on main, we did not meet the goal (observed: main log). 14 PRs were merged: 11 by Mia and 3 by Eddy.

- **Met:** A23 is complete. Students can see their game history and summary, and quit now returns the summary. The GPS replay script and its tests are merged. A password reset now revokes every refresh token. CI runs again on our own runner.
- **Not met:** the A20 imports, the account routes and A10 parts 3 to 6 were still open. So a game can't yet finish a location from a photo on main.
- **Challenges:** reviews were the bottleneck. Eddy and Fabiha each had stacks of four or more PRs. Most reviews came on Nov 14 and 15, which left no time to fix and merge (observed: review dates).
- **Files over 500 lines:** `tests/utils/test_gps_replay.py` (#340) and `tests/backend/test_login.py` (#336) crossed 500 lines this week. `api/v1/games.py`, `engagement/interface.py` and `tests/backend/conftest.py` were already over and grew. Each of them holds one kind of code for a whole component, so every feature adds to it. Splitting them by feature is a follow-up.
- **Plan for next week:** merge the open stacks early in the week. Then work on the items in Next week.
- **Limitation:** the number of merged PRs does not show all the work done. Several unmerged stacks were already approved or reviewed by Nov 15 (observed: review states).

## Code quality

- **Similar logic implemented multiple times:** we compared the helpers merged this week and read the reviews. By reviewing #349 and #351, Eddy found a copied lookup and a duplicate badge schema. Therefore, Mia reused the existing schema and opened #354 for the lookup. Eddy also moved the photo upload reader into shared services (#337). Photo verification now reuses it instead of copying it (DRY).
- **Many dependencies between modules:** `utils/dep_graph.py --check` passes at the end of the week (observed: 74 modules, 291 import edges, no rule broken). The only new import between components is game reading engagement for the summary. It goes through engagement's interface, so coupling stays low.
- **Changes in one component require changes elsewhere:** no merged PR changed more than one component (observed: files changed per PR). #350 kept the engagement change in its own PR. #337 moved shared code into `app/services` instead of editing two components.

Now, the team can confirm that the code base does not contain repeated logic, extra dependencies or changes that spread across components. The remaining duplication is tracked in #354.

## DFD

We did not update the DFDs this week because we ran out of time. They still show week 9. This week's work changes flows in 1.0 (the A3 reset), 2.0 (the A23 summary) and 3.0 (the start of A10). We will update them next week.

We considered one alternative data flow (AI-suggested): quit returns only the game status, and the app asks for the summary in a second request. We did not choose it because the app would need two calls to show the result.

## System architecture

We did not update the system architecture diagram this week, for the same reason. We will update it next week.

- **Alternative components:** the game summary could live in the engagement component, since it shows level and badges (AI-suggested). We kept it in the game component (#351) because a summary belongs to one game. Engagement only supplies the level and badges through its interface.
- **Alternative dependencies:** photo verification could import the upload reader from content (observed: #337). We did not choose it because it adds a dependency between two components. The reader is shared code, so it moved to `app/services`.

## Collaboration process

We changed our collaboration process in three ways (#347). CI now runs on our own self-hosted runner, `hunt-ci-runner`. A new push cancels the PR's older run. A passing CI check is required to merge again.

The reason is that GitHub Actions jobs still could not start because of the organization's billing issue. We considered keeping the week 9 process, where each author posts local check results. We dropped it because it depends on every author running every step. We also considered waiting for billing or paying for GitHub-hosted runners (AI-suggested). Waiting blocked every merge, and paying was not an option for a course project. So we used a free Oracle Cloud VM.

## Next week

We will:

- **Merge** the stacks still open: A10, A13, A14, A16, A20 and the account routes. We will review early in the week so fixes have time to land.
- **Refactor** repeated code. The game and class routes build the same errors and validation in several places. Class staff access is checked in more than one layer. We will also finish the A23 follow-ups (#348, #354).
- **Test the merged features together** while preparing the client demo. We will open issues for the bugs and gaps we find.
- **Update** the DFDs and the system architecture for this week's changes.

# Week 9 Individual Log

## Fabiha Afifa

**Week 9 (November 1–7, 2026)**

This week, I continued work on A12 (Points, Levels, and Achievements, Issue #86), focusing on reviewing, validating, and preparing the remaining three pull requests from my seven-part implementation: #298 (student engagement statistics), #299 (student achievements), and #300 (gameplay scoring integration). The first four A12 PRs (#225, #228, #229, and #230) were merged on November 1. My focus was on ensuring that the remaining changes were testable and compatible with the backend architecture, while coordinating the dependency between A12 scoring and A10 photo verification.

My availability for capstone work was more limited than usual because I had to travel and attend to time-sensitive passport-renewal requirements. I plan to make up for the reduced time during reading break by prioritizing outstanding PR feedback, integration coordination, and verification.

| PR | Work | Week 9 focus | Status / dependency |
|---|---|---|---|
| [#298](https://github.com/COSC-499-W2026/capstone-project-team-12/pull/298) | A12 P5: Student engagement statistics endpoint | Review and regression verification | Follow up on review and integration |
| [#299](https://github.com/COSC-499-W2026/capstone-project-team-12/pull/299) | A12 P6: Student achievements endpoint | Review and regression verification | Follow up on review and integration |
| [#300](https://github.com/COSC-499-W2026/capstone-project-team-12/pull/300) | A12 P7: Gameplay scoring integration helper | Review, scoring correctness, and test verification | Live A10 caller integration remains separate |

## PR #298: Student engagement statistics endpoint (A12 P5)

**Requirement:** Authenticated students need to retrieve their cumulative points and level for a selected language.

**Work and follow-up:** I continued reviewing the endpoint implementation and its verification requirements. The endpoint exposes the student's points and level through `GET /api/v1/me/engagement`, with language-specific statistics and appropriate authentication and authorization. I paid attention to consistency between the shared level-calculation logic and the values returned by the API, as well as regression coverage for students with no prior points.

**Verification:** Local backend verification of the A12 work included a full test-suite run, Ruff linting and formatting, and dependency checks. I will continue to monitor CI and address any review comments before considering the endpoint complete.

Evidence: [PR #298](https://github.com/COSC-499-W2026/capstone-project-team-12/pull/298).

## PR #299: Student achievements endpoint (A12 P6)

**Requirement:** Authenticated students need to view their unlocked achievements for a selected language, including global achievements.

**Work and follow-up:** I continued checking the achievement retrieval endpoint, `GET /api/v1/me/achievements`, and its use of the existing Engagement service. The implementation separates language-specific achievements from global achievements, restricts results to the authenticated student, and orders unlocks by timestamp. I focused on regression verification and readiness for review rather than adding a new feature.

**Verification:** I reviewed the existing endpoint tests and broader backend test results, with particular attention to filtering, authentication, and ordering. Any new issues identified through CI or review will be addressed in follow-up changes.

Evidence: [PR #299](https://github.com/COSC-499-W2026/capstone-project-team-12/pull/299).

## PR #300: Gameplay scoring integration helper (A12 P7)

**Requirement:** Finalized gameplay locations must be scored consistently without duplicate point awards or incorrect game-session totals.

**Work and follow-up:** I continued reviewing the `score_finalized_location()` helper and its tests. The helper awards 100 points for a completed location and zero for a failed or skipped location, preserves idempotency, recalculates the session total from persisted location awards, and leaves transaction management to the caller. The review also highlighted the importance of testing stale session totals and using explicit database-result handling.

**A10 dependency:** PR #300 provides the scoring helper but does **not** wire it into the live A10 photo-verification route. Eddy is responsible for that A10 work. The finalization route still needs to invoke scoring at the correct point in its transaction, validate ownership and location state, and return the appropriate result. This must also respect database constraints on completed locations and awarded points. As a result, **A12 Issue #86 cannot yet be closed solely on the basis of my seven PRs**; the remaining A10-to-A12 integration needs to be completed and verified.

**Verification:** I continued checking the gameplay scoring tests and backend verification results. I will rerun the relevant checks after any integration or review changes, rather than treating a previously passing run as proof that later changes are safe.

Evidence: [PR #300](https://github.com/COSC-499-W2026/capstone-project-team-12/pull/300).

## Other work this week

- **PR follow-up:** Monitored the three remaining A12 pull requests and reviewed their test coverage and integration requirements.
- **Regression verification:** Worked through local verification of the backend changes, including the test suite, Ruff, and architecture/dependency checks.
- **Cross-feature coordination:** Identified and documented the boundary between A12's scoring helper and Eddy's A10 photo-verification caller.
- **Time management:** Had less time available for capstone work because of travel and time-sensitive passport-renewal obligations.

## Challenges and lessons learned

The main technical challenge was coordinating scoring across two independently developed features. A12 can expose a correct, idempotent scoring helper, but gameplay points are not fully integrated until A10 calls it during location finalization. This reinforced the importance of clearly identifying integration ownership and verifying transaction ordering and database constraints at the point where features meet.

I also had to balance capstone responsibilities with an urgent personal administrative obligation. My passport renewal required travel and attention during the reporting period, which reduced the time I could devote to new capstone tasks. I recognize the need to follow through on the outstanding work and have planned additional time during reading break.

## Next week

I plan to use reading break to catch up on A13 and any task assigned to me for the demo.

**Note to TA:** My contribution volume this week was lower than usual because I was occupied with time-sensitive passport renewal and associated travel. I will use reading break to catch up on outstanding capstone work. The remaining A12 closure dependency is not an unimplemented scoring helper: PR #300 provides that helper, but the live A10 photo-verification flow, owned by Eddy, still needs to connect to it before Issue #86 can be closed.

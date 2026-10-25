# Week 7 Individual Log

## Fabiha Afifa

**Week 7 (Oct 18 – Oct 24, 2026)**

This week I worked on A6 (classroom creation, settings and access-code management, issue #84) and A5 (classroom joining and leaving, issue #85). I completed the classroom management functionality across ten A6 PRs (#220, #221, #222, #224, #245, #260, #262, #263 and #264, alongside the related integration work) and expanded A5 acceptance and leave-class coverage through #233, #252 and #253. The work included backend service functions, REST API endpoints, authorization checks, database validation, integration tests, and updates to generated OpenAPI documentation and the dependency graph.

I split the work into stacked PRs to keep individual changes within our team's review limits. Several PRs depended on other classroom services merging first, so I also spent time resolving conflicts in `app/api/v1/classes.py`, `app/engagement/classes.py` and `app/engagement/interface.py`. I regenerated the documentation after integration rather than editing generated files by hand. My main focus was completing the classroom lifecycle while preserving architectural boundaries, transaction ownership, and the existing game-session behavior.

| PR | What | Main files | Tests/evidence | Status |
|---|---|---|---|---|
| [#220](https://github.com/COSC-499-W2026/capstone-project-team-12/pull/220) | A6 classroom creation service | `engagement/classes.py` | Service tests | Merged |
| [#221](https://github.com/COSC-499-W2026/capstone-project-team-12/pull/221) | A6 access-code generation service | `engagement/classes.py` | Service tests | Merged |
| [#222](https://github.com/COSC-499-W2026/capstone-project-team-12/pull/222) | A6 create-class endpoint | `api/v1/classes.py` | API tests | Merged |
| [#224](https://github.com/COSC-499-W2026/capstone-project-team-12/pull/224) | A6 classroom settings endpoint | `api/v1/classes.py` | API tests | Merged |
| [#233](https://github.com/COSC-499-W2026/capstone-project-team-12/pull/233) | A5 join-class acceptance tests | `test_join_class.py` | 25 passing tests; +163/−2 | Merged Oct 24 |
| [#245](https://github.com/COSC-499-W2026/capstone-project-team-12/pull/245) | A6 classroom settings service | `engagement/classes.py` | Service tests | Merged |
| [#252](https://github.com/COSC-499-W2026/capstone-project-team-12/pull/252) | A5 leave-class endpoint and core tests | API, engagement, tests | 13 core tests; +444/−29 | Merged |
| [#253](https://github.com/COSC-499-W2026/capstone-project-team-12/pull/253) | A5 leave-class game tests | Backend test files | Game-session integration tests | Merged |
| [#260](https://github.com/COSC-499-W2026/capstone-project-team-12/pull/260) | A6 teacher routes and access-code expiry alignment | Classroom API and services | API/service checks | Merged |
| [#262](https://github.com/COSC-499-W2026/capstone-project-team-12/pull/262) | A6 teacher classroom listing | API, engagement, tests | Listing tests | Merged |
| [#263](https://github.com/COSC-499-W2026/capstone-project-team-12/pull/263) | A6 additional access codes | API, engagement, tests | Access-code tests | Merged |
| [#264](https://github.com/COSC-499-W2026/capstone-project-team-12/pull/264) | A6 access-code deactivation | API, engagement, tests | 12 deactivation tests; +471/−3 | Merged |

All PRs above are reported as merged. Exact GitHub merge dates and final diff statistics should be taken from each PR before submission.

## PR #220: Classroom creation service (A6)

**Requirement:** Issue #84 and the technical specification's classroom creation requirements. A teacher needs to create a classroom with a valid name, semester and target language before students can enrol.

**What I did:**
- Implemented `create_class()` in `app/engagement/classes.py`.
- Restricted classroom creation to active staff accounts.
- Validated classroom names and semester values, including the supported `YYYY(W1/W2)/S1/S2` semester format.
- Created the classroom with its target language and assigned the creator as its owner.
- Kept the service separate from the HTTP endpoint so classroom creation logic could be reused and tested independently.

**Review:** I kept the implementation within the engagement component and followed the project's convention that the caller owns the database transaction. The service performs validation and database changes without committing independently. This avoids splitting one classroom-creation operation across multiple transactions.

**Code quality:** `create_class()` is implemented in `app/engagement/classes.py`, with the public operation exposed through the engagement interface. The function separates classroom creation rules from API request handling. This belongs to the classroom creation process in the Engagement/Classroom DFD.

**Tests:** Added service-level tests for successful classroom creation and invalid inputs, including unauthorized actors and invalid classroom attributes. The PR's implementation and test changes are available in [#220](https://github.com/COSC-499-W2026/capstone-project-team-12/pull/220/files).

## PR #221: Access-code generation service (A6)

**Requirement:** Issue #84. Teachers and authorized classroom assistants need access codes that students can use to join their classrooms.

**What I did:**
- Implemented `generate_access_code(class_id, actor, expires_at)` in the engagement service.
- Allowed classroom owners and authorized assistants to generate codes.
- Checked that the classroom exists, is not archived, and that the actor is eligible to manage its access codes.
- Implemented uniqueness retries for generated codes using shared code-length and alphabet constants.
- Supported expiration dates and preserved access-code records for later validation and auditing.

**Review:** I addressed authorization and validation requirements for different staff relationships to a classroom, including student assistants. I kept access-code generation in the service layer rather than duplicating the logic in API routes. The function does not independently commit the transaction.

**Code quality:** The service is in `app/engagement/classes.py`, with its public contract in `app/engagement/interface.py`. This belongs to the access-code generation process in the Engagement/Classroom DFD.

**Tests:** Tested authorized owners and assistants, unauthorized actors, missing and archived classrooms, and invalid access-code creation scenarios. Evidence: [PR #221](https://github.com/COSC-499-W2026/capstone-project-team-12/pull/221/files).

## PR #222: Create classroom API endpoint (A6)

**Requirement:** Issue #84. The frontend needs an authenticated endpoint for teachers to create a classroom and receive its initial access code.

**What I did:**
- Implemented the create-class API endpoint in `app/api/v1/classes.py`.
- Connected the endpoint to the classroom creation and access-code generation services.
- Added request validation and response serialization.
- Restricted the operation to eligible teaching staff.
- Updated the generated OpenAPI document to describe the endpoint and its responses.

**Review:** The initial implementation required alignment with the technical specification's teacher route conventions and access-code expiration behavior. I handled those compatibility concerns through the later route-alignment work in #260 rather than duplicating service logic.

**Code quality:** The API route handles authentication, request/response models and transaction completion, while the engagement service handles business rules. This follows the API → Engagement interface → Engagement service dependency direction.

**Tests:** Added HTTP-level coverage for successful classroom creation and invalid or unauthorized requests. The implementation and tests are linked in [PR #222](https://github.com/COSC-499-W2026/capstone-project-team-12/pull/222/files).

## PR #224: Classroom settings update endpoint (A6)

**Requirement:** Issue #84. Classroom owners need to update supported settings without modifying immutable classroom properties.

**What I did:**
- Implemented the PATCH endpoint for updating classroom settings.
- Supported changes to the classroom name and `leaderboard_enabled`.
- Kept the target language and semester immutable.
- Connected the route to `update_class_settings()` through the public engagement interface.
- Added API request validation and response handling.

**Review:** Reviewer feedback identified inconsistencies between the initial `/me/classes/{id}` path and the technical specification's teacher-specific routes. The reviews also identified a missing-class negative test and concerns about PR size. I addressed route alignment in the later A6 work and kept the endpoint focused on request handling.

**Code quality:** The route is in `app/api/v1/classes.py`. The engagement service owns the settings update rules; the route owns the HTTP transaction. This belongs to the classroom settings process in the Engagement/Classroom DFD.

**Tests:** Added API tests for successful updates, unauthorized requests, invalid settings and missing classroom behavior. Evidence: [PR #224](https://github.com/COSC-499-W2026/capstone-project-team-12/pull/224/files).

## PR #233: Complete A5 classroom join acceptance coverage

**Requirement:** Issue #85. Classroom joining must correctly handle access-code validity, enrolment restrictions, voluntary rejoining and role-based conflicts. This PR expands the acceptance tests rather than implementing the original join endpoint.

**What I did:**
- Expanded `tests/backend/test_join_class.py` with tests for teacher-removed students, voluntary rejoining, teaching assistant conflicts and staff account restrictions.
- Added coverage for rejoining with expired or exhausted access codes.
- Verified that unsuccessful rejoin attempts preserve inactive enrolment states and do not incorrectly increment access-code usage counts.
- Verified that successfully joining a classroom selects the current class without changing the student's preferred Practice language.

**Review:** I corrected several AI-generated test assumptions that did not match our actual database models and constraints. The tests initially used an invalid teacher role, so I replaced it with `UserRole.STAFF`. I represented teaching assistant assignments using `ClassStaff` and `ClassStaffRole.ASSISTANT`, rather than an incorrect role assumption. I also corrected enrolment end-state fields and timestamps to satisfy database constraints.

For voluntary rejoining, I changed the tests to reuse the student's original access code and verify the resulting enrolment state and usage count. I also corrected the successful join assertion to ensure that `preferred_language` remains unchanged.

**Code quality:** Only `tests/backend/test_join_class.py` changed. The PR added 163 lines and removed 2, remaining well below the team size limit. It belongs to the student enrolment process in the Engagement/Classroom DFD.

**Tests:** All 25 join-class tests passed locally. Ruff lint and formatting passed, and backend CI passed. The tests cover both successful joining and failure cases involving inactive enrolments, invalid codes, role conflicts and usage-count preservation.

Evidence: [PR #233 files](https://github.com/COSC-499-W2026/capstone-project-team-12/pull/233/files) and [CI checks](https://github.com/COSC-499-W2026/capstone-project-team-12/pull/233/checks).

## PR #245: Classroom settings service (A6)

**Requirement:** Issue #84. Classroom settings changes need a reusable service that enforces ownership and protects archived classrooms.

**What I did:**
- Implemented `update_class_settings(class_id, actor, *, name?, leaderboard_enabled?)`.
- Allowed classroom owners to update supported settings.
- Rejected unauthorized assistants and unrelated users.
- Prevented updates to archived or nonexistent classrooms.
- Validated classroom names, including blank strings and NUL characters.
- Returned the updated classroom without changing its semester or target language.

**Review:** Reviewer feedback requested consistent `NOT_FOUND` handling for missing or inaccessible classrooms and stronger transaction-ownership tests. I reviewed these cases and expanded the service tests to check authorization, archived-state handling and invalid inputs.

**Code quality:** The implementation is in `app/engagement/classes.py`, exposed through the public engagement interface. It does not commit independently. This belongs to the classroom settings process in the Engagement/Classroom DFD.

**Tests:** Tested owner updates, assistant rejection, archived classrooms, blank names and inactive accounts. Evidence: [PR #245](https://github.com/COSC-499-W2026/capstone-project-team-12/pull/245/files).

## PR #252: Leave-class endpoint and core tests (A5)

**Requirement:** Issue #85 and the classroom withdrawal requirements. Students need to leave an actively enrolled classroom while keeping enrolment history and ending any open games associated with that classroom.

**What I did:**
- Implemented `POST /api/v1/me/classes/{class_id}/leave`.
- Connected the route to the engagement leave-class operation.
- Updated enrolment state when a student voluntarily leaves.
- Handled the student's current classroom selection after leaving.
- Integrated game-session cleanup so an active or paused class game does not remain open after the student leaves.
- Added core service and HTTP tests for the leave-class behavior.

**Review:** The main integration challenge was merging recent changes from `main` into the leave-class branch. I resolved conflicts in `app/api/v1/classes.py` and `app/engagement/interface.py`, preserving both the leave-class functionality and newer classroom endpoints.

I also investigated failures involving the database constraint `ck_class_enrollments_left_after_joining`. Some test fixtures generated `left_at` using the application clock while `joined_at` was generated by PostgreSQL, allowing the departure timestamp to be slightly earlier than the join timestamp. I corrected the affected test setup and checked the resulting constraints.

I also addressed affected start-game tests and a signed-URL assertion, then regenerated `docs/api/openapi.json` and `docs/architecture/dependency-graph.md`.

**Code quality:** The final branch contained 9 changed files, with 444 additions and 29 deletions, totaling 473 changed lines. It stayed within the team's PR limits. The route handles the HTTP transaction, while the engagement and game components retain their respective responsibilities.

The relevant DFD processes are student classroom withdrawal in Engagement and ending open game sessions in Game.

**Tests:** The PR included 13 core leave-class tests. I ran Ruff lint and formatting, mypy, the backend pytest suite, the OpenAPI check and the dependency-graph check after resolving conflicts.

Evidence: [PR #252 files](https://github.com/COSC-499-W2026/capstone-project-team-12/pull/252/files) and [CI checks](https://github.com/COSC-499-W2026/capstone-project-team-12/pull/252/checks).

## PR #253: Leave-class game-session tests (A5)

**Requirement:** Issue #85 and the session lifecycle requirements. Leaving a classroom must correctly end related open games without erasing previously completed work.

**What I did:**
- Expanded integration tests covering active and paused game sessions when a student leaves a classroom.
- Verified that open class games are abandoned.
- Verified that pending and in-progress session locations become skipped when their game is abandoned.
- Verified that completed locations remain completed and previously awarded points are preserved.
- Added coverage for interactions between classroom enrolment state and game-session state.

**Review:** This PR was stacked on #252, so changes to the leave-class implementation required synchronization of the test branch. I rebased the test branch on the updated leave-class branch and resolved conflicts in generated documentation and related code. I preserved the game-session lifecycle behavior introduced by other contributors rather than replacing it with test-specific logic.

**Code quality:** The PR focuses on tests rather than introducing another implementation of session cleanup. The game component remains responsible for ending open sessions, while the classroom route coordinates the operation within the same transaction. This belongs to the classroom withdrawal process in Engagement and the game-session lifecycle process in Game.

**Tests:** The integration tests cover active, paused, pending, in-progress and completed game states, including preservation of completed results. Evidence: [PR #253](https://github.com/COSC-499-W2026/capstone-project-team-12/pull/253/files) and [CI checks](https://github.com/COSC-499-W2026/capstone-project-team-12/pull/253/checks).

## PR #260: Teacher routes and access-code expiry alignment (A6)

**Requirement:** Issue #84. Classroom management endpoints and access-code behavior must match the technical specification and shared API conventions.

**What I did:**
- Aligned classroom-management API paths with teacher-specific route conventions.
- Addressed access-code expiration handling during classroom creation.
- Updated the relevant API and service integration.
- Regenerated the OpenAPI document to reflect the updated routes.

**Review:** Earlier classroom creation and settings PRs had identified route inconsistencies between `/me/classes` and the teacher-specific paths in the specification. I consolidated the alignment work here to avoid repeating changes across the existing service implementations.

**Code quality:** The changes preserve the distinction between API routing and engagement business logic. The relevant DFD processes are classroom creation, classroom settings and access-code management.

**Tests:** Verified the affected API paths, request handling and generated OpenAPI specification. Evidence: [PR #260](https://github.com/COSC-499-W2026/capstone-project-team-12/pull/260/files).

## PR #262: Teacher and assistant classroom listing (A6)

**Requirement:** Issue #84. Teachers and classroom assistants need to retrieve classrooms they manage, including archived classrooms where appropriate.

**What I did:**
- Implemented `list_staff_classes()` to retrieve classrooms where a user is an owner or assistant.
- Included archived classrooms in the listing.
- Applied consistent ordering to the returned results.
- Added the corresponding teacher classroom listing endpoint.
- Added integration tests for role-based visibility and classroom listing behavior.

**Review:** During CI, a Ruff `E501` error identified an overlong documentation line in `app/engagement/interface.py`. I reformatted the interface docstring to satisfy the repository's 100-character line limit. I also synchronized the branch with the latest classroom API and engagement interface changes.

**Code quality:** The listing service is read-only and does not commit or flush the database session. It is exposed through the public engagement interface, with the API route responsible for authentication and response formatting. This belongs to classroom lookup and management in the Engagement/Classroom DFD.

**Tests:** Added `tests/backend/test_teacher_class_listing.py` covering classroom visibility and role-based listing behavior. Evidence: [PR #262](https://github.com/COSC-499-W2026/capstone-project-team-12/pull/262/files) and [CI checks](https://github.com/COSC-499-W2026/capstone-project-team-12/pull/262/checks).

## PR #263: Generate additional classroom access codes (A6)

**Requirement:** Issue #84. Teachers and authorized assistants need to generate new access codes for an existing classroom after its initial creation.

**What I did:**
- Added the API functionality for generating additional classroom access codes.
- Reused the existing `generate_access_code()` engagement service rather than implementing code generation again in the route.
- Preserved role-based authorization and classroom-state validation.
- Added endpoint tests for successful and unsuccessful code generation.

**Review:** This PR overlapped with other classroom API changes, requiring merge conflict resolution in `app/api/v1/classes.py` and the engagement interface. I preserved the endpoints added by other PRs and regenerated the generated documentation after merging.

**Code quality:** The route delegates code generation to the engagement service. This avoids duplicating access-code validation, uniqueness handling and authorization checks. The relevant DFD process is classroom access-code management.

**Tests:** Added API-level coverage for access-code generation and invalid requests. Evidence: [PR #263](https://github.com/COSC-499-W2026/capstone-project-team-12/pull/263/files).

## PR #264: Deactivate classroom access codes (A6)

**Requirement:** Issue #84. Authorized classroom staff need to deactivate access codes so they cannot be used for future enrolments, without affecting students who have already joined.

**What I did:**
- Implemented `deactivate_access_code()` in the engagement component.
- Added `PATCH /api/v1/teacher/access-codes/{code_id}`.
- Allowed classroom owners and authorized assistants to deactivate codes.
- Preserved existing enrolments and access-code history.
- Added service and HTTP tests for successful deactivation, authorization failures and subsequent join attempts.

**Review:** Mia pointed out that a retry test alone did not establish that a deactivated access code would prevent a new student from joining. I added an end-to-end test that deactivates the code and then attempts to join with a different student. The expected result is `400 INVALID_ACCESS_CODE`.

I also added a disabled-account test for `ACCOUNT_DISABLED` and strengthened the authorization test by creating an unrelated classroom and assigning the outsider a staff role there. This ensures that managing another classroom does not grant permission to deactivate the target classroom's code.

I updated the public engagement interface documentation and resolved conflicts after the classroom listing and additional-access-code PRs merged.

**Code quality:** The final PR changed 6 files, with 471 additions and 3 deletions, totaling 474 changed lines. The API route delegates authorization and deactivation logic to the engagement service. The service changes the access-code state without deleting the row or independently committing.

This belongs to classroom access-code management in the Engagement/Classroom DFD.

**Tests:** The PR included 12 deactivation tests covering successful deactivation, invalid or inaccessible codes, unauthorized users, disabled accounts and join rejection after deactivation. Ruff, mypy and generated-document checks were used during verification.

Evidence: [PR #264 files](https://github.com/COSC-499-W2026/capstone-project-team-12/pull/264/files) and [CI checks](https://github.com/COSC-499-W2026/capstone-project-team-12/pull/264/checks).

## Other work this week

- **Stacked PR management:** I divided A6 into separate service, endpoint, listing and access-code management PRs. After prerequisite PRs merged, I updated dependent branches and resolved conflicts rather than leaving stale code or documentation in the stack.

- **Merge conflict resolution:** Several branches modified `app/api/v1/classes.py` and `app/engagement/interface.py`. I manually resolved conflicting implementation changes and regenerated `docs/api/openapi.json` and `docs/architecture/dependency-graph.md` with the repository utilities.

- **Database constraints:** I investigated intermittent failures involving `ck_class_enrollments_left_after_joining`, where application and database timestamps could differ. This highlighted the importance of testing against the actual PostgreSQL constraints rather than assuming timestamp ordering in fixtures.

- **Architectural boundaries:** I kept classroom creation, access-code generation, settings updates and enrolment behavior in Engagement, while game-session cleanup remained in Game. The API layer coordinates operations without duplicating business logic.

- **Code review:** I responded to reviewer concerns about API paths, authorization, missing negative cases, transaction ownership, test assumptions, and end-to-end verification. In particular, the deactivation tests now verify that a different student cannot join with a deactivated code.

- **Code quality and CI:** I used Ruff, mypy, pytest, OpenAPI validation and dependency-graph checks while integrating the branches. I checked changed-line counts and file counts to keep PRs within team review limits.

## Challenges and lessons learned

The biggest challenge this week was coordinating many dependent PRs while other team members were merging changes to the same files. A PR could pass locally and later encounter conflicts or stale generated documentation after its base branch changed. I learned to synchronize dependent branches, regenerate documentation and rerun checks before considering a PR ready.

Another challenge was ensuring that tests verified actual behavior instead of only exercising a service function. The review of access-code deactivation showed why an end-to-end test is necessary: changing an `is_active` field is not sufficient evidence that a new student will be rejected when attempting to join.

I also learned to inspect test fixtures carefully when working with database-generated timestamps and constraints. Tests should reflect the database model and real authorization relationships, especially for staff roles, classroom assistants and inactive enrolments.

## Next week

With the A5 and A6 classroom lifecycle work merged, I plan to continue the A12 engagement and points functionality. I will also keep PR descriptions, merge statuses and test evidence current so that the weekly log reflects completed work rather than outdated submitted statuses.
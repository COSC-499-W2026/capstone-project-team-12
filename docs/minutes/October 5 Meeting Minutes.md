# COSC 499 Meeting Minutes
Date: October 5th, 2026
Time: 8:00pm - 9:00pm

## 1. Attendees:
Kaedyn    Mia    Eddy

## 2. Agenda
1. Confirm everyone's tasks for the week
2. Review contract

## 3. Minutes

### Changes to Team Contract
- PR reviews are no longer conducted in 2 different groups. Each team member has 2 reviewers assigned to them who are responsible for reviewing their PRs. These PRs should at the least be acknowledged within 24 hours; they do not need to be completed within 24 hours. If you are unable to complete a PR review on time, please say so in the group Discord server.

- Contract has been corrected to require 2 reviews on PRs involving code changes, and 1 review on PRs for weekly logs and minutes.

- Changed description on where weekly notes are from the group drive to the `docs/weekly-logs` branch.

### Weekly Tasks 

#### Interfaces
- Issues [#10], [#12], [#13] should be completed as soon as possible so the team agrees on signatures and can implement functions from other components interfaces.

#### Core Schema and First Migration [#8]
- Mia is aiming to tackle the issue and complete on Tuesday.

- In the future, after this issue is merged, team members can create their own PRs for tables belonging to their respective components.

#### Authentication
- Kaedyn is responsible for issues [#68], [#69], and [#72], and at least one of issues [#70], [#71], and [#73]. Due to this being a large workload, assistance on one or two of the issues may be requested as the week progresses.

#### TA Restriction
- The system should not allow a TA of a language class to enroll in a class of the same language. The team decided to enforce this on the service layer, which must be done in both Kaedyn and Fabiha's components.

#### Updates to the Spec

- Email and username are unique regardless of upper/lower case.
- `game_sessions` gets the same `class_id, target_language` FK as enrolments.
- Users are never hard deleted, so FKs keep the default `ON DELETE`.
- Only one owner per classroom.

### Thursday Meeting Preparation
- The team should discuss and come prepared with questions to ask the TA questions regarind the project. 
- As of now, the only question discussed is if `docs/weekly-logs` should be merged into main.

### Additional Notes
- Ensure that you are only importing another components interface.
- Ensure that you are only performing one Alembic migration per PR.
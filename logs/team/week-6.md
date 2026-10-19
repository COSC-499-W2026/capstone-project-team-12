# Week 6 Team Log (Oct. 12 - Oct. 18)

Author: Kaedyn Hindley

This week, we decided to focus on expanding on the shared interfaces and individual components and authorization system, with the goal of beginning work on the core systems that will allow the game to function, while minimizing waiting on each other's work.

The work was distributed so that:

- Mia focused on assisting Kaedyn with the email system for account verification, password reset, and TA invitation (#175, #176, #183, #184, #185, #186), and after finishing that moved on to the foundations of the core game logic, such as starting both a private practice or class game session, and abandoning games in progress (#187, #188, #194). Mia also implemented CI files `utils/export_openapi.py` and `.github/workflows/ci.yml` that build the `docs/api/openapi.json` file and ensure that it is up to date so that changes to the API structure are more easily accessible during PR reviews (#179).

- Eddy focused on the storage service for the system (#177, ), allowing the system to write and find media files without their media URL revealing a location or answer. In addition, Eddy added locations and clues schemas to the shared models. Eddy also refactored the system so that interfaces always have their db parameter as the last required parameter in all of their signatures (#178). 

- Kaedyn focused on turning the functions in the accounts interface into FastAPI dependencies, allowing API endpoints to enforce role-based access (#180, #181), and on the sign-in system utilizing JWT for access tokens and rotating refresh tokens, with the goal of completing the basic authorization system. However, only the dependencies were finished on time.

- Fabiha focused on expanding the engagement component interface with the `EngagementResult` that is used by Eddy and Mia after processing a location's result (#190). Fabiha also implemented the join class endpoint, classroom creation and listing, and access code creation and deletion (#193, #201, #202, #203, #204). 

We considered alternative workload distributions such Kaedyn implementing the email system as originally planned, but due to the urgency of finishing the FastAPI dependencies and sign-in system, Mia offered to assist.

Based on the 15 merged PRs, we mostly met this goal because important shared systems for core user interactions were implemented:

- **Accounts**: email system and user authentication dependencies. The goal for user sign-in was not met, however.
- **Engagement**: shared interface for points/achievement tracking, as well as classroom and access code creation
- **Content**: Media saving, locating, and schemas for locations and clues
- **Game**: Creation and abandoning of games, carry over of locations

The team collectively did the following to keep the code base free of repeated logic and tangled dependencies:

- **Role-based auth dependencies (#180,#181)**: The old accounts interface functions returned bools or a user, making other components that depended on these need to implement their own handling of outputs. With this approach, routes that use these only need to call them as dependencies, and they will handle themselves by raising errors should a user not be authenticated.

Now, the team can confirm that the code base does not contain any of the following:

- similar logic implemented multiple times
- many dependencies between modules
- changes in one component require changes elsewhere

With everyone's newly merged work for this week, the system DFD looks like the updated diagrams below. The specific changes include [changes]. We considered alternative data flows such as [list other considerations and why you did not go with them].

### DFD Level 0
![DFD Level 0](img/dfd_level0_context.png)

### DFD Level 1
![1.0 Auth & Accounts](img/dfd_level1_auth_accounts.png)
![2.0 Game Session](img/dfd_level1_game_session.png)
![3.0 Photo Verification](img/dfd_level1_photo_verification.png)
![4.0 Class & Engagement](img/dfd_level1_class_engagement.png)
![5.0 Content Management](img/dfd_level1_content_management.png)
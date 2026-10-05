# Week 4 Individual Log
### Kaedyn Hindley

(Sept. 20th - Oct. 4th)

 Tasks Completed:

## Issue [#2]:
For the project proposal, I drafted a basic outline for the proposal document, and filled in as many functional requirements as I could.
Suggestions by the rest of the team expanded upon, rephrased, and solidified the document into a more polished and finished version. My descriptions for functional requirements were sometimes too short or not descriptive enough, and I had not accounted for several tests. I as well did not include information on our front-end framework of choice.

## PR [#9]:
I focused on establishing the conventions that will be used throughout the API in order to prepare for future API feature development. This needed to be done and agreed upon early as to prevent later refactoring and possible errors caused by a lack of consistency. These conventions included:
- Standardized error format for responses
- API path, method id and field naming conventions
- HTTP methods used and for what
- HTTP status code standards and practices
- Pagination standards, defaults, and behavior

The reviews by Mia identified several problems and notable info from our technical specifications that I missed. Some of these included changing our pagination convention when requesting a page beyond the last one to return an empty item set instead of a `400` status code.

This work affects all areas of the DFD, as every component interacts with the API, and having consistency and standards in doing so prevents having to refactor for standardization later in the project.

This PR did not break anything else in the system as it is simply a markdown document that does not interact with any code or components.

Otherwise, my aim was to stay on top of PR reviews consistently and quickly to help move the foundational steps of the project forward, as well as work on the data flow diagram for the authentication system with assistance from Mia.

The work is written in `docs/api-conventions.md` so that it is easy to access and reference when needed.

The PR only affected the one file, and was 156 lines in total.

## Issues:
Project proposal - Issue [#2]
Agree on API conventions - Issue [#9]

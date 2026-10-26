# Week 6 Individual Log
### Kaedyn Hindley

For PR #215:
As part of requirement A1 (#68), the system must allow a user to create an account with an email address, a username and a password, optionally joining a class with an access code.
Therefore, I implemented/generated code so that users are able to supply an optional class access code when registering an account, enrolling them alongside sign-up.

The PR does not contain any temporary workaround because it utilizes the final implementation of access code validation and enrollment. No workaround function or data is used.

The PR only contains small functions that are 114 lines long.

Any values used by the function are from provided user input, and use pre-existing functions as to not repeat code. No new classes or modules were created in this PR.
In review, Eddy found that my feature contribution contained unnecessary function calls and excessive conditional logic in the form of unnecessary try/except blocks. Therefore, I did simply removed the blocks.

Now, I can confirm that my feature contribution does not contain any of the following:
- hardcoded values
- duplicate code
- dead code
- unnecessary function calls
- excessive conditional logic
- deep nesting
- high cyclomatic complexity
- classes/modules/functions with many unrelated responsibilities

The review by Eddy also identified that the test for inalid access codes was not being performed correctly, as the database did not properly rollback an aborted register call. Therefore, I did implemented Eddy's suggested change of using transaction_calls test fixture and manually rolling back the database in the test to simulate the real behaviour of the system.

This work is written in `app/accounts/register.py` because it shares the same responsibilities, and a new module would introduce unecessary complexity.

The functionality works correctly because the happy path tests involving a user registering with a valid access code, or no access code both passed.

I checked that these negative cases involving invalid access codes and they failed as expected.

Among these tests, all new tests are integration tests. They all focus on testing the expected behaviour between the register function and the class enrollment function.

These tests are included in the directory `tests/backend/register.py`.

This new test PR did not break anything else in the system because the entire test suite passed, showing that no previous tests failed due to the newly implemented feature.
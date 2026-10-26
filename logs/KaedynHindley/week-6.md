# Week 6 Individual Log
### Kaedyn Hindley

### For PR #180
As part of requirement A4, the system needs to enforce authentication of users, as well as access to certain API endpoints with system-level and class-level roles for users.

Therefore, I implemented code that creates FastAPI dependencies in the `accounts/interface.py` file that any endpoint can use as a parameter to enforce authentication and role-based access in them without having to do any logic handling themselves. This PR focused on the `get_current_user`, `require_class_role`, and `can_manage_content` functions.

The PR does not contain any temporary workaround because all of the functions use values obtained from the database directly, there are no hardcoded values. Tests are ran with valid database entries, and return all planned errors.

The PR only contains small functions that are 322 lines long.

The review by Mia identified that the test routes were initially created on the same FastAPI app, causing them to appear in OpenAPI files and be accessible to user. Therefore, I implemented a new FastAPI app within the test file and moved the routes over to match.

I implemented the `get_current_user` as a sub-dependency for the others so that routes did not have to include both, or re-implement its logic.
All data used in the functions are obtained from querying the database or from the provided parameters.
Each function is responsible for one check and one check only.

This ensured that my feature contribution does not contain any of the following:
- hardcoded values
- duplicate code
- dead code
- unnecessary function calls
- excessive conditional logic
- deep nesting
- high cyclomatic complexity
- classes/modules/functions with many unrelated responsibilities

This work is written in `src/app/accounts/interface.py` because it is the only accounts module file that other components are allowed to import by our system design. These functions are required for many endpoints in the system across all components, and so they must be in this shared file.

The functionality works correctly because the happy path tests involving teachers, assistants, and logged-in students all accessing their respective endpoints passed.

I wrote tests to cover abnormal situations involving user accounts being deactivated and they passed.

I checked that these negative cases involving users accessing restricted endpoints without being authenticated or without proper priviledges and they failed as expected.

Among these tests, all are unit tests. Integration tests are not required, as these functions do not depend on any other component in the system.

These tests are included in the directory `tests/backend/test_role_access.py`.

This new test PR did not break anything else in the system because it did not depend on other components of the system, nor did other components depend on it. The full test suite passes.

For PR #181
As part of requirement A4, the system needs to enforce authentication of users, as well as access to certain API endpoints with system-level and class-level roles for users.

Therefore, I implemented code that creates FastAPI dependencies in the `accounts/interface.py` file that any endpoint can use as a parameter to enforce authentication and role-based access in them without having to do any logic handling themselves. This PR implemented the `require_admin` function for future admin endpoints.

The PR does not contain any temporary workaround because all of the functions use values obtained from the database directly, there are no hardcoded values. Tests are ran with valid database entries.

The PR only contains small functions that are 93 lines long.

Mia identified a few missing test cases, such as a student account and deactivated admin attempting to access an admin endpoint.

I implemented the `get_current_user` function as a sub-dependency just as the functions in the above PR as to not repeat its function.
All data used in the functions are obtained from querying the database or from the provided parameters.
The function only checks for admin role and nothing else.

This ensured that my feature contribution does not contain any of the following:
- hardcoded values
- duplicate code
- dead code
- unnecessary function calls
- excessive conditional logic
- deep nesting
- high cyclomatic complexity
- classes/modules/functions with many unrelated responsibilities

This work is written in `src/app/accounts/interface.py` because it is the only accounts module file that other components are allowed to import by our system design. Despite our components being separated by the team member responsible, the admin component does not fit well into the accounts component, and should import the dependency like other components. 

The functionality works correctly because the happy path tests involving admins being granted access to admin-only endpoints passed.

I checked that these negative cases involving students and staff accounts being denied access to admin endpoints and they failed as expected.

Among these tests, all are unit tests. As with the above PR, this function does not depend on any other component, so integration testing was not needed.

These tests are included in the directory `tests/backend/test_role_access.py`.

This new test PR did not break anything else in the system because it did not depend on other components of the system, nor did other components depend on it. The full test suite passes.
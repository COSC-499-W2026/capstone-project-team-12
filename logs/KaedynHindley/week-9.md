# Week 9 Individual Log
### Kaedyn Hindley

### For PR #286:
As part of requirement A2, the system shall allow a user to sign in to a created account with email and password and stay signed in with rotating refresh tokens.

The basics of this were implemented in #254, but needed a follow-up.

Therefore, I implemented code so that refresh token cookies are properly protected and rotated, fixed issues involving database commits in functions that should not own them, and login timing security.

The PR does not contain any temporary workaround because it builds upon #254. There are no hard-coded values or functions to work around unimplemented componened/features.

The PR only contains small functions that are 282 lines long.

This PR only receives values directly from HTTP requests and the database, and uses frequently called functions in `tokens.py` to prevent duplicate code and unnecessary calls.

All functions relating to token management are in `accounts/tokens.py`. Any component needing these functions must import them from the module.

I can confirm this code does not contain the following:
- hardcoded values
- duplicate code
- dead code
- unnecessary function calls
- excessive conditional logic
- deep nesting
- high cyclomatic complexity
- classes/modules/functions with many unrelated responsibilities

This work is primarily written in `accounts/tokens.py` because it belongs to the account/auth component, as is responsible for all auth token management, so should all stay in one place.

The functionality works correctly because the happy path tests involving users logging, rotating, and having tokens revoked all passed.

I checked that these negative cases involving users using expired or revoked tokens and they failed as expected.

Among these tests, all are unit tests, and integration tests are not required because this PR builds upon #254. No other components are tested, only token functionality.

These tests are included in the directory `tests/backend/test_login.py`.

### For PR #314:
As part of requirement A2, the system must limit login attempts with rate-limiting.

Therefore, I implemented code so that all routes receive a global rate limit, and login attempts are limited to 5 per minute per (email, IP) pair.

The PR does not contain any temporary workaround because all limits and/or settings are obtained from the applications config. There are no hard-coded values or dummy values used anywhere.

The PR only contains small functions that are 111 lines long.

The code contains no hard-coded values, only values obtained from application config file.
Existing or future routes do not need to change in order for these changes to take effect, so no code gets duplicated or unnecessarily called.
The limiter is in its own module. It only covers functions/services related to rate-limiting.
Dependency graph CI ensures no high cyclomatic complexity.

I can confirm the code does not contain any of the following:
- hardcoded values
- duplicate code
- dead code
- unnecessary function calls
- excessive conditional logic
- deep nesting
- high cyclomatic complexity
- classes/modules/functions with many unrelated responsibilities

The review by Eddy identified several issues regarding test clients sending from the same IP, and SlowAPI not applying limiting properly on included routers. Therefore, I did implemented suggestions from his reviews, implementing a custom limiter system that did not use slowapi's middleware, and an autouse pytest fixture to fix leak between tests.

This work is primarily written in `app/services/limiter.py` because it does not belong to a single component; multiple routes from multiple components may need it.

This PR does NOT include tests for rate limiting, as they were moved into #328 to meet the 10 file limit on PRs. Instead it contains tests for the new config settings introduced.

I checked that these negative cases involving unset/invalid settings and they failed as expected.

Among these tests, all are unit tests, and integration tests are not needed, as only the config is tested.

These tests are included in the directory `tests/backend/conftest.py`.

For test PR #328 written to assess functionality in #314 's rate-limiting:

The functionality works correctly because the happy path tests involving up to 100 route calls per IP passed on undecorated routes, and 5 login calls for an email/IP pair passed.

I checked that these negative cases involving exceeding the global/login limit and they failed as expected.

Among these tests, `test_global_rate_limit_is_shared_by_all_routes` is a unit test and `test_login_rate_limit_per_email_and_ip` is an in integration test.

These tests are included in the directory `tests/backend/test_rate_limit.py`.
# Week 4 Individual Log
### Kaedyn Hindley

### For PR[#152]
As part of requirement A4 and A5, the system must enforce role-based access on users, preventing student accounts from performing/accessing Staff endpoints or admin panels, etc.
Therefore, I implemented code so that the system can query the database and determine if a given user has specfic class roles.

After reviewing the code generated, it suggested using db.query(...) to query the database, but SQLAlchemy documentation suggests using db.execute(...) instead, which was used over db.query(...).

The PR does not contain any temporary workaround because dumm and stub variables are kept to the stub functions. Any implemented functions get their data from the database and parameters passed in.

The PR only contains small functions that are 156 lines long.

I ensured data is obtained from parameters and database only to ensure no hardcoded values, there is very little code that can be duplicated across the functions, I used pytest-cov and mypy to ensure that there is no dead code or unnecessary calls, kept if/else statements to the bare minimum to avoid excessive login and nesting to the bare minimum, and each function covers only its purpose.

The review by Eddy identified that require_class_role needed a `class_id` passed into it, or else it would return for all classes. Therefore, `class_id` can be passed into it as a parameter, and the select statement takes it into account when querying.

This work is written in `accounts/interface.py` because other components, particularly Fabiha's class component, need to use these functions, and other components should only be importing the interface file, and no others from `accounts`.

#### Tests

The functionality works correctly because the happy path tests involving users with required roles or having staff roles not raising errors passed.

I checked that these negative cases involving users not having the required roles, invalid tokens on getting a user, and not being able to manage content and they failed as expected.

Among these tests, all are unit tests and none are integration tests, as there was no usage from other components.

This new test PR did not break anything else in the system because all previous tests in the suite passed.

### For PR[#167]
As part of requirement A1, the user needs to do be able to register for an account with an email, username, password, and optional access code.
Therefore, I implemented code so that users are able to register an account in the system with an API endpoint, although access codes could not be implemented as they were blocked at the time.

No code was generated for this functionality, and so any review performed was done so in the PR.

The PR does not contain any temporary workaround because there are no dummy or stub variables used outside of the tests: any changes to the functionality are purely additional in nature (such as classroom access codes).

The PR exceeds the suggested 200 lines changed, but remains under 400.

I made sure any variables are either obtained from the API endpoint or database to ensure no hardcoded values, split repeated code into their own functions and functionalities into their own modules to avoid duplicate code, used pytest-cov and mypy to ensure there was no dead code and unnecessary function calls, only used if/else statements where absoluteley necessary to avoid excessive logic and nesting. Each module implemented only covers related responsibilities: register.py only registers, password.py validates and hashes passwords.

The review by Eddy and Mia identified that the implementation was raising Exceptions and converting them into a generic AppError, which should not have been user facing, and provided no information about the errors being caught. Instead of this, the implementation now catches sqlalchemy `IntegrityError` and `InternalError` and maps them to applicable error codes.

They also identified several errors involving data entry in database, such as password_hashes being stored as utf-8 bytes when they should be converted to strings, and that the usernames are unique on lowercase.

This work is written in the `app/accounts` module, in the `register.py` and `password.py` files. This keeps their functionalities separated from not only the other components of the system, but from each other, so that `register.py` is not performing password related responsibilities without importing it.

#### Tests

The functionality works correctly because the happy path tests involving registering using a valid email, username, and password passed. This included calling the function directly, as well as through the API endpoint.

I wrote tests to cover abnormal situations involving poorly formatted usernames (< 3 chars, starting and ending with space characters), emails (regex statement checks for proper email structure), and duplicate entries in the database (one email/username tied to one account) and they passed.

I checked that these negative cases involving the poorly formatted information above and they failed as expected.

Among these tests, all are unit tests and integration tests are not required because the functionality for access codes has not yet been implemented, so there is no integration to be tests.

These tests are included in the directory `/tests/backend/test_register.py`.

This new test PR did not break anything else in the system because all other tests in the suite passed.

By using pytest-cov to check for test coverage, we found that our tests have 97% coverage.

Name                            Stmts   Miss  Cover   Missing
-------------------------------------------------------------
app\__init__.py                     0      0   100%
app\accounts\__init__.py            0      0   100%
app\accounts\interface.py           0      0   100%
app\accounts\password.py           18      1    94%   17
app\accounts\register.py           58      7    88%   62-70
app\api\__init__.py                 0      0   100%
app\api\v1\__init__.py              4      0   100%
app\api\v1\auth.py                 11      0   100%
app\config.py                      15      0   100%
app\content\__init__.py             0      0   100%
app\content\interface.py           37      0   100%
app\db.py                          16      4    75%   35-39
app\engagement\__init__.py          0      0   100%
app\engagement\interface.py         0      0   100%
app\errors.py                      88      0   100%
app\game\__init__.py                0      0   100%
app\game\interface.py               0      0   100%
app\main.py                        12      0   100%
app\models\__init__.py              2      0   100%
app\models\accounts.py             30      0   100%
app\models\columns.py              18      0   100%
app\models\engagement.py           44      0   100%
app\models\enums.py                23      0   100%
app\verification\__init__.py        0      0   100%
app\verification\interface.py       0      0   100%
-------------------------------------------------------------
TOTAL                             376     12    97%

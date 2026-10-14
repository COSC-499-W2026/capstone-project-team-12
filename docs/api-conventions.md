# API Conventions
## Base Path and Authentication
- All endpoints are under the base path `/api/v1`, except `GET /health`.
- Request and response bodies are JSON, except file uploads, which use `multipart/form-data`.
- Endpoints require an `Authorization: Bearer <access_token>` header, except register, login, refresh, verify-email, resend-verification, forgot-password, reset-password, set-password and `GET /health`.
- A missing, invalid or expired access token returns 401.

## General Naming
### URL/Path Naming
- Lowercase
- Nouns for resources
- Plural nouns for collections
- Use hyphens for multi-word names
- Use path parameters for identifying a single resource
- Path and query parameters should follow the same rules as JSON property naming (snake_case)
- Do not put implementation info such as database table names in URL

### HTTP Methods
|Method|Purpose|
|---|---|
|GET|Retrieve a resource|
|POST|Create resource or perform an action|
|PUT|Replaces a resource|
|PATCH|Partially update a resource|
|DELETE|Delete a resource|

### JSON Property Naming
Use snake_case.

Example:
```
{
  "user_id": "123",
  "first_name": "John",
  "last_name": "Smith",
  "created_at": "2026-10-03T10:00:00Z"
}
```

### Dates and Times
- Timestamps use ISO 8601 in UTC with a `Z` suffix, for example `2026-10-03T10:00:00Z`.
- Calendar dates (streaks, daily challenges) use `YYYY-MM-DD` in local time (America/Vancouver).

### Operation IDs
Every OpenAPI operation must define an explicit, unique operationId.

The operationId must use camelCase and follow the pattern:

|Operation|Convention|Example|
|---|---|---|
|List|list{Resource}|listUsers|
|Get|get{Resource}|getUser|
|Create|create{Resource}|createUser|
|Update|update{Resource}|updateUser|
|Delete|delete{Resource}|deleteUser|

If an API call performs a function not listed, its operationId must follow a verbNoun structure. For example:

```
POST /games/{id}/pause
```
operationId -> `pauseGame`

## Success Responses

|Code|When|Body|
|---|---|---|
|200|GET, PUT, PATCH, and action endpoints (e.g. `POST /games/{id}/pause`)|The resource or its updated state|
|201|POST that creates a resource|The created resource|
|204|DELETE|No body|

## Error Handling

### Standard Error Response Format
```
{
  "error": {
    "code": "USER_NOT_FOUND",
    "message": "The requested user does not exist.",
    "details": []
  }
}
```
- Code -> For the software
- Message -> Human readable. For clients and developers.
- Details -> (optional) Additional structured information

API error responses must not expose stack traces, database errors, internal hostnames, SQL statements, framework exception names, or other implementation details.

### HTTP Status Code Conventions

|Code|Meaning|Example|
|---|---|---|
|400|Bad Request|Verification or reset link is invalid or expired|
|401|Missing authorization|Missing, invalid or expired access token; wrong email or password|
|403|Forbidden|Authenticated but not permitted, or a tampered or expired signed media link (`FORBIDDEN`)|
|404|Not found|Resource doesn't exist|
|409|Conflict|Duplicate resource or state conflict|
|413|Content Too Large|Request body exceeds server limit|
|415|Unsupported media type|Media format not accepted, so reject the request|
|422*|Unprocessable Content|Request validation failed, or semantic errors|
|429|Too many requests|Rate limit exceeded|
|500|Internal server error|Unexpected failure|
|503|Service Unavailable|OCR is busy (`OCR_BUSY`, sent with a `Retry-After` header) or server is unavailable|

*All request validation errors (body, path and query parameters) return 422 with code `VALIDATION_ERROR` and the invalid fields in `details`. FastAPI sends 422 with `{"detail":[...]}` by default, so one exception handler converts it to the `{"error":{...}}` format.

### Application Error Code Naming
- Error codes should be written in UPPER_SNAKE_CASE.
- Error codes must be stable. Changes to human-readable messages must not require changes to error codes.
- HTTP status codes handle the category of the error, while the application error code identifies a specific reason.

### Raising Errors in Code
- Services and interface functions raise `AppError` from `app/errors.py` with an `ErrorCode`, for example `raise AppError(ErrorCode.CLASS_ARCHIVED, "This class is archived.")`. Routers do not build error responses or raise `HTTPException`.
- Each `ErrorCode` has one HTTP status (`STATUS` in `app/errors.py`), taken from the Error Codes table in the Technical Specification. A new code is added to `ErrorCode` and `STATUS` together.
- The handlers in `app/main.py` turn `AppError`, request validation errors (422 `VALIDATION_ERROR`), unknown routes (404 `NOT_FOUND`), wrong methods (405 `METHOD_NOT_ALLOWED`) and unexpected errors (500 `INTERNAL_ERROR`, logged but never shown to the client) into the format above.
- Headers that a code needs, such as `Retry-After` for `UPLOAD_IN_PROGRESS` and `OCR_BUSY`, are passed with `headers=`.

## Interface and Service Functions
Functions in a component's `interface.py` and in the shared services (`app/services`) that use the database take the session as `db: Session`, placed **after all required arguments**. Arguments with a default value come after `db`. The same rule applies to the other functions inside a component, such as `register_account(request, db)` and `username_exists(username, db)` in `app/accounts/register.py`.

```python
location_snapshot(location_id, language, difficulty, db)
require_class_role(user, class_id, required_role, db)
end_open_sessions(class_id, db, student_id=None)
```

## Pagination
Collection endpoints that may return a large number of resources must use pagination.

The API uses Offset/Page based pagination. The following query parameters are standard:

|Parameter|Type|Required|Default|Maximum|
|---|---|---|---|---|
|page|int|no|1|-|
|page_size|int|no|20|100|

- page -> page number
- page_size -> number of records requested

Page is a 1-based page number. The first page is `page=1`.

- If `page` is omitted, the API must use `page=1`.
- If `page_size` is omitted, the API must use the default size of 20.
- Requesting a page past the last page returns empty `items`.
- Requesting a page_size larger than the maximum of 100 returns a validation error (422).
- Invalid parameters (such as negative) must return a validation error (422).

Example:
```
GET /users?page=2&page_size=25
```

### Pagination Response
Paginated collection responses must use the following structure:
```
{
  "items": [],
  "page": 1,
  "page_size": 25,
  "total": 137,
  "has_next_page": true
}
```
|Property|Description|
|---|---|
|items|Resources returned for current page|
|page|Current 1-based page number|
|page_size|Effective page size|
|total|Total number of resources matching request filter|
|has_next_page|Indicates whether another page is available|

# API Conventions
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
Use snake_case
Example:
```
{
  "user_id": "123",
  "first_name": "John",
  "last_name": "Smith",
  "created_at": "2026-10-03T10:00:00Z"
}
```

### Operation IDs
Every OpenAPI operation must define an explicit, unique operationId
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
POST /session/{id}/pause
```
operationId -> `pauseSession`

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
|400|Bad Request|Invalid parameter, token, wrong password|
|401|Missing authorization|Missing authentication|
|403|Forbidden|Authenticated but not permitted|
|404|Not found|Resource doesn't exist|
|409|Conflict|Duplicate resource or state conflict|
|413|Content Too Large|Requst body exceeds server limit|
|415|Unsupported media type|Media format not accepted, so reject the request|
|422*|Unprocessable Content|Semantic errors|
|429|Too many requests|Rate limit exceeded|
|500|Internal server error|Unexpected failure|
|503|Service Unavailable|OCR or server is unavailable|

*FastAPI sends 422 with `{"detail":{...}}` by default. Convert to `{"error":{...}}` format with one exception handler, and decide if it is `400` or `422`.

### Application Error Code Naming
Error codes should be written in UPPER_SNAKE_CASE
Error codes must be stable. Changes to human-readable messages must not require changes to error codes.
HTTP status codes handle the category of the error, while the application error code identifies a specific reason

## Pagination
Collection endpoints that may return a large number of resources must use pagination.

The API uses Offset/Page based pagination.
The following query parameters are standard:
|Parameter|Type|Required|Default|Maximum|
|---|---|---|---|---|
|page|int|no|1|-|
|page_size|int|no|20|100|

- page -> page number
- page_size -> number of records requested

Page is a 1-based page number. The first page is `page=1`.

If `page` is omitted, the API must use `page=1`.
If `page_size` is omitted, the API must use the default size of 20.
Requesting an invalid page returns empty `items`.
Requesting a page_size larger than the maximum of 100 returns a bad request error.
Invalid parameters (such as negative) must return a bad request error.

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
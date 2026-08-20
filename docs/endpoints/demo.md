## Endpoint: <human name>

1. Purpose: <one caller-centered sentence>
2. HTTP method: <GET | POST | PATCH | PUT | DELETE>
3. Path: </api/v1/...>
4. Authentication/authorization: <who can do what to which resource?>
5. Path parameters: <name, type, meaning>
6. Query parameters: <name, type, default, min/max, meaning>
7. Request body:
   - required fields:
   - optional fields:
   - nullable fields:
   - forbidden/ignored fields:
8. Successful response:
   - status:
   - headers:
   - JSON body:
9. Errors:
   - condition -> status -> stable error code
10. Database tables:
    - reads:
    - writes:
11. Transaction boundary: <what must succeed or fail as one unit?>
12. External side effects: <provider calls, retry and idempotency behavior>
13. Test cases:
    - happy path
    - invalid/missing/extra input
    - boundary values
    - missing resource
    - conflict
    - unauthorized/forbidden
    - database/provider failure
    - response and database side effects
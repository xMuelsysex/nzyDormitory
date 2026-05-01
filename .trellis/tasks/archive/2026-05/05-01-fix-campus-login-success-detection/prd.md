# PRD: Fix Campus Login Success Detection

## Background

During live browser testing, the proxied campus login succeeded visually and loaded the campus account management center, but `/api/status` still returned `authenticated: false`.

The authenticated campus page contains menu text such as `修改密码`. The current login-page detector treats any page containing `密码` as a login page, so it rejects real authenticated pages that include password-management navigation.

## Goal

Make the backend recognize real authenticated campus pages while still rejecting failed login pages and captcha/credential errors.

## Requirements

- `CampusPortalClient.submit_login_page()` must mark the session authenticated when the upstream response is an authenticated campus page.
- Login-page detection must rely on actual login-form signals such as `UserPwd`, `InputCode`, `__EVENTVALIDATION`, or password input fields, not generic visible text such as `密码`.
- Failure detection must not reject authenticated pages merely because they contain generic password-management text.
- Unit tests must cover an authenticated page containing `修改密码` and `退出` as a success signal.
- Existing parser/proxy tests must keep passing.

## Acceptance Criteria

- A representative authenticated campus page with `修改密码`, account info, and `退出` is not classified as a login page.
- `_looks_like_authenticated_page()` accepts the observed `退出` signal.
- `python -m unittest discover -s backend/tests -p test_*.py -v` passes.

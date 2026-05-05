# Update campus portal WebVPN URL

## Goal

Replace the default campus portal endpoint that points to `10.80.34.137:92` with the WebVPN URL provided by the user, so new deployments use the accessible WebVPN login path by default.

## What I already know

* User requested changing `10.80.34.137:92` to `https://webvpn.njucm.edu.cn/http/webvpn34f6d2940beaaa8a549e2c772ae7c064/Default.aspx`.
* Existing defaults live in `backend/app/config/settings.py`.
* The application still supports overriding URLs through `CAMPUS_LOGIN_URL` and `CAMPUS_ELECTRICITY_URL`.
* The original `task.md` also references the old campus intranet host.
* After switching to WebVPN, the first login/proxy page renders blank and submitting username/password appears to do nothing.
* The WebVPN flow for `https://webvpn.njucm.edu.cn/http/webvpn34f6d2940beaaa8a549e2c772ae7c064/Default.aspx` is two-step: first WebVPN/CAS login with student/work ID, then the underlying campus portal login with ID-card credentials. The app currently treats the first step as complete and returns too early.

## Assumptions

* The requested `Default.aspx` WebVPN URL should become the default login URL.
* The electricity URL should default to the WebVPN `Web/Student/FeeElect.aspx` path and remain independently overrideable through `CAMPUS_ELECTRICITY_URL`.

## Requirements

* Replace default settings that reference `10.80.34.137:92`.
* Preserve environment variable overrides for login and electricity URLs.
* Update any user-facing project notes that still instruct use of the old host when directly relevant.
* Update the deployment guide now that the project is available on GitHub at `https://github.com/gy1041/nzyDormitory.git`.
* Document Docker-based server deployment from a fresh Ubuntu host, including clone/build/run/update steps.
* Fix the proxied WebVPN login page so the user can see and submit the first login page instead of a blank page.
* Support the two-step WebVPN login flow: after the first WebVPN/CAS login succeeds, continue proxying the second underlying campus portal login page instead of marking the app authenticated.
* Keep the change minimal and avoid unrelated refactors.

## Acceptance Criteria

* [ ] `rg "10.80.34.137:92"` no longer finds runtime default configuration references.
* [ ] Settings tests pass.
* [ ] Existing campus portal tests still pass.
* [ ] README includes GitHub clone and Docker Compose deployment commands.
* [ ] README explains persistent data storage and the WebVPN portal URL environment variables.
* [ ] `/portal/login` rewrites the WebVPN page and same-host resources sufficiently for login form rendering and submission.
* [ ] Regression tests cover WebVPN relative asset/action patterns that caused blank/no-op login behavior.
* [ ] After first-step WebVPN/CAS success, `POST /portal/login` returns the rewritten second-step portal login page when the response is still a login form, and only redirects to `/?login=success` after the underlying campus portal session is actually authenticated.
* [ ] Regression tests cover a WebVPN/CAS-to-portal two-step login flow.

## Definition of Done

* Tests pass for the affected backend settings and portal behavior.
* Implementation stays scoped to URL defaults/documentation only.
* Docker deployment documentation is actionable for a fresh server.
* WebVPN login proxy regression tests pass.
* Two-step WebVPN login regression tests pass.
* No git commit or branch operation is performed unless explicitly requested.

## Out of Scope

* Automatically filling or storing the user's student/work ID, password, ID-card number, or second-step credentials.
* Discovering or validating live WebVPN endpoints.
* Changing production secrets or server configuration.

## Technical Notes

* Relevant spec context: `.trellis/spec/backend/index.md`.
* GitHub remote discovered from `git remote -v`: `https://github.com/gy1041/nzyDormitory.git`.

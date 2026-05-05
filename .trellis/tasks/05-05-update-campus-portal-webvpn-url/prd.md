# Update campus portal WebVPN URL

## Goal

Replace the default campus portal endpoint that points to `10.80.34.137:92` with the WebVPN URL provided by the user, so new deployments use the accessible WebVPN login path by default.

## What I already know

* User requested changing `10.80.34.137:92` to `https://webvpn.njucm.edu.cn/http/webvpn34f6d2940beaaa8a549e2c772ae7c064/web/auths/index.aspx`.
* Existing defaults live in `backend/app/config/settings.py`.
* The application still supports overriding URLs through `CAMPUS_LOGIN_URL` and `CAMPUS_ELECTRICITY_URL`.
* The original `task.md` also references the old campus intranet host.

## Assumptions

* The requested WebVPN URL should become the default login URL.
* The electricity URL should no longer default to the old `10.80.34.137:92` host. If a separate WebVPN electricity path is unavailable, default it to the provided WebVPN page and let environment variables override it for advanced deployments.

## Requirements

* Replace default settings that reference `10.80.34.137:92`.
* Preserve environment variable overrides for login and electricity URLs.
* Update any user-facing project notes that still instruct use of the old host when directly relevant.
* Keep the change minimal and avoid unrelated refactors.

## Acceptance Criteria

* [ ] `rg "10.80.34.137:92"` no longer finds runtime default configuration references.
* [ ] Settings tests pass.
* [ ] Existing campus portal tests still pass.

## Definition of Done

* Tests pass for the affected backend settings and portal behavior.
* Implementation stays scoped to URL defaults/documentation only.
* No git commit or branch operation is performed unless explicitly requested.

## Out of Scope

* Adding a new WebVPN login flow.
* Discovering or validating live WebVPN endpoints.
* Changing production secrets or server configuration.

## Technical Notes

* Relevant spec context: `.trellis/spec/backend/index.md`.

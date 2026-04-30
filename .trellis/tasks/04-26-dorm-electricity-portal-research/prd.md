# Research real campus portal login and electricity endpoints

## Goal

Establish verified facts for the authorized campus portal integration before more implementation changes.

## Scope

- Source login page: `http://10.80.34.137:92/Default.aspx`
- Electricity target currently assumed: `http://10.80.34.137:92/web/auths/index.aspx`
- App integration boundary: `backend/app/integrations/campus_portal.py`

## Required Findings

1. Login form fields, hidden fields, submit endpoint, method, and captcha field names.
2. Concrete failed-login signals from deliberately invalid credentials.
3. Concrete successful-login signals from an authorized valid login.
4. Whether successful login redirects, returns a portal home page, or requires selecting an app/menu item.
5. Actual self-service electricity page URL and any network requests used to load balance data.
6. HTML/text shape around electricity value, including labels, units, and room/building fields.
7. Session cookies required for collection, without recording cookie values.

## Evidence Rules

- Do not store real passwords, cookies, captcha values, session IDs, or full sensitive HTML.
- Record only sanitized field names, endpoint paths, status codes, redirect locations, page titles, and short redacted snippets.
- Invalid-credential testing is allowed only against this authorized dorm portal target.

## Exit Criteria

- Invalid login does not set authenticated success in the app.
- Valid login success has a verified signal stronger than “not a login page”.
- Collection target is verified from the real portal navigation/network flow.
- Parser tests use representative sanitized snippets from the verified electricity response.

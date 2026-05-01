# Live Campus Portal Login Observations

Observed on 2026-05-01 against `http://10.80.34.137:92/Default.aspx`.

## Response shape

- Server: `Microsoft-IIS/7.0`
- Framework headers: `X-AspNet-Version: 4.0.30319`, `X-Powered-By: ASP.NET`
- Login page is ASP.NET WebForms:
  - `form method="post" action="Default.aspx" id="form1"`
  - Hidden fields: `__VIEWSTATE`, `__EVENTVALIDATION`
  - Username input: `name="UserName" id="UserName"`
  - Password input: `name="UserPwd" id="UserPwd" type="password"`
  - Captcha input: `name="InputCode" id="InputCode"`
  - Captcha image: `src="CheckCode.aspx"`, click refreshes to `CheckCode.aspx?update=<random>`
  - Submit control: `input type="image" name="imgBtn" id="imgBtn"`

## Captcha response

`GET /CheckCode.aspx` returned:

- `Content-Type: image/Gif; charset=utf-8`
- `Set-Cookie: CheckCode=<captcha>; path=/`
- No `ASP.NET_SessionId` was observed in the unauthenticated GET responses.

Treat captcha handling as an authorization-sensitive site behavior. Do not submit automated login attempts without explicit user approval and valid user-provided credentials.

## Implementation implications

- Login submission must preserve current `__VIEWSTATE` and `__EVENTVALIDATION`.
- The backend form parser already discovers `UserName` and `UserPwd` by name heuristics.
- Manual proxy login should include the original `InputCode` and image submit fields produced by the browser form.
- Login page detection can safely key on structural signals present here:
  - `__EVENTVALIDATION`
  - `input name="UserPwd"`
  - `input name="InputCode"`
  - `input type="password"`
- Authenticated-page detection should avoid generic text such as `密码`, because post-login pages can include password-management menu entries.

## Authenticated navigation observations

After successful login, `POST /Default.aspx` returns `302 Location: /Web/Auths/Index.aspx` and sets `ASP.NET_SessionId`.

The authenticated shell is a frameset:

- `/Web/Auths/Top.aspx`
- `/Web/Auths/LeftMenu.aspx`
- `/Web/student/accountinfo.aspx`
- `/Web/Auths/Foot.aspx`

Useful authenticated-page signals:

- `Top.aspx` title `顶部`
- Text pattern `您好，[...] ... 欢迎您！`
- Logout link target `/njutcm_logout.aspx?...`
- Password-management link `../Student/PwdChange.aspx`
- Authenticated shell title `管理中心`

## Electricity page observations

The left menu defines the self-service electricity entry in JavaScript:

- Menu label: `自助购电`
- Target: `../Student/FeeElect.aspx`
- Absolute path: `/Web/Student/FeeElect.aspx`

`GET /Web/Student/FeeElect.aspx` is an ASP.NET WebForms page with:

- Hidden fields: `__VIEWSTATE`, `__EVENTVALIDATION`
- Card balance input: `txtSchoolCardBalance`
- Zone select: `ZoneID`
  - `1` = `仙林C区`
  - `2` = `仙林E区`
  - `3` = `仙林东苑`
  - `4` = `汉中学生宿舍`
  - `5` = `国教宿舍楼`
- House input: `txtHouse`
- Room input: `txtRoom`
- Query submit: `btnQuery` value `查询电量`
- Purchase amount input: `FeeAmtTxt`
- Purchase next-step submit: `btkOK` value `下一步`

For C-zone dorm rooms, the visible `C` prefix is rendered by `span#pZone`; `txtHouse` receives only the numeric building portion.

`POST /Web/Student/FeeElect.aspx` with the current hidden fields, selected `ZoneID`, `txtHouse`, `txtRoom`, and `btnQuery=查询电量` returns the queried room electricity balance in:

- `span#lblRoomMoney`

Do not submit `btkOK` when only reading electricity data; it starts the purchase flow.

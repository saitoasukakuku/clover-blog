# Registration approval design

## Context

The current `/register/` flow immediately creates a Django `User` and logs the
new user in after the form validates. The email field already exists, but it is
optional.

The approved direction is to make registration invitation-based:

- visitors first submit only an email address;
- no `User` is created before approval;
- the site owner reviews requests in a protected site page;
- after approval, the system emails a one-time registration code;
- the visitor uses the code to finish account creation.

## Goals

- Require an email address before any user can register.
- Prevent unapproved visitors from entering the `auth_user` table.
- Add a site-owned review page for superusers.
- Send an email registration code after approval.
- Keep the code single-use and time-limited.
- Let local development verify email content through the console email backend.
- Let production SMTP credentials come from environment variables only.

## Non-goals

- Do not add social login.
- Do not let ordinary staff users approve registrations in this iteration.
- Do not store SMTP passwords or email authorization codes in code, docs, or the
  database.
- Do not create a rich multi-role moderation system yet.
- Do not collect username, nickname, or password during the first request step.

## User flow

### Step 1: Request registration

`/register/` becomes the registration request page.

Anonymous visitors see one required field: email.

After a valid submission:

- the site creates or updates a `RegistrationRequest`;
- the request status becomes `pending`;
- no `User` is created;
- the page tells the visitor to wait for approval.

Already authenticated users who visit `/register/` are redirected to `/index/`.

### Step 2: Review requests

`/registration-requests/` is a protected review page.

Only superusers can enter it. Visitors and normal logged-in users cannot view
requests or perform review actions.

The page shows registration requests grouped by status:

- pending;
- approved;
- rejected;
- used.

Each pending request has POST-only actions:

- approve;
- reject.

### Step 3: Approve and email code

When a superuser approves a pending request:

- the system generates a random registration code;
- the database stores only a hash of the code;
- the raw code is used only for the outgoing email;
- the code expires after 7 days;
- the request status changes to `approved` only after the email is sent
  successfully.

If email sending fails, the request remains `pending` and the page shows an
error message so the superuser can retry later.

### Step 4: Complete registration

`/register/complete/` lets an approved visitor create the real account.

The form fields are:

- email;
- registration code;
- username;
- nickname;
- password;
- password confirmation.

After validation:

- the code must match the request email;
- the code must not be expired;
- the code must not have been used;
- the email must not already belong to a user;
- the username must be available.

If everything is valid, the system creates the `User`, saves the email and
nickname, marks the request as `used`, records `used_at`, logs the new user in,
and redirects to `/index/`.

## Data model

Add `RegistrationRequest`.

Fields:

- `email`: normalized email address.
- `status`: `pending`, `approved`, `rejected`, or `used`.
- `invite_code_hash`: hash of the current registration code; blank until
  approval.
- `code_expires_at`: expiration time for the current code; blank until approval.
- `approved_by`: nullable foreign key to the approving superuser.
- `reviewed_at`: time of approval or rejection.
- `used_at`: time the visitor completed registration.
- `created_at`: creation time.
- `updated_at`: last update time.

Rules:

- A registered email cannot submit a new registration request.
- An email with a `pending` request cannot create another pending request.
- An email with an unexpired `approved` request is told to check email instead
  of receiving a duplicate code.
- An email with an expired `approved` request can submit again; the same request
  returns to `pending`, and old code data is cleared.
- A `used` request is terminal.
- `approved_by` must point to a superuser when approval succeeds.

## Forms

Add `RegistrationRequestForm`.

It accepts only `email`. It is responsible for rejecting emails that already
belong to a user.

Add `CompleteRegistrationForm`.

It accepts email, registration code, username, nickname, password, and password
confirmation. It reuses Django password validation through the existing user
creation patterns.

Update the current registration form behavior so direct account creation is no
longer reachable from the first `/register/` page.

## Views and routes

Routes:

- `/register/`: registration request page, route name `register`.
- `/register/complete/`: final account creation page, route name
  `complete_registration`.
- `/registration-requests/`: review page, route name `registration_requests`.
- `/registration-requests/<request_id>/approve/`: POST-only approve action.
- `/registration-requests/<request_id>/reject/`: POST-only reject action.

The existing `register` URL name stays attached to `/register/` so existing
navigation links continue to work. The page content changes from direct account
creation to email request.

## Email behavior

Local development uses Django's console email backend so the registration email
appears in the terminal.

Production SMTP settings come from environment variables. The design expects
standard Django email settings such as:

- `EMAIL_BACKEND`;
- `EMAIL_HOST`;
- `EMAIL_PORT`;
- `EMAIL_HOST_USER`;
- `EMAIL_HOST_PASSWORD`;
- `EMAIL_USE_TLS`;
- `EMAIL_USE_SSL`;
- `DEFAULT_FROM_EMAIL`.

The visible sender should prefer the site owner or superuser account named
`白车轴草` when that account has an email address. If no owner email exists, the
system falls back to `DEFAULT_FROM_EMAIL`.

The approval email contains:

- the registration code;
- the expiration time;
- the `/register/complete/` link;
- a short note that the code is single-use.

## Review page UI

The review page should follow the existing template style and navigation.

It should include:

- total pending count;
- request email;
- request status;
- created time;
- reviewed time when available;
- expiration time when available;
- approved-by username when available;
- approve and reject buttons for pending requests.

The approve and reject buttons must be forms that submit with POST and CSRF.

## Error handling

Public request page:

- invalid email shows a form error;
- registered email shows a form error;
- pending request shows a waiting message;
- unexpired approved request shows a "check your email" message;
- expired approved request is reopened as pending after submission.

Completion page:

- wrong code shows a form error;
- expired code shows a form error;
- used code shows a form error;
- mismatched email and code shows a form error;
- duplicate username shows a form error;
- duplicate email shows a form error.

Review page:

- non-superusers receive a forbidden response or are redirected according to the
  existing auth style;
- GET requests cannot approve or reject;
- email failure leaves the request pending and shows a message.

## Testing

Add focused tests for:

- `/register/` requires email.
- submitting a valid email creates `RegistrationRequest` but not `User`.
- duplicate registered email is rejected.
- duplicate pending request does not create another row.
- expired approved request can be reopened as pending.
- normal users cannot view the review page.
- superusers can view the review page.
- approve action is POST-only.
- approving sends an email and stores only a code hash.
- email failure keeps the request pending.
- reject action is POST-only.
- rejected requests cannot complete registration.
- valid code creates a user, saves email and nickname, logs the user in, and
  marks the request used.
- wrong, expired, and used codes cannot create users.

## Rollout notes

This change requires a database migration.

Existing users are unaffected.

Production deployment must configure SMTP environment variables before real
approval emails can be sent. If SMTP is not configured correctly, approval will
fail visibly and the request will stay pending.

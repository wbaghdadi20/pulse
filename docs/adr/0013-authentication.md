# 13. Email accounts, a signed token per request, and no Django admin

- Status: accepted
- Date: 2026-11-12 (D8)

## Context

Until D8 anyone who could reach the API could create, change and delete every
monitor. Monitors need an owner, so callers need an identity: an account, a way
to log in, and something to send with each request.

## Options for the credential

1. **Django sessions and a cookie.** Built in and revocable. A cookie is sent by
   the browser on its own, so it needs CSRF protection, and API clients have to
   keep a cookie jar.
2. **DRF `TokenAuthentication`.** A random token stored in a table. Revocable, one
   query per request, no expiry unless added.
3. **`djangorestframework-simplejwt`.** JWTs with refresh tokens, rotation and a
   blacklist. More than is needed here, and the mechanics are hidden.
4. **A JWT made with PyJWT and a small DRF authentication class** (chosen). About
   forty lines, every one of them visible: `accounts/tokens.py` and
   `accounts/authentication.py`.

## Options for the user model

1. **Django's built-in `User`.** Logs in by username and carries staff, superuser,
   group and permission fields that nothing here uses.
2. **A custom model on `AbstractBaseUser`** (chosen): `email`, the password hash,
   `created_at`. Django cannot switch user models after the first migration, so
   this meant recreating the development database.

## Decision

- `accounts.User`: unique lowercase `email` (`uq_user_email`, and
  `ck_user_email_lowercase` so the database refuses any other case), the hash in
  `password_hash`, `created_at`.
- Hashing and checking are Django's: `PASSWORD_HASHERS` is argon2id only, signup
  runs `AUTH_PASSWORD_VALIDATORS`, and login calls `authenticate()`, which hashes a
  password even when the email is unknown. Both failures give one 401.
- A token is a JWT with `sub` (user id), `iat` and `exp`, signed with HS256 and
  `PULSE_JWT_SECRET`, valid `PULSE_JWT_TTL_SECONDS` (default 3600). Verification
  names the algorithm and requires `exp` and `sub`: a token cannot choose how it
  is checked.
- `PULSE_JWT_SECRET` is its own variable, not `DJANGO_SECRET_KEY`, and a startup
  check refuses one shorter than 32 characters.
- `IsAuthenticated` and `JWTAuthentication` are DRF's defaults. A view is public
  only by subclassing `core.views.PublicAPIView`. The authentication class sets
  `WWW-Authenticate: Bearer`; without it DRF answers 403 instead of 401.
- The user is loaded on every request, so a token whose account was deleted stops
  working at once.

## The Django admin is removed

`django.contrib.admin`, `sessions` and `messages`, and their middleware, are gone.

- **Why:** no model was registered in it. Its login form is a second way in, with
  cookies and sessions, that the token check and the rate limit (ADR 0016) do not
  cover. Without it the user model needs no staff or permission fields.
- **What it costs:** no web UI for looking at rows. `manage.py shell` and `psql`
  remain.
- **To bring it back**, none of which needs a database reset:
  1. On `User`: add `PermissionsMixin`, `is_staff` and `is_active`; add
     `create_superuser` to `UserManager`; make the migration.
  2. In settings: put `django.contrib.admin`, `sessions` and `messages` back in
     `INSTALLED_APPS`, `SessionMiddleware`, `AuthenticationMiddleware` and
     `MessageMiddleware` back in `MIDDLEWARE`, and the messages context processor
     in `TEMPLATES`.
  3. Add `path("admin/", admin.site.urls)` and register the models, with a
     `UserAdmin` written for this user model.
  4. Protect its login separately: it is not a DRF view, so no throttle applies.
     Rate limit it in middleware or with `django-axes`, or keep `/admin/` off the
     public network.

## Consequences

- A token cannot be revoked before it expires: there is no logout, and changing a
  password does not end existing tokens. The lifetime is the exposure, which is
  why it is bounded at one day by a startup check.
- Rotating `PULSE_JWT_SECRET` logs everyone out.
- One query per request loads the user. Query-count tests include it.
- `/signup` answers 409 for a registered email, which reveals who has an account.
  Hiding that needs email verification. Not built, and neither is password reset.
- Every endpoint added later is closed until someone makes it public on purpose.
  `tests/accounts/test_authentication.py` holds the list of public routes.

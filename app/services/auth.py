"""User accounts, password hashing and login rate limiting."""

from datetime import datetime, timedelta

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerifyMismatchError
from sqlalchemy import delete, func, or_, select
from sqlalchemy.orm import Session

from app.models.db_models import LoginAttempt, User
from app.models.enums import Role
from app.timeutils import utcnow

_hasher = PasswordHasher()

MIN_PASSWORD_LENGTH = 8


class AuthError(Exception):
    """Base class for authentication errors."""


class InvalidCredentials(AuthError):
    """Username or password is wrong, or the account is disabled."""


class LoginLocked(AuthError):
    """Too many failed attempts; login is temporarily blocked."""


class UserError(ValueError):
    """Invalid data when creating a user."""


def hash_password(password: str) -> str:
    """Hash a password with argon2."""
    return _hasher.hash(password)


def verify_password(password_hash: str, password: str) -> bool:
    """Return True if the password matches the stored hash."""
    try:
        return _hasher.verify(password_hash, password)
    except (VerifyMismatchError, InvalidHashError):
        return False


def create_user(db: Session, username: str, password: str, role: Role = Role.EMPLOYEE) -> User:
    """Create a user after validating the username and password length."""
    username = username.strip().lower()
    if not username or len(username) > 64 or not username.replace("_", "").replace(".", "").isalnum():
        raise UserError("El usuario solo puede contener letras, números, '.' y '_'.")
    if len(password) < MIN_PASSWORD_LENGTH:
        raise UserError(f"La contraseña debe tener al menos {MIN_PASSWORD_LENGTH} caracteres.")
    if db.scalar(select(User).where(User.username == username)):
        raise UserError("Ese usuario ya existe.")
    user = User(username=username, password_hash=hash_password(password), role=role)
    db.add(user)
    db.commit()
    return user


def is_locked(
    db: Session, username: str, ip: str, *, max_attempts: int, lockout_minutes: int, now: datetime | None = None
) -> bool:
    """True if the username or the IP has too many recent failed attempts."""
    since = (now or utcnow()) - timedelta(minutes=lockout_minutes)
    failures = db.scalar(
        select(func.count(LoginAttempt.id)).where(
            LoginAttempt.success.is_(False),
            LoginAttempt.created_at >= since,
            or_(LoginAttempt.username == username, LoginAttempt.ip == ip),
        )
    )
    return (failures or 0) >= max_attempts


def authenticate(
    db: Session,
    username: str,
    password: str,
    ip: str,
    *,
    max_attempts: int,
    lockout_minutes: int,
    now: datetime | None = None,
) -> User:
    """Check credentials, record the attempt, and return the user.

    Raises LoginLocked when rate limited and InvalidCredentials on a bad login.
    """
    now = now or utcnow()
    username = username.strip().lower()
    if is_locked(db, username, ip, max_attempts=max_attempts, lockout_minutes=lockout_minutes, now=now):
        raise LoginLocked
    user = db.scalar(select(User).where(User.username == username))
    ok = user is not None and user.is_active and verify_password(user.password_hash, password)
    db.add(LoginAttempt(username=username, ip=ip, success=ok, created_at=now))
    db.commit()
    if not ok or user is None:
        raise InvalidCredentials
    return user


def set_password(db: Session, username: str, password: str) -> User:
    """Replace a user's password and clear their failed login attempts."""
    username = username.strip().lower()
    user = db.scalar(select(User).where(User.username == username))
    if user is None:
        raise UserError("Ese usuario no existe.")
    if len(password) < MIN_PASSWORD_LENGTH:
        raise UserError(f"La contraseña debe tener al menos {MIN_PASSWORD_LENGTH} caracteres.")
    user.password_hash = hash_password(password)
    db.execute(delete(LoginAttempt).where(LoginAttempt.username == username, LoginAttempt.success.is_(False)))
    db.commit()
    return user


def verify_user_password(db: Session, user_id: int, password: str) -> bool:
    """Re-check the password of a logged-in user (used before sensitive changes)."""
    user = db.get(User, user_id)
    return user is not None and user.is_active and verify_password(user.password_hash, password)


def list_users(db: Session) -> list[User]:
    """All users, ordered by username."""
    return list(db.scalars(select(User).order_by(User.username)))


def set_user_active(db: Session, user_id: int, active: bool) -> User:
    """Enable or disable an account."""
    user = db.get(User, user_id)
    if user is None:
        raise UserError("Usuario no encontrado.")
    user.is_active = active
    db.commit()
    return user

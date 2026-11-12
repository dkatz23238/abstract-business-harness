"""User accounts: passwords for the UI, API keys for scripts, session tokens.

Everything lives in `<data-root>/users.json` (mode 0600). Passwords are
stored as scrypt hashes and API keys as sha256 hashes, so neither can be
read back: a new key is shown once, when it is created or rotated.

The first `serve` creates the store. `HARNESS_ADMIN_PASSWORD` is required
then and ignored afterwards. The admin's API key comes from
`HARNESS_ADMIN_API_KEY`, else from a pre-0.2 `<data-root>/api_key` file
(so existing clients keep working), else it is generated and printed once.

Session tokens are `s1.<user>.<expiry>.<epoch>.<hmac>`, signed with
`<data-root>/session_secret`. Changing a password bumps the user's epoch,
which invalidates every session that user had.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import re
import secrets
import threading
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path

from .config import ADMIN_USERNAME

USERS_FILENAME = "users.json"
SESSION_SECRET_FILENAME = "session_secret"
LEGACY_API_KEY_FILENAME = "api_key"

ROLES = ("user", "admin")
VISIBILITIES = ("siloed", "shared")
SESSION_TTL_S = 30 * 24 * 3600
MIN_PASSWORD_LEN = 8
USERNAME_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,31}$")

_SCRYPT_N, _SCRYPT_R, _SCRYPT_P = 2**14, 8, 1


class UserError(ValueError):
    """A request to the store that cannot be satisfied (bad name, duplicate...)."""


class BootstrapError(RuntimeError):
    """The store does not exist yet and cannot be created."""


def valid_username(name: str) -> bool:
    return bool(USERNAME_RE.match(name or ""))


def generate_api_key() -> str:
    return "bh_" + secrets.token_urlsafe(32)


def _b64(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def _unb64(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    digest = hashlib.scrypt(
        password.encode(), salt=salt, n=_SCRYPT_N, r=_SCRYPT_R, p=_SCRYPT_P, dklen=32
    )
    return f"scrypt${_SCRYPT_N}${_SCRYPT_R}${_SCRYPT_P}${_b64(salt)}${_b64(digest)}"


def verify_password(password: str, stored: str) -> bool:
    try:
        scheme, n, r, p, salt, digest = stored.split("$")
        if scheme != "scrypt":
            return False
        expected = _unb64(digest)
        actual = hashlib.scrypt(
            password.encode(), salt=_unb64(salt), n=int(n), r=int(r), p=int(p),
            dklen=len(expected),
        )
    except (ValueError, TypeError):
        return False
    return hmac.compare_digest(actual, expected)


def hash_api_key(key: str) -> str:
    return hashlib.sha256(key.strip().encode()).hexdigest()


@dataclass
class User:
    username: str
    role: str = "user"
    visibility: str = "siloed"
    password_hash: str = ""
    api_key_hash: str = ""
    token_epoch: int = 0
    created_at: float = field(default_factory=time.time)

    @property
    def is_admin(self) -> bool:
        return self.role == "admin"

    @property
    def is_bootstrap_admin(self) -> bool:
        return self.username == ADMIN_USERNAME

    def can_read(self, owner: str) -> bool:
        """May this user read `owner`'s threads? Writing is only ever your own."""
        return owner == self.username or self.is_admin or self.visibility == "shared"

    def public(self) -> dict:
        return {
            "username": self.username,
            "role": self.role,
            "visibility": self.visibility,
            "created_at": self.created_at,
            "can_view_others": self.is_admin or self.visibility == "shared",
        }


def _write_private(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    try:
        os.write(fd, text.encode())
    finally:
        os.close(fd)
    os.chmod(tmp, 0o600)
    os.replace(tmp, path)


class UserStore:
    def __init__(self, data_root: Path):
        self.data_root = Path(data_root).expanduser()
        self.path = self.data_root / USERS_FILENAME
        self._lock = threading.Lock()
        self._users: dict[str, User] = {}
        self._by_key: dict[str, str] = {}
        self._secret = b""

    # ---------------- persistence ----------------

    @property
    def exists(self) -> bool:
        return self.path.is_file()

    def load(self) -> None:
        data = json.loads(self.path.read_text())
        self._users = {
            row["username"]: User(**{k: v for k, v in row.items() if k in User.__dataclass_fields__})
            for row in data.get("users", [])
        }
        self._reindex()
        self._secret = self._load_secret()

    def _save(self) -> None:
        payload = {"version": 1, "users": [asdict(u) for u in self._users.values()]}
        _write_private(self.path, json.dumps(payload, indent=1) + "\n")
        self._reindex()

    def _reindex(self) -> None:
        self._by_key = {u.api_key_hash: u.username for u in self._users.values() if u.api_key_hash}

    def _load_secret(self) -> bytes:
        path = self.data_root / SESSION_SECRET_FILENAME
        if path.is_file():
            text = path.read_text().strip()
            if text:
                return _unb64(text)
        raw = secrets.token_bytes(32)
        _write_private(path, _b64(raw) + "\n")
        return raw

    # ---------------- accounts ----------------

    def all(self) -> list[User]:
        return sorted(self._users.values(), key=lambda u: (u.username != ADMIN_USERNAME, u.username))

    def get(self, username: str) -> User | None:
        return self._users.get(username)

    def create(
        self,
        username: str,
        password: str,
        *,
        role: str = "user",
        visibility: str = "siloed",
        api_key: str | None = None,
    ) -> tuple[User, str]:
        """Add a user. Returns the user and their API key (the only time it is visible)."""
        username = (username or "").strip().lower()
        if not valid_username(username):
            raise UserError(
                "username must be 1-32 characters: lowercase letters, digits, - or _, "
                "starting with a letter or digit"
            )
        _check_password(password)
        _check_choice("role", role, ROLES)
        _check_choice("visibility", visibility, VISIBILITIES)
        key = (api_key or "").strip() or generate_api_key()
        with self._lock:
            if username in self._users:
                raise UserError(f"user {username!r} already exists")
            if hash_api_key(key) in self._by_key:
                raise UserError("that API key is already in use")
            user = User(
                username=username,
                role=role,
                visibility=visibility,
                password_hash=hash_password(password),
                api_key_hash=hash_api_key(key),
            )
            self._users[username] = user
            self._save()
        return user, key

    def update(
        self,
        username: str,
        *,
        role: str | None = None,
        visibility: str | None = None,
        password: str | None = None,
    ) -> User:
        with self._lock:
            user = self._require(username)
            if role is not None:
                _check_choice("role", role, ROLES)
                if user.is_bootstrap_admin and role != "admin":
                    raise UserError(f"{ADMIN_USERNAME!r} is always an admin")
                user.role = role
            if visibility is not None:
                _check_choice("visibility", visibility, VISIBILITIES)
                user.visibility = visibility
            if password is not None:
                _check_password(password)
                user.password_hash = hash_password(password)
                user.token_epoch += 1
            self._save()
            return user

    def rotate_api_key(self, username: str) -> str:
        key = generate_api_key()
        with self._lock:
            user = self._require(username)
            user.api_key_hash = hash_api_key(key)
            self._save()
        return key

    def delete(self, username: str) -> None:
        with self._lock:
            user = self._require(username)
            if user.is_bootstrap_admin:
                raise UserError(f"{ADMIN_USERNAME!r} cannot be deleted")
            del self._users[username]
            self._save()

    def _require(self, username: str) -> User:
        user = self._users.get(username)
        if user is None:
            raise KeyError(username)
        return user

    # ---------------- credentials ----------------

    def check_password(self, username: str, password: str) -> User | None:
        user = self._users.get((username or "").strip().lower())
        if user is None or not user.password_hash:
            # Same cost as a real check, so timing does not reveal which names exist.
            verify_password(password, _dummy_hash())
            return None
        return user if verify_password(password, user.password_hash) else None

    def issue_session(self, user: User, ttl_s: int = SESSION_TTL_S) -> str:
        expiry = int(time.time()) + ttl_s
        body = f"s1.{user.username}.{expiry}.{user.token_epoch}"
        return f"{body}.{self._sign(body)}"

    def _sign(self, body: str) -> str:
        return _b64(hmac.new(self._secret, body.encode(), hashlib.sha256).digest())

    def resolve(self, token: str) -> User | None:
        """The user a bearer token (API key or session token) belongs to."""
        token = (token or "").strip()
        if not token:
            return None
        if token.startswith("s1."):
            return self._resolve_session(token)
        username = self._by_key.get(hash_api_key(token))
        return self._users.get(username) if username else None

    def _resolve_session(self, token: str) -> User | None:
        parts = token.split(".")
        if len(parts) != 5:
            return None
        _, username, expiry, epoch, sig = parts
        body = ".".join(parts[:4])
        if not hmac.compare_digest(sig, self._sign(body)):
            return None
        user = self._users.get(username)
        try:
            if user is None or int(expiry) < time.time() or int(epoch) != user.token_epoch:
                return None
        except ValueError:
            return None
        return user


_DUMMY_HASH: str | None = None


def _dummy_hash() -> str:
    global _DUMMY_HASH
    if _DUMMY_HASH is None:
        _DUMMY_HASH = hash_password(secrets.token_urlsafe(12))
    return _DUMMY_HASH


def _check_password(password: str) -> None:
    if not isinstance(password, str) or len(password) < MIN_PASSWORD_LEN:
        raise UserError(f"password must be at least {MIN_PASSWORD_LEN} characters")


def _check_choice(name: str, value: str, allowed: tuple[str, ...]) -> None:
    if value not in allowed:
        raise UserError(f"{name} must be one of: {', '.join(allowed)}")


@dataclass
class Bootstrap:
    store: UserStore
    # The admin API key to print, set only when this start generated it.
    generated_admin_key: str | None = None
    notes: list[str] = field(default_factory=list)


def open_store(
    data_root: Path,
    *,
    admin_password: str | None,
    admin_api_key: str | None = None,
) -> Bootstrap:
    """Load `users.json`, or create it with the admin account on first start.

    Raises BootstrapError before touching anything on disk when the store is
    missing and `admin_password` is not usable, so a failed first start
    leaves a pre-0.2 data root exactly as it was.
    """
    store = UserStore(data_root)
    if store.exists:
        store.load()
        if store.get(ADMIN_USERNAME) is None:
            raise BootstrapError(f"{store.path} has no {ADMIN_USERNAME!r} user")
        return Bootstrap(store)

    password = (admin_password or "").strip()
    if not password:
        raise BootstrapError(
            "HARNESS_ADMIN_PASSWORD must be set the first time the server starts "
            f"(no {store.path} yet). It becomes the password of the {ADMIN_USERNAME!r} user."
        )
    if len(password) < MIN_PASSWORD_LEN:
        raise BootstrapError(f"HARNESS_ADMIN_PASSWORD must be at least {MIN_PASSWORD_LEN} characters")

    notes: list[str] = []
    legacy = store.data_root / LEGACY_API_KEY_FILENAME
    key = (admin_api_key or "").strip()
    generated = None
    if key:
        notes.append(f"{ADMIN_USERNAME} api key: from HARNESS_ADMIN_API_KEY")
    elif legacy.is_file() and legacy.read_text().strip():
        key = legacy.read_text().strip()
        notes.append(f"{ADMIN_USERNAME} api key: kept from {legacy}")
    else:
        key = generate_api_key()
        generated = key

    store._secret = store._load_secret()
    store.create(ADMIN_USERNAME, password, role="admin", visibility="shared", api_key=key)
    if legacy.exists():
        legacy.unlink()
        notes.append(f"removed {legacy} (now the {ADMIN_USERNAME} API key)")
    notes.insert(0, f"created {store.path} with user {ADMIN_USERNAME!r}")
    return Bootstrap(store, generated_admin_key=generated, notes=notes)


class LoginThrottle:
    """Slows down password guessing: after a few failures, lock out for a while.

    Keyed by username and by client address separately, in memory. A restart
    clears it, which is fine for the threat it is meant for (scripted guessing
    through a public tunnel).
    """

    def __init__(self, max_failures: int = 5, window_s: float = 300.0, lockout_s: float = 300.0):
        self.max_failures = max_failures
        self.window_s = window_s
        self.lockout_s = lockout_s
        self._failures: dict[str, list[float]] = {}
        self._locked_until: dict[str, float] = {}

    def retry_after(self, *keys: str) -> float:
        now = time.time()
        return max((self._locked_until.get(k, 0) - now for k in keys), default=0.0)

    def failed(self, *keys: str) -> None:
        now = time.time()
        for key in keys:
            recent = [t for t in self._failures.get(key, []) if now - t < self.window_s]
            recent.append(now)
            self._failures[key] = recent
            if len(recent) >= self.max_failures:
                self._locked_until[key] = now + self.lockout_s
                self._failures[key] = []

    def succeeded(self, *keys: str) -> None:
        for key in keys:
            self._failures.pop(key, None)
            self._locked_until.pop(key, None)

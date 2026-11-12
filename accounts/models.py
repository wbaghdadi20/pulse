from typing import ClassVar

from django.contrib.auth.base_user import AbstractBaseUser, BaseUserManager
from django.db import models
from django.db.models import Q
from django.db.models.functions import Lower


class UserManager(BaseUserManager["User"]):
    @classmethod
    def normalize_email(cls, email: str | None) -> str:
        """Lowercase all of it. Django's version keeps the case of the part before @."""
        return (email or "").strip().lower()

    def create_user(self, email: str, password: str | None = None) -> "User":
        """With password=None the account exists but nobody can log in to it."""
        user = self.model(email=self.normalize_email(email))
        user.set_password(password)
        user.save()
        return user


class User(AbstractBaseUser):
    """Who owns a monitor. Hashing and checking the password are Django's."""

    email = models.EmailField()
    password = models.CharField(max_length=128, db_column="password_hash")
    created_at = models.DateTimeField(auto_now_add=True)
    last_login = None  # inherited from AbstractBaseUser, not kept

    objects: ClassVar[UserManager] = UserManager()

    USERNAME_FIELD = "email"

    class Meta:
        db_table = "users"
        constraints = [
            models.UniqueConstraint(fields=["email"], name="uq_user_email"),
            # A@x.com and a@x.com are one account, whichever code path wrote the row.
            models.CheckConstraint(
                condition=Q(email=Lower("email")), name="ck_user_email_lowercase"
            ),
        ]

    def __str__(self) -> str:
        return self.email

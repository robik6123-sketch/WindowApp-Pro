from dataclasses import dataclass
from datetime import datetime, timezone
import pydantic
from settings_models import UserSettingsStored, UserSettingsUpdate

USER_SETTINGS_COLLECTION = "user_settings"

class UserSettingsRepositoryError(Exception):
    """Базовий клас помилок репозиторію."""
    pass

class InvalidUIDError(UserSettingsRepositoryError):
    """Невалідний UID."""
    pass

class UserSettingsNotReadableError(UserSettingsRepositoryError):
    """Помилка читання з Firestore."""
    pass

class UserSettingsInvalidDocumentError(UserSettingsRepositoryError):
    """Невалідний або пошкоджений документ."""
    pass

class UserSettingsWriteError(UserSettingsRepositoryError):
    """Помилка запису у Firestore."""
    pass

@dataclass(frozen=True)
class UserSettingsRepositoryResult:
    settings: UserSettingsStored
    is_default: bool

class UserSettingsRepository:
    def __init__(self, db):
        self.db = db

    def _validate_uid(self, uid: str) -> None:
        if not isinstance(uid, str):
            raise InvalidUIDError("UID must be a string")
        if not uid.strip():
            raise InvalidUIDError("UID cannot be empty or whitespace-only")
        if '/' in uid:
            raise InvalidUIDError("UID cannot contain slashes")
        if len(uid) > 128:
            raise InvalidUIDError("UID cannot exceed 128 characters")

    def get_user_settings(self, uid: str) -> UserSettingsRepositoryResult:
        self._validate_uid(uid)

        # 1. Get snapshot from database
        try:
            doc_ref = self.db.collection(USER_SETTINGS_COLLECTION).document(uid)
            snapshot = doc_ref.get()
        except Exception as exc:
            raise UserSettingsNotReadableError("Failed to fetch settings from database") from exc

        # 2. Check if exists
        if not snapshot.exists:
            now_utc = datetime.now(timezone.utc)
            default_settings = UserSettingsStored(
                schema_version=1,
                updated_at=now_utc
            )
            return UserSettingsRepositoryResult(settings=default_settings, is_default=True)

        # 3. Call to_dict()
        try:
            data = snapshot.to_dict()
        except Exception as exc:
            raise UserSettingsNotReadableError("Failed to read document data") from exc

        # 4. Check that data is dict
        if data is None or not isinstance(data, dict):
            raise UserSettingsInvalidDocumentError("Document exists but data is invalid or not a dictionary")

        # 5. Pydantic validation
        try:
            settings = UserSettingsStored.model_validate(data)
        except pydantic.ValidationError as exc:
            raise UserSettingsInvalidDocumentError("Document does not match schema requirements") from exc

        return UserSettingsRepositoryResult(settings=settings, is_default=False)

    def save_user_settings(self, uid: str, settings: UserSettingsUpdate) -> UserSettingsStored:
        self._validate_uid(uid)
        if not isinstance(settings, UserSettingsUpdate):
            raise TypeError("settings must be UserSettingsUpdate")

        # Prepare storing model
        now_utc = datetime.now(timezone.utc)
        stored_dict = settings.model_dump()
        stored_dict["schema_version"] = 1
        stored_dict["updated_at"] = now_utc

        try:
            stored = UserSettingsStored.model_validate(stored_dict)
        except pydantic.ValidationError as exc:
            raise UserSettingsInvalidDocumentError("Failed to build valid stored settings model") from exc

        # Perform save
        try:
            doc_ref = self.db.collection(USER_SETTINGS_COLLECTION).document(uid)
            doc_ref.set(stored.model_dump(mode="python"))
        except Exception as exc:
            raise UserSettingsWriteError("Failed to write settings to database") from exc

        return stored

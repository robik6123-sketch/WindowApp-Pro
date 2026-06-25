import unittest
from datetime import datetime, timezone
import pydantic
from settings_models import UserSettingsStored, UserSettingsUpdate
from user_settings_repository import (
    UserSettingsRepository,
    UserSettingsRepositoryResult,
    UserSettingsRepositoryError,
    InvalidUIDError,
    UserSettingsNotReadableError,
    UserSettingsInvalidDocumentError,
    UserSettingsWriteError,
    USER_SETTINGS_COLLECTION
)

# Minimal Fake Firestore client matching instructions
class FakeDocumentSnapshot:
    def __init__(self, exists: bool, data=None, to_dict_exception=None):
        self.exists = exists
        self._data = data
        self._to_dict_exception = to_dict_exception

    def to_dict(self):
        if self._to_dict_exception is not None:
            raise self._to_dict_exception
        return self._data

class FakeDocumentReference:
    def __init__(self, doc_id, collection):
        self.doc_id = doc_id
        self.collection = collection

    def get(self):
        self.collection.get_calls.append(self.doc_id)
        if self.collection.read_exception is not None:
            raise self.collection.read_exception

        # If there's a preconfigured snapshot, use it
        if self.doc_id in self.collection.snapshots:
            return self.collection.snapshots[self.doc_id]

        # Default snapshot based on self.collection.store
        if self.doc_id in self.collection.store:
            return FakeDocumentSnapshot(exists=True, data=self.collection.store[self.doc_id])
        return FakeDocumentSnapshot(exists=False, data=None)

    def set(self, data, *args, **kwargs):
        self.collection.set_calls.append((self.doc_id, data, args, kwargs))
        if self.collection.write_exception is not None:
            raise self.collection.write_exception
        self.collection.store[self.doc_id] = data

        # Update snapshot to reflect set
        self.collection.snapshots[self.doc_id] = FakeDocumentSnapshot(exists=True, data=data)

class FakeCollectionReference:
    def __init__(self, name):
        self.name = name
        self.store = {}
        self.snapshots = {}
        self.get_calls = []
        self.set_calls = []
        self.document_calls = []
        self.read_exception = None
        self.write_exception = None

    def document(self, doc_id):
        self.document_calls.append(doc_id)
        return FakeDocumentReference(doc_id, self)

class FakeFirestoreClient:
    def __init__(self):
        self.collections = {}
        self.collection_calls = []

    def collection(self, name):
        self.collection_calls.append(name)
        if name not in self.collections:
            self.collections[name] = FakeCollectionReference(name)
        return self.collections[name]

class TestUserSettingsRepository(unittest.TestCase):

    def setUp(self):
        self.db = FakeFirestoreClient()
        self.repo = UserSettingsRepository(self.db)
        self.uid = "test_user_123"

    def test_uses_correct_collection(self):
        """1. Repository queries the correct collection name."""
        self.repo.get_user_settings(self.uid)
        self.assertIn(USER_SETTINGS_COLLECTION, self.db.collection_calls)

    def test_uid_used_as_document_id(self):
        """2. UID is correctly targeted as the document ID."""
        self.repo.get_user_settings(self.uid)
        coll = self.db.collection(USER_SETTINGS_COLLECTION)
        self.assertIn(self.uid, coll.document_calls)

    def test_get_valid_document_returns_stored_model(self):
        """3. An existing valid document is successfully parsed into UserSettingsStored."""
        coll = self.db.collection(USER_SETTINGS_COLLECTION)
        now = datetime.now(timezone.utc)
        coll.store[self.uid] = {
            "schema_version": 1,
            "currency": "UAH",
            "pricing": {"profiles": {"REHAU": 250.0}},
            "additional_costs": [],
            "commercial": {"markup_rate": 10.0, "discount_rate": 0.0},
            "tax_profile": {"name": "VAT", "rate": 0.20, "included_in_price": False},
            "updated_at": now
        }
        res = self.repo.get_user_settings(self.uid)
        self.assertIsInstance(res.settings, UserSettingsStored)
        self.assertEqual(res.settings.pricing.profiles["REHAU"], 250.0)
        self.assertEqual(res.settings.updated_at, now)

    def test_get_missing_document_returns_defaults(self):
        """4. Reading a missing document returns a model with defaults."""
        res = self.repo.get_user_settings("missing_uid")
        self.assertEqual(res.settings.currency, "UAH")
        self.assertEqual(res.settings.pricing.profiles, {})
        self.assertEqual(res.settings.commercial.markup_rate, 0.0)
        self.assertEqual(res.settings.tax_profile.name, "Без податку")

    def test_get_missing_document_sets_is_default_true(self):
        """5. Reading a missing document returns is_default=True."""
        res = self.repo.get_user_settings("missing_uid")
        self.assertTrue(res.is_default)

    def test_get_missing_document_does_not_write(self):
        """6. Reading a missing document does not perform any set() call to database."""
        self.repo.get_user_settings("missing_uid")
        coll = self.db.collection(USER_SETTINGS_COLLECTION)
        self.assertEqual(len(coll.set_calls), 0)

    def test_get_existing_document_sets_is_default_false(self):
        """7. Reading an existing document returns is_default=False."""
        coll = self.db.collection(USER_SETTINGS_COLLECTION)
        coll.store[self.uid] = {
            "schema_version": 1,
            "updated_at": datetime.now(timezone.utc)
        }
        res = self.repo.get_user_settings(self.uid)
        self.assertFalse(res.is_default)

    def test_save_creates_full_document(self):
        """8. Saving settings stores all updated fields in the database."""
        update = UserSettingsUpdate(
            commercial={"markup_rate": 15.0, "discount_rate": 5.0}
        )
        self.repo.save_user_settings(self.uid, update)
        coll = self.db.collection(USER_SETTINGS_COLLECTION)
        self.assertEqual(len(coll.set_calls), 1)
        stored_data = coll.store[self.uid]
        self.assertEqual(stored_data["commercial"]["markup_rate"], 15.0)

    def test_save_adds_schema_version_1(self):
        """9. Save adds schema_version=1 to the document."""
        update = UserSettingsUpdate()
        self.repo.save_user_settings(self.uid, update)
        coll = self.db.collection(USER_SETTINGS_COLLECTION)
        self.assertEqual(coll.store[self.uid]["schema_version"], 1)

    def test_save_adds_timezone_aware_updated_at(self):
        """10. Save adds a timezone-aware updated_at backend timestamp."""
        update = UserSettingsUpdate()
        stored = self.repo.save_user_settings(self.uid, update)
        self.assertIsNotNone(stored.updated_at.tzinfo)
        self.assertEqual(stored.updated_at.utcoffset().total_seconds(), 0)

    def test_save_does_not_leak_uid_in_document_body(self):
        """11. Save does not leak the UID inside the stored document dictionary."""
        update = UserSettingsUpdate()
        self.repo.save_user_settings(self.uid, update)
        coll = self.db.collection(USER_SETTINGS_COLLECTION)
        self.assertNotIn("uid", coll.store[self.uid])
        self.assertNotIn("owner_uid", coll.store[self.uid])

    def test_save_uses_set_without_merge(self):
        """12. Save performs a full set without passing merge options."""
        update = UserSettingsUpdate()
        self.repo.save_user_settings(self.uid, update)
        coll = self.db.collection(USER_SETTINGS_COLLECTION)
        doc_id, data, args, kwargs = coll.set_calls[0]
        self.assertEqual(doc_id, self.uid)
        self.assertEqual(args, ())
        self.assertEqual(kwargs, {})

    def test_save_empty_update_normalizes_defaults(self):
        """13. Save with empty payload stores normalized default values."""
        update = UserSettingsUpdate()
        self.repo.save_user_settings(self.uid, update)
        coll = self.db.collection(USER_SETTINGS_COLLECTION)
        stored = coll.store[self.uid]
        self.assertEqual(stored["pricing"]["profiles"], {})
        self.assertEqual(stored["tax_profile"]["name"], "Без податку")

    def test_zero_prices_preserved(self):
        """14. Zero prices are successfully preserved in save operations."""
        update = UserSettingsUpdate(
            pricing={"profiles": {"FREE_PROF": 0.0}}
        )
        self.repo.save_user_settings(self.uid, update)
        coll = self.db.collection(USER_SETTINGS_COLLECTION)
        self.assertEqual(coll.store[self.uid]["pricing"]["profiles"]["FREE_PROF"], 0.0)

    def test_false_included_in_price_preserved(self):
        """15. False in boolean settings is preserved as a boolean value."""
        update = UserSettingsUpdate(
            tax_profile={"included_in_price": False}
        )
        self.repo.save_user_settings(self.uid, update)
        coll = self.db.collection(USER_SETTINGS_COLLECTION)
        self.assertFalse(coll.store[self.uid]["tax_profile"]["included_in_price"])
        self.assertNotIsInstance(coll.store[self.uid]["tax_profile"]["included_in_price"], float)

    def test_save_returns_stored_model(self):
        """16. Save returns the generated UserSettingsStored model."""
        update = UserSettingsUpdate()
        res = self.repo.save_user_settings(self.uid, update)
        self.assertIsInstance(res, UserSettingsStored)

    def test_empty_uid_rejected(self):
        """17. Empty UID strings are rejected."""
        with self.assertRaises(InvalidUIDError):
            self.repo.get_user_settings("")
        with self.assertRaises(InvalidUIDError):
            self.repo.save_user_settings("", UserSettingsUpdate())

    def test_whitespace_uid_rejected(self):
        """18. Whitespace-only UIDs are rejected."""
        with self.assertRaises(InvalidUIDError):
            self.repo.get_user_settings("   ")

    def test_uid_with_slash_rejected(self):
        """19. UIDs containing a slash are rejected."""
        with self.assertRaises(InvalidUIDError):
            self.repo.get_user_settings("user/slash")

    def test_too_long_uid_rejected(self):
        """20. UIDs exceeding 128 characters are rejected."""
        long_uid = "a" * 129
        with self.assertRaises(InvalidUIDError):
            self.repo.get_user_settings(long_uid)

    def test_invalid_uid_does_not_call_firestore(self):
        """21. An invalid UID prevents any collection calls to Firestore."""
        with self.assertRaises(InvalidUIDError):
            self.repo.get_user_settings("user/slash")
        self.assertEqual(len(self.db.collection_calls), 0)

    def test_firestore_read_error_translated(self):
        """22. Database exceptions on document fetch are translated into UserSettingsNotReadableError."""
        coll = self.db.collection(USER_SETTINGS_COLLECTION)
        coll.read_exception = Exception("Firestore read timeout")
        with self.assertRaises(UserSettingsNotReadableError) as ctx:
            self.repo.get_user_settings(self.uid)
        self.assertIsInstance(ctx.exception.__cause__, Exception)

    def test_firestore_write_error_translated(self):
        """23. Database exceptions on save are translated into UserSettingsWriteError."""
        coll = self.db.collection(USER_SETTINGS_COLLECTION)
        coll.write_exception = Exception("Firestore write blocked")
        with self.assertRaises(UserSettingsWriteError) as ctx:
            self.repo.save_user_settings(self.uid, UserSettingsUpdate())
        self.assertIsInstance(ctx.exception.__cause__, Exception)

    def test_document_with_extra_fields_rejected(self):
        """24. Existing documents containing extra fields trigger UserSettingsInvalidDocumentError."""
        coll = self.db.collection(USER_SETTINGS_COLLECTION)
        coll.store[self.uid] = {
            "schema_version": 1,
            "updated_at": datetime.now(timezone.utc),
            "extra_unsupported_field": "exploit"
        }
        with self.assertRaises(UserSettingsInvalidDocumentError) as ctx:
            self.repo.get_user_settings(self.uid)
        self.assertIsInstance(ctx.exception.__cause__, pydantic.ValidationError)

    def test_document_missing_updated_at_rejected(self):
        """25. Existing documents missing updated_at trigger UserSettingsInvalidDocumentError."""
        coll = self.db.collection(USER_SETTINGS_COLLECTION)
        coll.store[self.uid] = {
            "schema_version": 1,
            "currency": "UAH"
        }
        with self.assertRaises(UserSettingsInvalidDocumentError):
            self.repo.get_user_settings(self.uid)

    def test_document_invalid_schema_version_rejected(self):
        """26. Existing documents with a schema version other than 1 are rejected."""
        coll = self.db.collection(USER_SETTINGS_COLLECTION)
        coll.store[self.uid] = {
            "schema_version": 2,
            "updated_at": datetime.now(timezone.utc)
        }
        with self.assertRaises(UserSettingsInvalidDocumentError):
            self.repo.get_user_settings(self.uid)

    def test_document_corrupted_pricing_rejected(self):
        """27. Existing documents with invalid pricing types (e.g. bools) are rejected."""
        coll = self.db.collection(USER_SETTINGS_COLLECTION)
        coll.store[self.uid] = {
            "schema_version": 1,
            "pricing": {"profiles": {"REHAU": True}},
            "updated_at": datetime.now(timezone.utc)
        }
        with self.assertRaises(UserSettingsInvalidDocumentError):
            self.repo.get_user_settings(self.uid)

    def test_corrupted_document_does_not_fallback_to_default(self):
        """28. Corrupted documents do not silently fall back to default settings."""
        coll = self.db.collection(USER_SETTINGS_COLLECTION)
        coll.store[self.uid] = {
            "schema_version": 1,
            "pricing": {"profiles": {"REHAU": True}},
            "updated_at": datetime.now(timezone.utc)
        }
        with self.assertRaises(UserSettingsInvalidDocumentError):
            self.repo.get_user_settings(self.uid)

    def test_existing_document_with_non_dict_data_rejected(self):
        """29. Existing documents with non-dictionary data trigger UserSettingsInvalidDocumentError."""
        coll = self.db.collection(USER_SETTINGS_COLLECTION)
        # Snapshot exists, but data is a string
        coll.snapshots[self.uid] = FakeDocumentSnapshot(exists=True, data="raw string")
        with self.assertRaises(UserSettingsInvalidDocumentError):
            self.repo.get_user_settings(self.uid)

    def test_save_rejects_raw_dict(self):
        """30. Save rejects raw dictionaries directly, and does not perform database writes."""
        with self.assertRaises(TypeError):
            self.repo.save_user_settings(self.uid, {"currency": "UAH"})
        self.assertEqual(self.db.collection_calls, [])

    def test_existing_document_with_none_data_rejected(self):
        """33. Existing document with None data is rejected."""
        coll = self.db.collection(USER_SETTINGS_COLLECTION)
        coll.snapshots[self.uid] = FakeDocumentSnapshot(
            exists=True,
            data=None,
        )
        with self.assertRaises(UserSettingsInvalidDocumentError):
            self.repo.get_user_settings(self.uid)

    def test_timezone_validation(self):
        """31. Stored settings timestamps are timezone-aware and have zero UTC offset."""
        update = UserSettingsUpdate()
        stored = self.repo.save_user_settings(self.uid, update)
        self.assertIsNotNone(stored.updated_at.tzinfo)
        self.assertEqual(stored.updated_at.utcoffset().total_seconds(), 0)

    def test_to_dict_error_translated_to_not_readable(self):
        """32. Exceptions during to_dict() serialization are translated to UserSettingsNotReadableError."""
        coll = self.db.collection(USER_SETTINGS_COLLECTION)
        coll.snapshots[self.uid] = FakeDocumentSnapshot(exists=True, to_dict_exception=Exception("Serialization crash"))
        with self.assertRaises(UserSettingsNotReadableError) as ctx:
            self.repo.get_user_settings(self.uid)
        self.assertIsInstance(ctx.exception.__cause__, Exception)

if __name__ == "__main__":
    unittest.main()

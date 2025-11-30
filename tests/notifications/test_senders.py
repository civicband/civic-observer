import pytest

from notifications.senders.base import NotificationSender


class TestNotificationSenderInterface:
    def test_base_class_is_abstract(self):
        """Test base sender class cannot be instantiated."""
        with pytest.raises(TypeError):
            NotificationSender()  # type: ignore

    def test_subclass_must_implement_send(self):
        """Test subclasses must implement send method."""

        class IncompleteSender(NotificationSender):
            def validate_handle(self, handle: str) -> bool:
                return True

        with pytest.raises(TypeError):
            IncompleteSender()  # type: ignore

    def test_subclass_must_implement_validate_handle(self):
        """Test subclasses must implement validate_handle method."""

        class IncompleteSender(NotificationSender):
            def send(self, channel, message: str) -> bool:
                return True

        with pytest.raises(TypeError):
            IncompleteSender()  # type: ignore

    def test_complete_subclass_can_be_instantiated(self):
        """Test complete subclass can be instantiated."""

        class CompleteSender(NotificationSender):
            def send(self, channel, message: str) -> bool:
                return True

            def validate_handle(self, handle: str) -> bool:
                return True

        sender = CompleteSender()
        assert sender is not None

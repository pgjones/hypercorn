import pytest
from wsproto.events import BytesMessage, TextMessage

from hypercorn.asgi.utils import (
    build_and_validate_headers,
    FrameTooLarge,
    raise_if_subprotocol_present,
    WebsocketBuffer,
)


def test_buffer() -> None:
    buffer_ = WebsocketBuffer(10)
    buffer_.extend(TextMessage(data="abc", frame_finished=False, message_finished=True))
    assert buffer_.to_message() == {"type": "websocket.receive", "bytes": None, "text": "abc"}
    buffer_.clear()
    buffer_.extend(BytesMessage(data=b"abc", frame_finished=False, message_finished=True))
    assert buffer_.to_message() == {"type": "websocket.receive", "bytes": b"abc", "text": None}


def test_buffer_frame_too_large() -> None:
    buffer_ = WebsocketBuffer(2)
    with pytest.raises(FrameTooLarge):
        buffer_.extend(TextMessage(data="abc", frame_finished=False, message_finished=True))


@pytest.mark.parametrize(
    "data",
    [
        (
            TextMessage(data="abc", frame_finished=False, message_finished=True),
            BytesMessage(data=b"abc", frame_finished=False, message_finished=True),
        ),
        (
            BytesMessage(data=b"abc", frame_finished=False, message_finished=True),
            TextMessage(data="abc", frame_finished=False, message_finished=True),
        ),
    ],
)
def test_buffer_mixed_types(data: list) -> None:
    buffer_ = WebsocketBuffer(10)
    buffer_.extend(data[0])
    with pytest.raises(TypeError):
        buffer_.extend(data[1])


def test_build_and_validate_headers() -> None:
    assert build_and_validate_headers([(b" Foo ", b" Bar ")]) == [(b"foo", b"Bar")]


def test_raise_if_subprotocol_present() -> None:
    with pytest.raises(Exception) as exc_info:
        raise_if_subprotocol_present([(b"sec-websocket-protocol", b"foo")])
    assert str(exc_info.value) == "Invalid header, use the subprotocol option instead"

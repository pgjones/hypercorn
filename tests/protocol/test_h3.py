from unittest.mock import Mock

import pytest
from aioquic.h3.connection import (
    FrameUnexpected,
    HeadersState,
    ProtocolError,
)

from hypercorn.protocol.h3 import H3Wrapper


@pytest.mark.parametrize("interim_count", [1, 2])
def test_informational_headers_do_not_advance_send_state(
    interim_count: int,
) -> None:
    quic = Mock()
    connection = H3Wrapper(quic)
    stream_id = 0

    for _ in range(interim_count):
        connection.send_headers(
            stream_id,
            [(b":status", b"103")],
            is_informational=True,
        )
        assert connection._stream[stream_id].headers_send_state == HeadersState.INITIAL

    connection.send_headers(stream_id, [(b":status", b"200")])
    assert connection._stream[stream_id].headers_send_state == HeadersState.AFTER_HEADERS

    # DATA and end-of-stream remain valid after the final response headers.
    connection.send_data(stream_id, b"response body", end_stream=True)


def test_informational_headers_cannot_end_stream() -> None:
    connection = H3Wrapper(Mock())

    with pytest.raises((ProtocolError,FrameUnexpected), match="Informational headers.*cannot end the stream"):
        connection.send_headers(
            0,
            [(b":status", b"103")],
            end_stream=True,
            is_informational=True,
        )


def test_informational_headers_cannot_follow_final_headers() -> None:
    connection = H3Wrapper(Mock())
    stream_id = 0

    connection.send_headers(stream_id, [(b":status", b"200")])

    with pytest.raises(
        (FrameUnexpected,ProtocolError),
        match="Informational headers cannot be sent after final headers",
    ):
        connection.send_headers(
            stream_id,
            [(b":status", b"103")],
            is_informational=True,
        )

    assert connection._stream[stream_id].headers_send_state == HeadersState.AFTER_HEADERS

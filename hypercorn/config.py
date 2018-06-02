import logging
import sys
from ssl import SSLContext
from typing import Optional, Union

BYTES = 1
SECONDS = 1.0


class Config:

    _access_log_target: Optional[str] = None
    _error_log_target: Optional[str] = None
    _port = 5000
    _ssl: Optional[SSLContext] = None

    access_log_format = "%(h)s %(r)s %(s)s %(b)s %(D)s"
    access_logger = logging.getLogger('hypercorn.access')
    debug = False
    error_logger = logging.getLogger('hypercorn.error')
    h11_max_incomplete_size = 16 * 1024 * BYTES
    host = '127.0.0.1'
    keep_alive_timeout = 5 * SECONDS
    use_reloader = False
    uvloop = False
    websocket_max_message_size = 16 * 1024 * 1024 * BYTES

    @property
    def port(self) -> int:
        return self._port

    @port.setter
    def port(self, value: Union[str, int]) -> None:
        self._port = int(value)

    @property
    def ssl(self) -> Optional[SSLContext]:
        return self._ssl

    @ssl.setter
    def ssl(self, value: Optional[SSLContext]) -> None:
        if value is not None:
            value.set_alpn_protocols(['h2', 'http/1.1'])
        self._ssl = value

    @property
    def access_log_target(self) -> Optional[str]:
        return self._access_log_target

    @access_log_target.setter
    def access_log_target(self, value: Optional[str]) -> None:
        if self.access_log_target == '-':
            self.access_logger.addHandler(logging.StreamHandler(sys.stdout))

    @property
    def error_log_target(self) -> Optional[str]:
        return self._error_log_target

    @error_log_target.setter
    def error_log_target(self, value: Optional[str]) -> None:
        if self.error_log_target == '-':
            self.error_logger.addHandler(logging.StreamHandler(sys.stderr))

import os
import time


class AccessLogAtoms(dict):

    def __init__(
            self,
            request: dict,
            response: dict,
            request_time: float,
    ) -> None:
        for name, value in request['headers']:
            self[f"{{{name.decode().lower()}}}i"] = value.decode()
        for name, value in response['headers']:
            self[f"{{{name.decode().lower()}}}o"] = value.decode()
        for name, value in os.environ.items():
            self[f"{{{name.lower()}}}e"] = value
        protocol = request.get('http_version', 'ws')
        client = request.get('client')
        if client is None:
            remote_addr = None
        elif len(client) == 2:
            remote_addr = f"{client[0]}:{client[1]}"
        elif len(client) == 1:
            remote_addr = client[0]
        else:  # make sure not to throw UnboundLocalError
            remote_addr = f"<???{client}???>"
        method = request.get('method', 'GET')
        self.update({
            'h': remote_addr,
            'l': '-',
            't': time.strftime('[%d/%b/%Y:%H:%M:%S %z]'),
            'r': f"{method} {request['path']} {protocol}",
            's': response['status'],
            'm': method,
            'U': request['path'],
            'q': request['query_string'].decode(),
            'H': protocol,
            'b': self['{Content-Length}o'],
            'B': self['{Content-Length}o'],
            'f': self['{Referer}i'],
            'a': self['{User-Agent}i'],
            'T': int(request_time),
            'D': int(request_time * 1000000),
            'L': f"{request_time:.6f}",
            'p': f"<{os.getpid()}>",
        })

    def __getitem__(self, key: str) -> str:
        try:
            if key.startswith('{'):
                return super().__getitem__(key.lower())
            else:
                return super().__getitem__(key)
        except KeyError:
            return '-'

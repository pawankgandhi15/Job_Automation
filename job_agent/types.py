"""Types at the JSON and public-HTTP boundaries; domain models follow in Phase 2."""

from typing import Any, Protocol

Record = dict[str, Any]


class HTTPClient(Protocol):
    def get(self, url: str, *, as_json: bool = False) -> Any: ...

from __future__ import annotations


class ApiError(Exception):
    """An error with a stable machine code and an HTTP status.

    Raising it inside a use case rolls the whole transaction back, which is what
    keeps a failed multi-step operation free of partial state.
    """

    def __init__(self, code: str, status: int, detail: str) -> None:
        super().__init__(detail)
        self.code = code
        self.status = status
        self.detail = detail

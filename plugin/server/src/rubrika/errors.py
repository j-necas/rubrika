from typing import Any


class ResearchError(Exception):
    """A safe, structured error that can be returned to the model."""

    def __init__(self, code: str, message: str, **details: Any):
        super().__init__(message)
        self.code, self.message, self.details = code, message, details

    def as_dict(self) -> dict:
        return {"ok": False, "error": {"code": self.code, "message": self.message,
                                       "details": self.details}}

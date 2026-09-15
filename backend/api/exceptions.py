"""Exceptions shared by API views and domain services."""

from http import HTTPStatus


class ApiError(ValueError):
    """An expected request error with its HTTP response status."""

    def __init__(self, message: str, status: int = HTTPStatus.BAD_REQUEST) -> None:
        super().__init__(message)
        self.status = int(status)

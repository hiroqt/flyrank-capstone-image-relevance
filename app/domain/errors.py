"""Domain errors. The service layer raises these; the HTTP layer maps them to clean 4xx responses."""


class DomainError(Exception):
    status_code = 400
    code = "bad_request"

    def __init__(self, message: str):
        super().__init__(message)
        self.message = message


class BadRequest(DomainError):
    status_code = 400
    code = "bad_request"


class NotFound(DomainError):
    status_code = 404
    code = "not_found"


class Conflict(DomainError):
    status_code = 409
    code = "conflict"

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from starlette.exceptions import HTTPException


class ErrorDetail(BaseModel):
    code: str
    message: str
    details: list[dict] | None = None


class ErrorResponse(BaseModel):
    error: ErrorDetail


class APIError(Exception):
    def __init__(self, status: int, code: str, message: str) -> None:
        self.status = status
        self.code = code
        self.message = message


def error_response(
    status: int, code: str, message: str, details: list | None = None
) -> JSONResponse:
    return JSONResponse(
        status_code=status,
        content={"error": {"code": code, "message": message, "details": details}},
    )


def install_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(APIError)
    async def api_error(request: Request, error: APIError) -> JSONResponse:
        return error_response(error.status, error.code, error.message)

    @app.exception_handler(RequestValidationError)
    async def validation_error(request: Request, error: RequestValidationError) -> JSONResponse:
        # Do not echo submitted attributes, credentials, or raw bodies in error responses.
        details = [{"loc": list(item["loc"]), "type": item["type"]} for item in error.errors()]
        return error_response(422, "validation_error", "Request validation failed", details)

    @app.exception_handler(HTTPException)
    async def http_error(request: Request, error: HTTPException) -> JSONResponse:
        response = error_response(error.status_code, "http_error", str(error.detail))
        response.headers.update(error.headers or {})
        return response

    @app.exception_handler(IntegrityError)
    async def integrity_error(request: Request, error: IntegrityError) -> JSONResponse:
        return error_response(409, "constraint_conflict", "Request conflicts with stored data")

    @app.exception_handler(SQLAlchemyError)
    async def database_error(request: Request, error: SQLAlchemyError) -> JSONResponse:
        return error_response(503, "database_unavailable", "Database operation unavailable")

import logging
from http import HTTPStatus

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.modules.notes.service import NoteNotFoundError

logger=logging.getLogger(__name__)

def request_id_for(request: Request)->str:
    return getattr(request.state, "request_id", "unavailable")

def route_template_for(request:Request)-> str:
    route=request.scope.get("route")
    template=getattr(route,"path",None)
    return template if isinstance(template,str) and template.startswith("/") else "<unmatched>"

def register_exception_handlers(app: FastAPI)->None:
    @app.exception_handler(NoteNotFoundError)
    async def note_not_found(request:Request, exc: NoteNotFoundError)->JSONResponse:
        return JSONResponse(
            status_code=404,
            content={
                "error":{
                    "code":"NOTE_NOT_FOUND",
                    "message":"Note was not found",
                    "details": {"note_id":str(exec.note_id)},
                    "request_id": request_id_for(request),}
            },
        )
    @app.exception_handler(RequestValidationError)
    async def validation_failed(request:Request, exc: RequestValidationError)->JSONResponse:
        all_errors=exc.errors()
        violations=[
            {
                "location": [str(part)[:100] for part in error["loc"][:10]],
                "message":str(error["msg"])[:300],
                "type": str(error["type"])[:100],
            }
            for error in all_errors[:20]
        ]
        return JSONResponse(
            status_code=422,
            content={
                "error":{
                    "code": "Request_Validation_Failed",
                    "message": "One or more request values were ianvalid",
                    "details": {
                        "violations": violations,
                        "truncated": len(all_errors)>len(violations)
                    },
                    "request_id": request_id_for(request)
                }
            }
        )
    @app.exception_handler(StarletteHTTPException)
    async def http_error(
        request: Request,
        exc: StarletteHTTPException,
    )->JSONResponse:
        try:
            phrase=HTTPStatus(exc.status_code).phrase
        except ValueError:
            phrase="Http error"
        return JSONResponse(
            status_code=exc.status_code,
            headers=exc.headers,
            content={
                "error":{
                    "code": f"HTTP_{exc.status_code}",
                    "message":phrase,
                    "details":{},
                    "request_id": request_id_for(request),
                }
            },
        )

    @app.exception_handler(Exception)
    async def unknown_error(
        request:Request,
        exc:Exception
    )->JSONResponse:
        logger.error(
            "unhandled_request_error method %s route %s request_id=%s",
            request.method,
            route_template_for(request),
            request_id_for(request),
            exc_info=(type(exc),exc, exc.__traceback__),

        )

        return JSONResponse(
            status_code=500,
            headers={"X-Request-ID": request_id_for(request)},
            content={
                "error": {
                    "code": "Internal_Server_Error",
                    "message": "The server could not complete the request",
                    "details":{},
                    "request_id": request_id_for(request),
                }
            },
        )
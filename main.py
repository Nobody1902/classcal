import logging

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from api.routes import router
from api.service import CalendarService

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = FastAPI(title="Easistent Timetable Calendar")
app.state.calendar_service = CalendarService()
app.include_router(router)


@app.exception_handler(Exception)
async def unexpected_error_handler(request: Request, exc: Exception):
    logger.exception("Unhandled error while processing %s", request.url.path)
    return JSONResponse(status_code=500, content={"detail": "Internal server error"})

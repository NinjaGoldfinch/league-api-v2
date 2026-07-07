from json import JSONDecodeError

from fastapi import HTTPException, Request, Response, status
from pydantic import BaseModel, ValidationError

ACCEPT_QUERY_JSON = '"application/json"'
ACCEPT_QUERY_HEADER = "Accept-Query"


def add_accept_query_header(response: Response) -> None:
    response.headers[ACCEPT_QUERY_HEADER] = ACCEPT_QUERY_JSON


async def parse_query_json[QueryModel: BaseModel](
    request: Request,
    model_type: type[QueryModel],
) -> QueryModel:
    content_type = request.headers.get("content-type")
    if content_type is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="QUERY requests must include Content-Type.",
            headers={ACCEPT_QUERY_HEADER: ACCEPT_QUERY_JSON},
        )

    media_type = content_type.split(";", maxsplit=1)[0].strip().lower()
    if media_type != "application/json":
        raise HTTPException(
            status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
            detail="QUERY requests must use Content-Type: application/json.",
            headers={ACCEPT_QUERY_HEADER: ACCEPT_QUERY_JSON},
        )

    try:
        payload = await request.json()
    except JSONDecodeError as exc:
        raise HTTPException(
            status_code=422,
            detail="QUERY request body must be valid JSON.",
            headers={ACCEPT_QUERY_HEADER: ACCEPT_QUERY_JSON},
        ) from exc

    try:
        return model_type.model_validate(payload)
    except ValidationError as exc:
        raise HTTPException(
            status_code=422,
            detail=exc.errors(),
            headers={ACCEPT_QUERY_HEADER: ACCEPT_QUERY_JSON},
        ) from exc

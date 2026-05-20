from typing import Any


def success(data: Any = None, message: str = "success") -> dict:
    return {"code": "OK", "message": message, "data": data}


def error(code: str, message: str, data: Any = None) -> dict:
    return {"code": code, "message": message, "data": data}

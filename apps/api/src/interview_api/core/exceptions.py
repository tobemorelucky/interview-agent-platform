class AppError(Exception):
    def __init__(self, code: str, message: str, status_code: int = 400):
        self.code = code
        self.message = message
        self.status_code = status_code
        super().__init__(message)


class AuthInvalidCredentialsError(AppError):
    def __init__(self, message: str = "邮箱或密码错误"):
        super().__init__(
            code="AUTH_INVALID_CREDENTIALS", message=message, status_code=401
        )


class AuthTokenExpiredError(AppError):
    def __init__(self, message: str = "令牌已过期"):
        super().__init__(code="AUTH_TOKEN_EXPIRED", message=message, status_code=401)


class AuthPermissionDeniedError(AppError):
    def __init__(self, message: str = "需要管理员权限"):
        super().__init__(
            code="AUTH_PERMISSION_DENIED", message=message, status_code=403
        )


class DuplicateEmailError(AppError):
    def __init__(self, message: str = "该邮箱已被注册"):
        super().__init__(code="DUPLICATE_EMAIL", message=message, status_code=409)


class DuplicateUsernameError(AppError):
    def __init__(self, message: str = "该用户名已被使用"):
        super().__init__(code="DUPLICATE_USERNAME", message=message, status_code=409)


class NotFoundError(AppError):
    def __init__(self, code: str = "NOT_FOUND", message: str = "资源不存在"):
        super().__init__(code=code, message=message, status_code=404)


class ValidationError(AppError):
    def __init__(self, message: str = "请求参数无效"):
        super().__init__(code="VALIDATION_ERROR", message=message, status_code=422)

from flask import jsonify


class ApiError(Exception):
    def __init__(self, status, code, message, details=None):
        super().__init__(message)
        self.status, self.code, self.message, self.details = status, code, message, details


def bad_request(message, details=None):
    return ApiError(400, "bad_request", message, details)


def unauthorized(message="authentication required"):
    return ApiError(401, "unauthorized", message)


def forbidden(message="you do not have permission to do this"):
    return ApiError(403, "forbidden", message)


def not_found(message="not found"):
    return ApiError(404, "not_found", message)


def conflict(message):
    return ApiError(409, "conflict", message)


def error_response(status, code, message, details=None):
    body = {"error": {"code": code, "message": message}}
    if details:
        body["error"]["details"] = details
    return jsonify(body), status


def register_error_handlers(app):
    @app.errorhandler(ApiError)
    def _api(e):
        return error_response(e.status, e.code, e.message, e.details)

    @app.errorhandler(404)
    def _404(e):
        return error_response(404, "not_found", "resource not found")

    @app.errorhandler(405)
    def _405(e):
        return error_response(405, "method_not_allowed", "method not allowed")

    @app.errorhandler(Exception)
    def _500(e):
        from werkzeug.exceptions import HTTPException
        if isinstance(e, HTTPException):
            return error_response(e.code or 500, (e.name or "error").lower().replace(" ", "_"), e.description or "")
        app.logger.exception("unhandled error")
        return error_response(500, "internal_error", "unexpected error")

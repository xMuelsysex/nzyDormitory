from __future__ import annotations


class AppError(Exception):
    code = "APP_ERROR"
    status = 500

    def __init__(self, message: str):
        super().__init__(message)
        self.message = message


class ValidationError(AppError):
    code = "VALIDATION_ERROR"
    status = 400


class AuthenticationError(AppError):
    code = "AUTHENTICATION_ERROR"
    status = 401


class PortalFetchError(AppError):
    code = "PORTAL_FETCH_ERROR"
    status = 502


class PortalParseError(AppError):
    code = "PORTAL_PARSE_ERROR"
    status = 502


class PersistenceError(AppError):
    code = "PERSISTENCE_ERROR"
    status = 500


class EmailDeliveryError(AppError):
    code = "EMAIL_DELIVERY_ERROR"
    status = 502


class SchedulerError(AppError):
    code = "SCHEDULER_ERROR"
    status = 500

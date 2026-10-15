import pytest

from onedep_lib.exceptions import (
    ApiError,
    ApiUnreachableError,
    AuthError,
    ConfigError,
    DepositApiException,
    OneDepError,
    SchemaError,
)


def test_all_errors_inherit_from_onedep_error():
    assert issubclass(AuthError, OneDepError)
    assert issubclass(ApiError, OneDepError)
    assert issubclass(ConfigError, OneDepError)
    assert issubclass(SchemaError, OneDepError)
    assert issubclass(ApiUnreachableError, OneDepError)


def test_api_unreachable_error_is_an_api_error():
    # Subclassing ApiError keeps existing `except ApiError` handlers working.
    assert issubclass(ApiUnreachableError, ApiError)
    with pytest.raises(ApiError):
        raise ApiUnreachableError()


def test_api_unreachable_error_has_no_status_code():
    # No response was received, so there is no status to report. It must not
    # borrow a plausible-looking one: a caller checking for 401/403 to decide
    # "the credentials are bad" would otherwise blame an offline network on
    # the user's token.
    err = ApiUnreachableError()
    assert err.status_code is None
    assert str(err) == "Failed to access the API"


def test_api_unreachable_error_accepts_a_message():
    err = ApiUnreachableError("Retry after redirect failed")
    assert str(err) == "Retry after redirect failed"
    assert err.status_code is None


def test_api_error_stores_status_code():
    err = ApiError("Not found", 404)
    assert err.status_code == 404
    assert str(err) == "Not found"


@pytest.mark.parametrize("error", [ApiError("failure", 400), AuthError("failure"), ApiUnreachableError()])
def test_error_diagnostics_default_to_none(error):
    assert error.code is None
    assert error.extras is None
    assert error.details is None
    assert error.request_id is None


def test_auth_error_preserves_exception_constructor_arguments():
    assert AuthError().args == ()
    assert AuthError("first", "second").args == ("first", "second")


def test_deposit_api_exception_is_alias_for_api_error():
    assert DepositApiException is ApiError
    err = DepositApiException("Unauthorized", 401)
    assert isinstance(err, OneDepError)


def test_exceptions_are_catchable_as_base():
    with pytest.raises(OneDepError):
        raise AuthError("bad token")

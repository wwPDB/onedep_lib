import pytest

from onedep_lib.exceptions import (
    ApiError,
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


def test_api_error_stores_status_code():
    err = ApiError("Not found", 404)
    assert err.status_code == 404
    assert str(err) == "Not found"


def test_deposit_api_exception_is_alias_for_api_error():
    assert DepositApiException is ApiError
    err = DepositApiException("Unauthorized", 401)
    assert isinstance(err, OneDepError)


def test_exceptions_are_catchable_as_base():
    with pytest.raises(OneDepError):
        raise AuthError("bad token")

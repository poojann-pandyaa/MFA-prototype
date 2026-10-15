import pytest
from pydantic import ValidationError

from amfa.engine.policy import TenantPolicy


def test_branding_colour_defaults_and_is_a_plain_hex():
    """The colour lands in the hosted pages' CSS, so anything but #RRGGBB is refused at the policy."""
    assert TenantPolicy().branding.color == "#1B4FD8"
    assert TenantPolicy.model_validate({"branding": {"color": "#0B6E63"}}).branding.color == "#0B6E63"
    for bad in ("red;}", "#0B6E6", "0B6E63", "#0B6E63;background:url(x)"):
        with pytest.raises(ValidationError):
            TenantPolicy.model_validate({"branding": {"color": bad}})

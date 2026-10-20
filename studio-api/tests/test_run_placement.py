"""services/run_placement: which machines one run may use, as -D properties (#572)."""

from __future__ import annotations

import pytest

from services.run_placement import plan_args, plan_properties

P = "pegasus.catalog.site.sites.compute.profiles.condor."


def test_anywhere_without_gpus_adds_nothing():
    """The access point's own pool-or-annex routing applies, and a
    requirements row of the user's own stays in force."""
    assert plan_properties("compute", "any", 0, "alice") == {}


def test_annex_only_pins_to_the_users_annex():
    assert plan_properties("compute", "annex", 0, "alice") == {
        P + "requirements": 'TARGET.AnnexName =?= "alice"'
    }


def test_pool_only_excludes_every_annex_node():
    assert plan_properties("compute", "pool", 0, "alice") == {
        P + "requirements": "TARGET.AnnexName =?= undefined"
    }


def test_gpus_ride_as_request_gpus():
    props = plan_properties("compute", "annex", 2, "alice")
    assert props[P + "request_gpus"] == "2"
    assert plan_args(props) == [
        f"-D{P}request_gpus=2",
        f'-D{P}requirements=TARGET.AnnexName =?= "alice"',
    ]


@pytest.mark.parametrize(
    "site,placement,gpus,name",
    [
        ("compute site", "any", 0, "alice"),  # lands inside a property key
        ("compute", "everywhere", 0, "alice"),
        ("compute", "any", 99, "alice"),
        ("compute", "annex", 0, 'a" || true'),
    ],
)
def test_refuses_what_would_not_be_a_clean_property(site, placement, gpus, name):
    with pytest.raises(ValueError):
        plan_properties(site, placement, gpus, name)

"""services/run_placement: which machines one run may use, as -D properties (#572)."""

from __future__ import annotations

import pytest

from services.run_placement import plan_args, plan_properties

P = "pegasus.catalog.site.sites.compute.profiles.condor."


def test_anywhere_adds_nothing():
    """The access point's own pool-or-annex routing applies, and a
    requirements row of the user's own stays in force."""
    assert plan_properties("compute", "any", "alice") == {}


def test_annex_only_pins_to_the_users_annex():
    assert plan_properties("compute", "annex", "alice") == {
        P + "requirements": 'TARGET.AnnexName =?= "alice"'
    }


def test_pool_only_excludes_every_annex_node():
    assert plan_properties("compute", "pool", "alice") == {
        P + "requirements": "TARGET.AnnexName =?= undefined"
    }


def test_plan_args_are_one_D_per_property():
    props = plan_properties("compute", "annex", "alice")
    assert plan_args(props) == [f'-D{P}requirements=TARGET.AnnexName =?= "alice"']


@pytest.mark.parametrize(
    "site,placement,name",
    [
        ("compute site", "any", "alice"),  # lands inside a property key
        ("compute", "everywhere", "alice"),
        ("compute", "annex", 'a" || true'),
    ],
)
def test_refuses_what_would_not_be_a_clean_property(site, placement, name):
    with pytest.raises(ValueError):
        plan_properties(site, placement, name)

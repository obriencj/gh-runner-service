from preoccupied.gh_runner_ctl import version_check as vc


class TestOrdering:
    def test_numeric_not_lexical(self):
        """2.9.0 < 2.10.0 — the whole point of not comparing strings."""
        assert vc.as_tuple("2.10.0") > vc.as_tuple("2.9.0")

    def test_v_prefix_and_suffixes(self):
        assert vc.as_tuple("2.328.0") == (2, 328, 0)
        assert vc.as_tuple("2.328.0-rc1") == (2, 328, 0)


class TestBehind:
    RELEASES = ["2.330.0", "2.329.0", "2.328.0", "2.327.1", "2.327.0"]

    def test_current_is_zero_behind(self):
        assert vc.releases_behind("2.330.0", self.RELEASES) == 0

    def test_counts_only_newer(self):
        assert vc.releases_behind("2.328.0", self.RELEASES) == 2

    def test_ahead_of_upstream_is_zero(self):
        assert vc.releases_behind("2.999.0", self.RELEASES) == 0

"""Configurable menu variant without the built-in electron/photon isolation cuts."""

from .configurable_menu import ConfigurableCollide2VTriggerMenu


class NoIsolationCollide2VTriggerMenu(ConfigurableCollide2VTriggerMenu):
    """Reuse all trigger rules, replacing isolated-object selection with kinematics.

    Names, target rates, transverse thresholds, eta, DZ, mass and other cuts are
    inherited unchanged. The input collections' upstream ID/selection remains.
    External trigger callables retain their explicitly defined rules.
    """

    isolation_enabled = False

    def _isolated_indices(self, event, prefix, *, pt_min, eta_max, iso_max):
        # Ignore iso_max and never read an isolation branch, including NaNs.
        return self._object_indices(event, prefix, pt_min=pt_min, eta_max=eta_max)

    @property
    def _builtin_required_columns(self) -> list[str]:
        return sorted(set(self.BASE_REQUIRED_COLUMNS))

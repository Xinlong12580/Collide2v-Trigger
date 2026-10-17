"""COLLIDE-2V trigger-menu proxies and configurable threshold profiles."""

from .core import (
    Collide2VTriggerMenuConfigurable,
    ConfigurableCollide2VTriggerMenu,
    IDBasedTriggerMenu,
    NoIsolationCollide2VTriggerMenu,
)
from .core.external import TriggerDefinition, TriggerRegistry, create_trigger_menu

__all__ = [
    "NoIsolationCollide2VTriggerMenu",
    "TriggerDefinition", "TriggerRegistry", "create_trigger_menu",
    "ConfigurableCollide2VTriggerMenu",
    "Collide2VTriggerMenuConfigurable",
    "IDBasedTriggerMenu",
]

__version__ = "0.1.0"

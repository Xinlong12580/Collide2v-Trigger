"""Self-contained external trigger definitions and their file registry."""

from __future__ import annotations

from dataclasses import dataclass, field
import hashlib
import json
import math
from numbers import Real
from pathlib import Path
import re
import sys
from types import MappingProxyType, ModuleType
from typing import Any, Callable, Mapping, Sequence


Evaluator = Callable[[Mapping[str, Any], Mapping[str, float], Mapping[str, Any]], bool]


@dataclass(frozen=True)
class TriggerDefinition:
    """Everything specific to one trigger, exported by a trusted Python file.

    ``evaluate(event, thresholds, fixed_parameters)`` must be deterministic.
    ``monotonic=True`` declares that acceptance cannot increase when the
    specified transverse thresholds are proportionally increased. Only such
    rules support the critical-score bisection calibration.
    """

    name: str
    evaluate: Evaluator
    required_columns: tuple[str, ...]
    default_thresholds: Mapping[str, float]
    target_rate_khz: float
    scale_fields: tuple[str, ...]
    reference_field: str
    fixed_parameters: Mapping[str, Any] = field(default_factory=dict)
    description: str = ""
    version: str = "1"
    monotonic: bool = False

    def __post_init__(self) -> None:
        if not isinstance(self.name, str) or not re.fullmatch(r"[A-Za-z][A-Za-z0-9_]*", self.name):
            raise ValueError("Trigger name must start with a letter and contain letters, digits, or underscores")
        if not callable(self.evaluate):
            raise TypeError(f"{self.name}: evaluate must be callable")
        if not isinstance(self.default_thresholds, Mapping) or not self.default_thresholds:
            raise ValueError(f"{self.name}: default_thresholds cannot be empty")
        defaults = {}
        for key, value in self.default_thresholds.items():
            if not isinstance(key, str) or not key:
                raise ValueError(f"{self.name}: threshold field names must be nonempty strings")
            if isinstance(value, bool) or not isinstance(value, Real) or not math.isfinite(value) or value < 0:
                raise ValueError(f"{self.name}.{key}: threshold must be finite and nonnegative")
            defaults[key] = float(value)
        if isinstance(self.required_columns, str) or isinstance(self.scale_fields, str):
            raise TypeError(f"{self.name}: required_columns and scale_fields must be sequences of names")
        columns = tuple(self.required_columns)
        fields = tuple(self.scale_fields)
        if not columns or any(not isinstance(x, str) or not x for x in columns) or len(set(columns)) != len(columns):
            raise ValueError(f"{self.name}: required_columns must contain unique nonempty names")
        if not fields or any(not isinstance(x, str) for x in fields) or len(set(fields)) != len(fields) or set(fields) - set(defaults):
            raise ValueError(f"{self.name}: scale_fields must be unique fields from default_thresholds")
        if self.reference_field not in fields or defaults[self.reference_field] <= 0:
            raise ValueError(f"{self.name}: reference_field must be scaled and have a positive default")
        if isinstance(self.target_rate_khz, bool) or not isinstance(self.target_rate_khz, Real) or not math.isfinite(self.target_rate_khz) or self.target_rate_khz < 0:
            raise ValueError(f"{self.name}: target_rate_khz must be finite and nonnegative")
        if not isinstance(self.monotonic, bool):
            raise TypeError(f"{self.name}: monotonic must be a boolean")
        if not isinstance(self.version, str) or not self.version or not isinstance(self.description, str):
            raise ValueError(f"{self.name}: version must be a nonempty string and description must be a string")
        if not isinstance(self.fixed_parameters, Mapping):
            raise TypeError(f"{self.name}: fixed_parameters must be a mapping")
        fixed = json.loads(json.dumps(dict(self.fixed_parameters), allow_nan=False))
        object.__setattr__(self, "default_thresholds", MappingProxyType(defaults))
        object.__setattr__(self, "fixed_parameters", MappingProxyType(fixed))
        object.__setattr__(self, "required_columns", columns)
        object.__setattr__(self, "scale_fields", fields)

    def metadata(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "version": self.version,
            "description": self.description,
            "required_columns": list(self.required_columns),
            "default_thresholds": dict(self.default_thresholds),
            "target_rate_khz": float(self.target_rate_khz),
            "scale_fields": list(self.scale_fields),
            "reference_field": self.reference_field,
            "fixed_parameters": dict(self.fixed_parameters),
            "monotonic": self.monotonic,
        }


class TriggerRegistry:
    """Load ``TRIGGER`` or ``TRIGGERS`` exports and retain their provenance."""

    def __init__(self, files: Sequence[str | Path] = ()) -> None:
        self.definitions: dict[str, TriggerDefinition] = {}
        self.provenance: dict[str, dict[str, Any]] = {}
        for filename in files:
            path = Path(filename).expanduser().resolve()
            if not path.is_file() or path.suffix.lower() != ".py":
                raise ValueError(f"External trigger file must be an existing .py file: {path}")
            source = path.read_bytes()
            digest = hashlib.sha256(source).hexdigest()
            module_name = "_collide2v_external_" + hashlib.sha256(str(path).encode() + source).hexdigest()
            module = sys.modules.get(module_name)
            if module is None:
                module = ModuleType(module_name)
                module.__file__ = str(path)
                sys.modules[module_name] = module
                try:
                    # Compile the hashed bytes directly; do not reuse timestamp-based pyc files.
                    exec(compile(source, str(path), "exec"), module.__dict__)
                except BaseException:
                    sys.modules.pop(module_name, None)
                    raise
            single = getattr(module, "TRIGGER", None)
            multiple = getattr(module, "TRIGGERS", None)
            if (single is None) == (multiple is None):
                raise ValueError(f"{path} must export exactly one of TRIGGER or TRIGGERS")
            definitions = [single] if single is not None else multiple
            if not isinstance(definitions, (list, tuple)) or not definitions:
                raise ValueError(f"{path}: TRIGGERS must be a nonempty list or tuple")
            for definition in definitions:
                if not isinstance(definition, TriggerDefinition):
                    raise TypeError(f"{path}: each export must be a TriggerDefinition")
                if definition.name in self.definitions:
                    raise ValueError(f"Duplicate external trigger name: {definition.name}")
                self.definitions[definition.name] = definition
                self.provenance[definition.name] = {
                    "source_file": str(path), "sha256": digest,
                    "definition": definition.metadata(),
                }


def definition_identity(provenance: Mapping[str, Any]) -> dict[str, Any]:
    """Content identity remains the same when an unchanged definition is moved."""
    return {key: value for key, value in provenance.items() if key != "source_file"}


def create_trigger_menu(*, custom_trigger_files: Sequence[str | Path] = (), ignore_isolation: bool = False, **kwargs):
    """Common menu factory for both Python callers and command-line runners."""
    from .configurable_menu import ConfigurableCollide2VTriggerMenu
    from .no_isolation_menu import NoIsolationCollide2VTriggerMenu

    menu_class = NoIsolationCollide2VTriggerMenu if ignore_isolation else ConfigurableCollide2VTriggerMenu
    return menu_class(custom_trigger_files=custom_trigger_files, **kwargs)


__all__ = ["TriggerDefinition", "TriggerRegistry", "create_trigger_menu"]

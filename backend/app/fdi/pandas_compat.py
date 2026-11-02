"""pandas 2.x -> 3.x StringDtype compatibility shim.

Legacy joblib artifacts pickled under pandas 2.x carry a StringDtype whose
`__init__` no longer accepts `na_value` / `storage` under pandas 3.x. This
module installs a minimal shim ONCE (idempotent) before any joblib.load, and
exposes `safe_joblib_load` so artifacts can be scrubbed on load.

The shim is loud when it activates — you should regenerate the artifacts
under the target pandas version before relying on this in production.
"""

from __future__ import annotations

import logging

import numpy as np
import joblib

logger = logging.getLogger("fdi.pandas_compat")

_PATCHED = False


def install() -> bool:
    """Install the StringDtype shim if needed. Returns True iff active."""
    global _PATCHED
    try:
        from pandas.core.arrays.string_ import StringDtype
    except Exception as e:  # pragma: no cover
        logger.warning("pandas StringDtype not importable — compat patch skipped: %s", e)
        return False

    if getattr(StringDtype, "_fdi_compat_init", False):
        _PATCHED = True
        return True

    # Probe: does the current signature accept the legacy no-arg call?
    try:
        StringDtype()
        return False
    except Exception:
        pass

    logger.warning(
        "Installing pandas StringDtype compat patch. Legacy joblib artifacts "
        "will load, but `na_value`/`storage` kwargs are dropped. Regenerate "
        "artifacts under the current pandas version when possible."
    )

    _orig_init = StringDtype.__init__

    def _compat_init(self, *args, **kwargs):
        if len(args) > 1:
            args = args[:1]
        kwargs.pop("na_value", None)
        kwargs.pop("storage", None)
        return _orig_init(self, *args, **kwargs)

    StringDtype.__init__ = _compat_init
    StringDtype._fdi_compat_init = True

    if "_na_value" not in StringDtype.__dict__:
        try:
            StringDtype._na_value = np.nan
        except Exception:
            logger.debug("Could not set StringDtype._na_value.", exc_info=True)

    if not getattr(StringDtype, "_fdi_compat_setstate", False):
        _orig_setstate = getattr(StringDtype, "__setstate__", None)

        def _compat_setstate(self, state):
            if _orig_setstate is not None:
                try:
                    return _orig_setstate(self, state)
                except Exception:
                    logger.debug("StringDtype.__setstate__ failed; manual fallback.",
                                 exc_info=True)
            if isinstance(state, dict):
                for k in ("storage", "_storage"):
                    if k in state:
                        try:
                            object.__setattr__(self, k, state[k])
                        except Exception:
                            logger.debug("Could not restore %s on StringDtype.",
                                         k, exc_info=True)
            return None

        StringDtype.__setstate__ = _compat_setstate
        StringDtype._fdi_compat_setstate = True

    _PATCHED = True
    return True


def _coerce_legacy_string_dtypes(obj, _depth: int = 0):
    if _depth > 32:
        return obj
    try:
        import pandas as pd
        if hasattr(obj, "dtype") and isinstance(obj.dtype, pd.StringDtype):
            try:
                return obj.astype("str")
            except Exception:
                return obj
        if isinstance(obj, dict):
            return {k: _coerce_legacy_string_dtypes(v, _depth + 1) for k, v in obj.items()}
        if isinstance(obj, (list, tuple)):
            return type(obj)(_coerce_legacy_string_dtypes(v, _depth + 1) for v in obj)
        if hasattr(obj, "__dict__") and not isinstance(obj, type):
            for k, v in list(vars(obj).items()):
                if isinstance(v, pd.StringDtype):
                    try:
                        setattr(obj, k, pd.StringDtype())
                    except Exception:
                        pass
    except Exception:
        pass
    return obj


def safe_joblib_load(path: str):
    """Load a joblib artifact and scrub legacy StringDtype state."""
    obj = joblib.load(path)
    return _coerce_legacy_string_dtypes(obj)


# Activate on import — before any artifact is loaded downstream.
install()
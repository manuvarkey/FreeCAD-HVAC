# SPDX-License-Identifier: LGPL-2.1-or-later
"""Native PartScript loader for text-based parametric HVAC models."""

import hashlib
import importlib.util
import os
import sys


class PartScriptError(Exception):
    pass


class PartScriptSchemaError(PartScriptError):
    pass


_MODULE_CACHE = {}
_PARTSCRIPT_API_VERSION = 1


def _module_signature(path):
    stat = os.stat(path)
    return stat.st_mtime_ns, stat.st_size


def _load_module(script_path):
    path = os.path.realpath(script_path)
    if not os.path.isfile(path):
        raise PartScriptSchemaError("PartScript file not found: '{}'".format(path))

    signature = _module_signature(path)
    cached = _MODULE_CACHE.get(path)
    if cached is not None and cached[0] == signature:
        return cached[1]

    module_name = "_freecad_hvac_partscript_{}".format(
        hashlib.sha1(path.encode("utf-8")).hexdigest()
    )
    spec = importlib.util.spec_from_file_location(module_name, path)
    if spec is None or spec.loader is None:
        raise PartScriptSchemaError("Cannot load PartScript '{}'".format(path))

    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    try:
        spec.loader.exec_module(module)
    except Exception:
        sys.modules.pop(module_name, None)
        raise

    _MODULE_CACHE[path] = (signature, module)
    return module


def clear_cache():
    _MODULE_CACHE.clear()


def execute_partscript(script_path, context):
    module = _load_module(script_path)

    version = int(getattr(module, "HVAC_PARTSCRIPT_API", _PARTSCRIPT_API_VERSION))
    if version != _PARTSCRIPT_API_VERSION:
        raise PartScriptSchemaError(
            "PartScript '{}' requires API {}; loader supports {}".format(
                script_path, version, _PARTSCRIPT_API_VERSION
            )
        )

    validator = getattr(module, "validate", None)
    if validator is not None:
        if not callable(validator):
            raise PartScriptSchemaError("PartScript validate must be callable")
        validator(context)

    generator = getattr(module, "generate", None)
    if not callable(generator):
        raise PartScriptSchemaError(
            "PartScript '{}' must define generate(context)".format(script_path)
        )

    result = generator(context)
    if hasattr(result, "isNull"):
        result = {"shape": result}
    if not isinstance(result, dict):
        raise PartScriptSchemaError(
            "PartScript '{}' must return Part.Shape or dict".format(script_path)
        )

    shape = result.get("shape")
    if shape is None or not hasattr(shape, "isNull"):
        raise PartScriptSchemaError(
            "PartScript '{}' did not return a Part.Shape in result['shape']".format(script_path)
        )
    if shape.isNull():
        raise PartScriptSchemaError("PartScript '{}' returned an empty shape".format(script_path))

    return dict(result)

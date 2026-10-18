"""Build/runtime checks for the cooperating application maintenance protocol."""
import json
import re

LABEL = 'io.epilykos.maintenance-lock'
PROTOCOL = 'flock-v1'
LOCK_ENV = 'EPILYKOS_MAINTENANCE_LOCK=/app/data/.maintenance.lock'
IMAGE_ID = re.compile(r'^[0-9a-f]{64}$')


class CompatibilityError(ValueError):
    pass


def _labels(config):
    if not isinstance(config, dict) or not isinstance(config.get('Labels'), dict) \
            or config['Labels'].get(LABEL) != PROTOCOL:
        raise CompatibilityError('The app does not support the required shared maintenance protocol (flock-v1)')


def oci_config(config):
    if not isinstance(config, dict):
        raise CompatibilityError('The app image configuration is not an object')
    if config.get('os') != 'linux' or config.get('architecture') != 'arm64':
        raise CompatibilityError('The app image platform must be linux/arm64')
    _labels(config.get('config'))


def inspection(raw):
    try:
        if len(raw) > 4 * 1024 * 1024:
            raise ValueError('too large')
        result = json.loads(raw)
    except (ValueError, TypeError, RecursionError):
        raise CompatibilityError('The app inspection configuration is unreadable') from None
    if not isinstance(result, list) or len(result) != 1 or not isinstance(result[0], dict):
        raise CompatibilityError('The app inspection configuration must name exactly one object')
    return result[0]


def candidate_image(info):
    if info.get('Os') != 'linux' or info.get('Architecture') != 'arm64':
        raise CompatibilityError('The app image platform must be linux/arm64')
    _labels(info.get('Config'))
    image_id = info.get('Id')
    if not isinstance(image_id, str) or not IMAGE_ID.fullmatch(image_id):
        raise CompatibilityError('The app image configuration has an invalid image ID')
    return image_id


def running_container(info):
    config = info.get('Config')
    _labels(config)
    env = config.get('Env')
    lock_values = [value for value in env if isinstance(value, str) and value.startswith('EPILYKOS_MAINTENANCE_LOCK=')] \
        if isinstance(env, list) else []
    if lock_values != [LOCK_ENV] or not isinstance(info.get('State'), dict) or info['State'].get('Running') is not True:
        raise CompatibilityError('The running app is not configured for shared maintenance; restart it on the paired OS')
    mounts = info.get('Mounts')
    if not isinstance(mounts, list):
        raise CompatibilityError('The app maintenance mount configuration is invalid')
    data = [mount for mount in mounts if isinstance(mount, dict) and mount.get('Destination') == '/app/data'] \
        if isinstance(mounts, list) else []
    shadowed = any(isinstance(mount, dict) and mount.get('Destination') == '/app/data/.maintenance.lock' for mount in (mounts or []))
    if len(data) != 1 or data[0].get('Type') != 'bind' or data[0].get('Source') != '/data/epilykos/app-data' \
            or data[0].get('RW') is not True or shadowed:
        raise CompatibilityError('The running app DATA mount cannot share the host maintenance lock')

"""Liveness checks for durable operation ownership."""
import os


def owner_alive(record):
    # Never steal an active claim on a timer. A slow network must not enable a
    # second remote mutation. Dead process recovery requires explicit refresh.
    pid = record.get('_pid')
    if type(pid) is not int or pid <= 0: return True
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True

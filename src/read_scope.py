"""Reuse immutable evidence reads within one operation, never across requests."""
from contextlib import contextmanager
from contextvars import ContextVar
from copy import deepcopy
from functools import wraps

_READS = ContextVar("operation_evidence_reads", default=None)
_CONTACTS = ContextVar("locked_contact_reads", default=None)


@contextmanager
def scope():
    if _READS.get() is not None:
        yield
        return
    token = _READS.set({})
    try:
        yield
    finally:
        _READS.reset(token)


def scoped(function):
    @wraps(function)
    def read(*args, **kwargs):
        with scope():
            return function(*args, **kwargs)
    return read


def once(key, factory, *, copy=False):
    reads = _READS.get()
    if reads is None:
        return factory()
    if key not in reads:
        reads[key] = factory()
    return deepcopy(reads[key]) if copy else reads[key]


@contextmanager
def contacts():
    """Caller holds the communication gate for this entire read-only view."""
    token = _CONTACTS.set({})
    try:
        yield
    finally:
        _CONTACTS.reset(token)


def contact_once(key, factory, *, copy=False):
    reads = _CONTACTS.get()
    if reads is None:
        return factory()
    if key not in reads:
        reads[key] = factory()
    return deepcopy(reads[key]) if copy else reads[key]

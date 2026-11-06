"""Cooperative cancellation signal shared by the worker and its I/O stages."""


class JobCancelled(Exception):
    """The job was cancelled before its result became available."""

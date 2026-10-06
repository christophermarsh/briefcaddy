"""Construct the transition staff application using explicitly injected factories."""

from argparse import Namespace
from collections.abc import Callable
from typing import Any


def build_staff_app(args: Namespace, *, accounts_factory: Callable[..., Any],
                    app_factory: Callable[..., Any], local_setup: bool) -> tuple[Any, Any]:
    """Preserve one conditional account construction and one legacy app construction.

    Constructor effects remain in the injected implementation. Worker, server
    and warm-up lifecycle order stays with the compatibility entrypoint.
    """
    accounts = accounts_factory(args.users) if args.users.exists() else None
    app = app_factory(args.data, args.field_map, args.template, args.policy, args.portal,
                      accounts, args.secure_cookies, behind_tls=args.behind_tls_proxy,
                      trusted_proxies=tuple(args.trusted_proxy), local_setup=local_setup,
                      deployment_path=getattr(args, "deployment", None))
    return app, accounts

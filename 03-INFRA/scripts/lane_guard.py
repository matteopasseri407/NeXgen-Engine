#!/usr/bin/env python3
"""Checkout entry point for the packaged contributor lane checks."""
from nexgen_core.lanes import check_ref as check_ref, guarded_ref as guarded_ref, main

if __name__ == "__main__":
    raise SystemExit(main())

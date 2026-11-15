"""The deployed half of an installation: what runs under `03-INFRA/deploy/`.

One job: hold the code that keeps the containers on a server on the same engine
commit as the checkout that describes them. Nothing here starts a stack on a
workstation: that is `nexgen stack`, one machine closer to home.
"""
from __future__ import annotations

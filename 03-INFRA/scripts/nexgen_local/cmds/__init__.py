"""Domain command groups for nexgen-local: each module owns one lane slice.

cli.py keeps only argument wiring (main) and re-exports, so
`from nexgen_local import cli` and `cli.cmd_mail_propose` keep working.
"""

"""Local travel journal generator."""

import os


# Some Windows installations define this globally with an unwritable path.
# Python's SSL stack opens it for every HTTPS client, although the app does not
# need TLS key logging.
os.environ.pop("SSLKEYLOGFILE", None)

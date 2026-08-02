import os
import subprocess
import sys


def test_app_disables_inherited_ssl_key_logging() -> None:
    environment = os.environ.copy()
    environment["SSLKEYLOGFILE"] = r"D:\ssl.log"
    probe = subprocess.run(
        [
            sys.executable,
            "-c",
            (
                "import app, os, ssl; "
                "context = ssl.create_default_context(); "
                "assert 'SSLKEYLOGFILE' not in os.environ; "
                "assert context.keylog_filename is None"
            ),
        ],
        cwd=os.getcwd(),
        env=environment,
        capture_output=True,
        text=True,
        check=False,
    )

    assert probe.returncode == 0, probe.stderr

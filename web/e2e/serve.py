"""Run the actual production HTTP handler with throwaway browser-test storage."""

from pathlib import Path
from tempfile import TemporaryDirectory

from data_curator.server import create_server


def main():
    with TemporaryDirectory(prefix="data-curator-browser-") as data_dir:
        server = create_server(
            host="127.0.0.1",
            port=8766,
            data_dir=Path(data_dir),
            static_dir=Path(__file__).resolve().parents[1] / "dist",
        )
        try:
            server.serve_forever()
        finally:
            server.server_close()


if __name__ == "__main__":
    main()

import logging


def configure_logging(level: int = logging.INFO) -> None:
    """Configure basic application logging."""
    logging.basicConfig(
        level=level,
        format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
    )

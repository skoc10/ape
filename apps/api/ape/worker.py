"""Queue worker entrypoint: rules, LLM, PDF (image: ape-worker).

Idle loop until task 3.4 implements the real queue consumer.
"""

import logging
import signal
import threading

log = logging.getLogger("ape.worker")


def main() -> None:
    logging.basicConfig(level=logging.INFO)
    stop = threading.Event()
    for sig in (signal.SIGINT, signal.SIGTERM):
        signal.signal(sig, lambda *_: stop.set())
    log.info("worker started (idle loop)")
    while not stop.wait(timeout=5):
        pass
    log.info("worker stopped")


if __name__ == "__main__":
    main()

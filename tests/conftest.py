from pathlib import Path

import numpy as np
import pytest

from ergobike.audio import read_wav
from ergobike.pulses import EdgeDetector

DATA = Path(__file__).parent / "data"


@pytest.fixture(scope="session")
def recording():
    """Load one of the real recordings in tests/data by short name."""
    def load(name: str):
        return read_wav(str(DATA / f"realtek_mme_{name}.wav"))
    return load


def detect(x, rate, block=1024, **kw) -> np.ndarray:
    det = EdgeDetector(rate, **kw)
    out = []
    for i in range(0, len(x), block):
        out += det.process(x[i:i + block])
    return np.array(out) / rate

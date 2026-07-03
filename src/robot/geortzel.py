from math import pi, cos, sin

class Goertzel:
    def __init__(self, target_freq: float, sample_rate: float, n: int):
        self._target_freq: float = target_freq
        self._sample_rate: float = sample_rate
        self._n: int = n

        self._k = int(0.5 + (n * target_freq / sample_rate))
        self._omega: float = (2.0 * pi * self._k) / n
        self._coeff = 2.0 * cos(self._omega)

    def compute(self, samples):
        q0 = q1 = q2 = 0

        for x in samples:
            q0 = self._coeff * q1 - q2 + x
            q2 = q1
            q1 = q0
        
        real = (q1 - q2 * cos(self._omega))
        imaginary = (q2 * sin(self._omega))
        return real * real + imaginary * imaginary

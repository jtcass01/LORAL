
class DCBlocker:
    def __init__(self, alpha=0.995):
        self._alpha = alpha
        self._prev_x: int = 0
        self._prev_y: float = 0.

    def update(self, x: int) -> float:
        y = x - self._prev_x + self._alpha * self._prev_y

        self._prev_x: int = x
        self._prev_y: float = y

        return y
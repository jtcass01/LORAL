from machine import Pin
from utime import sleep_us

class Beacon:
    def __init__(self, id: str, led_gpio: Pin,
                 bit_rate: int = 250):
        self._id: str = id
        self._led_gpio: Pin = led_gpio
        self._bit_period_us = int(1_000_000 / bit_rate)

    def broadcast_id(self) -> None:
        msg = self._id + "\n"

        while True:
            for c in msg:
                self.send_byte(c)

    def send_byte(self, c: str) -> None:
        byte = ord(c)

        # Start bit
        self._led_gpio.off()
        sleep_us(self._bit_period_us)

        for i in range(8):
            bit = (byte >> i) & 1

            if bit:
                self._led_gpio.on()
            else:
                self._led_gpio.off()

            sleep_us(self._bit_period_us)
        
        # Stop bit
        self._led_gpio.on()
        sleep_us(self._bit_period_us)


if __name__ == "__main__":
    led_gpio: Pin = Pin(16, Pin.OUT)
    test_beacon: Beacon = Beacon(id="A", led_gpio=led_gpio, bit_rate=500)
    test_beacon.broadcast_id()
